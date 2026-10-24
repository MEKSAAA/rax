#!/usr/bin/env bash
# The harness's host environment (runtime/envs/host): launchers, MCP service, workspace generator
# and dashboard. Simulators run in their own environments. Source this file from bash.

RAX_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAX_RUNTIME_ROOT="${RAX_RUNTIME_ROOT:-$RAX_PROJECT_ROOT/runtime}"
RAX_HOST_ENV_ROOT="${RAX_HOST_ENV_ROOT:-$RAX_RUNTIME_ROOT/envs/host}"
RAX_PYTHON="${RAX_PYTHON:-$RAX_HOST_ENV_ROOT/bin/python}"

export RAX_PROJECT_ROOT RAX_RUNTIME_ROOT RAX_HOST_ENV_ROOT RAX_PYTHON
export PATH="$RAX_HOST_ENV_ROOT/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"
export HF_HOME="${HF_HOME:-$RAX_RUNTIME_ROOT/huggingface-cache}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$RAX_RUNTIME_ROOT/pip-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$RAX_RUNTIME_ROOT/xdg-cache}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$RAX_RUNTIME_ROOT/xdg-runtime}"

mkdir -p "$XDG_RUNTIME_DIR" "$HF_HOME" "$XDG_CACHE_HOME"
chmod 700 "$XDG_RUNTIME_DIR"
