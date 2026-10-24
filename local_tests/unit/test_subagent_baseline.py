"""Multi-agent baseline: subagents scale independently of exploration environments."""
import json

import pytest

from types import SimpleNamespace

from harness.claude_cli.auto_research import agent_command
from harness.codex_cli.configuration import subagent_settings
from services import subagent_metrics
from services.mcp_contract import Contract
from services.workspace import describe
from bench import OBSERVE, STEP, TASK


@pytest.mark.parametrize('envs, subagents, cap', [(1, 0, 0), (1, 3, 3), (3, 0, 3), (3, 1, 1)])
def test_subagent_cap(envs, subagents, cap):
    assert Contract(environments=envs, subagents=subagents).subagent_cap == cap


@pytest.mark.parametrize('kwargs', [dict(subagents=9), dict(subagents=-1), dict(subagents=True),
                                    dict(subagents=2, phase='isolated')])
def test_invalid_subagents(kwargs):
    with pytest.raises(ValueError):
        Contract(**kwargs)


def test_isolated_phases_never_inherit_subagents():
    from types import SimpleNamespace
    config = SimpleNamespace(observation_profile='rgbd', exploration_envs=1, subagents=4)
    assert Contract.from_config(config).subagent_cap == 4
    assert Contract.from_config(config, isolated=True).subagent_cap == 0


def test_codex_settings_follow_the_cap():
    assert subagent_settings(0) == {'features': {'multi_agent': False, 'multi_agent_v2': False}}
    assert subagent_settings(3) == {'features': {'multi_agent': True, 'multi_agent_v2': True},
                                    'agents': {'enabled': True, 'max_concurrent_threads_per_session': 3}}


def test_claude_subagent_tool_is_removed_only_without_a_cap(tmp_path):
    deployed = {'claude': 'claude', 'claude_home': tmp_path, 'model': 'm'}
    denied = lambda cap: agent_command(SimpleNamespace(), {**deployed, 'subagents': cap}, prompt='go')
    assert 'Agent' in denied(0) and 'Task' in denied(0) and 'WebFetch' in denied(0)
    assert 'Agent' not in denied(2) and 'WebFetch' in denied(2)


def test_claude_effort_follows_the_session_setting(tmp_path):
    deployed = {'claude': 'claude', 'claude_home': tmp_path, 'model': 'm'}
    effort = lambda config: (args := agent_command(config, deployed, prompt='go'))[args.index('--effort')+1]
    assert effort(SimpleNamespace()) == 'xhigh' and effort(SimpleNamespace(agent_effort='max')) == 'max'


def test_single_environment_description_points_to_the_turn_taking_skill():
    shared = describe(Contract(subagents=2))
    assert 'Up to 2 subagents' in shared and 'multi-agent-exploration' in shared and 'env_id' not in shared
    assert 'subagents' not in describe(Contract())


def test_settings_accept_and_bound_subagents(tmp_path):
    from services.dashboard.sessions import ResearchSessions
    sessions = ResearchSessions(tmp_path, lambda: [TASK])
    assert sessions.settings({})['subagents'] == 0
    assert sessions.settings({'subagents': 4})['subagents'] == 4
    with pytest.raises(ValueError):
        sessions.settings({'subagents': 9})


def rollout(home, name, meta_source, rows):
    path = home/'sessions/2026/09/30'/f'rollout-{name}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {'timestamp': rows[0][0], 'type': 'session_meta', 'payload': {'id': name, 'source': meta_source}}
    lines = [meta] + [{'timestamp': t, 'type': 'response_item', 'payload': payload} for t, payload in rows]
    path.write_text(''.join(json.dumps(line) + '\n' for line in lines))


def code(*tools):
    return {'type': 'custom_tool_call', 'name': 'exec', 'input': ''.join(f'await tools.{t}({{}});\n' for t in tools)}


def spawn(parent):
    return {'subagent': {'thread_spawn': {'parent_thread_id': parent, 'agent_nickname': 'w'}}}


def test_codex_metrics_count_spawns_concurrency_and_interleaving(tmp_path):
    t = lambda s: f'2026-09-30T10:00:{s:02d}Z'
    step, observe = f'mcp__auto_research__{STEP}', f'mcp__auto_research__{OBSERVE}'
    rollout(tmp_path, 'main', 'exec', [(t(0), code(step)), (t(20), code(step)),
                                       (t(50), {'type': 'function_call', 'name': 'spawn_agent', 'namespace': 'collaboration'})])
    rollout(tmp_path, 'w1', spawn('main'), [(t(10), code(observe, 'mcp__auto_research__gemini_generate')),
                                          (t(40), {'type': 'function_call', 'name': STEP, 'namespace': 'mcp__auto_research'})])
    rollout(tmp_path, 'w2', spawn('main'), [(t(30), code('mcp__auto_research__status')),
                                          (t(59), code('mcp__auto_research__status'))])
    result = subagent_metrics.measure(tmp_path, cap=2)
    assert (result['cap'], result['spawned'], result['peak_concurrent']) == (2, 2, 2)
    # Environment calls in time order: main 0, w1 10, main 20, w1 40 -> 3 handoffs;
    # main's call at 20 and w1's at 40 each follow another agent's call within 60 s.
    assert (result['environment_calls'], result['handoffs'], result['interleaved_calls']) == (4, 3, 2)
    by_id = {agent['id']: agent for agent in result['agents']}
    assert by_id['w1']['mcp_calls'] == 3 and by_id['w1']['environment_calls'] == 2
    assert by_id['w2']['environment_calls'] == 0


def test_claude_metrics_read_sidechain_transcripts(tmp_path):
    workspace = tmp_path/'projects/-workspace'
    (workspace/'s1/subagents').mkdir(parents=True)
    def transcript(path, rows, **extra):
        path.write_text(''.join(json.dumps({'timestamp': time, **extra, 'message': {'content': [
            {'type': 'tool_use', 'name': name, 'input': {}}]}}) + '\n' for time, name in rows))
    transcript(workspace/'s1.jsonl', [('2026-09-30T10:00:00Z', 'mcp__auto_research__start_episode'),
                                     ('2026-09-30T10:05:00Z', 'Agent')])
    transcript(workspace/'s1/subagents/agent-a.jsonl', [('2026-09-30T10:05:01Z', 'Bash'),
                                                         ('2026-09-30T10:06:00Z', 'Read')], agentId='a', sessionId='s1')
    result = subagent_metrics.measure(tmp_path, cli='claude', cap=0)
    assert (result['spawned'], result['peak_concurrent'], result['environment_calls']) == (1, 1, 1)


def test_metrics_write_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent_metrics, 'measure', lambda *a, **k: (_ for _ in ()).throw(ValueError('bad')))
    result = subagent_metrics.write(tmp_path, tmp_path/'out/subagents.json', cap=1)
    assert result['error'] == 'ValueError: bad'
    assert json.loads((tmp_path/'out/subagents.json').read_text())['cap'] == 1
