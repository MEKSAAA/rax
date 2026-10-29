# Progress Report: Evaluating RAX on RoboWits

**Experiment dates:** October 28–29, 2026  
**Scope:** Ten RoboWits **seed tasks**; no mutation tasks. Five official evaluation layouts per task, compared with 50 in the full seed-task evaluation protocol.

## Summary

We integrated RAX with RoboWits and completed a preliminary evaluation of agent-generated robot controllers. Across the ten tasks, the final controllers achieved **15 successes in 50 evaluation episodes (30%)**. This is a **mixed-budget result**: Hold Cup received 16 exploration episodes through a continuation run, while the other nine tasks received eight each.

The strongest results were **Dominos (5/5)** and **Ball onto Tower (3/5)**. The main limitation was transfer from the repeated exploration scene to unseen evaluation layouts. Failures also exposed a controller software bug and insufficient budget allocation for validation.

## Results

Each task was evaluated on five official layouts. “Rehearsal” refers to execution of the exact final controller in the original exploration scene; it is **not** a held-out success rate.

| Task | Exploration episodes | Successful rehearsals | Evaluation successes | Success rate |
| --- | ---: | ---: | ---: | ---: |
| 01 Align Blocks | 8 | 2/2 | 2/5 | 40% |
| 02 Retrieve Cube | 8 | 1/1 | 0/5 | 0% |
| 03 Gap Retrieve (lemon) | 8 | 2/2 | 1/5 | 20% |
| 04 Pinch Card | 8 | 2/2 | 2/5 | 40% |
| 06 Dominos | 8 | 2/2 | 5/5 | **100%** |
| 09 Hold Cup | **16 (8 + 8)** | 2/2 | 1/5 | 20% |
| 13 Cover with Lid | 8 | 0/1 | 0/5 | 0% |
| 16 Stand Bulb | 8 | 2/2 | 1/5 | 20% |
| 17 Ball onto Tower | 8 | 1/1 | 3/5 | **60%** |
| 25 Water into Mug | 8 | 5/5 | 0/5 | 0% |
| **Total: mixed exploration budgets** | — | — | **15/50** | **30%** |

**Original eight-episode budget:** Hold Cup achieved interactive success only in its final exploration episode, leaving no episode for controller rehearsal and submission. The other nine tasks scored **14/45 (31.1%)**. Counting the five unexecuted Hold Cup evaluations as zero gives **14/50 (28%)**, but these must be marked as missing rather than observed failures. The continuation inherited the original code, memory, and conversation; it was not an independent restart.

## Experimental Setup

| Component | Configuration |
| --- | --- |
| Method and framework | RAX with its **Codex CLI harness**, using OpenAI Codex CLI 0.164. The agent explores, writes controller code, rehearses it, and submits a frozen version. No model-weight training or operator-written task solutions. |
| Model and access | `gpt-6-astra`, reasoning effort `xhigh`, accessed through **ChatGPT-authenticated Codex CLI** (`profile=chatgpt`), rather than a standalone API key or OpenRouter. External Gemini perception was disabled. |
| Robot interface | Local MCP tools for RGB-D observation, calibrated localization, state queries, and 14D dual-arm end-effector/gripper control. Low-level control and inverse kinematics are provided by the environment. |
| Exploration | Repeated source-defined canonical scene (`collection=2`); eight episodes shared by interactive exploration, development, and rehearsal. The agent could perturb the scene through robot actions. |
| Evaluation | Official seed-task layouts 0–4 from `eval_dataset_seed` (`collection=1`, seed 42). Frozen Python controllers run without per-step LLM calls or adaptation to evaluation results. Success uses the native task checker. |
| Limits and hardware | 1,000 control steps per episode; 7,200-second wall-clock limit. Genesis 1.3.1 on an RTX 6000 Ada 48 GB GPU; up to two tasks ran concurrently. |

All results use the corrected canonical-scene protocol. Earlier random-reset experiments are excluded.

## Key Findings

1. **Exploration produced executable, observation-dependent skills.** In Dominos, the agent identified the connected block chain and implemented pushing with re-observation, achieving 5/5 evaluation successes in 102–104 steps. In Ball onto Tower, development failures prompted smoother transport, visual alignment, and gradual release; the resulting controller succeeded in 3/5 layouts. These are observed development trajectories, not ablations establishing each component’s causal contribution.

2. **Repeated-scene rehearsal substantially overestimated generalization.** Water into Mug passed 5/5 rehearsals with a controller that held the robot’s measured pose, yet failed all five evaluation layouts. The controller did not actively adapt to the water stream or mug location. This indicates that successful behavior in the canonical scene can be insufficient for layout transfer.

3. **Some failures were software defects, not evidence of physical inability.** Cover with Lid encountered a NumPy Boolean serialization error during carrying in every evaluation episode. The exception handler retreated and released the object. The controller remained eligible for submission because the qualification check required a clean completed rehearsal, not task success; its rehearsal had failed the task. This defect should be repaired and evaluated in a separately recorded experiment.

4. **Additional exploration completed the workflow, but did not eliminate transfer failures.** Hold Cup’s continuation added visual cup-orientation feedback and revised motion timing. The final controller passed two rehearsals at 842 steps, then achieved 1/5 evaluation successes. Failures included missing initial object detections, 3.2 cm tracking errors during disk pickup, and an unreliable-grasp check during transport. More exploration enabled submission, but did not establish robust recovery across layouts.

## Limitations and Next Steps

This is a small-scale feasibility evaluation: one exploration run per task, only five evaluation layouts, no matched baseline, and no controlled ablations. The mixed exploration budgets also prevent interpreting 30% as an equal-budget benchmark score.

Next steps are to:

- Repair the known controller defect and reserve exploration episodes for validation and repair.
- Compare eight- and sixteen-episode budgets across repeated exploration runs.
- Introduce diverse training layouts isolated from the evaluation data, including a no-action baseline to detect scene-specific shortcuts.
- Expand evaluation toward all 50 official seed-task layouts and analyze perception, contact, and recovery failures separately.

## Evidence and Demos

The archive contains **138 recorded videos**, controller code, public agent explanations, tool calls, and per-episode outputs. The linked Markdown records include direct video links; they exclude hidden internal reasoning.

- [Dominos: successful exploration-to-controller pipeline](../runtime/robowits-report/cases/robowits_06/process.md)
- [Ball onto Tower: successful and failed evaluation episodes](../runtime/robowits-report/cases/robowits_17/process.md)
- [Cover with Lid: software failure trace](../runtime/robowits-report/cases/robowits_13/process.md)
- [Water into Mug: rehearsal–evaluation mismatch](../runtime/robowits-report/cases/robowits_25/process.md)
- [Hold Cup: continuation and final evaluation](../runtime/robowits-report/cases/robowits_09_continued/process.md)
- [Results CSV](../runtime/robowits-report/results.csv) · [Full Chinese report](ROBOWITS_EXPERIMENT_REPORT.md)

The results table is self-contained. Demo links require the accompanying local evidence directory when sharing this Markdown file.
