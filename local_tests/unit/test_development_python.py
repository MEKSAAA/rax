"""Real development-container Python and direct MCP share one exploration scene.

Robot physics are deterministic test doubles here; no task-success claim.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import uuid

from PIL import Image
import pytest

from auto_research_agent.api.runtime import Context
from services.controller.frontend import ResearchFrontend
from services.controller.supervisor import Supervisor
from services.mcp_reply import _agent_safe
from test_controller_backend import configuration
from test_controller_docker import docker_runtime
from bench import CAMERA, OBSERVE, STEP

CAMERAS = (CAMERA,)
# The robot tool names, defined at the top of the scripts these tests run as the agent.
NAMES = f'STEP, OBSERVE = {STEP!r}, {OBSERVE!r}\n'


def native_row(mode, t=0):
    target = {arm: {'position': [.001, 0, 0], 'quaternion_wxyz': [1, 0, 0, 0],
                   'gripper_closed': False, 'gripper_opening': .7} for arm in ('left', 'right')}
    return {'step_id': t+1, 'observation_step_id': t, 'executed_action': [0.]*6+[.7]+[0.]*6+[.7],
            'episode_ended': False, 'eef_action_mode': mode.removeprefix('eef_'), 'requested_eef_target': target}


def reply(t, row=None):
    image = io.BytesIO()
    Image.new('RGB', (32, 32), (t*20, 100, 50)).save(image, format='PNG')
    block = {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(image.getvalue()).decode()}
    value = {'episode_id': 'episode', 'step_id': t, 'states': [t*.01]*14,
             'attachments': [{'kind': 'rgb', 'camera': c, 'content_index': i+1} for i, c in enumerate(CAMERAS)]}
    if row is not None:
        value['transition'] = {'steps': [row]}
    return {'content': [{'type': 'text', 'text': json.dumps(_agent_safe(value))}, *[block]*len(CAMERAS)]}


def unpack(response):
    return json.loads(response['content'][0]['text'])


class NativeFrames:
    """Deterministic fake native backend; genuine MCP/runner path, no task claim."""
    def __init__(self, workspace, terminal_at=4):
        self.frame_workspace = workspace
        self.step, self.serial, self.success = 0, 0, False
        self.terminal_at, self.calls = terminal_at, []

    def start(self, **_):
        self.step = 0
        return {}, []

    def evaluate(self):
        return {'task_complete': self.success}

    def close(self):
        pass

    def tools(self):
        return [{'name': name} for name in (STEP, 'private_step_eef', OBSERVE)]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == OBSERVE:
            response = reply(self.step)
            return unpack(response), response['content'][1:]
        self.serial += 1
        mode = 'joint' if name == STEP else 'eef_absolute'
        targets = arguments['actions' if mode == 'joint' else 'targets']
        rows, frames = [], []
        directory = self.frame_workspace/f'runtime/frames/sequence-{self.serial}'
        directory.mkdir(parents=True)
        for target in targets:
            row = native_row(mode, self.step)
            if mode == 'joint':
                row['executed_action'] = target
            self.step += 1
            row['terminated'] = self.step == self.terminal_at
            rows.append(row)
            response = reply(self.step, row)
            value = unpack(response)
            files = []
            for attachment in value['attachments']:
                path = directory/f'frame_{self.step:06d}_{attachment["camera"]}_rgb.png'
                path.write_bytes(base64.b64decode(response['content'][attachment['content_index']]['data']))
                files.append({'kind': 'rgb', 'camera': attachment['camera'],
                              'path': str(path.relative_to(self.frame_workspace))})
            frames.append({'step_id': self.step, 'states': value['states'], 'files': files})
            if row['terminated']:
                break
        manifest = directory/'manifest.json'
        manifest.write_text(json.dumps({'frequency_hz': 25, 'frames': frames}))
        value.update(transition={'steps': rows}, episode_ended=rows[-1]['terminated'],
                     frame_sequence={'manifest_path': str(manifest.relative_to(self.frame_workspace))})
        return _agent_safe(value), response['content'][1:]


@contextmanager
def client(path):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(15)
        connection.connect(str(path))
        with connection.makefile('r') as reader, connection.makefile('w') as writer:
            yield Context(reader, writer)


def data(result):
    return json.loads(result['content'][0]['text'])


def server(listener, config, workspace, ready):
    def stop(*_):
        raise SystemExit()
    signal.signal(signal.SIGTERM, stop)
    class Backend(NativeFrames):
        def evaluate(self):
            return {'task_complete': self.step >= 4}  # Fake task for lifecycle/path checks only.

        def call(self, name, arguments):
            if name == STEP:
                time.sleep(.02)  # Give concurrent callers an opportunity to overlap.
            return super().call(name, arguments)
    supervisor = Supervisor(config, lambda: Backend(config.root/'native'/uuid.uuid4().hex, terminal_at=100))
    frontend = ResearchFrontend(supervisor, workspace)
    ready.send(True)
    ready.close()
    try:
        frontend.serve_socket(listener)
    finally:
        frontend.close()
        listener.close()


@pytest.fixture
def live_endpoint(docker_runtime):
    image, root = docker_runtime
    development_image = subprocess.run(['docker', 'image', 'inspect', 'rax-auto-research:dev'],
                                  capture_output=True, text=True, timeout=15)
    if development_image.returncode == 0:
        image = json.loads(development_image.stdout)[0]['Id']
    config = replace(configuration(root), image=image)
    limits = replace(config.development, memory_mb=2048, pids=128)
    config = replace(config, development=limits, formal=limits)
    workspace = root/'workspace'
    workspace.mkdir()
    published = config.root/'published'
    published.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='robot-mcp-') as relay:
        path = Path(relay)/'mcp.sock'
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen(16)
            context = multiprocessing.get_context('fork')
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=server, args=(listener, config, workspace, child))
            process.start()
            child.close()
            try:
                assert parent.poll(15) and parent.recv(), 'MCP server did not start'
                yield image, root, workspace, published, path
            finally:
                process.terminate()
                process.join(15)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                parent.close()


def test_socket_clients_are_serialized_and_bad_json_does_not_stop_server(live_endpoint):
    _, _, _, _, path = live_endpoint
    with client(path) as agent:
        assert data(agent.call('start_episode'))['step_id'] == 0
        def move(_):
            with client(path) as ctx:
                return data(ctx.call(STEP, actions=[[0]*14]))['step_id']
        with ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(move, range(2))) == [1, 2]
        assert data(agent.call(OBSERVE))['step_id'] == 2
        with socket.socket(socket.AF_UNIX) as invalid:
            invalid.connect(str(path))
            invalid.sendall(b'not-json\n')
            assert json.loads(invalid.recv(4096))['error']['code'] == -32700
        assert data(agent.call('status'))['exploration_remaining'] == 2


def test_ordinary_python_full_script_recording_and_agent_handoff(live_endpoint):
    _, root, workspace, published, path = live_endpoint
    image = subprocess.run(['docker', 'image', 'inspect', 'rax-auto-research:dev'],
                           capture_output=True, text=True, timeout=15)
    if image.returncode:
        pytest.skip('Locally provisioned development image required')
    image = json.loads(image.stdout)[0]['Id']
    repo = Path(__file__).resolve().parents[2]
    name = 'rax-test-development-'+uuid.uuid4().hex
    command = ['docker', 'run', '--detach', '--pull=never', '--name', name,
        '--network=none', '--read-only', '--user', f'{os.getuid()}:{os.getgid()}',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit', '128',
        '--memory', '2g', '--tmpfs', '/tmp:size=64m', '--workdir', '/workspace',
        '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONPATH=/workspace',
        '--mount', f'type=bind,src={workspace},dst=/workspace',
        '--mount', f'type=bind,src={repo}/auto_research_agent/api,dst=/workspace/api,readonly',
        '--mount', f'type=bind,src={published},dst=/workspace/runtime/autonomous_controller,readonly',
        '--mount', f'type=bind,src={path.parent},dst=/run/agent-relay,readonly',
        '--entrypoint', '/bin/sleep', image, '300']
    # Same function is usable as controller.py:main(ctx), with no special operate RPC.
    (workspace/'controller.py').write_text(NAMES + '''
import json
def main(ctx):
    for _ in range(2):
        observation = json.loads(ctx.call(OBSERVE)['content'][0]['text'])
        ctx.call(STEP, actions=[observation['states']]*2)
    print('full-script-finished', flush=True)
''')
    def python(code, *, success=True):
        result = subprocess.run(['docker', 'exec', name, 'python', '-c', NAMES + code],
                                capture_output=True, text=True, timeout=60)
        if success:
            assert result.returncode == 0, result.stderr
        return result
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=30)
        with client(path) as agent:  # Remains open/idle while scripts connect.
            agent.call('start_episode')
            result = python('''
from api.runtime import Context
from controller import main
with Context() as ctx:
    main(ctx)
''')
            assert 'full-script-finished' in result.stdout
            assert data(agent.call(OBSERVE))['step_id'] == 4
            python('''
import base64, json
from pathlib import Path
from api.runtime import Context
with Context() as ctx:
    content = ctx.call(STEP, actions=[[0]*14]*2)['content']
assert json.loads(content[0]['text'])['step_id'] == 6
image = next(block for block in content if block['type'] == 'image')
Path('preview.png').write_bytes(base64.b64decode(image['data']))
''')
            assert (workspace/'preview.png').read_bytes().startswith(b'\x89PNG')
            assert data(agent.call(STEP, actions=[[0]*14]))['step_id'] == 7
            failed = python('''
from api.runtime import Context
with Context() as ctx:
    ctx.call(STEP, actions=[[0]*14])
    raise RuntimeError('intentional-development-error')
''', success=False)
            assert failed.returncode != 0 and 'intentional-development-error' in failed.stderr
            assert data(agent.call(OBSERVE))['step_id'] == 8
            status = data(agent.call('status'))
            assert status['exploration_remaining'] == 2
            assert not status['formal_reserved']
            # Full development scripts never produce a qualifying rehearsal.
            state = json.loads((root/'private/state.json').read_text())
            assert not any(r['mode'] == 'rehearsal' for r in state['results'])
            trace = published/Path(status['artifacts']['mcp_trace']).relative_to('runtime/autonomous_controller')
            assert STEP in trace.read_text()

            # Run identical nested-project code in development, rehearsal and formal.
            # Reply frame paths must resolve to the current run's frames in each setting.
            project = workspace/'code/nested/reach'
            project.mkdir(parents=True)
            (workspace/'output').mkdir()
            (workspace/'private-development-marker').write_text('must not enter isolated runs')
            (project/'asset.txt').write_text('frozen-input')
            controller = NAMES + '''
import base64
import json
from pathlib import Path

def main(ctx):
    assert Path.cwd() == Path('/workspace')
    assert Path(__file__).parent == Path('/workspace/code/nested/reach')
    assert Path(__file__).with_name('asset.txt').read_text() == 'frozen-input'
    assert Path('/workspace/api/runtime.py').is_file()
    steps = []
    for _ in range(2):
        observation = json.loads(ctx.call(OBSERVE)['content'][0]['text'])
        content = ctx.call(STEP, actions=[observation['states']]*2)['content']
        feedback = json.loads(content[0]['text'])
        frames = Path('/workspace') / feedback['frame_sequence']['manifest_path']
        assert frames.is_file()
        steps += [row['step_id'] for row in feedback['transition']['steps']]
    image = next(block for block in content if block['type'] == 'image')
    Path('/workspace/output/preview.png').write_bytes(base64.b64decode(image['data']))
    Path('/workspace/output/trajectory.json').write_text(json.dumps(steps))
    Path('/workspace/output/paths.json').write_text(json.dumps({
        'project': str(Path(__file__).parent), 'frames': str(frames),
        'development_visible': Path('/workspace/private-development-marker').exists(),
        'socket_visible': Path('/run/agent-relay/mcp.sock').exists(),
        'visible_runs': sorted(p.name for p in Path('/workspace/runtime/autonomous_controller/runs').iterdir()),
    }))
'''
            (project/'controller.py').write_text(controller)
            python("import sys; sys.path.insert(0, '/workspace/code/nested/reach'); "
                   "from api.runtime import Context; from controller import main; "
                   "ctx = Context(); main(ctx); ctx.close()")
            development = json.loads((workspace/'output/paths.json').read_text())
            assert development['development_visible'] and development['socket_visible']
            bundle = data(agent.call('register', source='code/nested/reach'))
            assert bundle['project_directory'] == '/workspace/code/nested/reach'
            for tool in ('rehearse',):
                result = data(agent.call(tool, bundle=bundle['bundle']))
                assert result['returncode'] == 0 and result['task_complete'], result
                run = published/Path(result['artifacts']['directory']).relative_to('runtime/autonomous_controller')
                exported = json.loads((run/'exports/paths.json').read_text())
                assert exported['project'] == development['project']
                assert not exported['development_visible'] and not exported['socket_visible']
                assert exported['visible_runs'] == [run.name]
                assert exported['frames'].startswith('/workspace/runtime/autonomous_controller/runs/'+run.name+'/frames/')
                assert (run/'exports/preview.png').read_bytes().startswith(b'\x89PNG')
                assert json.loads((run/'exports/trajectory.json').read_text()) == [1, 2, 3, 4]
                assert (run/'code/controller.py').read_text() == controller
            # Submit only closes exploration: formal runs later, after the agent is frozen.
            submitted = data(agent.call('submit', bundle=bundle['bundle']))
            assert submitted['status'] == 'submitted' and 'returncode' not in submitted, submitted
            assert 'you do not see' in submitted['next_step']
    finally:
        subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=30)
