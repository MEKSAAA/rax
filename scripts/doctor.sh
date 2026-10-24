#!/usr/bin/env bash
# Check that this machine can run auto-research sessions: the host environment, Docker and the
# agent image, an agent CLI and GPUs. Each benchmark's simulator has its own doctor (see the
# backend document in docs/).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/host_env.sh"
IMAGE="${RAX_AGENT_IMAGE:-rax-auto-research:dev}"
failures=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures+1)); }
note() { echo "NOTE  $*"; }

# Host environment.
if [[ -x "$RAX_PYTHON" ]]; then
    "$RAX_PYTHON" -c 'import jinja2, msgpack, numpy, PIL, yaml' 2>/dev/null \
        && pass "host environment $RAX_HOST_ENV_ROOT ($("$RAX_PYTHON" --version 2>&1))" \
        || fail "host environment $RAX_HOST_ENV_ROOT is incomplete (scripts/setup_host_env.sh)"
else
    fail "no Python at $RAX_PYTHON (scripts/setup_host_env.sh)"
fi

# Docker and the agent image.
if docker info >/dev/null 2>&1; then
    pass "Docker usable without sudo"
    docker image inspect "$IMAGE" >/dev/null 2>&1 && pass "agent image $IMAGE" \
        || fail "agent image $IMAGE missing (scripts/build_agent_image.py)"
else
    fail "Docker not usable by $(id -un) (add the user to the docker group)"
fi
[[ -e /dev/loop-control ]] && pass "loop devices (capped session workspaces)" || fail "/dev/loop-control missing"
for tool in mkfs.ext4 findmnt git-lfs; do
    command -v "$tool" >/dev/null 2>&1 || [[ -x "/sbin/$tool" || -x "/usr/sbin/$tool" ]] \
        && pass "$tool" || fail "$tool not found"
done

# Agent CLIs: native binaries, since they run inside the agent container.
clis=0
for cli in codex claude; do
    path="$(command -v "$cli" 2>/dev/null)" || continue
    real="$(readlink -f "$path")"
    if [[ "$(head -c 4 "$real" | od -An -c | tr -d ' ')" == '177ELF' ]]; then
        pass "$cli $("$real" --version 2>&1 | tail -n 1) ($real)"
        clis=$((clis+1))
    else
        fail "$cli at $real is not a native executable (install the standalone binary, not the npm wrapper)"
    fi
done
[[ $clis -gt 0 ]] || fail "no agent CLI: install Codex CLI or Claude Code"
key="$HOME/.codex/secrets/openrouter_api_key"  # The launcher's default --key-file.
[[ -r "$key" || -n "${OPENROUTER_API_KEY:-}" ]] && pass "OpenRouter key (Gemini tool, OpenRouter models)" \
    || note "no OpenRouter key at $key or in OPENROUTER_API_KEY: the Gemini tool and OpenRouter providers are unavailable"

# GPUs.
if gpus="$(nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader 2>/dev/null)"; then
    pass "GPUs:"; echo "$gpus" | sed 's/^/      /'
else
    fail "nvidia-smi failed"
fi

if [[ $failures -eq 0 ]]; then echo "All checks passed."; else echo "$failures check(s) failed."; exit 1; fi
