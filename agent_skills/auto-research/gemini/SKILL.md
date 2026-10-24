---
name: gemini
{% if formal_episode %}
description: Use Gemini 3.8 Flash through MCP for text/image prompts, structured JSON output and function calling during this episode, from tool calls or your own Python.
{% else %}
description: Use Gemini 3.8 Flash through MCP for text/image prompts, structured JSON output, client function calling and API usage tracking. Use for interactive prompt testing or model calls inside Python controllers.
{% endif %}
---

# Gemini

Write your own prompts and response-processing code. The tool adds no perception
instructions, task-solving logic or action selection.
Use the `gemini_generate` tool for interactive calls; in a running
{% if formal_episode %}
robot script, use `ctx`. Ordinary Python in this episode connects with `Context()`.
{% elif agent_formal %}
robot script, use `ctx`. Ordinary development Python connects with `Context()` and
passes it to `main(ctx)`; see autonomous-control.
{% else %}
robot script, use `ctx`. Ordinary development Python connects with `Context()`;
rehearsals and the formal batch supply the context to `main(ctx)`; see
autonomous-control.
{% endif %}
{% if multi_env %}

Gemini calls take no `env_id`; a bound exploration context forwards them. Supply
images from the intended environment; Gemini does not pick a scene for you. All
workers share one Gemini budget.
{% endif %}

## Recommended practice

Prefer simple, hard-coded perception rules when they work reliably across layouts.
Gemini calls add cost and latency, and outputs can vary between calls. Use Gemini
when those rules are insufficient, primarily for visual and spatial perception:
identifying objects, locating them in images, and interpreting spatial relationships.
Test other uses, such as structured decisions, before relying on them.

### Pointing at targets

Ask for the pixel of a named, distinctive point on the target in each camera view.
{% if camera_geometry %}
Accept the point only when `libero_pixel_to_position` reports a small
`depth_spread_m` and the other camera gives the same world-frame point.
{% else %}
Accept it only when the other camera marks the same physical feature.
{% endif %}
Keep a non-Gemini fallback.

## MCP usage

The tool follows OpenRouter's **chat-completions** format, not the Google SDK's
`contents`/`generationConfig` format. Read [API reference](references/api.md) for
request fields, structured-output schemas, conversation history and a complete
function-calling round trip. Supply only fields you need. The model and credentials
are fixed for you.

For a visual prompt inside `main(ctx)`:

```python
import base64
import json
from pathlib import Path

png_bytes = Path(__file__).with_name("crop.png").read_bytes()  # Or in-memory PNG bytes.
image = {"type": "image", "mimeType": "image/png",
         "data": base64.b64encode(png_bytes).decode("ascii")}
reply = ctx.call("gemini_generate", messages=[{
    "role": "user",
    "content": [
        {"type": "text", "text": "Describe the visible objects and their relative positions."},
        image,
    ],
}], max_tokens=256)
result = json.loads(reply["content"][0]["text"])
print(result["message"], result["usage"], result["budget"])
```

`max_tokens` covers reasoning as well as the visible answer. Without a `reasoning`
field, Gemini chooses its own reasoning depth and can spend most of a ~1000-token
budget before answering. For higher reasoning, or open-ended visual analysis,
increase `max_tokens` well above the expected answer length (for example 4096–8192).
For short answers, set `reasoning={"effort": "low"}`. `finish_reason="length"`
means the answer is incomplete: retry with a larger budget or lower effort; never
act on a partial answer.

Use ordinary Python for file reads, image processing and base64 encoding. For JPEG,
set `mimeType` to `image/jpeg`.
{% if formal_episode %}
Any readable workspace image works, including this episode's frames under
`runtime/autonomous_controller/runs/`.
{% elif agent_formal %}
Exploration can use any readable workspace image; later episodes see only the carried
folders.
{% else %}
Exploration can use any readable workspace image. Rehearsals and the formal batch
can read only their bundle, `/workspace/output` and their own frames.
{% endif %}
The tool accepts image bytes, not file paths or remote URLs. No SDK helper is needed.
Do not print large base64 payloads into agent context just to pass them back.

For a live image, reuse an image content block returned by `ctx.call("{{ robot.observe }}")`
directly (select the intended camera from the response order/metadata).

Limits: eight images per call, 4 MiB per image, 8 MiB total, at most 4096×4096
pixels per image; PNG/JPEG only, no animation. Resize or compress locally if needed.
Supplied images are controller input, not trusted evidence of task success.
The tool calls Gemini 3.8 Flash through OpenRouter; credentials stay outside this
container. Results contain `message`, `finish_reason`, `usage`, `cost_usd`
and cumulative/remaining `budget`; missing provider cost is `null`.
Spending is capped; see [Spending limit](references/api.md#spending-limit), and check
the returned `budget` before further calls. Function requests in
`message.tool_calls` are data, never automatically executed. Validate and dispatch
them in your script; robot actions still go through robot MCP. Preserve the whole
assistant message, including `reasoning_details`, when returning tool results.
