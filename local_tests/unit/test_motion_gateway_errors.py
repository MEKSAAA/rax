"""Errors carry their full reason and never block the episode; only a simulator exit ends it."""
import json

import pytest

from services.controller.gateway import Gateway
from services.controller.errors import public_failure
from services.simulator.io import SimulatorError, SimulatorExited
from bench import OBSERVE, STATUS, STEP


class Backend:
    def __init__(self):
        self.calls = []
        self.error = None

    def call(self, name, arguments):
        self.calls.append(name)
        if self.error:
            raise self.error
        return {'step_id': len(self.calls)}, []


def gateway(**kwargs):
    backend = Backend()
    g = Gateway(backend, None, audit=lambda _: None, charge=lambda *_: None,
                refund=lambda *_: None, budget=lambda: {}, **kwargs)
    return g, g.acquire(), backend


def call(g, token, name, **args):
    return g.handle(token, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': name, 'arguments': args}})


@pytest.mark.parametrize('stage', ['execution', 'publication', 'recording'])
def test_post_dispatch_failure_reports_its_reason_and_keeps_the_episode(stage):
    g, token, backend = gateway()
    def fail(_):
        raise RuntimeError('recording quota exhausted')
    if stage == 'execution':
        backend.error = SimulatorError('Traceback ...\nValueError: IK diverged at row 3', {'episode_id': 'e', 'step_id': 7})
    elif stage == 'publication':
        g.publish_sequence = fail
    else:
        g.audit = lambda event: fail(event) if event['event'] == 'mcp_result' else None
    failure = call(g, token, STEP, actions=[[0]*14])['error']['data']
    assert 'IK diverged at row 3' in failure['reason'] if stage == 'execution' else failure['reason'] == 'recording quota exhausted'
    assert set(failure) >= {'type', 'operation', 'stage', 'reason', 'episode_ended'}
    assert not {'control_uncertain', 'no_action_executed', 'recovery'} & set(failure)
    if stage == 'execution':
        assert failure['simulator_state'] == {'episode_id': 'e', 'step_id': 7}
    assert not g.terminal
    backend.error = None
    g.publish_sequence = lambda _: None
    g.audit = lambda _: None
    assert 'result' in call(g, token, STEP, actions=[[0]*14])
    assert len(backend.calls) == 2


def test_simulator_exit_ends_the_episode_with_its_exit_report():
    g, token, backend = gateway()
    backend.error = SimulatorExited('step failed with EOFError: closed; simulator process exited with code -11; '
                                    'last log errors: CUDA error: an illegal memory access was encountered')
    failure = call(g, token, STEP, actions=[[0]*14])['error']['data']
    assert 'code -11' in failure['reason'] and 'illegal memory access' in failure['reason']
    assert failure['episode_ended'] and g.terminal and g.simulator_exited
    blocked = call(g, token, STEP, actions=[[0]*14])['error']['message']
    assert 'code -11' in blocked and len(backend.calls) == 1
    blocked = call(g, token, OBSERVE)['error']['message']
    assert 'code -11' in blocked and len(backend.calls) == 1


def test_audit_failure_before_execution_returns_reason():
    g, token, backend = gateway()
    def fail(_):
        raise RuntimeError('audit quota exhausted')
    g.audit = fail
    error = call(g, token, STEP, actions=[[0]*14])['error']['data']
    assert error['audit_recording_failed'] and not error['episode_ended']
    assert error['reason'] == 'audit quota exhausted' and backend.calls == []


def test_provider_errors_and_host_paths_do_not_leak():
    error = RuntimeError('API_KEY=private-key Bearer private-token /private/key https://private.example sk-secret')
    for stage in ('provider_request', 'validation'):
        public = json.dumps(public_failure(error, stage=stage))
        for secret in ('private-key', 'private-token', '/private/', 'private.example', 'sk-secret'):
            assert secret not in public


def test_tracebacks_keep_file_names_and_the_innermost_frames():
    frames = ''.join(f'  File "/home/user/repo/services/simulator/m{i}.py", line {i}, in f\n' for i in range(400))
    reason = public_failure(RuntimeError('Traceback (most recent call last):\n' + frames + 'ValueError: real cause'),
                            stage='native_execution')['reason']
    assert reason.endswith('ValueError: real cause') and 'm399.py", line 399' in reason
    assert '/home/user' not in reason and 'Traceback' in reason


def test_step_limit_and_episode_end_survive_private_field_removal():
    g, token, backend = gateway()
    replies = iter([
        {'step_id': 590, 'metadata': {'episode_step_limit': 600, 'seed': 3},
         'environment_spec': {'episode_step_limit': 600, 'layout_id': 'private'}},
        {'step_id': 600, 'transition': {'steps': [{'episode_ended': True}]}},
        {'active': False, 'step_id': 600, 'environment_spec': {'episode_step_limit': 600}},
    ])
    backend.call = lambda name, arguments: (next(replies), [])
    first = json.loads(call(g, token, STATUS)['result']['content'][0]['text'])
    assert first['episode_limits'] == {'episode_step_limit': 600, 'remaining_episode_steps': 10}
    assert first['episode_ended'] is False
    assert 'metadata' not in first and 'environment_spec' not in first and 'private' not in json.dumps(first)
    call(g, token, STEP, actions=[[0]*14])
    status = json.loads(call(g, token, STATUS)['result']['content'][0]['text'])
    assert status['episode_ended'] is True and status['episode_limits']['remaining_episode_steps'] == 0
    assert 'control_uncertain' not in status
