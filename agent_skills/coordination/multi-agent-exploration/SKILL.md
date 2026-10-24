---
name: multi-agent-exploration
{% if multi_env %}
description: Divide robot exploration among subagents with explicit environment assignments and complementary motion or subtask goals.
{% else %}
description: Divide exploration among subagents.
{% endif %}
---

# Multi-agent exploration

{% if multi_env %}
Read [multi-env exploration](../multi-env-exploration/SKILL.md) for environment
protocols. Follow AGENTS.md for what to do at each point of the task.
{% else %}
Subagents share the robot by turn-taking (below). Follow AGENTS.md for what to do
at each point of the task.
{% endif %}

## Decide whether to parallelize

Parallel workers save wall time only on independent questions; coordination costs
context, tokens and episodes. Keep work in one agent when each step depends on the
previous one's outcome, or when the question is short. Delegate a later subtask
{% if multi_env %}
only if its worker can physically create the required starting state, for example by
arranging objects in its own scene; otherwise it waits for the earlier result.
{% else %}
only if its worker can physically create the required starting state when it holds
the robot; otherwise it waits for the earlier result.
{% endif %}

{% if multi_env %}
Start no more robot workers than available environments, and at most
{{ subagent_cap }} subagents at once.
{% else %}
Start at most {{ subagent_cap }} subagents at once; only one holder acts on the robot
at a time.
{% endif %}
{% if agent_formal %}
Size allowances so episodes remain for practising with the whole controller.
{% else %}
Size allowances so the episodes still needed later in AGENTS.md remain after exploration.
{% endif %}
{% if not multi_env %}

## Turn-taking

The main agent holds the robot by default and is the only one that grants it.

- Grant the robot to one holder at a time, in the worker's brief: "you hold the
  robot" plus its allowance and stopping condition. Everyone else works without the
  robot: analyzing saved frames and logs, writing and testing code offline,
  calibrating, and planning.
- Only the holder uses the robot, including starting and finishing episodes.
- The holder hands back by stopping its control loop, waiting for outstanding calls,
  describing the current scene and episode state, and reporting. The main agent
  confirms the handoff before granting the robot to anyone else.
- Parallelism here saves thinking time, not robot time. Delegate only work that can
  proceed off the robot, or a robot experiment whose result the others need next.
{% endif %}

## Divide work before spawning

- Assign different agents different motions, manipulation subtasks or strategy
  questions: for example, grasp/lift, transport/orientation, and placement/release.
{% if multi_env %}
  Choose independent experiments that can run concurrently and combine into a
  solution. Avoid having every agent repeat the whole task; duplicate work only
  when deliberately comparing alternatives or testing robustness.
{% else %}
  Choose independent questions that workers can prepare off the robot and test in
  turn, and that combine into a solution. Avoid having every agent repeat the whole
  task; duplicate work only when deliberately comparing alternatives or testing robustness.
{% endif %}
- Give each robot worker a brief: its question or hypothesis, the starting
  state and how to create it, an observable check for the end state, the output
{% if multi_env %}
  format, its distinct `env_id`, whether to start or continue an episode, episode
{% else %}
  format, whether it holds the robot, whether to start or continue an episode, episode
{% endif %}
{% if multi_env %}
  and step allowances, stopping conditions and result paths. Keep a separate
  environment for the main agent if it will also control a robot.
{% else %}
  and step allowances, stopping conditions and result paths.
{% endif %}
- When comparing alternatives, give each worker a genuinely different approach and
  the same success check, so results are comparable and do not converge early.
- Spawn subagents with your subagent tool; give each the `TASK.md`, skill and
  memory paths. Observations you pass along are not the worker's current scene.
{% if multi_env %}
- Launch independent workers without waiting for each one to finish. Match active
  robot workers to available environments; share useful findings as they arrive.
{% else %}
- Launch independent off-robot workers without waiting for each one to finish, and
  grant the robot to one holder at a time; share useful findings as they arrive.
{% endif %}

## Share decisions and findings

Subtasks carry implicit decisions, such as which arm acts, how an object is held or
which frame a handoff uses. Record decisions every part must share in a ledger that
only the main agent edits. Workers follow it and propose changes rather than
silently diverging.

Keep one append-only findings log. Workers record a claim before a new experiment,
append results while working rather than only at the end, and read recent entries
before choosing the next experiment. Do not start a claimed experiment unless
deliberately repeating it. Both files live in `{{ vocab.memory }}/exploration/`; see
[coordination files](references/coordination.md) for the record format.

## Coordinate ownership and budget

{% if multi_env %}
Keep one active decision-maker per environment, including Python loops. Workers use only
their assigned environment and consult the main agent before switching environments or
spending additional episodes. Episode allowances are agreements between agents;
only the shared budget is a hard limit.
{% else %}
Only the current holder acts on the robot, including Python loops. Workers consult the
main agent before spending additional episodes. Episode allowances are agreements
between agents; only the shared budget is a hard limit.
{% endif %}

Assign separate note/code/data/log paths in the shared workspace. Before handing
off {% if multi_env %}an environment{% else %}the robot{% endif %}, stop its worker's control loop, wait for outstanding calls and inspect
the scene; interrupting an agent does not cancel an already-dispatched action.

Each worker stops and reports on success, on its give-up condition (for example,
the same failure twice without a new hypothesis), or when its allowance or time
limit is reached. At regular checkpoints, the main agent compares progress and
moves {% if multi_env %}environments and {% endif %}allowances from stalled lines of work to promising ones, or
restarts a stalled worker from a better approach with a deliberate variation.

## Combine results

Report the tested motion/subtask, {% if multi_env %}environment and {% endif %}episode IDs, useful parameters
and observable preconditions, failures, evidence paths, remaining steps and whether
the episode is still active. Distinguish local progress from confirmed task success.
Transfer procedures, not layout-specific coordinates: record each result as
named parameters, decision rules and a tested function, in the form described in
[coordination files](references/coordination.md).

The main agent integrates findings into the shared strategy and tests their
composition. A worker's result enters the strategy only after the main agent checks
its evidence; test the combined procedure end to end, preferably in a scene not used
to develop it. Only the main agent merges code into the main project. Keep
rejected alternatives with the reason they lost; do not delete their evidence.
Record reusable results in `{{ vocab.memory }}/TIPS.md` following
[experience memory](../experience-memory/SKILL.md): a visible local result is enough
for a tip, with its confirmation level stated.

Only the main agent decides when exploration ends; gather worker reports and stop
outstanding control first. Follow AGENTS.md for what comes next rather than letting
workers decide it.
Record each worker's episodes, steps, wall time and model calls in its results' `cost`, so you
can decide whether more workers are worth it.

{% if agent_formal %}
## Code and episodes
{% else %}
## Code, registration and submission
{% endif %}

- **Manual and controller work.** While solving the task by hand, workers answer
  subtask questions through direct robot calls. While building the controller, they
  develop and test individual controller functions; the main agent composes
  `controller.py` and runs full-controller tests.
{% if agent_formal %}
- **Code paths.** Keep worker code under `experiments/<agent>/` and outputs under
  `output/<agent>/`; only accepted code moves into `code/<project>/`.
{% else %}
- **Code paths.** `register` freezes the whole `code/<project>/` directory, so keep
  worker code under `experiments/<agent>/` and outputs under `output/<agent>/`.
  Move only accepted code into the project.
{% endif %}
{% if multi_env and agent_formal %}
- **Episode calls.** A worker calls `start_episode(env_id=i)` only within its
  allowance, and `evaluate(env_id=i)` only on its own environment when its brief
  includes a full-task check.
- **Python workers.** Run concurrent scripts as
  [multi-env exploration](../multi-env-exploration/SKILL.md) describes.
{% elif multi_env %}
- **Episode and submission calls.** A worker calls `start_episode(env_id=i)` only within its
  allowance, and `evaluate(env_id=i)` only on its own environment when its brief
  includes a full-task check. Only the main agent calls `register`, `rehearse` and
  `submit`. Rehearsal and submission close every live environment, so collect
  `release` records and stop all worker control first.
- **Python workers.** Run concurrent scripts as
  [multi-env exploration](../multi-env-exploration/SKILL.md) describes.
{% elif agent_formal %}
- **Episode calls.** Only the holder calls `start_episode`, within its allowance, and
  `evaluate` when its brief includes a full-task check.
- **Scripts.** Only the holder runs robot scripts. Other workers write and
  test code offline, then hand it to the holder or the main agent to run.
{% else %}
- **Episode and submission calls.** Only the holder calls `start_episode`, within its
  allowance, and `evaluate` when its brief includes a full-task check. Only the main
  agent calls `register`, `rehearse` and `submit`. Rehearsal and submission close the
  live episode, so stop all worker control first.
- **Scripts.** Only the holder runs robot scripts. Other workers write and
  test code offline, then hand it to the holder or the main agent to run.
{% endif %}
- **Model budget.** Gemini's dollar balance is shared by all agents and {% if agent_formal %}your later
  episodes{% else %}the formal batch{% endif %}. Include model calls in each allowance and record them in `cost`.
- **Notes.** The main agent summarizes accepted findings, open claims and allocations
  in `memory/PROGRESS.md`; workers do not edit it.
