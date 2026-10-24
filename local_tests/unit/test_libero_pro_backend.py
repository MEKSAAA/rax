"""LIBERO-PRO backend: catalog, layout collections, validation, geometry, privacy and host separation.

No simulator: the backend is exercised against a fake simulator process. Catalog and data tests read
the pinned LIBERO-PRO checkout and the runtime data tree when they are installed.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from services.benchmarks import BENCHMARKS, LIBERO_PRO_TOOLS
from services.libero_pro import tasks as TK
from services.libero_pro import tools as T
from services.mcp_contract import Contract
from services.simulator.io import RejectedBeforeAction

ROOT = Path(__file__).resolve().parents[2]
needs_source = pytest.mark.skipif(not (TK.source_root() / "libero" / "libero").is_dir(),
                                  reason="pinned LIBERO-PRO checkout not installed (scripts/bootstrap_sources.sh)")
needs_data = pytest.mark.skipif(not (TK.data_root() / "config" / "config.yaml").is_file(),
                                reason="LIBERO-PRO data tree not built (scripts/setup_libero_pro_data.py)")
# Modules that run inside the simulator interpreter (Python 3.8).
SIMULATOR_MODULES = ('services/libero_pro/session.py', 'services/libero_pro/server.py',
                     'services/libero_pro/checks.py', 'services/libero_pro/demo_tools.py',
                     'services/libero_pro/tasks.py', 'services/libero_pro/__init__.py',
                     'services/simulator/__init__.py', 'services/simulator/io.py', 'services/simulator/rpc.py',
                     'services/simulator/protocol.py')


# ------------------------------------------------------------------ contract
def test_contract_selects_libero_pro_tools():
    contract = Contract(benchmark='libero_pro')
    assert contract.robot_names == LIBERO_PRO_TOOLS
    assert Contract(observation_profile='rgb-only', benchmark='libero_pro').robot_names == \
        LIBERO_PRO_TOOLS - {'libero_pixel_to_position'}
    with pytest.raises(PermissionError):
        contract.require_robot('dexjoco_step', {})
    assert contract.scoped_tools >= {'libero_step', 'start_episode'}


def test_registry_entry_matches_the_tool_module():
    bench = BENCHMARKS['libero_pro']
    assert T.ROBOT_TOOLS == bench.robot_tools and T.MUTATING == bench.mutating
    assert bench.robot['benchmark'] == bench.label and not bench.previewable and bench.robot['plan'] == ''
    assert bench.graded_score is False and bench.partial_credit is False
    assert bench.episode_reviews is False and bench.fixed['episode_reviews'][0] == 'off'
    assert bench.fixed['task_rules'][0] == 'public' and bench.fixed['expert_demonstrations'][0] == 0
    assert {t['name'] for t in bench.definitions()} == LIBERO_PRO_TOOLS
    assert set(T.CAMERAS) == set(bench.robot['cameras'])


def test_vocabularies_do_not_claim_shared_robot_names():
    """The Franka Panda is a common robot: the LIBERO-PRO vocabulary does not own it."""
    for bench in BENCHMARKS.values():
        if bench.vocabulary:
            assert not re.search(bench.vocabulary, 'Franka Panda arm', re.I), bench.name
    assert re.search(BENCHMARKS['libero_pro'].vocabulary, 'libero_step', re.I)
    assert not re.search(BENCHMARKS['libero_pro'].vocabulary, 'dexjoco_step', re.I)


# ------------------------------------------------------------------- catalog
def test_catalog_and_step_limits():
    names = TK.task_names()
    assert len(names) == 40 and names[0] == 'libero_spatial_0' and names[-1] == 'libero_10_9'
    assert TK.official_task('libero_goal_8') == ('libero_goal', 8, 'put_the_bowl_on_the_plate')
    assert [TK.native_step_limit(f'{s}_0') for s in TK.SUITES] == [220, 280, 300, 520]
    for bad in ('libero_90_0', 'libero_spatial_10', 'pick_bucket', 'libero_spatial'):
        with pytest.raises(ValueError):
            TK.official_task(bad)
    others = {t for name, b in BENCHMARKS.items() if name != 'libero_pro' for t in b.task_names()}
    assert not others & set(names)
    timeouts = TK.task_timeouts('libero_10_0')
    assert timeouts['native_step_limit'] == 520 and timeouts['episode_seconds'] > timeouts['startup_seconds']


@needs_source
def test_catalog_is_the_official_suite_order():
    path = TK.source_root() / 'libero' / 'libero' / 'benchmark' / 'libero_suite_task_map.py'
    official = next(ast.literal_eval(node.value) for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                    and 'libero_spatial' in ast.literal_eval(node.value))
    for suite, (_, tasks) in TK.SUITES.items():
        assert list(tasks) == official[suite], suite


def test_layout_collections():
    assert TK.layout('libero_spatial_0', 0, 0) == {'kind': 'reset', 'seed': 1000}
    assert TK.layout('libero_spatial_0', 0, 999)['seed'] == 1999
    for collection in TK.FORMAL_COLLECTIONS:
        assert TK.layout('libero_spatial_0', collection, 49) == {'kind': 'init_state', 'seed': 0, 'init_index': 49}
    with pytest.raises(ValueError, match='outside'):
        TK.layout('libero_spatial_0', 1, 50)
    # Swap exploration (optional second stage): fresh resets of the swap BDDL, never its init states.
    assert TK.layout('libero_spatial_0', 6, 0) == {'kind': 'reset', 'seed': 2000}
    assert TK.suite_directory('libero_goal_1', 6) == 'libero_goal_swap'
    with pytest.raises(ValueError):
        TK.layout('libero_spatial_0', 7, 0)
    with pytest.raises(ValueError):
        TK.init_states_path('libero_goal_1', 6)
    assert TK.suite_directory('libero_goal_1', 0) == 'libero_goal'
    assert [TK.suite_directory('libero_goal_1', c) for c in TK.FORMAL_COLLECTIONS] == \
        ['libero_goal', 'libero_goal_swap', 'libero_goal_object', 'libero_goal_lan', 'libero_goal_task']
    with pytest.raises(ValueError):
        TK.init_states_path('libero_goal_1', 0)


def test_bddl_language_follows_libero_parsing():
    text = "(define (problem X)\n  (:domain robosuite)\n  ; a comment (:language no)\n  (:language Put the  Bowl\n on the plate)\n)"
    assert TK.bddl_language(text) == 'put the bowl on the plate'


def config_for(**values):
    base = dict(task='libero_spatial_0', eval_seed=0, formal_collection=1, agent_formal=False, task_rules='public')
    return SimpleNamespace(**{**base, **values})


def test_config_rules():
    from services.libero_pro.benchmark import validate_config, validate_settings
    validate_config(config_for())
    validate_config(config_for(formal_collection=2))
    validate_config(config_for(formal_collection=5, agent_formal=True))
    validate_config(config_for(eval_seed=6, formal_collection=2))  # optional second stage on swap layouts
    for bad in (config_for(eval_seed=1), config_for(eval_seed=2), config_for(formal_collection=0),
                config_for(formal_collection=6),
                config_for(formal_collection=5), config_for(task_rules='code-summary')):
        with pytest.raises(ValueError):
            validate_config(bad)
    validate_settings({'formal_seed': 0, 'formal_episodes': 50})
    with pytest.raises(ValueError, match='50 official'):
        validate_settings({'formal_seed': 1, 'formal_episodes': 50})


@needs_data
def test_data_tree_and_task_context():
    from services.libero_pro.task_rules import task_context
    for task in TK.task_names():
        for collection in (0, *TK.FORMAL_COLLECTIONS):
            assert TK.bddl_path(task, collection).is_file()
            if collection:
                assert TK.init_states_path(task, collection).is_file()
    assert TK.instruction('libero_goal_8') == 'put the bowl on the plate'
    assert TK.instruction('libero_goal_8', 5) != TK.instruction('libero_goal_8')  # the task perturbation's goal
    public = task_context('libero_goal_8')
    assert '**Instruction:** put the bowl on the plate' in public and 'There is no partial credit' in public
    assert 'held-out layouts' in public
    assert 'Formal layouts may swap objects' in public and 'do not rely on where objects usually are' in public
    for word in ('perturb', 'LIBERO-PRO', 'collection', 'init state', 'paraphras'):
        assert word.lower() not in public.lower(), word
    copy = task_context('libero_goal_8', live_formal=True, collection=5)
    assert TK.instruction('libero_goal_8', 5) in copy and 'no reset' in copy and 'Wall-clock' not in copy
    assert 'Objects may be swapped' in copy and 'exploration' not in copy.lower()
    second = task_context('libero_goal_8', collection=6)  # optional second stage on swap layouts
    assert 'in exploration and formal alike' in second and 'held-out layouts' in second
    assert 'in places exploration never shows' not in second
    assert TK.instruction('libero_goal_8', 6) == TK.instruction('libero_goal_8', 2)
    practice = task_context('libero_goal_8', evaluation=False)  # live-copy exploration
    assert 'Objects may be swapped' in practice and not re.search(r'\bformal\b|held-out', practice)


# ---------------------------------------------------------------- validation
def test_request_validation_rejects_malformed_calls_before_dispatch():
    row = [0.0] * 6 + [-1.0]
    T.validate_request(T.STEP, {'actions': [row] * 3})
    for bad in ({'actions': []}, {'actions': [[0.0] * 6]}, {'actions': [row, [0.0] * 8]},
                {'actions': [[float('nan')] * 7]}, {'actions': [[1.5] + row[1:]]},
                {'actions': [row] * (T.MAX_ACTION_ROWS + 1)}, {'rows': [row]}):
        with pytest.raises(RejectedBeforeAction):
            T.validate_request(T.STEP, bad)
    with pytest.raises(RejectedBeforeAction):
        T.validate_request(T.PIXEL_TO_POSITION, {'observation_step_id': 0,
                                                 'selections': [{'camera': 'front', 'u': 1, 'v': 1}]})
    with pytest.raises(RejectedBeforeAction):
        T.validate_request('dexjoco_hold', {'steps': 1})


def test_pixel_and_world_projection_round_trip():
    intrinsics = {'matrix': [[300.0, 0, 128], [0, 300.0, 128], [0, 0, 1]]}
    pose = {'position_m': [0.1, -0.2, 2.0], 'quaternion_wxyz': [1.0, 0.0, 0.0, 0.0]}
    point = np.array([0.25, -0.1, 0.9])
    u, v, z = T.world_to_pixel(point, intrinsics, pose)
    depth = np.full((256, 256), z, np.float32)
    back = T.pixel_to_world(depth, intrinsics, pose, u, v, 0)
    assert back['valid'] and np.allclose(back['position_m'], point, atol=1e-6)
    assert T.world_to_pixel(point + [0, 0.05, 0], intrinsics, pose)[1] < v  # row 0 at the top


def test_session_math_matches_robosuite():
    from scipy.spatial.transform import Rotation as R
    from services.libero_pro.session import matrix_to_quat_wxyz, quat_xyzw_to_axis_angle
    rotation = R.from_rotvec([0.3, -1.2, 2.0])
    np.testing.assert_allclose(quat_xyzw_to_axis_angle(rotation.as_quat()), rotation.as_rotvec(), atol=1e-9)
    q = np.asarray(matrix_to_quat_wxyz(rotation.as_matrix()))
    expected = rotation.as_quat()[[3, 0, 1, 2]]
    assert np.allclose(q, expected, atol=1e-9) or np.allclose(q, -expected, atol=1e-9)


# ------------------------------------------------------------------- backend
class FakeProcess:
    """In-memory stand-in for services.libero_pro.process.LiberoProProcess."""

    def __init__(self, output_root, success=False):
        self.output_root = Path(output_root)
        self.process = self.rpc = None
        self.episode_id, self.step_id, self.run_id, self.output_dir = None, 0, None, None
        self.finished, self.startup_seconds, self.success = False, 1.0, success
        self.metadata = {'cameras': list(T.CAMERAS)}
        self.requests = []

    @property
    def active(self):
        return self.process is not None and not self.finished

    def start(self, *, task, collection, layout_id, run_id, **kwargs):
        self.task, self.collection, self.layout_id, self.run_id = task, collection, layout_id, run_id
        self.output_dir = self.output_root / run_id
        (self.output_dir / 'sim').mkdir(parents=True)
        self.process, self.episode_id, self.step_id, self.finished = object(), f'ep-{layout_id}', 0, False
        self._state()
        return {'episode_id': self.episode_id, 'step_id': 0, 'native_step_limit': 220}

    def _state(self):
        (self.output_dir / 'sim' / 'operator_state.json').write_text(json.dumps({
            'episode_id': self.episode_id, 'step_id': self.step_id,
            'reward': {'native_success': self.success, 'native_score_percent': 100.0 if self.success else 0.0}}))

    def observation(self):
        obs = {c: np.full((8, 8, 3), 100, np.uint8) for c in T.CAMERAS}
        obs.update({f'{c}_depth_m': np.ones((8, 8), np.float32) for c in T.CAMERAS})
        obs.update(state=list(np.linspace(0, 1, 8)), instruction='put the bowl on the plate',
                   camera_intrinsics={c: {'matrix': [[8, 0, 4], [0, 8, 4], [0, 0, 1]]} for c in T.CAMERAS},
                   camera_poses={c: {'position_m': [0, 0, 2], 'quaternion_wxyz': [0, 1, 0, 0]} for c in T.CAMERAS},
                   step_id=self.step_id)
        return obs

    def request(self, op, **kwargs):
        self.requests.append((op, kwargs))
        if op == 'observe':
            return self.observation()
        if op == 'chunk_step':
            frames, steps = [], []
            for _ in range(len(kwargs['actions'])):
                self.step_id += 1
                steps.append({'step_id': self.step_id, 'executed_action': None, 'terminated': self.success,
                              'truncated': False, **({'success': True} if self.success else {})})
                if self.step_id % T.FRAME_STRIDE == 0:
                    frames.append({k: v for k, v in self.observation().items() if not k.endswith('_depth_m')})
                if self.success:
                    break
            frames.append(self.observation())
            self._state()
            return {'episode_id': self.episode_id, 'step_id': self.step_id, 'frames': frames, 'steps': steps,
                    'terminated': self.success, 'truncated': False, **({'success': True} if self.success else {})}
        raise AssertionError(op)

    def finish(self, reason):
        self.finished = True

    def stop(self):
        self.process = None
        self.finished = True


def backend_for(tmp_path, profile='rgbd', success=False, formal_collection=1):
    from services.libero_pro.backend import LiberoProBackend
    config = SimpleNamespace(root=tmp_path, task='libero_spatial_0', benchmark='libero_pro',
                             observation_profile=profile, exploration_envs=1, subagents=0, eval_seed=0,
                             formal_collection=formal_collection, sim_gpu='GPU-0',
                             formal=SimpleNamespace(wall_seconds=3000))
    backend = LiberoProBackend(config)
    backend.sim = FakeProcess(tmp_path / 'native' / 'results', success=success)
    return backend


PRIVATE_WORDS = ('native_score', 'native_success', 'layout_id', 'collection', 'init_index', 'init_state',
                 'swap', 'perturb', 'objects', '"seed"', 'bddl', 'suite')


def assert_public(value):
    text = json.dumps(value).lower()
    for word in PRIVATE_WORDS:
        assert word not in text, word


def test_backend_start_step_and_evaluate(tmp_path):
    backend = backend_for(tmp_path, formal_collection=2)
    value, images = backend.start(formal=True, seed=17)
    assert backend.sim.collection == 2 and backend.sim.layout_id == 17
    assert value['environment_spec']['episode_step_limit'] == 220
    assert value['instruction'] == 'put the bowl on the plate'
    assert len(images) == 4  # two RGB + two depth attachments
    assert_public(value)
    value, images = backend.call('libero_step', {'actions': [[0.0] * 6 + [-1.0]] * 13})
    assert value['step_id'] == 13 and value['frame_sequence']['frame_count'] == 3 and len(images) == 4
    assert backend.sim.requests[-1][0] == 'chunk_step' and backend.sim.requests[-1][1]['actions'].shape == (13, 7)
    assert_public(value)
    manifest = json.loads((backend.frame_workspace / value['frame_sequence']['manifest_path']).read_text())
    from services.controller.recording import IMAGE_NAME
    names = [Path(item['path']).name for f in manifest['frames'] for item in f['files']]
    assert all(IMAGE_NAME.fullmatch(name) for name in names) and 'frame_000002_robot0_eye_in_hand_depth.png' in names
    evaluation = backend.evaluate()
    assert evaluation['task_complete'] is False and evaluation['evaluation_kind'] == 'binary_task_completion'
    point = backend.call('libero_pixel_to_position',
                         {'observation_step_id': 13, 'selections': [{'camera': 'agentview', 'u': 4, 'v': 4}]})[0]
    assert point['selections'][0]['valid'] and point['selections'][0]['frame'] == 'world'
    with pytest.raises(RejectedBeforeAction, match='Stale'):
        backend.call('libero_pixel_to_position',
                     {'observation_step_id': 12, 'selections': [{'camera': 'agentview', 'u': 4, 'v': 4}]})
    status, _ = backend.call('libero_status', {})
    assert status['action_layout']['dim'] == 7 and status['state_layout']['dim'] == 8
    assert status['instruction'] == 'put the bowl on the plate'
    assert_public(status)


def test_backend_rgb_only_removes_depth_and_calibration(tmp_path):
    backend = backend_for(tmp_path, 'rgb-only')
    value, images = backend.start(formal=False, seed=0)
    assert len(images) == 2 and backend.sim.collection == 0
    text = json.dumps(value)
    assert 'camera_intrinsics' not in text and 'camera_poses' not in text and '"depth"' not in text
    assert 'libero_pixel_to_position' not in {t['name'] for t in backend.tools()}


def test_backend_reports_success_and_episode_end(tmp_path):
    backend = backend_for(tmp_path, success=True)
    backend.start(formal=False, seed=0)
    value, _ = backend.call('libero_step', {'actions': [[0.0] * 7] * 4})
    assert value['transition']['episode_ended'] is True and value['transition']['task_complete'] is True
    assert value['step_id'] == 1
    assert backend.evaluate()['task_complete'] is True


def test_gateway_lists_and_routes_libero_tools(tmp_path):
    from services.controller.gateway import Gateway
    backend = backend_for(tmp_path)
    gateway = Gateway(backend, None, contract=backend.contract, audit=lambda *_: None, charge=lambda *_: None,
                      refund=lambda *_: None, budget=lambda: {})
    token = gateway.acquire()
    names = {t['name'] for t in gateway.handle(token, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']}
    assert LIBERO_PRO_TOOLS <= names and not any(n.startswith(('humanoid_', 'dexjoco_')) for n in names)
    backend.start(formal=False, seed=0)
    reply = gateway.handle(token, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                   'params': {'name': 'libero_step', 'arguments': {'actions': [[0.0] * 7] * 2}}})
    assert json.loads(reply['result']['content'][0]['text'])['step_id'] == 2


def test_dashboard_lists_libero_tasks_and_cameras():
    from services.dashboard.sessions import CAMERAS, benchmark_of, timeouts
    assert benchmark_of('libero_spatial_3') == 'libero_pro'
    assert timeouts('libero_object_0')['native_step_limit'] == 280
    assert {'agentview', 'robot0_eye_in_hand'} <= set(CAMERAS)
    from services.mcp_contract import GEOMETRY_KEYS
    assert {'agentview_depth_m', 'robot0_eye_in_hand_depth_m'} <= GEOMETRY_KEYS


# -------------------------------------------------------------- separation
def test_host_modules_never_import_the_simulator_stack():
    """Every host-side module imports with libero, robosuite, mujoco, torch, bddl, gym and h5py unavailable."""
    code = r'''
import importlib, sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'libero', 'robosuite', 'mujoco', 'torch', 'bddl', 'gym', 'h5py', 'robomimic'}:
            raise ImportError('blocked ' + name)
sys.meta_path.insert(0, Block())
for module in ('services.benchmarks', 'services.mcp_contract', 'services.libero_pro.benchmark',
               'services.libero_pro.tools', 'services.libero_pro.tasks', 'services.libero_pro.process',
               'services.libero_pro.backend', 'services.libero_pro.task_rules',
               'services.libero_pro.demonstrations', 'services.controller.backend', 'services.controller.gateway',
               'services.controller.frontend', 'services.controller.supervisor', 'services.workspace.build'):
    importlib.import_module(module)
print('ok')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'ok', result.stderr


@pytest.mark.parametrize('module', SIMULATOR_MODULES)
def test_simulator_side_modules_are_python_3_8(module):
    """The simulator runs in LIBERO's Python 3.8: no newer syntax, and none of the newer APIs that
    fail only at run time."""
    source = (ROOT / module).read_text()
    ast.parse(source, feature_version=(3, 8))
    for pattern in (r'\.removeprefix\(', r'\.removesuffix\(', r'\.is_relative_to\(', r'functools\.cache\b',
                    r'@cache\b', r'zip\([^)]*strict=', r'BooleanOptionalAction', r'format_exception\(\s*exc\s*\)',
                    r'^\s*match\s+\S.*:\s*$'):
        lines = [line for line in source.splitlines()
                 if re.search(pattern, line) and not line.lstrip().startswith('#')]
        assert not lines, (module, pattern, lines)
