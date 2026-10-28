"""RoboWits episode using the official environment, controllers, resets and binary success checker."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import numpy as np

from services.robowits import tasks as T
from services.simulator.io import RejectedBeforeAction, action_execution_scope, reject_unless, require, write_json

SCHEMA = "robowits_operator_state_v1"
VIDEO_FPS = T.CONTROL_HZ


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


class RoboWitsSession:
    def __init__(self, output, task, collection, *, include_depth):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.task, self.collection = task, int(collection)
        self.info = T.task_info(task)
        self.suite_directory = 'mutation' if self.info['mutation'] else 'seed'
        self.episode_mode = 'formal' if collection == 1 else 'exploration'
        self.include_depth = include_depth
        self.cameras = list(T.CAMERAS)
        self.step_limit = T.native_step_limit(task)
        self.env = self.raw = self.instruction = self.episode_id = self.episode_dir = None
        self.layout_id = self.layout = None
        self.step_id = 0
        self.native_rewards = []
        self.terminated = self.truncated = self.success = False
        self.invalid_reason = self.finish_reason = self.obs = self.video = None
        self.metadata = {'task':task, 'benchmark':'robowits','cameras':self.cameras,
            'control_frequency_hz':T.CONTROL_HZ,'native_step_limit':self.step_limit,
            'action_dim':14,'state_dim':14,'image_size':[848,480],
            'depth':include_depth,'frame_stride':T.FRAME_STRIDE,'episode_mode':self.episode_mode}

    def _build(self):
        import gs_gym
        kwargs = dict(show_viewer=False, max_episode_steps=self.step_limit,
                      control_mode='EE_ABS', observation_mode='EE', camera_type='rasterizer')
        # Keep each task's native CPU/CUDA backend choice, especially for particle tasks.
        if self.collection == 1:
            kwargs['eval_dataset_json'] = str(T.eval_path(self.task))
        from .assets import benchmark_texture_loading
        with benchmark_texture_loading():
            self.env = gs_gym.get_task_class(self.info['registered_name'])(**kwargs)
        self.env.seed(self.layout['seed'])
        if self.collection == 2:
            # Same canonical-scene reset mechanism as the source's sample_scenes.py.
            # Keep original object geometry, poses, supports and material physics.
            # No task-specific action or held-out layout is supplied to the agent.
            self.env._place_objects = self.env._restore_eval_particles
        if self.collection == 1:
            self.env.set_eval_dataset_idx(self.layout['init_index'])
        self.instruction = self.env.task_description

    def _camera_image(self, camera):
        return np.ascontiguousarray(self.raw['pixels'][camera], dtype=np.uint8)

    def _calibration(self):
        intrinsics, poses = {}, {}
        base = np.asarray(self.env._robot.args.position)
        for name in self.cameras:
            sensor = self.env._cameras[name]
            sensor.read()
            wrapper = sensor._camera_wrapper
            transform = np.asarray(wrapper.transform).reshape(-1,4,4)[0]
            w,h = wrapper.res
            focal = h / (2*np.tan(np.deg2rad(wrapper.fov)/2))
            intrinsics[name] = {'matrix':[[focal,0,w/2],[0,focal,h/2],[0,0,1]],'width':w,'height':h,'fovy_deg':wrapper.fov}
            poses[name] = {'position_m':(transform[:3,3]-base).tolist(),
                'quaternion_wxyz':matrix_to_quat_wxyz(transform[:3,:3]),'frame':'robot_base',
                'convention':'OpenGL camera: looks along -Z, +Y up; image row 0 at top'}
        return intrinsics,poses

    def _depth(self, name):
        sensor = self.env._cameras[name]
        sensor.read()
        _,depth,_,_ = sensor._shared_metadata.renderer.render_camera(sensor._camera_wrapper, rgb=False, depth=True)
        return np.asarray(depth,dtype=np.float32).reshape(-1,T.IMAGE_HEIGHT,T.IMAGE_WIDTH)[0]

    def _state(self):
        return np.asarray(self.raw['agent_pos'],dtype=float).reshape(14).tolist()

    def _agent_observation(self, *, depth=None):
        depth = self.include_depth if depth is None else depth
        obs = {camera:self._camera_image(camera) for camera in self.cameras}
        obs.update(state=self._state(),instruction=self.instruction,step_id=self.step_id)
        if depth:
            obs['camera_intrinsics'],obs['camera_poses'] = self._calibration()
            for name in self.cameras: obs[name+'_depth_m'] = self._depth(name)
        return obs

    def _object_poses(self):
        # Only saved under the private operator directory, never returned by RPC tools.
        result = {}
        for name,entry in self.env._entities.items():
            try:
                p,q=self.env._get_entity_state(entry['entity'])
                result[name]={'position_m':p.detach().cpu().numpy().reshape(-1,3)[0].tolist(),
                              'quaternion_wxyz':q.detach().cpu().numpy().reshape(-1,4)[0].tolist()}
            except Exception: continue
        return result

    def reset(self, layout_id, source='agent', policy_version='mcp'):
        require(self.episode_id is None,'One fresh episode per process')
        self.layout_id=int(layout_id)
        self.layout=T.layout(self.task,self.collection,self.layout_id)
        self._build()
        self.raw,_=self.env.reset(seed=self.layout['seed'])
        self.initial_objects=self._object_poses()
        self.episode_id=f'{self.task}_{self.episode_mode}_{layout_id}_{uuid.uuid4().hex[:8]}'
        self.episode_dir=self.output/self.episode_id
        self.episode_dir.mkdir()
        self._open_video()
        self.obs=self._agent_observation()
        self._write_video_frame()
        write_json(self.output/'reset.json',{'task':self.task,'episode_id':self.episode_id,
             'collection':self.collection,'layout_id':self.layout_id,'layout':self.layout,
             'source':source,'native_step_limit':self.step_limit,'instruction':self.instruction})
        write_json(self.output/'private_initial_state.json',{'objects':self.initial_objects,'state':self._state()})
        self._write_operator_state()
        return {'episode_id':self.episode_id,'step_id':0,'native_step_limit':self.step_limit}

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
        from services.robowits.tools import validate_actions
        validate_actions(matrix.tolist())
        frames, steps = [], []
        for values in matrix:
            self.raw, native_reward, done, truncated, info = self.env.step(np.asarray(values, dtype=np.float32))
            self.step_id += 1
            self.native_rewards.append(float(native_reward))
            self.success = bool(info.get("is_success", done))
            self.terminated = self.success
            self.truncated = not self.success and (bool(truncated) or self.step_id >= self.step_limit)
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
            "benchmark": "robowits", "task": self.task, "official_name": self.info["official_name"],
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
            # Operator-only metrics: retain native dense reward without exposing it to MCP.
            "native_sum_reward": sum(self.native_rewards),
            "native_max_reward": max(self.native_rewards) if self.native_rewards else None,
            "native_reward_steps": len(self.native_rewards),
            "valid_for_score": not self.invalid_reason, "score_source": "robowits_success",
            "invalid_reason": self.invalid_reason, "status": "native_completed" if eligible else "incomplete",
            "reason": reason,
            "end_reason": "task_complete" if self.success else ("step_limit" if self.truncated else None),
            "native_control_steps": self.step_id, "native_reference_step_limit": self.step_limit,
            "step_limit_enforced": True, "episode_step_limit": self.step_limit})
        write_json(self.output / 'native_rewards.json', {'episode_id': self.episode_id,
            'rewards': self.native_rewards, 'aggregation': 'per-step native env.step reward; sum and max'})
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
