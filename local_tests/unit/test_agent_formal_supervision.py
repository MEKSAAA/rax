"""Live-copy mode (agent formal on): one evaluation mode per session, so exploration has
no bundle tools, closes when its episodes are spent, and only agent formal follows."""
import json

import pytest

from services.agent_formal import checkpoint
from services.controller.batch import import_rehearsed_bundle
from services.controller.frontend import DEFINITIONS, ResearchFrontend, for_config
from services.controller.supervisor import Supervisor
from services.mcp_contract import BUNDLE_TOOLS, Contract
from test_controller_backend import Backend, Runner, source
from test_formal_batch import config


def frontend_for(tmp_path, **kwargs):
    c = config(tmp_path, **{'formal_episodes': 1, 'agent_formal': True, 'exploration_seeds': (3,), **kwargs})
    s = Supervisor(c, Backend, runner_factory=Runner)
    return s, ResearchFrontend(s, tmp_path/'ws')


def text(response):
    return json.loads(response['content'][0]['text'])


def test_live_copy_contract_has_no_bundle_tools(tmp_path):
    live = Contract.from_config(config(tmp_path, formal_episodes=1, agent_formal=True))
    code = Contract.from_config(config(tmp_path, formal_episodes=1))
    assert not live.tool_names & BUNDLE_TOOLS and BUNDLE_TOOLS <= code.tool_names
    # The formal copy's isolated contract has no lifecycle tools in either mode.
    assert not Contract.from_config(config(tmp_path, agent_formal=True), isolated=True).lifecycle_tools


def test_live_copy_tools_read_as_practice(tmp_path):
    s, frontend = frontend_for(tmp_path)
    try:
        tools = {t['name']: t['description'] for t in frontend.tools()}
        assert not set(tools) & BUNDLE_TOOLS
        for description in tools.values():
            for word in ('bundle', 'submit', 'rehears', 'formal', 'held-out', 'score'):
                assert word not in description.lower(), description
        assert 'carry' in tools['status'] and 'next_step' in tools['status']
    finally:
        frontend.close()
    code = config(tmp_path/'code', formal_episodes=1)
    assert next(t for t in for_config(DEFINITIONS, code) if t['name'] == 'submit')
    assert 'cleanly rehearsed' in next(t for t in DEFINITIONS if t['name'] == 'submit')['description']


@pytest.mark.parametrize('name', sorted(BUNDLE_TOOLS))
def test_live_copy_refuses_bundle_calls(tmp_path, name):
    s, frontend = frontend_for(tmp_path)
    try:
        bundle, _ = s.register(source(tmp_path, 'controller.py'))
        arguments = {'source': 'code/ctl'} if name == 'register' else {'bundle': bundle}
        reply = frontend.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': name, 'arguments': arguments}})
        assert reply['result']['isError']
        assert not s.state.get('submission') and not s.state['formal_reserved']
        with pytest.raises(RuntimeError, match='no bundle submission'):
            s.submit(bundle)
    finally:
        frontend.close()


def test_live_copy_status_has_no_bundle_fields(tmp_path):
    s, frontend = frontend_for(tmp_path)
    try:
        status = frontend.status()
        assert not {'bundles', 'submitted_bundle', 'formal_episode_count', 'formal_reserved',
                    'formal_api_budget'} & set(status)
        assert set(status['carry']) >= {'carried_folders', 'carried_bytes', 'carried_limit_bytes', 'conversation'}
    finally:
        frontend.close()


def test_live_copy_exploration_closes_when_its_episodes_are_spent(tmp_path):
    s, frontend = frontend_for(tmp_path)
    try:
        frontend.call('start_episode', {})
        assert 'next_step' not in frontend.status()
        frontend.call('finish', {})
        status = frontend.status()
        assert 'No exploration episode remains' in status['next_step'] and 'end your turn' in status['next_step']
        for word in ('bundle', 'formal', 'held-out', 'score', 'evaluat'):
            assert word not in status['next_step']
        assert checkpoint.exploration_closed(s.config)
        reply = frontend.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': 'start_episode', 'arguments': {}}})
        assert reply['result']['isError'] and 'call status' in reply['result']['content'][0]['text']
    finally:
        frontend.close()


def test_live_copy_runs_only_agent_formal_after_the_freeze(tmp_path, monkeypatch):
    s, frontend = frontend_for(tmp_path)
    frontend.call('start_episode', {})
    frontend.call('finish', {})
    s.state['submission'] = {'bundle': 'legacy'}  # Even a stray submission runs no bundle formal.
    s._save()
    frontend.close()
    calls = []
    monkeypatch.setattr(checkpoint, 'freeze_exploration', lambda *a: calls.append('freeze') or 'frozen')
    monkeypatch.setattr(checkpoint, 'agent_formal', lambda *a: calls.append('agent') or {'status': 'completed'})
    monkeypatch.setattr('services.controller.supervisor.Supervisor.run_submission',
                        lambda self: calls.append('bundle'))
    monkeypatch.setattr('services.controller.backend.make_backend', lambda config: Backend())
    summary = checkpoint.after_exploration(s.config, {})
    assert calls == ['freeze', 'agent'] and 'bundle_formal' not in summary
    assert summary['status'] == 'completed' and summary['agent_formal'] == {'status': 'completed'}


def test_formal_worker_import_always_needs_a_qualifying_rehearsal(tmp_path):
    original = Supervisor(config(tmp_path/'original', formal_episodes=1), Backend, runner_factory=Runner)
    batch = Supervisor(config(tmp_path/'batch', formal_episodes=1), Backend, runner_factory=Runner)
    try:
        bundle, _ = original.register(source(tmp_path, 'controller.py'))
        with pytest.raises(ValueError, match='no qualifying'):
            import_rehearsed_bundle(batch, original.config.root, bundle)
    finally:
        for s in (original, batch):
            s.close()
