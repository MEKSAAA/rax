# Local LIBERO validation (2026-10-27)

The `libero-pro/native` implementation completed a real end-to-end run on this machine:
Codex exploration, controller generation, independent rehearsals, bundle submission,
and frozen-program evaluation in MuJoCo. This is a one-task smoke validation, not a
reproduction of the paper's full benchmark.

## Result

- Task: `libero_goal_0`, open the middle drawer.
- Agent: Codex CLI 0.164.0, `gpt-6-astra`, xhigh reasoning, existing ChatGPT login.
- Inputs: RGB-D, no demonstrations, no external Gemini perception.
- One exploration environment, no subagents; eight exploration episodes available and used.
- Frozen controller: `55e0357252c247fb8f9229f82d07cf2c`.
- Independent rehearsals: 4/4 successful.
- Formal evaluation: 3/3 successful, official collection 1, initial states 0–2;
  each finished at step 268 of the 300-step limit, with process exit code 0.
- Formal evaluation runs the submitted Python program without the exploring agent.
- Launcher exited normally and `formal.json` reports `completed`.

Session directory:
`/home/junwenmiao/code/rax-local/runtime/auto-research/20261027T181815Z_dd80c660cd/`

Evidence within that directory:

- `formal-scores.json` and `formal.json`: native evaluation results.
- `agent-lifecycle.json`: launcher completion.
- `agent-workspace/code/drawer/`: generated controller and perception implementation.
- `agent-workspace/memory/REHEARSALS.md`: rehearsal evidence and limitations.
- `native/results/libero_goal_0_formal_*/sim/sensors.mp4`: formal-run videos.
- `published/runs/formal-*/result.json`: process exit codes, timings and native outcomes.

The exploratory agent reported 124,843 tokens. The launcher ran for about 15.5 minutes,
including formal evaluation. This small sample does not establish a general success rate.

## Installation and compatibility

The checkout is `/home/junwenmiao/code/rax`, on `local/libero-run`.
`runtime/` links to `/home/junwenmiao/code/rax-local/runtime`.
The pinned LIBERO-PRO source is at `/home/junwenmiao/code/rax-local/LIBERO-PRO`.
Official bootstrap, host environment, simulator environment and data setup scripts were run.
The original benchmark source was not changed.

System bubblewrap 0.6.1 lacks `--size`; use the project environment's bubblewrap 0.13.1.
System Node 12 cannot parse the dashboard; Node 26.12.1 is installed in the project environment.
To install these tools in a fresh host environment:

```bash
runtime/miniforge3/bin/conda install -y -p runtime/envs/host \
  --override-channels -c conda-forge 'bubblewrap=0.13.1' 'nodejs>=22'
```

Always source `scripts/host_env.sh` before launch so the project tools take precedence.
The Codex launcher now exposes only its `tmp/arg0-linux` helper directory as read-only
inside its otherwise hidden session home, fixing Codex 0.164 startup. Tests also account
for the empty `/home` ancestors required when mounting a Python environment stored there.

Host doctor passed. The simulator doctor passed exploration and official layouts,
camera/depth checks, deterministic resets and control characterization. After the final
permission fix, all 42 targeted host/frontend/real-Codex tests passed. The final full
non-GPU regression suite passed: **757 passed, 25 skipped** in 62.72 seconds.
Its log is `/home/junwenmiao/code/rax-local/unit-tests-verified.log`.

The upstream data setup recorded all 320 downloaded perturbation files as `unverified`:
its release checksum list did not contain their paths. This smoke run uses original
LIBERO states from the pinned Git checkout, not those perturbation files.

## Run a fresh session

The existing local profile is
`/home/junwenmiao/code/rax-local/provider/chatgpt.config.toml`.
It references the existing Codex login file; it contains no copied token.

```bash
cd /home/junwenmiao/code/rax
export RAX_PROVIDER_HOME=/home/junwenmiao/code/rax-local/provider
bash scripts/run_local_libero_smoke.sh
```

This starts a new exploration and uses model quota. Optional environment variables:
`RAX_TASK`, `RAX_GPU`, `RAX_EPISODES`, `RAX_FORMAL_EPISODES`, and `RAX_PROFILE`.
Defaults are `libero_goal_0`, GPU 1, eight exploration episodes, three formal episodes,
and the `chatgpt` profile. Existing sessions are preserved.

For local video/result browsing, start the official dashboard and select Earlier runs:

```bash
source scripts/host_env.sh
bash scripts/run_operator_dashboard.sh
```

Open the private loopback URL printed by the dashboard. No dashboard is left running
by this validation.
