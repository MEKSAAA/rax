"""Install official LIBERO demonstrations for LIBERO-PRO tasks (goal images).

For each task, downloads the task's official demonstration file from LIBERO's Hugging Face dataset
(``yifengzhu-hf/LIBERO-datasets``, revision pinned in dependencies.lock), then runs
``services.libero_pro.demo_tools`` in the official environment (needs a GPU for rendering) to replay
candidates, in a random order seeded by the task name, from their recorded start states. The first
three that replay to success become the task's experts, with third-person keyframes rendered during the replay, under runtime/reference-demos/libero_pro/<task>/ with a sha256
manifest. ``demonstration_context: terminal_state`` shows each expert's final frame.

  runtime/envs/host/bin/python scripts/setup_libero_pro_demos.py --gpu GPU-... --task libero_spatial_0
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

SCHEMA = "libero_pro_demos_v1"
EXPERTS = 3


def locked(name):
    for line in (PROJECT / "dependencies.lock").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name} missing from dependencies.lock")


def download(repo, path, target, rev):
    url = f"https://huggingface.co/datasets/{repo}/resolve/{rev}/{urllib.parse.quote(path)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=1800) as response, partial.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    partial.replace(target)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(path):
    """Remove an installed (read-only) demonstration tree. Retried: on the shared scratch filesystem
    a directory just emptied can still read as not empty."""
    for attempt in range(5):
        try:
            for item in [path, *path.rglob("*")]:
                if item.is_dir() and not item.is_symlink():
                    item.chmod(0o755)
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def file_hashes(directory):
    return {p.relative_to(directory).as_posix(): sha256(p) for p in sorted(directory.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpu", required=True, help="GPU index or UUID for the official environment (rendering)")
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--candidates", type=int, default=12)
    parser.add_argument("--keep-download", action="store_true", help="Keep the task's demonstration file")
    args = parser.parse_args()
    from services.libero_pro import tasks as T
    from services.libero_pro.demonstrations import demos_root
    from services.libero_pro.process import libero_pro_python, simulator_environment

    repo, rev = locked("LIBERO_DATASETS_REPO"), locked("LIBERO_DATASETS_REVISION")
    for task in args.task:
        suite, _, name = T.official_task(task)
        task_root = demos_root() / task
        relative = f"{suite}/{name}_demo.hdf5"
        hdf5 = demos_root() / "_downloads" / rev / relative
        if not hdf5.is_file():
            download(repo, relative, hdf5, rev)
        candidates_dir = task_root / "candidates"
        if candidates_dir.exists():
            remove_tree(candidates_dir)
        command = [libero_pro_python(), "-m", "services.libero_pro.demo_tools", "--task", task, "--hdf5", str(hdf5),
                   "--output", str(candidates_dir), "--experts", str(EXPERTS), "--candidates", str(args.candidates)]
        result = subprocess.run(command, cwd=PROJECT, env=simulator_environment(args.gpu), capture_output=True,
                                text=True, timeout=7200)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode != 0 or not lines:
            raise SystemExit(f"demo_tools failed for {task} (exit {result.returncode}):\n{result.stderr[-4000:]}")
        report = json.loads(lines[-1])
        experts = report["experts"]
        if len(experts) < EXPERTS:
            raise SystemExit(f"Only {len(experts)} verified demos for {task}; rerun with more --candidates:\n"
                             + json.dumps(report["candidates"], indent=2))
        experts_dir = task_root / "experts"
        if experts_dir.exists():
            remove_tree(experts_dir)
        for demo_name in experts:
            target = experts_dir / demo_name
            shutil.copytree(candidates_dir / demo_name, target)
            episode = json.loads((target / "episode.json").read_text())
            episode.update(task=task, source=f"{repo}@{rev}/{relative}#{demo_name}",
                           verification="replayed to success in the official environment from its recorded start state")
            (target / "episode.json").write_text(json.dumps(episode, indent=2) + "\n")
            for path in [target, *target.rglob("*")]:
                path.chmod(0o555 if path.is_dir() else 0o444)
        remove_tree(candidates_dir)
        manifest = {"schema": SCHEMA, "task": task, "dataset": repo, "revision": rev, "source_file": relative,
                    "source_sha256": sha256(hdf5), "selection": "random order seeded by the task name; the first "
                    f"{EXPERTS} that replay to success", "candidates": report["candidates"], "experts": experts,
                    "files": {demo: file_hashes(experts_dir / demo) for demo in experts}}
        (task_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if not args.keep_download:
            hdf5.unlink()
        print(json.dumps({"task": task, "experts": experts, "candidates": report["candidates"]}))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
