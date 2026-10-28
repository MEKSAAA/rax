---
name: object-localization
description: Localize visible objects from RoboWits RGB-D cameras.
---
# Localize from observations

Read `robowits_observe` and use ego, wrist_right and wrist_left images. Select pixels
on visible surfaces, then call `robowits_pixel_to_position` with the observation step id.
Returned points and camera positions are in the robot-base frame, matching the action.
Camera rotations use OpenGL axes (+X right, +Y up, -Z forward); RGB row zero is the top.
Depth PNGs encode metric depth in millimetres, with zero invalid. Camera intrinsics are
included. Use multiple views and move a wrist to resolve occlusion. Never treat a
surface point as an object's hidden center or use a previous episode's object placement.
