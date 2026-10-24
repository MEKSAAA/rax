"""Helpers shared by every benchmark's episode-review analysis (stdlib, plus PIL for frames).

The condensed tool trace, the final RGB frames, the one Gemini call through the OpenRouter
router, and the limits every review shares.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

REVIEWABLE_MODES = frozenset({"exploration", "rehearsal"})
MAX_TRACE_LINES = 160
MAX_OUTPUT_TOKENS = 6000
SESSION_BUDGET_USD = 10.0  # Operator-side; never charged to the agent's Gemini budget.
# Motion outcome fields of a tool reply's `transition` the trace keeps.
OUTCOME_KEYS = ("status", "reached", "stalled", "executed")


def read_json(path):
    """A JSON object from ``path``, or {} when it is missing or not an object."""
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _short(value, limit=160):
    text = json.dumps(value, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def condensed_trace(mcp_trace, outcome_keys=OUTCOME_KEYS):
    """One line per tool call: tool, compact arguments, step and motion outcome."""
    lines, start = [], None
    try:
        events = [json.loads(line) for line in Path(mcp_trace).read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    pending = None
    for event in events:
        start = start if start is not None else event.get("time", 0)
        if event.get("event") == "mcp_request":
            arguments = dict(event.get("arguments") or {})
            if isinstance(arguments.get("actions"), list):
                arguments["actions"] = f"<{len(arguments['actions'])} rows>"
            pending = f"{event.get('time', 0) - start:6.0f}s {event.get('tool')} {_short(arguments)}"
        elif event.get("event") == "mcp_result":
            value = event.get("returned") or {}
            transition = value.get("transition") or {}
            outcome = {k: transition[k] for k in outcome_keys if k in transition}
            line = pending or f"{event.get('time', 0) - start:6.0f}s {event.get('tool')}"
            lines.append(f"{line} -> step {value.get('step_id')}" + (f" {_short(outcome, 80)}" if outcome else ""))
            pending = None
    if len(lines) > MAX_TRACE_LINES:
        head = MAX_TRACE_LINES // 4
        lines = lines[:head] + [f"... {len(lines) - MAX_TRACE_LINES} calls omitted ..."] + lines[-(MAX_TRACE_LINES - head):]
    return lines


def rgb_images(paths, limit=3):
    """The first RGB PNGs among ``paths`` (depth PNGs are 16-bit and skipped)."""
    from PIL import Image
    images = []
    for path in paths:
        try:
            raw = Path(path).read_bytes()
            with Image.open(io.BytesIO(raw)) as frame:
                if frame.mode not in ("RGB", "RGBA"):
                    continue
        except OSError:
            continue
        images.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(raw).decode()})
        if len(images) == limit:
            break
    return images


def ask(router, prompt, content):
    """One Gemini call: (text, cost_usd). ``router`` is a ``GeminiRouter``; ``content`` the user
    message's parts (text and images)."""
    payload, reservation = router.prepare({"messages": [{"role": "system", "content": prompt},
                                                         {"role": "user", "content": content}],
                                           "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.2})
    result, _ = router.execute(payload, reservation)
    text = result["message"].get("content") or ""
    if isinstance(text, list):
        text = "\n".join(part.get("text", "") for part in text if isinstance(part, dict))
    return text.strip(), float(result.get("cost_usd") or 0.0)
