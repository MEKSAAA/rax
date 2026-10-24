"""Real quarantine checks. Reuse a locally installed image; never pull one."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from bench import OBSERVE, STEP, TASK
from services.controller.config import Limits
from services.controller.gateway import Gateway
from services.controller.sandbox import DockerSandbox as _DockerSandbox
from services.controller.storage import snapshot


# The robot tool names, defined at the top of the controllers these tests run in containers.
NAMES = f'STEP, OBSERVE = {STEP!r}, {OBSERVE!r}\n'


class DockerSandbox(_DockerSandbox):
    def run(self, *args, **kwargs):
        if kwargs.get('gateway') is None:
            class Backend:
                def tools(self):
                    return []
            gateway = Gateway(Backend(), None, audit=lambda _: None, charge=lambda *_: None,
                              refund=lambda *_: None, budget=lambda: {})
            kwargs.update(gateway=gateway, token=gateway.acquire())
        return super().run(*args, **kwargs)


@pytest.fixture
def docker_runtime(host_artifact_root):
    try:
        query = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True, timeout=15)
        if query.returncode:
            pytest.skip("Docker access and local python:3.12-slim image required")
        image = json.loads(query.stdout)[0]["Id"]
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")
    root = Path(__file__).resolve().parents[2] / "runtime"
    # Repository may be reached through a symlink; use the actual project root.
    root = Path(__file__).resolve().parents[3] / "runtime" if not root.exists() else root
    root = root.resolve()
    if not root.is_relative_to(host_artifact_root) or not os.access(root, os.W_OK):
        pytest.skip(f"Writable runtime/ under the artifact root {host_artifact_root} required")
    parent = root / "integration-tests"
    parent.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="controller-docker-", dir=parent))
    return image, output


def test_actual_container_quarantine_and_artifact_export(docker_runtime):
    image, output = docker_runtime
    src = output / "source"
    src.mkdir()
    (src / "controller.py").write_text('''
import json, os, socket, importlib.util, subprocess, sys
from pathlib import Path
def main(ctx):
    assert os.getuid() == 65534
    assert not Path("/var/run/docker.sock").exists()
    assert not Path(HOST_HOME).exists()
    assert not os.environ.get("OPENROUTER_API_KEY")
    assert importlib.util.find_spec("isaacsim") is None
    assert importlib.util.find_spec("isaaclab") is None
    print("python-print-checkpoint")
    print("python-stderr-checkpoint", file=sys.stderr)
    subprocess.run([sys.executable, "-c", "print('child-print-checkpoint')"], check=True)
    try:
        Path("/workspace/api/runtime.py").write_text("modified")
        raise AssertionError("SDK was writable")
    except OSError:
        pass
    for address in [("1.1.1.1", 443), ("127.0.0.1", 80)]:
        try:
            connection = socket.create_connection(address, timeout=0.2)
        except OSError:
            continue
        connection.close()
        raise AssertionError("Network was reachable")
    Path("/workspace/output/report.json").write_text(json.dumps({"isolated": True}))
'''.replace("HOST_HOME", repr(str(Path.home()))))
    snapshot(src, output / "bundle", image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    runner.preflight()
    result = runner.run(output / "bundle", mode="rehearsal", entrypoint="controller.py", output=output / "run")
    assert result["returncode"] == 0, (output / "run/stderr.log").read_text()
    assert json.loads((output / "run/files/report.json").read_text()) == {"isolated": True}
    assert "python-print-checkpoint" in (output / "run/stdout.log").read_text()
    assert "python-stderr-checkpoint" in (output / "run/stderr.log").read_text()
    assert "child-print-checkpoint" in (output / "run/stderr.log").read_text()
    assert not result["logs"]["stdout"]["truncated"]


def test_actual_container_mcp_closed_loop(docker_runtime):
    image, output = docker_runtime
    src = output / "source"
    src.mkdir()
    (src / "controller.py").write_text(NAMES + '''
import json
def main(ctx):
    def data(reply):
        return json.loads(reply["content"][0]["text"])
    for i in range(3):
        state = data(ctx.call(OBSERVE))
        assert state["step_id"] == i
        next_state = data(ctx.call(STEP, actions=[[0.0]*14]))
        assert next_state["step_id"] == i+1
''')
    class Backend:
        step = 0
        def call(self, name, arguments):
            if name == STEP:
                self.step += 1
            return {"step_id": self.step}, []
    backend = Backend()
    gateway = Gateway(backend, None, audit=lambda e: None, charge=lambda *_: None,
                      refund=lambda *args: None, budget=lambda: {})
    token = gateway.acquire()
    snapshot(src, output / "bundle", image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output / "bundle", mode="rehearsal", entrypoint="controller.py",
                        gateway=gateway, token=token, output=output / "run")
    assert result["returncode"] == 0, (output / "run/stderr.log").read_text()
    assert backend.step == 3


def test_local_png_to_gemini_through_real_container(docker_runtime):
    from PIL import Image
    from services.controller.gemini import GeminiRouter, MODEL
    image, output = docker_runtime
    src = output/'source'
    src.mkdir()
    Image.frombytes('RGB', (768, 768), os.urandom(768*768*3)).save(src/'crop.png')
    raw = (src/'crop.png').read_bytes()
    assert len(raw) > 1024*1024
    (src/'controller.py').write_text('''
import json
from pathlib import Path
def main(ctx):
    import base64
    raw = Path('/workspace/code/controller/crop.png').read_bytes()
    from_file = {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(raw).decode()}
    result = ctx.call('gemini_generate', messages=[{'role': 'user', 'content': [from_file]}])
    value = json.loads(result['content'][0]['text'])
    assert value['message']['content'] == 'received-local-image'
    assert value['usage']['total_tokens'] == 10
    print('local-image-roundtrip-ok')
''')
    captured, charged = [], []
    def send(payload):
        captured.append(payload)
        return {'model': MODEL, 'choices': [{'message': {'content': 'received-local-image'}}],
                'usage': {'total_tokens': 10}}
    gateway = Gateway(None, GeminiRouter('host-test-key', send=send),
        audit=lambda e: None, charge=lambda amount, cost: charged.append(amount), refund=lambda *args: None,
        budget=lambda: {'calls_used': len(charged)})
    token = gateway.acquire()
    snapshot(src, output/'bundle', image=image, max_bytes=4*1024*1024)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output/'bundle', mode='rehearsal', entrypoint='controller.py',
                        gateway=gateway, token=token, output=output/'run')
    assert result['returncode'] == 0, (output/'run/stderr.log').read_text()
    import base64
    sent = captured[0]['messages'][0]['content'][0]['image_url']['url']
    assert base64.b64decode(sent.split(',', 1)[1]) == raw
    assert len(charged) == 1
    assert 'local-image-roundtrip-ok' in (output/'run/stdout.log').read_text()


def test_function_call_roundtrip_through_isolated_python(docker_runtime):
    from services.controller.gemini import GeminiRouter, MODEL
    from test_gemini_contract import ASSISTANT, TOOLS
    image, output = docker_runtime
    src = output/'source'
    src.mkdir()
    (src/'tools.json').write_text(json.dumps(TOOLS))
    (src/'controller.py').write_text('''
import json
from pathlib import Path
def main(ctx):
    tools = json.loads(Path(__file__).with_name('tools.json').read_text())
    messages = [{'role':'user', 'content':'Use add to compute 2+3.'}]
    def generate(**options):
        reply = ctx.call('gemini_generate', messages=messages, tools=tools, **options)
        return json.loads(reply['content'][0]['text'])
    first = generate(tool_choice='required', reasoning={'effort':'low'})
    messages.append(first['message'])
    for call in first['message']['tool_calls']:
        assert call['function']['name'] == 'add'
        args = json.loads(call['function']['arguments'])
        messages.append({'role':'tool', 'tool_call_id':call['id'],
                         'content':json.dumps({'answer':args['a']+args['b']})})
    final = generate(tool_choice='none', response_format={'type':'json_object'})
    assert json.loads(final['message']['content']) == {'answer':5}
    assert final['budget']['calls_used'] == 2
    Path('/workspace/output/result.json').write_text(json.dumps(final))
''')
    sent, charged = [], []
    def send(payload):
        sent.append(payload)
        if len(sent) == 2:
            assert payload['messages'][1] == ASSISTANT
            assert json.loads(payload['messages'][2]['content']) == {'answer': 5}
        return {'model': MODEL, 'choices': [{'message': ASSISTANT if len(sent) == 1 else
                {'role': 'assistant', 'content': '{"answer":5}'},
                'finish_reason': 'tool_calls' if len(sent) == 1 else 'stop'}], 'usage': {'total_tokens': 10}}
    gateway = Gateway(None, GeminiRouter('host-only-key', send=send), audit=lambda _: None,
        charge=lambda amount, cost: charged.append(amount), refund=lambda *_: None, budget=lambda: {'calls_used': len(charged)})
    token = gateway.acquire()
    snapshot(src, output/'bundle', image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output/'bundle', mode='rehearsal', entrypoint='controller.py',
                        gateway=gateway, token=token, output=output/'run')
    assert result['returncode'] == 0, (output/'run/stderr.log').read_text()
    assert len(sent) == 2 and 'host-only-key' not in (output/'run/files/result.json').read_text()


def test_terminal_rehearsal_copies_files_but_denies_further_calls(docker_runtime):
    image, output = docker_runtime
    src = output/'source'
    src.mkdir()
    (src/'controller.py').write_text(NAMES + '''
from pathlib import Path
def main(ctx):
    ctx.call(STEP, actions=[[0.0]*14])
    for tool in (STEP, OBSERVE, 'gemini_generate'):
        try:
            ctx.call(tool)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Environment/API still accessible after terminal')
    Path('/workspace/output/final.txt').write_text('terminal saved')
''')
    class Backend:
        calls = []
        def call(self, name, arguments):
            self.calls.append(name)
            return {'episode_ended': True}, []
    backend = Backend()
    gateway = Gateway(backend, None, audit=lambda e: None, charge=lambda *_: None,
                      refund=lambda *args: None, budget=lambda: {})
    token = gateway.acquire()
    snapshot(src, output/'bundle', image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output/'bundle', mode='rehearsal', entrypoint='controller.py',
                        gateway=gateway, token=token, output=output/'run')
    assert result['reason'] == 'episode_ended' and result['returncode'] == 0
    assert backend.calls == [STEP]
    assert (output/'run/files/final.txt').read_text() == 'terminal saved'


def test_actual_container_timeout_kills_descendants(docker_runtime):
    image, output = docker_runtime
    src = output / "source"
    src.mkdir()
    (src / "controller.py").write_text("import subprocess, time\ndef main(ctx):\n    subprocess.Popen(['sleep','300'])\n    time.sleep(300)\n")
    snapshot(src, output / "bundle", image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(3, 128, 1, 32, 16, 100000))
    result = runner.run(output / "bundle", mode="rehearsal", entrypoint="controller.py", output=output / "run")
    assert result["reason"] == "timeout"
    assert not os.path.ismount(output/'run/writable')


@pytest.mark.parametrize('unsafe', ['symlink', 'hardlink'])
def test_evaluation_outputs_reject_links_and_release_mount(docker_runtime, unsafe):
    image, output = docker_runtime
    src = output/'source'
    src.mkdir()
    link = ('os.symlink("/workspace/code/controller/controller.py", "/workspace/output/link.py")' if unsafe == 'symlink'
            else 'Path("/workspace/output/normal.txt").write_text("data"); os.link("/workspace/output/normal.txt", "/workspace/output/alias.txt")')
    (src/'controller.py').write_text('import os\nfrom pathlib import Path\ndef main(ctx):\n    '+link+'\n')
    snapshot(src, output/'bundle', image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output/'bundle', mode='rehearsal', entrypoint='controller.py', output=output/'run')
    assert result['returncode'] == 0 and result['output_error']
    assert not (output/'run/files').exists()
    assert not os.path.ismount(output/'run/writable')


def test_evaluation_output_filesystem_enforces_byte_cap(docker_runtime):
    image, output = docker_runtime
    src = output/'source'
    src.mkdir()
    (src/'controller.py').write_text('''
import errno
from pathlib import Path
def main(ctx):
    try:
        Path('/workspace/output/oversized.bin').write_bytes(b'x'*(1024*1024))
    except OSError as error:
        assert error.errno == errno.ENOSPC
        Path('/workspace/output/oversized.bin').unlink()
        Path('/workspace/output/recovered.txt').write_text('cap-enforced')
    else:
        raise AssertionError('Output cap not enforced')
''')
    snapshot(src, output/'bundle', image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 65536))
    result = runner.run(output/'bundle', mode='rehearsal', entrypoint='controller.py', output=output/'run')
    assert result['returncode'] == 0 and not result['output_error']
    assert (output/'run/files/recovered.txt').read_text() == 'cap-enforced'


def test_actual_container_crash_and_truncation_logs(docker_runtime):
    image, output = docker_runtime
    src = output / "source"
    src.mkdir()
    (src / "controller.py").write_text("def main(ctx):\n    print('checkpoint-before-crash')\n    print('x' * (1024 * 1024 + 10))\n    raise RuntimeError('intentional-test-error')\n")
    snapshot(src, output / "bundle", image=image, max_bytes=100000)
    runner = DockerSandbox(image, Limits(30, 128, 1, 32, 16, 100000))
    result = runner.run(output / "bundle", mode="rehearsal", entrypoint="controller.py", output=output / "run")
    assert result["returncode"] == 1
    assert (output / "run/stdout.log").read_text().startswith("checkpoint-before-crash")
    assert result["logs"]["stdout"]["truncated"] is True
    assert result["logs"]["stdout"]["bytes_saved"] == 1024 * 1024
    assert "Traceback" in (output / "run/stderr.log").read_text()
    assert "intentional-test-error" in (output / "run/stderr.log").read_text()
    assert "checkpoint-before-crash" not in json.dumps(result)


def test_actual_container_rehearsal_and_formal(docker_runtime):
    """Real script lifecycle, deterministic fake task; no claimed robot success."""
    from services.controller.config import Configuration
    from services.controller.supervisor import Supervisor
    image, output = docker_runtime
    limits = Limits(30, 128, 1, 32, 16, 1000000)
    config = Configuration(output / 'trial', image, TASK, (0, 1), 2, 0,
                           'GPU-aaaa', limits, limits, limits, formal_eval_seed=1)
    class Backend:
        step = 0
        def start(self, **kwargs):
            self.step = 0
            return {}, []
        def call(self, name, arguments):
            if name == STEP:
                self.step += 1
            return {'step_id': self.step, 'episode_id': 'fake-episode'}, []
        def evaluate(self):
            return {'task_complete': self.step == 3}
        def close(self):
            pass
    src = output / 'source'
    src.mkdir()
    (src / 'controller.py').write_text(NAMES + '''
import json
def main(ctx):
    for i in range(3):
        value = json.loads(ctx.call(OBSERVE)['content'][0]['text'])
        assert value['step_id'] == i
        ctx.call(STEP, actions=[[0.0]*14])
    print('full-script-completed')
''')
    supervisor = Supervisor(config, Backend)
    try:
        gateway, token = supervisor.start_interactive()
        for i in range(3):
            reply = gateway.handle(token, {'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                                          'params': {'name': STEP, 'arguments': {}}})
            assert 'error' not in reply
        assert supervisor.finish_interactive()['evaluation']['task_complete']
        bundle, _ = supervisor.register(src)
        for formal in (False, True):
            result = supervisor.run(bundle, formal=formal)
            assert result['returncode'] == 0 and result['task_complete']
            assert result['start_step_id'] == 0 and result['end_step_id'] == 3
            assert not result['recording_errors']
            timing = result['timing']
            assert timing['controller_processing_seconds'] > 0
            assert timing['environment_setup_seconds'] > 0
            assert timing['environment_step_seconds'] > 0
            assert timing['mcp_tools'][STEP]['calls'] == 3
            assert timing['mcp_tools'][OBSERVE]['calls'] == 3
            assert timing['container_overhead_seconds'] > 0
            components = [v for k, v in timing.items() if k.endswith('_seconds')
                          and k not in {'total_seconds', 'wall_limit_seconds'}]
            assert sum(components) == pytest.approx(timing['total_seconds'])
            assert supervisor.backend is None
        assert supervisor.state['exploration_started'] == 2
        with pytest.raises(RuntimeError, match='no retry'):
            supervisor.run(bundle, formal=True)
    finally:
        supervisor.close()
