"""Private, single-threaded native environment worker (not an agent endpoint)."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
from types import SimpleNamespace
import uuid

from services.benchmarks import DEFAULT
from services.controller.backend import make_backend
from services.controller.gateway import Gateway, complete, sanitized
from services.controller.recording import RunRecording
from services.controller.sandbox import deadline
from services.controller.errors import public_failure


SESSION_COUNTERS = {'environment_setup_count', 'environment_reset_count',
                    'exploration_episodes_started', 'max_exploration_episodes', 'total_interaction_steps'}


def episode_scoped(value):
    """Drop a private worker's single-start counters; the pool owns the shared budget."""
    value = dict(value)
    value.pop('total_interaction_steps', None)
    if 'performance_cost' in value:
        value['performance_cost'] = {k: v for k, v in value['performance_cost'].items()
                                     if k not in SESSION_COUNTERS}
        value['performance_cost']['scope'] = 'current_episode'
    return value


class EpisodeView:
    """Do not advertise a private worker's single-start counter as a session budget."""
    def __init__(self, backend):
        self.backend = backend

    def tools(self):
        return self.backend.tools()

    def validate(self, name, arguments):
        validator = getattr(self.backend, 'validate', None)
        if validator:
            validator(name, arguments)

    def call(self, name, arguments):
        value, images = self.backend.call(name, arguments)
        return episode_scoped(value), images


class EpisodeWorker:
    def __init__(self, config, backend_factory=make_backend):
        self.config = config
        native = SimpleNamespace(benchmark=config.get('benchmark', DEFAULT),
            observation_profile=config.get('observation_profile', 'rgbd'),
            root=Path(config['root']), task=config['task'], sim_gpu=config['sim_gpu'],
            formal=SimpleNamespace(wall_seconds=config['episode_seconds']), eval_seed=config['eval_seed'],
            formal_collection=config['eval_seed'], exploration_seeds=(config['seed'],))
        self.backend = backend_factory(native)
        self.recording = RunRecording(Path(config['published_root']), 'interactive-' + uuid.uuid4().hex,
            logical_prefix=config.get('logical_prefix', 'runtime/autonomous_controller'),
            observation_profile=native.observation_profile)
        self.state = {'env_id': config['env_id'], 'episode': config['episode'], 'status': 'starting',
                      'task_complete': False, 'artifacts': self.recording.paths}
        from services.mcp_contract import Contract
        self.gateway = Gateway(EpisodeView(self.backend), None,
            contract=Contract.from_config(native, isolated=True), audit=self.recording.event,
            charge=lambda *_: None, refund=lambda *_: None, budget=lambda: {},
            on_success=self.success,
            publish_sequence=lambda value: self.recording.sequence(value, self.backend.frame_workspace))
        self.token = self.gateway.acquire()
        self.closed = False

    def success(self):
        self.state['task_complete'] = True

    def robot(self, name, arguments):
        response = self.gateway.handle(self.token, {'jsonrpc': '2.0', 'id': uuid.uuid4().hex,
            'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        if 'error' in response:
            return {'isError': True, 'content': [{'type': 'text', 'text': json.dumps(response['error'])}]}
        return response['result']

    def evaluate_record(self):
        """After its simulator exits, a backend whose verdict is the simulator's own record
        (written when it ends an episode at its wall limit) is still evaluated, as
        the supervisor does; the pool counts task_complete."""
        if not (self.gateway.simulator_exited and getattr(self.backend, 'evaluates_from_record', False)
                and self.state['status'] == 'active' and 'evaluation' not in self.state):
            return
        try:
            with deadline(60):
                value = episode_scoped(self.backend.evaluate())
        except Exception as exc:
            self.state['evaluation_error'] = f'{type(exc).__name__}: {exc}'
        else:
            self.state['evaluation'] = sanitized(value)
            if complete(value):
                self.success()

    def finish(self):
        if not self.closed:
            self.closed = True
            try:
                self.evaluate_record()
                self.gateway.revoke()
                self.backend.close()
            finally:
                self.state.update(status='closed', simulator_exited=self.gateway.simulator_exited)
                self.recording.finish_interactive(self.state)

    def call(self, name, arguments):
        if name == 'start':
            if self.state['status'] != 'starting':
                raise RuntimeError('An episode worker cannot reset')
            # Each benchmark's own startup deadline (scenes may take minutes on a cold cache).
            with deadline(self.gateway.contract.bench.startup_seconds):
                value, _ = self.backend.start(formal=False, seed=self.config['seed'])
            self.state.update(status='active', episode_id=value.get('episode_id'))
            reply = self.robot(self.gateway.contract.bench.observe_tool, {})
        elif name == 'finish':
            if set(arguments) - {'reason'}:
                raise ValueError('Finish accepts only an optional reason')
            self.recording.event({'event': 'mcp_request', 'tool': name, 'arguments': arguments})
            if 'reason' in arguments:
                self.state['finish_reason'] = arguments['reason']
            self.finish()
            reply = {'content': [{'type': 'text', 'text': json.dumps(self.state)}]}
        elif self.closed:
            raise RuntimeError('Episode is closed')
        elif name == 'evaluate':
            if set(arguments) - {'agent_assessment', 'confidence'}:
                raise ValueError('Evaluation accepts only agent_assessment and confidence')
            self.recording.event({'event': 'mcp_request', 'tool': name, 'arguments': arguments})
            if self.gateway.simulator_exited and not getattr(self.backend, 'evaluates_from_record', False):
                raise RuntimeError(f'Cannot evaluate: {self.gateway.simulator_exited}')
            with deadline(60):
                value = episode_scoped(self.backend.evaluate(**arguments))
            if complete(value):
                self.success()
            reply = {'content': [{'type': 'text', 'text': json.dumps({'evaluation': sanitized(value)})}]}
            self.recording.event({'event': 'mcp_result', 'tool': 'evaluate', 'result': reply})
        elif name in self.gateway.contract.robot_names:
            reply = self.robot(name, arguments)
        else:
            raise PermissionError('Worker only permits robot calls, start, evaluate and finish')
        if self.gateway.terminal:
            self.finish()
        return {'reply': reply, 'state': dict(self.state)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fd', type=int, required=True)
    args = parser.parse_args()
    parent = os.getppid()
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)
    if os.getppid() != parent:
        return
    worker = None
    try:
        worker = EpisodeWorker(json.loads(args.config.read_text()))
        with socket.socket(fileno=args.fd) as connection, connection.makefile('rwb') as stream:
            while line := stream.readline(32 * 1024 * 1024 + 1):
                if len(line) > 32 * 1024 * 1024:
                    raise ValueError('Worker request exceeds wire limit')
                request = json.loads(line)
                try:
                    result = worker.call(request['name'], request.get('arguments', {}))
                    response = {'id': request['id'], 'result': result}
                except Exception as exc:
                    response = {'id': request['id'], 'error': public_failure(exc)['reason']}
                stream.write(json.dumps(response, allow_nan=False).encode() + b'\n')
                stream.flush()
    finally:
        if worker is not None:
            worker.finish()


if __name__ == '__main__':
    main()
