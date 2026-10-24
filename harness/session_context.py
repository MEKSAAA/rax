"""Compact an auto-research conversation with its agent CLI (Codex or Claude Code).

One bounded, never-fatal native agent invocation with a stub MCP endpoint that lists no
tools, so nothing in the environment can change. It writes the CLI's own compaction into
the same transcript, which later resumes continue from.

- Codex 0.157 has no one-shot compact command; `exec resume` with
  model_auto_compact_token_limit=1000 forces its auto-compaction on that turn (a
  `compacted` record; user messages are kept, tool outputs summarized).
- Claude Code: `/compact <instructions>` on the resumed conversation (a
  `compact_boundary` record with pre/post token counts). If print mode does not run
  the built-in command, the auto-compact threshold is lowered for one turn instead.
"""
import json
from pathlib import Path
import socketserver
import subprocess
import threading
import time

from harness import agent_host

COMPACT_SECONDS = 600
COMPACT_PROMPT = (
    'Summarize this robot exploration for an agent that continues from it in a new episode. Keep '
    'exactly: the task and every rubric condition with how it was verified; the tested strategy with '
    'its parameters (poses, heights, offsets, grasp and approach choices, step counts) and the code '
    'that implements it; what failed and why; the open uncertainties; and which workspace folders '
    '(memory/, code/, handoff/) hold the saved notes and code. Historical episode IDs, frame paths, '
    'tool results and budgets are not current state.')
COMPACT_ACK = 'Context checkpoint. Do not call any tools. Reply with only OK.'


class Stub(socketserver.StreamRequestHandler):
    """MCP endpoint with no tools: the CLI starts normally but cannot act."""
    def handle(self):
        while line := self.rfile.readline(1024 * 1024):
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if 'id' not in request:
                continue
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'compaction', 'version': '1.0'}}
                reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
            elif method == 'tools/list':
                reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': {'tools': []}}
            else:
                reply = {'jsonrpc': '2.0', 'id': request['id'],
                         'error': {'code': -32601, 'message': 'No tools during compaction'}}
            self.wfile.write(json.dumps(reply).encode() + b'\n')
            self.wfile.flush()


class StubServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def codex_context(home, session_id):
    """(compaction records, per-call input tokens before the latest one, first after it).

    Without a compaction, `before` is the latest call's input tokens.
    """
    count, before, after, last = 0, None, None, None
    for path in Path(home, 'sessions').rglob('rollout-*.jsonl'):
        with path.open() as stream:
            try:
                if (json.loads(stream.readline()).get('payload') or {}).get('id') != session_id:
                    continue
            except ValueError:
                continue
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get('payload') or {}
                if row.get('type') == 'compacted':
                    count, before, after = count + 1, last, None
                elif row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                    tokens = ((payload.get('info') or {}).get('last_token_usage') or {}).get('input_tokens')
                    if tokens:
                        if count and after is None:
                            after = tokens
                        last = tokens
    return count, before if count else last, after


def claude_context(home, session_id):
    """(compaction boundaries, preTokens and postTokens of the latest one)."""
    path = next(Path(home, 'projects').glob(f'*/{session_id}.jsonl'), None)
    count, before, after = 0, None, None
    for line in path.open() if path else ():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('type') == 'system' and row.get('subtype') == 'compact_boundary':
            metadata = row.get('compactMetadata') or {}
            count, before, after = count + 1, metadata.get('preTokens'), metadata.get('postTokens')
    return count, before, after


def compact(config, deployed, session_id, *, log, seconds=COMPACT_SECONDS):
    """Compact `session_id` in place; returns a record, never raises for a failed attempt."""
    from services.agent_formal.runner import harness
    cli = harness(config.agent_cli)
    home = deployed[cli.HOME_KEY]
    measure = claude_context if config.agent_cli == 'claude' else codex_context
    count, before, _ = measure(home, session_id)
    record = {'cli': config.agent_cli, 'status': 'failed', 'context_tokens_before': before,
              'context_tokens_after': None}
    started = time.monotonic()
    if config.agent_cli == 'claude':
        attempts = [('claude /compact', {'resume_session': session_id}, '/compact ' + COMPACT_PROMPT),
                    ('claude auto-compaction at a 1% threshold',
                     {'resume_session': session_id, 'environment': {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '1'}},
                     COMPACT_ACK)]
    else:
        attempts = [('codex exec resume with forced auto-compaction',
                     {'resume_session': session_id, 'codex_overrides': [
                         '-c', 'model_auto_compact_token_limit=1000',
                         '-c', 'compact_prompt=' + json.dumps(COMPACT_PROMPT)]}, COMPACT_ACK)]
    for method, overrides, prompt in attempts:
        record['method'] = method
        try:
            returncode = run_once(config, cli, {**deployed, **overrides}, prompt, log=log,
                                  seconds=max(30, seconds - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            record['reason'] = 'timed out'
            break
        except (OSError, RuntimeError, ValueError) as exc:
            record['reason'] = f'{type(exc).__name__}: {exc}'
            break
        new_count, _, after = measure(home, session_id)
        if new_count > count:
            record.update(status='compacted', context_tokens_after=after)
            if config.agent_cli == 'claude':
                record['context_tokens_before'] = measure(home, session_id)[1]
            record.pop('reason', None)
            break
        record['reason'] = f'exit {returncode}; no compaction record in the conversation'
    record['seconds'] = round(time.monotonic() - started, 1)
    return record


def run_once(config, cli, deployed, prompt, *, log, seconds):
    socket = agent_host.socket_path(config.root/'compaction')
    socket.unlink(missing_ok=True)
    server = StubServer(str(socket), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    agent = None
    try:
        cli.write_home(config, deployed, socket)  # Every launch rewrites it for the session's own socket.
        if config.agent_cli == 'claude':
            cli.relocate(deployed[cli.HOME_KEY], deployed['workspace'])
        command = cli.agent_command(config, deployed, prompt=prompt, json_output=True)
        environment = {**cli.agent_environment(config, deployed, socket), **deployed.get('environment', {})}
        with open(log, 'ab') as stream:
            agent = agent_host.start(command, cwd=deployed['workspace'], env=environment,
                                     stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
            return agent.wait(timeout=seconds)
    finally:
        agent_host.stop(agent)
        server.shutdown()
        server.server_close()
        socket.unlink(missing_ok=True)
