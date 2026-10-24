"""Small, dependency-free persistence helpers for simulator evidence."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np


class RejectedBeforeAction(ValueError):
    """A request refused before any native action ran; simulator state stays trusted."""


class SimulatorError(RuntimeError):
    """The simulator raised while handling a request; ``state`` is its episode and step after it."""

    def __init__(self, message, state=None):
        super().__init__(message)
        self.state = state or {}


class PartialExecutionError(SimulatorError):
    """A later request was rejected after native rows in this call already executed."""


class SimulatorExited(RuntimeError):
    """The simulator process, or the one connection to it, is gone; its episode cannot continue."""


def simulator_request(owner, op, **kwargs):
    """One RPC request from a process manager (``owner.rpc``, ``owner.process``,
    ``owner.log_path``, ``owner.episode_id``, ``owner.step_id``, ``owner.finished``).

    A simulator error leaves the episode running at the step the simulator reports. A lost
    connection ends it with the transport error, the exit code and what the simulator
    logged since its last reply."""
    try:
        result = owner.rpc.request(op, **kwargs)
    except SimulatorError as exc:
        owner.log_mark = log_size(owner.log_path)  # This error is reported in full already.
        if exc.state.get("episode_id") == owner.episode_id and exc.state.get("step_id") is not None:
            owner.step_id = int(exc.state["step_id"])
        raise
    except RejectedBeforeAction:
        owner.log_mark = log_size(owner.log_path)
        raise
    except Exception as exc:
        owner.finished = True  # The RPC client closed the only connection to this simulator.
        process = owner.process
        if process is not None:
            try:
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - still running; reported below
                pass
        since = getattr(owner, "log_mark", 0)
        raise SimulatorExited(f"{op} failed with {type(exc).__name__}: {exc}; "
                              f"{exit_report(process, owner.log_path, since=since)}") from exc
    owner.log_mark = log_size(owner.log_path)
    return result


def log_size(log_path):
    try:
        return Path(log_path).stat().st_size if log_path else 0
    except OSError:
        return 0


def exit_report(process, log_path, *, since=0, lines=12):
    """Exit code plus the error lines a simulator logged after byte ``since`` (or its
    last lines when none look like errors), for the error the agent sees."""
    code = process.poll() if process is not None else None
    try:
        with open(log_path, "rb") as stream:
            stream.seek(since)
            text = stream.read().decode(errors="replace")
    except (OSError, TypeError):
        text = ""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    logged = [line.split("[py stderr]: ")[-1].strip() for line in text.splitlines() if line.strip()]
    # Server events such as {"event": "episode_wall_timeout"} say why it closed on purpose.
    keep = ("Error", "error:", "Fatal", "Traceback", "Segmentation", "Exception", "Killed", "Aborted",
            '"event": ')
    errors = [line for line in logged if any(word in line for word in keep) and "Warning" not in line]
    tail = (errors or logged)[-lines:]
    state = (f"simulator process exited with code {code}" if code is not None
             else "simulator process still running")
    return state + ("; its log: " + " | ".join(tail) if tail else "")


@contextmanager
def action_execution_scope(owner, operation):
    """Promote request-local rejections after completed rows to a call-wide failure.

    Native rows advance step_id before another proposal or chunk is requested. Apply at
    both the native dispatch and whole host tool boundaries.
    """
    before = getattr(owner, "step_id", 0)
    try:
        yield
    except RejectedBeforeAction as exc:
        executed = getattr(owner, "step_id", 0) - before
        if executed > 0:
            raise PartialExecutionError(
                f"{operation} failed after {executed} native action rows executed: {exc}"
            ) from exc
        raise


def reject_unless(condition: bool, message: str) -> None:
    """Validate a request before it touches the simulator."""
    if not condition:
        raise RejectedBeforeAction(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
