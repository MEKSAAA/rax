"""RoboWits hooks for the unmodified RAX exploration/submission lifecycle."""
from . import tasks as T


def tool_definitions():
    from .tools import tool_definitions
    return tool_definitions()


def backend(config):
    from .backend import RoboWitsBackend
    return RoboWitsBackend(config)


def task_names(): return T.task_names()
def task_timeouts(task): return T.task_timeouts(task)

def validate_config(config):
    T.task_info(config.task)
    if config.eval_seed != 0 or config.formal_collection != 1:
        raise ValueError('RoboWits explores random resets (0) and evaluates official JSON layouts (1)')
    if config.demonstration_context != 'none':
        raise ValueError('RoboWits integration uses no demonstrations')


def validate_settings(values):
    if values['formal_seed'] + values['formal_episodes'] > T.task_info(values['task'])['formal_layouts']:
        raise ValueError('Requested formal episodes exceed released layouts')


def validate_layouts(config):
    T.validate_layouts(config.task, config.eval_seed, config.exploration_seeds)
    T.validate_layouts(config.task, config.formal_collection, range(config.formal_seed, config.formal_seed+config.formal_episodes))


def exploration_seeds(task, collection, start, episodes, **_): return tuple(range(start,start+episodes))

def task_context(config, demonstration, *, live_formal):
    return f'''# Task: {config.task}

**Instruction:** {T.instruction(config.task)}

Use the current camera observations to solve this task. Two 7-DoF arms share a table.
The native benchmark's binary success checker is authoritative. There is no partial credit.
Each episode has a limit of {T.native_step_limit(config.task)} control steps at approximately 30 Hz.
Exploration uses randomly sampled placements, and evaluation uses disjoint released scene layouts.
Object placement varies; perceive the scene again each episode. No demonstration is provided.
Only robot state and calibrated RGB-D sensors are exposed, never object ground-truth poses.
Read the robot-interface skill for the absolute EE action order and coordinate convention.
'''


def refresh_workspace(config, workspace): pass
def reference_mounts(config): return []
def fetch_demonstration(demos_root, task, kind):
    if kind != 'none': raise ValueError('No RoboWits demonstration context supported')
def provision_demonstration(config, target, demos_root): return {'kind':'none','image_count':0}
def episode_reviewer(root): return None
def review_analysis(): return None
def unscoped_call(name, arguments): raise ValueError(f'Unknown RoboWits unscoped tool {name}')
