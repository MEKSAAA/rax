# Learned memory format

Store learned skills only below `{{ vocab.memory }}/skills/`, each under a lowercase
hyphenated slug.

## `TIPS.md`

One row per reusable local result; update a matching row instead of adding variants
of the same lesson:

```markdown
| Task phase | Preconditions | What worked | Visible effect | Evidence |
|---|---|---|---|---|
| <phase> | <observable conditions> | <tool and action> | <visible change> | <returned frame manifest path> frame <n> |
```

## `INDEX.md`

{% if agent_formal %}
```markdown
| Skill | Tags | Confirmation | Last episode |
|---|---|---|---|
| `skills/<slug>/SKILL.md` | <tags> | observed | <episode_id> |
```

`Confirmation` is `observed` for a clear local visual effect, or
`task-confirmed` when the procedure contributed to an episode accepted with
`task_complete: true`.
{% else %}
```markdown
| Skill | Tags | Confirmation | Modes | Last episode |
|---|---|---|---|---|
| `skills/<slug>/SKILL.md` | <tags> | observed | exploration | <episode_id> |
```

`Confirmation` is `observed` for a clear local visual effect, or
`task-confirmed` when the procedure contributed to an episode accepted with
`task_complete: true`. `Modes` lists the evidence modes: `exploration`, `rehearsal`
or both.
{% endif %}

## Generated `SKILL.md`

```markdown
---
name: learned-<slug>
description: <maneuver and when this experience applies>
---

# <name>

- Applies when: <observable conditions>
- Avoid when: <observable mismatch>
- Executed action: <tool and exact action representation>
- Pose recipe: <anchor, recipe representation, frame, delta>
- Actions: <compact ordered procedure>
- Expected effect: <visible result>
- Recovery: <evidence-backed alternative>
```

Do not copy tool documentation or global limits into a learned skill, and do not
encode hidden state, inferred task success or an unobserved object pose.

## `evidence.json`

```json
{
  "schema_version": 4,
  "skill": "learned-<slug>",
  "examples": [
    {
      "task": "<task>",
      "episode_id": "<episode_id>",
{% if not agent_formal %}
      "episode_mode": "exploration | rehearsal",
{% endif %}
      "confirmation": "observed | task-confirmed",
      "manifest_path": "runtime/autonomous_controller/runs/<run>/frames/<sequence>/manifest.json",
      "step_ids": [1, 2],
      "frame_indices": [0, 1],
      "action_kind": "<see pose records>",
      "executed_action": "<see pose records>",
      "pose_recipe": "<see pose records>",
      "point_reacquisition": "<see pose records>",
{% if demonstrations %}
      "demonstration_provenance": {
        "manifest_path": "runtime/demonstrations/manifest.json",
        "kind": "completion_sequence | terminal_state",
        "demonstrator": "human | robot | unknown",
        "selected_images": ["<ordered_images entry>"]
      },
{% endif %}
      "observed_effect": "<visible change>"
    }
  ]
}
```

{% if not agent_formal %}Every example has an `episode_mode`. {% endif %}The robot-specific fields are defined in
[pose records](../../robot-interface/references/pose-records.md). For a
point-relative recipe, `point_reacquisition` records the visible feature, the
offset, {% if camera_geometry %}the `{{ robot.pixel }}` method{% else %}the RGB alignment procedure{% endif %} and the preferred views.
{% if demonstrations %}
`demonstration_provenance` records the demonstration manifest, its kind and
demonstrator, and the `ordered_images` entries you used, never action or state data;
otherwise it is `null`.
{% endif %}
