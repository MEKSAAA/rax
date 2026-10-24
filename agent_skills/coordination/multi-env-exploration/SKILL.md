---
name: multi-env-exploration
description: Select and operate parallel exploration environments, track their independent scenes, and use the shared episode budget.
---

# Multi-env exploration

Exploration has {{ environments }} independent environments.

Inspect `status().exploration`: `environments` lists environments and their episode
state; the top-level `active_episode` is not the pool inventory.
Use `start_episode(env_id=i)`, robot calls such as
`{{ robot.observe }}(env_id=i)`, `evaluate(env_id=i)` and `finish(env_id=i)`.
Continue an active scene instead of starting again on resumption.

In development Python, bind `ctx = session.env(i)` and use
`ctx.call(tool, **arguments)` without repeating `env_id`. Keep binding in
the development wrapper; see [autonomous-control](../autonomous-control/SKILL.md)
for the portable controller example. Use a separate `Context()` connection per
concurrent Python worker; requests sharing one connection run one at a time.
The bound context adds `env_id` only where it applies; Gemini and
`status` take no `env_id`, and the Gemini budget is shared.
{% if not agent_formal %}

`register`, `rehearse` and `submit` take no `env_id` and are refused while a call is
in progress in any environment, so finish outstanding control first. `rehearse` and
`submit` close every live exploration environment; `register` leaves them open. Each
rehearsal uses one episode from the shared budget and gives the controller one bound
environment, as autonomous-control shows.
{% endif %}

## Environment rules

- Pass the selected `env_id` on every environment-scoped call; [robot-interface](../robot-interface/SKILL.md)
  lists which of this robot's tools take it. Keep multi-call sequences (a planned move
  and its execution) in the one environment that produced them,
  and pass an `observation_step_id` only to the environment that produced it. A plan from
  another environment returns `motion_plan.status: "Plan_Not_Found"` without
  executing. Step IDs count separately per environment, so a step ID from the
  wrong environment can still be accepted; track the `env_id` yourself.
- Each environment has its own scene, step counter and episode. Starting an episode
  in one does not affect the others. Each start uses one episode from the shared
  budget; an unused environment is capacity, not a free episode. A failed start
  also uses its episode. Zero remaining episodes blocks new starts, not remaining
  steps in live scenes.
- Calls within an environment run one at a time; different environments can run
  concurrently.
  This is not exclusive ownership of an environment or a lock around a whole script.
- Keep observations, pixels, cached plans and feedback tied to `env_id` and
  episode ID, not just step number. Other environments may have different layouts;
  they are not copies for same-state action comparisons. Reacquire geometry locally.
- Replies retain text/image blocks and add environment, episode and artifact metadata.
  Keep evidence and output paths associated with their source episode.

For delegated work, read
[multi-agent exploration](../multi-agent-exploration/SKILL.md).
