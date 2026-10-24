# Coordination files

Shared coordination state lives in `{{ vocab.memory }}/exploration/`. Workers' own
notes, code, data and logs stay in their assigned paths; these files hold only what
others need.

| File | Writer | Purpose |
|---|---|---|
| `{{ vocab.memory }}/exploration/decisions.json` | Main agent only | Shared decisions every part must follow |
| `{{ vocab.memory }}/exploration/findings.jsonl` | All agents, append only | Claims, results, proposals and releases |

## `decisions.json`

A JSON list of `{"id", "decision", "reason", "evidence", "status", "supersedes"}`.
Replace an obsolete decision by marking it `superseded` and adding a new entry, so
earlier findings stay interpretable.

## `findings.jsonl`

One JSON object per line, appended in a single write; never edit or reorder earlier
lines. Read the recent lines before claiming new work.

Every record has `id` (unique, e.g. `<agent>-<n>`), `time` (UTC ISO-8601), `agent`
(the name used in the spawn message), `kind` and `claim` (the claim `id` it belongs
to){% if multi_env %}; `claim`, `result` and `release` records also carry the `env_id`{% endif %}. By kind:

- `claim`: `question` (one testable hypothesis), `allowance` (`episodes`, `steps`),
  `stop_when` (success and give-up conditions), `duplicates` (a claim `id` when
  repetition is deliberate).
- `result`: `episode_id`, `steps` (first and last), `preconditions`, `procedure`,
  `reuse` (below), `outcome` (`supported`, `refuted` or `inconclusive`),
  `confirmation` (`observed` or `task-confirmed`), `evidence` (paths), `cost`
  (`episodes`, `steps`, `wall_seconds`, `model_calls`), optional `next`.
- `proposal`: `change` (to a decision `id`, or a new decision), `reason`, `evidence`.
  Only the main agent turns a proposal into a decision.
- `release`: `reason` (`done`, `gave_up`, `reassigned` or `allowance_spent`),
  `episode_active`, `steps_remaining`, `scene` (the state left behind){% if not multi_env %}. It hands the
  robot back to the main agent{% endif %}.

## Record results in reusable form

`reuse` holds what the final solution will contain, so the main agent can adopt it
without re-deriving it:

- `parameters`: each value the result depends on, with `name`, `value`, `unit`,
  `anchor` (what it is measured from), `tested` (values tried and which worked) and
  `source` (measured, tuned or assumed).
- `rules`: each decision as `when` (observable condition), `choose` and `otherwise`.
- `code`: `path`, `function` and `signature` of a tested function written against a
  supplied context, like `main(ctx)` in
  [autonomous-control](../../autonomous-control/SKILL.md), that opens no context of its
  own.

When merging, the main agent keeps parameters as named constants and cites the
finding `id` beside each.

`confirmation` follows [experience memory](../../experience-memory/references/format.md):
`task-confirmed` only when `{{ vocab.evaluate }}` or native `task_complete: true`
accepted an episode the procedure contributed to. The log is coordination state, not
{% if agent_formal %}
a substitute for `{{ vocab.notes }}` or experience memory, and the controller must not
depend on it.
{% else %}
a substitute for `{{ vocab.notes }}` or experience memory. Rehearsals and the formal
batch never read these files, so the controller must not depend on them.
{% endif %}
