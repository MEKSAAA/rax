---
name: rehearse-the-controller
description: Use rehearsals as the measure of the controller - the rehearsal pass rate predicts the formal batch, so rehearse early and regularly, read each failure as a formal failure, and keep a time table per phase against the wall limit. Read before registering a version and when deciding what to submit.
---

# Rehearse the controller: the pass rate is the measure

**What it is:** `register(source=...)` freezes `code/<project>/`, and `rehearse(bundle=...)` runs that
bundle alone in a fresh container and a fresh exploration episode, exactly as a formal episode runs
it: no live intervention, no edits and no retries. A rehearsal consumes one exploration episode.

**Why it matters:** the rehearsal pass rate predicts the formal result; successes in episodes the
agent helped do not. A version that rarely passes its rehearsals rarely passes formal episodes, however
often it succeeded with help.

## The pass rate as your measure

- **The rehearsal pass rate (k/n with its interval) is the best estimate of the formal result you
  have.** Stating it in `memory/PROGRESS.md` after each rehearsal keeps your judgement honest.
  `status.bundles` lists each registered bundle and whether it has a clean rehearsal
  (`rehearsal_qualified`); the `rehearse` reply and the run's `result.json` give the outcome.
  Agent-assisted successes measure you, not the code.
- **Rehearsing as soon as the controller runs end to end** paid off even when a failure was expected:
  where the code alone breaks is worth an episode.
- **A regular cadence** (roughly every third exploration episode, and after each batch of changes,
  [change-discipline](../change-discipline/SKILL.md)) keeps the pass rate current. Planning the
  rehearsals ahead helps.
- **A failed rehearsal is best read as a failed formal episode.** If it fails where an agent-assisted
  episode succeeded, the difference is in the code: missing state, a decision you made by hand, an
  untested branch.
- **Rehearse the bundle you intend to submit.** `submit` accepts only a bundle whose own rehearsal ran
  cleanly to its end; a clean run need not succeed, but submitting a version that never succeeded
  alone is a bet against your own evidence.
- **Several failed rehearsals in a row are a signal to step back** and compare methods for the phase
  that loses the most, before touching its thresholds again; how many is your call.

## Time per phase

- **Log the current phase coarsely.** Three to five phases defined by what the task needs done (for
  example approach, grasp, carry, place), not the individual steps inside them, keep rehearsals
  comparable. Logging each phase's start and end step, wall time and retries to the controller's
  stdout puts the numbers in each rehearsal's `stdout.log`.
- **A time table** over rehearsals (phase, steps, wall seconds, retries) shows the margin: the step
  limit and `status.episode_timeout_seconds` minus the time of each successful rehearsal. Successes
  with little of the limit to spare tend to fail on harder layouts; formal episodes may also run
  slower, in parallel.
- **Where time usually went:** standing still to look or wait, short cautious actions where a longer
  one is safe, approaches in several stop-and-turn segments, alignment more precise than the next
  phase needs ([match-precision-to-error](../match-precision-to-error/SKILL.md)), a phase that keeps
  failing for the same reason. A per-phase time budget from the table, enforced as the phase's
  timeout ([engineer-the-controller](../engineer-the-controller/SKILL.md)), keeps the total in check.
  Keep the recoveries the rehearsals show are needed: a fast failure scores nothing.
