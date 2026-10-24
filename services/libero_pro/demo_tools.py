"""Replay LIBERO's official human demonstrations in the official environment (runs in
runtime/envs/libero_pro; prints one JSON line).

  python -m services.libero_pro.demo_tools --task libero_spatial_0 --hdf5 <task>_demo.hdf5 \
      --output <dir> --experts 3 --candidates 12

Candidates are the task's official demonstrations in a random order seeded by the task name (the same
order on every rerun). Each starts from its recorded first simulator state (``set_init_state``, as
LIBERO's own replay does) and executes its recorded actions with ``env.step``; the first ``--experts``
that reach success become the experts. Experts get third-person keyframes rendered during the replay
(upright, 256x256 JPEG), the actions and the 8-D state per step.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

import numpy as np

from services.libero_pro import tasks as T
from services.libero_pro.session import quat_xyzw_to_axis_angle

KEYFRAMES = 8


def build(task, collection):
    from libero.libero.envs import OffScreenRenderEnv

    return OffScreenRenderEnv(bddl_file_name=str(T.bddl_path(task, collection)), camera_heights=T.IMAGE_SIZE,
                              camera_widths=T.IMAGE_SIZE)


def state8(obs):
    return np.concatenate([obs["robot0_eef_pos"], quat_xyzw_to_axis_angle(obs["robot0_eef_quat"]),
                           obs["robot0_gripper_qpos"]]).astype(np.float64)


def replay(env, states, actions, keyframe_steps):
    env.seed(0)
    env.reset()
    obs = env.set_init_state(np.asarray(states[0]))
    images, trajectory, success = {}, [state8(obs)], False
    if 0 in keyframe_steps:
        images[0] = np.ascontiguousarray(np.asarray(obs["agentview_image"])[::-1])
    for step, action in enumerate(actions, 1):
        obs, _, done, _ = env.step(np.asarray(action, dtype=np.float64).tolist())
        trajectory.append(state8(obs))
        if step in keyframe_steps or done:
            images[step] = np.ascontiguousarray(np.asarray(obs["agentview_image"])[::-1])
        if done:
            success = True
            break
    return success, images, np.asarray(trajectory), step


def main(argv=None):
    import h5py
    import imageio

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--hdf5", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--experts", type=int, default=3)
    parser.add_argument("--candidates", type=int, default=12)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    env = build(args.task, T.EXPLORATION_COLLECTION)
    experts, candidates = [], []
    with h5py.File(args.hdf5, "r") as data:
        names = sorted(data["data"].keys(), key=lambda name: int(name.split("_")[-1]))
        random.Random(args.task).shuffle(names)
        for name in names[:args.candidates]:
            if len(experts) >= args.experts:
                break
            demo = data["data"][name]
            actions = np.asarray(demo["actions"], dtype=np.float64)
            states = np.asarray(demo["states"])
            keyframe_steps = set(int(round(i)) for i in np.linspace(0, len(actions), KEYFRAMES))
            success, images, trajectory, steps = replay(env, states, actions, keyframe_steps)
            entry = {"demo": name, "recorded_steps": int(len(actions)), "replayed_steps": int(steps),
                     "success": success}
            candidates.append(entry)
            if not success:
                continue
            directory = output / name
            (directory / "frames").mkdir(parents=True, exist_ok=True)
            frames = []
            for step in sorted(images):
                path = directory / "frames" / f"agentview_{step:04d}.jpg"
                imageio.imwrite(path, images[step], quality=95)
                frames.append({"step": step, "file": f"frames/{path.name}"})
            (directory / "frames" / "index.json").write_text(json.dumps({"camera": "agentview", "frames": frames},
                                                                         indent=2) + "\n")
            np.savez_compressed(directory / "trajectory.npz", actions=actions[:steps], state=trajectory)
            (directory / "episode.json").write_text(json.dumps(entry, indent=2) + "\n")
            experts.append(name)
    env.close()
    print(json.dumps({"task": args.task, "experts": experts, "candidates": candidates}), flush=True)


if __name__ == "__main__":
    main()
