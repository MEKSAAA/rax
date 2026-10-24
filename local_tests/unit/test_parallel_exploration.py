"""Budget races, slot isolation and native-worker contracts without GPU/API calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from services.exploration.pool import ExplorationPool, LifecycleGate
from services.exploration.worker import EpisodeWorker
from test_controller_backend import Backend, configuration, supervisor
from bench import STEP, TASK


class FakeWorker:
    barrier = None

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.step = 0
        self.inside = False

    def request(self, name, arguments):
        assert not self.inside, 'same slot raced'
        self.inside = True
        try:
            if name == 'fail':
                raise RuntimeError('native crash')
            if name == 'step':
                if self.barrier is not None:
                    self.barrier.wait(timeout=2)
                time.sleep(.01)
                self.step += 1
            state = {'episode': self.config['episode'], 'episode_id': f"episode-{self.config['episode']}",
                     'status': 'closed' if name == 'finish' else 'active'}
            return {'state': state, 'reply': {'content': [
                {'type': 'text', 'text': json.dumps({'step_id': self.step})},
                {'type': 'image', 'mimeType': 'image/png', 'data': 'image-bytes'}]}}
        finally:
            self.inside = False

    def close(self):
        self.closed = True


@pytest.fixture
def pool(tmp_path):
    p = ExplorationPool(tmp_path/'pool', count=2, seeds=[10, 11, 12], worker_config={}, worker_factory=FakeWorker)
    yield p
    p.close()


def test_global_budget_reset_and_images(pool):
    first = pool.start(0)
    assert first['content'][1]['type'] == 'image'
    assert 'structuredContent' not in first
    assert 'seed' not in json.dumps(first)
    pool.start(1)
    other = pool.workers[1][0]
    pool.start(0)
    assert not other.closed
    assert [r['seed'] for r in pool.state['episodes']] == [10, 11, 12]
    with pytest.raises(RuntimeError, match='budget exhausted'):
        pool.start(1)
    assert not other.closed
    pool.call(1, 'step', {})
    assert not pool.status()['exploration_complete']
    pool.finish(0)
    assert not pool.status()['exploration_complete']
    pool.finish(1)
    assert pool.status()['exploration_complete']


def test_parallel_slots_really_overlap_and_same_slot_serializes(pool, monkeypatch):
    pool.start(0)
    pool.start(1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        monkeypatch.setattr(FakeWorker, 'barrier', threading.Barrier(2))
        list(executor.map(lambda i: pool.call(i, 'step', {}), [0, 1]))
        monkeypatch.setattr(FakeWorker, 'barrier', None)
        list(executor.map(lambda _: pool.call(0, 'step', {}), range(4)))
    assert pool.workers[0][0].step == 5
    assert pool.workers[1][0].step == 1


def test_budget_race_never_oversubscribes(pool):
    def start(i):
        try:
            pool.start(i % 2)
            return True
        except RuntimeError as exc:
            assert 'budget exhausted' in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(start, range(12))) == 3
    assert pool.status()['exploration_episodes_remaining'] == 0


def test_rehearsal_consumes_the_same_budget(pool):
    pool.start(1)
    row = pool.reserve_rehearsal()
    assert row['seed'] == 11
    assert row['kind'] == 'rehearsal'
    pool.finish_reservation(row)
    pool.start(0)
    assert pool.status()['exploration_episodes_started'] == 3


def test_a_broken_episode_returns_its_budget_and_its_seed_is_played_next(pool):
    pool.start(0)
    pool.start(1)
    with pytest.raises(RuntimeError, match='native crash'):
        pool.call(0, 'fail', {})
    assert pool.state['episodes'][0]['status'] == 'error'
    status = pool.status()
    assert (status['exploration_episodes_charged'], status['exploration_episodes_refunded'],
            status['exploration_episodes_remaining']) == (1, 1, 2)
    pool.call(1, 'step', {})
    pool.start(0)  # The broken episode's layout again.
    pool.start(0)  # Closes it and takes the last slot.
    assert [(r['episode'], r['seed'], r['status']) for r in pool.state['episodes']] == [
        (1, 10, 'error'), (2, 11, 'active'), (3, 10, 'closed'), (4, 12, 'active')]
    with pytest.raises(RuntimeError, match='budget exhausted'):
        pool.start(1)
    assert pool.status()['exploration_episodes_remaining'] == 0


@pytest.mark.parametrize('env_id', [None, True, -1, 2, '0', 0.0])
def test_invalid_slot_has_no_effect(pool, env_id):
    with pytest.raises(ValueError):
        pool.start(env_id)
    assert not pool.state['episodes']


def test_crash_recovery_fails_closed_and_configuration_is_bound(tmp_path):
    root = tmp_path/'pool'
    p = ExplorationPool(root, count=2, seeds=[10, 11], worker_config={}, worker_factory=FakeWorker)
    p._reserve(0)
    p._owner.close()  # Simulate a lost supervisor after a durable reservation.
    with pytest.raises(RuntimeError, match='recovery'):
        ExplorationPool(root, count=2, seeds=[10, 11], worker_config={})
    with pytest.raises(ValueError, match='Cannot change'):
        ExplorationPool(root, count=2, seeds=[10, 12], worker_config={})


def test_duplicate_owner_and_sealed_pool(pool):
    with pytest.raises(RuntimeError, match='owner'):
        ExplorationPool(pool.root, count=2, seeds=pool.seeds, worker_config={})
    pool.seal()
    with pytest.raises(RuntimeError, match='closed'):
        pool.start(0)


def test_global_lifecycle_excludes_inflight_operations():
    gate = LifecycleGate()
    with gate.enter():
        with gate.enter():
            with pytest.raises(RuntimeError, match='in progress'):
                with gate.enter(exclusive=True):
                    pytest.fail('entered lifecycle during a motion')
    with gate.enter(exclusive=True):
        with pytest.raises(RuntimeError):
            with gate.enter():
                pytest.fail('entered motion during lifecycle')


def test_schemas_require_env_id_without_mutating_single_env_contract():
    from services.benchmarks import benchmark
    original = benchmark().definitions()
    snapshot = deepcopy(original)
    from services.mcp_contract import Contract
    tools = Contract(environments=2).select(original, scoped={STEP})
    step = next(t for t in tools if t['name'] == STEP)
    assert 'env_id' in step['inputSchema']['required']
    assert step['inputSchema']['properties']['env_id']['maximum'] == 1
    assert original == snapshot


def test_episode_worker_preserves_images_and_publishes_trace(tmp_path):
    backend = Backend()
    backend.frame_workspace = tmp_path
    config = {'root': str(tmp_path/'private'), 'task': TASK, 'sim_gpu': 'GPU-aaaa',
              'eval_seed': 0, 'seed': 7, 'episode': 1, 'env_id': 0, 'episode_seconds': 60,
              'published_root': str(tmp_path/'published')}
    worker = EpisodeWorker(config, backend_factory=lambda _: backend)
    try:
        reply = worker.call('start', {})['reply']
        assert reply['content'][1] == backend.image
        assert backend.starts == [(False, 7)]
        backend.success = True
        result = worker.call('evaluate', {})
        assert result['state']['task_complete']
        assert 'native_score' not in json.dumps(result)
        with pytest.raises(PermissionError):
            worker.call('start_episode', {})
        with pytest.raises(RuntimeError, match='cannot reset'):
            worker.call('start', {})
    finally:
        worker.finish()
    assert backend.closed
    assert list((tmp_path/'published').glob('runs/*/mcp.jsonl'))


def test_research_frontend_routes_slots_and_shares_supervisor_budget(supervisor, tmp_path):
    from services.exploration.research import ParallelResearchFrontend
    s, _ = supervisor
    object.__setattr__(s.config, 'exploration_envs', 2)
    frontend = ParallelResearchFrontend(s, tmp_path/'workspace',
        pool_factory=lambda *a, **kw: ExplorationPool(*a, **kw, worker_factory=FakeWorker))
    try:
        with pytest.raises(ValueError, match='explicit env_id'):
            frontend.call('start_episode', {})
        frontend.call('start_episode', {'env_id': 0})
        frontend.call('start_episode', {'env_id': 1})
        assert s.state['exploration_started'] == 2
        assert frontend.status()['exploration_remaining'] == 1
        assert frontend.status()['exploration']['active_episode_count'] == 2
        with pytest.raises(ValueError):
            frontend.call('start_episode', {'env_id': 0, 'seed': 7})
        assert s.state['exploration_started'] == 2
    finally:
        frontend.pool.close()
        frontend.api.revoke()


def test_rehearsal_uses_next_global_seed_after_parallel_exploration(supervisor, tmp_path):
    from services.exploration.research import ParallelResearchFrontend
    s, backend = supervisor
    object.__setattr__(s.config, 'exploration_envs', 2)
    frontend = ParallelResearchFrontend(s, tmp_path/'workspace',
        pool_factory=lambda *a, **kw: ExplorationPool(*a, **kw, worker_factory=FakeWorker))
    try:
        frontend.call('start_episode', {'env_id': 0})
        frontend.call('start_episode', {'env_id': 1})
        project = frontend.workspace/'code/test'
        project.mkdir(parents=True)
        (project/'controller.py').write_text('def main(ctx): pass')
        registered = json.loads(frontend.call('register', {'source': 'code/test'})['content'][0]['text'])
        with pytest.raises(RuntimeError, match='clean rehearsal'):
            frontend.call('submit', {'bundle': registered['bundle']})
        assert frontend.pool.status()['active_episode_count'] == 2
        frontend.call('rehearse', {'bundle': registered['bundle']})
        assert backend.starts[-1] == (False, 3)
        assert frontend.pool.status()['exploration_episodes_started'] == 3
        assert frontend.pool.status()['exploration_complete']
    finally:
        frontend.pool.close()
        frontend.api.revoke()


def test_a_rehearsal_after_a_broken_episode_plays_its_seed_and_the_supervisor_counts_charges(supervisor, tmp_path):
    from services.exploration.research import ParallelResearchFrontend
    s, backend = supervisor
    object.__setattr__(s.config, 'exploration_envs', 2)
    frontend = ParallelResearchFrontend(s, tmp_path/'workspace',
        pool_factory=lambda *a, **kw: ExplorationPool(*a, **kw, worker_factory=FakeWorker))
    try:
        frontend.call('start_episode', {'env_id': 0})
        frontend.call('start_episode', {'env_id': 1})
        with pytest.raises(RuntimeError, match='native crash'):
            frontend.pool.call(0, 'fail', {})
        frontend.sync()
        assert s.state['exploration_started'] == 1 and frontend.status()['exploration_remaining'] == 2
        project = frontend.workspace/'code/test'
        project.mkdir(parents=True)
        (project/'controller.py').write_text('def main(ctx): pass')
        registered = json.loads(frontend.call('register', {'source': 'code/test'})['content'][0]['text'])
        frontend.call('rehearse', {'bundle': registered['bundle']})
        rehearsal = frontend.pool.state['episodes'][-1]
        assert (rehearsal['kind'], rehearsal['seed_index']) == ('rehearsal', 0)
        assert backend.starts[-1] == (False, s.config.exploration_seeds[0])
        assert s.state['exploration_started'] == 2 and frontend.pool.status()['exploration_episodes_remaining'] == 1
    finally:
        frontend.pool.close()
        frontend.api.revoke()


def test_pipelined_socket_calls_run_in_parallel_and_ids_images_survive(tmp_path):
    import socket
    from services.exploration.transport import serve_socket
    barrier = threading.Barrier(2)

    class Service:
        def parallel_request(self, request):
            return request['method'] == 'step'

        def handle(self, request):
            if request['method'] == 'stop':
                raise SystemExit()
            barrier.wait(timeout=3)
            return {'jsonrpc': '2.0', 'id': request['id'],
                    'result': {'content': [{'type': 'image', 'data': 'bytes', 'mimeType': 'image/png'}]}}

        def interrupt(self):
            pass

    errors = []
    listener = socket.socket(socket.AF_UNIX)
    path = str(tmp_path/'mcp.sock')
    listener.bind(path)
    listener.listen()
    def run():
        try:
            serve_socket(Service(), listener, slots=2)
        except SystemExit:
            pass
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(path)
            client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"step"}\n{"jsonrpc":"2.0","id":2,"method":"step"}\n')
            with client.makefile('rb') as stream:
                replies = [json.loads(stream.readline()), json.loads(stream.readline())]
            assert {r['id'] for r in replies} == {1, 2}
            assert all(r['result']['content'][0]['type'] == 'image' for r in replies)
            client.sendall(b'{"jsonrpc":"2.0","id":3,"method":"stop"}\n')
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
    finally:
        listener.close()


def test_startup_failure_reserves_exactly_once(tmp_path):
    def fail(_):
        raise RuntimeError('startup failed')
    pool = ExplorationPool(tmp_path/'pool', count=2, seeds=[0, 1], worker_config={}, worker_factory=fail)
    try:
        with pytest.raises(RuntimeError, match='startup failed'):
            pool.start(0)
        assert pool.status()['exploration_episodes_started'] == 1
        assert pool.status()['active_episode_count'] == 0
        assert pool.state['episodes'][0]['status'] == 'error'
        assert pool.status()['exploration_episodes_remaining'] == 2  # A failed start is not charged.
    finally:
        pool.close()


def test_worker_evaluation_keeps_assessment_and_drops_private_budget(tmp_path):
    class AssessedBackend(Backend):
        def evaluate(self, **arguments):
            self.assessment = arguments
            return {'task_complete': False, 'agent_assessment': arguments.get('agent_assessment'),
                    'performance_cost': {'max_exploration_episodes': 1, 'exploration_episodes_started': 1,
                                         'current_episode_interaction_steps': 3}}
    backend = AssessedBackend()
    backend.frame_workspace = tmp_path
    config = {'root': str(tmp_path/'private'), 'task': TASK, 'sim_gpu': 'GPU-aaaa',
              'eval_seed': 0, 'seed': 7, 'episode': 1, 'env_id': 0, 'episode_seconds': 60,
              'published_root': str(tmp_path/'published')}
    worker = EpisodeWorker(config, backend_factory=lambda _: backend)
    try:
        worker.call('start', {})
        with pytest.raises(ValueError, match='only agent_assessment'):
            worker.call('evaluate', {'score': 1})
        result = worker.call('evaluate', {'agent_assessment': 'looks placed', 'confidence': .4})
        assert backend.assessment == {'agent_assessment': 'looks placed', 'confidence': .4}
        evaluation = json.loads(result['reply']['content'][0]['text'])['evaluation']
        assert evaluation['agent_assessment'] == 'looks placed'
        assert evaluation['performance_cost'] == {'current_episode_interaction_steps': 3, 'scope': 'current_episode'}
        closed = worker.call('finish', {'reason': 'abandon layout'})
        assert closed['state']['finish_reason'] == 'abandon layout'
    finally:
        worker.finish()
    trace = next((tmp_path/'published').glob('runs/*/mcp.jsonl')).read_text()
    assert 'looks placed' in trace


def test_failed_preflight_leaves_exploration_open(supervisor, tmp_path, monkeypatch):
    # Docker/image trouble at rehearse or submit must not discard or seal exploration.
    from services.exploration.research import ParallelResearchFrontend
    from test_controller_backend import Runner
    s, _ = supervisor
    object.__setattr__(s.config, 'exploration_envs', 2)
    frontend = ParallelResearchFrontend(s, tmp_path/'workspace',
        pool_factory=lambda *a, **kw: ExplorationPool(*a, **kw, worker_factory=FakeWorker))
    try:
        frontend.call('start_episode', {'env_id': 0})
        project = frontend.workspace/'code/test'
        project.mkdir(parents=True)
        (project/'controller.py').write_text('def main(ctx): pass')
        registered = json.loads(frontend.call('register', {'source': 'code/test'})['content'][0]['text'])
        def unavailable(self):
            raise RuntimeError('Docker unavailable')
        monkeypatch.setattr(Runner, 'preflight', unavailable)
        with pytest.raises(RuntimeError, match='Docker unavailable'):
            frontend.call('rehearse', {'bundle': registered['bundle']})
        assert frontend.pool.status()['active_episode_count'] == 1
        assert not frontend.pool.state['sealed'] and not s.state['formal_reserved']
        frontend.call('start_episode', {'env_id': 1})
        assert frontend.pool.status()['active_episode_count'] == 2
    finally:
        frontend.pool.close()
        frontend.api.revoke()


def test_live_copy_parallel_exploration_has_no_bundle_tools(supervisor, tmp_path):
    from services.exploration.research import ParallelResearchFrontend
    s, _ = supervisor
    object.__setattr__(s.config, 'exploration_envs', 2)
    object.__setattr__(s.config, 'agent_formal', True)
    frontend = ParallelResearchFrontend(s, tmp_path/'workspace',
        pool_factory=lambda *a, **kw: ExplorationPool(*a, **kw, worker_factory=FakeWorker))
    try:
        assert not {t['name'] for t in frontend.tools()} & {'register', 'rehearse', 'submit'}
        frontend.call('start_episode', {'env_id': 0})
        for name, arguments in (('register', {'source': 'code/test'}), ('rehearse', {'bundle': 'x'}),
                                ('submit', {'bundle': 'x'})):
            with pytest.raises(PermissionError):
                frontend.call(name, arguments)
        assert frontend.pool.status()['active_episode_count'] == 1  # Nothing was closed.
    finally:
        frontend.pool.close()
        frontend.api.revoke()


def test_budget_beyond_the_collection_repeats_layouts(tmp_path):
    p = ExplorationPool(tmp_path/'pool', count=2, seeds=[1, 0, 1], worker_config={}, worker_factory=FakeWorker)
    try:
        p.start(0)
        p.start(1)
        assert [row['seed'] for row in p.state['episodes']] == [1, 0]
    finally:
        p.close()
    with pytest.raises(ValueError, match='nonnegative'):
        ExplorationPool(tmp_path/'other', count=1, seeds=[0, -1], worker_config={}, worker_factory=FakeWorker)


@pytest.mark.parametrize('episode_seconds, timeout', [(None, None), (60, 360)])
def test_worker_transport_waits_out_an_episode_without_a_wall_limit(tmp_path, monkeypatch, episode_seconds, timeout):
    import subprocess
    from services.exploration import pool
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: SimpleNamespace(poll=lambda: 0))
    worker = pool.Worker({'root': str(tmp_path/'worker'), 'episode_seconds': episode_seconds})
    try:
        # None: a reply may take as long as the episode runs to its step limit.
        assert worker.socket.gettimeout() == timeout
    finally:
        worker.stream.close()
        worker.socket.close()
        worker.log.close()


@pytest.mark.parametrize('from_record', [True, False])
def test_worker_evaluates_a_record_backend_after_its_simulator_exits(tmp_path, from_record):
    from services.simulator.io import SimulatorExited
    exit_report = ('chunk_step failed with EOFError: RPC connection closed; simulator process exited with code 0; '
                   'its log: {"event": "episode_wall_timeout"}')

    class ExitingBackend(Backend):
        evaluates_from_record = from_record  # The verdict is a file the simulator wrote.

        def call(self, name, arguments):
            if name == STEP:
                raise SimulatorExited(exit_report)
            return super().call(name, arguments)
    backend = ExitingBackend()
    backend.frame_workspace, backend.success = tmp_path, True
    config = {'root': str(tmp_path/'private'), 'task': TASK, 'sim_gpu': 'GPU-aaaa',
              'eval_seed': 0, 'seed': 7, 'episode': 1, 'env_id': 0, 'episode_seconds': 60,
              'published_root': str(tmp_path/'published')}
    worker = EpisodeWorker(config, backend_factory=lambda _: backend)
    worker.call('start', {})
    state = worker.call(STEP, {})['state']
    assert state['status'] == 'closed' and state['simulator_exited'] == exit_report and backend.closed
    if from_record:
        assert backend.evaluations == 1 and state['task_complete'] is True
        assert state['evaluation'] == {'task_complete': True}
    else:
        assert backend.evaluations == 0 and state['task_complete'] is False and 'evaluation' not in state


def test_worker_records_why_a_record_backend_could_not_be_evaluated(tmp_path):
    from services.simulator.io import SimulatorExited

    class UnreadableBackend(Backend):
        evaluates_from_record = True

        def call(self, name, arguments):
            if name == STEP:
                raise SimulatorExited('chunk_step failed with EOFError; simulator process exited with code -11')
            return super().call(name, arguments)

        def evaluate(self):
            raise RuntimeError('Native evaluation identity mismatch: recorded step 4, host step 6')
    backend = UnreadableBackend()
    backend.frame_workspace = tmp_path
    config = {'root': str(tmp_path/'private'), 'task': TASK, 'sim_gpu': 'GPU-aaaa',
              'eval_seed': 0, 'seed': 7, 'episode': 1, 'env_id': 0, 'episode_seconds': 60,
              'published_root': str(tmp_path/'published')}
    worker = EpisodeWorker(config, backend_factory=lambda _: backend)
    worker.call('start', {})
    state = worker.call(STEP, {})['state']
    assert state['evaluation_error'] == 'RuntimeError: Native evaluation identity mismatch: recorded step 4, host step 6'
    assert state['status'] == 'closed' and state['task_complete'] is False and backend.closed
