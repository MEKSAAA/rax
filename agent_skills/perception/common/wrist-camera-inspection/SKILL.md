---
name: wrist-camera-inspection
description: Obtain a clearer close-up of small or occluded task details, or a second viewpoint, by positioning an empty arm's wrist camera when the {{ robot.overview }} view is insufficient. Use object-localization on the new observation.
---

# Wrist-camera inspection

Use an empty arm as a movable inspection camera. Bringing its wrist camera closer
and adjusting the viewing angle can reveal details that are unclear in `{{ robot.overview_camera }}`.
This is physical camera repositioning, not digital enlargement or a zoom API.

- Choose an arm that is not holding an object. Do not release a held object just
  to obtain a view; preserve the other arm's grasp and task state.
{% if robot.plan %}
- Use the {{ robot.overview }} overview to choose a clear approach. Follow
  [free-space motion](../free-space-move/SKILL.md) for positioning, then
  use small, observation-guided adjustments if needed. Keep the arm and gripper
  clear of the table, task objects and other arm; planning success alone does not
  establish clearance from objects or the other arm.
{% else %}
- Use the {{ robot.overview }} overview to choose a clear approach. Move the
  wrist with small, observation-guided motions as
  [robot-interface](../robot-interface/SKILL.md) describes. Keep the arm and hand
  clear of the table, task objects and other arm.
{% endif %}
- Inspect the fresh {{ robot.wrist_images }} image after each move.
  Adjust distance and angle until the relevant detail is clear, avoiding gripper
  occlusion. Stop approaching once the view is useful, or if clearance is uncertain.
{% if camera_geometry %}
- Use [object-localization](../object-localization/SKILL.md) on the new
  observation. Do not reuse old wrist-image pixels, rays or calibration after moving
  the camera.
{% else %}
- Use [object-localization](../object-localization/SKILL.md) on the new
  observation; do not reuse old wrist images after moving the camera.
{% endif %}

Inspection motions consume episode steps. Move only when the clearer view can
resolve a task-relevant uncertainty, then return the empty arm to a clear pose
if it obstructs the next manipulation.
