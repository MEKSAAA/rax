"""Linux Docker runner. No host sockets, networks, credentials or simulator mounts."""
from __future__ import annotations

from contextlib import contextmanager, suppress
import json
import math
import os
from pathlib import Path
import selectors
import re
import signal
import subprocess
import uuid
import time

from .storage import RUNTIME_SOURCE, snapshot, project_directory
from .gemini import MAX_REQUEST_BYTES
from .errors import public_failure
from harness.codex_cli import workspace_storage


class ExecutionDeadline(BaseException):
    """Cannot be swallowed by a tool's ordinary exception-to-MCP-error handler."""


@contextmanager
def deadline(seconds):
    """Bound even a blocked native MCP/provider call, not just container CPU time. None sets no
    limit of its own, but a timer set inside the block (a finalization limit) still raises."""
    def timeout(*_):
        raise ExecutionDeadline("Execution wall-time budget exhausted")
    old = signal.signal(signal.SIGALRM, timeout)
    previous = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    if seconds is not None:
        signal.setitimer(signal.ITIMER_REAL, min(seconds, previous[0]) if previous[0] else seconds)
    try:
        yield
    finally:
        remaining = max(0.000001, previous[0] - (time.monotonic() - started)) if previous[0] else 0
        signal.setitimer(signal.ITIMER_REAL, remaining, previous[1])
        signal.signal(signal.SIGALRM, old)


def wall_end(limit, seconds=None):
    """When a run must end: the tighter of its limit and the time left in its episode, as a
    monotonic time; None when neither is set (the episode ends at its step limit)."""
    bounds = [value for value in (limit, seconds) if value is not None]
    return time.monotonic() + min(bounds) if bounds else None


def time_left(end_at):
    return None if end_at is None else end_at - time.monotonic()


def finalize_within(seconds):
    """After the episode ends, give the run at most `seconds` more, within any limit left."""
    remaining, _ = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, remaining) if remaining else seconds)


THREAD_VARIABLES = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
                    'VECLIB_MAXIMUM_THREADS')


def thread_env(cpus):
    """Docker --env arguments sizing math-library thread pools to the container's CPU quota.

    --cpus is a time quota, so nproc inside still reports every host core, and each numpy
    or torch process would start one thread per host core; threads count against
    --pids-limit, and a few parallel processes otherwise exhaust it (fork then fails for
    everything in the container, including the agent CLI)."""
    threads = str(max(1, math.ceil(float(cpus))))
    return [argument for name in THREAD_VARIABLES for argument in ('--env', f'{name}={threads}')]


def docker(*arguments, ok_missing=False, timeout=20):
    """Run one docker command; a failure carries docker's own stderr."""
    try:
        done = subprocess.run(["docker", *arguments], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"docker {' '.join(arguments)} timed out after {timeout} s") from exc
    if done.returncode and not (ok_missing and b"No such container" in done.stderr):
        raise RuntimeError(f"docker {' '.join(arguments)} failed (exit {done.returncode}): "
                           + done.stderr.decode(errors="replace").strip())
    return done


class DockerSandbox:
    output_mount_action = 'output_mount'
    # Seconds allowed for the process to exit after the episode ends.
    finalization_seconds = 10
    def __init__(self, image, limits):
        self.image, self.limits = image, limits
        self.execution_seconds = 0.0

    def command(self, name, bundle, mode, entrypoint, *, sdk=None, frames=None, writable=None,
                workspace_path='code/controller'):
        lim = self.limits
        sdk = sdk or RUNTIME_SOURCE
        if writable is None or not os.path.ismount(writable):
            raise ValueError("Capped output filesystem required")
        project = project_directory(workspace_path)
        layout = Path(writable).parent / 'workspace'
        for path in (bundle, sdk, writable, layout):
            if any(c in str(path) for c in (",", "\n")):
                raise ValueError("Invalid bind path")
        command = [
            "docker", "run", "--pull=never", "--name", name, "--interactive",
            "--network=none", "--read-only", "--user=65534:65534", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--ipc=private", "--pids-limit", str(lim.pids),
            "--memory", f"{lim.memory_mb}m", "--memory-swap", f"{lim.memory_mb}m",
            "--cpus", str(lim.cpus), "--ulimit", "core=0", "--ulimit", "nofile=128:128",
            "--log-driver=none", "--init",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={lim.scratch_mb}m,mode=1777",
            "--mount", f"type=bind,src={layout},dst=/workspace,readonly",
            "--mount", f"type=bind,src={writable},dst=/workspace/output",
            "--mount", f"type=bind,src={bundle},dst={project},readonly",
            "--mount", f"type=bind,src={sdk},dst=/workspace/api/runtime.py,readonly",
            "--workdir", "/workspace", "--env", "HOME=/workspace",
            "--env", f"PYTHONPATH=/workspace:{project}", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONNOUSERSITE=1", "--env", "PIP_NO_INDEX=1",
            "--env", "HF_HUB_OFFLINE=1", "--env", "WANDB_MODE=disabled", *thread_env(lim.cpus),
            "--entrypoint", "/usr/local/bin/python",
        ]
        if frames is not None:
            frames = Path(frames)
            if (not frames.is_absolute() or frames.is_symlink() or not frames.is_dir()
                    or frames.name != 'frames' or frames.parent.parent.name != 'runs'
                    or not re.fullmatch(r'(?:rehearsal|formal)-[a-f0-9]{32}', frames.parent.name)
                    or any(c in str(frames) for c in (',', '\n'))):
                raise ValueError('Invalid published frame mount')
            destination = '/workspace/runtime/autonomous_controller/runs/' + frames.parent.name + '/frames'
            command += ['--mount', f'type=bind,src={frames},dst={destination},readonly']
        return command + [self.image, "-u", "/workspace/api/runtime.py", mode, str(project), entrypoint]

    def preflight(self):
        # Inspect only: never pull or build an image during a trial.
        result = subprocess.run(["docker", "image", "inspect", self.image], capture_output=True, timeout=20, check=True)
        spec = json.loads(result.stdout)[0]
        if spec["Config"].get("Volumes") or spec["Config"].get("OnBuild"):
            raise ValueError("Approved runtime must not declare volumes or build hooks")

    def run(self, bundle, *, mode, entrypoint, gateway=None, token=None, output, name=None,
            frames=None, wall_seconds=None, workspace_path='code/controller'):
        if mode not in {"formal", "rehearsal"} or entrypoint != "controller.py":
            raise ValueError("Unsupported execution mode/entrypoint")
        project_directory(workspace_path)
        name = name or "rax-controller-" + uuid.uuid4().hex
        if not re.fullmatch(r"rax-controller-[a-f0-9]{32}", name):
            raise ValueError("Invalid owned container name")
        output.mkdir(mode=0o700, exist_ok=True)
        # Mount a private immutable runtime snapshot, never a mutable skill or
        # developer source path into a running controller.
        sdk = output / "runtime.py"
        with sdk.open("xb") as stream:
            stream.write(RUNTIME_SOURCE.read_bytes())
        sdk.chmod(0o444)
        # Empty read-only workspace scaffold, never the agent's live filesystem.
        layout = output / 'workspace'
        (layout / workspace_path).mkdir(parents=True, mode=0o755)
        (layout / 'output').mkdir(mode=0o755)
        (layout / 'api').mkdir(mode=0o755)
        (layout / 'api/runtime.py').touch(mode=0o444)
        if frames is not None:
            (layout / 'runtime/autonomous_controller/runs' / Path(frames).parent.name / 'frames').mkdir(parents=True)
        writable = output / "writable"
        writable.mkdir(mode=0o700)
        end_at = wall_end(self.limits.wall_seconds, wall_seconds)
        output_error = None
        process = None
        reason = "exit"
        returncode = None
        finalizing = False
        execution_started = None
        logs = {"stdout": bytearray(), "stderr": bytearray()}
        seen = {"stdout": 0, "stderr": 0}
        log_details = None
        primary = None
        def log(stream, data):
            seen[stream] += len(data)
            logs[stream].extend(data[:max(0, 1024 * 1024 - len(logs[stream]))])
        try:
            workspace_storage.helper(output, self.image, self.output_mount_action, output_bytes=self.limits.artifact_bytes)
            if not os.path.ismount(writable):
                raise RuntimeError("Output mount did not propagate")
            remaining = time_left(end_at)
            if remaining is not None and remaining <= 0:
                raise ExecutionDeadline()
            with deadline(remaining):
                execution_started = time.monotonic()
                process = subprocess.Popen(self.command(name, bundle, mode, entrypoint, sdk=sdk, frames=frames,
                                           writable=writable, workspace_path=workspace_path), stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ, "mcp")
                selector.register(process.stderr, selectors.EVENT_READ, "log")
                pending = bytearray()
                try:
                    while selector.get_map():
                        for key, _ in selector.select(timeout=1):
                            block = os.read(key.fileobj.fileno(), 65536)
                            if not block:
                                selector.unregister(key.fileobj)
                                continue
                            if key.data == "log":
                                log("stderr", block)
                                continue
                            pending.extend(block)
                            if len(pending) > MAX_REQUEST_BYTES:
                                raise ValueError("Controller request exceeds wire limit")
                            while b"\n" in pending:
                                line, _, tail = pending.partition(b"\n")
                                pending = bytearray(tail)
                                request = json.loads(line)
                                if request.get("method") == "logs/write":
                                    params = request.get("params", {})
                                    if set(params) != {"text"} or not isinstance(params["text"], str):
                                        raise ValueError("Invalid log event")
                                    log("stdout", params["text"].encode())
                                    continue
                                elif finalizing:
                                    response = {'jsonrpc': '2.0', 'id': request.get('id'),
                                                'error': {'code': -32000, 'message': 'Episode ended; artifact cleanup only'}}
                                else:
                                    response = gateway.handle(token, request)
                                if response is not None:
                                    process.stdin.write(json.dumps(response, allow_nan=False).encode() + b"\n")
                                    process.stdin.flush()
                                if gateway is not None and gateway.terminal and not finalizing:
                                    finalizing = True
                                    reason = "episode_ended"
                                    finalize_within(self.finalization_seconds)
                    returncode = process.wait(timeout=5)
                finally:
                    selector.close()
        except ExecutionDeadline:
            reason = "finalization_timeout" if finalizing else "timeout"
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if execution_started is not None:
                self.execution_seconds = time.monotonic() - execution_started
            # Every cleanup step runs even when an earlier one fails (a docker kill that times
            # out must not leave the output mounted or the container behind).
            cleanup_errors = []
            def step(action):
                try:
                    action()
                except Exception as exc:
                    cleanup_errors.append(exc)
            def kill():
                # Container lifetime is independent of the docker attach client's life. Best effort:
                # the container has usually exited already, and `docker rm --force` below is the
                # authoritative removal.
                with suppress(subprocess.SubprocessError, OSError):
                    subprocess.run(["docker", "kill", name], capture_output=True, timeout=20)
            def reap():
                if not process:
                    return
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                # Drain error output already emitted before a terminal action.
                os.set_blocking(process.stderr.fileno(), False)
                while True:
                    try:
                        block = os.read(process.stderr.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not block:
                        break
                    log("stderr", block)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    pipe.close()
            def write_logs():
                nonlocal log_details
                output.mkdir(mode=0o700, exist_ok=True)
                for stream, data in logs.items():
                    (output / f"{stream}.log").write_bytes(data)
                log_details = {stream: {"bytes_received": seen[stream], "bytes_saved": len(data),
                                       "truncated": seen[stream] > len(data)} for stream, data in logs.items()}
                (output / "logs.json").write_text(json.dumps(log_details))
            def copy_outputs():
                nonlocal output_error
                # The writer is dead before inspecting untrusted output names. Copy
                # regular files only; never execute/deserialise submitted artifacts.
                try:
                    if os.path.ismount(writable):
                        snapshot(writable, output / "files", image=self.image, max_bytes=self.limits.artifact_bytes)
                except Exception as exc:
                    output_error = f"{type(exc).__name__}: {public_failure(exc)['reason']}"
            def unmount():
                if os.path.ismount(writable):
                    workspace_storage.helper(output, self.image, "output_unmount")
                writable.rmdir()
            for action in (kill, reap, write_logs, copy_outputs, unmount):
                step(action)
            step(lambda: docker("rm", "--force", name, ok_missing=True))
            if cleanup_errors:
                summary = "Controller sandbox cleanup failed: " + "; ".join(
                    f"{type(e).__name__}: {e}" for e in cleanup_errors)
                if primary is None:
                    raise RuntimeError(summary) from cleanup_errors[0]
                primary.add_note(summary)
        return {"reason": reason, "returncode": returncode, "logs": log_details,
                "simulator_exited": gateway and gateway.simulator_exited, "output_error": output_error}
