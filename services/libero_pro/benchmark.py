"""LIBERO-PRO (Franka Panda + parallel gripper, LIBERO's official environment) hooks for the shared
harness; see services/benchmarks.py.

Import-light: the host never imports libero, robosuite or mujoco, and task, rule and demonstration
modules load inside the hooks.
"""
from pathlib import Path


def tool_definitions():
    from services.libero_pro.tools import tool_definitions
    return tool_definitions()


def backend(config):
    from services.libero_pro.backend import LiberoProBackend
    return LiberoProBackend(config)


def task_names():
    from services.libero_pro.tasks import task_names
    return task_names()


def task_timeouts(task):
    from services.libero_pro.tasks import task_timeouts
    return task_timeouts(task)


def validate_config(config):
    from services.libero_pro.tasks import (AGENT_FORMAL_ONLY, COLLECTION_NAMES, EXPLORATION_COLLECTION,
                                           FORMAL_COLLECTIONS, SWAP_EXPLORATION_COLLECTION, official_task)
    from services.libero_pro.task_rules import TASK_RULES
    official_task(config.task)
    if config.eval_seed not in (EXPLORATION_COLLECTION, SWAP_EXPLORATION_COLLECTION):
        raise ValueError('LIBERO-PRO explores fresh layouts of the original task (collection 0) or, in the '
                         'optional second stage, of the swap perturbation (collection 6)')
    if config.formal_collection not in FORMAL_COLLECTIONS:
        raise ValueError('LIBERO-PRO formal collections are 1 (official LIBERO), 2 (swap), 3 (object), '
                         '4 (language) and 5 (task)')
    if config.formal_collection in AGENT_FORMAL_ONLY and not config.agent_formal:
        raise ValueError(f'The LIBERO-PRO {COLLECTION_NAMES[config.formal_collection]} perturbation changes the goal; '
                         'it is scored only with agent formal copies, which read the new instruction')
    if config.task_rules not in TASK_RULES:
        raise ValueError(f"LIBERO-PRO task_rules must be one of {', '.join(TASK_RULES)}")


def validate_settings(values):
    """Dashboard setup values (fixed fields already applied): formal plays official init states."""
    from services.libero_pro.tasks import FORMAL_LAYOUTS
    if values['formal_seed'] + values['formal_episodes'] > FORMAL_LAYOUTS:
        raise ValueError(f'LIBERO-PRO has {FORMAL_LAYOUTS} official init states per task and collection: '
                         'first formal layout + formal episodes must be at most that')


def validate_layouts(config):
    from services.libero_pro.tasks import validate_layouts as libero_layouts
    libero_layouts(config.task, config.formal_collection,
                   range(config.formal_seed, config.formal_seed + config.formal_episodes))
    libero_layouts(config.task, config.eval_seed, config.exploration_seeds)


def exploration_seeds(task, collection, start, episodes, **_):
    """Exploration layouts are fresh seeded resets, never a stored official init state."""
    return tuple(range(start, start + episodes))


def task_context(config, demonstration, *, live_formal):
    from services.libero_pro.task_rules import task_context
    return task_context(config.task, task_rules=config.task_rules, live_formal=live_formal,
                        evaluation=not config.agent_formal,
                        collection=config.formal_collection if live_formal else config.eval_seed)


def refresh_workspace(config, workspace):
    """Episode reviews are unavailable for LIBERO-PRO; the instructions never change after deploy."""


def reference_mounts(config):
    """No expert episodes are mounted for LIBERO-PRO."""
    return []


def fetch_demonstration(demos_root, task, kind):
    if kind != 'none':
        from services.libero_pro.demonstrations import cached_experts
        cached_experts(Path(demos_root)/'libero_pro', task)  # installed by setup_libero_pro_demos.py


def provision_demonstration(config, target, demos_root):
    from services.libero_pro.demonstrations import provision_trial_demonstration
    return provision_trial_demonstration(cache_root=Path(demos_root)/'libero_pro', target=target,
                                         task=config.task, context_kind=config.demonstration_context)


def episode_reviewer(root):
    """LIBERO-PRO has no episode reviews (fixed off)."""
    return None


def review_analysis():
    return None


def unscoped_call(name, arguments):
    raise ValueError(f'{name!r} is not a LIBERO-PRO unscoped tool')
