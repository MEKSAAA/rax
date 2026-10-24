"""No paid API: session-wide admission, reconciliation and crash-safe spending."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import base64
import io
import json
import threading

import pytest
from PIL import Image

from services.controller.billing import NANODOLLARS, SESSION_LIMIT, nanodollars, reserve_cost
from services.controller.gemini import GeminiRouter, MODEL
from services.controller.frontend import ResearchFrontend
from services.controller.config import Configuration
from services.controller.gateway import Gateway
from services.controller.supervisor import Supervisor
from test_controller_backend import Backend, configuration, packet, value
from bench import OBSERVE


def response(cost=.001):
    return {'model': MODEL, 'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
            'usage': {'total_tokens': 10, 'cost': cost}}


def frontend(tmp_path, send):
    s = Supervisor(configuration(tmp_path), Backend, GeminiRouter('host-key', send=send))
    return ResearchFrontend(s, tmp_path/'workspace')


def call(front):
    return front.call('gemini_generate', {'messages': [{'role': 'user', 'content': 'Hello'}], 'max_tokens': 128})


def failure(front, stage):
    reply = call(front)
    assert reply['isError'] is True
    error = json.loads(reply['content'][0]['text'])['error']
    assert error['data']['stage'] == stage and error['message']
    assert not error['data']['episode_ended']
    return error


def test_admission_prevents_crossing_cap_before_provider_io(tmp_path):
    calls = []
    f = frontend(tmp_path, lambda payload: calls.append(payload) or response())
    try:
        f.s.charge(100, SESSION_LIMIT)
        f.s.refund(90, {'total_tokens': 10, 'cost': 9.999}, SESSION_LIMIT)
        before = deepcopy(f.s.state)
        failure(f, 'api_budget')
        assert not calls and f.s.state['usage'] == before['usage']
        assert f.s.budget()['session_cost_remaining_usd'] == .001
        # Dollar cutoff leaves independent robot control available.
        f.call('start_episode', {})
        assert f.call(OBSERVE, {})['content']
    finally:
        f.close()


def test_shared_across_no_episode_exploration_resets_and_formal(tmp_path):
    f = frontend(tmp_path, lambda _: response())
    try:
        call(f)  # API-only development, before any environment.
        f.call('start_episode', {})
        call(f)
        f.call('start_episode', {})  # Reset cannot refund prior spending.
        call(f)
        f.call('finish', {})
        f.s.phase = 'formal'
        result = json.loads(call(f)['content'][0]['text'])
        assert result['budget']['calls_used'] == 1  # Phase counters are separate.
        assert result['budget']['session_cost_reported_usd'] == .004
        assert result['budget']['session_cost_remaining_usd'] == 9.996
        assert result['budget']['session_cost_reserved_usd'] == 0
        assert f.status()['api_budget']['session_cost_limit_usd'] == 10
    finally:
        f.close()


@pytest.mark.parametrize('cost', [None, False, -1, '0.001', float('nan'), float('inf')])
def test_untrusted_cost_retains_dollar_reservation(tmp_path, cost):
    f = frontend(tmp_path, lambda _: response(cost))
    try:
        call(f)  # Non-finite values return an MCP error; the reservation remains.
        budget = f.s.budget()
        assert budget['session_cost_reported_usd'] == 0
        assert budget['session_cost_reserved_usd'] > 0
        assert budget['session_cost_remaining_usd'] < 10
        assert budget['tokens_reported'] == 10
    finally:
        f.close()


def test_timeout_reservation_survives_restart(tmp_path):
    def fail(_):
        raise TimeoutError('unknown billing outcome')
    f = frontend(tmp_path, fail)
    assert failure(f, 'provider_request')['message'] == (
        'Provider request failed (TimeoutError); the provider may have billed it, '
        'so its billing reservation is retained.')
    before = f.s.budget()
    assert before['unresolved_reserved_tokens'] > 0 and before['session_cost_reserved_usd'] > 0
    f.close()
    restored = Supervisor(configuration(tmp_path), Backend)
    try:
        assert restored.budget() == before
        restored.phase = 'formal'
        assert restored.budget()['session_cost_remaining_usd'] == before['session_cost_remaining_usd']
    finally:
        restored.close()


def test_reported_cost_bound_violation_closes_gemini(tmp_path):
    calls = []
    f = frontend(tmp_path, lambda payload: calls.append(payload) or response(1))
    try:
        failure(f, 'api_accounting')
        assert f.s.budget()['session_cost_reported_usd'] == 1
        assert f.s.budget()['session_cost_blocked'] is True
        failure(f, 'api_budget')
        assert len(calls) == 1
    finally:
        f.close()


def test_concurrent_reservations_cannot_oversubscribe(tmp_path):
    s = Supervisor(configuration(tmp_path), Backend)
    try:
        def reserve(_):
            try:
                s.charge(1, SESSION_LIMIT//4)
                return True
            except RuntimeError:
                return False
        with ThreadPoolExecutor(8) as pool:
            assert sum(pool.map(reserve, range(8))) == 4
        assert s.budget()['session_cost_reserved_usd'] == 10
        assert s.budget()['session_cost_remaining_usd'] == 0
    finally:
        s.close()


def test_parallel_image_calls_have_no_phase_token_cap(tmp_path):
    """Eight pending images exceed the old 1M-token cap, but fit under $10."""
    barrier = threading.Barrier(8)
    def send(_):
        barrier.wait(timeout=10)
        return response()
    router = GeminiRouter('host-key', send=send)
    s = Supervisor(configuration(tmp_path), Backend, router)
    s.phase = 'formal'
    png = io.BytesIO()
    Image.new('RGB', (725, 190)).save(png, format='PNG')
    arguments = {'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Compare these tiles.'},
        {'type': 'image', 'mimeType': 'image/png',
         'data': base64.b64encode(png.getvalue()).decode('ascii')}]}], 'max_tokens': 2048}
    try:
        def request(_):
            # Separate workers share only the host billing ledger.
            gateway = Gateway(Backend(), router, audit=lambda _: None,
                              charge=s.charge, refund=s.refund, budget=s.budget)
            return value(gateway.handle(gateway.acquire(), packet('gemini_generate', **arguments)))
        with ThreadPoolExecutor(8) as pool:
            replies = list(pool.map(request, range(8)))
        assert all(r['message']['content'] == 'OK' for r in replies)
        budget = s.budget()
        assert budget['calls_used'] == 8 and budget['tokens_reported'] == 80
        assert budget['session_cost_reported_usd'] == .008
        assert budget['session_cost_reserved_usd'] == 0
        assert 'tokens_remaining' not in budget and 'calls_remaining' not in budget
    finally:
        s.close()


def test_usage_can_exceed_legacy_call_and_token_caps(tmp_path):
    s = Supervisor(configuration(tmp_path), Backend)
    try:
        for _ in range(101):
            s.charge(20_000, nanodollars(.02))
            s.refund(0, {'total_tokens': 20_000, 'cost': .01}, nanodollars(.02))
        assert s.budget()['calls_used'] == 101
        assert s.budget()['tokens_reported'] == 2_020_000
        assert s.budget()['session_cost_remaining_usd'] == 8.99
    finally:
        s.close()


def test_legacy_phase_caps_ignored_without_resetting_dollar_ledger(tmp_path):
    config = configuration(tmp_path)
    s = Supervisor(config, Backend)
    s.charge(586_338, nanodollars(.45))
    before = s.budget()
    path = s.state_path
    saved = deepcopy(s.state)
    s.close()
    for phase in ('development', 'formal', 'training'):
        saved['configuration'][phase].update(gemini_calls=0, gemini_tokens=0)
    path.write_text(json.dumps(saved))
    raw = deepcopy(saved['configuration'])
    restored_config = Configuration.from_dict(raw)
    assert 'gemini_tokens' not in asdict(restored_config.development)
    object.__setattr__(restored_config, 'root', config.root)
    restored = Supervisor(restored_config, Backend)
    try:
        assert restored.budget() == before
        restored.charge(586_338, nanodollars(.45))
        assert restored.budget()['session_cost_reserved_usd'] == .9
        assert restored.budget()['calls_used'] == 2
    finally:
        restored.close()


@pytest.mark.parametrize('prior_calls', [0, 1])
def test_legacy_state_does_not_get_free_allowance(tmp_path, prior_calls):
    s = Supervisor(configuration(tmp_path), Backend)
    path = s.state_path
    state = deepcopy(s.state)
    s.close()
    del state['gemini_spend']
    state['schema'] = 1
    state['usage']['development']['calls'] = prior_calls
    path.write_text(json.dumps(state))
    restored = Supervisor(configuration(tmp_path), Backend)
    try:
        assert restored.state['schema'] == 2
        assert restored.budget()['session_cost_blocked'] is bool(prior_calls)
        assert restored.budget()['session_cost_remaining_usd'] == (0 if prior_calls else 10)
    finally:
        restored.close()


def test_exact_decimal_arithmetic_and_maximum_price_bound():
    assert nanodollars(.0000000001) == 1  # Always round upward, never undercharge.
    assert nanodollars(.1)+nanodollars(.2) == nanodollars(.3)
    assert SESSION_LIMIT == nanodollars(10)
    assert reserve_cost(1_001_000, 1000) == 750_000_000+3_750_000+6000


def test_reported_and_pending_cost_share_the_same_cap(tmp_path):
    s = Supervisor(configuration(tmp_path), Backend)
    try:
        s.charge(10, 6*NANODOLLARS)
        s.refund(0, {'total_tokens': 10, 'cost': 4}, 6*NANODOLLARS)
        s.charge(10, 6*NANODOLLARS)  # Pending request exactly fills remaining balance.
        with pytest.raises(RuntimeError):
            s.charge(1, 1)
        assert s.budget()['session_cost_reported_usd'] == 4
        assert s.budget()['session_cost_reserved_usd'] == 6
    finally:
        s.close()


def test_trial_saved_before_agent_formal_fields_reopens(tmp_path):
    config = configuration(tmp_path)
    s = Supervisor(config, Backend)
    path, saved = s.state_path, deepcopy(s.state)
    s.close()
    for key in ('agent_formal', 'formal_context', 'transfer_parts', 'transfer_mb'):
        saved['configuration'].pop(key)
    path.write_text(json.dumps(saved))
    Supervisor(config, Backend).close()  # Default-valued fields are what the old trial ran with.
    changed = deepcopy(config)
    object.__setattr__(changed, 'transfer_mb', 50)  # A real change is still refused.
    with pytest.raises(ValueError, match='cannot change'):
        Supervisor(changed, Backend)


def test_trial_saved_under_an_older_wall_limit_resumes_under_the_current_one(tmp_path):
    from dataclasses import replace
    config = configuration(tmp_path)
    s = Supervisor(config, Backend)
    s.close()
    # The launcher re-derives the limits from the task.
    resumed = deepcopy(config)
    for phase in ('development', 'formal'):
        object.__setattr__(resumed, phase, replace(getattr(config, phase), wall_seconds=None))
    s = Supervisor(resumed, Backend)
    try:
        saved = json.loads(s.state_path.read_text())['configuration']
        assert saved['formal']['wall_seconds'] is None and saved['development']['wall_seconds'] is None
        assert s.config.formal.wall_seconds is None
    finally:
        s.close()
    changed = deepcopy(resumed)
    object.__setattr__(changed, 'formal', replace(resumed.formal, memory_mb=1024))  # Other limits still cannot change.
    with pytest.raises(ValueError, match='cannot change'):
        Supervisor(changed, Backend)


def test_trial_saved_with_a_formal_call_cap_reopens(tmp_path):
    config = configuration(tmp_path)
    s = Supervisor(config, Backend)
    path, saved = s.state_path, deepcopy(s.state)
    s.close()
    saved['configuration']['agent_formal_max_model_calls'] = 2000  # Live copies' former cap.
    path.write_text(json.dumps(saved))
    raw = deepcopy(saved['configuration'])
    assert not hasattr(Configuration.from_dict(raw), 'agent_formal_max_model_calls')
    Supervisor(config, Backend).close()


def test_trial_saved_with_retired_mode_fields_reopens(tmp_path):
    config = configuration(tmp_path)
    s = Supervisor(config, Backend)
    path, saved = s.state_path, deepcopy(s.state)
    s.close()
    # Configurations saved while other experiment modes existed carry these fields.
    saved['configuration'].update(mode='auto-research', student_model='openai/other', student_reasoning_effort='high',
                                  student_max_model_calls=10, teacher_source_session=None)
    path.write_text(json.dumps(saved))
    raw = deepcopy(saved['configuration'])
    restored = Configuration.from_dict(raw)
    assert not hasattr(restored, 'mode') and not hasattr(restored, 'student_model')
    object.__setattr__(restored, 'root', config.root)
    Supervisor(restored, Backend).close()
    with pytest.raises(ValueError, match="Experiment mode 'teacher-student' was removed"):
        Configuration.from_dict({**raw, 'mode': 'teacher-student'})
    saved['configuration']['mode'] = 'direct-control'
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='was removed'):
        Supervisor(config, Backend)


def test_an_http_error_releases_its_reservation_and_keeps_the_providers_message(tmp_path):
    """An error status carries no completion: nothing was billed, the reservation is released,
    the agent reads the status and the provider's raw body stays in the operator's private log."""
    import urllib.error
    body = b'{"error":{"message":"No endpoints available for google/gemini-3.8-flash under max_price","code":503}}'
    def unavailable(_):
        raise urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions', 503, 'Service Unavailable',
                                     {}, io.BytesIO(body))
    f = frontend(tmp_path, unavailable)
    try:
        error = failure(f, 'provider_request')
        assert error['message'] == ('Provider returned HTTP 503 (Service Unavailable); it produced no completion, '
                                    'so its billing reservation was released.')
        budget = f.s.budget()
        assert budget['calls_used'] == 1 and budget['session_cost_reserved_usd'] == 0
        assert budget['unresolved_reserved_tokens'] == 0 and budget['session_cost_remaining_usd'] == 10
        [record] = [json.loads(line) for line in (f.s.config.root/'provider-errors.jsonl').read_text().splitlines()]
        assert (record['type'], record['status'], record['body']) == ('HTTPError', 503, body.decode())
        assert (f.s.config.root/'provider-errors.jsonl').stat().st_mode & 0o777 == 0o600
    finally:
        f.close()


def test_a_checked_provider_answer_keeps_its_own_message_and_reservation(tmp_path):
    f = frontend(tmp_path, lambda _: {**response(), 'model': MODEL + '-preview'})
    try:
        assert failure(f, 'provider_request')['message'] == 'Provider returned an unexpected model'
        assert f.s.budget()['session_cost_reserved_usd'] > 0  # A completion was produced and may be billed.
    finally:
        f.close()


def test_image_reservations_bound_the_bill_without_locking_the_budget():
    """About 1,100 tokens are billed per image; the reservation keeps a wide margin but a
    single-image call no longer reserves about a dollar."""
    from services.controller.gemini import image_token_bound
    assert image_token_bound(640, 480) == image_token_bound(1280, 720) == 4096
    assert image_token_bound(4096, 4096) == 516 * 36
    buffer = io.BytesIO()
    Image.new('RGB', (640, 480), (40, 90, 160)).save(buffer, format='PNG')
    image = {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode()}
    router = GeminiRouter('host-key', send=lambda _: response())
    payload, tokens = router.prepare({'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Cup?'}, image]}],
                                      'max_tokens': 512})
    assert tokens >= 4096 + 512 + 32768 and reserve_cost(tokens, 512) / NANODOLLARS < 0.05
