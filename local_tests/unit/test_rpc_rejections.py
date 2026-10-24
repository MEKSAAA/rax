"""Simulator errors come back whole, with the episode state, over a connection that stays usable."""
from types import SimpleNamespace

import pytest

from services.simulator import protocol, rpc
from services.simulator.io import PartialExecutionError, RejectedBeforeAction, SimulatorError
from bench import STEP


class Socket:
    def __init__(self): self.closed = False
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def setsockopt(self, *_): pass
    def settimeout(self, *_): pass
    def bind(self, *_): pass
    def listen(self, *_): pass
    def getsockname(self): return ("127.0.0.1", 1)
    def accept(self): return self, None
    def close(self): self.closed = True


@pytest.mark.parametrize("kind", ["rejection", "partial", "runtime"])
def test_server_reports_every_error_and_keeps_serving(monkeypatch, kind):
    monkeypatch.setattr(rpc.socket, "socket", lambda *_: Socket())
    replies, reasons = [], []
    session = SimpleNamespace(metadata={}, terminated=False, truncated=False, episode_id="e", step_id=3,
                              _write_summary=reasons.append)
    def dispatch(op, args):
        if op == "chunk_step":
            if kind == "runtime":
                return {"q": [1][2]}
            raise (PartialExecutionError("one row executed") if kind == "partial" else RejectedBeforeAction("bad target"))
        return {"step_id": 0}
    session.dispatch = dispatch
    requests = iter(["chunk_step", "observe"])
    def receive(_):
        op = next(requests, None)
        if op is None: raise EOFError()
        return {"version": protocol.VERSION, "request_id": op, "op": op}
    monkeypatch.setattr(rpc, "receive_packet", receive)
    monkeypatch.setattr(rpc, "send_packet", lambda _, reply: replies.append(reply))
    rpc.serve(session, 0)
    assert replies[0]["error_type"] == {"partial": "PartialExecutionError", "rejection": "RejectedBeforeAction",
                                        "runtime": "SimulatorError"}[kind]
    assert not replies[0]["ok"] and replies[1]["ok"]
    if kind == "runtime":
        # The whole error: the exception and the frame that raised it.
        assert "IndexError: list index out of range" in replies[0]["error"]
        assert "return {\"q\": [1][2]}" in replies[0]["error"]
    if kind != "rejection":
        assert replies[0]["state"] == {"episode_id": "e", "step_id": 3}
    assert not hasattr(session, "invalid_reason")


@pytest.mark.parametrize("failure", ["rejection", "partial", "runtime", "untyped_message", "identity", "timeout"])
def test_rpc_preserves_only_explicit_identified_before_action_responses(monkeypatch, failure):
    client = protocol.RPCClient.__new__(protocol.RPCClient)
    sock = client.sock = Socket()
    requests = []
    monkeypatch.setattr(protocol, "send_packet", lambda _, request: requests.append(request))
    def receive(_):
        if failure == "timeout": raise TimeoutError("timeout")
        response = {"version": protocol.VERSION, "request_id": requests[-1]["request_id"],
                    "ok": False, "error": "RejectedBeforeAction: bad target"}
        if failure in ("rejection", "identity"): response["error_type"] = "RejectedBeforeAction"
        if failure == "partial": response["error_type"] = "PartialExecutionError"
        if failure == "identity": response["request_id"] = "another request"
        if len(requests) > 1: response.update(ok=True, result={"step_id": 0})
        return response
    monkeypatch.setattr(protocol, "receive_packet", receive)
    expected = {"rejection": RejectedBeforeAction, "identity": ValueError,
                "timeout": TimeoutError, "partial": PartialExecutionError}.get(failure, SimulatorError)
    with pytest.raises(expected, match="timeout" if failure == "timeout" else "bad target|mismatch"):
        client.request("chunk_step")
    if failure in ("identity", "timeout"):
        assert client.sock is None and sock.closed
    else:
        # The simulator answered: whatever the error, the connection stays usable.
        assert client.sock is sock and not sock.closed
        assert client.request("observe") == {"step_id": 0}


def test_publication_failure_after_motion_reports_its_stage_and_reason():
    from local_tests.unit.test_motion_gateway_errors import call, gateway
    g, token, _ = gateway()
    def fail(_):
        raise RejectedBeforeAction("publication failed after physics")
    g.publish_sequence = fail
    failure = call(g, token, STEP, actions=[[0] * 14])["error"]["data"]
    assert failure["stage"] == "artifact_publication" and failure["reason"] == "publication failed after physics"
    assert not g.terminal


def test_lost_simulator_reports_exit_code_and_only_this_requests_log_errors(tmp_path):
    from services.simulator.io import SimulatorExited, simulator_request
    log = tmp_path / "sim.log"
    log.write_text("Traceback (most recent call last):\nKeyError: 'earlier, already reported'\n")
    class Client:
        calls = 0
        def request(self, op, **_):
            Client.calls += 1
            if Client.calls == 1:
                return {"step_id": 4}  # The earlier error was already answered.
            with log.open("a") as stream:
                stream.write("[py stderr]: \x1b[33mUserWarning: noise\x1b[0m\n"
                             "[py stderr]: CUDA error: an illegal memory access was encountered\n"
                             "Fatal Python error: Segmentation fault\n")
            raise EOFError("RPC connection closed")
    owner = SimpleNamespace(rpc=Client(), log_path=log, episode_id="e", step_id=4, finished=False,
                            process=SimpleNamespace(wait=lambda timeout: -11, poll=lambda: -11))
    simulator_request(owner, "observe")
    with pytest.raises(SimulatorExited) as caught:
        simulator_request(owner, "chunk_step")
    assert str(caught.value) == ("chunk_step failed with EOFError: RPC connection closed; simulator process "
                                 "exited with code -11; its log: CUDA error: an illegal memory access was "
                                 "encountered | Fatal Python error: Segmentation fault")
    assert owner.finished


def test_simulator_error_resyncs_the_step_and_keeps_the_episode():
    from services.simulator.io import simulator_request
    class Client:
        def request(self, op, **_):
            raise SimulatorError("KeyError: 'position'", {"episode_id": "e", "step_id": 9})
    owner = SimpleNamespace(rpc=Client(), log_path=None, episode_id="e", step_id=4, finished=False, process=None)
    with pytest.raises(SimulatorError, match="position"):
        simulator_request(owner, "eef_move")
    assert owner.step_id == 9 and not owner.finished


def test_wall_timeout_close_names_the_event():
    from services.simulator.io import SimulatorExited, simulator_request
    import tempfile, pathlib
    log = pathlib.Path(tempfile.mkdtemp()) / "sim.log"
    log.write_text("")
    class Client:
        def request(self, op, **_):
            log.write_text('!!!!\n{"event": "episode_wall_timeout", "timeout_seconds": 10701}\n')
            raise EOFError("RPC connection closed")
    owner = SimpleNamespace(rpc=Client(), log_path=log, episode_id="e", step_id=4, finished=False,
                            process=SimpleNamespace(wait=lambda timeout: 0, poll=lambda: 0))
    with pytest.raises(SimulatorExited, match='exited with code 0; its log: {"event": "episode_wall_timeout", '
                                              '"timeout_seconds": 10701}$'):
        simulator_request(owner, "observe")
