# RAX on RoboWits (Genesis 1.3.1)

**Implementation audit:** [confirmed protocol and infrastructure issues](ROBOWITS_AUDIT.md).
The initial random-reset protocol changed task constraints and is retained only as legacy
collection 0. New trials default to canonical source-scene exploration (collection 2),
selected by the user after the audit. Old and new trials must use separate batch directories.

The `robowits` backend runs RAX's native exploration → policy development → frozen
rehearsal → submission → held-out evaluation workflow. The policy receives RGB-D
and measured end-effector state and writes its own perception/control program.
There are no supplied task-solving primitives, demonstrations, or object poses.
This is a new benchmark adaptation, not a reproduction of a published RoboWits
result. The model and episode budget must accompany any reported score.

## Local setup

Run from the RAX repository. The existing RoboWits virtual environment must contain
Genesis 1.3.1, gs_gym, and its dependencies. The two source trees are not modified.

```bash
ln -s /home/junwenmiao/code/RoboWits-genesis131 ROBOWITS
ln -s /home/junwenmiao/code/RoboWits ROBOWITS_REFERENCE
source scripts/host_env.sh
python scripts/robowits_doctor.py --task robowits_06 --gpu 1
python scripts/robowits_doctor.py --task robowits_06 --gpu 1 --collection 1
```

`ROBOWITS_SOURCE` and `ROBOWITS_PYTHON` override the source and interpreter.
`GS_GYM_ASSET_PATHS` overrides asset discovery. By default, both trees' assets are
searched; the reference tree supplies missing `hf_assets`. Source symlink targets,
evaluation data, native simulation, and private diagnostics remain outside the
agent sandbox. Keep source overrides represented by project symlinks as well so
RAX's hidden-path policy excludes them. First use compiles Genesis kernels.

## Protocol

- Catalog: 30 seed tasks and 170 mutations, `services/robowits/catalog.json`.
- Exploration collection 2 (default): native source-defined scene, with random
  placement bypassed using the source sampler's canonical-reset mechanism. All
  exploration episodes repeat the same initial layout; they are not independent
  randomized-layout rehearsals. No official evaluation JSON is loaded.
- Collection 0: legacy random reset, unvalidated task semantics; explicit opt-in only.
- Formal collection 1: released evaluation JSON, explicit dataset index, RNG seed 42.
- Native EE_ABS controller, 14-dimensional bimanual actions, ~30 Hz; 1000 control
  steps, matching RoboWits `scripts/robowits/eval/eval.sh`.
- Success: native `is_success`; truncations and unsuccessful policies receive zero.
- Progress: native per-step reward, sum and maximum saved only in operator files.
  `formal-progress.json` separately reports `avg_sum_reward` / `avg_max_reward`;
  missing historical rewards remain unavailable. No reward is added to agent tools.
- Default batch: README RoboWits-10 tasks (01, 02, 03, 04, 06, 09, 13, 16, 17, 25),
  eight exploration episodes per task, five formal
  layouts (indices 0–4), one simulator at a time. This is a five-layout evaluation,
  not the full 50-layout benchmark. Pass `--formal-episodes 50` for all layouts.
- Exploratory model calls and generated code use the selected RAX provider profile.
  Each task has its own isolated workspace and budget. Formal code is frozen.

## Execute and resume

```bash
source scripts/host_env.sh
python scripts/evaluate_robowits.py \
  --output runtime/robowits-canonical-eval \
  --provider-home /home/junwenmiao/code/rax-local/provider \
  --profile chatgpt --gpu 1 --episodes 8 --formal-episodes 5
```

Use `--tasks robowits_06` for one task, or list mutation IDs explicitly.
Use `--suite all-seed` in a new output directory to select all 30 seed tasks.
Existing output directories preserve the task list and exploration collection.
Resuming or adopting trials with a different exploration collection is rejected. Reusing the
output directory starts only pending tasks, preserving prior trial records; it
does not silently retry failed or interrupted trials. A `running` record after
interruption requires inspecting that trial before deciding whether to retry it.
`--adopt-config PATH` includes an already running/completed trial without launching
it again. `--report-only` refreshes aggregate results, including adopted trials.

`manifest.json` records source commit, dataset hashes, budgets and trial paths.
`summary.json` keeps the fixed denominator, flags incomplete runs, and lists
runtime errors separately. An incomplete aggregate is not a completed benchmark
result. Per-trial `formal-scores.json`, native evaluation records, videos, policy
code, and transcripts remain under the trial directory. Credentials are not
copied into batch outputs.

## Implementation and verification

`services/robowits/` implements the process, RGB-D geometry, tool contract, native
scoring, and catalog. Generic robot/camera skill documents live under
`agent_skills/{control,perception}/`. `local_tests/unit/test_robowits_backend.py`
checks protocol separation, native reward privacy, bounded texture loading, action rejection, geometry, host import isolation,
and task prompts. The doctor exercises real Genesis resets, all three cameras,
calibrated pixel round trips, and five native control steps; it is an interface
check, not a successful task evaluation.

## Initial local evaluation (2026-10-27)

The first completed task was `robowits_06` (push the red block onto the green
area), using `gpt-6-astra`, xhigh effort, RGB-D and eight exploration episodes.
The frozen controller passed 5/5 exploration rehearsals, but scored **0/5 on
formal layouts 0–4**. All five formal episodes reached the native 1000-step limit;
there were no missing scores. Thus the integration ran end to end, while this
policy failed to generalize. The subsequent audit found that its exploration reset
made the red block directly reachable, changing a key task constraint; see
[the audit](ROBOWITS_AUDIT.md). This is one diagnostic task result, not a validated full benchmark result.

The [machine-readable result](robowits_initial_result.json) records the trial,
bundle hash and every formal outcome. In the first formal layout, logs show
repeated 2–3 cm approach tracking errors followed by retries until timeout.
The formal outcomes were not used to revise the submitted policy.

The batch was narrowed at the user’s request to the README RoboWits-10 subset,
preserving completed trials and the active task. It is tracked under `runtime/robowits-seed-eval/`. Consult its
current manifest and refreshed summary for completion; the initial result above
must not be interpreted as the aggregate benchmark score.

## Preserving RAX's method

The controller lifecycle (`services/controller/`), method skills
(`agent_skills/method/`), and agent workspace runtime (`auto_research_agent/`)
are unchanged from the upstream native branch at `1767412`. RAX supplies the
exploration, coding, frozen rehearsal and evaluation process. This adapter
supplies only the robot API, sensors, reset protocol and private scoring. No
task-specific policy, action sequence, simulator object poses or formal feedback
is injected into a new agent workspace.

The 04 eraser texture is loaded at its original resolution. During native
environment construction only, Pillow's warning threshold is temporarily set
to 140 million pixels (fatal threshold 280 million) to admit its packaged
277,805,556-pixel image. The previous threshold is restored in `finally`,
including construction failures. No image, mesh, mass, friction or task predicate
is changed. This compatibility adjustment does not disable Pillow's protection.

Real simulator checks passed for canonical 06 and 04. In canonical 06, the red
block is at x≈0.915m and white block 4 at x≈0.5m, preserving the original task
constraint that the red block is far from the robot. These are operator checks,
not facts supplied to the solving agent. Success on those checks verifies the
interface; the new RAX trial must still demonstrate its own performance.

## Canonical-source validation result

The corrected `robowits_06` run completed: **5/5 formal successes**, at steps
104, 102, 104, 102, and 104 on official layout indices 0–4. Each episode's native
maximum reward was 1.0. All scores are native, finalized, and present. The agent
independently generated `code/push_chain/` from its observations and exploration;
the operator supplied no task-solving controller. Its final two rehearsals passed
on the repeated canonical scene, both in 401 steps.

See [the machine-readable result](robowits_canonical_result.json) for the trial,
exact frozen bundle hash, success records and separate native reward aggregates.
The initial legacy run scored 0/5, but this single rerun is not a statistical
causal estimate: the exploration protocol changed and a new policy was learned.
Five layouts of one task do not establish performance on the full benchmark.

The user resumed the ten-task evaluation with the corrected protocol. Its new
batch is `runtime/robowits10-canonical-eval/`; it adopts only the corrected 06
result and reruns the other nine tasks in fresh workspaces. The old random-reset
batch remains paused and is excluded from the new summary. Budgets remain eight
exploration episodes and five formal layouts per task.

The user subsequently limited this corrected batch to **01, 02 and 06 only**.
The manifest under `runtime/robowits10-canonical-eval/` now contains those three
IDs (the directory name is retained to preserve active artifact paths). Corrected
06 is retained, active 01 drains normally, and only 02 remains queued. The final
planned denominator is 15 formal episodes, not 50; the other seven tasks will
not be started by this batch.
