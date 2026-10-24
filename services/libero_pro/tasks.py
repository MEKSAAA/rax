"""LIBERO-PRO task catalog, layout collections and budgets (stdlib only; Python 3.8 compatible).

Each task of LIBERO's four ten-task suites (spatial, object, goal, libero_10) is one harness task,
``<suite>_<index>`` in the official suite order. LIBERO-PRO's perturbations are not separate tasks:
they are formal layout collections of the same task, so one exploration session is scored on the
official LIBERO layouts and, by post-hoc formal batches, under each perturbation.

- Collection 0 (exploration): the original BDDL, ``env.seed(1000 + i)`` then ``env.reset()``: fresh
  samples of the official placement distribution, never one of the stored evaluation states.
- Collection 1 (official LIBERO): the original BDDL and its 50 official init states.
- Collections 2-5 (LIBERO-PRO): the perturbed BDDL and its 50 init states from the official
  perturbation release: 2 swap (objects exchange initial regions), 3 object (object appearance and
  size variants), 4 language (paraphrased instruction), 5 task (a new goal in the same scene).

The environment perturbation was never released and has no collection.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

EXPLORATION_COLLECTION, OFFICIAL_COLLECTION = 0, 1
SWAP_COLLECTION, OBJECT_COLLECTION, LANGUAGE_COLLECTION, TASK_COLLECTION = 2, 3, 4, 5
# Optional second stage (adaptation to the swap perturbation): exploration on fresh seeded layouts of
# the swap BDDL, never its official init states, which formal collection 2 holds out.
SWAP_EXPLORATION_COLLECTION = 6
# Formal collection -> suffix of the official suite directory (bddl_files/<suite><suffix>).
COLLECTION_SUFFIX = {OFFICIAL_COLLECTION: "", SWAP_COLLECTION: "_swap", OBJECT_COLLECTION: "_object",
                     LANGUAGE_COLLECTION: "_lan", TASK_COLLECTION: "_task"}
COLLECTION_NAMES = {EXPLORATION_COLLECTION: "exploration", OFFICIAL_COLLECTION: "official",
                    SWAP_COLLECTION: "swap", OBJECT_COLLECTION: "object", LANGUAGE_COLLECTION: "language",
                    TASK_COLLECTION: "task", SWAP_EXPLORATION_COLLECTION: "swap exploration"}
# Exploration collection -> (suite directory suffix, reset seed base); its layouts are fresh resets.
EXPLORATION_COLLECTIONS = {EXPLORATION_COLLECTION: ("", 1000), SWAP_EXPLORATION_COLLECTION: ("_swap", 2000)}
FORMAL_COLLECTIONS = tuple(COLLECTION_SUFFIX)
# The task perturbation changes the goal: a frozen bundle written for the original goal fails it by
# construction, so it is scored only by agent formal copies, which read the new instruction.
AGENT_FORMAL_ONLY = frozenset({TASK_COLLECTION})
FORMAL_LAYOUTS = 50  # Official init states per task and collection.
EXPLORATION_SEED_BASE = EXPLORATION_COLLECTIONS[EXPLORATION_COLLECTION][1]
COLLECTIONS_ERROR = ("LIBERO-PRO layout collections are 0 (exploration), 6 (swap exploration) and "
                     + ", ".join(map(str, FORMAL_COLLECTIONS)) + " (formal)")
MAX_EXPLORATION_LAYOUTS = 1000

# Official suite order (libero/libero/benchmark/libero_suite_task_map.py) and the step limits the
# public evaluation runner uses per suite (openvla-oft run_libero_eval.py TASK_MAX_STEPS).
SUITES = {
    "libero_spatial": (220, (
        "pick_up_the_black_bowl_between_the_plate_and_the_ramekin_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_next_to_the_ramekin_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_from_table_center_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_on_the_cookie_box_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_in_the_top_drawer_of_the_wooden_cabinet_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_on_the_ramekin_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_next_to_the_cookie_box_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_on_the_stove_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_next_to_the_plate_and_place_it_on_the_plate",
        "pick_up_the_black_bowl_on_the_wooden_cabinet_and_place_it_on_the_plate")),
    "libero_object": (280, (
        "pick_up_the_alphabet_soup_and_place_it_in_the_basket",
        "pick_up_the_cream_cheese_and_place_it_in_the_basket",
        "pick_up_the_salad_dressing_and_place_it_in_the_basket",
        "pick_up_the_bbq_sauce_and_place_it_in_the_basket",
        "pick_up_the_ketchup_and_place_it_in_the_basket",
        "pick_up_the_tomato_sauce_and_place_it_in_the_basket",
        "pick_up_the_butter_and_place_it_in_the_basket",
        "pick_up_the_milk_and_place_it_in_the_basket",
        "pick_up_the_chocolate_pudding_and_place_it_in_the_basket",
        "pick_up_the_orange_juice_and_place_it_in_the_basket")),
    "libero_goal": (300, (
        "open_the_middle_drawer_of_the_cabinet",
        "put_the_bowl_on_the_stove",
        "put_the_wine_bottle_on_top_of_the_cabinet",
        "open_the_top_drawer_and_put_the_bowl_inside",
        "put_the_bowl_on_top_of_the_cabinet",
        "push_the_plate_to_the_front_of_the_stove",
        "put_the_cream_cheese_in_the_bowl",
        "turn_on_the_stove",
        "put_the_bowl_on_the_plate",
        "put_the_wine_bottle_on_the_rack")),
    "libero_10": (520, (
        "LIVING_ROOM_SCENE2_put_both_the_alphabet_soup_and_the_tomato_sauce_in_the_basket",
        "LIVING_ROOM_SCENE2_put_both_the_cream_cheese_box_and_the_butter_in_the_basket",
        "KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it",
        "KITCHEN_SCENE4_put_the_black_bowl_in_the_bottom_drawer_of_the_cabinet_and_close_it",
        "LIVING_ROOM_SCENE5_put_the_white_mug_on_the_left_plate_and_put_the_yellow_and_white_mug_on_the_right_plate",
        "STUDY_SCENE1_pick_up_the_book_and_place_it_in_the_back_compartment_of_the_caddy",
        "LIVING_ROOM_SCENE6_put_the_white_mug_on_the_plate_and_put_the_chocolate_pudding_to_the_right_of_the_plate",
        "LIVING_ROOM_SCENE1_put_both_the_alphabet_soup_and_the_cream_cheese_box_in_the_basket",
        "KITCHEN_SCENE8_put_both_moka_pots_on_the_stove",
        "KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it")),
}
# The public runners' 10 no-op steps (gripper open) before the policy acts, for objects to settle.
WAIT_STEPS = 10
WAIT_ACTION = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0)
CONTROL_HZ = 20  # robosuite control_freq of LIBERO's OffScreenRenderEnv.
IMAGE_SIZE = 256  # The public runners render 256x256.
CAMERAS = ("agentview", "robot0_eye_in_hand")
ACTION_DIM, STATE_DIM = 7, 8
# Every FRAME_STRIDE-th executed step is saved as a frame (plus the final step).
FRAME_STRIDE = 5
# robosuite OSC_POSE (osc_pose.json): an input of 1 moves 0.05 m or turns 0.5 rad.
POSITION_SCALE_M, ROTATION_SCALE_RAD = 0.05, 0.5


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    base = os.environ.get("LIBERO_PRO_RUNTIME_ROOT") or os.environ.get("RAX_RUNTIME_ROOT")
    return Path(base or project_root() / "runtime").resolve()


def source_root() -> Path:
    """The pinned official checkout (scripts/bootstrap_sources.sh)."""
    return Path(os.environ.get("LIBERO_PRO_SOURCE") or project_root() / "LIBERO-PRO").resolve()


def data_root() -> Path:
    """The runtime-owned LIBERO config and data tree (scripts/setup_libero_pro_data.py)."""
    return Path(os.environ.get("LIBERO_PRO_DATA") or runtime_root() / "libero-pro").resolve()


def task_names():
    return [f"{suite}_{index}" for suite, (_, tasks) in SUITES.items() for index in range(len(tasks))]


def official_task(task):
    """Harness task -> (suite, index, official task name)."""
    match = re.fullmatch(r"(libero_(?:spatial|object|goal|10))_(\d)", str(task))
    if match and match.group(1) in SUITES and int(match.group(2)) < len(SUITES[match.group(1)][1]):
        suite, index = match.group(1), int(match.group(2))
        return suite, index, SUITES[suite][1][index]
    raise ValueError(f"Unknown LIBERO-PRO task: {task!r}; tasks are <suite>_<0-9> for suites "
                     f"{', '.join(SUITES)}")


def native_step_limit(task):
    return SUITES[official_task(task)[0]][0]


def suite_directory(task, collection):
    """The official suite directory of a collection (exploration uses the original suite, swap
    exploration the swap suite)."""
    suite = official_task(task)[0]
    if collection in EXPLORATION_COLLECTIONS:
        return suite + EXPLORATION_COLLECTIONS[collection][0]
    if collection not in COLLECTION_SUFFIX:
        raise ValueError(COLLECTIONS_ERROR)
    return suite + COLLECTION_SUFFIX[collection]


def bddl_path(task, collection):
    suite, _, name = official_task(task)
    return data_root() / "bddl_files" / suite_directory(task, collection) / f"{name}.bddl"


def init_states_path(task, collection):
    if collection in EXPLORATION_COLLECTIONS:
        raise ValueError("Exploration layouts are fresh resets, not stored init states")
    _, _, name = official_task(task)
    return data_root() / "init_files" / suite_directory(task, collection) / f"{name}.pruned_init"


def bddl_language(text):
    """The instruction a BDDL file defines in ``(:language ...)`` exactly as LIBERO reads it
    (``ControlEnv.language_instruction``: bddl's scan_tokens drops comments and lowercases, and the
    tokens are joined by single spaces)."""
    text = re.sub(r";.*$", "", text, flags=re.MULTILINE).lower()
    match = re.search(r"\(\s*:language\s+([^()]*)\)", text)
    if not match:
        raise ValueError("BDDL file has no (:language ...) entry")
    return " ".join(match.group(1).split())


def instruction(task, collection=EXPLORATION_COLLECTION):
    """The task's instruction in a collection, from its BDDL (not the file name: the public runners'
    file-name instruction misses the language and task perturbations)."""
    return bddl_language(bddl_path(task, collection).read_text())


def task_info(task):
    suite, index, name = official_task(task)
    return {"task": task, "suite": suite, "index": index, "official_name": name,
            "native_step_limit": SUITES[suite][0]}


def layout(task, collection, layout_id):
    """A harness layout -> how the simulator builds it."""
    official_task(task)
    if not isinstance(layout_id, int) or isinstance(layout_id, bool) or layout_id < 0:
        raise ValueError(f"layout ids are nonnegative integers, got {layout_id!r}")
    if collection in EXPLORATION_COLLECTIONS:
        if layout_id >= MAX_EXPLORATION_LAYOUTS:
            raise ValueError(f"exploration layouts are 0..{MAX_EXPLORATION_LAYOUTS - 1}")
        return {"kind": "reset", "seed": EXPLORATION_COLLECTIONS[collection][1] + layout_id}
    if collection in COLLECTION_SUFFIX:
        if layout_id >= FORMAL_LAYOUTS:
            raise ValueError(f"formal layout {layout_id} is outside the {FORMAL_LAYOUTS} official init states "
                             f"(0..{FORMAL_LAYOUTS - 1})")
        # As the public runner: env.seed(0), reset, then the official init state.
        return {"kind": "init_state", "seed": 0, "init_index": layout_id}
    raise ValueError(COLLECTIONS_ERROR)


def validate_layouts(task, collection, layout_ids):
    for layout_id in layout_ids:
        layout(task, collection, layout_id)


# Wall-clock model: steps are synchronous and fast; agents think between calls.
STARTUP_SECONDS = 300
EPISODE_BASE_SECONDS = 600
SECONDS_PER_STEP = 5


def task_timeouts(task):
    steps = native_step_limit(task)
    return {"native_step_limit": steps, "episode_seconds": EPISODE_BASE_SECONDS + steps * SECONDS_PER_STEP,
            "startup_seconds": STARTUP_SECONDS}
