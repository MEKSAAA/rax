"""Formal is a standalone, frozen evaluation: the agent is frozen when exploration ends,
formal runs from that freeze afterwards, and nothing it produces reaches the agent or any
trial that continues from the session."""
import fcntl
import json

import pytest

from services import adaptation, transfer
from services.agent_formal import checkpoint
from services.controller.frontend import ResearchFrontend
from services.controller.supervisor import Supervisor
from test_adaptation import BENCH, PAIRS, adapting, source_trial
from test_controller_backend import Backend, Runner, source
from test_formal_batch import config


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def session(tmp_path, **kwargs):
    c = config(tmp_path, **{'formal_episodes': 1, 'exploration_seeds': (3,), **kwargs})
    backend = Backend()
    s = Supervisor(c, lambda: backend, runner_factory=Runner)
    ws = tmp_path/'ws'
    write(ws/'memory/PROGRESS.md', 'learned in exploration')
    write(ws/'code/ctl/controller.py', 'def main(ctx): pass')
    home = tmp_path/'codex-home'
    write(home/'config.toml', 'model = "m"')
    write(home/'sessions/2026/09/30/rollout-a.jsonl',
          json.dumps({'type': 'session_meta', 'payload': {'id': 'thread', 'thread_source': 'user'}}) + '\n')
    return s, ws, home


def text(response):
    return json.loads(response['content'][0]['text'])


def rehearsed(s, tmp_path):
    bundle, manifest = s.register(source(tmp_path, 'controller.py'))
    s.state['results'].append({'mode': 'rehearsal', 'bundle': bundle, 'sha256': manifest['sha256'],
                               'task_complete': True, 'returncode': 0, 'reason': 'exit'})
    return bundle


def test_submit_closes_exploration_and_runs_nothing(tmp_path):
    s, ws, _ = session(tmp_path)
    frontend = ResearchFrontend(s, ws)
    try:
        bundle = rehearsed(s, tmp_path)
        frontend.call('start_episode', {})  # Submitting closes a live exploration episode.
        reply = text(frontend.call('submit', {'bundle': bundle}))
        assert reply['status'] == 'submitted' and 'success_rate' not in reply and 'returncode' not in reply
        assert not s.state['active']
        assert 'you do not see its result' in reply['next_step']
        assert s.state['submission']['bundle'] == bundle and s.state['formal_reserved']
        assert not [r for r in s.state['results'] if r['mode'] == 'formal']
        assert (s.config.root/'exploration-complete.json').is_file()
        assert frontend.status()['submitted_bundle'] == bundle and 'formal_batch' not in frontend.status()
        with pytest.raises(Exception):
            frontend.call('start_episode', {})
    finally:
        frontend.close()


def test_a_bundle_session_is_told_formal_runs_unseen(tmp_path):
    s, ws, _ = session(tmp_path)
    frontend = ResearchFrontend(s, ws)
    try:
        bundle = rehearsed(s, tmp_path)
        reply = text(frontend.call('submit', {'bundle': bundle}))
        assert reply['status'] == 'submitted' and 'you do not see' in reply['next_step']
    finally:
        frontend.close()


def test_formal_runs_from_the_freeze_once(tmp_path):
    s, ws, home = session(tmp_path)
    try:
        s.submit(rehearsed(s, tmp_path))
        deployed = {'workspace': ws, 'codex_home': home}
        ident = checkpoint.freeze_exploration(s.config, s, deployed)
        assert checkpoint.freeze_exploration(s.config, s, deployed) == ident  # Frozen once.
        frozen = checkpoint.package_path(s.config.root, s.state)
        # Anything the agent writes after the freeze is not part of it.
        write(ws/'memory/PROGRESS.md', 'written after formal')
        assert (frozen/'transfer/memory/PROGRESS.md').read_text() == 'learned in exploration'
        assert (frozen/'session/sessions/2026/09/30/rollout-a.jsonl').is_file()
        record = json.loads((frozen/'package.json').read_text())
        assert set(p for p, info in record['parts'].items() if info) == {'memory', 'code'}
        assert record['stage'] == 'exploration_end'
        assert s.run_submission()['returncode'] == 0
        assert [r['mode'] for r in s.state['results']].count('formal') == 1
        assert s.submission_done() and s.run_submission() is None  # No second run.
        # The agent formal checkpoint takes its folders from the freeze as well.
        agent = checkpoint.freeze(s.config, s, deployed)
        carried = s.config.root/'bundles'/s.state['bundles'][agent]['directory']
        assert (carried/'transfer/memory/PROGRESS.md').read_text() == 'learned in exploration'
    finally:
        s.close()


def test_an_open_exploration_freezes_nothing(tmp_path):
    s, _, _ = session(tmp_path, agent_formal=True)
    s.close()
    assert checkpoint.after_exploration(s.config, {})['status'] == 'exploration_incomplete'
    assert not checkpoint.exploration_closed(s.config)


@pytest.mark.skipif(not PAIRS or 'adaptation_source' in BENCH.fixed, reason=f'{BENCH.label} has no adaptation')
def test_adaptation_reads_the_freeze_even_while_the_source_runs_formal(tmp_path):
    source_root = source_trial(tmp_path)
    from harness.codex_cli import auto_research
    home = source_root/'codex-home'
    (source_root/'bundles').mkdir()
    manifest, _ = transfer.pack(
        source_root/'bundles'/'freeze', workspace=source_root/'agent-workspace', parts=tuple(transfer.PARTS),
        home=home, session_files=auto_research.session_files(home, 'thread'),
        metadata={'session_id': 'thread', 'frozen_at': 1.0, 'stage': 'exploration_end'})
    manifest['directory'] = 'freeze'
    state = json.loads((source_root/'state.json').read_text())
    state['bundles'] = {'freeze': manifest}
    state['exploration_checkpoint'] = {'bundle': 'freeze', 'session_id': 'thread'}
    state['formal_batch'] = {'status': 'running'}
    (source_root/'state.json').write_text(json.dumps(state))
    write(source_root/'agent-workspace/memory/PROGRESS.md', 'formal results')  # After the freeze.
    c = adapting(tmp_path, source_root, adaptation_context='resume', transfer_parts=('memory',))
    ws, new_home = tmp_path/'new-ws', tmp_path/'new-home'
    ws.mkdir()
    new_home.mkdir()
    with (source_root/'launch.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # The source is still running its formal batch.
        assert adaptation.import_source(c, ws, new_home) == 'thread'
    assert (ws/'memory/PROGRESS.md').read_text() == 'grasp at 0.82'
    assert (new_home/'sessions/2026/09/30/rollout-a.jsonl').is_file()
