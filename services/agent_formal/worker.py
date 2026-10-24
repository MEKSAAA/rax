"""One agent formal episode in its own process (the bundle formal_worker's sibling).

The parent supervisor keeps the Gemini key and the session ledger and brokers both
over the inherited channel. The worker imports the frozen checkpoint, then runs the
supervisor's ordinary formal episode with an AgentRunner in place of the controller.
"""
import argparse
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

from services.controller.backend import make_backend
from services.controller.config import Configuration
from services.controller.formal_worker import Channel, WorkerSupervisor, worker_configuration, worker_root
from services.controller.storage import verify
from .runner import AgentRunner

PROJECT = Path(__file__).resolve().parents[2]
DIRECTORY = 'agent-formal'


def spawn(parent_root, bundle_id, index, connection):
    root = worker_root(parent_root, index, DIRECTORY)
    root.parent.mkdir(mode=0o700, exist_ok=True)
    environment = {k: v for k, v in os.environ.items() if k != 'OPENROUTER_API_KEY'}
    with (root.parent/f'{root.name}.log').open('xb') as log:
        return subprocess.Popen([sys.executable, '-m', 'services.agent_formal.worker',
            '--parent-root', str(parent_root), '--bundle', bundle_id, '--index', str(index),
            '--channel-fd', str(connection.fileno()), '--parent-pid', str(os.getpid())],
            pass_fds=(connection.fileno(),), stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, cwd=PROJECT, env=environment, start_new_session=True)


def import_checkpoint(supervisor, parent_root, bundle_id):
    """Copy the parent's frozen checkpoint into this worker's own bundle store."""
    state = json.loads((Path(parent_root)/'state.json').read_text())
    manifest = state['bundles'][bundle_id]
    if manifest.get('kind') != 'agent_checkpoint':
        raise ValueError('Not an agent checkpoint')
    source = (Path(parent_root)/'bundles'/manifest['directory']).resolve()
    if not source.is_relative_to((Path(parent_root)/'bundles').resolve()):
        raise ValueError('Invalid checkpoint directory')
    verify(source, manifest)
    target = supervisor.config.root/'bundles'/manifest['directory']
    target.parent.mkdir(mode=0o700, exist_ok=True)
    shutil.copytree(source, target)
    verify(target, manifest)
    supervisor.state['bundles'][bundle_id] = manifest
    supervisor._save()
    return bundle_id


def on_gpu(config, gpu):
    """This copy's configuration with its simulator on ``gpu`` (a copy itself runs on no GPU)."""
    return replace(config, sim_gpu=gpu)


def run_worker(parent_config, bundle_id, index, channel, *, backend_factory=None, runner_factory=None):
    config = worker_configuration(parent_config, index, DIRECTORY)
    gpus = parent_config.formal_sim_gpus
    if gpus:
        config = on_gpu(config, gpus[index % len(gpus)])
    # The runner reads the parent's agent login and CLI settings; its layout is this worker's.
    factory = runner_factory or (lambda limits: AgentRunner(limits, config=parent_config,
                                                            layout_id=config.formal_seed))
    supervisor = WorkerSupervisor(config, backend_factory or (lambda: make_backend(config)), channel,
                                  runner_factory=factory, published_root=parent_config.root/'published')
    try:
        import_checkpoint(supervisor, parent_config.root, bundle_id)
        return supervisor._run_episode(bundle_id, formal=True, formal_index=0)
    finally:
        try:
            supervisor.close()
        finally:
            shutil.rmtree(config.root/'bundles', ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-root', type=Path, required=True)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--channel-fd', type=int, required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    args = parser.parse_args()
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    if os.getppid() != args.parent_pid:
        raise SystemExit('Parent supervisor exited before the worker started')

    def stop(*_):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    state = json.loads((args.parent_root/'state.json').read_text())
    parent = Configuration.from_dict(state['configuration'])
    if parent.root != args.parent_root.resolve():
        raise SystemExit('Parent configuration root mismatch')
    channel = Channel(socket.socket(fileno=args.channel_fd))
    result = run_worker(parent, args.bundle, args.index, channel)
    print(json.dumps({k: result.get(k) for k in ('reason', 'returncode', 'task_complete')}), flush=True)


if __name__ == '__main__':
    main()
