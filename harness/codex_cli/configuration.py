"""Codex configuration helpers: subagent settings and a TOML writer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib


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


def write_config(path: Path, value: dict) -> None:
    """Replace a Codex config.toml atomically (rewritten at every launch)."""
    data = "".join(f"{_toml(key)} = {_toml(item)}\n" for key, item in value.items())
    # Validate our encoding before publishing any configuration.
    tomllib.loads(data)
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)
    os.replace(temporary, path)
