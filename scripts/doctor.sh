#!/usr/bin/env bash
# Check that this machine can run auto-research sessions: the host environment (also the agent's
# Python), the sandboxes, an agent CLI and GPUs. Each benchmark's simulator has its own doctor (see
# the backend document in docs/).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/host_env.sh"
failures=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures+1)); }
note() { echo "NOTE  $*"; }

# Host environment.
if [[ -x "$RAX_PYTHON" ]]; then
    "$RAX_PYTHON" -c 'import cv2, h5py, jinja2, msgpack, numpy, PIL, scipy, torch, yaml' 2>/dev/null \
        && pass "host environment $RAX_HOST_ENV_ROOT ($("$RAX_PYTHON" --version 2>&1))" \
        || fail "host environment $RAX_HOST_ENV_ROOT is incomplete (scripts/setup_host_env.sh)"
else
    fail "no Python at $RAX_PYTHON (scripts/setup_host_env.sh)"
fi

# Sandboxes: bubblewrap runs submitted bundles (and Claude Code's commands, with socat); Codex
# confines the agent's commands with its own sandbox.
if bwrap --ro-bind / / --unshare-all --die-with-parent true >/dev/null 2>&1; then
    pass "bubblewrap $(bwrap --version 2>&1 | tail -n 1) (bundle sandbox)"
else
    fail "bubblewrap missing or unable to create unprivileged namespaces: $(bwrap --ro-bind / / --unshare-all true 2>&1 | tail -n 1)"
fi
command -v socat >/dev/null 2>&1 && pass "socat (Claude Code's sandbox)" \
    || note "no socat: Claude Code's sandbox needs it (Codex does not)"
command -v git-lfs >/dev/null 2>&1 && pass "git-lfs" || fail "git-lfs not found"

# Agent CLIs: installed on the host, each with its own login.
clis=0
command -v codex >/dev/null 2>&1 && { pass "codex $(codex --version 2>&1 | tail -n 1) ($(readlink -f "$(command -v codex)"))"; clis=$((clis+1)); }
command -v claude >/dev/null 2>&1 && { pass "claude $(claude --version 2>&1 | tail -n 1) ($(readlink -f "$(command -v claude)"))"; clis=$((clis+1)); }
[[ $clis -gt 0 ]] || fail "no agent CLI: install Codex CLI or Claude Code"
key="$HOME/.codex/secrets/openrouter_api_key"  # The launcher's default --key-file.
if [[ -r "$key" ]]; then
    pass "OpenRouter key $key (Gemini tool, OpenRouter models)"
elif [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    note "OPENROUTER_API_KEY serves the Gemini tool and Claude Code's openrouter provider; Codex's openrouter profile needs a key file (--key-file or $key)"
else
    note "no OpenRouter key at $key or in OPENROUTER_API_KEY: the Gemini tool and OpenRouter models are unavailable"
fi

# GPUs.
if gpus="$(nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader 2>/dev/null)"; then
    pass "GPUs:"; echo "$gpus" | sed 's/^/      /'
else
    fail "nvidia-smi failed"
fi

if [[ $failures -eq 0 ]]; then echo "All checks passed."; else echo "$failures check(s) failed."; exit 1; fi
