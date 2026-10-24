# Architecture

This document describes how a session is built: its services, its isolation
boundaries, the MCP tool contract, the evaluation lifecycle, and how to extend the
harness with a new simulator backend or session setting. For setup and day-to-day
use, see [Setup](SETUP.md) and [Usage](USAGE.md). The LIBERO-PRO backend has its own
document, [LIBERO-PRO backend](LIBERO_PRO_BACKEND.md).

## Contents

1. [Overview](#overview)
2. [Trust and isolation](#trust-and-isolation)
3. [The MCP contract](#the-mcp-contract)
4. [Session lifecycle](#session-lifecycle)
5. [Parallel exploration and subagents](#parallel-exploration-and-subagents)
6. [Workspace generator](#workspace-generator)
7. [Recording and evidence](#recording-and-evidence)
8. [Scoring](#scoring)
9. [Adding a simulator backend](#adding-a-simulator-backend)
10. [Adding a session setting](#adding-a-session-setting)

## Overview

```text
 operator ─ dashboard (services/dashboard) ─┐
          ─ terminal scripts (scripts/) ────┴─► launcher (harness/codex_cli, harness/claude_cli)
                                                   │ one launcher per session
   ┌───────────────────────────────────────────────┼──────────────────────────────────┐
   │ agent container (offline, no GPU)             │ host, per session                 │
   │   Codex CLI or Claude Code                    ▼                                   │
   │   Bash + Python (api.runtime.Context) ──MCP──► MCP frontend + supervisor          │
   │   /workspace (capped ext4 image)              │  services/controller/frontend.py  │
   │                                    ──model──► model relay (holds the credential)  │
   └───────────────────────────────────────────────┼──────────────────────────────────┘
                                                   ▼
                                    gateway (services/controller/gateway.py)
                                                   │ one native worker per environment
                                                   ▼
                                          episode simulator
                                  (official LIBERO, robosuite and MuJoCo,
                                   runtime/envs/libero_pro)
```

| Component | Location | Role |
| --- | --- | --- |
| Launcher | `harness/codex_cli/auto_research.py`, `harness/claude_cli/auto_research.py` | Prepares the workspace, starts the relay, the MCP frontend and the agent container; relaunches, resumes and runs the formal evaluation after exploration |
| MCP frontend | `services/controller/frontend.py` | The session's only MCP server: lifecycle tools, robot tools and Gemini, on a Unix socket |
| Supervisor | `services/controller/supervisor.py` | Owns the session state (`state.json`): episodes, budgets, bundles, rehearsals and formal batches |
| Gateway | `services/controller/gateway.py` | Admits only the session's tools, charges budgets, strips private fields, publishes frame sequences |
| Native backend | `services/libero_pro/` | The LIBERO-PRO simulator process and its tools |
| Exploration pool | `services/exploration/` | Several exploration environments, one worker process each |
| Isolated runner | `services/controller/sandbox.py` | Runs a frozen robot program in a fresh container for rehearsal and formal episodes |
| Formal workers | `services/controller/formal_worker.py`, `services/agent_formal/` | One process per concurrent formal episode: a frozen bundle, or a live agent copy |
| Workspace generator | `services/workspace/` | Builds every agent workspace from `auto_research_agent/` and `agent_skills/` |
| Benchmark registry | `services/benchmarks.py` | One entry per simulator backend; shared code dispatches through it |
| Dashboard | `services/dashboard/` | Operator website: prepare, launch, monitor, stop, resume, review |

**Sessions are independent and run in parallel.** Each session has its own directory
(`runtime/auto-research/<session>/`), launcher, relay, MCP frontend, simulators, agent
container and capped storage. Nothing is shared between sessions except the source
tree, the agent image and the GPUs. Inside one session, up to eight exploration
environments run at once, and formal evaluation runs up to 16 episodes at once.

**The backend is a plug-in.** LIBERO-PRO plugs into the harness through the registry in
`services/benchmarks.py` and its adapter module `services/libero_pro/benchmark.py`. Shared
code dispatches through the registry and never names the benchmark.

| | LIBERO-PRO |
| --- | --- |
| Robot | Franka Panda arm with a parallel-jaw gripper, under LIBERO's official OSC_POSE delta control |
| Control rate | 20 Hz |
| Simulator interpreter | `LIBERO_PRO_PYTHON` (default `runtime/envs/libero_pro/bin/python`, Python 3.8 and the official requirements) |
| Episode wall limit | 600 s plus 5 s per native task step |
| Simulator startup limit | 300 s |
| Episode reviews | no |
| Formal score | binary: the success rate over the 50 init states of the formal collection |

The host harness never imports a simulator package. The simulators run in their own
processes and talk to the host over a msgpack RPC (`services/simulator/`). MuJoCo renders
headless through the GPU's EGL; no container is needed for the simulator.

## Trust and isolation

### The agent container

The agent CLI, its subagents and every script it runs share one container built from
the offline agent image (`services/controller/agent_runtime.Dockerfile`). The launcher
starts it with:

- `--network=none`, a read-only root filesystem, the operator's non-root UID, all
  capabilities dropped, `no-new-privileges`, private IPC;
- memory, CPU and PID limits (16 GiB and 4 CPUs by default);
- **no GPU**. Only the simulators use a GPU, on the host.

The container sees only these mounts:

| Path in the container | Contents | Access |
| --- | --- | --- |
| `/workspace` | the agent's workspace, on a capped ext4 image | read-write |
| `/codex-home` or `/claude-home` | the CLI's home (conversation, settings), on the same image | read-write |
| `/workspace/runtime/autonomous_controller` | this session's published run artifacts | read-only |
| `/run/agent-relay` | two Unix sockets: the MCP frontend and the model relay | — |
| `/opt/codex/codex` or `/opt/claude/claude` | the host's native CLI binary | read-only |
| `/opt/agent/container_bridge.py` | bridges the CLI's MCP and model connections to those sockets | read-only |

The workspace image is a sparse file (`--workspace-gib`, default 1024), loop-mounted by
a short-lived privileged helper container (`harness/codex_cli/workspace_storage.py`).
The agent has no mount privileges itself.

The agent cannot see the repository, the simulator source, operator configuration,
the session's private state, native rewards, seeds or layout IDs, other sessions, the
Docker socket, host credentials or the internet.

### Credentials and relays

Model credentials stay on the host. The CLI in the container talks to a relay on the
host through a Unix socket; the relay adds the credential and forwards the request to
the provider.

- **Codex:** `harness/codex_cli/model_relay.py`, a fixed Responses endpoint for the
  session's model.
- **Claude Code:** `harness/claude_cli/messages_relay.py`, a Messages endpoint.

A relay is transparent. It passes requests and responses through as the native client
would send and receive them: every header in both directions, the body, and the
provider's status and error message. It replaces only the credential and sets only the
framing headers it must own. A refusal (for example a server-side tool) answers with a
body that names the reason. The relays retry provider overload and rate-limit statuses
(such as 429, 502, 503 and 504) themselves, before any response byte reaches the CLI, for up to 600
seconds. Every request is counted in `<session>/model-calls.json`; there is no model-call
cap.

Gemini (`gemini_generate`) is brokered by the MCP frontend with a host-held OpenRouter
key. Each session has one $10 Gemini ledger, kept in integer nanodollars, reserved before
a request is sent and shared by every phase and client (`services/controller/billing.py`,
`services/controller/gemini.py`).

### Isolated runs

Rehearsal and formal episodes of a frozen program run in a fresh container
(`services/controller/sandbox.py`) with the same hardening, 8 GiB of memory by default
and these mounts only:

- the frozen bundle at its registered `/workspace/code/<project>/` path, read-only;
- the runtime API at `/workspace/api/runtime.py`, read-only;
- this run's sanitized frames, read-only;
- a byte-capped writable `/workspace/output` (`--recording-gib`).

The program reaches the robot through a private MCP channel with robot tools and
Gemini only: no episode lifecycle, no development socket, no earlier runs. While an
isolated run executes, the development container is paused. After the run, safe
regular files from `/workspace/output` are copied to the run's `exports/`; links,
special files and oversized outputs are rejected, and nothing from them is executed or
deserialized on the host.

Containers share the host kernel; they are an isolation layer, not a hardware trust
boundary.

## The MCP contract

`services/mcp_contract.py` defines one immutable contract per session, used both for
tool discovery and for dispatch. The gateway rejects a tool outside the contract even
if a caller bypasses discovery.

### Tools by phase

| Phase | Tools |
| --- | --- |
| Exploration, code mode | the backend's robot tools, `gemini_generate`, `status`, `start_episode`, `evaluate`, `finish`, `register`, `rehearse`, `submit` |
| Exploration, agent formal | the same without `register`, `rehearse` and `submit` |
| Isolated run (rehearsal, formal bundle) | robot tools and `gemini_generate` |
| Agent formal copy | robot tools, `gemini_generate`, and a local `status` for its one episode |

Lifecycle tools (`services/controller/frontend.py`):

| Tool | Contract |
| --- | --- |
| `status` | task, episode state, wall limit, exploration episodes left, registered bundles and whether each has a clean rehearsal, storage, Gemini budget |
| `start_episode` | closes the previous episode and starts the next one on the next layout; takes no layout or seed |
| `evaluate` | the trusted binary `task_complete` of the current exploration episode |
| `finish` | closes the current exploration episode |
| `register(source)` | freezes `code/<project>/` (with `controller.py`) as a hashed bundle, without running it |
| `rehearse(bundle)` | runs the whole bundle unattended in a fresh exploration episode, under formal limits; consumes one exploration episode |
| `submit(bundle)` | closes exploration on one cleanly rehearsed bundle; the formal batch runs after the agent has ended |

Robot tools come from the backend. LIBERO-PRO offers status and observation,
`libero_step` (1 to 100 rows of the official 7-D OSC_POSE delta action, one per 20 Hz
control step) and pixel-to-position. See [LIBERO-PRO backend](LIBERO_PRO_BACKEND.md#interface).

### Replies

Every reply has a JSON text block first, then zero or more image blocks.
`attachments[*].content_index` points from the JSON to its images. RGB is 8-bit PNG;
depth is lossless uint16 PNG in millimetres, with 0 invalid. A motion call attaches only
its final frame and saves the complete sequence (see
[Recording and evidence](#recording-and-evidence)).

The gateway removes native reward, score, intermediate success, seeds, layout IDs and
host paths from every reply. An ended episode discloses only `episode_ended` and the
binary `task_complete`.

Errors pass through whole. An error reply carries the error type, the operation, the
stage, the complete reason (for a simulator exception, its message and innermost
traceback frames), `simulator_state` (where the episode stands after the error) and
`episode_ended`. An error never locks an episode: the episode continues from the
simulator's actual state. Only a real end stops it: the task ended, or the simulator
process or its connection is gone, in which case the reason names the exit code and the
simulator log's last errors (`services/controller/errors.py`).

### Environment routing

With more than one exploration environment, every environment-scoped tool (the robot
tools, plus `start_episode`, `evaluate` and `finish`) requires an integer `env_id`. The contract validates and removes it before the
call reaches a native worker; workers never see it. Single-environment and isolated
contracts reject `env_id`.

### Observation profiles

| Profile | Delivered |
| --- | --- |
| `rgbd` (default) | RGB, depth, camera intrinsics and extrinsics, the pixel-to-position tool |
| `rgb-only` | RGB and measured robot state; depth, calibration and the pixel-to-position tool are removed |

The RGB-only filter runs before image encoding, so depth never reaches the agent or its
published frames.

### Development Python

`auto_research_agent/api/runtime.py` gives scripts the same MCP endpoint as the CLI:

```python
from api.runtime import Context
with Context() as ctx:
    ctx.call("libero_observe")
```

A submitted program defines `main(ctx)` and uses only the context it receives. In
development, `ctx.env(i)` selects an exploration environment; inside a frozen program,
the runner supplies a context for its single environment.

The frontend multiplexes up to 16 clients on one dispatcher thread. Calls, not scripts,
are serialized, and a client that disconnects does not cancel a call already
dispatched.

## Session lifecycle

```text
prepare ─► launch ─► exploration ─► agent ends ─► freeze ─► formal evaluation ─► results
                       │  episodes, scripts,                    code mode: frozen bundle
                       │  register/rehearse/submit (code mode)  agent formal: live copies
                       └─ resume after a stop or failure
```

### Configuration

`scripts/configure_auto_research.py` (or the dashboard's **Prepare**) writes an
immutable configuration (`services/controller/config.py`) into a new session directory.
It pins the agent image digest and the simulator GPU UUID, and fixes the exploration
layout schedule before any simulator starts. A session is resumed only with an
unchanged configuration; the one exception is moving it to another GPU
(`harness/session_gpus.py`).

### Exploration

- **Episodes.** The operator sets the exploration budget (1 to 1000 episodes). Each
  `start_episode` plays the next layout of the exploration schedule; when episodes
  outnumber layouts, the schedule cycles through the collection again. Formal layouts
  in the same collection are skipped, so they stay held out.
- **Layouts.** Exploration (collection 0) layout `i` is a fresh reset with seed
  `1000 + i`, never an official init state; formal evaluation plays the 50 official init
  states of its collection (1 for official LIBERO, 2 to 5 for LIBERO-PRO's perturbations).
- **Limits.** Every episode has its native step limit and a wall limit of 600 seconds
  plus 5 seconds per native step (`services/libero_pro/tasks.py`), the same in exploration,
  rehearsal and formal. The session itself has no wall-clock, model-call or recording
  budget.
- **Accounting with one environment.** A start is reserved in `state.json` before the
  simulator is created, so a failed startup or an episode interrupted by a stop stays
  charged. Accounting with several environments is described in
  [Parallel exploration](#parallel-exploration-and-subagents).

### Code mode: register, rehearse, submit

1. `register` copies and hashes the bundle's regular files (no links, size and count
   limits). The registration path is part of the hash.
2. `rehearse` runs the exact bundle in a fresh exploration episode and isolated
   container, under the formal wall limit, startup included. A rehearsal *qualifies*
   when it runs to a clean, evaluated end with no simulator exit, evaluation, output or
   recording error. The task need not succeed.
3. `submit` accepts only a bundle with a qualifying rehearsal. It closes exploration;
   there is no second submission.

Changing the code means registering and rehearsing a new bundle. Every rehearsal and
formal result records `timing` (`episode_wall_timing_v1`), a breakdown of wall time
into environment setup, motion calls, other MCP calls, Gemini calls and controller
processing. A timeout names its `timeout_stage`: `environment_setup`,
`controller_execution` or `evaluation`.

### Freeze

When the exploring agent has ended with exploration closed, the launcher freezes the
session (`services/agent_formal/checkpoint.py`): the transfer folders (`memory`, `code`,
`handoff`) and the whole conversation, as the exploration checkpoint. Nothing later is
added to it. An agent that stops while episodes remain leaves exploration open and
nothing runs (`formal.json`: `exploration_incomplete`).

### Formal evaluation

| | Code mode | Agent formal |
| --- | --- | --- |
| What plays | the submitted bundle, alone | a fresh live copy of the agent per layout |
| Carries | the frozen bundle | the transfer folders (default `memory,code,handoff`, at most `transfer_mb`, default 100 MB) and the conversation (`formal_context`: `resume`, `compact` or `clear`) |
| Tools | robot tools and Gemini | robot tools, Gemini, development Python and a local `status` |
| Records | `formal_batch`, `formal-workers/episode-NNN/`, `formal-scores.json` | `agent_formal_batch`, `agent-formal/episode-NNN/`, `agent-formal-scores.json` |

- **Concurrency.** `formal_workers` (1 to 16, default 4)
  episodes run at once, each in its own worker process with its own simulator,
  container and supervisor lock. Agent formal copies start 60 seconds apart, so their
  simulators do not compete at startup, and can be spread over several GPUs
  (`formal_sim_gpus`).
- **Schedule.** The formal schedule is `formal_episodes` consecutive official init
  states (1 to 50, default 50) starting at `formal_seed`. It is reserved before the first episode; no episode is retried or dropped
  from the denominator.
- **Transfer packing.** Folders are packed in the order `handoff`, `memory`, `code`. A
  folder that does not fit the remaining cap is left out whole, with the reason recorded,
  and later folders may still fit. `compact` compacts the conversation once before the
  copies start; compaction never fails the trial.
- **Copies.** A copy works in a fresh capped workspace rendered for the formal episode
  (`FORMAL_AGENTS.md` as its `AGENTS.md`). Once it has made a model call, its own exit
  does not make the episode an error. A copy whose run fails while its episode is live
  resumes its conversation after 300 seconds, at most 5 times in a row (a run of 30
  minutes resets the count).
- **Live copies after code mode.** A finished code-mode session can also be played by
  live copies from the same freeze, beside its bundle formal
  (`--resume --agent-formal`, see [Usage](USAGE.md)).
- **Workers.** A worker still running 10 minutes past the episode's wall limit gets
  SIGTERM, then SIGKILL 3 minutes later. A worker that started an episode but died
  without a result counts as an error.
- **Infrastructure diagnostics.** `infrastructure_error_count` counts completed
  episodes that also had a worker or shutdown problem. It overlaps the outcome counts and
  is not an extra failure category.

### Resume and recovery

- **Resume.** A stopped, exited or failed session resumes in its own conversation
  (Codex `exec resume`, Claude Code `--resume`), in the same workspace.
- **Recovery on resume.** Episodes a dead frontend left live are recorded as
  interrupted, leftover simulators are terminated, and the shutdown seal is lifted.
- **Interrupted formal.** An interrupted agent formal resumes the layouts that have no
  outcome. An interrupted bundle formal is not resumed.
- **Automatic relaunch.** A non-interactive launch relaunches the agent while
  exploration is open:
  - after a turn that ended normally, at most 3 times;
  - after a run that failed, after 300 seconds, at most 5 times in a row (a run of 30
    minutes resets the count).
- **Cleanup.** Normal shutdown removes the session's containers and closes its
  simulators. After a SIGKILL of the supervisor, the next start removes persisted
  containers, and interrupted attempts stay consumed.

## Parallel exploration and subagents

`exploration_envs` (1 to 8) sets how many exploration environments a session has. With
more than one, `services/exploration/` runs one worker process per environment, each with
its own simulator, step counter and recorder on the session's simulator GPU.
This is process-level parallelism, not vectorized scenes. Workers start
lazily: an unused environment takes no GPU memory and no episode.

- **Calls.** Calls to different environments run concurrently, even over one MCP
  connection. Calls to the same environment serialize.
- **One ledger.** A durable ledger covers every start, reset and rehearsal across all
  environments, so four environments and ten episodes allow ten episode starts in total.
  An exhausted budget blocks new starts but not actions in live episodes.
- **Refunds.** An episode that ended with `error` (its worker failed: a broken pipe, an
  exit, a reply over the limit) or `interrupted` (the session stopped while it was live)
  is not charged. Its layout is replayed by the next episode, and the agent is told in
  `status` (`exploration_episodes_refunded`) and in its next prompt.
- **Lifecycle boundaries.** `register`, `rehearse` and `submit` wait for outstanding
  exploration calls to finish. `rehearse` and `submit` then close all live environments.
  Each isolated run still has exactly one environment.

`subagents` (0 to 8) caps concurrent exploration subagents. With 0, the cap is one per
environment when there are several, and none with one environment. Setting
`exploration_envs=1` with `subagents=N` gives several agents that take turns on one
robot.

| CLI | How the cap is applied |
| --- | --- |
| Codex | `multi_agent` with `max_concurrent_threads_per_session` set to the cap (`harness/codex_cli/configuration.py`) |
| Claude Code | the cap is stated in the workspace; with a zero cap the `Agent` tool is disallowed |

Environment ownership between subagents is a workspace convention
(`multi-env-exploration`, `multi-agent-exploration` skills), not enforced by the
server. At the end of every launch, `services/subagent_metrics.py` writes
`subagents.json`: subagents spawned, peak concurrency, MCP and environment calls per
agent, and handoffs.

## Workspace generator

Every workspace is built by one pipeline in `services/workspace/`:

```text
Setup (facts) + Recipe (role) + sources ─► build() ─► workspace + manifest
```

- **Setup** (`setup.py`): the MCP contract (phase, profile, environments, subagent cap,
  benchmark), the task, the agent CLI and the options (`agent-formal`,
  `formal-episode`, `handoff`). Facts only.
- **Recipe** (`recipe.py`, values in `recipes.py`): the template directory, the skill
  layers, skills a setup leaves out, the role's documents, skill-file variants and the
  vocabulary. Two roles exist: `AUTO_RESEARCH` (the exploring agent) and `FORMAL_COPY`
  (an agent formal copy).
- **Sources:** `auto_research_agent/` (the agent's `AGENTS.md`, `FORMAL_AGENTS.md`,
  `START_PROMPT.md` and `api/`) and `agent_skills/`. Every Markdown file is a sandboxed
  Jinja template (`templates.py`); an unknown variable fails the build. Shared
  passages live in `agent_blocks/` and are included with `{{ block("id") }}`.
- **build()** (`build.py`): copies the template, installs the skill layers, applies the
  role, renders every Markdown file, and writes `MCP_CONTRACT.json` and `MCP_SESSION.md`.
  It returns a manifest (each file's sha256), which the launcher writes outside the
  workspace as `workspace-manifest.json`.
- **Validator** (`validate.py`): checks links, skill names, mentions of absent skills or
  tools, CLI-specific text, single-environment and no-subagent wording, another
  benchmark's vocabulary, and the words a live-copy explorer or formal copy must not see.

Skills are layered in this order (`SKILL_LAYERS` in `recipes.py`); later layers add or
replace files:

| Layer | Contents |
| --- | --- |
| `task/` | `manual-robot-control`, `experience-memory`, `in-context-action-learning` |
| `coordination/` | `multi-env-exploration`, `multi-agent-exploration` |
| `perception/common/` | `wrist-camera-inspection` (robots with a wrist camera) |
| `perception/<depth\|rgb-only>-<benchmark>/` | `object-localization` |
| `control/<benchmark>/` | `robot-interface`, and the backend's own control skills |
| `auto-research/` | `autonomous-control`, `gemini`, `transfer-handoff`, formal-copy variants |
| `method/` | the research-method skills (evidence, regression, controller engineering, rehearsal, budgets), for backends with `method_skills` |

Agents see one flat `.agents/skills/` with robot-neutral names (`.claude/skills` links
to it for Claude Code). Skills a session cannot use are left out: the multi-environment
skill with one environment, the multi-agent skill without subagents, the demonstration
skill without a demonstration, the expert skill without expert episodes, and the
wrist-camera skill on a robot without a wrist camera.

The workspace an agent receives:

| Path | Contents |
| --- | --- |
| `AGENTS.md` (`CLAUDE.md` imports it) | the workflow and the episode lifecycle |
| `START_PROMPT.md` | the first message |
| `TASK.md` | the task rubric and, if selected, the demonstration |
| `MCP_CONTRACT.json`, `MCP_SESSION.md` | this session's tool contract |
| `.agents/skills/` | the skills for this session |
| `api/runtime.py` | the development `Context` |
| `code/`, `memory/`, `output/` | the agent's own work |
| `runtime/autonomous_controller/` | published run artifacts, read-only |
| `runtime/demonstrations/` | the demonstration images, when one is selected |

A resume reuses the existing workspace; the launcher refreshes only what the backend's
`refresh_workspace` hook updates.

To change workspace text safely, render every setup before and after and diff them,
then refresh the golden index:

```bash
runtime/envs/host/bin/python scripts/render_workspaces.py --out /tmp/before   # old revision
runtime/envs/host/bin/python scripts/render_workspaces.py --out /tmp/after    # new revision
diff -ru /tmp/before /tmp/after
runtime/envs/host/bin/python scripts/render_workspaces.py --index local_tests/goldens/workspaces.json
```

`local_tests/unit/test_workspace_goldens.py` fails on any unreviewed change, and
`test_workspace_validation.py` runs the validator on every setup.

## Recording and evidence

| Record | Location | Who sees it |
| --- | --- | --- |
| Run artifacts: `mcp.jsonl`, per-call responses, complete frame sequences, `result.json`; for isolated runs also frozen code, `exports/`, `stdout.log`, `stderr.log` | `<session>/published/runs/<run>/`, mounted at `/workspace/runtime/autonomous_controller/runs/<run>/` | agent (read-only) and operator |
| Native simulator output: logs, evaluator state, `sim/sensors.mp4` | `<session>/native/`, `formal-workers/`, `agent-formal/`, `exploration/` | operator only |
| Session state: `state.json`, `configuration.json`, `mcp.jsonl` audit, `model-calls.json` | `<session>/` | operator only |
| Formal scores: `formal-scores.json`, `agent-formal-scores.json` | `<session>/` | operator only |

Every motion call saves its full RGB (and, with RGB-D, depth) sequence at the control
rate. The reply attaches only the final frame and returns
`frame_sequence.manifest_path`, `frame_path_pattern` and `frame_index_range`, so the
agent can open the frames it needs from `runtime/autonomous_controller/runs/<run>/frames/`.

Artifact writers treat agent-writable paths as untrusted: frame directories are walked
with no-follow descriptors, and links and pre-existing files are rejected. Published
files are read-only to the agent. Private session paths, seeds, evaluation internals and
simulator files are never published.

## Scoring

Scores are operator-only (`services/scoring.py`). The agent sees only the binary
`task_complete` of its own exploration episodes. LIBERO-PRO scores each episode as success or failure (the registry's
`graded_score` is off), so the formal result is the success rate.

- **Per episode:** 1.0 on native success, otherwise 0. An episode whose run ended in an
  error is never counted as a success.
- **Per batch:** the mean over the fixed number of scheduled episodes. An episode
  without a valid score counts as zero and is reported in `missing_score_count`; it never
  shrinks the denominator. `average_score` is written only once the batch is complete.
- **Success rate:** `formal_batch.success_rate` is successes divided by scheduled
  episodes. Success requires native success and a clean run.
- **Per task and collection:** a batch plays one task on one collection; a suite's
  success rate is the mean over its ten tasks, and each perturbation is scored by its own
  batch.

## Adding a simulator backend

A backend is a registry entry plus an adapter module. Shared code dispatches through the
entry and never compares benchmark names.

1. **Add an entry** to `BENCHMARKS` in `services/benchmarks.py`:

   | Field | Meaning |
   | --- | --- |
   | `name`, `label` | registry key and display name |
   | `adapter` | module implementing the hooks below, e.g. `services.<name>.benchmark` |
   | `robot_tools` | every robot tool name; prefix them with the backend name |
   | `geometry_tools` | tools the RGB-only profile removes |
   | `unscoped_tools` | pure computation that needs no environment |
   | `mutating` | tools that advance physics |
   | `previewable` | tools whose `preview_only: true` makes them planning-only |
   | `status_tool`, `observe_tool` | the robot status and observation tools |
   | `tool_prefixes`, `episode_tool_prefixes` | name prefixes; tools whose replies carry the episode's `step_id` |
   | `simulator_modules` | simulator server modules, the episode server first |
   | `startup_seconds` | simulator startup deadline |
   | `robot` | words shared workspace sources use for the robot (tool names, cameras, control rate) |
   | `partial_credit`, `graded_score`, `wall_limit`, `episode_reviews` | capabilities |
   | `planner_files` | planning and stepping sources a parallel formal batch fingerprints |
   | `method_skills` | the workspaces take the research-method skills (`agent_skills/method/`) |
   | `task_nouns` | regex of task nouns that skills never name (they belong in `TASK.md`) |
   | `vocabulary` | regex of this backend's own names, which other backends' workspaces must not mention |
   | `task_tag`, `score_label`, `layout_note` | dashboard text |
   | `setup_defaults`, `cli_defaults`, `fixed` | dashboard and CLI defaults, and settings that do not apply (with the reason) |

2. **Write the adapter** (`services/<name>/benchmark.py`). Keep it import-light: load
   simulator modules inside the functions.

   | Hook | Returns or does |
   | --- | --- |
   | `tool_definitions()` | the robot tools' MCP schemas |
   | `backend(config)` | the controller backend of one environment |
   | `task_names()`, `task_timeouts(task)` | the task catalog; `native_step_limit`, `episode_seconds` (or `None`) |
   | `validate_config(config)`, `validate_settings(values)` | the backend's rules for a configuration and for dashboard values |
   | `validate_layouts(config)` | check that the formal and exploration layouts exist |
   | `exploration_seeds(...)` | one layout per exploration episode, skipping formal layouts |
   | `task_context(config, demonstration, *, live_formal)` | the text of `TASK.md` |
   | `refresh_workspace(config, workspace)` | update instructions on deploy and resume |
   | `fetch_demonstration(...)`, `provision_demonstration(...)` | cache visual context before deploy, and copy it into a workspace |
   | `episode_reviewer(root)`, `review_analysis()` | a reviewer of closed episodes and its analysis module, or `None` |
   | `reference_mounts(config)` | read-only reference data for the agent container, such as expert episodes |
   | `unscoped_call(name, arguments)` | the reply of one of the entry's `unscoped_tools` |

3. **Run the simulator in its own process.** Speak the msgpack RPC in
   `services/simulator/` and keep simulator dependencies out of the host interpreter
   (LIBERO-PRO runs in its own Python 3.8 conda environment).
4. **Add skills** under `agent_skills/control/<name>/` and
   `agent_skills/perception/<profile>-<name>/`, and robot facts in the entry's `robot`
   field rather than conditions in shared templates.
5. **Add tests:**
   - the backend's tool schemas and error shapes, with a fake simulator;
   - an import-isolation test: host modules import with the simulator packages blocked
     (see `local_tests/unit/test_libero_pro_backend.py`);
   - workspace rendering for the new backend, added to `scripts/render_workspaces.py`
     and the golden index;
   - `local_tests/unit/test_benchmark_registry.py` registers a fake backend and checks
     that nothing falls back to the default backend, and that shared modules contain no
     backend name comparisons;
   - a check that starts the real simulator (as `scripts/libero_pro_doctor.py` does).

## Adding a session setting

1. Define and validate its immutable meaning in the configuration
   (`services/controller/config.py`) and, if it changes tools, in the contract
   (`services/mcp_contract.py`). Reject unsupported combinations before reserving
   resources.
2. Use it for both discovery and dispatch; never hide a tool only from discovery.
3. Pass it to each worker and isolated runner. A running episode never reads mutable
   dashboard defaults.
4. If outputs change, update inline replies, published files and workspace text
   together, using template conditions on setup facts.
5. Extend the contract tests, the forged-call tests, the workspace goldens and a real
   worker test. The existing lifecycle and budget tests must still pass.

New execution topologies belong in the routing and worker adapters
(`services/exploration/`); new evaluation semantics belong in the supervisor.
