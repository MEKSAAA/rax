"""Host-only fixed Responses endpoint. Credentials never enter Docker."""
import base64
import gzip
from http.server import BaseHTTPRequestHandler
import http.client
import io
import json
import os
from pathlib import Path
import random
import re
import socketserver
import threading
import time
from urllib.parse import urlsplit


MODEL = 'openai/gpt-6-astra'
# Image-heavy conversation history is serialized into every model request.
MAX_BODY = 256*1024*1024
REQUEST_TIMEOUT_SECONDS = 120
# Provider throttling/overload is retried here, before any response byte reaches
# Codex, so a partial stream is never replayed. Codex's own two retries back off
# for seconds; provider rate-limit windows last minutes.
RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_WINDOW_SECONDS = 600
RETRY_MAX_DELAY_SECONDS = 60
# Requests and responses pass through whole, as between Codex and its provider natively:
# the provider relies on Codex's headers (session-id routes a conversation to its prompt
# cache; dropping it once billed almost every call as uncached input), and Codex on the
# provider's (usage limits, error messages). Only these framing headers are the relay's own.
TRANSPORT_HEADERS = frozenset({'host', 'content-length', 'content-encoding', 'transfer-encoding', 'connection',
                               'keep-alive', 'proxy-connection', 'te', 'trailer', 'upgrade', 'accept-encoding'})
RESPONSE_OWN = ('Date', 'Server')  # Written by send_response itself.
MAX_ERROR_BODY = 1024*1024
# Concurrent model calls one relay serves: the agent, its subagents, compaction and spare
# room. A call beyond it is answered 503 with a reason (the CLIs retry it), never dropped.
MIN_SLOTS = 8
SPARE_SLOTS = 2


def slot_count(subagents=0):
    """Concurrent calls for an agent with up to ``subagents`` subagents."""
    return max(MIN_SLOTS, 1+int(subagents or 0)+SPARE_SLOTS)


def refusal(reason, kind='relay_refused'):
    """An OpenAI/Anthropic-style error body naming why the relay answered itself."""
    return json.dumps({'type': 'error', 'error': {'type': kind, 'message': reason}}).encode()


def passed_headers(headers, own=()):
    """Headers to pass on, without framing headers or any the relay sets itself (case-insensitive)."""
    taken = TRANSPORT_HEADERS | {name.lower() for name in own}
    return [(name, value) for name, value in headers if name.lower() not in taken]


def chatgpt_headers(path, lasting=0):
    """Auth headers from the operator's live Codex ChatGPT login (e.g. ~/.codex/auth.json).

    Read on every call and never refreshed here: the operator's own Codex owns the
    rotating refresh token, so two refreshers cannot invalidate each other.
    """
    auth = json.loads(Path(path).read_text())
    tokens = auth.get('tokens') or {}
    if auth.get('auth_mode') != 'chatgpt' or not tokens.get('access_token') or not tokens.get('account_id'):
        raise ValueError('Not a Codex ChatGPT login')
    if lasting:
        claims = tokens['access_token'].split('.')[1]
        expires = json.loads(base64.urlsafe_b64decode(claims+'='*(-len(claims) % 4)))['exp']
        if expires < time.time()+lasting:
            raise ValueError('The ChatGPT login expires before the session ends; run Codex on the host to refresh it')
    return {'Authorization': 'Bearer '+tokens['access_token'], 'ChatGPT-Account-ID': tokens['account_id']}


def retry_delay(attempt, retry_after):
    """Provider Retry-After seconds when given, else jittered exponential backoff."""
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        delay = min(RETRY_MAX_DELAY_SECONDS, 2**attempt) * random.uniform(.5, 1)
    return max(0., min(delay, RETRY_MAX_DELAY_SECONDS))


def local_inputs(value):
    """Reject provider-side fetching; text may freely discuss URLs as ordinary text."""
    if isinstance(value, list):
        return all(local_inputs(item) for item in value)
    if not isinstance(value, dict):
        return True
    if value.get('type') in {'input_image', 'image_url'}:
        url = value.get('image_url', '')
        if isinstance(url, dict):
            url = url.get('url', '')
        if not isinstance(url, str) or not url.startswith('data:image/'):
            return False
    if value.get('type') == 'input_file':
        return False
    return all(local_inputs(item) for item in value.values())


def local_tools(items):
    return isinstance(items, list) and all(isinstance(item, dict) and
        item.get('type') in {'function', 'custom', 'namespace'} and
        local_tools(item.get('tools', [])) for item in items)


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, *args, slots=MIN_SLOTS, **kwargs):
        self.capacity = slots
        self.slots = threading.BoundedSemaphore(slots)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            body = refusal(f'The host model relay is serving its maximum of {self.capacity} concurrent calls; '
                           'retry shortly', 'overloaded_error')
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                                b'Retry-After: 1\r\nConnection: close\r\n'
                                + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class CallCounter:
    """Model calls of a session, persisted so the count spans every launch (resume)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.value = 0
        if self.path is not None and self.path.is_file():
            self.value = int(json.loads(self.path.read_text()).get('calls', 0))

    def increment(self):
        self.value += 1
        if self.path is not None:
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'calls': self.value}))
            os.replace(temporary, self.path)


class ModelRelay:
    def __init__(self, path, base_url, headers=None, *, client_write_timeout=120,
                 model=MODEL, reasoning_effort=None, calls_file=None,
                 native_models=False, chatgpt_auth=None, slots=MIN_SLOTS):
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', model):
            raise ValueError('Invalid operator model identifier')
        url = urlsplit(base_url)
        if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Invalid operator model endpoint')
        if url.scheme == 'http' and url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Non-TLS endpoint is only allowed for local tests')
        self.url, self.headers = url, dict(headers or {})
        self.chatgpt_auth = chatgpt_auth
        self.counter, self.lock = CallCounter(calls_file), threading.Lock()
        relay = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass  # Never log headers, prompts, provider failures or credentials.
            def reject(self, status, reason):
                """The relay's own answer, with a body naming the reason."""
                body = refusal(reason)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
            def do_POST(self):
                upstream, started = None, False
                try:
                    self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
                    if self.path != '/responses':
                        self.reject(403, f'The relay serves only POST /responses, not {self.path}')
                        return
                    if self.headers.get('Transfer-Encoding'):
                        self.reject(411, 'Send the request body with Content-Length, not Transfer-Encoding')
                        return
                    length = int(self.headers.get('Content-Length', '-1'))
                    if not 0 < length <= MAX_BODY:
                        self.reject(413, f'Request body must be 1..{MAX_BODY} bytes')
                        return
                    body = self.rfile.read(length)
                    if self.headers.get('Content-Encoding') == 'gzip':
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                            body = stream.read(MAX_BODY+1)
                    elif self.headers.get('Content-Encoding'):
                        self.reject(415, 'Only gzip request encoding is accepted')
                        return
                    if len(body) > MAX_BODY:
                        self.reject(413, f'Decoded request body exceeds {MAX_BODY} bytes')
                        return
                    payload = json.loads(body)
                    if not isinstance(payload, dict) or not isinstance(payload.get('model'), str):
                        self.reject(400, 'Request body must be a JSON object with a model')
                        return
                    selected_model = payload['model']
                    if selected_model != model:
                        self.reject(403, f'Model {selected_model!r} is not this session\'s model ({model})')
                        return
                    required_effort = reasoning_effort
                    if required_effort is not None:
                        reasoning = payload.get('reasoning', {})
                        if not isinstance(reasoning, dict) or reasoning.get('effort') != required_effort:
                            self.reject(403, f'Reasoning effort must be {required_effort}')
                            return
                    # This is an inference relay, never a browser/download/MCP proxy.
                    if not (local_tools(payload.get('tools', [])) and local_tools(payload.get('additional_tools', []))):
                        self.reject(403, 'Only client-executed tools are allowed (no server-side or hosted tools)')
                        return
                    if payload.get('plugins') or payload.get('web_search_options'):
                        self.reject(403, 'Plugins and web search are not allowed')
                        return
                    if payload.get('previous_response_id'):
                        self.reject(403, 'previous_response_id is not allowed; send the conversation inline')
                        return
                    if not local_inputs(payload.get('input', [])):
                        self.reject(403, 'Only inline data: images are allowed (no URLs or files for the provider to fetch)')
                        return
                    if relay.chatgpt_auth:
                        payload.pop('max_output_tokens', None)  # The ChatGPT backend rejects the field.
                    # OpenAI's own API names models without OpenRouter's vendor prefix.
                    payload['model'] = selected_model.removeprefix('openai/') if native_models else selected_model
                    try:
                        login = chatgpt_headers(relay.chatgpt_auth) if relay.chatgpt_auth else {}
                    except (OSError, ValueError, KeyError) as exc:
                        self.reject(401, f'The ChatGPT login is unusable ({type(exc).__name__}: {exc}); '
                                         'run Codex on the host to refresh it')
                        return
                    connection = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
                    own = {**relay.headers, **login, 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
                    headers = {**dict(passed_headers(self.headers.items(), own)), **own}
                    if not any(name.lower() == 'accept' for name in headers):
                        headers['Accept'] = 'text/event-stream'
                    body = json.dumps(payload).encode()
                    # One logical model call: throttled/overloaded attempts are
                    # neither billed nor counted against the cap.
                    deadline, attempt = time.monotonic()+RETRY_WINDOW_SECONDS, 0
                    while True:
                        upstream = connection(url.hostname, url.port, timeout=600)
                        upstream.request('POST', url.path.rstrip('/')+'/responses', body, headers)
                        response = upstream.getresponse()
                        if response.status not in RETRY_STATUSES:
                            break
                        delay = retry_delay(attempt, response.getheader('Retry-After'))
                        if time.monotonic()+delay >= deadline:
                            break
                        upstream.close()
                        time.sleep(delay)
                        attempt += 1
                    with relay.lock:  # One model call the provider answered.
                        relay.counter.increment()
                    if not 400 <= response.status <= 599 and response.status != 200:
                        # Redirects are never followed.
                        self.reject(502, f'The provider answered {response.status} {response.reason}; '
                                         'the relay never follows redirects')
                        return
                    if response.status != 200:
                        # The provider's own status, headers and error message. Codex's
                        # bounded retries follow once the relay's retry window is spent.
                        error = response.read(MAX_ERROR_BODY)
                        started = True
                        self.send_response(response.status)
                        for name, value in passed_headers(response.getheaders(), RESPONSE_OWN):
                            self.send_header(name, value)
                        self.send_header('Content-Length', str(len(error)))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.wfile.write(error)
                        self.close_connection = True
                        return
                    # Rehearsal/submission pauses the whole client container.
                    # Its full socket buffers must not truncate a valid model
                    # response. Request reads retain 120s; provider I/O 600s.
                    self.connection.settimeout(client_write_timeout)
                    started = True
                    self.send_response(200)
                    for name, value in passed_headers(response.getheaders(), RESPONSE_OWN):
                        self.send_header(name, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.close_connection = True
                    total = 0
                    while block := response.read1(65536):
                        total += len(block)
                        if total > MAX_BODY:
                            break
                        self.wfile.write(block)
                        self.wfile.flush()
                except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException) as exc:
                    self.close_connection = True
                    if not started:
                        # Before any response byte: answer with the cause. A provider that is
                        # unreachable, times out or breaks off is a 502; anything earlier is the request's.
                        provider = upstream is not None
                        reason = (f'The relay could not complete the provider call: {type(exc).__name__}: {exc}'
                                  if provider else f'Invalid request: {type(exc).__name__}: {exc}')
                        try:
                            self.reject(502 if provider else 400, reason)
                        except OSError:
                            pass  # The client is gone.
                finally:
                    if upstream:
                        upstream.close()
            def do_GET(self):
                self.reject(403, 'The relay serves only POST /responses')
            do_CONNECT = do_GET
        self.server = UnixHTTPServer(str(path), Handler, slots=slots)
        os.chmod(path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def calls(self):
        return self.counter.value

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
