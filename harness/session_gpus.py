"""Move a stopped auto-research session's simulators to another GPU before resuming it.

    bash scripts/move_session_gpus.sh --config runtime/auto-research/<run>/operator-input.json \
        --sim-gpu GPU-...

GPU placement is operator choice, not part of what a trial is. The session keeps it in three
copies (operator-input.json, configuration.json and the supervisor's state.json), which must
agree for `--resume`; this rewrites all three together and records the move in gpu-moves.jsonl.
Only the simulator has a GPU: the agent and its bundle runs have none. An agent-formal copy
listed on the old simulator GPU (formal_sim_gpus) moves with the simulator. Agent GPU fields
of sessions created before that (controller_gpu, training_gpu) are left as saved; loading
retires them. Refuses while any process still uses the session.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from harness.session_control import alive, lifecycle_of

FIELDS = ('sim_gpu',)


def gpu_uuids():
    """GPU index -> UUID, from nvidia-smi."""
    listing = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, check=True).stdout
    return {match[0]: match[1] for match in re.findall(r'^GPU (\d+):.*\(UUID: (GPU-[0-9a-fA-F-]+)\)', listing, re.M)}


def resolve(gpu, uuids):
    if gpu is None or gpu.startswith('GPU-'):
        return gpu
    if gpu not in uuids:
        raise ValueError(f'Unknown GPU {gpu!r}; use an index from nvidia-smi or a GPU UUID')
    return uuids[gpu]


def users(directory):
    """PIDs of processes whose command line names the session directory or a path inside it."""
    found = []
    target = Path(directory).resolve()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry/'cmdline').read_bytes().split(b'\0')
            cwd = os.readlink(entry/'cwd')  # Relative paths are the process's own, not ours.
        except OSError:
            continue
        for arg in args:
            text = os.fsdecode(arg)
            for part in (text, *text.split('=')[1:]):
                if part.startswith('/') or part.startswith('runtime/'):
                    try:
                        path = Path(cwd, part).resolve()
                    except (OSError, RuntimeError):
                        continue
                    if path == target or target in path.parents:
                        found.append(int(entry.name))
                        break
            else:
                continue
            break
    return found


def assignment(copy):
    """A saved copy's GPU fields; formal_sim_gpus only when the session lists any."""
    found = {key: copy.get(key) for key in FIELDS}
    if copy.get('formal_sim_gpus'):
        found['formal_sim_gpus'] = list(copy['formal_sim_gpus'])
    return found


def placement(current, sim_gpu):
    """The new GPU fields: the simulator GPU, and the formal copies' simulator GPUs with the
    old simulator GPU replaced by the new one."""
    new = {'sim_gpu': sim_gpu}
    if 'formal_sim_gpus' in current:
        new['formal_sim_gpus'] = list(dict.fromkeys(sim_gpu if gpu == current['sim_gpu'] else gpu
                                                    for gpu in current['formal_sim_gpus']))
    return new


def write_json(path, value):
    mode = path.stat().st_mode & 0o777
    temporary = path.with_name(path.name + '.gpu-move')
    temporary.write_text(json.dumps(value, indent=2 if path.name == 'operator-input.json' else None))
    temporary.chmod(mode)
    temporary.replace(path)


def move(directory, *, sim_gpu, process_users=users, is_alive=alive):
    directory = Path(directory)
    if is_alive(directory, lifecycle_of(directory)) or process_users(directory):
        raise ValueError('The session is still running; stop it and wait for cleanup first')
    files = {name: directory/name for name in ('operator-input.json', 'configuration.json', 'state.json')}
    values = {name: json.loads(path.read_text()) for name, path in files.items()}
    copies = [values['operator-input.json'], values['configuration.json'], values['state.json']['configuration']]
    current = assignment(copies[1])
    if any(assignment(copy) != current for copy in copies):
        raise ValueError('The saved GPU assignments disagree; inspect the session before moving it')
    if not sim_gpu:
        raise ValueError('Give the simulator GPU to move to')
    new = placement(current, sim_gpu)
    from services.controller.config import Configuration
    Configuration.from_dict({**values['configuration.json'], **new})  # Same validation as a launch.
    if new == current:
        return {'run_id': directory.name, 'status': 'unchanged', **new}
    for copy in copies:
        copy.update(new)
    # The supervisor compares state.json with the launch configuration: write it last, so an
    # interrupted move fails that check instead of silently mixing placements.
    for name in ('operator-input.json', 'configuration.json', 'state.json'):
        write_json(files[name], values[name])
    with (directory/'gpu-moves.jsonl').open('a') as stream:
        stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'from': current, 'to': new}) + '\n')
    return {'run_id': directory.name, 'status': 'moved', 'from': current, 'to': new}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, required=True, help="the session's operator-input.json")
    parser.add_argument('--sim-gpu', required=True, help='GPU index or UUID for the simulators')
    args = parser.parse_args()
    print(json.dumps(move(args.config.resolve().parent, sim_gpu=resolve(args.sim_gpu, gpu_uuids()))))


if __name__ == '__main__':
    main()
