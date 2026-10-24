"""PID-reuse-safe identities for trusted host processes."""
import os
from pathlib import Path
import signal


def identity(pid):
    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': stat[19]}


def alive(record):
    if not record or record.get('finished_at') or type(record.get('pid')) is not int or record['pid'] <= 1:
        return False
    try:
        return identity(record['pid'])['start_ticks'] == record.get('start_ticks')
    except (OSError, IndexError):
        return False


def terminate(record, calls=None):
    """SIGTERM through a pidfd pinned to the recorded identity (never a recycled PID).
    calls: (open, send) from harness.session_control.pidfd_calls, which falls back to the raw
    system calls on a libc without the pidfd wrappers (older than glibc 2.36)."""
    if not alive(record):
        raise ValueError('No matching live process; nothing was signaled')
    from harness.session_control import pidfd_calls
    pin, send = calls or pidfd_calls()
    fd = pin(record['pid'])
    try:
        if not alive(record):
            raise ValueError('Process exited; nothing was signaled')
        send(fd, signal.SIGTERM)
    finally:
        os.close(fd)
