---
name: keep-trying
description: Use every step of an episode - recovery and retries after a failure, practising another sub-task when the attempt is lost, and exercising recovery paths. Read when an attempt fails or an episode seems lost.
---

# Keep going: every step of an episode is practice

{% if formal_episode %}
Your episode's steps are its scarcest resource. While the robot is standing, the episode can still
succeed, even after a failure; keep working toward every condition in TASK.md until it ends.

- **After a failure, recovery and a retry** count most: re-perceive, back off, re-approach, look
  again. A diagnosis before repeating a motion saves attempts.
- **A recovery your controller never ran** may not work now; watch it closely the first time.
{% else %}
Each episode's steps are the scarcest resource in this task. While the robot is standing, an episode
is still practice, even after a failure: closing it early with `finish`, or starting the next one,
gives the remaining steps away.

- **After a failure, recovery and a retry** teach the most: re-perceive, back off, re-approach, look
  again. A diagnosis before repeating a motion saves attempts.
- **When retrying is hopeless, practising another sub-task** you have not mastered yet pays off:
  walking to a measured target, turning precisely, a contact, a transport, a release. Practice with
  the real objects is worth more than a simplified stand-in, which leaves the real path untested.
{% if agent_formal %}
- **Exercising the paths later episodes will need**, failure detection, recovery and retries
  included, matters: a recovery branch that never ran in exploration may not work on a new layout.
{% else %}
- **Exercising the paths the formal batch will use**, failure detection, recovery and retries
  included, matters: a recovery branch that never ran in exploration may not work there.
{% endif %}
- **Mastery means repeated success** at each sub-task, counted as k/n, rather than one stage further
  than last time.
{% endif %}
