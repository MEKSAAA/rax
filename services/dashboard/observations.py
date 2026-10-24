"""Read-only native camera snapshots shared by operator views; never robot RPC."""
from pathlib import Path

from services.benchmarks import EPISODE_SERVERS


def process_matches(pid, run_path):
    try:
        pid = int(pid)
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except (TypeError, ValueError, OSError):
        return False
    return (any(module.encode() in command for module in EPISODE_SERVERS)
            and str(run_path.resolve()).encode() in command)
