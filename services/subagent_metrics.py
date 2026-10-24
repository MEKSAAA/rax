"""Post-hoc subagent usage from Codex rollouts or Claude Code transcripts.

The MCP gateway has no client identity, so environment calls are attributed from
each agent's own transcript. Robot calls issued by Python scripts that an agent runs
(auto-research api.runtime) are not visible here; the counts cover MCP tool calls.
"""
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from services.benchmarks import ALL_ROBOT_TOOLS, ALL_UNSCOPED_TOOLS

# MCP tools that neither act on nor observe an environment.
GLOBAL_TOOLS = frozenset({'status', 'exploration_status', 'gemini_generate', *ALL_UNSCOPED_TOOLS,
                          *(name for name in ALL_ROBOT_TOOLS if name.endswith('_preview_describe'))})
CODE_CALL = re.compile(r'tools\.(mcp__[A-Za-z0-9_]+)\(')
# An A -> B -> A sequence counts as interleaved control when A's two calls are this close.
INTERLEAVE_SECONDS = 60


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def _rows(path):
    with open(path, errors='replace') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('timestamp'):
                yield row


def _mcp_name(name, namespace=''):
    """Short MCP tool name, or None for a non-MCP tool."""
    if name.startswith('mcp__'):
        return name.split('__', 2)[-1]
    if namespace.startswith('mcp__'):
        return name
    return None


def codex_agents(home):
    agents = []
    for path in sorted(Path(home, 'sessions').rglob('rollout-*.jsonl')):
        rows = list(_rows(path))
        if not rows or rows[0].get('type') != 'session_meta':
            continue
        meta = rows[0]['payload']
        spawn = (meta.get('source') or {}).get('subagent', {}).get('thread_spawn') if isinstance(meta.get('source'), dict) else None
        calls = []
        for row in rows:
            payload = row.get('payload') or {}
            if row.get('type') != 'response_item':
                continue
            if payload.get('type') == 'function_call':
                names = [_mcp_name(payload.get('name', ''), payload.get('namespace') or '')]
            elif payload.get('type') == 'custom_tool_call':
                names = [_mcp_name(name) for name in CODE_CALL.findall(payload.get('input', ''))]
            else:
                continue
            calls += [(_time(row['timestamp']), name) for name in names if name]
        agents.append({'id': meta.get('id', path.stem), 'subagent': bool(spawn),
                       'parent': spawn and spawn.get('parent_thread_id'),
                       'name': spawn and (spawn.get('agent_nickname') or spawn.get('agent_path')),
                       'start': _time(rows[0]['timestamp']), 'end': _time(rows[-1]['timestamp']), 'calls': calls})
    return agents


def claude_agents(home):
    agents = []
    for path in sorted(Path(home, 'projects').rglob('*.jsonl')):
        rows = list(_rows(path))
        if not rows:
            continue
        subagent = path.parent.name == 'subagents'
        calls = []
        for row in rows:
            message = row.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else ():
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    name = _mcp_name(block.get('name', ''))
                    if name:
                        calls.append((_time(row['timestamp']), name))
        agents.append({'id': rows[0].get('agentId') if subagent else path.stem, 'subagent': subagent,
                       'parent': rows[0].get('sessionId') if subagent else None, 'name': None,
                       'start': _time(rows[0]['timestamp']), 'end': _time(rows[-1]['timestamp']), 'calls': calls})
    return agents


def peak_concurrency(intervals):
    events = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals],
                    key=lambda item: (item[0], item[1]))
    peak = current = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def summarize(agents, *, cap=None):
    subagents = [agent for agent in agents if agent['subagent']]
    environment = sorted((time, agent['id']) for agent in agents for time, name in agent['calls']
                         if name not in GLOBAL_TOOLS)
    handoffs = interleaved = 0
    for index in range(1, len(environment)):
        if environment[index][1] != environment[index - 1][1]:
            handoffs += 1
    last = {}
    for index, (time, caller) in enumerate(environment):
        previous = last.get(caller)
        if previous is not None and time - environment[previous][0] <= INTERLEAVE_SECONDS and any(
                other != caller for _, other in environment[previous + 1:index]):
            interleaved += 1
        last[caller] = index
    return {
        'cap': cap,
        'spawned': len(subagents),
        'peak_concurrent': peak_concurrency([(agent['start'], agent['end']) for agent in subagents]),
        'environment_calls': len(environment),
        'handoffs': handoffs,
        'interleaved_calls': interleaved,
        'agents': [{'id': agent['id'], 'subagent': agent['subagent'], 'name': agent['name'],
                    'parent': agent['parent'], 'seconds': round(agent['end'] - agent['start'], 1),
                    'mcp_calls': len(agent['calls']),
                    'environment_calls': sum(name not in GLOBAL_TOOLS for _, name in agent['calls'])}
                   for agent in agents],
        'note': 'MCP tool calls from transcripts; robot calls made by agent-run Python scripts are not attributed.',
    }


def measure(home, *, cli='codex', cap=None):
    return summarize(claude_agents(home) if cli == 'claude' else codex_agents(home), cap=cap)


def write(home, path, *, cli='codex', cap=None):
    """Best effort: a measurement failure never affects the trial."""
    try:
        result = measure(home, cli=cli, cap=cap)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'cap': cap, 'error': f'{type(error).__name__}: {error}'}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    if len(sys.argv) not in (2, 3):
        raise SystemExit('usage: python -m services.subagent_metrics <codex-or-claude-home> [codex|claude]')
    print(json.dumps(measure(sys.argv[1], cli=sys.argv[2] if len(sys.argv) == 3 else 'codex'), indent=2))
