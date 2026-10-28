---
name: robot-interface
description: RoboWits dual-arm control, action frames and sensing contract.
---
# RoboWits / Marvin

Two 7-DoF arms and parallel grippers. Actions are absolute EE pose targets sent through
`robowits_step`, at approximately 30 Hz. `robowits_observe` returns calibrated camera
images and measured state; `robowits_status` describes the live interface.

Each action and state has exactly 14 entries:
`[right_x,right_y,right_z, left_x,left_y,left_z, right_rx,right_ry,right_rz, left_rx,left_ry,left_rz, right_width,left_width]`.
Positions and depth-lifted points are in the robot-base frame (metres). Base origin is
world `(0,0,1.08)`, so the 0.76m table is around base z=-0.32m. +X is forward,
+Y is the robot's left, +Z is up. Orientations are rotation vectors (axis-angle radians),
not Euler angles. The base has identity world rotation. Gripper widths are metres:
0 closes, approximately 0.08 opens. Hold the unused arm at its observed pose.

A row commands one native step, not a completed Cartesian motion. The benchmark's IK
and joint controller own tracking. Repeat targets over short chunks, reobserve and
measure convergence. Avoid large discontinuous pose changes near objects. A requested
pose can be unreachable; no helper supplies object poses or task-specific primitives.
Measure the tool/finger offset visually instead of assuming the EE point is a fingertip.

Ego and two wrist cameras are 848x480. RGB-D and current camera transforms accompany
observations. `robowits_pixel_to_position` requires the current observation step id.
Depth measures visible surfaces. Execution pauses between action calls. Native success
or the 1000-step benchmark limit ends the episode; no action is accepted after that.
