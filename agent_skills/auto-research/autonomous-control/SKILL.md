---
name: autonomous-control
{% if agent_formal %}
description: Run and debug Python robot control in the development container, and build a controller you supervise live. Use for closed-loop scripts, switching between your script and direct control, and run artifacts.
{% else %}
description: Run and debug Python robot control in the development container, then register, rehearse and submit a frozen controller. Use for closed-loop scripts, switching between your script and direct control, and run artifacts.
{% endif %}
---

# Autonomous control

Follow the workflow in AGENTS.md. The environment and API are external services,
reachable only through MCP.

## Start from your successful exploration

Read the saved successful strategy and decisive frames in `memory/`. Create a
project under `/workspace/code/<project>/` and turn one useful maneuver into a
Python function that receives `ctx` and explicit inputs. Test it in the current
exploration scene using the ordinary Python example below. Inspect its final
images and logs, correct it, and repeat before composing the full `main(ctx)`.

Separate reusable motion logic from scene-specific inputs: reacquire object
locations from current observations instead of embedding one layout's positions.

## Turn successful control into automated scripts

{% if agent_formal %}
After succeeding through direct robot control, reproduce the decisions that made
it work in automated observation–decision–action steps that run without you
wherever they can. Use current observations to adapt to new layouts, rather than
blindly replaying one episode's actions.
{% else %}
After succeeding through direct robot control, reproduce the decisions that made
it work in an automated observation–decision–action script that runs without you.
Use current observations to adapt to new layouts, rather than blindly replaying
one episode's actions.
{% endif %}

- Hard-code reusable motion functions and decision rules using MCP calls{% if robot.plan %},
  including `{{ robot.plan }}`,{% endif %} when suitable.
- Use Gemini when simple perception rules are insufficient. Write your own
  prompts and response-processing code; see the [gemini skill](../gemini/SKILL.md).
- Practise and debug functions within the same exploration episode, using direct
  robot calls to rearrange objects into the test conditions you need rather than
{% if agent_formal %}
  starting a new episode, and use each episode's remaining steps.

Combine these approaches as needed.
{% else %}
  starting a new episode. Exploration outcomes are not scored, so use the remaining
  steps.

Combine these approaches as needed, and test the resulting closed-loop script in
exploration before rehearsing it.
{% endif %}

## Pressure-test across layouts

{% if agent_formal %}
Use all exploration episodes to practise with your controller and pressure-test it,
not just enough to obtain one success. Layouts vary between episodes; reacquire
objects and adapt approach poses instead of reusing layout-specific coordinates.
Within an episode, refresh observations after moving objects. In each new episode,
run the controller on the new layout, step in where it falls short, then fix the
code so it covers that case next time. Record each tested layout, which steps ran
without you, each failure and its fix in memory. Add observation-driven recovery for
expected perception or planning failures{% if robot.plan %}; the free-space-move skill covers
collision-aware alternatives{% endif %}. Do not claim untested generalization.

## Code you can supervise

Later, on new layouts, you run this controller as far as it goes, watch each step and
{% if wall_limit %}
handle the rest. Each such episode has a wall limit (`status.episode_timeout_seconds`,
startup included), so make that supervision quick:
{% else %}
handle the rest. Make that supervision quick:
{% endif %}

{% if method_skills %}
- Split the pipeline into steps, each with an observable precondition and
  completion check, and build each step, its escalation and its state as
  [engineer-the-controller](../engineer-the-controller/SKILL.md) describes.
- Make the pipeline resumable at any step, and give every motion that can fail a
  fallback.
{% else %}
- Split the pipeline into steps, each with an observable precondition and
  completion check.
- Give each step a function `fn(ctx, ...)` that checks its preconditions from fresh
  observations, acts, verifies the result, and returns a small dict with `ok`, what
  it measured and, on failure, why. It returns instead of improvising when the
  scene is outside what it was tested on.
- Make the pipeline resumable at any step, and give every motion that can fail a
  fallback.
- Keep tunable values as named constants at the top of a module, with the range
  you tested.
{% endif %}

`controller.py:main(ctx)` chains the same steps end to end and stops at the first one
that fails. `code/<project>/README.md` says how to run each step from ordinary Python
and its preconditions; write the {% if carries_handoff %}handoff and tips as transfer-handoff and
experience-memory describe{% else %}tips as experience-memory describes{% endif %}.
{% else %}
Use all exploration episodes for development and robustness tests, not just enough
to obtain one success. Layouts vary between episodes; reacquire objects and adapt
approach poses instead of reusing layout-specific coordinates. Within an episode,
refresh observations after moving objects. Use new episodes to test different
layouts and the full controller, and keep episodes to rehearse the final version
(AGENTS.md step 3). Record each tested layout, outcome, failure and fix in memory;
agent-assisted recovery is debugging, not an autonomous success. Add
observation-driven recovery for expected perception or planning failures{% if robot.plan %}; the
free-space-move skill covers collision-aware alternatives{% endif %}. Do not claim untested
generalization.
{% endif %}

## Exploration: ordinary Python

{% if multi_env %}
Follow [multi-env exploration](../multi-env-exploration/SKILL.md) to start or continue
the environment you use (a subagent uses the one it was given).
{% endif %}
Python runs in the same development container as you, with normal files, stdout and
stderr.

{% if multi_env %}
### Keep environment selection outside the controller
{% else %}
### Write the controller against the supplied context
{% endif %}

{% if agent_formal %}
Write reusable functions against a supplied context `ctx`, so the same code runs in
every episode:

```python
# code/reach/controller.py
{% else %}
Write reusable functions against a supplied context `ctx`. The entrypoint below is
the same in exploration, rehearsals and the formal batch:

```python
# code/reach/controller.py — registered controller code
{% endif %}
def main(ctx):
    reply = ctx.call("{{ robot.observe }}")
    # Your observation–decision–action loop:
    # ctx.call("libero_step", actions=action_rows)  # official rows: position and rotation deltas, gripper
```

Only the development wrapper opens the context:

```python
{% if agent_formal %}
# Wrapper script, outside code/reach
{% else %}
# Development wrapper, outside the registered code/reach directory
{% endif %}
import sys
from api.runtime import Context

sys.path.insert(0, "/workspace/code/reach")
from controller import main

with Context() as session:
{% if multi_env %}
    main(session.env(env_id))  # The environment you use.
{% else %}
    main(session)
{% endif %}
```

Keep this wrapper under `/workspace/experiments/` and run it from the shell.
{% if multi_env and agent_formal %}
`main(ctx)` and its helpers use the supplied context unchanged, passing `ctx` down:
no `env_id`, `.env(...)` or new `Context()`.
{% elif multi_env %}
Submitted `main(ctx)` and its helpers use the supplied context unchanged, passing
`ctx` down: no `env_id`, `.env(...)` or new `Context()`; rehearsals and the formal
batch supply their one environment.
{% elif agent_formal %}
`main(ctx)` and its helpers use the supplied context unchanged, passing `ctx` down:
no new `Context()`.
{% else %}
Submitted `main(ctx)` and its helpers use the supplied context unchanged, passing
`ctx` down: no new `Context()`.
{% endif %}
`Context()` connects to the same MCP endpoint as you do. Your function can send
chosen official action rows, or repeatedly observe, choose rows and inspect feedback
using heuristics or Gemini.
Return whenever you want to inspect images and choose new actions yourself. The
scene retains the script's actions; returning or closing the connection does not
reset or finish the episode.

{% if multi_env %}
Robot calls run one at a time within each environment, but a script does not hold the
robot: other calls can come between its calls. For concurrent scripts, see
multi-env-exploration.
{% else %}
Robot calls run one at a time, but a script does not hold the robot: other calls can
come between its calls.
{% endif %}
Run one controller at a time, and stop it before acting directly. Interrupting
Python does not cancel an already-dispatched action: after an interruption or an
ambiguous motion error, check `{{ robot.status }}` and a fresh observation before
continuing. Stop when the episode ends.

You may test the entire controller in the current episode, including stepping in
yourself.

## MCP and files

`ctx.call(tool_name, **arguments)` uses the live tool schemas and returns MCP
content blocks. JSON metadata is in the first text block; images remain image
blocks. Inspect current tool schemas instead of inventing arguments.
Calls reporting an MCP error raise `RuntimeError`; inspect the call's record in the
run's `mcp.jsonl` (`artifacts.mcp_trace`) rather than blindly retrying motion.

Use normal Python for files, images and recording. Returned artifact paths are
relative to `/workspace`; save script replies and prints to local files for
debugging. Robot replies, per-call images and saved frames are published
automatically; ordinary shell stdout/stderr needs your own redirection if you want
a saved log.

{% if not agent_formal %}
## Rehearsal and submission

Put `main(ctx)` in `controller.py`, with all required modules and data files in its
source directory. `register(source="code/reach")` freezes that directory.
{% if multi_env %}
`rehearse(bundle=...)` closes all live exploration environments and runs the frozen
controller alone with one fresh environment in a fresh container. Wait for other
workers and outstanding robot calls before invoking it.
{% else %}
`rehearse(bundle=...)` closes the live exploration episode and runs the frozen
controller alone in a fresh episode and container. Wait for outstanding robot calls
before invoking it.
{% endif %}
{% if wall_limit %}
A rehearsal and each formal episode have the same wall limit
(`status.episode_timeout_seconds`, startup included). Leave timing margin; formal
episodes may run in parallel and slower. A timeout stops the container, closes the
environment and returns an error with diagnostic artifact paths; it does not count as
a pass. `status.bundles` lists registered bundles and whether each has passed a
rehearsal.
{% else %}
A rehearsal and each formal episode run until the task's step limit or until the
controller returns. `status.bundles` lists registered bundles and whether each has
passed a rehearsal.
{% endif %}

Rehearsals and the formal batch pass `main` its context as the sole argument. This
channel exposes robot and Gemini calls, not `start_episode`, `finish`, `register`, `rehearse` or
`submit`. Rehearsals and the formal batch use `/workspace` as the working directory
and keep the project's path, e.g. `/workspace/code/reach`, but read-only. Use paths
relative to `__file__` for bundled inputs. They receive only their own frames, under
`/workspace/runtime/autonomous_controller/runs/<run-id>/frames/`, and the submitted
project, not the live workspace, previous runs or the exploration {% if multi_env %}environments{% else %}episode{% endif %}.

Write generated files under `/workspace/output` in both settings; it is fresh and
byte-capped in rehearsals and the formal batch. After the container stops, safe
regular files are copied to
`/workspace/runtime/autonomous_controller/runs/<run-id>/exports/`. Storage quotas
apply; links and special files are rejected. After the episode ends, stop calling
tools and finish file writes promptly (up to ten seconds within the original
deadline).

Results point to frozen code, stdout/stderr logs, `mcp.jsonl`, per-call responses,
images and full frame sequences. Read only what you need, directly with ordinary
file tools.
{% if method_skills %}
[rehearse-the-controller](../rehearse-the-controller/SKILL.md) says how to use the
rehearsals as the measure of the controller.
{% endif %}
{% endif %}
