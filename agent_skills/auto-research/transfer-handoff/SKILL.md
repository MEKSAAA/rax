---
name: transfer-handoff
{% if carries_handoff %}
description: Write handoff/README.md before exploration ends, describing how to plan the motion for each subtask and each object manipulation, for when you later continue this task on layouts you have not seen.
{% else %}
description: Write handoff/README.md before exploration ends, describing how to plan the motion for each subtask and each object manipulation, as notes for a later session on this task with new layouts.
{% endif %}
---

# Transfer handoff

{% if carries_handoff %}
Later you continue this task in new episodes on layouts you have not seen, starting
from the folders AGENTS.md says are kept. Write `handoff/README.md` for then: how to
plan the motion for each subtask and each object manipulation of this task, so you can
plan them on a new layout instead of working them out again.
{% else %}
`handoff/README.md` holds notes for a later session on this same task with new
layouts, which starts from `handoff/`, not from frames or logs.{% if not agent_formal %} It is separate from
this session's submitted bundle, which runs as it stands.{% endif %} Write in it how to plan the
motion for each subtask and each object manipulation of this task, so that session can
plan them on a new layout instead of working them out again.
{% endif %}

## What to write

Start with the subtasks in order, one line each, with what marks each one complete in
the current observation and the `TASK.md` condition it serves.

Then give each subtask a section, with one entry per object manipulation in it (for
example reach, grasp, lift, carry, place, push or release):

- **Object and goal**: which object, what must change, and how to see that it did.
- **Locating it**: how to find the object and the target in a fresh observation (which
  camera, which visible feature, which skill or tool), and what to locate again after
  each move.
- **Motion plan**: the ordered moves, each relative to something observed (for
  example "\<distance> above \<observed feature>, \<orientation>"), with the arm, the tool
  that executes it (`libero_step` rows), the gripper orientation and opening, the
  approach and retreat directions, and the step sizes that worked.
- **Parameters**: each value with its unit, what it is measured from, the range you
  tried and which value worked.
- **Checks and failures**: how to confirm the manipulation worked, the failures you
  saw, how to recognize each and what fixed it.
- **Code**: the function under `code/` that performs it and how to call it, if any.

Write what you verified and mark anything untested. Give positions relative to
observed features, never absolute coordinates from one layout. Keep it as text; copy
at most a few decisive images into `handoff/`.

{% if carries_handoff %}
Update `handoff/README.md` when you learn something new, and check the carried folders
against `status.carry.carried_limit_bytes` before you end your turn.
{% else %}
Update `handoff/README.md` when you learn something new, and keep it short: text, plus
at most a few images.
{% endif %}
