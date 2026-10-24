"""One LIBERO-PRO episode inside the simulator process (runs in runtime/envs/libero_pro, Python 3.8).

The official ``OffScreenRenderEnv`` (libero.libero.envs) owns the scene, robot, controller, physics,
rendering and the success check, built from the official BDDL of the layout's collection. Episodes
start as the public LIBERO evaluation runner starts them: ``env.seed``, ``reset``, the official init
state (formal), then 10 no-op steps with the gripper open for objects to settle. The agent's rows go
to ``env.step``; the episode ends when ``step`` reports done (LIBERO: the BDDL goal holds) or at the
suite's step limit. Exploration layouts are fresh seeded resets of the original BDDL.

The session adds the harness contract: identity-checked RPC ops, agent observations (the two
cameras upright at 256x256, optional depth from robosuite's get_real_depth_map and calibration,
the runners' 8-D state, the BDDL instruction) and the trusted operator files. Object poses, the
layout and the collection stay in operator-only files.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import numpy as np

from services.libero_pro import tasks as T
from services.simulator.io import RejectedBeforeAction, action_execution_scope, reject_unless, require, write_json

SCHEMA = "libero_pro_operator_state_v1"
VIDEO_FPS = T.CONTROL_HZ


def quat_xyzw_to_axis_angle(quat):
    """robosuite.utils.transform_utils.quat2axisangle (the runners' state conversion)."""
    q = np.asarray(quat, dtype=np.float64).copy()
    q[3] = min(max(q[3], -1.0), 1.0)
    den = np.sqrt(1.0 - q[3] * q[3])
    if np.isclose(den, 0.0):
        return np.zeros(3)
    return (q[:3] * 2.0 * np.arccos(q[3])) / den


def matrix_to_quat_wxyz(matrix):
    m = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    trace = np.trace(m)
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.asarray(q)
    return (q / np.linalg.norm(q)).tolist()


class LiberoProSession:
    def __init__(self, output, task, collection, *, include_depth):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.task = task
        self.info = T.task_info(task)
        self.collection = int(collection)
        self.suite_directory = T.suite_directory(task, self.collection)
        self.bddl = T.bddl_path(task, self.collection)
        require(self.bddl.is_file(), f"Missing official BDDL {self.bddl}; run scripts/setup_libero_pro_data.py")
        self.episode_mode = "exploration" if self.collection in T.EXPLORATION_COLLECTIONS else "formal"
        self.include_depth = bool(include_depth)
        self.cameras = list(T.CAMERAS)
        self.step_limit = T.native_step_limit(task)
        self.env = None
        self.raw = None  # Last observation dict of the official environment.
        self.instruction = None
        self.episode_id = None
        self.episode_dir = None
        self.layout_id = None
        self.layout = None
        self.step_id = 0
        self.terminated = self.truncated = self.success = False
        self.invalid_reason = None  # set only for a concrete reason, e.g. episode_wall_timeout
        self.finish_reason = None
        self.obs = None
        self.video = None
        self.metadata = {
            "task": task, "benchmark": "libero_pro", "cameras": self.cameras,
            "control_frequency_hz": T.CONTROL_HZ, "native_step_limit": self.step_limit,
            "wait_steps": T.WAIT_STEPS, "action_dim": T.ACTION_DIM, "state_dim": T.STATE_DIM,
            "image_size": T.IMAGE_SIZE, "depth": self.include_depth, "frame_stride": T.FRAME_STRIDE,
            "episode_mode": self.episode_mode,
        }

    # ------------------------------------------------------------- environment
    def _build(self):
        from libero.libero.envs import OffScreenRenderEnv

        self.env = OffScreenRenderEnv(bddl_file_name=str(self.bddl), camera_heights=T.IMAGE_SIZE,
                                      camera_widths=T.IMAGE_SIZE, camera_depths=self.include_depth)
        # The official parse of the BDDL's (:language ...), not the file name.
        self.instruction = str(self.env.language_instruction)

    def _init_state(self, index):
        import torch

        states = torch.load(str(T.init_states_path(self.task, self.collection)))
        require(len(states) == T.FORMAL_LAYOUTS, f"expected {T.FORMAL_LAYOUTS} official init states, got {len(states)}")
        return np.asarray(states[index])

    def _camera_image(self, camera):
        # robosuite renders OpenGL-style (row 0 at the bottom); flip rows to the upright camera view.
        return np.ascontiguousarray(np.asarray(self.raw[f"{camera}_image"])[::-1]).astype(np.uint8)

    def _depth(self, camera):
        from robosuite.utils.camera_utils import get_real_depth_map

        depth = get_real_depth_map(self.env.sim, np.asarray(self.raw[f"{camera}_depth"]))
        return np.ascontiguousarray(np.asarray(depth, dtype=np.float32).reshape(T.IMAGE_SIZE, T.IMAGE_SIZE)[::-1])

    def _calibration(self):
        sim = self.env.sim
        intrinsics, poses = {}, {}
        for camera in self.cameras:
            cid = sim.model.camera_name2id(camera)
            fovy = float(sim.model.cam_fovy[cid])
            focal = (T.IMAGE_SIZE / 2.0) / np.tan(np.deg2rad(fovy) / 2.0)
            intrinsics[camera] = {"matrix": [[focal, 0.0, T.IMAGE_SIZE / 2.0], [0.0, focal, T.IMAGE_SIZE / 2.0],
                                             [0.0, 0.0, 1.0]],
                                  "width": T.IMAGE_SIZE, "height": T.IMAGE_SIZE, "fovy_deg": fovy}
            poses[camera] = {"position_m": [float(v) for v in sim.data.cam_xpos[cid]],
                             "quaternion_wxyz": matrix_to_quat_wxyz(sim.data.cam_xmat[cid]), "frame": "world",
                             "convention": "OpenGL camera: looks along -Z, +Y up; image row 0 at the top"}
        return intrinsics, poses

    def _state(self):
        raw = self.raw
        return [*map(float, raw["robot0_eef_pos"]), *map(float, quat_xyzw_to_axis_angle(raw["robot0_eef_quat"])),
                *map(float, raw["robot0_gripper_qpos"])]

    def _agent_observation(self, *, depth=None):
        depth = self.include_depth if depth is None else depth
        obs = {camera: self._camera_image(camera) for camera in self.cameras}
        obs["state"] = self._state()
        obs["instruction"] = self.instruction
        obs["step_id"] = int(self.step_id)
        if depth:
            for camera in self.cameras:
                obs[f"{camera}_depth_m"] = self._depth(camera)
            obs["camera_intrinsics"], obs["camera_poses"] = self._calibration()
        return obs

    def _object_poses(self):
        """Operator-only: every movable object's world position and quaternion (wxyz)."""
        inner = self.env.env
        poses = {}
        for name in inner.objects_dict:
            body = inner.obj_body_id[name]
            poses[name] = {"position_m": [float(v) for v in inner.sim.data.body_xpos[body]],
                           "quaternion_wxyz": [float(v) for v in inner.sim.data.body_xquat[body]]}
        return poses

    # ----------------------------------------------------------------- episode
    def reset(self, layout_id, source="agent", policy_version="mcp"):
        require(self.episode_id is None, "One fresh episode per process; no reset")
        self.layout_id = int(layout_id)
        self.layout = T.layout(self.task, self.collection, self.layout_id)
        started = time.monotonic()
        self._build()
        # As the public runner: seed, reset, the official init state, then the settling no-ops.
        self.env.seed(self.layout["seed"])
        self.raw = self.env.reset()
        if self.layout["kind"] == "init_state":
            self.raw = self.env.set_init_state(self._init_state(self.layout["init_index"]))
        for _ in range(T.WAIT_STEPS):
            self.raw, _, _, _ = self.env.step(list(T.WAIT_ACTION))
        self.initial_objects = self._object_poses()
        self.episode_id = f"{self.task}_{self.episode_mode}_{self.layout_id}_{uuid.uuid4().hex[:8]}"
        self.episode_dir = self.output / self.episode_id
        self.episode_dir.mkdir(parents=True)
        self.step_id = 0
        self._open_video()
        self.obs = self._agent_observation()
        self._write_video_frame()
        write_json(self.output / "reset.json", {
            "episode_id": self.episode_id, "task": self.task, "official_name": self.info["official_name"],
            "suite_directory": self.suite_directory, "collection": self.collection,
            "collection_name": T.COLLECTION_NAMES[self.collection], "layout_id": self.layout_id,
            "layout": self.layout, "bddl": str(self.bddl), "instruction": self.instruction,
            "wait_steps": T.WAIT_STEPS, "source": source, "policy_version": policy_version,
            "native_step_limit": self.step_limit, "reset_seconds": round(time.monotonic() - started, 3),
            "started_at_unix_s": time.time()})
        (self.output / "reset.json").chmod(0o600)
        write_json(self.output / "private_initial_state.json", {
            "episode_id": self.episode_id, "objects": self.initial_objects, "state": self._state(),
            "success_at_start": bool(self.env.check_success())})
        (self.output / "private_initial_state.json").chmod(0o600)
        self._write_operator_state()
        return {"episode_id": self.episode_id, "step_id": 0, "native_step_limit": self.step_limit}

    def _open_video(self):
        import imageio

        self.video = imageio.get_writer(
            str(self.output / "sensors.mp4"), fps=VIDEO_FPS, codec="libx264", pixelformat="yuv420p",
            macro_block_size=16, output_params=["-movflags", "+faststart"])

    def _write_video_frame(self):
        if self.video is None:
            return
        self.video.append_data(np.concatenate([self._camera_image(c) for c in self.cameras], axis=1))

    def _close_video(self):
        if self.video is not None:
            try:
                self.video.close()
            finally:
                self.video = None

    def chunk_step(self, actions):
        require(not (self.terminated or self.truncated), "Episode already ended")
        matrix = np.asarray(actions, dtype=np.float64)
        reject_unless(matrix.ndim == 2 and matrix.shape[1] == T.ACTION_DIM and len(matrix) >= 1,
                      f"actions must be rows of {T.ACTION_DIM} numbers")
        reject_unless(bool(np.isfinite(matrix).all()), "actions must be finite")
        reject_unless(bool((np.abs(matrix) <= 1.0).all()), "action values must be in [-1, 1]")
        frames, steps = [], []
        for values in matrix:
            self.raw, _, done, _ = self.env.step(values.tolist())
            self.step_id += 1
            self.success = bool(done)
            self.terminated = self.success
            self.truncated = not self.success and self.step_id >= self.step_limit
            ended = self.terminated or self.truncated
            self._write_video_frame()
            steps.append({"step_id": self.step_id, "executed_action": values.tolist(),
                          "terminated": self.terminated, "truncated": self.truncated,
                          **({"success": self.success} if ended else {})})
            if self.step_id % T.FRAME_STRIDE == 0 and not ended:
                frames.append(self._agent_observation(depth=False))
            if ended:
                break
        self.obs = self._agent_observation()
        frames.append(self.obs)
        if self.terminated or self.truncated:
            self._write_summary("terminal")
        else:
            self._write_operator_state()
        return {"episode_id": self.episode_id, "step_id": self.step_id, "kind": "official_actions",
                "observation_frequency_hz": T.CONTROL_HZ / T.FRAME_STRIDE, "control_frequency_hz": T.CONTROL_HZ,
                "frame_stride": T.FRAME_STRIDE, "frame_count": len(frames), "frames": frames, "steps": steps,
                "terminated": self.terminated, "truncated": self.truncated,
                **({"success": self.success} if self.terminated or self.truncated else {})}

    # ---------------------------------------------------------- operator files
    def _ended(self):
        return bool(self.terminated or self.truncated)

    def _write_operator_state(self, reason=None):
        if self.episode_id is None:
            return
        ended = self._ended()
        state = {
            "schema": SCHEMA, "simulator_pid": os.getpid(), "updated_at_unix_s": time.time(),
            "benchmark": "libero_pro", "task": self.task, "official_name": self.info["official_name"],
            "suite_directory": self.suite_directory, "instruction": self.instruction, "episode_id": self.episode_id,
            "collection": self.collection, "collection_name": T.COLLECTION_NAMES[self.collection],
            "layout_id": self.layout_id, "episode_mode": self.episode_mode,
            "step_id": int(self.step_id), "native_reference_step_limit": self.step_limit,
            "step_limit_enforced": True, "episode_step_limit": self.step_limit,
            # Latest RGB frames for the operator's live view (overwritten; history is in sensors.mp4).
            "observation_file": f"{self.episode_id}/live.npz", "cameras": self.cameras, "reason": reason,
            "reward": {"native_success": bool(self.success), "native_reward": 1.0 if self.success else 0.0,
                       "native_score_percent": 100.0 if self.success else 0.0,
                       "terminated": bool(self.terminated), "truncated": bool(self.truncated),
                       "valid_for_success_rate": bool(ended and not self.invalid_reason)},
        }
        live = self.episode_dir / "live.npz"
        with open(live.with_suffix(".tmp"), "wb") as stream:
            np.savez(stream, **{camera: self.obs[camera] for camera in self.cameras if camera in self.obs})
        live.with_suffix(".tmp").replace(live)
        path = self.output / "operator_state.json"
        write_json(path, state)
        path.chmod(0o600)

    def _write_summary(self, reason):
        if self.episode_id is None:
            return
        reason = self.finish_reason or reason
        complete = self._ended()
        eligible = complete and not self.invalid_reason
        write_json(self.output / "evaluation_outcome.json", {
            "complete": complete, "valid_for_success_rate": eligible,
            "native_success": self.success if eligible else None,
            # Binary scoring: once closed, the outcome is decided unless the episode was invalidated
            # (e.g. a wall timeout); a controller that returned early scores what it reached.
            "native_score": None if self.invalid_reason else (1.0 if self.success else 0.0),
            "valid_for_score": not self.invalid_reason, "score_source": "libero_success",
            "invalid_reason": self.invalid_reason, "status": "native_completed" if eligible else "incomplete",
            "reason": reason,
            "end_reason": "task_complete" if self.success else ("step_limit" if self.truncated else None),
            "native_control_steps": self.step_id, "native_reference_step_limit": self.step_limit,
            "step_limit_enforced": True, "episode_step_limit": self.step_limit})
        write_json(self.output / "summary.json", {
            "episode_id": self.episode_id, "step_id": self.step_id, "success": self.success,
            "terminated": self.terminated, "truncated": self.truncated, "complete": complete, "reason": reason,
            "control_dt": 1.0 / T.CONTROL_HZ, "no_rollback": True, "collection": self.collection,
            "layout_id": self.layout_id, "episode_mode": self.episode_mode})
        try:
            final_objects = self._object_poses()
        except Exception as exc:  # noqa: BLE001 - operator diagnostics must never break scoring
            final_objects = {"error": f"{type(exc).__name__}: {exc}"}
        path = self.output / "private_final_state.json"
        write_json(path, {"episode_id": self.episode_id, "reason": reason, "objects": final_objects,
                          "success": bool(self.success)})
        path.chmod(0o600)
        self._write_operator_state(reason=reason)

    def finish(self, reason):
        self.finish_reason = self.finish_reason or reason
        self._write_summary(reason)
        self._close_video()
        return {"episode_id": self.episode_id, "step_id": self.step_id, "reason": reason}

    def close(self):
        self._close_video()
        if self.env is not None:
            self.env.close()
            self.env = None

    # --------------------------------------------------------------- dispatch
    def _check_identity(self, episode_id, step_id):
        require(self.episode_id is not None, "No active episode")
        reject_unless(episode_id == self.episode_id, "Stale episode")
        reject_unless(int(step_id) == self.step_id, f"Stale step: {step_id} != {self.step_id}")

    def dispatch(self, op, args):
        with action_execution_scope(self, op):
            return self._dispatch(op, args)

    def _dispatch(self, op, args):
        if op == "metadata":
            return self.metadata
        if op == "reset":
            return self.reset(**args)
        if op == "finish_pilot":
            require(args["episode_id"] == self.episode_id, "Stale episode")
            return self.finish(str(args.get("reason", "finish")))
        self._check_identity(args["episode_id"], args["step_id"])
        data = {k: v for k, v in args.items() if k not in ("episode_id", "step_id")}
        if op == "observe":
            return self.obs
        if op == "chunk_step":
            return self.chunk_step(data["actions"])
        raise RejectedBeforeAction(f"Unknown op: {op}")
