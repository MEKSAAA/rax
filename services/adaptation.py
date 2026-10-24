"""Adaptation trial: continue a finished source trial's agent in a new environment.

The new environment is this trial's own task and layout collection: the source task
on another (e.g. generated, randomized) collection, or its native `<task>_random`
variant. The agent carries a transfer package from the source: selected workspace
folders (capped at transfer_mb) and, unless the context is 'clear', its conversation,
which a 'compact' context compacts before the first turn. The source's frame history,
robot sessions and budgets are never reopened or copied.
"""
from contextlib import ExitStack
import fcntl
import json
from pathlib import Path
import tempfile

from services.controller.recording import read_regular
from services.controller.storage import persist, verify
from services.transfer import PARTS, pack, unpack

PROMPT = ('You are continuing in a NEW environment. Read ADAPTATION.md first: it says what changed, '
          'what you carried over and your new, small episode budget. Then follow AGENTS.md and TASK.md '
          'for this environment; call status for the live state.')


def compatible(source_task, task):
    """The same task (another collection) or its native randomized variant."""
    return task in (source_task, source_task + '_random')


def source_trial(config):
    """Validated source root and its saved configuration."""
    source = Path(config.adaptation_source).resolve()
    if source == config.root.resolve() or config.root.resolve().is_relative_to(source):
        raise ValueError('Adaptation source must be a separate finished trial')
    original = json.loads(read_regular(source/'state.json', 16*1024*1024))['configuration']
    if original.get('mode', 'auto-research') != 'auto-research':
        raise ValueError('Adaptation sources are auto-research trials')
    if not compatible(original['task'], config.task):
        raise ValueError(f'Adapt {original["task"]} to itself on another collection or to {original["task"]}_random')
    if config.adaptation_context != 'clear' and original.get('agent_cli', 'codex') != config.agent_cli:
        raise ValueError('Resuming a conversation needs the source trial\'s agent CLI (or use context clear)')
    return source, original


def import_source(config, workspace, home):
    """Copy the transfer package into this trial; returns the conversation to resume, if any."""
    if not config.adaptation_source:
        return None
    from services.agent_formal.runner import harness
    source, original = source_trial(config)
    source_cli = harness(original.get('agent_cli', 'codex'))
    state = json.loads(read_regular(source/'state.json', 16*1024*1024))
    from services.agent_formal.checkpoint import package_path
    frozen = package_path(source, state)
    if frozen is not None:
        # The source as it stood when its exploration ended: nothing from its formal
        # evaluations (bundle or agent) reaches this trial. The source may still be running
        # them; the frozen package is only read.
        verify(frozen, state['bundles'][state['exploration_checkpoint']['bundle']])
        frozen_record = json.loads((frozen/'package.json').read_text())
        session_id = frozen_record.get('session_id') if config.adaptation_context != 'clear' else None
        with tempfile.TemporaryDirectory(dir=config.root) as temporary:
            package = Path(temporary)/'package'
            _, record = pack(package, workspace=frozen/'transfer', parts=config.transfer_parts,
                             max_mb=config.transfer_mb, home=frozen/'session',
                             session_files=frozen_record['session_files'] if session_id else (),
                             metadata={'session_id': session_id, 'frozen_at': frozen_record.get('frozen_at')})
            present = unpack(package, workspace=workspace, home=home, context=config.adaptation_context)
    else:
        session_id, present, record = _import_live(config, source, source_cli, workspace, home)
    summary = {'source_trial': str(source), 'source_task': original['task'], 'source_cli': original.get('agent_cli', 'codex'),
               'source_eval_seed': original.get('eval_seed'), 'task': config.task, 'eval_seed': config.eval_seed,
               'context': config.adaptation_context, 'resume_session': session_id, 'transferred': present,
               'excluded': record['excluded'], 'skipped': len(record['skipped'])}
    persist(config.root/'adaptation-source.json', summary)
    (workspace/'ADAPTATION.md').write_text(note(config, original, present, record))
    return session_id


def _import_live(config, source, source_cli, workspace, home):
    """Sessions frozen before formal was isolated: copy from the stopped source's live state."""
    with ExitStack() as stack:
        # The source must be stopped; its state and transcripts are only read.
        for name in ('launch.lock', 'owner.lock'):
            stream = stack.enter_context((source/name).open('rb'))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('Adaptation source is still running') from exc
        state = json.loads(read_regular(source/'state.json', 16*1024*1024))
        if state.get('active') or any((state.get(key) or {}).get('status') == 'running'
                                      for key in ('formal_batch', 'agent_formal_batch')):
            raise ValueError('Adaptation source has an unfinished episode or batch')
        source_workspace = (source/'agent-workspace').resolve()
        source_home = (source/source_cli.HOME_NAME).resolve()
        for path in (source_workspace, source_home):
            if not path.is_relative_to(source.resolve()):
                raise ValueError('Source workspace is not inside the source session')
        session_id = None
        if config.adaptation_context != 'clear':
            session_id = source_cli.latest_session(source_home)
        with tempfile.TemporaryDirectory(dir=config.root) as temporary:
            package = Path(temporary)/'package'
            _, record = pack(package, workspace=source_workspace, parts=config.transfer_parts,
                             max_mb=config.transfer_mb, home=source_home,
                             session_files=source_cli.session_files(source_home, session_id) if session_id else (),
                             metadata={'session_id': session_id})
            present = unpack(package, workspace=workspace, home=home, context=config.adaptation_context)
    return session_id, present, record


def note(config, original, present, record):
    changed = ('the same task on a different set of layouts' if original['task'] == config.task
               else f'the randomized variant {config.task} of {original["task"]} (TASK.md says what varies)')
    carried = ', '.join(f'{name}/' for name in present) or 'no files'
    conversation = {'resume': 'your whole earlier conversation',
                    'compact': 'a compacted summary of your earlier conversation',
                    'clear': 'no earlier conversation'}[config.adaptation_context]
    # A live-copy session frames nothing as an evaluation and has no bundles (AGENTS.md).
    live_copy = config.agent_formal
    lines = ['# Adaptation', '',
             f'You explored {original["task"]} before. This environment is {changed}.', '',
             f'Carried over: {carried} (at most {config.transfer_mb} MB), and {conversation}.',
             'Frames, episode IDs, ' + ('' if live_copy else 'bundles, ') + 'budgets and paths from the '
             'earlier environment are gone;',
             'do not assume earlier poses or colors still hold. Verify them here first.', '',
             f'This environment gives {len(config.exploration_seeds)} exploration episodes; '
             + ('then continue as AGENTS.md and status describe.' if live_copy
                else 'the formal evaluation follows as AGENTS.md and status describe.')]
    if record['excluded']:
        lines += ['', 'Left out (over the size limit on carried folders): ' + ', '.join(
            f'{PARTS[name]}/' for name in record['excluded']) + '.']
    return '\n'.join(lines) + '\n'
