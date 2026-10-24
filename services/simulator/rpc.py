"""Single-owner blocking RPC server for a simulator session."""

from __future__ import annotations

import json
import signal
import socket
import traceback

from .io import PartialExecutionError, RejectedBeforeAction
from .protocol import VERSION, receive_packet, send_packet


class EpisodeWallTimeout(BaseException):
    """Escape action handlers so an expired episode cannot accept further calls."""


# The deadline alarm repeats until handled: Python discards an exception raised while it
# runs a gc callback or __del__, which once left an episode running hours past its deadline.
RETRY_SECONDS = 1.0


def error_text(exc, frames=6):
    """The exception with the last frames of its traceback (innermost last)."""
    # The three-argument form: simulator interpreters go back to Python 3.8 (LIBERO-PRO).
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return "".join(lines[-(frames + 1):]).strip()


def serve(session, port, *, episode_timeout_seconds=0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(port)))
        listener.listen(1)
        bound_port = int(listener.getsockname()[1])
        print(
            json.dumps(
                {"event": "ready", "port": bound_port, "metadata": session.metadata}
            ),
            flush=True,
        )
        connection, _ = listener.accept()
        with connection:
            # Idle reasoning must not hit the old 15-minute socket timeout before
            # the episode deadline. Managed runs have their own host watchdog.
            connection.settimeout(None)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            previous_handler = None
            def expire(*_):
                raise EpisodeWallTimeout()
            try:
                while True:
                    request = receive_packet(connection)
                    response = {
                        "version": VERSION,
                        "request_id": request.get("request_id"),
                    }
                    try:
                        if request.get("version") != VERSION:
                            raise ValueError("Unsupported protocol version")
                        result = session.dispatch(
                            request["op"], request.get("args", {})
                        )
                        if request['op'] == 'reset' and episode_timeout_seconds:
                            previous_handler = signal.signal(signal.SIGALRM, expire)
                            signal.setitimer(signal.ITIMER_REAL, episode_timeout_seconds, RETRY_SECONDS)
                        response.update(ok=True, result=result)
                    except RejectedBeforeAction as exc:
                        response.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                                        error_type="RejectedBeforeAction")
                    except Exception as exc:  # noqa: BLE001 - RPC must report simulator errors
                        # Report the whole error and where the episode now stands; the
                        # episode continues from the simulator's actual state.
                        traceback.print_exc()
                        response.update(ok=False, error=error_text(exc),
                                        error_type=("PartialExecutionError"
                                                    if isinstance(exc, PartialExecutionError) else "SimulatorError"),
                                        state={"episode_id": getattr(session, "episode_id", None),
                                               "step_id": getattr(session, "step_id", None)})
                    try:
                        send_packet(connection, response)
                    except ValueError as exc:  # Too large: raised before any byte is written.
                        if not response.get("ok"):
                            raise
                        # The call ran; report that and where the episode stands instead of
                        # dropping the connection, which would end the episode.
                        send_packet(connection, {
                            "version": VERSION, "request_id": response["request_id"], "ok": False,
                            "error": f"SimulatorError: {request.get('op')} ran, but its reply could not be "
                                     f"sent ({exc}); observe to see the current state",
                            "error_type": "SimulatorError",
                            "state": {"episode_id": getattr(session, "episode_id", None),
                                      "step_id": getattr(session, "step_id", None)}})
            except EpisodeWallTimeout:
                signal.setitimer(signal.ITIMER_REAL, 0)  # No repeat may interrupt the summary below.
                # Preserve finalized native outcomes; an unfinished timed-out
                # attempt is not eligible for success. Closing the socket then
                # returning lets server.main close the simulator in its finally.
                if not (session.terminated or session.truncated):
                    session.invalid_reason = "episode_wall_timeout"
                    session.truncated = True
                session.finish_reason = 'episode_wall_timeout'
                session._write_summary('episode_wall_timeout')
                print(json.dumps({'event': 'episode_wall_timeout',
                                  'timeout_seconds': episode_timeout_seconds}), flush=True)
            except (EOFError, ConnectionError, TimeoutError):
                reason = (
                    "terminal"
                    if session.terminated or session.truncated
                    else "controller_disconnect"
                )
                session.disconnect_reason = reason  # Kept by the server's closing summary.
                session._write_summary(reason)
            finally:
                if previous_handler is not None:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous_handler)
