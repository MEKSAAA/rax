#!/usr/bin/env bash
# Small end-to-end LIBERO run using an existing RAX provider profile.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
source scripts/host_env.sh

: "${RAX_PROVIDER_HOME:?Set RAX_PROVIDER_HOME to the directory containing your RAX provider profile}"
profile="${RAX_PROFILE:-chatgpt}"
[[ -f "$RAX_PROVIDER_HOME/$profile.config.toml" ]] || {
    echo "Missing provider profile: $RAX_PROVIDER_HOME/$profile.config.toml" >&2
    exit 2
}
if ! bwrap --help | grep -q -- '--size'; then
    echo 'RAX needs a newer bubblewrap supporting --size. See docs/LOCAL_VALIDATION.md.' >&2
    exit 2
fi
prepared="$("$RAX_PYTHON" scripts/configure_auto_research.py \
    --task "${RAX_TASK:-libero_goal_0}" --sim-gpu "${RAX_GPU:-1}" \
    --episodes "${RAX_EPISODES:-8}" --formal-episodes "${RAX_FORMAL_EPISODES:-3}" \
    --formal-workers 1 --exploration-envs 1 --subagents 0 \
    --external-perception off --demonstration-context none)"
printf '%s\n' "$prepared"
config="${prepared##*$'\n'}"
[[ -f "$config" ]] || { echo 'Session configuration was not created' >&2; exit 1; }
exec bash scripts/start_auto_research_agent.sh --config "$config" \
    --provider-home "$RAX_PROVIDER_HOME" --profile "$profile" --non-interactive
