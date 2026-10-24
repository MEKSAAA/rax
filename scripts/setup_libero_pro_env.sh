#!/usr/bin/env bash
# Build runtime/envs/libero_pro for the LIBERO-PRO simulator subprocess as the official README does
# (conda python 3.8.13, `pip install -r requirements.txt`, torch 1.11.0), with the additions listed
# in dependencies.lock: MuJoCo pinned, torch's CPU build, msgpack. The package itself is imported
# from the pinned checkout on PYTHONPATH (services/libero_pro/process.py) instead of
# `pip install -e .`, which would write egg-info into the checkout.
# Never touches runtime/envs/host. Usage: bash scripts/setup_libero_pro_env.sh
# CONDA_EXE selects the conda binary (default: the Miniforge scripts/setup_host_env.sh installs,
# else `conda` on PATH).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/libero_pro_env.sh"
source "$(dirname "${BASH_SOURCE[0]}")/../dependencies.lock"
CONDA="${CONDA_EXE:-}"
if [[ -z "$CONDA" ]]; then
    CONDA="$LIBERO_PRO_RUNTIME_ROOT/miniforge3/bin/conda"
    [[ -x "$CONDA" ]] || CONDA="$(command -v conda || true)"
fi
[[ -x "$CONDA" ]] || { echo "No conda binary; set CONDA_EXE" >&2; exit 1; }
[[ -f "$LIBERO_PRO_SOURCE/requirements.txt" ]] || { echo "Missing LIBERO-PRO source; run scripts/bootstrap_sources.sh" >&2; exit 1; }
actual="$(git -C "$LIBERO_PRO_SOURCE" rev-parse HEAD)"
[[ "$actual" == "$LIBERO_PRO_COMMIT" ]] || { echo "LIBERO-PRO is at $actual, expected $LIBERO_PRO_COMMIT" >&2; exit 1; }
[[ -z "$(git -C "$LIBERO_PRO_SOURCE" status --porcelain --untracked-files=no)" ]] || { echo "LIBERO-PRO checkout is modified" >&2; exit 1; }

if [[ ! -x "$LIBERO_PRO_PYTHON" ]]; then
    # conda-forge only: some sites block the `defaults` channel.
    "$CONDA" create -y -p "$LIBERO_PRO_ENV_ROOT" --override-channels -c conda-forge "python=$LIBERO_PRO_PYTHON_VERSION" pip
fi
# Build tool only: robomimic's egl_probe dependency compiles with CMake.
[[ -x "$LIBERO_PRO_ENV_ROOT/bin/cmake" ]] || "$CONDA" install -y -p "$LIBERO_PRO_ENV_ROOT" --override-channels -c conda-forge cmake
export PATH="$LIBERO_PRO_ENV_ROOT/bin:$PATH"
# egl_probe declares cmake_minimum_required < 3.5, which CMake 4 refuses without this policy floor.
export CMAKE_POLICY_VERSION_MINIMUM=3.5
PIP=("$LIBERO_PRO_ENV_ROOT/bin/python" -m pip)
# torch first (its CPU build), so the requirements' unpinned torch dependency (robomimic) is met
# by it instead of pulling the newest CUDA build; MuJoCo pinned before robosuite resolves its own.
TORCH=("torch==$LIBERO_PRO_TORCH_VERSION" "torchvision==$LIBERO_PRO_TORCHVISION_VERSION")
"${PIP[@]}" install "${TORCH[@]}" --index-url https://download.pytorch.org/whl/cpu
"${PIP[@]}" install "mujoco==$LIBERO_PRO_MUJOCO_VERSION" msgpack
"${PIP[@]}" install -r "$LIBERO_PRO_SOURCE/requirements.txt" "${TORCH[@]}" \
    "mujoco==$LIBERO_PRO_MUJOCO_VERSION" --extra-index-url https://download.pytorch.org/whl/cpu

"$LIBERO_PRO_PYTHON" - <<'PY'
import importlib.metadata as m
for name in ("robosuite", "mujoco", "bddl", "numpy", "torch", "torchvision", "gym", "msgpack", "opencv-python", "PyOpenGL", "imageio"):
    try:
        print(f"{name}=={m.version(name)}")
    except m.PackageNotFoundError:
        print(f"{name}: MISSING")
PY
