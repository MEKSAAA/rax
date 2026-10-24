"""Stop a running auto-research launcher from a terminal; the dashboard's Stop calls the same code.

    bash scripts/stop_auto_research_agent.sh --config runtime/auto-research/<run>/operator-input.json

The launcher gets SIGTERM through a pidfd pinned to its identity (never a recycled PID), then
stops its agent and services and records `stopped`; resume it with
`scripts/start_auto_research_agent.sh --config ... --resume`.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import signal

# Linux syscall numbers (identical on x86_64 and aarch64), for a libc older than glibc 2.36
# that lacks the pidfd_open/pidfd_send_signal wrappers.
SYS_PIDFD_OPEN, SYS_PIDFD_SEND_SIGNAL = 434, 424


def pidfd_calls(libc=None):
    """(open(pid) -> fd, send(fd, signal)) through pidfds, which pin a process's identity:
    Python's own functions, else the libc wrappers, else the raw Linux system calls."""
    if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
        return (lambda pid: os.pidfd_open(pid, 0)), (lambda fd, number: signal.pidfd_send_signal(fd, number, None, 0))
    libc = libc or ctypes.CDLL(None, use_errno=True)

    def checked(result, message):
        if result < 0:
            raise OSError(ctypes.get_errno(), message)
        return result
    try:
        open_pid, send = libc.pidfd_open, libc.pidfd_send_signal
        open_pid.argtypes, open_pid.restype = [ctypes.c_int, ctypes.c_uint], ctypes.c_int
        send.argtypes, send.restype = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int
        return (lambda pid: checked(open_pid(pid, 0), 'Cannot pin launcher identity'),
                lambda fd, number: checked(send(fd, number, None, 0), 'Cannot signal pinned launcher'))
    except AttributeError:
        pass
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise ValueError('Host lacks PID-safe stop support; stop the launch terminal instead')
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    return (lambda pid: checked(syscall(SYS_PIDFD_OPEN, ctypes.c_int(pid), ctypes.c_uint(0)),
                                'Cannot pin launcher identity'),
            lambda fd, number: checked(syscall(SYS_PIDFD_SEND_SIGNAL, ctypes.c_int(fd), ctypes.c_int(number),
                                               ctypes.c_void_p(None), ctypes.c_uint(0)),
                                       'Cannot signal pinned launcher'))


def names(args, path, cwd=None):
    """Whether a command line names `path`, however it was spelled (e.g. via the runtime link).
    A relative argument is resolved against `cwd`, the process's own working directory."""
    target, base = path.resolve(), Path(cwd or Path.cwd())
    return any(arg.endswith(os.fsencode(path.name)) and (base/os.fsdecode(arg)).resolve() == target
               for arg in args if arg)


def lifecycle_of(directory):
    try:
        value = json.loads((Path(directory)/'agent-lifecycle.json').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def alive(directory, lifecycle):
    """Whether the lifecycle's PID is still this session's launcher (or its formal batch)."""
    pid = lifecycle.get('pid')
    if type(pid) is not int or pid <= 1 or lifecycle.get('finished_at'):
        return False
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        cwd = os.readlink(f'/proc/{pid}/cwd')  # A launcher may name its config relative to it.
        if lifecycle.get('kind') == 'formal_batch':
            return b'services.controller.batch' in args and names(args, directory, cwd)
        return b'harness.codex_cli.auto_research' in args and names(args, directory/'operator-input.json', cwd)
    except OSError:
        return False


def stop(directory, *, is_alive=alive):
    directory = Path(directory)
    lifecycle = lifecycle_of(directory)
    if not is_alive(directory, lifecycle):
        raise ValueError('No matching live launcher; nothing was signaled')
    # Pin process identity before rechecking; never signal a recycled PID, and never fall
    # back to a racy numeric-PID kill.
    open_pid, send = pidfd_calls()
    fd = open_pid(lifecycle['pid'])
    try:
        if not is_alive(directory, lifecycle):
            raise ValueError('Launcher exited; nothing was signaled')
        send(fd, signal.SIGTERM)
    finally:
        os.close(fd)
    return {'run_id': directory.name, 'status': 'stop requested; waiting for cleanup'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, required=True, help="The session's operator-input.json")
    args = parser.parse_args()
    try:
        print(json.dumps(stop(args.config.resolve().parent)))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
