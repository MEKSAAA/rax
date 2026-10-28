# RAX on RoboWits (Genesis 1.3.1)

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
- Exploration collection 0: random resets, RNG seed `10000 + layout_id`.
- Formal collection 1: released evaluation JSON, explicit dataset index, RNG seed 42.
- Native EE_ABS controller, 14-dimensional bimanual actions, ~30 Hz; 1000 control
  steps, matching RoboWits `scripts/robowits/eval/eval.sh`.
- Success: native `is_success`; truncations and unsuccessful policies receive zero.
- Default batch: all seed tasks, eight exploration episodes per task, five formal
  layouts (indices 0–4), one simulator at a time. This is a five-layout evaluation,
  not the full 50-layout benchmark. Pass `--formal-episodes 50` for all layouts.
- Exploratory model calls and generated code use the selected RAX provider profile.
  Each task has its own isolated workspace and budget. Formal code is frozen.

## Execute and resume

```bash
source scripts/host_env.sh
python scripts/evaluate_robowits.py \
  --output runtime/robowits-seed-eval \
  --provider-home /home/junwenmiao/code/rax-local/provider \
  --profile chatgpt --gpu 1 --episodes 8 --formal-episodes 5
```

Use `--tasks robowits_06` for one task, or list mutation IDs explicitly. Reusing the
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
checks protocol separation, action rejection, geometry, host import isolation,
and task prompts. The doctor exercises real Genesis resets, all three cameras,
calibrated pixel round trips, and five native control steps; it is an interface
check, not a successful task evaluation.

## Initial local evaluation (2026-10-27)

The first completed task was `robowits_06` (push the red block onto the green
area), using `gpt-6-astra`, xhigh effort, RGB-D and eight exploration episodes.
The frozen controller passed 5/5 exploration rehearsals, but scored **0/5 on
formal layouts 0–4**. All five formal episodes reached the native 1000-step limit;
there were no missing scores. Thus the integration ran end to end, while this
policy failed to generalize. This is one task, not a full benchmark result.

The [machine-readable result](robowits_initial_result.json) records the trial,
bundle hash and every formal outcome. In the first formal layout, logs show
repeated 2–3 cm approach tracking errors followed by retries until timeout.
The formal outcomes were not used to revise the submitted policy.

The 30-seed-task batch is tracked under `runtime/robowits-seed-eval/`. Consult its
current manifest and refreshed summary for completion; the initial result above
must not be interpreted as the aggregate benchmark score.
