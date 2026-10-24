"""Host-side LIBERO-PRO demonstrations from LIBERO's official human demonstration dataset.

scripts/setup_libero_pro_demos.py installs, per task, three official demonstrations (HF
``yifengzhu-hf/LIBERO-datasets``, pinned; the first that replay to success in the official environment,
in a random order seeded by the task name) under runtime/reference-demos/libero_pro/<task>/
with a sha256 manifest and third-person keyframes rendered during the replay (upright, 256x256).

``demonstration_context`` copies keyframe images of the experts into runtime/demonstrations
(``terminal_state``: each expert's final frame). Expert episodes are never mounted.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from services.demonstration_io import (
    DEMONSTRATION_CONTEXTS, DemonstrationError, _replace_directory, _validate_jpeg,
)

from .tasks import task_info

PROJECT = Path(__file__).resolve().parents[2]
SCHEMA = "libero_pro_demos_v1"
EXPERTS = 3


def demos_root():
    return PROJECT / "runtime" / "reference-demos" / "libero_pro"


def _load_manifest(directory, task):
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except (OSError, ValueError) as error:
        raise DemonstrationError(f"LIBERO-PRO demonstrations for {task} are not installed; "
                                 f"run scripts/setup_libero_pro_demos.py --task {task}") from error
    if manifest.get("schema") != SCHEMA or manifest.get("task") != task:
        raise DemonstrationError(f"Invalid LIBERO-PRO demonstration manifest for {task}")
    if len(manifest.get("experts", ())) != EXPERTS:
        raise DemonstrationError(f"LIBERO-PRO expert set for {task} must hold {EXPERTS} verified demonstrations; "
                                 f"rerun scripts/setup_libero_pro_demos.py --task {task} with more candidates")
    return manifest


def cached_experts(cache_root, task):
    """The installed expert demonstration directories of one task, in order (light check; the setup
    script verified every file hash and the replay)."""
    task_info(task)
    directory = (Path(cache_root).expanduser() / task).resolve()
    manifest = _load_manifest(directory, task)
    experts_dir = directory / "experts"
    demos = []
    for name in manifest["experts"]:
        path = (experts_dir / name).resolve()
        if not path.is_relative_to(experts_dir) or not (path / "frames" / "index.json").is_file():
            raise DemonstrationError(f"Incomplete LIBERO-PRO expert demonstration {name} for {task}")
        demos.append(path)
    return demos


def keyframes(demo):
    """The demonstration's third-person keyframes, in time order (the last is the final frame)."""
    index = json.loads((demo / "frames" / "index.json").read_text())
    images = []
    for frame in sorted(index["frames"], key=lambda item: item["step"]):
        path = (demo / frame["file"]).resolve()
        if not path.is_relative_to(demo):
            raise DemonstrationError("LIBERO-PRO keyframe escaped its demonstration directory")
        _validate_jpeg(path)
        images.append(path)
    if not images:
        raise DemonstrationError(f"No keyframes in {demo.name}")
    return images, index.get("camera")


def provision_trial_demonstration(*, cache_root, target, task, context_kind):
    """Expose the selected keyframes in the trial runtime."""
    if context_kind not in DEMONSTRATION_CONTEXTS:
        raise DemonstrationError("demonstration_context must be one of: " + ", ".join(DEMONSTRATION_CONTEXTS))
    demos = [keyframes(demo) for demo in cached_experts(cache_root, task)] if context_kind != "none" else []
    target = Path(target).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target.parent.resolve() / target.name
    # Stage in the trusted trial root, outside the agent-writable runtime.
    staging = target.parent.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    staging.mkdir(mode=0o700)
    try:
        if context_kind == "none":
            _replace_directory(staging, target)
            return {"kind": "none", "image_count": 0, "root": "runtime/demonstrations", "manifest_path": None}
        sequences, camera = [], None
        for number, (images, camera) in enumerate(demos, 1):
            chosen = images if context_kind == "completion_sequence" else images[-1:]
            (staging / f"demo-{number}").mkdir(mode=0o700)
            names = []
            for order, source in enumerate(chosen):
                relative = (f"demo-{number}/frame_{order:03d}.jpg" if context_kind == "completion_sequence"
                            else f"demo-{number}/terminal.jpg")
                shutil.copyfile(source, staging / relative)
                (staging / relative).chmod(0o600)
                names.append(relative)
            sequences.append(names)
        ordered_images = [name for names in sequences for name in names]
        manifest = {
            "schema_version": 1, "task": task, "context_kind": context_kind, "demonstrator": "human_teleoperation",
            "source": "libero_official_human_demos", "camera": camera,
            "image_count": len(ordered_images), "ordered_images": ordered_images,
            # Independent demonstrations, each in a different layout; order applies within one.
            "demonstrations": [{"ordered_images": names} for names in sequences],
            "contains": ["rgb_images"],
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (staging / "manifest.json").chmod(0o600)
        _replace_directory(staging, target)
        return {"kind": context_kind, "image_count": len(ordered_images), "root": "runtime/demonstrations",
                "manifest_path": "runtime/demonstrations/manifest.json"}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
