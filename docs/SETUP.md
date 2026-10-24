# Setup

This guide installs everything a session needs on one Linux machine. Each step is one
script, and two doctors check the result at the end. The repository holds only source
code. Everything the scripts download or build goes under `runtime/`, a link to a
directory you choose, or into Docker's image store. Credentials stay in your home directory.

| Step | What | Needed for |
| --- | --- | --- |
| [0](#0-host-requirements) | Linux host, NVIDIA GPU and driver, Docker | everything |
| [1](#1-sources) | Pinned official LIBERO-PRO checkout | everything |
| [2](#2-host-environment) | The harness's Python environment `runtime/envs/host` | everything |
| [3](#3-libero-pro-environment-and-data) | The official LIBERO simulator environment `runtime/envs/libero_pro`, and the LIBERO config and perturbation files `runtime/libero-pro` | every session |
| [4](#4-agent-image) | Offline agent image `rax-auto-research:dev` | every session |
| [5](#5-agent-cli-and-model-credentials) | Codex CLI or Claude Code, and a model provider | every session |
| [6](#6-demonstrations) | Three verified demonstrations per task | sessions with a demonstration (the default) |
| [7](#7-check-the-installation) | Doctors and tests | recommended |

## 0. Host requirements

- **Operating system:** Linux x86_64.
- **GPU:** one NVIDIA GPU whose driver provides EGL (`libEGL_nvidia`). MuJoCo renders the
  cameras headless through EGL on the GPU the session chooses; physics runs on the CPU.
  Only the simulators use a GPU; agents and their programs run on the CPU. See [GPUs](#gpus).
- **Docker:** rootful Docker with BuildKit, usable by your (non-root) user without sudo:
  add yourself to the `docker` group. Only the agent runs in a container, and it needs no
  GPU, so the NVIDIA Container Toolkit is not needed.
  - Each session's workspace is a capped ext4 image, loop-mounted by a short-lived
    `--privileged` container. The daemon must allow privileged containers, and the host
    needs `/dev/loop-control`.
  - Put Docker's image store on a fast disk; the agent image is about 12 GB.
- **Host tools:** `git`, `git-lfs`, `curl`, `sha256sum`, `mkfs.ext4`, `findmnt` and
  `nvidia-smi`. Everything else (conda, Python, CMake) is installed by the scripts.
- **Disk:** use a fast local SSD for `runtime/`. The LIBERO-PRO checkout is about 1 GB with
  its assets, the agent image about 12 GB (plus its 3.8 GB wheelhouse), and each task's
  demonstration file is downloaded while its demonstrations are installed and removed
  afterwards. A session keeps every episode's recording. Each session's workspace image is
  sparse, so its nominal size (`--workspace-gib`, default 1024) takes no disk until used.

## 1. Sources

```bash
git clone --branch libero-pro/container https://github.com/robot-agent-exploration/rax.git
cd rax
export RAX_RUNTIME_ROOT=/fast-disk/rax/runtime    # optional; default: .local-runtime/ in the repository
bash scripts/bootstrap_sources.sh
```

The bootstrap script:

- links `runtime/` to `$RAX_RUNTIME_ROOT`, or to `.local-runtime/` when the variable is
  unset;
- clones the official LIBERO-PRO repository at the commit pinned in `dependencies.lock`
  next to the runtime directory, linked as `LIBERO-PRO/`. The harness never modifies it.

Session artifacts, capped workspaces and image builds must live under the *artifact root*.
By default that is the directory `runtime/` links to, so nothing needs setting. If you set
`RAX_ARTIFACT_ROOT`, it must be that directory or one of its ancestors.

Run every later step from the repository root.

## 2. Host environment

```bash
bash scripts/setup_host_env.sh
```

The launchers, the MCP service, the workspace generator, the dashboard and the tests run in
`runtime/envs/host`: Python 3.12 with NumPy, Pillow, Jinja2, msgpack, PyYAML, imageio,
SciPy and pytest at the versions in `dependencies.lock`. The script installs the pinned
Miniforge into `runtime/miniforge3` (after checking its SHA-256) and creates the prefix. It
holds no simulator: the host never imports LIBERO, robosuite or MuJoCo.
`scripts/host_env.sh` sets the environment variables if you want them in a shell.

## 3. LIBERO-PRO environment and data

```bash
bash scripts/setup_libero_pro_env.sh
runtime/envs/host/bin/python scripts/setup_libero_pro_data.py
```

The simulator processes run in `runtime/envs/libero_pro`, built as the official README does:
conda Python 3.8.13 (from conda-forge only, since some sites block the `defaults` channel)
and the checkout's `requirements.txt`, with the additions pinned in `dependencies.lock`:
MuJoCo pinned (the official requirements leave it open, and newer MuJoCo changes how LIBERO's
init states settle), the CPU build of the official torch version (the simulator only loads
init states with it), and msgpack for the simulator RPC. CMake is installed into the
environment to build robomimic's `egl_probe`. The `libero` package is imported from the
pinned checkout rather than installed into it. The script refuses a checkout that is not at
the pinned commit or has local changes, and ends by printing the installed versions.
It uses the Miniforge of step 2 (`CONDA_EXE` selects another conda binary).

The upstream workflow moves the perturbation files into the package and asks for paths on
the first `import libero`. Instead, `setup_libero_pro_data.py` downloads LIBERO-PRO's
perturbation release (`zhouxueyang/LIBERO-Pro` on Hugging Face, at the pinned revision),
checks it against the release's `SHA256SUMS.txt`, and builds `runtime/libero-pro/`: a
`config/config.yaml` that LIBERO reads through `LIBERO_CONFIG_PATH`, and `bddl_files/` and
`init_files/` trees that link the checkout's original suites and the perturbed ones.
`LIBERO_PRO_RUNTIME_ROOT`, `LIBERO_PRO_DATA` and `LIBERO_PRO_PYTHON` override the defaults;
`scripts/libero_pro_env.sh` sets these variables if you want them in a shell.

## 4. Agent image

For DeepSeek Harness or Kimi Code, follow the [agent harness guide](https://github.com/robot-agent-exploration/rax/blob/main/docs/AGENT_HARNESSES.md) after installing the host dependencies.

```bash
runtime/envs/host/bin/python scripts/build_agent_image.py
```

The agent, its subagents and every robot program it writes run in this image: Python
3.12 (`python:3.12-slim`) with git, ripgrep, NumPy, SciPy, Pillow, OpenCV, h5py and a
CPU-usable PyTorch. The script downloads the pinned wheels into
`runtime/agent-image/wheels`, records their SHA-256, and builds the tag
`rax-auto-research:dev`. It takes about 5 minutes.

The containers run without network. Each session pins the image digest when it is
prepared, so rebuilding the tag never changes a prepared or running session.
`--offline` rebuilds from the downloaded wheels and refuses if they changed.

## 5. Agent CLI and model credentials

Install at least one agent CLI on the host as a **native binary**. The launcher mounts
the binary into the agent container, which has no Node.js, so npm-installed wrappers do
not work.

- **Codex CLI** (default): the standalone release binary from
  [github.com/openai/codex/releases](https://github.com/openai/codex/releases). Tested with 0.162.1.
- **Claude Code:** the native installer from
  [docs.claude.com](https://docs.claude.com/en/docs/claude-code/setup). Tested with 2.1.296.

Credentials never enter the container. A relay on the host holds the credential, and
the CLI in the container talks to that relay.

### Codex providers

By default a Codex session uses OpenRouter with the model `openai/gpt-6-astra`. Store
the OpenRouter key in a private file, or export it:

```bash
mkdir -p ~/.codex/secrets && chmod 700 ~/.codex/secrets
install -m 600 /dev/null ~/.codex/secrets/openrouter_api_key   # then paste the key into it
# or: export OPENROUTER_API_KEY=...
```

The launcher looks for the key in `--key-file`, then `~/.codex/secrets/openrouter_api_key`,
then `OPENROUTER_API_KEY`. It sends this key only to an OpenRouter endpoint.

To use another Responses API provider, write a provider profile. The launcher reads
`<provider-home>/config.toml` and then `<provider-home>/<profile>.config.toml`, where the
later file wins. `--provider-home` defaults to `$CODEX_HOME`, else `~/.codex`. `--profile`
defaults to the name in `<provider-home>/experiment-profile`, else `openrouter`. Only the
harness reads these files: the Codex CLI in the container is configured to talk to the
relay.

```toml
# ~/.codex/myprovider.config.toml, used with --profile myprovider
model_provider = "myprovider"

[model_providers.myprovider]
name = "My provider"
base_url = "https://api.example.com/v1"
wire_api = "responses"                  # the only supported API
env_key = "MY_PROVIDER_API_KEY"         # read when the session is deployed
# http_headers = { "X-Team" = "robotics" }

[rax]
model = "gpt-6-astra"                   # the session's only model (default openai/gpt-6-astra)
# key_file = "secrets/myprovider_key"   # instead of env_key; relative to the provider home
# chatgpt_auth = "auth.json"            # a Codex ChatGPT login, re-read on every call
# native_models = true                  # strip a leading "openai/" from the model name
```

The credential comes from the first of these that is set: `[rax] chatgpt_auth`, then
`[rax] key_file`, then the OpenRouter key (OpenRouter endpoints only), then `env_key`. A
provider that needs an `auth` command is refused. Without a provider table, the
`openrouter` profile uses `https://openrouter.ai/api/v1`.

The relay admits only the session's model at the session's reasoning effort
(`--agent-effort`), only client-executed tools (no hosted tools, plugins or web search),
only inline images, and no `previous_response_id`.

### Claude Code providers

Choose the provider when you prepare the session (`--agent-provider`, or the dashboard):

| Provider | Model | Credential |
| --- | --- | --- |
| `openrouter` (default) | `anthropic/claude-opus-5.5` | The OpenRouter key, as for Codex |
| `claude-login` | `claude-opus-5-5` | A token for a dedicated Claude account, set up as below |
| `anthropic` | `claude-opus-5-5` | An Anthropic API key: `--claude-key-file` at launch, or `ANTHROPIC_API_KEY` |

`--claude-model` overrides the model at launch. Claude Code's subagent alias `sonnet` maps
to Sonnet 5.5 on OpenRouter and to Haiku 4.5 on the first-party providers, and `haiku`
maps to Haiku 4.5.

`claude-login` never uses your personal Claude login (`~/.claude`), and refuses it. Set up
a separate account for experiments once:

```bash
bash scripts/setup_claude_experiment_login.sh [--email experiment-account@example.com]
PYTHONPATH=. runtime/envs/host/bin/python -m harness.claude_cli.experiment_login check
```

The script signs that account in under its own configuration directory, issues a
long-lived `claude setup-token` token, and stores it as
`~/.config/rax/claude-experiment.token` (mode 0600). Set `RAX_CLAUDE_EXPERIMENT_DIR` to use
a directory other than `~/.config/rax`. `check` makes one free API call, and `status`
reports the stored identity without the network. The relay reads the token on every
request, so running the script again rotates the token for running sessions too. The
dashboard checks the token when it prepares a `claude-login` session, and the launcher
checks it before every launch and resume. `--claude-key-file` points a launch at another
token file.

### What the relays do

- **Retries.** Provider answers 429, 502, 503 and 504 (and 529 for Claude) are retried for
  up to 10 minutes, following the provider's `Retry-After`. After that, the provider's
  status, headers and error message reach the CLI unchanged.
- **Refusals.** A request the relay refuses (another model, a server-side tool, media by
  URL) is answered 403 with a JSON body naming the reason.
- **Provider failures.** A provider that is unreachable, times out or breaks off
  mid-response gives 502 with the reason.
- **Concurrency.** A relay serves the agent, its subagents and 2 spare calls at once,
  and at least 8. A call over the limit is answered 503 with `Retry-After: 1`, and the
  CLIs retry it.

For a Codex session, the launcher writes the provider address and its headers, including
the key, to `<session>/model-relay.json` (mode 0600, never mounted into a container), so
keep session directories private; a ChatGPT login is stored only as a path and read on
every call. A Claude session stores only the key file's path, or reads the environment
variable at each launch, and its relay reads the credential for every request.

### Gemini tool (optional)

The agent can call Gemini (`gemini_generate`, model `google/gemini-3.8-flash`) through
the MCP service, for example to check an image. It uses the OpenRouter key from the
same file or `OPENROUTER_API_KEY`, and each session may spend at most $10. Without an
OpenRouter key the tool stays listed, and every call answers `Gemini is not configured`.

## 6. Demonstrations

By default a session shows the agent the final third-person frames of three official
demonstrations of its task (`--demonstration-context terminal_state`). They come from
LIBERO's official human demonstrations (`yifengzhu-hf/LIBERO-datasets` on Hugging Face, at
the revision pinned in `dependencies.lock`). Install them per task, on a machine with a GPU:

```bash
runtime/envs/host/bin/python scripts/setup_libero_pro_demos.py --gpu 0 --task libero_spatial_0 --task libero_goal_8
```

For each task, the script downloads the task's demonstration file, replays candidates
(`--candidates`, default 12) in the official environment from their recorded start states,
in a random order seeded by the task name, and keeps the first three that succeed, with
third-person keyframes rendered during the replay and a SHA-256 manifest, under
`runtime/reference-demos/libero_pro/<task>/`. The downloaded file is removed afterwards
unless `--keep-download` is given. Sessions with `--demonstration-context none` need no
demonstrations. See [demonstrations](LIBERO_PRO_BACKEND.md#demonstrations).

## 7. Check the installation

```bash
bash scripts/doctor.sh
runtime/envs/host/bin/python scripts/libero_pro_doctor.py --gpu 0 --task libero_spatial_0
```

`doctor.sh` checks the host environment, Docker access and the agent image, loop devices
and host tools, the agent CLIs, the OpenRouter key and the GPUs. It prints one line per
check and exits non-zero if any check fails.

`libero_pro_doctor.py` starts the official simulator for each `--task` on the given GPU,
once on an exploration layout and once on an official init state (`--collection` chooses
other collections), through the same host client sessions use. It reports the startup time,
the observation contract (the 8-D state, both cameras at 256x256 upright, depth and
calibration, the instruction), a zero row and a short motion, depth back-projection of every
visible object's true position in both cameras, and the operator files, then runs the
simulator-side checks (`services/libero_pro/checks.py`: every collection loads, official
layouts are reproduced exactly, the controller's response). It writes `doctor-report.json`
under `runtime/diagnostics/` and exits non-zero if a task failed.

Then run the unit tests:

```bash
runtime/envs/host/bin/python -m pytest -m "not gpu" -q local_tests/unit
```

They use fake simulators and a local mock model provider, and call no paid model. Tests that
run real containers need the agent image. See [local_tests/README.md](../local_tests/README.md).

## GPUs

Every simulator is one MuJoCo process that renders on the session's simulator GPU. A
session runs these at once:

- one simulator per live exploration environment (`--exploration-envs`, default 1);
- during formal evaluation, one simulator per concurrent formal episode
  (`--formal-workers`, default 4).

Physics runs on the CPU, so CPU cores bound the concurrency as much as the GPU does. On a
small machine, lower `--formal-workers` and `--exploration-envs`. Several sessions can share
a GPU, or use different GPUs.
