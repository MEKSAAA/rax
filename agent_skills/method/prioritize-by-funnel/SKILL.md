---
name: prioritize-by-funnel
description: Account for every attempt by stage in a cumulative funnel and fix the stage that loses the most first, with a backlog of known issues. Read after each episode and when choosing what to work on next.
---

# Account for every attempt by stage and fix the biggest loss first

- **A funnel by stage.** Naming the stages of your task in order (for example find, approach,
  contact, transport, finish; your task's own) and recording after each episode whether each stage
  was reached and succeeded, with the cause of the first failure, gives a cumulative table in
  `memory/` that shows where episodes are lost. Retries within an episode count as attempts.
- **Best gain per cost first.** Gain: the episodes lost at a stage times the chance that the fix
  works. Cost: effort, risk to stages that work, and the budget needed to validate it. Late stages
  are best judged per attempt that reached them; their losses are easy to undercount.
- **A backlog** of known issues, each open, fixed in version k, or rejected for a stated reason, keeps
  repeated findings from slipping.
- **Exploration where the uncertainty is.** A late stage reached only twice is worth practising
  ([keep-trying](../keep-trying/SKILL.md)) before an earlier one is polished.
{% if agent_formal %}
- A summary of the final funnel and the open backlog in `memory/` before exploration ends tells you,
  later, where your controller needs you.
{% else %}
- A summary of the final funnel and the open backlog in `memory/` before you submit makes the
  submission decision clearer.
{% endif %}
