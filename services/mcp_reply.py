"""Agent-visible MCP replies: evaluation fields hidden, images encoded.

Shared by the frontend and every simulator backend. Scores and intermediate success stay
on the operator side; only a terminal outcome is disclosed, as task_complete.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from services.json_io import jsonable

# These fields remain available to the simulator-side recorder and evaluator,
# but their raw fields are never part of an agent-visible MCP result. Terminal
# success is disclosed separately as task_complete. Operational status
# strings such as a cuRobo planner's ``status: Success`` are intentionally not
# removed: they describe whether a requested computation ran, not task outcome.
_TASK_EVALUATION_KEYS = {
    "native_end_flag",
    "native_reward",
    "native_score",
    "native_score_percent",
    "native_success",
    "operator_decision_source",
    "reward",
    "rewards",
    "reward_state",
    "score",
    "success",
    "terminated",
    "truncated",
    "valid_for_success_rate",
}

_jsonable = jsonable


def _agent_safe(value: Any) -> Any:
    """Hide scores and intermediate success; disclose native terminal outcome."""

    if isinstance(value, dict):
        result = {
            str(key): _agent_safe(item)
            for key, item in value.items()
            if str(key).lower() not in _TASK_EVALUATION_KEYS
        }
        if "terminated" in value or "truncated" in value:
            result["episode_ended"] = bool(
                value.get("terminated", False) or value.get("truncated", False)
            )
            if result["episode_ended"] and "success" in value:
                result["task_complete"] = bool(value["success"])
        return result
    if isinstance(value, (list, tuple)):
        return [_agent_safe(item) for item in value]
    return _jsonable(value)


def _png_data(value: Any) -> str:
    """Encode a uint8 RGB array as an MCP image content payload."""

    from PIL import Image

    array = value
    if getattr(array, "ndim", 0) == 2:
        array = array[..., None]
    if array.shape[-1] == 1:
        array = array[..., 0]
    elif array.shape[-1] > 3:
        array = array[..., :3]
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def _depth_png_data(value: Any) -> tuple[str, dict[str, Any]]:
    """Encode distance-to-image-plane meters as lossless uint16 millimeters."""

    import numpy as np
    from PIL import Image

    depth = np.asarray(value, dtype=np.float32)
    if depth.ndim == 3 and depth.shape[-1] == 1:
        depth = depth[..., 0]
    if depth.ndim != 2:
        raise ValueError(f"Expected a 2-D depth map, got shape {depth.shape}")
    valid = np.isfinite(depth) & (depth > 0)
    millimeters = np.zeros(depth.shape, dtype=np.uint16)
    if valid.any():
        scaled = np.rint(np.clip(depth[valid] * 1000.0, 0, 65535)).astype(np.uint32)
        millimeters[valid] = scaled.astype(np.uint16)
    stream = io.BytesIO()
    Image.fromarray(millimeters).save(stream, format="PNG")
    valid_values = depth[valid]
    stats = {
        "shape": [int(depth.shape[1]), int(depth.shape[0])],
        "encoding": "uint16_png",
        "unit": "millimeters",
        "meters_per_unit": 0.001,
        "invalid_value": 0,
        "invalid_pixels": int((~valid).sum()),
        "clipped_pixels": int((valid & (depth * 1000.0 > 65535)).sum()),
        "min_meters": float(valid_values.min()) if valid_values.size else None,
        "max_meters": float(valid_values.max()) if valid_values.size else None,
    }
    return base64.b64encode(stream.getvalue()).decode("ascii"), stats
