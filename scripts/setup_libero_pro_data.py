"""Build the runtime-owned LIBERO config and data tree for LIBERO-PRO (no simulator, stdlib only).

The README installs LIBERO-PRO's perturbation files by moving them into the package directory, and
LIBERO asks interactively for paths on its first import. Instead this script

1. downloads the official perturbation files (HF ``zhouxueyang/LIBERO-Pro`` at the revision pinned in
   dependencies.lock: BDDLs and init states of {spatial, object, goal, 10} x {swap, object, lan, task})
   into runtime/libero-pro/hf/<revision>/, checked against the release's SHA256SUMS.txt;
2. links runtime/libero-pro/{bddl_files,init_files}/<suite> to the pinned checkout's original suites and
   <suite>_<perturbation> to the downloaded ones;
3. writes runtime/libero-pro/config/config.yaml (LIBERO_CONFIG_PATH), so the checkout stays clean.

  runtime/envs/host/bin/python scripts/setup_libero_pro_data.py
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from services.libero_pro import tasks as T  # noqa: E402

PERTURBATIONS = ("swap", "object", "lan", "task")
KINDS = {"bddl_files": ".bddl", "init_files": ".pruned_init"}


def locked(name):
    for line in (PROJECT / "dependencies.lock").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name} missing from dependencies.lock")


def download(repo, path, target, rev):
    url = f"https://huggingface.co/datasets/{repo}/resolve/{rev}/{urllib.parse.quote(path)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=600) as response, partial.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    partial.replace(target)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def link(target, source):
    if target.is_symlink() or target.exists():
        if target.is_symlink() and Path(os.readlink(target)) == source:
            return
        if target.is_symlink():
            target.unlink()
        else:
            raise SystemExit(f"Refusing to replace {target}")
    target.symlink_to(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=None, help="Data tree (default: runtime/libero-pro)")
    args = parser.parse_args()
    repo, rev = locked("LIBERO_PRO_DATA_REPO"), locked("LIBERO_PRO_DATA_REVISION")
    source = T.source_root()
    package = source / "libero" / "libero"
    if not (package / "bddl_files").is_dir():
        raise SystemExit(f"Missing LIBERO-PRO checkout at {source}; run scripts/bootstrap_sources.sh")
    data = (args.data or T.data_root()).resolve()
    hf = data / "hf" / rev
    hf.mkdir(parents=True, exist_ok=True)

    sums_path = hf / "SHA256SUMS.txt"
    if not sums_path.is_file():
        download(repo, "SHA256SUMS.txt", sums_path, rev)
    sums = {}
    for line in sums_path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[parts[-1].lstrip("*").lstrip("./")] = parts[0]

    manifest = {"schema": "libero_pro_data_v1", "repo": repo, "revision": rev, "source": str(source),
                "files": {}, "unverified": []}
    for kind, suffix in KINDS.items():
        for suite, (_, names) in T.SUITES.items():
            for perturbation in PERTURBATIONS:
                directory = f"{suite}_{perturbation}"
                for name in names:
                    relative = f"{kind}/{directory}/{name}{suffix}"
                    target = hf / relative
                    if not target.is_file():
                        download(repo, relative, target, rev)
                    digest = sha256(target)
                    expected = sums.get(relative)
                    if expected is None:
                        manifest["unverified"].append(relative)
                    elif expected != digest:
                        raise SystemExit(f"sha256 mismatch for {relative}: {digest} != {expected}")
                    manifest["files"][relative] = digest
    # The data tree LIBERO reads through its config: original suites from the checkout, perturbed
    # suites from the release.
    for kind in KINDS:
        root = data / kind
        root.mkdir(parents=True, exist_ok=True)
        for suite in T.SUITES:
            link(root / suite, package / kind / suite)
            for perturbation in PERTURBATIONS:
                link(root / f"{suite}_{perturbation}", hf / kind / f"{suite}_{perturbation}")
    config = data / "config"
    config.mkdir(parents=True, exist_ok=True)
    (data / "datasets").mkdir(exist_ok=True)
    values = {"benchmark_root": package, "bddl_files": data / "bddl_files", "init_states": data / "init_files",
              "datasets": data / "datasets", "assets": package / "assets"}
    (config / "config.yaml").write_text("".join(f"{key}: {value}\n" for key, value in values.items()))
    (data / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Every task's instruction in every collection parses (the BDDL text, as LIBERO reads it).
    for task in T.task_names():
        for collection in (T.EXPLORATION_COLLECTION, *T.FORMAL_COLLECTIONS):
            T.instruction(task, collection)
            if collection != T.EXPLORATION_COLLECTION and not T.init_states_path(task, collection).is_file():
                raise SystemExit(f"Missing init states for {task} collection {collection}")
    print(json.dumps({"data": str(data), "revision": rev, "files": len(manifest["files"]),
                      "unverified": len(manifest["unverified"])}))


if __name__ == "__main__":
    main()
