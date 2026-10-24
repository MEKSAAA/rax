#!/usr/bin/env bash
# Build runtime/envs/host: the harness's own Python environment (launchers, MCP service, workspace
# generator, dashboard, tests), which is also the agent's Python (harness/agent_host.py). It holds no
# simulator; each backend's simulator has its own environment. Usage: bash scripts/setup_host_env.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/host_env.sh"
source "$RAX_PROJECT_ROOT/dependencies.lock"

MINIFORGE="$RAX_RUNTIME_ROOT/miniforge3"
if [[ ! -x "$MINIFORGE/bin/conda" ]]; then
    installer="$(mktemp --suffix=.sh)"
    curl -fsSL -o "$installer" \
        "https://github.com/conda-forge/miniforge/releases/download/$MINIFORGE_VERSION/Miniforge3-$MINIFORGE_VERSION-Linux-x86_64.sh"
    echo "$MINIFORGE_SHA256  $installer" | sha256sum -c --quiet
    bash "$installer" -b -p "$MINIFORGE"
    rm -f "$installer"
fi
if [[ ! -x "$RAX_PYTHON" ]]; then
    "$MINIFORGE/bin/conda" create -y -p "$RAX_HOST_ENV_ROOT" -c conda-forge "python=$HOST_PYTHON_VERSION" pip git-lfs
fi
export PIP_USER=0 PYTHONNOUSERSITE=1
"$RAX_PYTHON" -m pip install "numpy==$HOST_NUMPY_VERSION" "pillow==$HOST_PILLOW_VERSION" \
    "jinja2==$HOST_JINJA2_VERSION" "msgpack==$HOST_MSGPACK_VERSION" "pyyaml==$HOST_PYYAML_VERSION" \
    "imageio==$HOST_IMAGEIO_VERSION" "imageio-ffmpeg==$HOST_IMAGEIO_FFMPEG_VERSION" \
    "scipy==$HOST_SCIPY_VERSION" "pytest==$HOST_PYTEST_VERSION" "opencv-python-headless==$HOST_OPENCV_VERSION" \
    "h5py==$AGENT_H5PY_VERSION"
# CPU-only PyTorch for the agent; its other dependencies come from PyPI.
"$RAX_PYTHON" -m pip install --index-url "$AGENT_TORCH_INDEX" --extra-index-url https://pypi.org/simple \
    "torch==$AGENT_TORCH_VERSION+cpu" "torchvision==$AGENT_TORCHVISION_VERSION+cpu"
"$RAX_PYTHON" - <<'PY'
import cv2, h5py, jinja2, msgpack, numpy, PIL, scipy, torch, torchvision, yaml, imageio  # noqa: F401
print("host packages import")
PY
echo "Host environment ready: $RAX_HOST_ENV_ROOT"
