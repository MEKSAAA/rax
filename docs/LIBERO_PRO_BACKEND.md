# LIBERO-PRO backend

This branch runs auto-research sessions on [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO)
and [LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO) (arXiv 2510.03827): LIBERO's four
ten-task suites (spatial, object, goal, libero_10) with a Franka Panda arm and a parallel-jaw
gripper in robosuite and MuJoCo, evaluated on LIBERO's official init states and under
LIBERO-PRO's perturbations of object positions, object appearance, instructions and goals. The
harness, workspace generator, supervisor, gateway, formal batches, agent formal and dashboard
are the shared ones described in [Architecture](ARCHITECTURE.md); this document covers what is
specific to LIBERO-PRO.

## Principle: the official LIBERO environment, unchanged

The pinned LIBERO-PRO checkout (`LIBERO_PRO_COMMIT` in `dependencies.lock`, linked at
`LIBERO-PRO/`) is never modified, and the perturbation files are LIBERO-PRO's release on Hugging
Face (`LIBERO_PRO_DATA_REVISION`). The environment, robot, controller, physics, rendering and
success check are LIBERO's own `OffScreenRenderEnv`. The harness reaches it through its own
simulator RPC:

| Layer | Interpreter | LIBERO dependencies |
| --- | --- | --- |
| Agent container | the offline agent image `rax-auto-research:dev` | none |
| Host harness (frontend, supervisor, gateway, workspace generator, dashboard) | `runtime/envs/host` | none: `services/libero_pro/{benchmark,backend,process,tools,tasks,task_rules,demonstrations}.py` use the standard library, msgpack and NumPy |
| Simulator (`python -m services.libero_pro.server`) | `LIBERO_PRO_PYTHON` (default `runtime/envs/libero_pro/bin/python`: Python 3.8.13 and the official `requirements.txt`) | the checkout's `libero` package on `PYTHONPATH` |

The upstream workflow modifies its own checkout: the README moves the perturbation files into
the package, and the first `import libero` asks for paths with `input()`. Instead
`scripts/setup_libero_pro_data.py` builds `runtime/libero-pro/`: a `config/config.yaml` that
`LIBERO_CONFIG_PATH` points at, and `bddl_files/` and `init_files/` trees that link the
checkout's original suites and the downloaded perturbed suites. The package is imported from
the checkout on `PYTHONPATH` rather than installed with `pip install -e .`, which would write
egg-info into it. LIBERO-PRO's runtime perturbation generator (`perturbation.py`) is never
used: it is unseeded and writes into the package.

The simulator side (`services/simulator/`, and `services/libero_pro/{session,server,checks,
demo_tools,tasks}.py`) must stay valid Python 3.8.
`local_tests/unit/test_libero_pro_backend.py` checks that syntax, and imports every host module
with libero, robosuite and MuJoCo blocked.

The additions to the official environment are all outside the task:

- MuJoCo is pinned (`LIBERO_PRO_MUJOCO_VERSION`). Upstream leaves it open, and newer MuJoCo
  releases change how LIBERO's init states settle.
- torch is the CPU build of the official 1.11.0. The simulator only loads init states with it.
- msgpack serves the RPC.
- CMake builds robomimic's `egl_probe`.

MuJoCo renders headless through NVIDIA EGL on the session's simulator GPU
(`MUJOCO_EGL_DEVICE_ID`, set by `services/libero_pro/process.py`); the simulator needs no
container.

## Tasks, layouts and scoring

Each official task is one harness task, `<suite>_<index>` in the official suite order:
`libero_spatial_0..9`, `libero_object_0..9`, `libero_goal_0..9` and `libero_10_0..9`.
LIBERO-PRO's perturbations are **layout collections** of the same task, not separate tasks:

| Collection | Use | Layouts |
| --- | --- | --- |
| 0 | exploration | fresh resets of the original BDDL: `env.seed(1000 + i)`, then `env.reset()`. These follow the official placement distribution and are never a stored evaluation state |
| 1 | formal | official LIBERO: the original BDDL and its 50 official init states |
| 2 | formal | swap: LIBERO-PRO's position perturbation, in which objects exchange their initial regions |
| 3 | formal | object: LIBERO-PRO's object perturbation, with appearance and size variants. The instruction is unchanged |
| 4 | formal | language: LIBERO-PRO's language perturbation, a paraphrased instruction |
| 5 | formal | task: LIBERO-PRO's task perturbation, a new goal in the same scene (agent formal only) |
| 6 | exploration | swap exploration, for the [second stage](#optional-second-stage-adapting-to-the-swap-perturbation): fresh resets of the swap BDDL, `env.seed(2000 + i)` |

LIBERO-PRO's environment perturbation was never released and has no collection.

A session explores collection 0 and plays its formal on `formal_eval_seed` (default 1). The
frozen bundle of a finished session is scored on other collections by post-hoc formal batches,
in parallel:

```bash
PYTHONPATH=. runtime/envs/host/bin/python -m services.controller.parallel_batch \
    --source-session <session> --bundle <bundle-id> \
    --output runtime/auto-research/<new-directory> --episodes 50 --workers 4 --eval-seed 2
```

`services.controller.batch` takes the same `--source-session`, `--bundle`, `--output`,
`--episodes` and `--eval-seed` and plays the episodes one at a time. The task perturbation
changes the goal, so a bundle written for the original goal fails it by construction.
Collection 5 is therefore accepted only with agent formal, whose copies read the new
instruction. `TASK.md` says that formal plays layouts the agent has not seen and that they may
swap objects (an object where another usually stands, or on top of another), so a controller
locates every object from its observations. It never names the collection a batch plays.
Exploration plays fresh layouts of the original task only, so it never shows a swapped layout.

- **Episode start** is the public evaluation runners' (openvla-oft `run_libero_eval.py`, openpi
  `examples/libero`): `env.seed`, `env.reset()`, `set_init_state` of the official state (formal),
  then 10 no-op steps `[0, 0, 0, 0, 0, 0, -1]` for objects to settle. A formal layout is
  reproduced exactly (`services/libero_pro/checks.py --determinism`).
- **Step limits** are the runners' per-suite limits: spatial 220, object 280, goal 300 and
  libero_10 520 control steps. The episode ends at once when `env.step` reports done, which
  in LIBERO means the BDDL goal holds. The official evaluation is synchronous already, so
  there is no timing deviation. Each episode's wall-clock limit is 600 s plus 5 s per control
  step.
- **Scoring** is binary (`native_score` 1.0 or 0.0, `graded_score=False`), reported per task and
  collection. A suite's success rate is the mean over its ten tasks.

**Instruction.** The public runners take the instruction from the BDDL file name
(`grab_language_from_filename`). As a result, the language perturbation never reaches the
policy, and the task perturbation scores a new goal while instructing the old one (upstream
issue #14). The harness uses the instruction the BDDL defines (`ControlEnv.language_instruction`),
as the maintainer suggests. Results on collections 4 and 5 are therefore not comparable with
the LIBERO-PRO paper's; results on collections 1 to 3 are.

## Interface

| Tool | Official counterpart |
| --- | --- |
| `libero_step` | rows of the official 7-D action, robosuite OSC_POSE with `control_delta`: `[dx, dy, dz, drx, dry, drz, gripper]` in [-1, 1]. An input of 1.0 moves 0.05 m or turns 0.5 rad (world frame, axis-angle); the gripper value -1 opens and +1 closes. 1 to 100 rows per call at 20 Hz; the call stops when the episode ends. Rows outside [-1, 1] are rejected before acting (robosuite would clip them) |
| `libero_observe` | the cameras the runners use, `agentview` and `robot0_eye_in_hand`, at 256x256, flipped upright (robosuite renders OpenGL-style). Also the runners' 8-D state (end-effector position, axis-angle orientation, two finger positions) and the instruction. In the `rgbd` profile, also depth (`camera_depths`, through robosuite's `get_real_depth_map`) and world-frame calibration |
| `libero_pixel_to_position` | depth back-projection of 1 to 32 pixels into world-frame points (`rgbd` only) |
| `libero_status` | layouts, cameras, instruction, limits, and the action and state layouts |

There is no motion planner and no hold tool: LIBERO's only idle row is the runners' wait row,
and a zero row targets the measured pose. The public VLA runners rotate images by 180 degrees
to match their training data, which mirrors them left to right on top of the upright flip. The
agent sees the upright camera view, consistent with the calibration. A step reply carries the
final frame, and every 5th frame of the motion is saved.

## Demonstrations

`scripts/setup_libero_pro_demos.py --gpu <gpu> --task <task>` downloads the task's file of
LIBERO's official human demonstrations (`yifengzhu-hf/LIBERO-datasets` at
`LIBERO_DATASETS_REVISION`). It replays candidates in the official environment from their
recorded start states (`services/libero_pro/demo_tools.py`), in a random order seeded by the
task name, and keeps the first three that succeed. They are installed under
`runtime/reference-demos/libero_pro/<task>/`. Their start layouts are not compared with the
formal layouts: in libero_object the official layouts lie within millimetres of each other, so
every demonstration starts next to one.

- `demonstration_context: terminal_state` (the default) gives the agent each demonstration's
  final third-person frame.
- `completion_sequence` gives the ordered keyframes rendered during the replay.

Complete expert episodes are not available (`expert_demonstrations` is fixed at 0).

## Optional second stage: adapting to the swap perturbation

The first stage explores the original task only, so its swap score measures robustness to an
unseen perturbation. The optional second stage continues a finished first-stage session on the
swap perturbation itself, as ASPIRE (NVlabs) develops on its `_swap` suites. It is an
adaptation session (`services/adaptation.py`) with:

- the same task;
- `--exploration-collection 6`: fresh seeded resets of the swap BDDL, seeds 2000 and up,
  never its official init states;
- a small episode budget;
- `--adaptation-source <first-stage session>`;
- `--adaptation-context resume`, so code, memory and the whole conversation carry over;
- formal on collection 2, the same 50 held-out swap init states the first stage was scored on.

`TASK.md` then says that both exploration and formal layouts swap objects. Afterwards, score the
official layouts (collection 1) with a post-hoc batch to check that the adapted bundle did not
regress.

```bash
runtime/envs/host/bin/python scripts/configure_auto_research.py \
    --task libero_goal_1 --sim-gpu 0 --episodes 5 --exploration-collection 6 --formal-eval-seed 2 \
    --adaptation-source runtime/auto-research/<first-stage-session> --adaptation-context resume
```

## Not available

The following are not wired for LIBERO-PRO:

- **Episode reviews:** `episode_reviews` is fixed off.
- **Complete expert episodes:** `expert_demonstrations` is fixed at 0.
- **Robot-only preview, motion planner and rotation-math tool:** none exists for this backend.
- **Task rules other than `public`:** `task_rules` is fixed to `public`. `TASK.md` gives the
  instruction and the swap warning, never the success check.
- **Transfer handoff:** `write_handoff` is fixed off, because the second stage carries code,
  memory and the conversation instead.

## Setup

```bash
bash scripts/bootstrap_sources.sh                          # LIBERO-PRO/ at LIBERO_PRO_COMMIT
bash scripts/setup_libero_pro_env.sh   # runtime/envs/libero_pro
runtime/envs/host/bin/python scripts/setup_libero_pro_data.py
runtime/envs/host/bin/python scripts/setup_libero_pro_demos.py --gpu 0 --task libero_spatial_0
runtime/envs/host/bin/python scripts/libero_pro_doctor.py --gpu 0 --task libero_spatial_0
```

The doctor works per task and collection: by default on an exploration layout and an official
init state, and `--collection` chooses others. It checks:

- the observation contract;
- the instruction;
- a zero row and a short motion;
- depth back-projection of every object's true position;
- the operator files;
- the simulator-side checks in `services/libero_pro/checks.py`:
  - `--collections`: every collection's BDDL and init states load;
  - `--determinism`: a formal layout is rebuilt exactly;
  - `--characterize`: the controller's measured response per unit row;
  - `--describe`: the robot base, end-effector and camera poses.

See [Setup](SETUP.md) for the full installation.
