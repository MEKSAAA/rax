---
name: change-discipline
description: Keep a changelog of controller versions with the evidence behind each change, batch fixes into versions, and decide in advance when to switch methods instead of retuning. Read before changing the controller and when a method keeps failing.
---

# Change discipline: a changelog, batched fixes and knowing when to switch methods

## A changelog helps you attribute results

A `CHANGELOG.md` in `code/<project>/`, kept current, lets you tell later which change helped. A
useful entry per version holds:

- the version (a number the controller also logs at start-up) and the files changed;
- the evidence behind the change (episodes, k/n, the logged quantity) and the hypothesis it tests;
- the result of your offline regression suite, if you keep one
  ([evidence-and-regression](../evidence-and-regression/SKILL.md));
{% if agent_formal %}
- the result of the first episode in which the version ran without you: how far it got, the steps it
  used, the step that failed.
{% else %}
- the bundle and the result of its rehearsal: whether it ran cleanly to its end, whether the task
  succeeded, the steps it used, the step that failed.
{% endif %}

## Batching fixes

Collecting fixes and releasing them together as one version, each with its own hypothesis and
changelog line, has worked better than one version per fix.
{% if agent_formal %}
One episode in which the version runs as far as it can without you tests the whole batch, and the
changelog keeps each fix attributable.
{% else %}
One rehearsal per batch is affordable, one per threshold is not, and the changelog keeps each fix
attributable.
{% endif %}
When a batch makes things worse, replaying archived episodes shows which decisions changed, so you
can revert the fixes the evidence does not support. Method changes and threshold tuning are easier
to read apart in separate batches.

## When to switch methods

It pays to decide in advance when you will give up on a method. A rule of thumb:
when a few consecutive versions of a method all fail
{% if agent_formal %}
in the episodes they ran without you,
{% else %}
in rehearsal,
{% endif %}
it is usually time to try another method (another contact strategy, approach geometry or
primitive) rather than another threshold. A method that succeeds only with your help has not yet
succeeded. Writing the decision and its evidence in the changelog makes it easier to revisit; a
per-phase funnel over those runs ([prioritize-by-funnel](../prioritize-by-funnel/SKILL.md)) shows
which phase's method to question.
