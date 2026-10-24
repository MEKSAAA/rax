"""LIBERO-PRO doctor: the real official simulator through the host process manager.

For each task and collection: simulator startup, the observation contract (8-D state, both cameras
at 256x256 upright, depth and calibration, the BDDL instruction), a zero row and a short motion
through ``chunk_step``, depth back-projection of every visible object's true position (operator-only
file) in both cameras, the operator files (operator_state, live frame, sensors.mp4, outcome), and
the simulator-side checks (collections, determinism, controller response). Saves the first
observation's images for a visual check and writes a JSON report.

  runtime/envs/host/bin/python scripts/libero_pro_doctor.py --gpu GPU-... --task libero_spatial_0
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def doctor_episode(task, gpu, root, collection):
    from services.libero_pro import tasks as T
    from services.libero_pro.process import LiberoProProcess
    from services.libero_pro.tools import pixel_to_world, world_to_pixel

    report = {"task": task, "collection": collection}
    sim = LiberoProProcess(output_root=root / "results")
    run_id = f"{task}_c{collection}_doctor_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex[:6]}"
    started = time.monotonic()
    reset = sim.start(task=task, collection=collection, layout_id=0, run_id=run_id, sim_gpu=gpu,
                      startup_timeout=T.STARTUP_SECONDS, include_depth=True, episode_timeout_seconds=1800)
    report["startup_seconds"] = round(time.monotonic() - started, 2)
    report["reset"] = reset
    try:
        obs = sim.request("observe")
        cameras = sim.metadata["cameras"]
        report["observation"] = {
            "state": [round(v, 4) for v in obs["state"]], "cameras": cameras,
            "image_shapes": {c: list(np.asarray(obs[c]).shape) for c in cameras},
            "depth": {c: {"finite": float(np.isfinite(obs[f"{c}_depth_m"]).mean()),
                          "min_m": float(np.nanmin(obs[f"{c}_depth_m"])),
                          "median_m": float(np.median(obs[f"{c}_depth_m"][np.isfinite(obs[f"{c}_depth_m"])]))}
                      for c in cameras if f"{c}_depth_m" in obs},
            "instruction": obs.get("instruction"), "instruction_from_file": T.instruction(task, collection)}
        assert len(obs["state"]) == T.STATE_DIM, obs["state"]
        assert obs.get("instruction") == T.instruction(task, collection), "instruction mismatch"
        try:
            import imageio
            for camera in cameras:
                imageio.imwrite(root / f"{run_id}_{camera}.png", np.asarray(obs[camera], dtype=np.uint8))
        except Exception as exc:  # noqa: BLE001 - the images are a convenience
            report["image_save_error"] = repr(exc)
        # Every object's true position against its depth back-projection in each camera that sees it:
        # the visible surface lies within the object's size of its body origin.
        private = json.loads((sim.output_dir / "sim" / "private_initial_state.json").read_text())
        report["success_at_start"] = private.get("success_at_start")
        checks = {}
        for name, pose in private["objects"].items():
            target = np.asarray(pose["position_m"])
            entry = {}
            for camera in cameras:
                pixel = world_to_pixel(target, obs["camera_intrinsics"][camera], obs["camera_poses"][camera])
                if pixel is None or not (0 <= pixel[0] < T.IMAGE_SIZE and 0 <= pixel[1] < T.IMAGE_SIZE):
                    entry[camera] = {"visible": False}
                    continue
                back = pixel_to_world(obs[f"{camera}_depth_m"], obs["camera_intrinsics"][camera],
                                      obs["camera_poses"][camera], pixel[0], pixel[1], 1)
                entry[camera] = {"pixel": [round(pixel[0], 1), round(pixel[1], 1)], "valid": back.get("valid"),
                                 "distance_m": round(float(np.linalg.norm(np.asarray(back["position_m"]) - target)), 4)
                                 if back.get("valid") else None}
            checks[name] = entry
        report["object_projection"] = checks
        # The end effector projected into the third-person image.
        eef = np.asarray(obs["state"][0:3])
        report["eef_pixel_agentview"] = world_to_pixel(eef, obs["camera_intrinsics"]["agentview"],
                                                       obs["camera_poses"]["agentview"])
        started = time.monotonic()
        held = sim.request("chunk_step", actions=np.asarray([[0.0] * 6 + [-1.0]] * 10))
        report["zero_rows"] = {"step_id": held["step_id"], "frames": held["frame_count"],
                               "seconds_per_step": round((time.monotonic() - started) / 10, 4),
                               "eef_drift_m": round(float(np.linalg.norm(np.asarray(held["frames"][-1]["state"][:3]) - eef)), 5)}
        down = np.asarray([[0.0, 0.0, -0.5, 0.0, 0.0, 0.0, -1.0]] * 10)
        started = time.monotonic()
        moved = sim.request("chunk_step", actions=down)
        report["down_rows"] = {"step_id": moved["step_id"], "frames": moved["frame_count"],
                               "seconds_per_step": round((time.monotonic() - started) / 10, 4),
                               "eef_z_drop_m": round(float(np.asarray(held["frames"][-1]["state"])[2]
                                                           - np.asarray(moved["frames"][-1]["state"])[2]), 4),
                               "terminated": moved["terminated"]}
        sim.finish("doctor")
        sim_dir = sim.output_dir / "sim"
        state = json.loads((sim_dir / "operator_state.json").read_text())
        outcome = json.loads((sim_dir / "evaluation_outcome.json").read_text())
        reset_record = json.loads((sim_dir / "reset.json").read_text())
        video = sim_dir / "sensors.mp4"
        report["operator_files"] = {
            "operator_state": {k: state.get(k) for k in ("episode_mode", "step_id", "episode_step_limit", "cameras",
                                                         "collection_name", "suite_directory")},
            "reset_layout": reset_record.get("layout"),
            "reward": state.get("reward"), "outcome": {k: outcome.get(k) for k in ("native_score", "native_success",
                                                                                   "status", "reason")},
            "modes": {name: oct((sim_dir / name).stat().st_mode & 0o777)
                      for name in ("reset.json", "operator_state.json", "private_initial_state.json")},
            "live_npz": (sim_dir / state["observation_file"]).is_file(),
            "sensors_mp4_bytes": video.stat().st_size if video.is_file() else 0}
    finally:
        sim.stop()
    return report


def simulator_checks(task, gpu, flags):
    from services.libero_pro.process import libero_pro_python, simulator_environment

    checks = subprocess.run([libero_pro_python(), "-m", "services.libero_pro.checks", "--task", task, *flags],
                            env=simulator_environment(gpu), cwd=PROJECT, capture_output=True, text=True, timeout=3600)
    lines = [line for line in checks.stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if lines else {"returncode": checks.returncode, "stderr": checks.stderr[-3000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpu", required=True, help="Simulator GPU UUID or index")
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--collection", type=int, action="append",
                        help="Collections to start episodes in (default: 0 and 1)")
    parser.add_argument("--checks", default="--collections,--determinism,--characterize,--describe",
                        help="Comma-separated simulator-side checks (services/libero_pro/checks.py); '' for none")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    root = Path(args.output or PROJECT / "runtime" / "diagnostics" /
                f"libero-pro-doctor-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for task in args.task:
        for collection in args.collection or (0, 1):
            try:
                reports.append(doctor_episode(task, args.gpu, root, collection))
            except Exception as exc:  # noqa: BLE001 - report every task, then fail
                reports.append({"task": task, "collection": collection, "error": f"{type(exc).__name__}: {exc}"})
        flags = [flag for flag in args.checks.split(",") if flag]
        if flags:
            reports.append({"task": task, "simulator_checks": simulator_checks(task, args.gpu, flags)})
    (root / "doctor-report.json").write_text(json.dumps(reports, indent=2, default=str) + "\n")
    print(json.dumps(reports, indent=2, default=str))
    print(f"report: {root / 'doctor-report.json'}")
    if any("error" in r for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
