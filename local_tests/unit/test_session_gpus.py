"""Moving a stopped session to another simulator GPU keeps its three saved copies in step, so it resumes."""
from dataclasses import asdict, replace
import json

import pytest

from harness import session_gpus
from local_tests.unit.test_controller_backend import Backend, Runner, configuration
from services.controller.config import Configuration
from services.controller.supervisor import Supervisor


@pytest.fixture
def stopped_session(tmp_path):
    def make(legacy=None, **changes):
        return save(replace(configuration(tmp_path), **changes), legacy or {})
    return make


def save(config, legacy):
    """legacy: fields an older launch also saved in all three copies (agent GPUs)."""
    Supervisor(config, lambda: Backend(), runner_factory=Runner).close()  # Saves state.json as a launch does.
    saved = {**json.loads(json.dumps(asdict(config), default=str)), **legacy}
    for name in ('configuration.json', 'operator-input.json'):
        (config.root / name).write_text(json.dumps(saved))
        (config.root / name).chmod(0o600)
    state = json.loads((config.root / 'state.json').read_text())
    state['configuration'].update(legacy)
    (config.root / 'state.json').write_text(json.dumps(state))
    return config.root


def move(root, **gpus):
    return session_gpus.move(root, process_users=lambda _: [], is_alive=lambda *_: False, **gpus)


def resume(root):
    """The frontend's resume: its configuration.json, checked by the supervisor against state.json."""
    resumed = Configuration.from_dict(json.loads((root / 'configuration.json').read_text()))
    object.__setattr__(resumed, 'root', root)
    return Supervisor(resumed, lambda: Backend(), runner_factory=Runner)


def test_moved_session_resumes_on_the_new_gpu(stopped_session):
    root = stopped_session()
    result = move(root, sim_gpu='GPU-bbbb')
    assert (result['from'], result['to']) == ({'sim_gpu': 'GPU-aaaa'}, {'sim_gpu': 'GPU-bbbb'})
    for name in ('configuration.json', 'operator-input.json'):
        assert json.loads((root / name).read_text())['sim_gpu'] == 'GPU-bbbb'
        assert (root / name).stat().st_mode & 0o777 == 0o600
    # The supervisor refuses any changed configuration; resuming with the moved one must pass.
    supervisor = resume(root)
    assert supervisor.state['configuration']['sim_gpu'] == 'GPU-bbbb'
    supervisor.close()
    assert json.loads((root / 'gpu-moves.jsonl').read_text())['from']['sim_gpu'] == 'GPU-aaaa'
    assert move(root, sim_gpu='GPU-bbbb')['status'] == 'unchanged'


def test_a_session_with_agent_gpus_moves_its_simulator_onto_one(stopped_session):
    """Sessions created while agents had a GPU: their simulator may now take that GPU, the agent
    GPU fields stay as saved, and the session resumes."""
    legacy = {'controller_gpu': 'GPU-bbbb', 'training_gpu': 'GPU-bbbb'}
    root = stopped_session(legacy)
    assert move(root, sim_gpu='GPU-bbbb')['to'] == {'sim_gpu': 'GPU-bbbb'}
    copies = [json.loads((root / name).read_text()) for name in ('configuration.json', 'operator-input.json')]
    copies.append(json.loads((root / 'state.json').read_text())['configuration'])
    for copy in copies:
        assert copy['sim_gpu'] == 'GPU-bbbb' and {k: copy[k] for k in legacy} == legacy
    supervisor = resume(root)
    try:
        assert supervisor.config.sim_gpu == 'GPU-bbbb'
        assert 'controller_gpu' not in supervisor.state['configuration']
    finally:
        supervisor.close()


def test_unmoved_session_still_cannot_resume_with_a_changed_configuration(stopped_session):
    root = stopped_session()
    changed = Configuration.from_dict({**json.loads((root / 'configuration.json').read_text()), 'sim_gpu': 'GPU-cccc'})
    object.__setattr__(changed, 'root', root)
    with pytest.raises(ValueError, match='cannot change'):
        Supervisor(changed, lambda: Backend(), runner_factory=Runner)


def test_refuses_a_running_session_and_an_invalid_gpu(stopped_session):
    root = stopped_session()
    before = (root / 'state.json').read_text()
    with pytest.raises(ValueError, match='still running'):
        session_gpus.move(root, sim_gpu='GPU-bbbb', process_users=lambda _: [123], is_alive=lambda *_: False)
    with pytest.raises(ValueError, match='explicit GPU UUID'):
        move(root, sim_gpu='1')
    with pytest.raises(ValueError, match='simulator GPU'):
        move(root, sim_gpu=None)
    assert (root / 'state.json').read_text() == before and not (root / 'gpu-moves.jsonl').exists()


def test_refuses_disagreeing_copies(stopped_session):
    root = stopped_session()
    path = root / 'operator-input.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), 'sim_gpu': 'GPU-eeee'}))
    with pytest.raises(ValueError, match='disagree'):
        move(root, sim_gpu='GPU-bbbb')


def test_process_users_finds_the_session_by_path(stopped_session):
    import subprocess, sys
    root = stopped_session()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', f'--output={root}/native/x'])
    try:
        assert child.pid in session_gpus.users(root)
    finally:
        child.kill()


def test_formal_copies_on_the_old_simulator_gpu_move_with_it(stopped_session):
    """Agent-formal copies listed on the session's simulator GPU follow it; other GPUs stay."""
    root = stopped_session(agent_formal=True, formal_sim_gpus=('GPU-aaaa', 'GPU-cccc'))
    result = move(root, sim_gpu='GPU-dddd')
    assert result['from']['formal_sim_gpus'] == ['GPU-aaaa', 'GPU-cccc']
    assert result['to'] == {'sim_gpu': 'GPU-dddd', 'formal_sim_gpus': ['GPU-dddd', 'GPU-cccc']}
    for name in ('configuration.json', 'operator-input.json'):
        assert json.loads((root / name).read_text())['formal_sim_gpus'] == ['GPU-dddd', 'GPU-cccc']
    assert json.loads((root / 'state.json').read_text())['configuration']['formal_sim_gpus'] == ['GPU-dddd', 'GPU-cccc']
    resumed = Configuration.from_dict(json.loads((root / 'configuration.json').read_text()))
    object.__setattr__(resumed, 'root', root)
    assert resumed.formal_sim_gpus == ('GPU-dddd', 'GPU-cccc')
    Supervisor(resumed, lambda: Backend(), runner_factory=Runner).close()  # Resumes with the moved copies.
    # Onto a GPU the copies already use: listed once.
    assert move(root, sim_gpu='GPU-cccc')['to']['formal_sim_gpus'] == ['GPU-cccc']
