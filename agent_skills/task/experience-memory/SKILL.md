---
name: experience-memory
description: Save successful {{ robot.benchmark }} action tips and retrieve them for future similar actions with matching observable conditions within this task.
---

# {{ robot.benchmark }} experience memory

Memory under `memory/` belongs to this task and persists across exploration
episodes. Retrieve matching tips when controlling the robot or building automation.
At task start or after compaction, read `{{ vocab.memory }}/INDEX.md` if present and retrieve
only matching learned skills. Open linked evidence when applicability or outcome
is uncertain; do not load the entire history. Keep learned procedures under
`{{ vocab.memory }}/skills/`, separate from the provided skills.
{% if agent_formal %}
Notes you carry to later episodes state facts, not paths, as AGENTS.md step 4 says.
{% else %}
Rehearsals and the formal batch cannot read the editable workspace: put needed
procedures into the frozen controller before registration.
{% endif %}

## Record successful tips immediately

- As soon as an action produces a visibly successful, useful result for reuse
  in the same task, add or update a concise row in `{{ vocab.memory }}/TIPS.md`.
  A visible local result is enough to save the tip; episode-level confirmation
  can be added later.
- Record the observable preconditions, task phase, tool/action or reusable pose
  relation, visible effect, and exact manifest/frame evidence. State what made
  it work so later attempts avoid repeating exploration or a superseded failure.
- Update the matching tip when the same maneuver is refined. Apply it to future
  similar actions with matching observable conditions.
- A tip records the visible local effect. Episode-level confirmation comes from
  `{{ vocab.evaluate }}` or native `task_complete: true`.

{% if method_skills %}
## Confirmation levels and history

- A tip or learned skill is `observed` (a visible local result), `task-confirmed` (an
  episode your code ran ended with `task_complete: true`), or `refuted`: kept, with the
  evidence against it, so a later attempt does not rediscover it. Later contradicting
  evidence is added to the entry rather than deleting its history.
- Updating `{{ vocab.notes }}` at the end of each episode and before long analyses made
  compactions cheap.

{% endif %}
## Select

- Select successful experience with future reuse value. Motion manifests
  already preserve the complete executed actions, states, and ordered frames
  at the published paths returned by MCP under `{{ vocab.frames }}`.
- Promote a tip to a learned skill when it shows a reusable motion,
  contact strategy, recovery, or completed task procedure.
- Reference the exact manifest, step IDs, and decisive frame indices; keep PNGs,
{% if camera_geometry %}
  depth arrays, trajectories, and complete manifests at their published paths.
{% else %}
  trajectories and complete manifests at their published paths.
{% endif %}
{% if not agent_formal %}
- Record each example's `episode_mode` ({{ vocab.episode_modes }}), as the format
  reference shows.
{% endif %}
- Label an episode-level `task_complete: true` as task-confirmed; otherwise describe
  the visible local effect.

## Encode poses

Record executed actions and reusable pose recipes as
[this robot's pose records](../robot-interface/references/pose-records.md) define.
{% if demonstrations %}
If a procedure was adapted from a demonstration, retain its source and selected
images under `demonstration_provenance`.
{% endif %}

## Distill

Create or revise `{{ vocab.memory }}/skills/<slug>/SKILL.md` and `evidence.json`.
Keep procedures short; keep episode-specific values and evidence links in
`evidence.json`.

Update `{{ vocab.memory }}/INDEX.md` with the skill path, task/maneuver tags,
confirmation level,{% if not agent_formal %} distinct evidence modes,{% endif %} and last episode. A learned skill
may guide current control, but current perception and the live tool schemas take
precedence. Revise the skill when later evidence contradicts it; retain
provenance in evidence.

Read [references/format.md](references/format.md) only when creating or
updating a learned skill.
