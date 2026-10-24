"""Episode reviews: an operator-side analyst's review of each finished exploration episode.

The lifecycle is shared by every benchmark whose simulator writes ground truth
(``services.benchmarks``: ``episode_reviews``):

- ``runner``: settings (off, on, detailed) in ``<session>/operator-analysis/settings.json``,
  background workers that wait for a closed episode's simulator files, the Gemini call through
  the OpenRouter router, the cost ledger, the 0600 operator record and the agent's
  ``review.md`` in that episode's published run directory. Formal episodes are never reviewed.
- ``common``: helpers every benchmark's analysis uses (condensed tool trace, final RGB frames,
  the Gemini call, the budget).
- ``workspace``: the TASK.md "Episode reviews" section and the AGENTS.md reminder.

A benchmark supplies its ground truth and wording through its adapter hook
``review_analysis()``, a module with:

- ``ground_truth_report(sim_dir)``: the report of one finished episode (a dict whose
  ``episode_id`` and ``episode_mode`` name it), or None when the simulator wrote nothing;
- ``condensed_trace(mcp_trace)``: one line per tool call;
- ``explain(router, report, trace, images, *, task_instruction, detailed, score)``:
  the analyst's explanation and its cost, ``(text, cost_usd)``;
- ``agent_review(report, explanation, *, episode_number, detailed, score, run)``: the
  agent-safe Markdown of ``review.md``;
- ``task_instruction(task)``: the task's instruction for the analyst.
"""
