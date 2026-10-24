"""Simulator-side LIBERO-PRO checks (runs in runtime/envs/libero_pro; prints one JSON line).

  python -m services.libero_pro.checks --task libero_spatial_0 [--collections] [--determinism]
                                       [--characterize] [--describe]

--collections   every collection's official BDDL and init states load; the instruction each gives;
                success at the start (must be false); object displacement over the settling steps.
--determinism   a formal layout rebuilt in a fresh environment reproduces the same simulator state, and
                an exploration seed reproduces its layout; exploration layouts differ from every
                official init state of collection 1.
--characterize  the controller's measured response: position and rotation per unit row, the zero-row
                drift, the gripper's closing and opening time.
--describe      robot base, start end-effector pose, camera poses and fields of view.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from services.libero_pro import tasks as T
from services.libero_pro.session import matrix_to_quat_wxyz, quat_xyzw_to_axis_angle


def build(task, collection):
    from libero.libero.envs import OffScreenRenderEnv

    return OffScreenRenderEnv(bddl_file_name=str(T.bddl_path(task, collection)), camera_heights=T.IMAGE_SIZE,
                              camera_widths=T.IMAGE_SIZE)


def init_states(task, collection):
    import torch

    return torch.load(str(T.init_states_path(task, collection)))


def start(env, layout, states=None):
    env.seed(layout["seed"])
    obs = env.reset()
    if layout["kind"] == "init_state":
        obs = env.set_init_state(np.asarray(states[layout["init_index"]]))
    for _ in range(T.WAIT_STEPS):
        obs, _, _, _ = env.step(list(T.WAIT_ACTION))
    return obs


def object_positions(env):
    inner = env.env
    return {name: np.array(inner.sim.data.body_xpos[inner.obj_body_id[name]]) for name in inner.objects_dict}


def check_collections(task):
    report = {}
    for collection in T.FORMAL_COLLECTIONS:
        entry = {"instruction_file": T.instruction(task, collection)}
        try:
            started = time.monotonic()
            env = build(task, collection)
            entry["build_seconds"] = round(time.monotonic() - started, 2)
            entry["instruction_env"] = str(env.language_instruction)
            states = init_states(task, collection)
            entry["init_states"] = len(states)
            moved, success_at_start = [], []
            for index in (0, len(states) // 2, len(states) - 1):
                env.seed(0)
                env.reset()
                env.set_init_state(np.asarray(states[index]))
                before = object_positions(env)
                for _ in range(T.WAIT_STEPS):
                    env.step(list(T.WAIT_ACTION))
                after = object_positions(env)
                # Init states hold objects at their spawn heights; they drop onto their supports while
                # settling, so only horizontal motion would mean a disturbed layout.
                moved.append(max((float(np.linalg.norm((after[k] - before[k])[:2])), k) for k in before))
                success_at_start.append(bool(env.check_success()))
            entry["max_settle_horizontal_displacement_m"] = round(max(moved)[0], 4)
            entry["most_moved_object"] = max(moved)[1]
            entry["success_at_start"] = success_at_start
            env.close()
        except Exception as exc:  # noqa: BLE001 - report every collection
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report[T.COLLECTION_NAMES[collection]] = entry
    return report


def check_determinism(task):
    report = {}
    states = init_states(task, T.OFFICIAL_COLLECTION)
    layout = T.layout(task, T.OFFICIAL_COLLECTION, 3)
    flats = []
    for _ in range(2):
        env = build(task, T.OFFICIAL_COLLECTION)
        start(env, layout, states)
        for _ in range(5):
            env.step([0.3, 0.0, -0.2, 0.0, 0.0, 0.1, 1.0])
        flats.append(np.asarray(env.get_sim_state()))
        env.close()
    report["formal_rebuild_max_state_difference"] = float(np.max(np.abs(flats[0] - flats[1])))
    exploration = []
    for _ in range(2):
        env = build(task, T.EXPLORATION_COLLECTION)
        start(env, T.layout(task, T.EXPLORATION_COLLECTION, 7))
        exploration.append(object_positions(env))
        env.close()
    report["exploration_seed_max_object_difference_m"] = max(
        float(np.linalg.norm(exploration[0][k] - exploration[1][k])) for k in exploration[0])
    # How far exploration layouts are from the nearest official layout (object positions).
    env = build(task, T.OFFICIAL_COLLECTION)
    official = []
    for index in range(len(states)):
        env.seed(0)
        env.reset()
        env.set_init_state(np.asarray(states[index]))
        official.append(object_positions(env))
    env.close()
    distances = []
    for layout_id in range(5):
        env = build(task, T.EXPLORATION_COLLECTION)
        env.seed(T.layout(task, T.EXPLORATION_COLLECTION, layout_id)["seed"])
        env.reset()
        positions = object_positions(env)
        env.close()
        distances.append(min(max(float(np.linalg.norm(positions[k] - other[k])) for k in positions)
                             for other in official))
    report["exploration_min_distance_to_official_m"] = [round(d, 4) for d in distances]
    return report


def matrix(obs):
    """The end-effector orientation matrix (world frame) from robosuite's xyzw quaternion."""
    x, y, z, w = (float(v) for v in obs["robot0_eef_quat"])
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def rotvec(m):
    angle = float(np.arccos(np.clip((np.trace(m) - 1) / 2, -1.0, 1.0)))
    if angle < 1e-9:
        return np.zeros(3)
    axis = np.array([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1]]) / (2 * np.sin(angle))
    return axis * angle


def eef(obs):
    return np.asarray(obs["robot0_eef_pos"], dtype=np.float64), quat_xyzw_to_axis_angle(obs["robot0_eef_quat"])


def check_characterize(task):
    env = build(task, T.OFFICIAL_COLLECTION)
    states = init_states(task, T.OFFICIAL_COLLECTION)
    layout = T.layout(task, T.OFFICIAL_COLLECTION, 0)
    report = {"position": {}, "rotation": {}}
    for axis, name in enumerate("xyz"):
        for value in (0.25, 1.0):
            obs = start(env, layout, states)
            p0, _ = eef(obs)
            steps = 10
            row = [0.0] * 7
            row[axis], row[6] = value, -1.0
            per_step = []
            for _ in range(steps):
                before, _ = eef(obs)
                obs, _, _, _ = env.step(row)
                after, _ = eef(obs)
                per_step.append(float(after[axis] - before[axis]))
            p1, _ = eef(obs)
            report["position"][f"{name}={value}"] = {
                "first_step_m": round(per_step[0], 5), "steady_step_m": round(float(np.mean(per_step[-5:])), 5),
                "total_m": round(float(p1[axis] - p0[axis]), 4),
                "cross_axis_m": round(float(np.linalg.norm(np.delete(p1 - p0, axis))), 4)}
    for axis, name in enumerate("xyz"):
        for value in (0.25, 1.0):
            obs = start(env, layout, states)
            m0 = matrix(obs)
            row = [0.0] * 7
            row[3 + axis], row[6] = value, -1.0
            per_step = []
            for _ in range(10):
                before = matrix(obs)
                obs, _, _, _ = env.step(row)
                per_step.append(rotvec(matrix(obs) @ before.T))
            total = rotvec(matrix(obs) @ m0.T)
            report["rotation"][f"r{name}={value}"] = {
                "first_step_rad": round(float(per_step[0][axis]), 5),
                "steady_step_rad": round(float(np.mean([v[axis] for v in per_step[-5:]])), 5),
                "total_world_rotvec_rad": [round(float(v), 4) for v in total]}
    obs = start(env, layout, states)
    p0, r0 = eef(obs)
    for _ in range(50):
        obs, _, _, _ = env.step([0.0] * 6 + [-1.0])
    p1, r1 = eef(obs)
    report["zero_row_drift_50_steps"] = {"position_m": round(float(np.linalg.norm(p1 - p0)), 5),
                                         "rotation_rad": round(float(np.linalg.norm(r1 - r0)), 5)}
    obs = start(env, layout, states)
    widths = [float(np.sum(np.abs(obs["robot0_gripper_qpos"])))]
    for _ in range(20):
        obs, _, _, _ = env.step([0.0] * 6 + [1.0])
        widths.append(float(np.sum(np.abs(obs["robot0_gripper_qpos"]))))
    closed = widths[-1]
    report["gripper_close"] = {"open_width_m": round(widths[0], 4), "closed_width_m": round(closed, 4),
                               "steps_to_95_percent": next((i for i, w in enumerate(widths)
                                                            if abs(w - closed) <= 0.05 * abs(widths[0] - closed)), None)}
    for _ in range(20):
        obs, _, _, _ = env.step([0.0] * 6 + [-1.0])
        widths.append(float(np.sum(np.abs(obs["robot0_gripper_qpos"]))))
    report["gripper_open_after_20_steps_m"] = round(widths[-1], 4)
    env.close()
    return report


def check_describe(task):
    env = build(task, T.OFFICIAL_COLLECTION)
    states = init_states(task, T.OFFICIAL_COLLECTION)
    started = time.monotonic()
    obs = start(env, T.layout(task, T.OFFICIAL_COLLECTION, 0), states)
    reset_seconds = time.monotonic() - started
    sim = env.sim
    base = sim.model.body_name2id("robot0_base")
    cameras = {}
    for camera in T.CAMERAS:
        cid = sim.model.camera_name2id(camera)
        cameras[camera] = {"position_m": [round(float(v), 4) for v in sim.data.cam_xpos[cid]],
                           "quaternion_wxyz": [round(v, 4) for v in matrix_to_quat_wxyz(sim.data.cam_xmat[cid])],
                           "fovy_deg": float(sim.model.cam_fovy[cid])}
    started = time.monotonic()
    for _ in range(20):
        env.step([0.0] * 6 + [-1.0])
    step_seconds = (time.monotonic() - started) / 20
    pos, axis_angle = eef(obs)
    report = {"robot_base_position_m": [round(float(v), 4) for v in sim.data.body_xpos[base]],
              "robot_base_quaternion_wxyz": [round(float(v), 4) for v in sim.data.body_xquat[base]],
              "start_eef_position_m": [round(float(v), 4) for v in pos],
              "start_eef_axis_angle_rad": [round(float(v), 4) for v in axis_angle],
              "start_gripper_qpos_m": [round(float(v), 4) for v in obs["robot0_gripper_qpos"]],
              "start_joint_positions_rad": [round(float(v), 4) for v in obs["robot0_joint_pos"]],
              "cameras": cameras, "reset_seconds": round(reset_seconds, 3), "step_seconds": round(step_seconds, 4),
              "instruction": str(env.language_instruction)}
    env.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--collections", action="store_true")
    parser.add_argument("--determinism", action="store_true")
    parser.add_argument("--characterize", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args(argv)
    report = {"task": args.task}
    for flag, function in (("describe", check_describe), ("collections", check_collections),
                           ("determinism", check_determinism), ("characterize", check_characterize)):
        if getattr(args, flag):
            try:
                report[flag] = function(args.task)
            except Exception as exc:  # noqa: BLE001 - report every check
                report[flag] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, default=float), flush=True)


if __name__ == "__main__":
    main()
