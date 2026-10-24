---
name: match-precision-to-error
description: Compare the precision a step needs with the precision the controller delivers before aligning or retrying, and change the design when the two do not fit. Read when alignment keeps failing or takes much of an episode.
---

# Match the precision a step needs to the precision you can deliver

**The idea:** align only as precisely as the next phase needs and the controller delivers. A
window narrower than the controller's error can exhaust the budget through retries. Clipping an
unreachable target hides the mismatch; excess alignment spends time without improving the next
phase. Measure the required tolerance and the controller's error before choosing a design.

## Both sides can be measured

- **The error you deliver.** For a primitive that positions the body, a foot or a hand: where it
  ends relative to where you aimed over several executions (mean, spread, count), separately for
  each posture, load and speed you use.
- **The tolerance you need.** For a step that depends on that position: the window of error within
  which the step still succeeds, from outcome against offset at the moment of contact.

## Comparing them before retrying

- **Tolerance wider than the error:** once inside it, more precision costs time and buys nothing.
- **Tolerance narrower than about two standard deviations of the error:** retries mostly fail, and a
  design change helps more:
  - a closed-loop final approach, re-measuring between small corrections;
  - a more tolerant geometry: a contact that self-centres, a wider capture region, another contact
    point;
  - a finer motion the controller already offers (a shorter or slower variant of a primitive, a
    smaller arm or torso move), once characterized ([{{ robot.tracking_skill }}](../{{ robot.tracking_skill }}/SKILL.md)).
- The chance that one attempt succeeds follows from the two distributions, and with it how many
  attempts fit into the remaining budget. Prefer designs where a single attempt usually works.

## Clipping is reported, not silent

A target clamped into the range you have measured, or into the range a recording covers, hides the
real problem. When a target lies outside it, measuring that region, re-planning or switching methods
works better, and the clipping or projection belongs in your log.
