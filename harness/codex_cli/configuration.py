"""Codex configuration helpers: the status line, subagent settings and a TOML writer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib


# Keep costly robot runs legible without inheriting the operator's unrelated TUI
# preferences. The thread ID also gives the operator a stable audit identifier
# when several trials are running concurrently.
ROBOT_STATUS_LINE = [
    "model-with-reasoning",
    "run-state",
    "context-remaining",
    "used-tokens",
    "thread-id",
]


def subagent_settings(cap):
    """Codex multi-agent settings for at most `cap` concurrent subagents (0 disables them)."""
    settings = {'features': {'multi_agent': cap > 0, 'multi_agent_v2': cap > 0}}
    if cap:
        settings['agents'] = {'enabled': True, 'max_concurrent_threads_per_session': cap}
    return settings


def _toml(value):
    """Encode the JSON-shaped subset used by provider and MCP configuration."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(_toml(str(k)) + " = " + _toml(v) for k, v in value.items()) + "}"
    raise ValueError(f"Unsupported provider configuration type: {type(value).__name__}")


def _write_config(path: Path, value: dict) -> None:
    data = "".join(f"{_toml(key)} = {_toml(item)}\n" for key, item in value.items())
    # Validate our encoding before publishing any configuration.
    tomllib.loads(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)
