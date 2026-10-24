#!/usr/bin/env bash
# Shared native LIBERO-PRO environment. Source this file from bash.
# Only the simulator subprocess uses this interpreter (the official LIBERO-PRO package, unmodified,
# imported from the pinned checkout); host harness processes stay in runtime/envs/host and never
# import libero, robosuite or mujoco.

LIBERO_PRO_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve symlinks: conda records its prefix in scripts, and worktree links are temporary.
LIBERO_PRO_RUNTIME_ROOT="$(readlink -f "${LIBERO_PRO_RUNTIME_ROOT:-${RAX_RUNTIME_ROOT:-$LIBERO_PRO_PROJECT_ROOT/runtime}}")"
LIBERO_PRO_ENV_ROOT="${LIBERO_PRO_ENV_ROOT:-$LIBERO_PRO_RUNTIME_ROOT/envs/libero_pro}"
LIBERO_PRO_SOURCE="$(readlink -f "${LIBERO_PRO_SOURCE:-$LIBERO_PRO_PROJECT_ROOT/LIBERO-PRO}")"
LIBERO_PRO_PYTHON="${LIBERO_PRO_PYTHON:-$LIBERO_PRO_ENV_ROOT/bin/python}"
# The runtime-owned LIBERO config and data tree (scripts/setup_libero_pro_data.py): the checkout
# stays clean and LIBERO never prompts for paths.
LIBERO_PRO_DATA="${LIBERO_PRO_DATA:-$LIBERO_PRO_RUNTIME_ROOT/libero-pro}"
export LIBERO_PRO_PROJECT_ROOT LIBERO_PRO_RUNTIME_ROOT LIBERO_PRO_ENV_ROOT LIBERO_PRO_SOURCE LIBERO_PRO_PYTHON LIBERO_PRO_DATA
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$LIBERO_PRO_RUNTIME_ROOT/pip-cache}"
export HF_HOME="${HF_HOME:-$LIBERO_PRO_RUNTIME_ROOT/huggingface-cache}"
