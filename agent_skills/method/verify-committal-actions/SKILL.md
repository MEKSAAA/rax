---
name: verify-committal-actions
description: Define the expected outcome of an action you cannot take back (striking, releasing, stepping over, moving out of view), check it at once and plan the recovery. Read before such an action and when one did not do what you meant.
---

# Verify the outcome of committal actions

**Committal actions** are actions after which you no longer control or see the object as before:
striking or pushing a free object, releasing or dropping it, stepping over or onto something, or
moving where the camera cannot see it.

- **An expected outcome, defined before acting:** an observable signature of success (where the
  object should be and how fast it should move, what the camera should show, what the body should
  feel) and how soon it should appear.
- **A check at the next decision.** Re-perceiving and comparing the result with that signature
  catches the failures that an assumed success hides.
- **A recovery for each failure configuration** (the object at your feet, out of view, displaced,
  still in the hand), starting from where the failure leaves the robot, not from where the attempt
  began.
- **Actions after which a failure is still recoverable** are worth preferring, and each action's
  outcomes counted per attempt before you rely on it.
- **A cap on repetitions** of the same committal action, after which another method takes over,
  keeps a failing move from eating the episode.
