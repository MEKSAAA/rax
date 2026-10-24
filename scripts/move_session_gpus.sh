#!/usr/bin/env bash
# Trusted operator: move a stopped auto-research session's simulators to another GPU, then resume it with
# start_auto_research_agent.sh --resume.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT"
exec "$PROJECT_ROOT/runtime/envs/host/bin/python" -m harness.session_gpus "$@"
