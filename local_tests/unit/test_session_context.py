"""Compaction of container conversations (Codex, Claude Code) and the formal fork workspace."""
import copy
import json
from pathlib import Path
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
        attempts.append(deployed.get('container_env'))
        if deployed.get('container_env'):
            with path.open('a') as stream:
                stream.write(json.dumps(boundary(100, 10)) + '\n')
        return 0
    monkeypatch.setattr(session_context, 'run_once', once)
    record = session_context.compact(c, {'claude_home': home}, 'thread', log=tmp_path/'log')
    assert attempts == [None, ['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=1']]
    assert record['status'] == 'compacted' and 'threshold' in record['method']


def test_compaction_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    c = config(tmp_path, formal_episodes=2)
    def once(*a, **k):
        raise subprocess.TimeoutExpired('codex', 1)
    monkeypatch.setattr(session_context, 'run_once', once)
    record = session_context.compact(c, {'codex_home': codex_home(tmp_path)}, 'thread', log=tmp_path/'log')
    assert record['status'] == 'failed' and record['reason'] == 'timed out'


def test_container_commands_accept_formal_mounts_and_overrides(tmp_path):
    from harness.codex_cli import auto_research as codex
    from harness.claude_cli import auto_research as claude
    c = config(tmp_path, formal_episodes=2)
    frames = tmp_path/'published/runs/formal-a/frames'
    base = {'workspace': tmp_path/'ws', 'bridge': tmp_path/'bridge.py', 'resume_session': 'thread',
            'published_mounts': [(frames, '/workspace/runtime/autonomous_controller/runs/formal-a/frames', True)],
            'container_env': ['A=1']}
    command = codex.container_command(c, {**base, 'codex_home': tmp_path/'h', 'codex': tmp_path/'codex',
                                          'codex_overrides': ['-c', 'x=1']}, tmp_path, 'n', prompt='go', json_output=True)
    joined = ' '.join(command)
    assert f'src={frames},dst=/workspace/runtime/autonomous_controller/runs/formal-a/frames,readonly' in joined
    assert f'src={c.root}/published' not in joined and '--env A=1' in joined
    assert command[command.index('-c'):command.index('-c') + 2] == ['-c', 'x=1']
    assert command.index('-c') < command.index('exec') and command[-2:] == ['thread', 'go']
    command = claude.container_command(c, {**base, 'claude_home': tmp_path/'h', 'claude': tmp_path/'claude',
                                           'model': 'm'}, tmp_path, 'n', prompt='go', json_output=True)
    assert '--resume' in command and f'src={c.root}/published' not in ' '.join(command)


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
                  metadata={'session_id': 'thread', 'baseline_usage': {'input_tokens': 5}})
    c = config(tmp_path, formal_episodes=2, formal_context='resume', **{'agent_formal': True, **overrides})
    c.root.mkdir(parents=True, exist_ok=True)
    (c.root/'model-relay.json').write_text('{}')
    def provision(root, image, megabytes):
        (root/'agent-storage').mkdir(parents=True)
        return root/'agent-storage'
    monkeypatch.setattr(agent_runner.workspace_storage, 'provision', provision)
    monkeypatch.setattr(agent_runner.shutil, 'which', lambda name: '/bin/true')
    if worker is None:
        runner = agent_runner.AgentRunner(c.image, c.formal, config=c)
    else:
        built = {}
        class Built(Exception):
            pass
        def supervisor(config, backend_factory, channel, *, runner_factory, published_root):
            built['runner'] = runner_factory(config.image, config.formal)
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
    assert deployed['resume_session'] == 'thread' and deployed['baseline_usage'] == {'input_tokens': 5}
    assert deployed['published_mounts'] == [(frames, '/workspace/runtime/autonomous_controller/runs/formal-abc/frames', True)]
    # The frames stay on the host, outside the copy's capped storage, as its prompt says.
    assert not frames.is_relative_to(output) and workspace.is_relative_to(output/'agent-storage')


CALL = '''import http.client, json, socket, sys
connection = http.client.HTTPConnection('localhost', timeout=10)
connection.sock = socket.socket(socket.AF_UNIX)
connection.sock.connect(sys.argv[1])
connection.request('POST', sys.argv[2], json.dumps({'model': sys.argv[3], 'max_tokens': 8, 'input': [],
                   'messages': [{'role': 'user', 'content': 'hi'}]}), {'Content-Type': 'application/json'})
response = connection.getresponse()
sys.exit(0 if response.status == 200 and response.read() else 1)
'''


@pytest.mark.parametrize('cli_name', ['codex', 'claude'])
def test_compaction_calls_count_toward_the_session(tmp_path, monkeypatch, cli_name):
    """The compaction turn runs through the session's real relay: its model call joins the session's
    persisted count (and, for Claude, its token usage log) like every other call."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import sys
    import threading
    from harness.claude_cli import auto_research as claude
    from harness.codex_cli import auto_research as codex

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = json.dumps({'usage': {'input_tokens': 13, 'output_tokens': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    c = config(tmp_path, agent_cli=cli_name)
    c.root.mkdir(parents=True)
    (c.root/'model-calls.json').write_text('{"calls": 5}')
    key = tmp_path/'key'
    key.write_text('k')
    key.chmod(0o600)
    model = 'anthropic/claude-opus-5.5' if cli_name == 'claude' else 'openai/gpt-6-astra'
    relay = ({'provider': 'openrouter', 'key_file': str(key), 'base_url': f'http://127.0.0.1:{upstream.server_port}',
              'model': model} if cli_name == 'claude' else
             {'base_url': f'http://127.0.0.1:{upstream.server_port}/v1', 'headers': {}, 'model': model})
    (tmp_path/'model-relay.json').write_text(json.dumps(relay))
    module = claude if cli_name == 'claude' else codex
    route = '/v1/messages' if cli_name == 'claude' else '/responses'
    monkeypatch.setattr(module, 'container_command', lambda config, deployed, relay_dir, name, **_: [
        sys.executable, '-c', CALL, str(relay_dir/'model.sock'), route, model])
    run = subprocess.run
    monkeypatch.setattr(session_context.subprocess, 'run', lambda command, **kwargs: (
        subprocess.CompletedProcess(command, 0) if command[0] == 'docker' else run(command, **kwargs)))
    try:
        returncode = session_context.run_once(c, module, {'relay_configuration': tmp_path/'model-relay.json'},
                                              'compact', log=tmp_path/'compaction.jsonl', seconds=30)
    finally:
        upstream.shutdown()
        upstream.server_close()
    assert returncode == 0 and json.loads((c.root/'model-calls.json').read_text()) == {'calls': 6}
    if cli_name == 'claude':
        row = json.loads((c.root/'model-usage.jsonl').read_text())
        assert row['model'] == model and row['input_tokens'] == 13 and row['output_tokens'] == 2
    else:
        assert not (c.root/'model-usage.jsonl').exists()
