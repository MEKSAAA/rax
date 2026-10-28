---
name: object-localization
description: Localize RoboWits objects from RGB observations without depth.
---
# RGB-only observations

Use ego and wrist camera images. Move the robot by small measured offsets and compare
image motion to estimate local scale. Reobserve before contact; no metric depth or
pixel-to-position helper is available in this profile.
