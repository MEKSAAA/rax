---
name: in-context-action-learning
{% if demonstration_kind == 'terminal_state' %}
description: Infer {{ robot.benchmark }} task goals from the supplied terminal-state {{ 'image' if one_demonstration_image else 'images' }} of the completed task. Use when visual examples should guide the current task.
{% elif demonstration_kind == 'completion_sequence' %}
description: Infer {{ robot.benchmark }} task goals and useful strategy from the supplied image sequence of a human or robot completing the task. Use when visual examples should guide the current task.
{% else %}
description: Infer {{ robot.benchmark }} task goals and useful strategy from a supplied image sequence of a human or robot completing the task, or from a supplied terminal-state image. Use when visual examples should guide the current task.
{% endif %}
---

# In-context action learning

{% if demonstration_kind == 'terminal_state' %}
{% if one_demonstration_image %}
The supplied demonstration is one image of the completed task. It contains no
actions, robot state, poses, calibration, or control timing. Use it for visual
intent, never as executable motion data.
{% else %}
The supplied demonstrations are images of the completed task. They contain no
actions, robot state, poses, calibration, or control timing. Use them for visual
intent, never as executable motion data.
{% endif %}
{% elif demonstration_kind == 'completion_sequence' %}
The supplied demonstration is an ordered image sequence of a human or robot
completing the task. It contains no actions, robot state, poses, calibration, or
control timing. Use it for visual intent and strategy, never as executable motion
data.
{% else %}
Supplied demonstrations contain images only: either an ordered sequence of a
human or robot completing the task, or one terminal-state image. They contain
no actions, robot state, poses, calibration, or control timing. Use them for
visual intent and strategy, never as executable motion data.
{% endif %}

The demonstration may use a different layout from the current episode. Use it
mainly to understand what the task requires and the intended final relationships,
not to copy exact positions or reproduce the image pixel-for-pixel. Satisfy every
condition in `TASK.md` using the current layout and observations.
{% if demonstration_kind != 'terminal_state' %}
Use a sequence to understand the task phases, but do not imitate every
demonstrated action. Choose simpler or more convenient actions when they achieve
the same required outcome in the current layout.
{% endif %}

## Retrieve

{% if formal_episode %}
- Retrieve matching carried tips or learned experience for similar
{% else %}
- Retrieve matching tips from this task or learned experience for similar
{% endif %}
  actions, then inspect the supplied {{ 'image' if one_demonstration_image else 'images' }} under `runtime/demonstrations/`.
{% if demonstration_kind != 'terminal_state' %}
- View the images directly. Preserve a sequence's supplied order and identify
  visible phases, contacts, object relations, and the final effect.
  Do not infer exact timing or interpolate missing phases.
{% endif %}
{% if demonstration_kind != 'completion_sequence' %}
- For a terminal image, infer only the desired visible end relationship; devise
  the intermediate robot behavior independently.
{% endif %}

## Adapt

- Transfer semantic structure, such as approach side, contact order, grasp
  region, relative placement, and completion appearance. A human hand or a
  different robot does not define the current arm, pose, or trajectory.
- Ground all geometry again from current {{ robot.benchmark }} observations.
  Use [object-localization](../object-localization/SKILL.md) and the motion tools in
  [robot-interface](../robot-interface/SKILL.md) when needed;
  never estimate metric targets or action magnitudes from demonstration pixels
  alone.
- Construct new actions with the robot tools, as the live tool schemas define them.

## Update from experience

- Associate the chosen visual strategy with its observed effect; do not repeat
  an ineffective interpretation from the same conditions.
{% if not formal_episode %}
- When starting a new exploration episode, retrieve the failed episode's decisive
  before/action/after evidence and revise the plan.
{% if agent_formal %}
- As you build the controller, turn relevant exploration evidence into executable
  logic that reacquires current geometry rather than assuming the exploratory
  layout is identical.
{% else %}
- Before registering the controller, turn relevant exploration evidence into
  executable logic that reacquires current geometry rather than assuming the
  exploratory layout is identical.
{% endif %}
{% else %}
- Use the strategy you carried, but reacquire current geometry: this layout
  differs from every earlier one.
{% endif %}
{% if formal_episode %}
- Keep this working set compact; it only serves this episode.
{% else %}
- Keep this working set compact. Promote only reusable, evidence-backed results
  through the experience-memory skill.

When retaining demonstration evidence, record it under `demonstration_provenance`
in the experience-memory format.
{% endif %}
