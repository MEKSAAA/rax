---
name: learn-from-incidents
description: Keep a cumulative table of falls, near-falls, unexpected contacts and stalls, cluster them by situation into hazard zones and change the plan there. Read after a fall or near-fall and when falls keep recurring.
---

# Learn from incidents: map where things go wrong and change the plan there

- **An incident table.** Falls, near-falls (a sharp rise in the reported tracking error, a safety or
  stabilizing action your code triggered, a large tilt), unexpected contacts and stalls, each with
  the positions of the robot and of the objects involved relative to the scene's fixed structure,
  the phase, the last few actions and the body signals, in one cumulative table across episodes.
- **Clusters by situation, not by the last command:** location, nearby objects, phase, body signals.
  The last command is usually only the trigger.
- **A cluster is a hazard zone.** Strategies that avoid it, pass through it quickly or finish the
  task from farther away worked better than patches; a keep-out or a dedicated, conservative
  behaviour covers the time the robot has to spend there. The escape route from one hazard can lead
  into the next, so it is worth checking.
- **A fix is real** once the robot has passed the same situation safely often enough to bound the
  rate ([evidence-and-regression](../evidence-and-regression/SKILL.md)).
- **A safety stop is evidence that the plan is wrong there.** Resuming the same action afterwards
  rarely helped; backing off to a known-safe configuration and approaching differently, or switching
  methods, did.
{% if agent_formal %}
- **Near-miss counts** estimate how often a situation would end in a fall later: one fall in a few
  passes means many falls over many new layouts.
{% else %}
- **Near-miss counts** estimate how often a situation would end in a fall in the formal batch: one
  fall in a few exploration passes means many falls over many formal episodes.
{% endif %}
