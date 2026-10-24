---
name: evidence-and-regression
description: Habits of evidence (k/n with intervals, falsifiable claims, re-tests before reversals, probes before "cannot") and an offline regression suite that replays every archived episode through your decision code. Read before changing a parameter, concluding a method fails, or releasing a new controller version.
---

# Evidence and regression: count, falsify, re-test, replay

## Habits of evidence

- **Outcomes as k/n with an interval** say more than adjectives ("works", "reliable"). The Wilson
  95 % interval: with p = k/n and z = 1.96, centre (p + z²/2n) / (1 + z²/n) and half-width
  z·√(p(1−p)/n + z²/4n²) / (1 + z²/n). Even 3/3 is only [0.44, 1]; 0/3 is [0, 0.56]. Two rates whose
  intervals overlap widely are not yet different.
- **A single failure is a reason to look, not to retune.** Parameter changes on fewer than about
  three trials mislead, unless a logged quantity shows what went wrong and why the change fixes it.
- **Write down what would falsify a claim** before you rely on it: the observation that would show
  it wrong, and how many trials you need to see it.
- **Re-test before you reverse a conclusion.** A result that contradicts an earlier one deserves at
  least as many trials as the earlier one had; until then both are worth keeping, with the
  difference in conditions.
- **Probe before you conclude "cannot".** A premature verdict on the controller often comes from one
  slow, small probe. Trying a capability a few times (n ≥ 3) at two or more rates or magnitudes is
  cheap by comparison. The same holds for a contact method: a few attempts at each of at least two
  magnitudes, such as two closure levels or two offsets into the surface, before rejecting it.
{% if agent_formal %}
- **Keep sources apart.** Steps your code ran without you are evidence about the code; steps you
  steered by hand are evidence about the world and the controller, not about your code.
{% else %}
- **Keep sources apart.** Rehearsals, where your code ran alone, are evidence about the controller
  you will submit; episodes you steered by hand are evidence about the world and the robot's
  controller, not about your code.
{% endif %}

## An offline regression suite

Every archived episode is a test case. A script that replays all of them through your current
decision code after each change catches many problems before an episode is spent:

- **Inputs:** the recorded replies and frames of every run so far, under
  `runtime/autonomous_controller/runs/<run-id>/`: `mcp.jsonl` (every call and reply), the per-call
  responses and the saved frame sequences (`frame_sequence.manifest_path` in each motion reply).
{% if not agent_formal %}
  Rehearsals are the most valuable cases: their `stdout.log` holds your controller's own decision log,
  so you can compare the old decisions with the new ones.
{% endif %}
- **Checks that proved useful:** no exception; every action valid and within the reply's limits;
  decision time well within the wall budget; perception outputs against labels you added for
  decisive frames (an object's position judged from later frames, the frame where a phase should
  have ended). For an archived failure, mark the decision that went wrong and check that the new code
  decides differently there; for an archived success, list the decisions that changed and confirm
  each change is intended.
- **Limits:** the world does not respond to replayed actions, so after the first changed decision
  the recorded future no longer follows from your actions. The suite catches crashes and regressions
  cheaply; only live episodes measure outcomes.
