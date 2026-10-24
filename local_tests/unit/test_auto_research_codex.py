"""Actual native Codex and the robot MCP tools against a local fake Responses endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import json
from pathlib import Path
import shutil
import shlex
import threading
import time
import tomllib

import pytest

from bench import STEP

from test_bundle_sandbox import sandbox_runtime
from test_controller_backend import configuration
from harness import agent_host
from harness.codex_cli.auto_research import PID_FILE, PROJECT, deploy, launch


def secret_probes():
    """Every credential path the sandbox hides, and the files directly inside each such
    directory: a directory is never readable as bytes, hidden or not."""
    secrets = agent_host.host_secrets()
    return [*map(str, secrets),
            *(str(path) for secret in secrets if secret.is_dir()
              for path in sorted(secret.iterdir()) if path.is_file())]


@pytest.mark.parametrize('case', ['none', 'pause'])
def test_real_native_codex_shell_tools_and_sandbox(sandbox_runtime, monkeypatch, case):
    # A regression must fail this fixture, not inherit the 20-day call timeout.
    monkeypatch.setattr('harness.codex_cli.auto_research.CALL_SECONDS', 120)
    root = sandbox_runtime
    codex = shutil.which('codex')
    if not codex:
        pytest.skip('Codex CLI is not installed')
    config = configuration(root)
    workspace = config.root/'agent-workspace'
    secret = root/'test-key'
    requests, pauses = [], []
    smoke = '''from pathlib import Path
import os, socket, struct, zlib, importlib.util
WORKSPACE = Path(WORKSPACE_PATH)
assert Path.cwd() == WORKSPACE and os.environ['PYTHONPATH'] == str(WORKSPACE)
# Hidden from the agent's commands: the repository, the session's private files (its key and
# Codex home), the operator's credentials, and host sockets that would leave the sandbox.
for path in HIDDEN:
    try:
        Path(path).read_bytes()
    except OSError:
        pass
    else:
        raise AssertionError(f'{path} is readable')
for path in ('/var/run/docker.sock', '/run/docker.sock'):
    if os.path.exists(path):
        with socket.socket(socket.AF_UNIX) as host:
            try:
                host.connect(path)
            except OSError:
                pass
            else:
                raise AssertionError(f'{path} is reachable')
# As with native Codex, a key-based login's key reaches the agent's commands; nothing else does.
leaked = [name for name in os.environ if 'KEY' in name or 'TOKEN' in name or 'SECRET' in name]
assert leaked == ['RAX_MODEL_KEY'], leaked
# Agents have no GPU, policy package or pretrained weights.
assert not os.environ.get('CUDA_VISIBLE_DEVICES') and not os.environ.get('NVIDIA_VISIBLE_DEVICES')
for device in Path('/dev').glob('nvidia*'):
    try:
        open(device, 'rb').close()
    except OSError:
        pass
    else:
        raise AssertionError(f'{device} is usable')
assert not Path('/pretrained').exists() and not (WORKSPACE/'.agents/skills/policies').exists()
assert importlib.util.find_spec('robot_policy') is None
# Writes stay in the workspace, and Codex's own project configuration is read-only.
for path in (WORKSPACE/'.codex/config.toml', WORKSPACE.parent/'escape.txt'):
    try:
        path.write_text('x')
    except OSError:
        pass
    else:
        raise AssertionError(f'{path} is writable')
from api.runtime import Context
assert callable(Context.call) and callable(Context.close)
with Context() as ctx:
    assert 'manual_success_confirmed' in ctx.call('status')['content'][0]['text']
Path('python-mcp-ready.txt').write_text('shared-endpoint-ready')
assert importlib.util.find_spec('api.runtime').origin == str(WORKSPACE/'api/runtime.py')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=.2)
except OSError:
    pass
else:
    raise AssertionError('Internet accessible')
import subprocess
subprocess.run(['git', '--version'], check=True)
subprocess.run(['rg', '--version'], check=True)
Path('startup.txt').write_text('native-python-ready')
with Path('execution-count.txt').open('a') as stream:
    stream.write('executed\\n')
def chunk(kind, data):
    return struct.pack('!I', len(data))+kind+data+struct.pack('!I', zlib.crc32(kind+data))
png = bytes([137,80,78,71,13,10,26,10])
png += chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
png += chunk(b'IDAT', zlib.compress((bytes([0])+bytes([255,0,0])*32)*32))
png += chunk(b'IEND', b'')
Path('smoke.png').write_bytes(png)
print('native-python-ready')
'''.replace('WORKSPACE_PATH', repr(str(workspace))).replace('HIDDEN', repr([
        str(PROJECT/'AGENTS.md'), str(secret), str(config.root/'codex-home/config.toml'),
        str(config.root/'configuration.json'), *secret_probes()]))
    action = '''text({inventory: ALL_TOOLS.map(t => t.name),
process: typeof process, require: typeof require, fetch: typeof fetch});
const status = ALL_TOOLS.find(t => t.name.endsWith('__status'));
text(await tools[status.name]({}));
text(await tools.exec_command({cmd: COMMAND, yield_time_ms: 10000}));
const result = await tools.view_image({path: SMOKE});
image(result.image_url);
'''.replace('COMMAND', json.dumps('python -c '+shlex.quote(smoke))).replace('SMOKE', json.dumps(str(workspace/'smoke.png')))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.headers.get('Authorization') == 'Bearer fixture-private-secret'
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            request = json.loads(raw)
            requests.append(request)
            message = {'id': 'msg_local', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': 'Local inventory check complete.', 'annotations': []}]}
            if len(requests) == 1:
                message = {'id': 'tool_local', 'type': 'custom_tool_call', 'call_id': 'call_local',
                           'name': 'exec', 'namespace': 'functions', 'input': action}
            response = {'id': 'resp_local', 'object': 'response', 'created_at': 1, 'status': 'completed',
                        'model': request['model'], 'output': [message],
                        'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
            events = [
                {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
                {'type': 'response.output_item.added', 'output_index': 0, 'item': {**message, 'status': 'in_progress', 'content': []}},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': message},
                {'type': 'response.completed', 'response': response}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if case == 'pause' and len(requests) == 1:
                # Emit the tool while keeping its model stream open, then freeze the agent as
                # register and rehearse do (agent_host.frozen on the launcher's pid file).
                prefix = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events[:-1]).encode()
                self.wfile.write(prefix)
                self.wfile.flush()
                deadline = time.monotonic()+20
                while not (workspace/'startup.txt').exists():
                    if time.monotonic() >= deadline:
                        pauses.append('tool did not start')
                        return
                    time.sleep(.02)
                with agent_host.frozen(config.root/PID_FILE):
                    pauses.append('paused')
                    time.sleep(7)
                pauses.append('unpaused')
                time.sleep(.2)  # Allow expired timers to fire before the final event.
                self.wfile.write(data[len(prefix):])
                return
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = root/'provider'
        provider.mkdir()
        (provider/'openrouter.config.toml').write_text(f'''model_provider="openrouter"
[model_providers.openrouter]
name="Local fixture"
base_url="http://127.0.0.1:{server.server_port}"
wire_api="responses"
request_max_retries=0
stream_max_retries=0
''')
        secret.write_text('fixture-private-secret')
        secret.chmod(0o600)
        deployed = deploy(config, provider_home=provider, codex=codex, key_file=secret)
        assert deployed['workspace'] == workspace
        settings = tomllib.loads((deployed['codex_home']/'config.toml').read_text())
        # Codex's own defaults: only the login's provider, the sandbox and the robot tools are set.
        assert settings['model_providers']['session']['base_url'] == f'http://127.0.0.1:{server.server_port}'
        assert settings['model_providers']['session']['env_key'] == 'RAX_MODEL_KEY'
        assert 'fixture-private-secret' not in (deployed['codex_home']/'config.toml').read_text()
        assert settings['approval_policy'] == 'never' and settings['web_search'] == 'disabled'
        assert settings['mcp_servers']['auto_research']['args'][0] == '-I'
        assert not {'model_auto_compact_token_limit', 'model_auto_compact_token_limit_scope'} & set(settings)
        from services.controller.sandbox import deadline
        with deadline(120):  # Sessions have no wall clock: the fixture bounds its own run.
            result = launch(config, deployed, prompt='Local infrastructure smoke. Do not start a robot episode.',
                            capture=True)
        stdout, stderr = result.stdout, result.stderr
        (root/'codex-stdout.jsonl').write_text(stdout)
        (root/'codex-stderr.log').write_text(stderr)
        assert requests, stderr
        (root/'model-requests.json').write_text(json.dumps(requests))
        if case == 'pause':
            # A freeze longer than a stream timer does not fail the open model stream.
            assert pauses == ['paused', 'unpaused'], (pauses, stdout, stderr)
        assert result.returncode == 0, (stdout, stderr)
        assert len(requests) == 2
        events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
        status_calls = [event for event in events if event.get('type') == 'item.completed'
                        and event.get('item', {}).get('type') == 'mcp_tool_call'
                        and event['item'].get('tool') == 'status']
        assert len(status_calls) == 1
        results = [item for request in requests for item in request.get('input', [])
                   if item.get('type') == 'custom_tool_call_output']
        assert results, (stdout, stderr)
        serialized = json.dumps(results)
        assert 'native-python-ready' in serialized, serialized[-3000:]
        assert (workspace/'execution-count.txt').read_text() == 'executed\n'
        assert 'status' in serialized and 'manual_success_confirmed' in serialized
        unescaped = serialized.replace('\\"', '"')
        for global_name in ('process', 'require', 'fetch'):
            assert f'"{global_name}":"undefined"' in unescaped.replace(' ', '')
        # The executor's own tool registry: Codex's tools and the robot tools, no web or subagents.
        for banned in ('web__run', 'web_search', 'spawn_agent', '__operate', '__research_'):
            assert banned not in serialized
        assert 'start_episode' in serialized and 'exec_command' in serialized and 'write_stdin' in serialized
        assert 'apply_patch' in serialized and 'view_image' in serialized
        assert STEP in serialized and 'gemini_generate' in serialized
        assert (workspace/'startup.txt').read_text() == 'native-python-ready'
        assert (workspace/'python-mcp-ready.txt').read_text() == 'shared-endpoint-ready'
        assert 'data:image/' in serialized  # The workspace image reached the next model request.
        state = json.loads((config.root/'state.json').read_text())
        assert state['exploration_started'] == 0 and not state['formal_reserved']
        assert not state['interactive_success']
        assert not (config.root/PID_FILE).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

