---
name: avoid-overfitting
description: Warning signs that code or notes fit the layouts you happened to see (encoded poses, layout-specific fallbacks, constants tuned on one episode, replayed trajectories) and what transferred instead. Read when reviewing your controller and before relying on a constant.
---

# Avoid overfitting to particular layouts

{% if agent_formal %}
Every episode draws a new layout from the distribution TASK.md describes (its randomization), and
later episodes are layouts you have never seen. Code that works only where the objects happened to
be during exploration does nothing for you there, however well it worked before.
{% else %}
Every exploration episode draws a new layout from the distribution TASK.md describes (its
randomization), and the formal episodes are layouts you have never seen. A controller that works
only where the objects happened to be during exploration fails in the formal batch, however well it
worked in exploration.
{% endif %}

## Warning signs in code and notes

- **Encoded poses.** Hard-coded positions, distances, headings, pixel coordinates or joint targets
  that record where an object was in one episode.
- **Fallbacks that assume a layout.** "If the target is not found, walk at heading X", or "after
  step Y the target is straight ahead". When perception fails, a search
  ([object-localization](../object-localization/SKILL.md)) transfers; a remembered pose does not.
- **Constants tuned on one episode** and never checked across the full ranges in TASK.md.
- **Replayed trajectories.** A recorded motion replayed as it is only fits the layout it was
  recorded in. As a primitive whose effect you measure and re-aim, it can transfer.
- **Inherited assumptions.** Code or notes from earlier attempts carry assumptions about where
  things are; listing each phase's assumptions and checking them against TASK.md exposes them.

## What has worked

- Perceiving the actual pose in each episode, acting relative to that measurement, and re-measuring
  after each move.
- Writing down, for each constant, what it encodes and over which range it has to hold; quantities
  measured online generalize better than fixed numbers.
- Walking through the extremes of TASK.md's ranges (nearest, farthest, most rotated, objects at
  opposite ends of their regions), asking what each phase does there, and practising the extremes
  that reasoning cannot settle.
