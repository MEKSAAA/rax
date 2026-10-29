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

## Success and Failure Demos

The archive contains **138 recorded videos**, controller code, public agent explanations, tool calls, and per-episode outputs. The cases below include direct video links and full process records covering exploration, code development, rehearsal, and evaluation. Public explanations are recorded outputs, not hidden internal reasoning. Video time follows the simulation and is not synchronized with the agent’s wall-clock reasoning time.

### Success: Dominos

The agent identified the connected block chain and implemented pushing with re-observation. All five evaluation episodes succeeded; the example below completes in 104 steps.

- [Successful evaluation video](../runtime/robowits-report/cases/robowits_06/episodes/robowits_06_formal_0_ac0d132b/sensors.mp4) — 104 control steps; episode `robowits_06_formal_0_ac0d132b`.
- [Full process record](../runtime/robowits-report/cases/robowits_06/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_06/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_06/failure-audit.json)

### Success–failure comparison: Ball onto Tower

The same frozen controller succeeded in three layouts and failed in two. Compare a successful alignment and release with an episode where the ball became undetectable after placement and recovery failed.

- [Successful evaluation video](../runtime/robowits-report/cases/robowits_17/episodes/robowits_17_formal_0_54b5091f/sensors.mp4) — 496 control steps; episode `robowits_17_formal_0_54b5091f`.
- [Failed evaluation video](../runtime/robowits-report/cases/robowits_17/episodes/robowits_17_formal_3_579ab8c7/sensors.mp4) — 1000 control steps; episode `robowits_17_formal_3_579ab8c7`.
- [Full process record](../runtime/robowits-report/cases/robowits_17/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_17/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_17/failure-audit.json)

### Software failure: Cover with Lid

A NumPy Boolean serialization error interrupted carrying and triggered retreat/release. The example illustrates why a controller can exit cleanly while failing the physical task; all five evaluations failed.

- [Failed evaluation video](../runtime/robowits-report/cases/robowits_13/episodes/robowits_13_formal_0_a9f52054/sensors.mp4) — 1000 control steps; episode `robowits_13_formal_0_a9f52054`.
- [Full process record](../runtime/robowits-report/cases/robowits_13/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_13/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_13/failure-audit.json)

### Generalization failure: Water into Mug

The hold-position controller passed all five rehearsals in the original scene but failed all five evaluation layouts. The videos contrast a successful rehearsal with an unsuccessful evaluation.

- [Successful rehearsal (original scene) video](../runtime/robowits-report/cases/robowits_25/episodes/robowits_25_exploration_3_9922ad4c/sensors.mp4) — 151 control steps; episode `robowits_25_exploration_3_9922ad4c`.
- [Failed evaluation video](../runtime/robowits-report/cases/robowits_25/episodes/robowits_25_formal_0_e681f464/sensors.mp4) — 1000 control steps; episode `robowits_25_formal_0_e681f464`.
- [Full process record](../runtime/robowits-report/cases/robowits_25/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_25/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_25/failure-audit.json)

### Trial and revision: Gap Retrieve (lemon)

Slippage during development motivated deeper grasping and regrasp logic. The final controller succeeded in one evaluation layout; the failed example records detection problems during obstacle clearing.

- [Successful evaluation video](../runtime/robowits-report/cases/robowits_03/episodes/robowits_03_formal_3_843d183d/sensors.mp4) — 972 control steps; episode `robowits_03_formal_3_843d183d`.
- [Failed evaluation video](../runtime/robowits-report/cases/robowits_03/episodes/robowits_03_formal_0_2aa63a9b/sensors.mp4) — 1000 control steps; episode `robowits_03_formal_0_2aa63a9b`.
- [Full process record](../runtime/robowits-report/cases/robowits_03/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_03/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_03/failure-audit.json)

### Continued exploration: Hold Cup

The continuation added visual cup-orientation feedback and revised timing. The final controller succeeded in one of five evaluation layouts; the failed example could not localize the disk at the initial observation.

- [Successful evaluation video](../runtime/robowits-report/cases/robowits_09_continued/episodes/robowits_09_formal_4_3eae603e/sensors.mp4) — 933 control steps; episode `robowits_09_formal_4_3eae603e`.
- [Failed evaluation video](../runtime/robowits-report/cases/robowits_09_continued/episodes/robowits_09_formal_0_0d886c8e/sensors.mp4) — 1000 control steps; episode `robowits_09_formal_0_0d886c8e`.
- [Full process record](../runtime/robowits-report/cases/robowits_09_continued/process.md) · [Tool calls and feedback](../runtime/robowits-report/cases/robowits_09_continued/timeline.json) · [Per-episode failure evidence](../runtime/robowits-report/cases/robowits_09_continued/failure-audit.json)

[Results CSV](../runtime/robowits-report/results.csv) · [Full Chinese report](ROBOWITS_EXPERIMENT_REPORT.md)

**Sharing note:** The result tables and case descriptions are self-contained. Videos and process records remain in the local evidence archive and have not been uploaded to GitHub. To share playable demos, include the accompanying evidence directory with the same relative folder structure.
