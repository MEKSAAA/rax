---
name: autonomous-control
description: Run the pipeline you carried under code/ in this episode while monitoring it, handling states it does not cover, and adapting it to this layout.
---

# Autonomous control

Automate as much of this episode as you can with the pipeline you carried, and
supervise it live: let tested code do the work it covers and spend your turns on
monitoring, recovery and adaptation, within the limits AGENTS.md describes.

## Before running

From the notes you carried, identify the entry points, each step's preconditions and
completion checks, the layouts they were tested on, and the known failure signs and
recoveries. Observe the current scene and compare it with those preconditions. Adjust
named parameters for this layout before the first run when the notes say how, rather
than after a failure.

The code's pass/fail checks are its guesses at the task conditions; you decide from
the scene whether a condition holds.

## Run and monitor

Run the pipeline from ordinary Python, one step or a short run of steps at a time:

```python
import sys
from api.runtime import Context

sys.path.insert(0, "code/<project>")
from controller import main  # or the step functions code/<project>/README.md names

with Context() as ctx:
    result = main(ctx)
print(result)
```

Keep the script under `experiments/`, run it from the workspace and save its output to a file. Judge
each step from a fresh observation, not from its returned verdict; when they
disagree, go by the scene. Run one controller at a time.

## Handle what the pipeline does not cover

When a step fails, reports an uncovered state, or the scene is outside what the
notes say it was tested on:

1. Stop the running code before acting directly; check `{{ robot.status }}` and a fresh
   observation. Interrupting Python does not cancel an already-dispatched action.
2. Choose the cheapest adequate response:
   - rerun the step after adjusting a named parameter for this layout;
   - recover by hand with the robot tools, following manual-robot-control, until
     the next step's preconditions hold;
   - edit the step's code when the same fix will be needed again in this episode,
     keeping the change small and testing it on the next attempt.
3. Resume the pipeline at the next step whose preconditions hold.

Do not rewrite the pipeline from scratch or start long experiments. Change the
approach rather than repeating a failure, and never stop because one motion failed.

## Finish

The pipeline ending, or the code raising, is not the episode ending. Finish as
AGENTS.md describes.
