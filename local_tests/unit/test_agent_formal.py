"""Agent formal: a frozen transfer package, one live agent copy per held-out layout."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re

import pytest

from services import transfer
from services.agent_formal import checkpoint, runner as agent_runner, worker as agent_worker
from services.controller import formal_worker
from services.controller.supervisor import Supervisor
from test_controller_backend import Backend, Runner, source
from test_formal_batch import config
from test_parallel_submit import Finished, finalized, orphan
from bench import BENCH, STEP, TASK


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# -- transfer package ---------------------------------------------------------

def workspace(tmp_path):
    ws = tmp_path/'ws'
    write(ws/'memory/PROGRESS.md', 'progress')
    write(ws/'code/ctl/controller.py', 'def main(ctx): pass')
    write(ws/'handoff/README.md', 'read me')
    write(ws/'output/big.bin', 'not transferred')
    (ws/'memory/link').symlink_to('/etc/passwd')
    return ws


def home(tmp_path):
    h = tmp_path/'home'
    write(h/'config.toml', 'model = "m"')
    write(h/'sessions/2026/09/30/rollout-a.jsonl', '{"payload": {"id": "thread"}}\n')
    return h


def test_package_carries_only_selected_folders_and_never_links(tmp_path):
    manifest, record = transfer.pack(tmp_path/'pkg', workspace=workspace(tmp_path), parts=('memory', 'code', 'handoff'),
                                     home=home(tmp_path), session_files=['sessions/2026/09/30/rollout-a.jsonl'],
                                     home_files=('config.toml', 'missing.json'), metadata={'session_id': 'thread'})
    files = set(manifest['files'])
    assert {'transfer/memory/PROGRESS.md', 'transfer/code/ctl/controller.py', 'transfer/handoff/README.md',
            'session/sessions/2026/09/30/rollout-a.jsonl', 'session/config.toml', 'package.json'} == files
    assert record['skipped'] == ['memory/link: link or special file']
    assert record['home_files'] == ['config.toml'] and manifest['kind'] == 'agent_checkpoint'
    from services.controller.storage import verify
    verify(tmp_path/'pkg', manifest)


def test_transfer_cap_leaves_whole_folders_out_in_priority_order(tmp_path):
    ws = workspace(tmp_path)
    write(ws/'code/weights.bin', 'w' * (2 * 1024**2))
    _, record = transfer.pack(tmp_path/'pkg', workspace=ws, parts=('code', 'memory', 'handoff'), max_mb=1)
    assert record['parts']['code'] is None and 'exceeds' in record['excluded']['code']
    assert record['parts']['handoff']['files'] == 1 and record['parts']['memory']['files'] == 1
    assert not (tmp_path/'pkg/transfer/code').exists()
    sizes = transfer.transfer_size(ws, ('code', 'handoff'))
    assert sizes['code'] > 2 * 1024**2 and sizes['handoff'] == len('read me')


@pytest.mark.parametrize('context, has_session', [('resume', True), ('compact', True), ('clear', False)])
def test_unpack_gives_a_private_copy_and_the_session_unless_clearing(tmp_path, context, has_session):
    transfer.pack(tmp_path/'pkg', workspace=workspace(tmp_path), parts=('memory', 'handoff'), home=home(tmp_path),
                  session_files=['sessions/2026/09/30/rollout-a.jsonl'], home_files=('config.toml',))
    new_ws, new_home = tmp_path/'new-ws', tmp_path/'new-home'
    present = transfer.unpack(tmp_path/'pkg', workspace=new_ws, home=new_home, context=context)
    assert present == ['handoff', 'memory'] and not (new_ws/'code').exists()
    (new_ws/'memory/PROGRESS.md').write_text('edited')  # Writable.
    assert (new_home/'config.toml').is_file()
    assert (new_home/'sessions/2026/09/30/rollout-a.jsonl').is_file() == has_session


def test_invalid_transfer_settings():
    with pytest.raises(ValueError):
        transfer.validate_parts(('memory', 'frames'))
    with pytest.raises(ValueError):
        transfer.validate_parts(['memory'])


# -- configuration --------------------------------------------------------------

def test_agent_formal_configuration_is_validated(tmp_path):
    base = config(tmp_path, formal_episodes=2)
    assert base.agent_formal is False and base.formal_context == 'compact' and base.transfer_mb == 100
    for bad in (dict(formal_context='new'), dict(transfer_parts=('frames',)), dict(transfer_mb=0),
                dict(agent_formal='yes')):
        with pytest.raises(ValueError):
            replace(base, **bad)


# -- frontend: end of exploration --------------------------------------------------

def test_status_tells_the_agent_to_summarize_once_exploration_closes(tmp_path):
    from services.controller.frontend import ResearchFrontend
    backend = Backend()
    c = config(tmp_path, formal_episodes=2, agent_formal=True, formal_context='clear', exploration_seeds=(3,))
    s = Supervisor(c, lambda: backend, runner_factory=Runner)
    frontend = ResearchFrontend(s, workspace(tmp_path))
    try:
        status = frontend.status()
        assert 'next_step' not in status and status['carry']['conversation'] == 'none'
        assert status['carry']['carried_folders'] == ['memory', 'code', 'handoff']
        assert not (c.root/'exploration-complete.json').exists()
        frontend.call('start_episode', {})
        frontend.call('finish', {})
        status = frontend.status()
        assert 'end your turn' in status['next_step'] and 'conversation' not in status['next_step']
        assert (c.root/'exploration-complete.json').is_file()
    finally:
        frontend.close()


def test_bundle_sessions_report_nothing_new(tmp_path):
    from services.controller.frontend import ResearchFrontend
    s = Supervisor(config(tmp_path, formal_episodes=2), Backend, runner_factory=Runner)
    frontend = ResearchFrontend(s, tmp_path/'ws')
    try:
        assert 'carry' not in frontend.status() and 'next_step' not in frontend.status()
    finally:
        frontend.close()


def test_a_code_session_out_of_episodes_is_told_whether_to_submit(tmp_path):
    """With no exploration left, next_step names what can still happen: submit a qualifying
    bundle, nothing (none qualifies), or the frozen formal batch after a submission."""
    from services.controller.frontend import ResearchFrontend
    backend = Backend()
    c = config(tmp_path, formal_episodes=2, exploration_seeds=(3, 4))
    s = Supervisor(c, lambda: backend, runner_factory=Runner)
    frontend = ResearchFrontend(s, workspace(tmp_path))
    try:
        bundle, _ = s.register(source(tmp_path, 'controller.py'))
        frontend.call('start_episode', {})
        frontend.call('finish', {})
        assert 'next_step' not in frontend.status()
        s.run(bundle, formal=False)  # The rehearsal spends the last exploration episode.
        step = frontend.status()['next_step']
        assert step.startswith('No exploration episode remains and you have not submitted a bundle. Call submit')
        s.state['results'][-1]['reason'] = 'wall_timeout'  # Without a qualifying rehearsal:
        assert frontend.status()['next_step'].startswith('No exploration episode remains and no bundle has a '
                                                         'qualifying rehearsal, so no formal batch can run.')
        s.state['results'][-1]['reason'] = 'exit'
        submitted = frontend.call('submit', {'bundle': bundle})
        assert 'formal batch runs your submitted bundle' in json.loads(submitted['content'][0]['text'])['next_step']
    finally:
        frontend.close()


# -- formal endpoint ----------------------------------------------------------------

class Gateway:
    terminal = False
    simulator_exited = None

    def __init__(self):
        self.requests = []

    def handle(self, token, request):
        self.requests.append((token, request))
        if request.get('method') == 'tools/list':
            return {'jsonrpc': '2.0', 'id': request['id'], 'result': {'tools': [{'name': STEP}]}}
        return {'jsonrpc': '2.0', 'id': request.get('id'), 'result': {'content': []}}


def test_formal_endpoint_adds_a_local_status_and_forwards_robot_calls():
    gateway = Gateway()
    endpoint = agent_runner.FormalMCP(gateway, 'token', info={'carried': ['memory'], 'conversation': 'summary'})
    tools = endpoint.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    assert [t['name'] for t in tools] == ['status', STEP]
    status = endpoint.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'status'}})
    value = json.loads(status['result']['content'][0]['text'])
    assert value['carried'] == ['memory'] and value['episode_ended'] is False
    endpoint.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'submit'}})
    assert [r['id'] for _, r in gateway.requests] == [1, 3]  # Lifecycle names reach the gateway, which rejects them.


@pytest.mark.parametrize('live_copy_session', [True, False])
def test_formal_prompt_lists_what_was_transferred(live_copy_session):
    from types import SimpleNamespace
    text = agent_runner.episode_prompt(SimpleNamespace(agent_formal=live_copy_session), ['memory', 'handoff'])
    assert 'memory, handoff' in text and 'carried code as the controller' in text
    assert not any(word in text for word in ('host', 'score', 'held-out', 'formal'))
    # A code-mode explorer built its code to run unattended; its copy is told it may now join in.
    assert ('built to run a whole episode end to end on its own' in text) is not live_copy_session
    assert agent_runner.episode_prompt(SimpleNamespace(agent_formal=True), []).endswith('needs help.')


# -- supervisor: agent batch beside a bundle batch ---------------------------------------

@pytest.fixture
def agent_roots():
    yield
    Runner.hook = None


def frozen(tmp_path, agent_formal=True, **kwargs):
    backend = Backend()
    c = config(tmp_path, formal_episodes=3, agent_formal=agent_formal, **kwargs)
    s = Supervisor(c, lambda: backend, runner_factory=Runner)
    ws, h = workspace(tmp_path), home(tmp_path)
    from harness.codex_cli import auto_research
    assert auto_research.session_files(h, 'thread')
    (c.root/'bundles').mkdir(mode=0o700)
    manifest, _ = transfer.pack(c.root/'bundles'/'ckpt', workspace=ws, parts=c.transfer_parts, home=h,
                                session_files=auto_research.session_files(h, 'thread'), home_files=('config.toml',),
                                metadata={'session_id': 'thread', 'cli': 'codex'})
    manifest['directory'] = 'ckpt'
    s.state['bundles']['ckpt'] = manifest
    s._save()
    return s, backend


def test_agent_batch_runs_each_layout_once_with_its_own_records(tmp_path, agent_roots):
    s, backend = frozen(tmp_path)
    seen = []
    def factory(root, bundle_id, index, connection):
        agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                backend_factory=lambda: backend, runner_factory=Runner)
        return Finished()
    def run(bundle, job):
        seen.append(sorted(p.relative_to(bundle).as_posix() for p in Path(bundle).rglob('*') if p.is_file()))
        backend.success = len(seen) != 2
        recovered = [{'returncode': 1, 'error': CAPACITY}] if len(seen) == 1 else []
        return {'reason': 'exit', 'returncode': 0, 'agent_token_usage': {'input_tokens': 10, 'output_tokens': 1},
                'agent_errors': recovered}
    Runner.hook = run
    s.worker_factory = factory
    try:
        result = s.run_agent_formal('ckpt')
        with pytest.raises(RuntimeError, match='already been reserved'):
            s.run_agent_formal('ckpt')
    finally:
        s.close()
    assert backend.starts == [(True, 0), (True, 1), (True, 2)]
    assert (result['success_count'], result['unsuccessful_count'], result['episode_count']) == (2, 1, 3)
    assert result['driver'] == 'agent' and result['formal_token_usage']['input_tokens'] == 30
    assert all('transfer/memory/PROGRESS.md' in files and 'package.json' in files for files in seen)
    state = json.loads((s.config.root/'state.json').read_text())
    assert state['agent_formal_batch']['status'] == 'completed' and 'formal_batch' not in state
    rows = [r for r in state['results'] if r.get('mode') == 'formal']
    assert [r['driver'] for r in rows] == ['agent'] * 3
    report = json.loads((s.config.root/'published/agent_formal_batch.json').read_text())
    # A copy's failed runs, recovered or not, are in its episode's report.
    assert [e['agent_errors'] for e in report['episodes']] == [[{'returncode': 1, 'error': CAPACITY}], [], []]
    assert (s.config.root/'agent-formal-scores.json').is_file()
    assert (s.config.root/'agent-formal/episode-001/state.json').is_file()


def test_each_copy_runs_its_simulator_on_its_turn_of_the_formal_gpus(tmp_path, agent_roots, monkeypatch):
    """Copy i runs its simulator on formal_sim_gpus[i mod n]."""
    import socket
    gpus = ('GPU-aaaa', 'GPU-bbbb')
    s, backend = frozen(tmp_path, formal_sim_gpus=gpus)
    placed = []
    class Placed(Exception):
        pass
    def capture(supervisor, parent_root, bundle_id):
        placed.append(supervisor.config.sim_gpu)
        raise Placed  # Placement is all this test needs from the worker.
    monkeypatch.setattr(agent_worker, 'import_checkpoint', capture)
    try:
        for index in range(3):
            ours, theirs = socket.socketpair()
            with ours, theirs, pytest.raises(Placed):
                agent_worker.run_worker(s.config, 'ckpt', index, formal_worker.Channel(theirs),
                                        backend_factory=lambda: backend, runner_factory=Runner)
    finally:
        s.close()
    assert placed == ['GPU-aaaa', 'GPU-bbbb', 'GPU-aaaa']


def test_formal_workers_start_a_gap_apart_while_others_run(tmp_path, agent_roots, monkeypatch):
    """Simulators that start together can miss their startup limit: a worker starts a gap after the
    last one while that one still runs."""
    import time
    from services.controller import supervisor as module
    monkeypatch.setattr(module, 'WORKER_START_GAP_SECONDS', 0.5)
    s, backend = frozen(tmp_path, formal_workers=3)
    started = []
    class Running(Finished):
        def __init__(self):
            super().__init__()
            self.until = time.monotonic() + 1.5
        def poll(self):
            return None if time.monotonic() < self.until else 0
    def factory(root, bundle_id, index, connection):
        started.append(time.monotonic())
        agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                backend_factory=lambda: backend, runner_factory=Runner)
        return Running()
    Runner.hook = lambda bundle, job: {'reason': 'exit', 'returncode': 0}
    s.worker_factory = factory
    try:
        result = s.run_agent_formal('ckpt')
    finally:
        s.close()
    assert result['completed_episodes'] == 3
    gaps = [later - earlier for earlier, later in zip(started, started[1:])]
    assert len(gaps) == 2 and all(0.45 <= gap < 1.5 for gap in gaps), gaps


def test_formal_sim_gpus_are_validated(tmp_path):
    base = config(tmp_path, formal_episodes=2, agent_formal=True)
    c = replace(base, formal_sim_gpus=(base.sim_gpu, 'GPU-bbbb'), formal_workers=16)
    from dataclasses import asdict
    from services.controller.config import Configuration
    saved = json.loads(json.dumps(asdict(c), default=str))
    assert Configuration.from_dict(saved).formal_sim_gpus == c.formal_sim_gpus
    for bad, message in ((dict(formal_sim_gpus=(base.sim_gpu, base.sim_gpu)), 'distinct'),
                         (dict(formal_sim_gpus=[base.sim_gpu]), 'distinct'),
                         (dict(formal_sim_gpus=(base.sim_gpu, None)), 'distinct'),
                         (dict(formal_sim_gpus=('0',)), 'explicit GPU UUIDs'),
                         (dict(formal_workers=17), '1..16'),
                         (dict(agent_formal=False), 'agent formal only')):
        with pytest.raises(ValueError, match=message):
            replace(c, **bad)


class Stopped(Finished):
    """A worker still running when the session is stopped: the stop reaches the batch while polling it."""

    def __init__(self):
        super().__init__(code=-15)
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.polls == 1:
            raise KeyboardInterrupt
        return None


def test_an_interrupted_agent_formal_resumes_only_the_layouts_without_an_outcome(tmp_path, agent_roots):
    s, backend = frozen(tmp_path)
    def factory(root, bundle_id, index, connection):
        log = formal_worker.worker_root(root, index, 'agent-formal')
        log.parent.mkdir(exist_ok=True)
        (log.parent/f'{log.name}.log').open('xb').close()  # As spawn: a layout's log is never reopened.
        try:
            agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                    backend_factory=lambda: backend, runner_factory=Runner)
        except KeyboardInterrupt:  # As SIGTERM: the copy saves its cut-short episode, then exits.
            return Stopped()
        return Finished()
    def run(bundle, job):
        if len(backend.starts) == 3 and not resumed:
            raise KeyboardInterrupt  # The session stops while layout 2's copy is playing.
        backend.success = len(backend.starts) != 2
        return {'reason': 'exit', 'returncode': 0, 'agent_token_usage': {'input_tokens': 10, 'output_tokens': 1}}
    Runner.hook, s.worker_factory, resumed = run, factory, False
    try:
        with pytest.raises(KeyboardInterrupt):
            s.run_agent_formal('ckpt')
        stopped = s.state['agent_formal_batch']
        assert (stopped['status'], stopped['completed_episodes']) == ('interrupted', 2)
        assert [r['formal_outcome'] for r in s.state['results'] if r.get('mode') == 'formal'] == [
            'success', 'unsuccessful', 'interrupted']
        resumed = True
        result = checkpoint.agent_formal(s.config, s, {})
        with pytest.raises(RuntimeError, match='Only an interrupted'):
            s.resume_agent_formal()
    finally:
        s.close()
    assert backend.starts == [(True, 0), (True, 1), (True, 2), (True, 2)]  # Only layout 2 again.
    assert result['status'] == 'completed' and (result['success_count'], result['episode_count']) == (2, 3)
    state = json.loads((s.config.root/'state.json').read_text())
    batch = state['agent_formal_batch']
    assert (batch['completed_episodes'], batch['unsuccessful_count'], batch['formal_token_usage']['input_tokens']) == (3, 1, 30)
    rows = [r for r in state['results'] if r.get('mode') == 'formal']
    assert [(r['formal_episode_index'], r['formal_outcome']) for r in rows] == [
        (1, 'success'), (2, 'unsuccessful'), (3, 'success')]
    [interruption] = batch['interruptions']
    assert interruption['episodes'] == [3] and interruption['results'][0]['formal_outcome'] == 'interrupted'
    aside = s.config.root/interruption['set_aside']
    assert {p.name for p in aside.iterdir()} == {'episode-003', 'episode-003.log'}
    assert (aside/'episode-003/state.json').is_file() and (s.config.root/'agent-formal/episode-003/state.json').is_file()
    report = json.loads((s.config.root/'published/agent_formal_batch.json').read_text())
    assert [e['formal_episode_index'] for e in report['episodes']] == [1, 2, 3]
    scores = json.loads((s.config.root/'agent-formal-scores.json').read_text())
    assert [e['episode_index'] for e in scores['episodes']] == [1, 2, 3] and scores['episode_count'] == 3
    assert json.loads((s.config.root/'agent-formal.json').read_text())['status'] == 'completed'


def test_a_copy_that_finished_before_the_stop_keeps_its_outcome_and_is_not_played_again(tmp_path, agent_roots):
    s, backend = frozen(tmp_path)
    def factory(root, bundle_id, index, connection):
        agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                backend_factory=lambda: backend, runner_factory=Runner)
        # Layout 2's copy saved its evaluated episode; the stop reaches it while it exits.
        return Stopped() if index == 2 and not resumed else Finished()
    def run(bundle, job):
        backend.success = len(backend.starts) != 2
        return {'reason': 'exit', 'returncode': 0, 'agent_token_usage': {'input_tokens': 10, 'output_tokens': 1}}
    Runner.hook, s.worker_factory, resumed = run, factory, False
    try:
        with pytest.raises(KeyboardInterrupt):
            s.run_agent_formal('ckpt')
        stopped = s.state['agent_formal_batch']
        assert (stopped['status'], stopped['completed_episodes'], stopped['success_count']) == ('interrupted', 3, 2)
        rows = [r for r in s.state['results'] if r.get('mode') == 'formal']
        assert [(r['formal_episode_index'], r['formal_outcome']) for r in rows] == [
            (1, 'success'), (2, 'unsuccessful'), (3, 'success')]
        assert rows[2]['worker_returncode'] == -15
        assert rows[2]['infrastructure_errors'][0]['episode_record_finalized'] is True
        resumed = True
        result = checkpoint.agent_formal(s.config, s, {})
    finally:
        s.close()
    assert backend.starts == [(True, 0), (True, 1), (True, 2)]  # No layout is played twice.
    assert result['status'] == 'completed' and (result['success_count'], result['episode_count']) == (2, 3)
    batch = json.loads((s.config.root/'state.json').read_text())['agent_formal_batch']
    assert batch['completed_episodes'] == 3 and batch['interruptions'][0]['episodes'] == []


def test_a_resume_waits_for_a_copy_still_exiting_and_keeps_its_episode(tmp_path, agent_roots):
    s, backend = frozen(tmp_path)
    process = orphan(s, 'ckpt', 0, directory='agent-formal',
                     final={'results': [finalized(s, 'ckpt', 0, reason='exit', driver='agent')], 'active': None})
    s.state['formal_reserved'] = True
    s.state['agent_formal_batch'] = {'bundle': 'ckpt', 'sha256': s.state['bundles']['ckpt']['sha256'],
        'status': 'interrupted', 'episode_count': 3, 'completed_episodes': 0, 'success_count': 0,
        'unsuccessful_count': 0, 'error_count': 0, 'infrastructure_error_count': 0, 'success_rate': None,
        'started': 0, 'finished': 1, 'driver': 'agent', 'active_episodes': [1],
        'report_path': 'runtime/autonomous_controller/agent_formal_batch.json'}
    s.state['formal_worker_pids'] = {'1': process.pid}
    def factory(root, bundle_id, index, connection):
        agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                backend_factory=lambda: backend, runner_factory=Runner)
        return Finished()
    def run(bundle, job):
        backend.success = True
        return {'reason': 'exit', 'returncode': 0}
    Runner.hook, s.worker_factory = run, factory
    try:
        batch = s.resume_agent_formal()
        assert process.poll() == 0  # The copy exited before its folder was read or moved.
    finally:
        s.close()
        process.kill()
        process.wait()
    assert backend.starts == [(True, 1), (True, 2)]  # Layout 0 kept the stopped run's episode.
    assert (batch['status'], batch['completed_episodes'], batch['success_count']) == ('completed', 3, 3)
    rows = [r for r in s.state['results'] if r.get('mode') == 'formal']
    assert [(r['formal_episode_index'], r['formal_outcome']) for r in rows] == [
        (1, 'success'), (2, 'success'), (3, 'success')]
    assert batch['interruptions'][0]['episodes'] == [] and 'formal_worker_pids' not in s.state


def test_agent_formal_after_a_bundle_formal_plays_live_copies_beside_it(tmp_path, agent_roots):
    """A code-mode session (agent_formal off) whose bundle formal already ran: agent formal adds
    live copies on the same layouts and keeps the bundle batch. A copy's workspace is the
    live-copy one either way (formal_episode), so the saved configuration stays as it was."""
    s, backend = frozen(tmp_path, agent_formal=False)
    s.state['formal_batch'] = {'status': 'completed', 'success_count': 0, 'episode_count': 3}
    s._save()
    def factory(root, bundle_id, index, connection):
        agent_worker.run_worker(s.config, bundle_id, index, formal_worker.Channel(connection),
                                backend_factory=lambda: backend, runner_factory=Runner)
        return Finished()
    s.worker_factory = factory
    try:
        result = s.run_agent_formal('ckpt')
    finally:
        s.close()
    assert result['driver'] == 'agent' and result['completed_episodes'] == 3
    assert backend.starts == [(True, 0), (True, 1), (True, 2)]  # The bundle formal's layouts.
    state = json.loads((s.config.root/'state.json').read_text())
    assert state['configuration']['agent_formal'] is False
    assert state['formal_batch']['status'] == 'completed' and state['agent_formal_batch']['status'] == 'completed'
    assert (s.config.root/'agent-formal-scores.json').is_file()


def test_live_copies_after_a_bundle_formal_run_only_on_request(tmp_path, monkeypatch):
    c = config(tmp_path, formal_episodes=2)
    s = Supervisor(c, Backend, runner_factory=Runner)
    s.state.update(submission={'bundle': 'b'}, formal_batch={'status': 'completed'},
                   exploration_checkpoint={'bundle': 'frozen', 'session_id': 'thread'})
    s._save()
    s.close()
    (c.root/'exploration-complete.json').write_text('{}')
    calls = []
    monkeypatch.setattr(checkpoint, 'agent_formal',
                        lambda config, supervisor, deployed: calls.append(config) or {'status': 'completed'})
    monkeypatch.setattr(Supervisor, 'run_submission', lambda self: pytest.fail('the bundle formal reran'))
    assert 'agent_formal' not in checkpoint.after_exploration(c, {}) and calls == []
    summary = checkpoint.after_exploration(c, {}, live_copies=True)
    assert summary['status'] == 'completed' and summary['agent_formal'] == {'status': 'completed'} and calls == [c]


def test_agent_formal_refuses_a_controller_bundle(tmp_path):
    backend = Backend()
    s = Supervisor(config(tmp_path, formal_episodes=2), lambda: backend, runner_factory=Runner)
    try:
        bundle, _ = s.register(source(tmp_path, 'controller.py'))
        with pytest.raises(ValueError, match='agent checkpoint'):
            s.run_agent_formal(bundle)
    finally:
        s.close()


def test_after_exploration_without_the_marker_runs_nothing(tmp_path):
    c = config(tmp_path, formal_episodes=2, agent_formal=True)
    c.root.mkdir(parents=True, exist_ok=True)
    assert checkpoint.after_exploration(c, {})['status'] == 'exploration_incomplete'
    assert json.loads((c.root/'formal.json').read_text())['status'] == 'exploration_incomplete'


def test_an_agent_exit_after_model_calls_is_its_result_not_an_error():
    from services.controller.supervisor import formal_outcome
    assert agent_runner.episode_returncode(25, 1) == 0 and agent_runner.episode_returncode(0, 1) == 1
    assert agent_runner.episode_returncode(3, None) is None
    row = {'reason': 'exit', 'returncode': agent_runner.episode_returncode(25, 1), 'task_complete': False}
    assert formal_outcome(row) == 'unsuccessful'


def test_live_copy_task_text_has_no_evaluation_framing(tmp_path):
    from services.controller.demonstrations import task_demonstration_context
    c = config(tmp_path, formal_episodes=2)
    none = {'kind': 'none'}
    framing = r'evaluat(?!e`)|held-out|formal'
    for text in (BENCH.task_context(replace(c, agent_formal=True), none, live_formal=False),
                 BENCH.task_context(c, none, live_formal=True)):
        assert not re.search(framing, text, re.I), text
    demo = {'kind': 'terminal_state', 'image_count': 1}
    assert 'bundle' not in task_demonstration_context(demo, live_formal=True)
    assert 'bundle' not in task_demonstration_context(demo, bundles=False)
    assert 'bundle' in task_demonstration_context(demo)


def test_formal_copy_instructions_name_only_what_its_status_reports(tmp_path):
    case = f'{BENCH.name}/auto-research/formal-copy/rgbd'
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    import render_workspaces
    text = (render_workspaces.render_case(case, tmp_path)/'AGENTS.md').read_text()
    # Live copies have no model-request cap (retired), so status reports none.
    assert 'model requests' not in text and 'capped at the limit' not in text
    fields = set(re.findall(r'`(\w+)`', text.split('## Start here', 1)[1].split('Then read', 1)[0]))
    reported = {'task', 'carried', 'conversation', 'elapsed_seconds', 'episode_ended', 'episode_timeout_seconds'}
    assert fields - {'status'} <= reported and {'carried', 'conversation'} <= fields
    assert ('episode_timeout_seconds' in fields) == BENCH.wall_limit


@pytest.mark.parametrize('write_handoff', [False, *(() if 'write_handoff' in BENCH.fixed else (True,))])
def test_a_live_copy_session_that_carries_no_handoff_is_not_asked_to_write_one(tmp_path, write_handoff):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    import render_workspaces
    from harness.codex_cli.auto_research import populate_workspace
    from services.mcp_contract import Contract
    from services.workspace.validate import validate
    c = render_workspaces.configuration(tmp_path/'root', agent_formal=True, transfer_parts=('memory', 'code'),
                                        write_handoff=write_handoff)
    ws = tmp_path/'ws'
    ws.mkdir()
    populate_workspace(c, ws)
    texts = {path.relative_to(ws).as_posix(): path.read_text() for path in ws.rglob('*.md')}
    agents = texts['AGENTS.md']
    assert '**Keep notes and code current.**' in agents and 'update the notes, then end your turn' in agents
    if write_handoff:
        # An adaptation source still writes handoff/ for a later trial, framed as notes, never a bundle.
        assert 'later session on this task with new layouts.' in agents
        skill = texts['.agents/skills/transfer-handoff/SKILL.md']
        assert 'notes for a later session' in skill and 'bundle' not in skill and 'AGENTS.md says are kept' not in skill
    else:
        assert [name for name, text in texts.items() if 'handoff' in text] == []
    tools = Contract.from_config(c).tool_names - {'register', 'rehearse', 'submit'}
    assert validate(ws, tools=tools, live_copy=True) == []


def test_formal_copies_leave_the_parent_demonstration_record(tmp_path, monkeypatch):
    import importlib
    from services.controller import demonstrations
    monkeypatch.setattr(importlib.import_module(BENCH.adapter), 'provision_demonstration',
                        lambda config, target, demos_root: {'kind': 'none'})
    c = config(tmp_path, formal_episodes=2)
    c.root.mkdir(parents=True, exist_ok=True)
    demonstrations.provision_demonstration(c, tmp_path/'ws', tmp_path/'cache', record=False)
    assert not (c.root/'demonstration-context.json').exists()
    demonstrations.provision_demonstration(c, tmp_path/'ws', tmp_path/'cache')
    assert (c.root/'demonstration-context.json').is_file()


# Runs AgentRunner.run in a child process, as the formal worker does: an unhandled SIGALRM
# would kill the process that runs it.
FINALIZATION_CHILD = r'''
import json, subprocess, sys, time
from types import SimpleNamespace
from services.agent_formal.runner import AgentRunner
real_run = subprocess.run
subprocess.run = lambda command, **kw: (SimpleNamespace(returncode=0) if command[0] == 'docker'
                                        else real_run(command, **kw))
runner = object.__new__(AgentRunner)
runner.finalization_seconds = 0.5
runner.limits = SimpleNamespace(wall_seconds=None if sys.argv[1] == 'none' else float(sys.argv[1]))
runner.config = SimpleNamespace(agent_cli='codex', task='t', formal_context='clear', agent_formal=True)
relay, relay_options = SimpleNamespace(calls=3, close=lambda: None), {}
runner.cli = SimpleNamespace(CALL_SECONDS=1000, model_relay=lambda *a, **k: relay_options.update(k) or relay,
                             container_command=lambda *a, **k: ['sleep', '30'])
runner.deployed = lambda package, output, frames: ({'workspace': output}, [], {})
runner.keep_evidence = lambda output, deployed: {}
gateway = SimpleNamespace(terminal=sys.argv[2] == 'ended', simulator_exited=False)
started = time.monotonic()
result = runner.run(sys.argv[3], mode='formal', entrypoint='controller.py', gateway=gateway, token='t',
                    output=sys.argv[3]+'/out', name='rax-controller-'+'0'*32, frames=sys.argv[3]+'/f/frames')
print(json.dumps({'reason': result['reason'], 'returncode': result['returncode'],
                  'seconds': time.monotonic()-started, 'relay_options': sorted(relay_options),
                  'relay_seconds': relay_options['seconds']}))
'''


@pytest.mark.parametrize('wall, ended, reason', [(60, 'ended', 'finalization_timeout'), (1, 'live', 'timeout'),
                                                 ('none', 'ended', 'finalization_timeout')])
def test_an_agent_overrunning_its_time_is_stopped_and_recorded(tmp_path, wall, ended, reason):
    import subprocess
    import sys
    project = Path(agent_runner.__file__).resolve().parents[2]
    child = subprocess.run([sys.executable, '-c', FINALIZATION_CHILD, str(wall), ended, str(tmp_path)],
                           cwd=project, capture_output=True, text=True, timeout=60)
    # Before the fix the alarm had no handler: the worker died with -14 and no episode result.
    assert child.returncode == 0, (child.returncode, child.stderr[-2000:])
    result = json.loads(child.stdout.splitlines()[-1])
    # The killed copy has no exit code; the supervisor evaluates a finalization timeout as ended.
    assert result['reason'] == reason and result['returncode'] is None and result['seconds'] < 20
    # Only the wall limit ends a live copy: its relay counts model calls but never caps them.
    assert result['relay_options'] == ['calls_file', 'seconds']
    # Without a wall limit the relay's client timeout is the per-call one.
    # With one, it is the whole seconds left of it, plus 300.
    assert result['relay_seconds'] == (1300 if wall == 'none' else wall - 1 + 300)


CAPACITY = 'Selected model is at capacity. Please try a different model.'
# A Codex run's event stream: a failed run ends on the provider's error, as a real one did.
COPY_RUN = r'''
import json, sys
code = int(sys.argv[1])
events = [{'type': 'turn.started'}] + ([{'type': 'error', 'message': sys.argv[2]},
                                       {'type': 'turn.failed', 'error': {'message': sys.argv[2]}}]
                                      if code else [{'type': 'turn.completed'}])
print('\n'.join(json.dumps(event) for event in events), flush=True)
sys.exit(code)
'''


def copy_runs(tmp_path, monkeypatch, codes, *, latest=None):
    """Run AgentRunner.run on a copy whose runs exit with codes in turn; the runs it started and docker calls."""
    import subprocess
    import sys
    from types import SimpleNamespace
    real_run = subprocess.run
    docker = []
    monkeypatch.setattr(agent_runner.subprocess, 'run', lambda command, **kw: (
        docker.append(command) or SimpleNamespace(returncode=0) if command[0] == 'docker' else real_run(command, **kw)))
    monkeypatch.setattr(agent_runner, 'ERROR_PAUSE_SECONDS', 0)
    runs = []

    def container_command(config, deployed, relay_dir, name, *, prompt, json_output):
        runs.append({'prompt': prompt, 'resume': deployed.get('resume_session'), 'removed_before': len(docker)})
        return [sys.executable, '-c', COPY_RUN, str(codes[len(runs)-1]), CAPACITY]
    runner = object.__new__(agent_runner.AgentRunner)
    runner.finalization_seconds = 0.5
    runner.limits = SimpleNamespace(wall_seconds=None)
    runner.config = SimpleNamespace(agent_cli='codex', task='t', formal_context='compact', agent_formal=True)
    runner.cli = SimpleNamespace(CALL_SECONDS=1000, HOME_KEY='codex_home', container_command=container_command,
                                 model_relay=lambda *a, **k: SimpleNamespace(calls=3, close=lambda: None),
                                 latest_session=latest or (lambda home: f'{home.name}-thread-{len(runs)}'))
    runner.deployed = lambda package, output, frames: (
        {'workspace': output, 'codex_home': tmp_path/'home', 'resume_session': 'exploring-thread'}, [], {})
    runner.keep_evidence = lambda output, deployed: {}
    gateway = SimpleNamespace(terminal=False, simulator_exited=None)
    result = runner.run(tmp_path, mode='formal', entrypoint='controller.py', gateway=gateway, token='t',
                        output=tmp_path/'out', name='rax-controller-'+'0'*32, frames=tmp_path/'f/frames')
    return result, runs, docker


def test_a_copy_whose_run_fails_on_a_provider_error_resumes_its_conversation(tmp_path, monkeypatch):
    from services.controller.supervisor import formal_outcome
    result, runs, docker = copy_runs(tmp_path, monkeypatch, [1, 0])
    # Before the fix the provider's "at capacity" ended the copy, and its live episode with it.
    assert [run['resume'] for run in runs] == ['exploring-thread', 'home-thread-1']
    assert runs[1]['prompt'] == agent_runner.ERROR_PROMPT.format(error=CAPACITY)
    assert ['docker', 'rm', '--force', 'rax-controller-'+'0'*32] in docker[:runs[1]['removed_before']]
    assert [(e['returncode'], e['error']) for e in result['agent_errors']] == [(1, CAPACITY)]
    assert result['reason'] == 'exit' and result['returncode'] == 0 and 'error' not in result
    assert result['agent_returncode'] == 0
    # Both runs' event streams are the episode's stdout log.
    events = [json.loads(line)['type'] for line in (tmp_path/'out/stdout.log').read_text().splitlines()]
    assert events == ['turn.started', 'error', 'turn.failed', 'turn.started', 'turn.completed']
    assert formal_outcome({**result, 'task_complete': False}) == 'unsuccessful'


def test_a_copy_that_keeps_failing_is_given_up_and_its_episode_is_an_error(tmp_path, monkeypatch):
    from services.controller.supervisor import formal_outcome
    monkeypatch.setattr(agent_runner, 'MAX_ERROR_RELAUNCHES', 2)
    result, runs, _ = copy_runs(tmp_path, monkeypatch, [1, 1, 1])
    assert len(runs) == 3 and [e['error'] for e in result['agent_errors']] == [CAPACITY] * 3
    assert result['returncode'] == 1 and result['error'] == CAPACITY
    assert result['reason'] == 'exit' and formal_outcome({**result, 'task_complete': False}) == 'error'
    from services.controller.progress_report import detail
    assert detail({**result, 'formal_outcome': 'error'}, tmp_path) == CAPACITY


def test_a_long_failing_run_resets_the_count_of_failures_in_a_row(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runner, 'MAX_ERROR_RELAUNCHES', 1)
    monkeypatch.setattr(agent_runner, 'STEADY_SECONDS', 0)
    result, runs, _ = copy_runs(tmp_path, monkeypatch, [1, 1, 1, 0])
    assert len(runs) == 4 and len(result['agent_errors']) == 3 and result['returncode'] == 0


def test_a_failed_copy_with_no_conversation_to_resume_is_an_error(tmp_path, monkeypatch):
    def latest(home):
        raise RuntimeError('No Codex conversation to resume')
    result, runs, _ = copy_runs(tmp_path, monkeypatch, [1], latest=latest)
    assert len(runs) == 1 and result['returncode'] == 1 and result['error'] == CAPACITY


def test_a_runs_error_is_read_from_its_own_part_of_the_event_stream(tmp_path):
    log = tmp_path/'stdout.log'
    first = json.dumps({'type': 'turn.failed', 'error': {'message': 'first run'}}) + '\n'
    second = 'x' * 70000 + '\n' + json.dumps({'type': 'error', 'message': 'second run'}) + '\nnot json\n'
    log.write_text(first + json.dumps({'type': 'turn.completed'}) + '\n')
    assert agent_runner.run_error('codex', log, 0, None) == 'first run'
    offset = log.stat().st_size
    assert agent_runner.run_error('codex', log, offset, None) is None
    log.write_text(log.read_text() + second)
    assert agent_runner.run_error('codex', log, offset, None) == 'second run'
    # Claude Code records its API errors in its conversation.
    home = tmp_path/'claude-home'
    (home/'projects/-workspace').mkdir(parents=True)
    (home/'projects/-workspace/s.jsonl').write_text(json.dumps(
        {'isApiErrorMessage': True, 'message': {'content': [{'type': 'text', 'text': 'API Error: 529 Overloaded'}]}},
        separators=(',', ':')) + '\n')
    assert agent_runner.run_error('claude', log, 0, home) == 'API Error: 529 Overloaded'


def test_a_failure_to_keep_a_copys_evidence_leaves_its_task_outcome(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.controller.supervisor import formal_outcome
    monkeypatch.setattr(agent_runner.workspace_storage, 'unmount', lambda root: None)
    monkeypatch.setattr(agent_runner.workspace_storage, 'mounted', lambda path: False)

    def copy_regular(source, destination, budget):
        raise OSError(28, 'No space left on device', 'files/home/sessions')
    monkeypatch.setattr(agent_runner, 'copy_regular', copy_regular)
    runner = object.__new__(agent_runner.AgentRunner)
    runner.config = SimpleNamespace(agent_cli='codex', formal_context='compact')
    runner.cli = SimpleNamespace(HOME_KEY='codex_home')
    (tmp_path/'out').mkdir()
    kept = runner.keep_evidence(tmp_path/'out', {'codex_home': tmp_path/'home', 'workspace': tmp_path/'ws'})
    assert kept == {'infrastructure_errors': [{
        'type': 'OSError', 'operation': 'keep_evidence', 'stage': 'evidence_copy',
        'reason': "[Errno 28] No space left on device: 'files/home/sessions'"}]}
    # The episode's own evidence (a clean exit, a completed task) still decides its outcome.
    row = {'reason': 'exit', 'returncode': 0, 'task_complete': True, 'output_error': None, **kept}
    assert formal_outcome(row) == 'success'


def test_a_copys_token_usage_is_read_from_its_live_home_not_the_kept_copy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    storage = tmp_path/'storage'
    rollout = storage/'codex-home/sessions/2026/10/10/rollout-2026-10-10T20-53-56-thread.jsonl'
    rollout.parent.mkdir(parents=True)
    (storage/'workspace/memory').mkdir(parents=True)
    rollout.write_text(json.dumps({'type': 'token_usage_record', 'payload': {
        'usage': {'input_tokens': 100, 'output_tokens': 5},
        'thread_token_usage': {'input_tokens': 1100, 'output_tokens': 55}}}) + '\n')
    monkeypatch.setattr(agent_runner.workspace_storage, 'unmount', lambda root: None)
    monkeypatch.setattr(agent_runner.workspace_storage, 'mounted', lambda path: False)
    # A resumed exploring conversation's rollout is gigabytes, over the kept copy's budget.
    real = agent_runner.copy_regular
    monkeypatch.setattr(agent_runner, 'copy_regular', lambda source, destination, budget: real(
        source, destination, budget={'files': 2000, 'bytes': 10}))
    runner = object.__new__(agent_runner.AgentRunner)
    runner.config = SimpleNamespace(agent_cli='codex', formal_context='compact')
    runner.cli = SimpleNamespace(HOME_KEY='codex_home')
    output = tmp_path/'out'
    output.mkdir()
    kept = runner.keep_evidence(output, {'codex_home': storage/'codex-home', 'workspace': storage/'workspace',
                                         'baseline_usage': {'input_tokens': 1000, 'output_tokens': 50,
                                                            'total_tokens': 1050}})
    assert not list((output/'files/home').rglob('rollout-*')) and 'output_error' not in kept
    # Before the fix it was read from the kept copy, which had no rollout: None.
    assert kept['agent_token_usage'] == {'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105}
