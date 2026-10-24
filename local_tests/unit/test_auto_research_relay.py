"""Real local HTTP/Unix relay tests; no external API requests."""
import gzip
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time

import pytest

from harness.codex_cli import model_relay
from harness.codex_cli.model_relay import MAX_BODY, MODEL, ModelRelay


@pytest.fixture
def relay(tmp_path, request):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, dict(self.headers), body))
            metadata = body.get('metadata', {})
            status = 302 if metadata.get('redirect') else metadata.get('status', 200)
            if len(received) > metadata.get('fail_first', len(received)):
                status = 200
            self.send_response(status)
            if 'retry_after' in metadata:
                self.send_header('Retry-After', str(metadata['retry_after']))
            self.send_header('Location', 'http://127.0.0.1:1/private')
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('x-codex-primary-used-percent', '12')
            self.end_headers()
            content = b'data: fixture-event\n\n' if status == 200 else b'private provider error body'
            self.wfile.write(b'x' * (4*1024*1024) if metadata.get('large') else content)
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    path = tmp_path/'relay.sock'
    router = ModelRelay(path, f'http://127.0.0.1:{upstream.server_port}/v1',
                        {'Authorization': 'Bearer host-secret'}, client_write_timeout=5,
                        **getattr(request, 'param', {}))
    router.start()
    def request(body=None, *, route='/responses', method='POST', compressed=False, extra=None):
        connection = http.client.HTTPConnection('localhost', timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.settimeout(5)
        connection.sock.connect(str(path))
        raw = json.dumps(body or {'model': MODEL, 'input': []}).encode()
        headers = {'Authorization': 'Bearer agent-spoof', 'X-Codex-Header': 'from-codex', **(extra or {})}
        if compressed:
            raw = gzip.compress(raw)
            headers['Content-Encoding'] = 'gzip'
        try:
            # Rejected routes/CONNECT need no body. Sending a body after the
            # server's immediate close races its 403 response with BrokenPipe.
            connection.request(method, route, raw if method == 'POST' and route == '/responses' else None, headers)
            response = connection.getresponse()
            request.headers = response.getheaders()
            return response.status, response.read()
        finally:
            connection.close()
    try:
        yield request, received, router
    finally:
        router.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def test_stream_auth_call_count_and_no_redirect(relay):
    request, received, router = relay
    status, body = request(compressed=True)
    assert status == 200 and body == b'data: fixture-event\n\n'
    path, headers, payload = received[0]
    assert path == '/v1/responses'
    assert headers['Authorization'] == 'Bearer host-secret'
    assert headers['X-Codex-Header'] == 'from-codex' and b'host-secret' not in body
    assert 'max_output_tokens' not in payload  # The relay adds no output cap of its own.
    status, body = request({'model': MODEL, 'metadata': {'redirect': True}})
    assert status == 502 and json.loads(body)['error']['message'] == (
        'The provider answered 302 Found; the relay never follows redirects')
    # Calls are counted, never refused: a session has no model-call cap.
    assert request() == (200, b'data: fixture-event\n\n')
    assert len(received) == router.calls == 3


def test_codex_and_provider_headers_pass_through_with_only_the_login_replaced(relay, tmp_path):
    """The ChatGPT backend hit its prompt cache only with Codex's session-id header (probe 2026-10-11:
    28,928 of 29,069 tokens cached on a repeat call with it, 0 without), so nothing Codex sends is dropped."""
    request, received, router = relay
    router.chatgpt_auth = chatgpt_login(tmp_path/'auth.json', time.time()+3600)
    session = '01a12b32-d483-73a0-ae10-5f2df8bbc9fe'
    codex = {'session-id': session, 'thread-id': session, 'x-codex-window-id': f'{session}:0',
             'x-openai-internal-codex-responses-lite': 'true', 'originator': 'codex_exec',
             'User-Agent': 'codex_exec/0.162.1', 'accept': 'text/event-stream',
             'authorization': 'Bearer agent-spoof', 'chatgpt-account-id': 'agent-spoof'}
    assert request(extra=codex)[0] == 200
    _, headers, _ = received[0]
    for name in ('session-id', 'thread-id', 'x-codex-window-id', 'x-openai-internal-codex-responses-lite',
                 'originator', 'User-Agent', 'accept'):
        assert headers[name] == codex[name]
    sent = [name.lower() for name in headers]
    assert sent.count('authorization') == sent.count('chatgpt-account-id') == sent.count('accept') == 1
    assert headers['Authorization'].startswith('Bearer h.') and headers['ChatGPT-Account-ID'] == 'acct-fixture'
    assert dict(request.headers)['x-codex-primary-used-percent'] == '12'


@pytest.mark.parametrize('relay', [{'native_models': True}], indirect=True)
def test_native_provider_receives_unprefixed_model_names(relay):
    request, received, router = relay
    assert request()[0] == 200
    assert request({'model': 'another-model'})[0] == 403
    assert [r[2]['model'] for r in received] == ['gpt-6-astra']


def chatgpt_login(path, expires):
    import base64
    claims = base64.urlsafe_b64encode(json.dumps({'exp': expires}).encode()).decode().rstrip('=')
    path.write_text(json.dumps({'auth_mode': 'chatgpt',
                                'tokens': {'access_token': f'h.{claims}.s', 'account_id': 'acct-fixture'}}))
    return path


def test_chatgpt_login_is_read_per_call_and_output_cap_is_not_forwarded(relay, tmp_path):
    request, received, router = relay
    router.chatgpt_auth = tmp_path/'missing.json'
    assert request()[0] == 401 and not received and router.calls == 0
    login = chatgpt_login(tmp_path/'auth.json', time.time()+3600)
    router.chatgpt_auth = login
    assert request({'model': MODEL, 'input': [], 'max_output_tokens': 1000})[0] == 200
    _, headers, payload = received[0]
    assert headers['Authorization'].startswith('Bearer h.') and headers['ChatGPT-Account-ID'] == 'acct-fixture'
    assert 'max_output_tokens' not in payload
    assert request({'model': MODEL, 'input': [], 'max_output_tokens': 10**6})[0] == 200
    assert 'max_output_tokens' not in received[1][2]


def test_api_output_cap_is_forwarded_unchanged(relay):
    request, received, router = relay
    assert request({'model': MODEL, 'input': [], 'max_output_tokens': 100000})[0] == 200
    assert received[0][2]['max_output_tokens'] == 100000 and router.calls == 1


def test_chatgpt_login_must_outlast_the_session(tmp_path):
    login = chatgpt_login(tmp_path/'auth.json', time.time()+600)
    assert model_relay.chatgpt_headers(login, lasting=60)['ChatGPT-Account-ID'] == 'acct-fixture'
    with pytest.raises(ValueError, match='expires before the session ends'):
        model_relay.chatgpt_headers(login, lasting=3600)
    (tmp_path/'key.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'sk-fixture'}))
    with pytest.raises(ValueError, match='Not a Codex ChatGPT login'):
        model_relay.chatgpt_headers(tmp_path/'key.json')


@pytest.mark.parametrize('status', [400, 401, 403, 408, 413, 422, 500])
def test_provider_status_and_error_pass_through_without_relay_retry(relay, status):
    request, received, router = relay
    assert request({'model': MODEL, 'metadata': {'status': status}}) == (status, b'private provider error body')
    assert dict(request.headers)['x-codex-primary-used-percent'] == '12'
    assert len(received) == router.calls == 1


@pytest.mark.parametrize('status', [429, 502, 503, 504])
def test_throttled_provider_is_retried_once_per_call_budget(relay, monkeypatch, status):
    delays = []
    monkeypatch.setattr(model_relay, 'retry_delay', lambda attempt, header: delays.append(header) or 0)
    request, received, router = relay
    body = {'model': MODEL, 'metadata': {'status': status, 'fail_first': 3, 'retry_after': 7}}
    assert request(body) == (200, b'data: fixture-event\n\n')
    assert len(received) == 4 and router.calls == 1
    assert delays == ['7', '7', '7']


@pytest.mark.parametrize('status', [429, 503])
def test_persistent_throttling_surfaces_status_after_retry_window(relay, monkeypatch, status):
    monkeypatch.setattr(model_relay, 'RETRY_WINDOW_SECONDS', .2)
    monkeypatch.setattr(model_relay, 'retry_delay', lambda attempt, header: .05)
    request, received, router = relay
    assert request({'model': MODEL, 'metadata': {'status': status}}) == (status, b'private provider error body')
    assert 2 <= len(received) <= 5 and router.calls == 1


def test_retry_delay_honours_retry_after_within_cap():
    assert model_relay.retry_delay(0, '12') == 12
    assert model_relay.retry_delay(0, '3600') == model_relay.RETRY_MAX_DELAY_SECONDS
    assert all(2**n/2 <= model_relay.retry_delay(n, None) <= 2**n for n in range(5))
    assert model_relay.retry_delay(20, 'Wed, 21 Oct 2015 07:28:00 GMT') <= model_relay.RETRY_MAX_DELAY_SECONDS


@pytest.mark.parametrize('body', [
    {'model': 'another-model'},
    {'model': MODEL, 'tools': [{'type': 'web_search'}]},
    {'model': MODEL, 'tools': [{'type': 'namespace', 'tools': [{'type': 'mcp'}]}]},
    {'model': MODEL, 'additional_tools': [{'type': 'web_search'}]},
    {'model': MODEL, 'plugins': [{'id': 'web'}]},
    {'model': MODEL, 'input': [{'type': 'input_image', 'image_url': 'https://example.org/image'}]},
    {'model': MODEL, 'input': [{'type': 'input_file', 'file_id': 'private'}]},
    {'model': MODEL, 'previous_response_id': 'another-session'},
])
def test_forbidden_routes_do_not_spend_budget(relay, body):
    request, received, router = relay
    assert request(body)[0] in {400, 403}
    assert request(route='/arbitrary')[0] == 403
    assert request(method='CONNECT')[0] == 403
    assert not received and router.calls == 0


@pytest.mark.parametrize('body, route, method, status, reason', [
    ({'model': 'another-model'}, '/responses', 'POST', 403,
     "Model 'another-model' is not this session's model (openai/gpt-6-astra)"),
    ({'model': MODEL, 'tools': [{'type': 'web_search'}]}, '/responses', 'POST', 403,
     'Only client-executed tools are allowed (no server-side or hosted tools)'),
    ({'model': MODEL, 'previous_response_id': 'x'}, '/responses', 'POST', 403,
     'previous_response_id is not allowed; send the conversation inline'),
    ({'model': MODEL, 'input': [{'type': 'input_file', 'file_id': 'x'}]}, '/responses', 'POST', 403,
     'Only inline data: images are allowed (no URLs or files for the provider to fetch)'),
    (None, '/arbitrary', 'POST', 403, 'The relay serves only POST /responses, not /arbitrary'),
    (None, '/responses', 'CONNECT', 403, 'The relay serves only POST /responses'),
])
def test_refusals_name_their_reason(relay, body, route, method, status, reason):
    request, received, router = relay
    answer = request(body, route=route, method=method)
    assert answer[0] == status and json.loads(answer[1]) == {
        'type': 'error', 'error': {'type': 'relay_refused', 'message': reason}}
    assert dict(request.headers)['Content-Type'] == 'application/json'
    assert not received and router.calls == 0


def test_unreachable_provider_is_a_502_with_its_error_and_no_call(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]  # Closed again: nothing listens there.
    router = ModelRelay(tmp_path/'relay.sock', f'http://127.0.0.1:{port}/v1', {'Authorization': 'Bearer k'})
    router.start()
    try:
        connection = http.client.HTTPConnection('localhost', timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.connect(str(tmp_path/'relay.sock'))
        connection.request('POST', '/responses', json.dumps({'model': MODEL, 'input': []}))
        response = connection.getresponse()
        error = json.loads(response.read())['error']
        assert response.status == 502 and error['message'].startswith(
            'The relay could not complete the provider call: ConnectionRefusedError: [Errno 111]')
        assert router.calls == 0
    finally:
        router.close()


def test_calls_beyond_the_slots_get_a_503_with_a_reason(tmp_path):
    release, arrived = threading.Event(), []
    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            arrived.append(1)
            release.wait(10)
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    assert model_relay.slot_count(0) == 8 and model_relay.slot_count(9) == 12
    router = ModelRelay(tmp_path/'relay.sock', f'http://127.0.0.1:{upstream.server_port}/v1', {}, slots=2)
    router.start()
    def call():
        connection = http.client.HTTPConnection('localhost', timeout=10)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.connect(str(tmp_path/'relay.sock'))
        connection.request('POST', '/responses', json.dumps({'model': MODEL, 'input': []}))
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    try:
        held = [threading.Thread(target=call) for _ in range(2)]
        for thread in held:
            thread.start()
        deadline = time.monotonic()+5
        while len(arrived) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        status, headers, body = call()
        assert status == 503 and headers['Retry-After'] == '1'
        assert json.loads(body)['error'] == {
            'type': 'overloaded_error',
            'message': 'The host model relay is serving its maximum of 2 concurrent calls; retry shortly'}
    finally:
        release.set()
        for thread in held:
            thread.join(5)
        router.close()
        upstream.shutdown()
        upstream.server_close()
    assert router.calls == 2


def test_image_history_larger_than_old_64_mib_cap_is_forwarded(relay):
    request, received, router = relay
    # Synthetic inline image data: local fixture inspects transport, not decoding.
    image = 'data:image/png;base64,' + 'A' * (65 * 1024 * 1024)
    assert request({'model': MODEL, 'input': [{'type':'input_image', 'image_url':image}]})[0] == 200
    assert received[0][2]['input'][0]['image_url'] == image
    assert router.calls == 1


def test_declared_body_above_256_mib_is_rejected_without_upstream(relay):
    _, received, router = relay
    assert MAX_BODY == 256 * 1024 * 1024
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(router.server.server_address)
        connection.sendall(f'POST /responses HTTP/1.1\r\nHost: test\r\nContent-Length: {MAX_BODY+1}\r\n\r\n'.encode())
        assert b'413' in connection.recv(4096).split(b'\r\n')[0]
    assert not received and router.calls == 0


def test_decompressed_body_limit_is_still_enforced(relay, monkeypatch):
    from harness.codex_cli import model_relay
    monkeypatch.setattr(model_relay, 'MAX_BODY', 1024)
    request, received, router = relay
    assert request({'model':MODEL, 'input':'A'*2048}, compressed=True)[0] == 413
    assert not received and router.calls == 0


def test_paused_reader_does_not_truncate_response(relay, monkeypatch):
    # Scale the old shared read/write deadline down; the independent delivery
    # allowance must survive a full Unix socket buffer until the client resumes.
    monkeypatch.setattr('harness.codex_cli.model_relay.REQUEST_TIMEOUT_SECONDS', .05)
    _, received, router = relay
    connection = http.client.HTTPConnection('localhost', timeout=5)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(5)
    connection.sock.connect(router.server.server_address)
    try:
        connection.request('POST', '/responses', json.dumps({'model': MODEL, 'metadata': {'large': True}}))
        response = connection.getresponse()
        time.sleep(.3)  # No reads: the response exceeds the socket buffer.
        assert response.status == 200
        assert response.read() == b'x' * (4*1024*1024)
        assert len(received) == router.calls == 1
    finally:
        connection.close()
