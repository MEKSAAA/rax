#!/usr/bin/env bash
# Check out the pinned upstream simulator sources (dependencies.lock) on the runtime storage tier
# and link runtime/ to it. Usage: bash scripts/bootstrap_sources.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$PROJECT_ROOT/dependencies.lock"

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

if [[ -n "${RAX_RUNTIME_ROOT:-}" ]]; then
    runtime_target="$(realpath -m "$RAX_RUNTIME_ROOT")"
else
    runtime_target="$PROJECT_ROOT/.local-runtime"
fi

mkdir -p "$runtime_target"
runtime_target="$(readlink -f "$runtime_target")"
if [[ -L "$PROJECT_ROOT/runtime" ]]; then
    [[ "$(readlink -f "$PROJECT_ROOT/runtime")" == "$(readlink -f "$runtime_target")" ]] || {
        echo "runtime points somewhere else: $PROJECT_ROOT/runtime" >&2
        exit 2
    }
elif [[ -e "$PROJECT_ROOT/runtime" ]]; then
    echo "refusing to replace non-symlink runtime path" >&2
    exit 2
else
    ln -s "$runtime_target" "$PROJECT_ROOT/runtime"
fi

# LIBERO-PRO: the official checkout (~1 GB with its assets), never modified, on the
# runtime storage tier and exposed at LIBERO-PRO/.
libero_pro_target="$(dirname "$runtime_target")/LIBERO-PRO"
if [[ ! -e "$PROJECT_ROOT/LIBERO-PRO" ]]; then
    if [[ ! -d "$libero_pro_target/.git" ]]; then
        git clone "$LIBERO_PRO_URL" "$libero_pro_target"
    fi
    git -C "$libero_pro_target" checkout --detach "$LIBERO_PRO_COMMIT"
    ln -s "$libero_pro_target" "$PROJECT_ROOT/LIBERO-PRO"
fi
[[ "$(git -C "$PROJECT_ROOT/LIBERO-PRO" rev-parse HEAD)" == "$LIBERO_PRO_COMMIT" ]] || {
    echo "LIBERO-PRO is not at $LIBERO_PRO_COMMIT" >&2
    exit 2
}

echo "Sources ready. Install the host environment (scripts/setup_host_env.sh), then run scripts/doctor.sh."
echo "For LIBERO-PRO: scripts/setup_libero_pro_env.sh, scripts/setup_libero_pro_data.py and scripts/setup_libero_pro_demos.py (see docs/LIBERO_PRO_BACKEND.md)."
