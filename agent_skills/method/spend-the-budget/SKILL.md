---
name: spend-the-budget
description: Plan and spend the step limit, the wall-clock limit and the model calls deliberately - action length by risk, scripts for the fast loop, budget checks before long sequences. Read when planning an episode or when a budget runs low.
---

# Spend the budgets deliberately

{% if formal_episode %}
Your episode has three budgets: its step limit (`episode_limits` in every robot reply), its wall-clock
limit (`status`, startup included, your own thinking too) and your model calls. The episode ends at the
step limit; the wall limit stops it wherever it stands.
{% else %}
Each episode has a step limit (`episode_limits` in every robot reply) and a wall-clock limit
(`status.episode_timeout_seconds`, startup included, your own thinking too); your model calls are capped
for the whole task. An episode ends when its steps run out; the wall limit stops it wherever it stands.

- **Counts come from `status`, not from notes.** How many exploration episodes remain, and the
  budgets left, are in `status`; counts remembered from notes or earlier context are a common
  source of error. When another source disagrees on a fact (your notes, a code comment, an old plan),
  TASK.md, `status` and the live tool schemas hold; noting the conflict in `memory/PROGRESS.md` keeps
  the stale information from coming back.
{% if agent_formal %}
- **A budget plan** in `memory/PROGRESS.md` helps: what each remaining episode is for and what it
  should measure, and in which of them your controller runs as far as it can on its own. Revising it
  after each episode keeps it useful; episodes without a stated purpose were usually wasted.
{% else %}
- **A budget plan** in `memory/PROGRESS.md` helps: what each remaining episode is for and what it
  should measure, and which of them are rehearsals
  ([rehearse-the-controller](../rehearse-the-controller/SKILL.md)). Revising it after each episode keeps
  it useful; episodes without a stated purpose were usually wasted.
{% endif %}
{% endif %}
- **Action length can follow risk:** long calls (up to 100 frames, 2 s) for open-space motion and
  settled holds, short ones near contact and obstacles.
- **A script for the fast loop** runs observe–decide–act in Python far more efficiently than deciding
  each step by hand. Thinking between steps and letting the script act within them works well.
- **When an approach proves infeasible, a different strategy** usually beats small variations of
  the same one; identical repeated actions rarely change the outcome.
- **A budget check before a long sequence** (enough steps and wall time to finish it, and a plan for
  when they do not suffice) avoids ending mid-way.
- **The end of the episode is usable:** frames past the step limit are cut, not refused, so no step
  needs to stay unused.
