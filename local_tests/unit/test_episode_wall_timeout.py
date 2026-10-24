"""Episode wall deadlines end the simulator server, not merely the current RPC."""
import gc
import sys
import time
from types import SimpleNamespace

import pytest

from services.simulator import rpc


@pytest.mark.parametrize('phase', ['idle', 'action', 'completed'])
def test_episode_deadline_closes_rpc_and_preserves_finalized_outcome(monkeypatch, phase):
    handlers, timers, replies, reasons = [], [], [], []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def setsockopt(self, *_): pass
        def settimeout(self, value): assert value is None
        def bind(self, *_): pass
        def listen(self, *_): pass
        def getsockname(self): return ('127.0.0.1', 1)
        def accept(self): return self, None
    monkeypatch.setattr(rpc.socket, 'socket', lambda *_: Connection())
    def install(sig, handler):
        handlers.append(handler)
        return 'original-handler'
    monkeypatch.setattr(rpc.signal, 'signal', install)
    monkeypatch.setattr(rpc.signal, 'setitimer', lambda *args: timers.append(args))
    session = SimpleNamespace(metadata={}, terminated=phase=='completed', truncated=False,
                              invalid_reason=None, finish_reason=None, _write_summary=reasons.append)
    def dispatch(op, args):
        if op == 'step': handlers[0]()
        return {}
    session.dispatch = dispatch
    requests = iter(['reset', 'step'])
    def receive(_):
        op = next(requests)
        if op == 'step' and phase != 'action': handlers[0]()
        return {'version':rpc.VERSION,'request_id':op,'op':op}
    monkeypatch.setattr(rpc, 'receive_packet', receive)
    monkeypatch.setattr(rpc, 'send_packet', lambda conn, value: replies.append(value))
    rpc.serve(session, 0, episode_timeout_seconds=2400)
    assert len(replies) == 1 and replies[0]['ok']
    assert reasons == ['episode_wall_timeout']
    assert session.invalid_reason == (None if phase == 'completed' else 'episode_wall_timeout')
    assert session.finish_reason == 'episode_wall_timeout'
    assert timers == [(rpc.signal.ITIMER_REAL,2400,rpc.RETRY_SECONDS),(rpc.signal.ITIMER_REAL,0),
                      (rpc.signal.ITIMER_REAL,0)]
    assert handlers[-1] == 'original-handler'


def test_deadline_discarded_inside_a_gc_callback_still_ends_the_episode(monkeypatch):
    """Episode 14 of getting_organized 6a93071f36 ran hours past its deadline because the
    one-shot alarm fired inside isaacsim's gc callback, where Python ignores exceptions."""
    replies, reasons, ignored, inside = [], [], [], []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def setsockopt(self, *_): pass
        def settimeout(self, value): pass
        def bind(self, *_): pass
        def listen(self, *_): pass
        def getsockname(self): return ('127.0.0.1', 1)
        def accept(self): return self, None
    monkeypatch.setattr(rpc.socket, 'socket', lambda *_: Connection())
    monkeypatch.setattr(rpc, 'RETRY_SECONDS', 0.05)
    monkeypatch.setattr(sys, 'unraisablehook', lambda value: ignored.append(value.exc_type))
    def callback(phase, info):  # Holds the interpreter in the callback until the alarm has fired there.
        if phase == 'start' and not inside:
            inside.append(True)
            deadline = time.monotonic() + 5
            while rpc.EpisodeWallTimeout not in ignored and time.monotonic() < deadline:
                pass
    session = SimpleNamespace(metadata={}, terminated=False, truncated=False,
                              invalid_reason=None, finish_reason=None, _write_summary=reasons.append)
    def dispatch(op, args):
        if op == 'step':
            gc.collect()  # Earlier tests' garbage, whose finalizers may report their own errors.
            gc.callbacks.append(callback)
            try:
                gc.collect()
            finally:
                gc.callbacks.remove(callback)
            assert rpc.EpisodeWallTimeout in ignored
            deadline = time.monotonic() + 5  # Further simulation, where the repeated alarm lands.
            while time.monotonic() < deadline:
                time.sleep(0.01)
        return {}
    session.dispatch = dispatch
    requests = iter(['reset', 'step'])
    def receive(_):
        try:
            return {'version': rpc.VERSION, 'request_id': 1, 'op': next(requests)}
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr(rpc, 'receive_packet', receive)
    monkeypatch.setattr(rpc, 'send_packet', lambda conn, value: replies.append(value))
    started = time.monotonic()
    rpc.serve(session, 0, episode_timeout_seconds=0.1)
    assert time.monotonic() - started < 2
    assert len(replies) == 1 and replies[0]['ok']
    assert reasons == ['episode_wall_timeout'] and session.invalid_reason == 'episode_wall_timeout'
    assert rpc.signal.getitimer(rpc.signal.ITIMER_REAL) == (0.0, 0.0)
