---
name: robot-interface
description: The robot - one Franka Panda arm with a parallel-jaw gripper - its official action rows (end-effector position and rotation deltas plus a gripper command), its state vector, frames, cameras and measured control response. Read before composing robot actions.
---

# Robot interface: Panda + parallel gripper

One Franka Panda arm is mounted at the edge of the table and ends in a two-finger
parallel-jaw gripper. `{{ robot.status }}` reports the exact action and state layouts;
this skill explains them and gives the controller's measured response.

## One action row

Each `{{ robot.step }}` row is one control step of 50 ms ({{ robot.hz }} per second). A row is a
**relative command**, every value in [-1, 1]:

| Slice | Meaning |
|---|---|
| `[0:3]` | end-effector position change, world frame: 1.0 asks for 0.05 m |
| `[3:6]` | end-effector rotation change as an axis-angle vector, world frame: 1.0 asks for 0.5 rad |
| `[6]` | gripper: -1 opens, +1 closes |

At every step the operational-space controller aims at the **measured** pose plus the
scaled change, so the arm never reaches a commanded change in one step and lags behind
large values (measured response below). There is no absolute target and no planner:
long moves are sequences of rows, and the arm avoids nothing, so plan clearance over the
table and objects yourself.

A row of zeros with the gripper value you want keeps the pose approximately (measured
drift below); keep sending the same gripper sign while holding an object, because the
last value of each row is the command for that step.

Each row counts once against the episode's step limit (`episode_limits`); the episode
ends at once when the instruction is accomplished, and later rows of the same call
are not run.

## The measured state

`state` has 8 values:

| Slice | Meaning |
|---|---|
| `[0:3]` | end-effector position (m, world frame): the point between the fingertips |
| `[3:6]` | end-effector orientation as an axis-angle vector (rad, world frame) |
| `[6:8]` | the two finger positions (m); their difference is the opening |

There are no joint angles and no object positions in the state; find objects in the
images.

## Start pose and frames

- **World frame:** +x points from the robot's base across the table, +y to the robot's
  left, +z up. The base stands at x of about -0.6 to -0.66 m, y = 0. In some scenes the
  objects lie on a table whose top is near z = 0.9 m, in others on a surface near
  z = 0; read heights from depth, not from these numbers.
- At the start the end effector is about 0.45 m in front of the base and 0.25 m above
  it, gripper open (fingers about 0.077 m apart) and pointing straight down: the
  orientation is about `[3.14, 0, -0.09]` as an axis-angle vector, a half turn about x.
  Near this pose the axis-angle vector sits close to angle pi, where its components can
  flip sign between nearby orientations; compare orientations by their relative
  rotation, not component by component.
- Objects start slightly above their supports and drop onto them before step 0, so the
  first observation already shows them resting.

## Control response

Measured from the start pose through the official step:

- **Position:** the arm reaches about a quarter of the commanded change per step. A row
  of 1.0 on one axis moves about 0.5 cm on the first step and about 1.2-1.3 cm per step
  once moving (about 10-12 cm over 10 steps); 0.25 moves about 0.3 cm per step. Moves
  along one axis drift up to about 1.5 cm sideways over 10 cm; check the measured
  position and correct.
- **Rotation:** the rotation part turns about world axes (x, y, z as in the row). A
  value of 0.25 turns about 0.03 rad per step and 1.0 about 0.07-0.09 rad per step once
  turning; the first step turns about half as much.
- **Hold:** zero rows drift less than 1 mm and 0.01 rad over 50 steps.
- **Gripper:** +1 closes the fingers from about 0.077 m apart to fully closed in about
  11 steps; -1 reopens them within about 20. A grasped object stops the fingers early:
  compare the finger positions with an empty close to confirm a grasp, and look at the
  wrist image.
- Near a target, use small values (0.1-0.3) and observe between short batches: the
  response lags behind each row, so a large value overshoots a short distance.

## Cameras

{% for camera in robot.cameras %}
- `{{ camera }}`{% if 'eye_in_hand' in camera %}: on the gripper, looking along the fingers, moving with it{% else %}: the third-person view of the table and the arm{% endif %}.
{% endfor %}

Images are 256x256 RGB, upright as each camera sees the scene. The third-person camera
faces the robot from the far side of the table: the robot is at the top of its image,
image right is world +y and image down is roughly world +x. Motion replies return
the final frame inline and save every 5th step as a frame sequence.
{% if camera_geometry %}
Depth images and `camera_intrinsics` / `camera_poses` (world frame) come with every
observation; see [object-localization](../object-localization/SKILL.md).
{% else %}
For locating objects without depth, see [object-localization](../object-localization/SKILL.md).
{% endif %}

{% if multi_env %}
## Environment selection

- Pass the selected `env_id` on every `libero_*` call, and on `start_episode`,
  `evaluate` and `finish`; for example `{{ robot.step }}(env_id=i, actions=...)`.
{% if agent_formal %}
  `{{ robot.status }}` reports one environment; `status` and `gemini_generate` take no `env_id`.
{% else %}
  `{{ robot.status }}` reports one environment; `status`, `gemini_generate`, `register`,
  `rehearse` and `submit` take no `env_id`.
{% endif %}
- Keep multi-call sequences in one environment, as
  [multi-env exploration](../multi-env-exploration/SKILL.md) describes.
{% endif %}

## Related skills

- [object-localization](../object-localization/SKILL.md) for where objects are.
{% if not formal_episode %}
- [references/pose-records.md](references/pose-records.md) for how poses and executed
  rows are recorded for reuse.
{% endif %}
