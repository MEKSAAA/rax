"""Claude Messages relay and launcher wiring; local fake provider, no external requests."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from harness.claude_cli import messages_relay
from harness.claude_cli.messages_relay import Credential, MessagesRelay, OAUTH_BETA

MODEL = 'anthropic/claude-opus-5.5'
SSE = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":11,'
       b'"cache_read_input_tokens":5,"output_tokens":1}}}\n\n'
       b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"ok"}}\n\n'
       b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":7,"cost":0.002}}\n\n')


def private(path, text):
    path.write_text(text)
    path.chmod(0o600)
    return path


@pytest.fixture
def relay(tmp_path, request):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, dict(self.headers), body))
            status = (body.get('metadata') or {}).get('status', 200)
            self.send_response(status)
            self.send_header('request-id', 'req_fixture')
            self.send_header('anthropic-ratelimit-requests-remaining', '41')
            if status == 200 and body.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(SSE)
            else:
                data = json.dumps({'type': 'error', 'error': {'type': 'invalid_request_error'}} if status != 200
                                  else {'usage': {'input_tokens': 3, 'output_tokens': 2}}).encode()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    provider, credential = getattr(request, 'param', ('openrouter', None))
    key = private(tmp_path/'key', 'host-secret')
    if provider == 'claude-login':
        credential = Credential('claude-login', key_file=private(tmp_path/'claude-experiment.token', 'login-token'))
    else:
        credential = Credential(provider, key_file=key)
    path = tmp_path/'relay.sock'
    usage = tmp_path/'usage.jsonl'
    router = MessagesRelay(path, credential, base_url=f'http://127.0.0.1:{upstream.server_port}/api',
                           models=[MODEL], client_write_timeout=5, usage_log=usage)
    router.start()

    def call(body=None, *, route='/v1/messages?beta=true', headers=None):
        connection = http.client.HTTPConnection('localhost', timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.settimeout(5)
        connection.sock.connect(str(path))
        payload = {'model': MODEL, 'max_tokens': 64, 'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]}
        raw = json.dumps(payload if body is None else body).encode()
        sent = {'Authorization': 'Bearer host-relay', 'X-Claude-Header': 'from-claude',
                'anthropic-version': '2023-06-01', 'anthropic-beta': 'interleaved-thinking-2025-05-14',
                **(headers or {})}
        try:
            connection.request('POST', route, raw, sent)
            response = connection.getresponse()
            call.headers = dict(response.getheaders())
            return response.status, response.read()
        finally:
            connection.close()
    try:
        yield call, received, router, usage
    finally:
        router.close()
        upstream.shutdown()
        upstream.server_close()


def test_stream_is_forwarded_with_host_credential_and_usage_logged(relay):
    call, received, router, usage = relay
    assert call() == (200, SSE)
    path, headers, _ = received[0]
    assert path == '/api/v1/messages?beta=true'
    # Everything Claude Code sends passes through; only the credential is the host's.
    assert headers['Authorization'] == 'Bearer host-secret' and headers['X-Claude-Header'] == 'from-claude'
    assert [name.lower() for name in headers].count('authorization') == 1 and 'x-api-key' not in headers
    assert headers['anthropic-beta'] == 'interleaved-thinking-2025-05-14'
    assert headers['anthropic-version'] == '2023-06-01'
    # And everything the provider answers passes back.
    assert call.headers['request-id'] == 'req_fixture'
    assert call.headers['anthropic-ratelimit-requests-remaining'] == '41'
    row = json.loads(usage.read_text())
    assert row['model'] == MODEL and row['input_tokens'] == 11 and row['cache_read_input_tokens'] == 5
    assert row['output_tokens'] == 7 and row['cost_usd'] == 0.002


def test_calls_count_messages_but_not_token_counting(relay):
    call, received, router, _ = relay
    assert call(route='/v1/messages/count_tokens')[0] == 200
    # Calls are counted, never refused: a session has no model-call cap.
    assert call()[0] == 200 and call()[0] == 200 and call()[0] == 200
    assert router.calls == 3 and len(received) == 4


@pytest.mark.parametrize('body', [
    {'model': 'anthropic/claude-haiku-4.5', 'max_tokens': 64, 'messages': []},  # Not allowlisted.
    {'model': MODEL, 'max_tokens': 64, 'messages': [], 'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}]},
    {'model': MODEL, 'max_tokens': 64, 'messages': [], 'mcp_servers': [{'url': 'https://x'}]},
    {'model': MODEL, 'max_tokens': 64, 'messages': [{'role': 'user', 'content': [
        {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.com/a.png'}}]}]},
    {'model': MODEL, 'max_tokens': 64, 'messages': [{'role': 'user', 'content': [{'type': 'tool_result', 'content': [
        {'type': 'document', 'source': {'type': 'file', 'file_id': 'f'}}]}]}]},
    {'model': MODEL, 'max_tokens': 10**6, 'messages': []},
])
def test_forbidden_requests_are_refused_without_spending(relay, body):
    call, received, router, _ = relay
    assert call(body)[0] in (400, 403)
    assert not received and router.calls == 0


@pytest.mark.parametrize('body, route, status, kind, reason', [
    ({'model': 'other', 'max_tokens': 8, 'messages': []}, '/v1/messages', 403, 'permission_error',
     f"Model 'other' is not served to this session; allowed: {MODEL}"),
    ({'model': MODEL, 'max_tokens': 8, 'messages': [], 'mcp_servers': []}, '/v1/messages', 403, 'permission_error',
     'Provider-side MCP servers and containers are not allowed'),
    ({'model': MODEL, 'max_tokens': 10**6, 'messages': []}, '/v1/messages', 400, 'invalid_request_error',
     'max_tokens must be an integer 1..128000'),
    ({'model': MODEL}, '/v1/files', 403, 'permission_error',
     'The relay serves only POST /v1/messages and /v1/messages/count_tokens, not /v1/files'),
])
def test_refusals_name_their_reason(relay, body, route, status, kind, reason):
    call, received, router, _ = relay
    answer = call(body, route=route)
    assert answer[0] == status and json.loads(answer[1]) == {'type': 'error', 'error': {'type': kind, 'message': reason}}
    assert not received and router.calls == 0


def test_unreachable_provider_is_a_502_with_its_error_and_no_call(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]  # Closed again: nothing listens there.
    router = MessagesRelay(tmp_path/'relay.sock', Credential('openrouter', key_file=private(tmp_path/'key', 'k')),
                           base_url=f'http://127.0.0.1:{port}/api', models=[MODEL])
    router.start()
    try:
        connection = http.client.HTTPConnection('localhost', timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.connect(str(tmp_path/'relay.sock'))
        connection.request('POST', '/v1/messages', json.dumps({'model': MODEL, 'max_tokens': 8, 'messages': []}))
        response = connection.getresponse()
        error = json.loads(response.read())['error']
        assert response.status == 502 and error['message'].startswith(
            'The relay could not complete the provider call: ConnectionRefusedError: [Errno 111]')
        assert router.calls == 0
    finally:
        router.close()


def test_client_tools_and_inline_images_pass(relay):
    call, received, _, _ = relay
    body = {'model': MODEL, 'max_tokens': 64, 'stream': True,
            'tools': [{'name': 'Bash', 'input_schema': {'type': 'object'}},
                      {'name': 'mcp__auto_research__exploration_status', 'input_schema': {'type': 'object'}}],
            'messages': [{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}}]}]}
    assert call(body)[0] == 200 and received


def test_provider_errors_keep_their_type(relay):
    call, _, _, _ = relay
    status, body = call({'model': MODEL, 'max_tokens': 64, 'messages': [], 'metadata': {'status': 400}})
    assert status == 400 and json.loads(body)['error']['type'] == 'invalid_request_error'
    assert call.headers['request-id'] == 'req_fixture'


@pytest.mark.parametrize('relay', [('claude-login', None)], indirect=True)
def test_claude_login_uses_the_oauth_token_and_beta(relay):
    call, received, _, _ = relay
    assert call()[0] == 200
    headers = received[0][1]
    assert headers['Authorization'] == 'Bearer login-token'
    assert headers['anthropic-beta'].split(',') == ['interleaved-thinking-2025-05-14', OAUTH_BETA]


def test_credentials_must_be_private(tmp_path):
    loose = tmp_path/'loose'
    loose.write_text('k')
    loose.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        Credential('openrouter', key_file=loose)
    with pytest.raises(ValueError, match='Unknown provider'):
        Credential('other')


def test_claude_login_refuses_the_personal_login(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    with pytest.raises(ValueError, match='experiment setup-token'):
        Credential('claude-login')
    personal = tmp_path/'.claude'
    personal.mkdir()
    login = json.dumps({'claudeAiOauth': {'accessToken': 't', 'expiresAt': (time.time()+3600)*1000}})
    for path in (personal/'.credentials.json', personal/'token', tmp_path/'elsewhere/.credentials.json'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError, match='personal'):
            Credential('claude-login', key_file=private(path, 'token'))
    # The login JSON shape is refused wherever it is copied.
    with pytest.raises(ValueError, match='credentials file'):
        Credential('claude-login', key_file=private(tmp_path/'copied.token', login))
    assert Credential('claude-login', key_file=private(tmp_path/'experiment.token', 'sk-ant-oat01-x')).headers() == {
        'Authorization': 'Bearer sk-ant-oat01-x'}


def test_container_command_mounts_claude_and_no_credential(tmp_path):
    from harness.claude_cli import auto_research as claude
    from test_controller_backend import configuration
    config = configuration(tmp_path)
    home, workspace = tmp_path/'claude-home', tmp_path/'agent-workspace'
    home.mkdir(); workspace.mkdir()
    deployed = {'workspace': workspace, 'claude_home': home, 'claude': tmp_path/'claude', 'model': MODEL,
                'bridge': tmp_path/'bridge.py'}
    command = claude.container_command(config, deployed, tmp_path/'relay', 'rax-agent-x', prompt='go')
    text = ' '.join(map(str, command))
    assert '--network=none' in command and '--read-only' in command
    assert 'AGENT_BINARY=/opt/claude/claude' in command and 'CLAUDE_CONFIG_DIR=/claude-home' in command
    assert command[command.index('--effort')+1] == 'xhigh'
    assert 'HOME=/claude-home' in command  # Not the project directory, or project skills are skipped.
    # Per launch, so sessions deployed before these settings get them when resumed.
    assert 'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0' in command
    assert 'CLAUDE_CODE_AUTO_COMPACT_WINDOW=250000' in command
    assert 'ENABLE_PROMPT_CACHING_1H=1' in command and 'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h' in command
    assert f'src={home}/settings.json,dst=/claude-home/settings.json,readonly' in text
    assert command[-2:] == ['--', 'go'] and '-p' in command and 'WebSearch' in command
    assert 'host-secret' not in text and 'credentials' not in text


def test_agent_cli_is_validated(tmp_path):
    from dataclasses import replace
    from test_controller_backend import configuration
    config = configuration(tmp_path)
    assert replace(config, agent_cli='claude').agent_cli == 'claude'
    with pytest.raises(ValueError, match='agent_cli'):
        replace(config, agent_cli='other')


def test_agent_containers_have_no_gpu_policy_package_or_weights(tmp_path):
    from harness.claude_cli import auto_research as claude
    from harness.codex_cli import auto_research as codex
    from test_controller_backend import configuration
    home, workspace = tmp_path/'claude-home', tmp_path/'agent-workspace'
    home.mkdir(); workspace.mkdir()
    deployed = {'workspace': workspace, 'claude_home': home, 'claude': tmp_path/'claude', 'model': MODEL,
                'bridge': tmp_path/'bridge.py', 'codex_home': home, 'codex': tmp_path/'codex'}
    config = configuration(tmp_path)
    for cli in (claude, codex):
        command = cli.container_command(config, deployed, tmp_path/'relay', 'n', prompt='go')
        assert 'PYTHONPATH=/workspace' in command, cli.__name__
        assert '--gpus' not in command and not any('NVIDIA' in part for part in command), cli.__name__
        assert not any('/pretrained' in part or 'polic' in part for part in command), cli.__name__


def test_agent_and_sandbox_thread_pools_follow_the_cpu_quota(tmp_path, monkeypatch):
    """nproc reports every host core under --cpus; uncapped numpy pools exhausted --pids-limit
    and took the agent CLI down with them."""
    from harness.claude_cli import auto_research as claude
    from harness.codex_cli import auto_research as codex
    from services.controller.sandbox import DockerSandbox, THREAD_VARIABLES
    from test_controller_backend import configuration
    config = configuration(tmp_path)
    home, workspace = tmp_path/'claude-home', tmp_path/'agent-workspace'
    home.mkdir(); workspace.mkdir()
    deployed = {'workspace': workspace, 'claude_home': home, 'claude': tmp_path/'claude', 'model': MODEL,
                'bridge': tmp_path/'bridge.py', 'codex_home': home, 'codex': tmp_path/'codex'}
    monkeypatch.setattr('os.path.ismount', lambda _: True)
    commands = {
        'claude': (claude.container_command(config, deployed, tmp_path/'relay', 'n', prompt='go'), config.training),
        'codex': (codex.container_command(config, deployed, tmp_path/'relay', 'n', prompt='go'), config.training),
        'sandbox': (DockerSandbox(config.image, config.development).command(
            'n', tmp_path, 'rehearsal', 'controller.py', writable=tmp_path/'output'), config.development)}
    for name, (command, limits) in commands.items():
        threads = max(1, -(-int(limits.cpus*1000)//1000))
        assert all(f'{variable}={threads}' in command for variable in THREAD_VARIABLES), name


def test_an_exhausted_usage_window_is_returned_at_once_and_remembered(tmp_path):
    hits = []
    reset = time.time()+3600

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            hits.append(1)
            data = b'{"type":"error","error":{"type":"rate_limit_error"}}'
            self.send_response(429)
            self.send_header('anthropic-ratelimit-unified-status', 'rejected')
            self.send_header('anthropic-ratelimit-unified-reset', str(int(reset)))
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    credential = Credential('claude-login', key_file=private(tmp_path/'claude-experiment.token', 'login-token'))
    router = MessagesRelay(tmp_path/'relay.sock', credential, base_url=f'http://127.0.0.1:{upstream.server_port}',
                           models=[MODEL], client_write_timeout=5)
    router.start()
    try:
        for _ in range(2):
            connection = http.client.HTTPConnection('localhost', timeout=5)
            connection.sock = socket.socket(socket.AF_UNIX)
            connection.sock.connect(str(tmp_path/'relay.sock'))
            connection.request('POST', '/v1/messages', json.dumps(
                {'model': MODEL, 'max_tokens': 8, 'messages': [{'role': 'user', 'content': 'hi'}]}).encode(),
                {'Content-Type': 'application/json'})
            response = connection.getresponse()
            assert response.status == 429 and json.loads(response.read())['error']['type'] == 'rate_limit_error'
            connection.close()
        assert hits == [1] and int(router.limited_until) == int(reset)  # No retry window, no second upstream call.
    finally:
        router.close()
        upstream.shutdown()
        upstream.server_close()


class FakeRelay:
    limited_until = 0.0


def test_the_agent_continues_its_conversation_after_the_usage_reset(tmp_path, monkeypatch):
    from harness.claude_cli import auto_research as claude
    monkeypatch.setattr(claude, 'latest_claude_session', lambda home: 'conv')
    relay, runs, waits, slept = FakeRelay(), [], [], []
    def run(prompt):
        runs.append(prompt)
        if len(runs) == 1:
            relay.limited_until = time.time()+120  # The window ran out during this run.
        return 1
    deployed = {'claude_home': tmp_path}
    def reopen(seconds):
        slept.append(seconds)
        relay.limited_until = time.time()-1  # Real time passes the reset while sleeping.
    code = claude.until_usage_resets(run, relay, deployed=deployed, prompt='go', interactive=False,
                                     on_wait=lambda until, waited: waits.append(until), sleep=reopen)
    assert code == 1 and runs == ['go', claude.USAGE_PROMPT]
    assert 175 < slept[0] <= 180 and deployed['resume_session'] == 'conv'
    assert waits[0] > time.time() and waits[1] is None


@pytest.mark.parametrize('interactive', [True, False])
def test_no_usage_wait_when_interactive_or_not_limited(tmp_path, interactive):
    from harness.claude_cli import auto_research as claude
    relay, runs = FakeRelay(), []
    def run(prompt):
        runs.append(prompt)
        if interactive:
            relay.limited_until = time.time()+120
        return 0
    claude.until_usage_resets(run, relay, deployed={}, prompt='go', interactive=interactive,
                              sleep=lambda s: pytest.fail('waited'))
    assert runs == ['go']


def test_usage_log_keeps_cache_writes_by_lifetime(tmp_path):
    log = messages_relay.UsageLog(tmp_path/'usage.jsonl')
    log.record(MODEL, {'input_tokens': 2, 'cache_creation_input_tokens': 30, 'cache_read_input_tokens': 5,
                       'cache_creation': {'ephemeral_5m_input_tokens': 10, 'ephemeral_1h_input_tokens': 20}})
    row = json.loads((tmp_path/'usage.jsonl').read_text())
    assert row['cache_creation_5m'] == 10 and row['cache_creation_1h'] == 20 and row['cache_creation_input_tokens'] == 30


def test_preflight_checks_the_experiment_token(tmp_path, monkeypatch, capsys):
    """Every claude-login launch and resume checks the one experiment token first."""
    from harness.claude_cli import auto_research as claude
    from harness.claude_cli import experiment_login as login
    monkeypatch.setenv('RAX_CLAUDE_EXPERIMENT_DIR', str(tmp_path))
    with pytest.raises(RuntimeError, match='No Claude experiment token'):
        claude.preflight_login()
    login.install('token-a', {'email': 'a@x'})
    sent = []
    answer = {'code': 200}
    def request(method, path, headers, body=None, timeout=30):
        sent.append(headers['Authorization'])
        if isinstance(answer['code'], Exception):
            raise answer['code']
        return answer['code'], b''
    monkeypatch.setattr(login, '_request', request)
    monkeypatch.setattr(login, 'token_account', lambda headers, timeout=30: 'a@x')
    result = claude.preflight_login()
    assert sent == ['Bearer token-a'] and result['verified'] and result['email'] == 'a@x'
    assert result['token_file'] == str(login.token_file())
    answer['code'] = 401
    with pytest.raises(RuntimeError, match=r'rejected \(HTTP 401\)'):
        claude.preflight_login()
    # No network is not a verdict on the token: reported, not blocking.
    answer['code'] = ConnectionRefusedError(111, 'Connection refused')
    assert claude.preflight_login() is None
    assert ('Could not verify the Claude experiment token (ConnectionRefusedError: [Errno 111] Connection refused); '
            'continuing') in capsys.readouterr().err


def test_the_frontend_runs_in_its_own_session_without_operator_variables(tmp_path, monkeypatch):
    from harness.codex_cli import auto_research
    monkeypatch.setenv('RAX_ARTIFACT_ROOT', '/elsewhere')
    started = {}
    monkeypatch.setattr(auto_research.subprocess, 'Popen', lambda args, **kwargs: started.update(kwargs, args=args))
    with auto_research.open_frontend_log(tmp_path) as log:
        auto_research.start_frontend(['python', '-m', 'services.controller.frontend'], log)
    assert started['start_new_session'] is True  # A terminal's Ctrl+C or hangup reaches only the launcher.
    assert started['stdout'] is log and started['stderr'] is log and started['cwd'] == auto_research.PROJECT
    assert started['env']['PYTHONPATH'] == str(auto_research.PROJECT)
    assert not any(name.startswith('RAX_') for name in started['env'])
