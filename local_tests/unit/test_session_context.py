"""Compaction of agent conversations (Codex, Claude Code) and the formal fork workspace."""
import copy
import json
import subprocess

import pytest

from harness import session_context
from test_formal_batch import config


def rows(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(item) + '\n' for item in items))
    return path


def tokens(n):
    return {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': n}}}}


def codex_home(tmp_path, *extra):
    home = tmp_path/'codex-home'
    rows(home/'sessions/2026/09/30/rollout-x.jsonl',
         [{'type': 'session_meta', 'payload': {'id': 'thread'}}, tokens(11000), tokens(90000), *extra])
    return home


def boundary(pre, post):
    return {'type': 'system', 'subtype': 'compact_boundary', 'compactMetadata': {'preTokens': pre, 'postTokens': post}}


def test_codex_context_reports_calls_around_the_latest_compaction(tmp_path):
    assert session_context.codex_context(codex_home(tmp_path), 'thread') == (0, 90000, None)
    home = codex_home(tmp_path/'b', {'type': 'compacted', 'payload': {}}, tokens(12000), tokens(13000))
    assert session_context.codex_context(home, 'thread') == (1, 90000, 12000)


def test_claude_context_reads_compact_boundaries(tmp_path):
    home = tmp_path/'claude-home'
    rows(home/'projects/-workspace/s1.jsonl', [{'type': 'user'}, boundary(168138, 11497)])
    assert session_context.claude_context(home, 's1') == (1, 168138, 11497)
    assert session_context.claude_context(home, 'missing') == (0, None, None)


@pytest.mark.parametrize('cli', ['codex', 'claude'])
def test_compact_records_success_by_the_new_record(tmp_path, monkeypatch, cli):
    c = config(tmp_path, formal_episodes=2, agent_cli=cli, agent_provider='claude-login' if cli == 'claude' else 'openrouter')
    if cli == 'codex':
        home = codex_home(tmp_path)
        path = home/'sessions/2026/09/30/rollout-x.jsonl'
        append = [{'type': 'compacted', 'payload': {}}, tokens(12000)]
    else:
        home = tmp_path/'claude-home'
        path = rows(home/'projects/-workspace/thread.jsonl', [{'type': 'user'}])
        append = [boundary(150000, 9000)]
    calls = []
    def once(config, harness, deployed, prompt, *, log, seconds):
        calls.append((deployed, prompt))
        with path.open('a') as stream:
            stream.write(''.join(json.dumps(r) + '\n' for r in append))
        return 0
    monkeypatch.setattr(session_context, 'run_once', once)
    record = session_context.compact(c, {f'{cli}_home': home}, 'thread', log=tmp_path/'log')
    assert record['status'] == 'compacted' and len(calls) == 1
    deployed, prompt = calls[0]
    assert deployed['resume_session'] == 'thread'
    if cli == 'codex':
        assert 'model_auto_compact_token_limit=1000' in deployed['codex_overrides']
        assert record['context_tokens_before'] == 90000 and record['context_tokens_after'] == 12000
    else:
        assert prompt.startswith('/compact ')
        assert (record['context_tokens_before'], record['context_tokens_after']) == (150000, 9000)


def test_claude_falls_back_to_a_lowered_auto_compact_threshold(tmp_path, monkeypatch):
    c = config(tmp_path, formal_episodes=2, agent_cli='claude', agent_provider='claude-login')
    home = tmp_path/'claude-home'
    path = rows(home/'projects/-workspace/thread.jsonl', [{'type': 'user'}])
    attempts = []
    def once(config, harness, deployed, prompt, *, log, seconds):
        attempts.append(deployed.get('environment'))
        if deployed.get('environment'):
            with path.open('a') as stream:
                stream.write(json.dumps(boundary(100, 10)) + '\n')
        return 0
    monkeypatch.setattr(session_context, 'run_once', once)
    record = session_context.compact(c, {'claude_home': home}, 'thread', log=tmp_path/'log')
    assert attempts == [None, {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '1'}]
    assert record['status'] == 'compacted' and 'threshold' in record['method']


def test_compaction_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    c = config(tmp_path, formal_episodes=2)
    def once(*a, **k):
        raise subprocess.TimeoutExpired('codex', 1)
    monkeypatch.setattr(session_context, 'run_once', once)
    record = session_context.compact(c, {'codex_home': codex_home(tmp_path)}, 'thread', log=tmp_path/'log')
    assert record['status'] == 'failed' and record['reason'] == 'timed out'


def test_one_compaction_run_adds_its_environment_to_the_agent_and_stops_it(tmp_path, monkeypatch):
    from types import SimpleNamespace
    c = config(tmp_path, formal_episodes=2, agent_cli='claude', agent_provider='claude-login')
    workspace = tmp_path/'ws'
    workspace.mkdir()
    started, stopped = [], []
    class Agent:
        def wait(self, timeout):
            return 0
    cli = SimpleNamespace(HOME_KEY='claude_home', write_home=lambda config, deployed, socket: None,
                          relocate=lambda home, workspace: None,
                          agent_command=lambda config, deployed, *, prompt, json_output: ['claude', '-p', prompt],
                          agent_environment=lambda config, deployed, socket: {'HOME': 'h', 'A': '0'})
    def start(command, *, cwd, env, **streams):
        started.append((command, cwd, env))
        return Agent()
    monkeypatch.setattr(session_context.agent_host, 'start', start)
    monkeypatch.setattr(session_context.agent_host, 'stop', stopped.append)
    deployed = {'workspace': workspace, 'claude_home': tmp_path/'h', 'environment': {'A': '1'}}
    assert session_context.run_once(c, cli, deployed, 'go', log=tmp_path/'log', seconds=5) == 0
    assert started == [(['claude', '-p', 'go'], workspace, {'HOME': 'h', 'A': '1'})] and len(stopped) == 1


def test_native_commands_resume_with_overrides_and_read_only_their_own_frames(tmp_path):
    from harness import agent_host
    from harness.codex_cli import auto_research as codex
    from harness.claude_cli import auto_research as claude
    c = config(tmp_path, formal_episodes=2)
    frames = (tmp_path/'published/runs/formal-a/frames').resolve()
    workspace = tmp_path/'ws'
    workspace.mkdir()
    socket = agent_host.socket_path(c.root)
    login = tmp_path/'login.json'
    login.write_text(json.dumps({'model': 'm', 'key_file': str(tmp_path/'key'), 'provider': 'anthropic'}))
    base = {'workspace': workspace, 'resume_session': 'thread', 'frames': frames, 'login': login, 'subagents': 0}
    deployed = {**base, 'codex_home': tmp_path/'h', 'codex': tmp_path/'codex', 'codex_overrides': ['-c', 'x=1']}
    command = codex.agent_command(c, deployed, prompt='go', json_output=True)
    assert command[command.index('-c'):command.index('-c') + 2] == ['-c', 'x=1']
    assert command.index('-c') < command.index('exec') and command[-2:] == ['thread', 'go']
    filesystem = codex.permissions(c, deployed, socket)['filesystem']
    assert filesystem[str(frames)] == 'write' and str((c.root/'published').resolve()) not in filesystem
    assert filesystem[str(workspace.resolve())] == 'write'
    deployed = {**base, 'claude_home': tmp_path/'h', 'claude': tmp_path/'claude', 'model': 'm'}
    command = claude.agent_command(c, deployed, prompt='go', json_output=True)
    assert command[command.index('--resume') + 1] == 'thread' and command[-2:] == ['--', 'go']
    sandbox = claude.settings(c, deployed, socket)['sandbox']['filesystem']
    assert str(frames) in sandbox['allowRead'] and str((c.root/'published').resolve()) not in sandbox['allowRead']


def deploy(tmp_path, monkeypatch, *, worker=None, **overrides):
    """A copy deployed from a two-folder package; ``worker`` deploys it with the runner that
    formal worker builds (services/agent_formal/worker.py), as the agent formal batch does."""
    from services import transfer
    from services.agent_formal import runner as agent_runner, worker as agent_worker
    ws = tmp_path/'source-ws'
    for name, text in (('memory/PROGRESS.md', 'p'), ('code/ctl/controller.py', 'c'), ('output/x.bin', 'x')):
        (ws/name).parent.mkdir(parents=True, exist_ok=True)
        (ws/name).write_text(text)
    home = tmp_path/'source-home'
    rows(home/'sessions/2026/09/30/rollout-x.jsonl', [{'type': 'session_meta', 'payload': {'id': 'thread'}}])
    (home/'config.toml').write_text('model = "m"')
    package = tmp_path/'bundle'
    transfer.pack(package, workspace=ws, parts=('memory', 'code'), home=home,
                  session_files=['sessions/2026/09/30/rollout-x.jsonl'], home_files=('config.toml',),
                  metadata={'session_id': 'thread'})
    c = config(tmp_path, formal_episodes=2, formal_context='resume', **{'agent_formal': True, **overrides})
    c.root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_runner.shutil, 'which', lambda name: '/bin/true')
    if worker is None:
        runner = agent_runner.AgentRunner(c.formal, config=c)
    else:
        built = {}
        class Built(Exception):
            pass
        def supervisor(config, backend_factory, channel, *, runner_factory, published_root):
            built['runner'] = runner_factory(config.formal)
            raise Built
        def configuration(parent, index, directory):
            value = copy.copy(parent)
            object.__setattr__(value, 'formal_seed', parent.formal_seed + index)
            return value
        monkeypatch.setattr(agent_worker, 'WorkerSupervisor', supervisor)
        monkeypatch.setattr(agent_worker, 'worker_configuration', configuration)
        with pytest.raises(Built):
            agent_worker.run_worker(c, 'ckpt', worker, channel=None)
        runner = built['runner']
    frames = tmp_path/'published/runs/formal-abc/frames'
    frames.mkdir(parents=True)
    output = tmp_path/'out'
    output.mkdir()
    return (*runner.deployed(package, output, frames), frames, output)


def test_fork_workspace_holds_only_the_package(tmp_path, monkeypatch):
    deployed, transferred, record, frames, output = deploy(tmp_path, monkeypatch)
    workspace = deployed['workspace']
    assert transferred == ['memory', 'code']
    assert (workspace/'memory/PROGRESS.md').read_text() == 'p' and not (workspace/'output/x.bin').exists()
    assert (workspace/'AGENTS.md').is_file() and (workspace/'TASK.md').is_file()
    assert (workspace/'runtime/autonomous_controller/runs/formal-abc/frames').is_dir()
    assert (deployed['codex_home']/'sessions/2026/09/30/rollout-x.jsonl').is_file()
    assert deployed['resume_session'] == 'thread'
    # Its own episode's frames, where its instructions say, linked to where the episode writes them.
    link = workspace/'runtime/autonomous_controller/runs/formal-abc/frames'
    assert link.is_symlink() and link.resolve() == frames.resolve() and deployed['frames'] == frames
    assert not frames.is_relative_to(output) and workspace == output/'workspace'


