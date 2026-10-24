"""Adaptation: a finished source trial's agent continues in a new environment."""
import json

import pytest

from services import adaptation
from test_formal_batch import config
from bench import BENCH

# A task of the default benchmark with its native randomized variant, the pair adaptation joins.
PAIRS = [(task, task + '_random') for task in BENCH.task_names() if task + '_random' in BENCH.task_names()]
pytestmark = pytest.mark.skipif(not PAIRS or 'adaptation_source' in BENCH.fixed,
                                reason=f'{BENCH.label} has no adaptation')
BASE, RANDOM = PAIRS[0] if PAIRS else ('', '')


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def source_trial(tmp_path, *, task=None, cli='codex'):
    root = tmp_path/'source'
    storage = root/'agent-storage'
    home_name = 'claude-home' if cli == 'claude' else 'codex-home'
    for name in ('workspace', home_name):
        (storage/name).mkdir(parents=True)
    (root/'agent-workspace').symlink_to(storage/'workspace')
    (root/home_name).symlink_to(storage/home_name)
    ws = root/'agent-workspace'
    write(ws/'memory/PROGRESS.md', 'grasp at 0.82')
    write(ws/'handoff/README.md', 'strategy')
    write(ws/'code/ctl/controller.py', 'def main(ctx): pass')
    if cli == 'codex':
        write(root/'codex-home/sessions/2026/09/30/rollout-a.jsonl',
              json.dumps({'type': 'session_meta', 'payload': {'id': 'thread', 'thread_source': 'user'}}) + '\n')
    else:
        write(root/'claude-home/projects/-workspace/thread.jsonl', '{"type": "user"}\n')
    for name in ('launch.lock', 'owner.lock'):
        (root/name).touch()
    state = {'configuration': {'task': task or BASE, 'mode': 'auto-research', 'agent_cli': cli, 'eval_seed': 0},
             'active': None, 'results': []}
    (root/'state.json').write_text(json.dumps(state))
    return root


@pytest.fixture
def mounted(monkeypatch):
    from harness.codex_cli import workspace_storage
    monkeypatch.setattr(workspace_storage, 'remount', lambda root: None)


def adapting(tmp_path, source, **kwargs):
    c = config(tmp_path, **{'formal_episodes': 2, 'task': RANDOM, 'exploration_seeds': (1, 2),
                            'adaptation_source': str(source), **kwargs})
    c.root.mkdir(parents=True, exist_ok=True)
    return c


@pytest.mark.parametrize('context, resumed', [('resume', 'thread'), ('compact', 'thread'), ('clear', None)])
def test_import_carries_folders_and_the_conversation_unless_clearing(tmp_path, mounted, context, resumed):
    source = source_trial(tmp_path)
    c = adapting(tmp_path, source, adaptation_context=context, transfer_parts=('memory', 'handoff'))
    ws, home = tmp_path/'new-ws', tmp_path/'new-home'
    ws.mkdir()
    home.mkdir()
    assert adaptation.import_source(c, ws, home) == resumed
    assert (ws/'memory/PROGRESS.md').read_text() == 'grasp at 0.82' and (ws/'handoff/README.md').is_file()
    assert not (ws/'code').exists()
    assert (home/'sessions/2026/09/30/rollout-a.jsonl').is_file() == (resumed is not None)
    note = (ws/'ADAPTATION.md').read_text()
    assert f'randomized variant {RANDOM}' in note and '2 exploration episodes' in note
    record = json.loads((c.root/'adaptation-source.json').read_text())
    assert record['context'] == context and record['transferred'] == ['handoff', 'memory']
    # The source is only read.
    assert (source/'agent-workspace/code/ctl/controller.py').is_file()


@pytest.mark.parametrize('agent_formal', [False, True])
def test_the_note_of_a_live_copy_session_frames_nothing_as_an_evaluation(tmp_path, mounted, agent_formal):
    from services.workspace.validate import live_copy_terms
    source = source_trial(tmp_path)
    c = adapting(tmp_path, source, agent_formal=agent_formal, adaptation_context='clear')
    ws, home = tmp_path/'new-ws', tmp_path/'new-home'
    ws.mkdir()
    home.mkdir()
    adaptation.import_source(c, ws, home)
    note = (ws/'ADAPTATION.md').read_text()
    assert '2 exploration episodes' in note and 'poses or colors' in note
    assert live_copy_terms(ws, agent_formal) == [] if agent_formal else 'formal evaluation' in note
    assert ('bundles' in note) != agent_formal


def test_claude_sources_resume_their_transcript(tmp_path, mounted):
    source = source_trial(tmp_path, cli='claude')
    c = adapting(tmp_path, source, agent_cli='claude', agent_provider='claude-login', adaptation_context='resume')
    ws, home = tmp_path/'ws', tmp_path/'home'
    ws.mkdir()
    home.mkdir()
    assert adaptation.import_source(c, ws, home) == 'thread'
    assert (home/'projects/-workspace/thread.jsonl').is_file()


@pytest.mark.parametrize('change, message', [
    (dict(task=next(t for t in BENCH.task_names() if t not in (BASE, RANDOM))), 'Adapt '),
    (dict(agent_cli='claude', agent_provider='claude-login', adaptation_context='resume'), 'agent CLI'),
])
def test_incompatible_sources_are_refused(tmp_path, mounted, change, message):
    source = source_trial(tmp_path)
    c = adapting(tmp_path, source, **change)
    with pytest.raises(ValueError, match=message):
        adaptation.import_source(c, tmp_path, tmp_path)


def test_a_clear_context_may_switch_the_agent_cli(tmp_path, mounted):
    source = source_trial(tmp_path)
    c = adapting(tmp_path, source, agent_cli='claude', agent_provider='claude-login', adaptation_context='clear')
    ws, home = tmp_path/'ws', tmp_path/'home'
    ws.mkdir()
    home.mkdir()
    assert adaptation.import_source(c, ws, home) is None and (ws/'memory/PROGRESS.md').is_file()


def test_a_running_source_is_refused(tmp_path, mounted):
    import fcntl
    source = source_trial(tmp_path)
    c = adapting(tmp_path, source)
    with (source/'owner.lock').open('rb') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match='still running'):
            adaptation.import_source(c, tmp_path, tmp_path)


def test_adaptation_configuration_is_validated(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, formal_episodes=2, adaptation_context='fresh')
    with pytest.raises(ValueError, match='absolute trial path'):
        config(tmp_path, formal_episodes=2, adaptation_source='relative/trial')
    assert adaptation.compatible(BASE, RANDOM) and adaptation.compatible(BASE, BASE)


def test_startup_prompt_points_to_the_adaptation_note(tmp_path):
    from harness.codex_cli.auto_research import startup_prompt
    write(tmp_path/'START_PROMPT.md', 'start')
    assert startup_prompt(tmp_path) == 'start'
    write(tmp_path/'ADAPTATION.md', 'note')
    assert 'ADAPTATION.md' in startup_prompt(tmp_path)
