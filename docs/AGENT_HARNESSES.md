# Agent harnesses and model providers

RAX connects coding agents to robot observation and control tools. The agent
harness manages the model conversation, tool calls and session history; RAX
provides the workspace, robot interface, exploration budget and evaluation.
The model provider supplies inference. These are separate choices: Claude Code,
for example, can run against a self-hosted DeepSeek endpoint.

## Choose a harness

The following backends are available on the DexJoCo, HumanoidArena, LIBERO-PRO and
RoboDojo/BEHAVIOR simulation branches, in native and container execution.

| Harness | `agent_cli` | Model interface | Provider options |
| --- | --- | --- | --- |
| Codex CLI | `codex` | OpenAI Responses | OpenRouter or a compatible endpoint; CLI authentication follows the branch setup guide. |
| Claude Code | `claude` | Anthropic Messages | OpenRouter, Anthropic API, Claude login or a compatible endpoint. |
| DeepSeek Harness | `dsh` | Chat Completions for OpenRouter; Messages for DeepSeek/custom | OpenRouter, DeepSeek API, DeepSeek account or a compatible endpoint. |
| Kimi Code | `kimi` | OpenAI Chat Completions | OpenRouter, Moonshot API, Kimi account or a compatible endpoint. |

Each backend runs its vendor's agent loop. Adapters load when selected, so
installing an optional harness is independent of using Codex CLI or Claude Code.
Native execution uses the branch's process sandbox; container execution runs the
agent in an offline container and relays model requests through the host.
Credentials stay on the host in both modes.

## Install and configure

Start with the selected simulation branch's `docs/SETUP.md` to install the host
environment, simulator and sandbox dependencies. Optional harnesses require
Node.js 24 and npm. Install their pinned releases into runtime storage:

```bash
PY=runtime/envs/host/bin/python
ROOT="${RAX_RUNTIME_ROOT:-$PWD/runtime}"

$PY scripts/install_dsh.py \
    --prefix "$ROOT/agent-backends/deepseek-harness/tools/dsh-0.2.1-rc.2" --cache "$ROOT/cache"
$PY scripts/install_native_cli.py --backend kimi \
    --prefix "$ROOT/agent-backends/kimi-code/tools/kimi-2.2.0" --cache "$ROOT/cache"
```

For Kimi account authentication, add `--with-account` to its installation
command to build the host-side credential helper. Configure credentials and the
installed CLI through the selected backend's login setup, then prepare a session
with the operator dashboard or command line. For example, on DexJoCo:

```bash
CONFIG=$($PY scripts/configure_auto_research.py \
    --task click_mouse --sim-gpu 0 \
    --agent-cli kimi --agent-provider openrouter --agent-effort max \
    --episodes 16 --exploration-envs 1 --subagents 0)

# RAX_API_KEY_FILE points to a private OpenRouter key file outside the checkout.
bash scripts/start_auto_research_agent.sh --config "$CONFIG" \
    --agent-binary "$ROOT/agent-backends/kimi-code/tools/kimi-2.2.0/node_modules/.bin/kimi" \
    --agent-key-file "$RAX_API_KEY_FILE" --non-interactive
```

For DeepSeek Harness, select `--agent-cli dsh` and use its installed `dsh` binary. The branch's
`docs/USAGE.md` describes login records, launch, stop, resume and formal evaluation.
The added DeepSeek and Kimi adapters use one exploration environment,
no subagents and code formal evaluation.

The launcher accepts `--agent-binary` for a custom CLI installation,
`--agent-model` to select a model, `--agent-key-file` for API credentials,
`--agent-base-url` for a compatible endpoint and `--agent-auth-home` for a
dedicated vendor account directory. Keep key files and account directories
outside the source checkout.

## Authentication and model selection

| Access method | Provider values | Setup |
| --- | --- | --- |
| OpenRouter | `openrouter` | Supply an OpenRouter key and a model compatible with the harness interface. |
| Vendor API | `deepseek-api`, `moonshot-api` | Supply the corresponding vendor API key. |
| Vendor account | `deepseek-account`, `kimi-account` | Complete the vendor login; account helpers resolve and refresh credentials on the host. |
| Self-hosted or other compatible API | `custom` | Set the endpoint, model ID and private key file; match the harness's protocol. |

For DeepSeek account login, run the host-side authorization service and complete
the browser flow it prints:

```bash
$PY scripts/dsh_account.py serve --auth-home "$RAX_AUTH_HOME"
$PY scripts/dsh_account.py status --auth-home "$RAX_AUTH_HOME"
```

For Kimi account login, use the installed CLI's login flow with
`KIMI_CODE_HOME` set to a dedicated account directory. Pass that directory as
`--agent-auth-home` when launching a `kimi-account` session. API-key providers
use `--agent-key-file`; `custom` also takes `--agent-base-url`.

The OpenRouter configurations used for validation are:

| Harness | Model ID | Reasoning |
| --- | --- | --- |
| DeepSeek Harness | `deepseek/deepseek-v4.1-flash-1017` | Maximum |
| Kimi Code | `moonshotai/kimi-k3` | Maximum |

Native and container OpenRouter checks cover image input, tool calls, shell
execution, session continuation and sandbox boundaries. Vendor API and account
adapters are implemented; the migration's live service checks used OpenRouter.
Model availability and account access depend on the selected provider. Save the
requested model ID with experiment results: an account alias such as
`deepseek-flash` does not pin a checkpoint revision.

## Self-host DeepSeek

The [DeepSeek serving guide](DEEPSEEK_SERVING.md) describes the tested four-H200
deployment, with the exact model revision and vLLM image digest. It includes
weight preparation, GPU checks, startup and readiness, CUDA Graphs and DSpark
tuning, protocol tests, performance measurements and service cleanup.

The tested integration connects Claude Code to the service using `custom` and
the Anthropic Messages protocol. The serving lifecycle is independent of the
simulator and agent backend, so the same endpoint can serve compatible clients
across the simulation branches.

See [DeepSeek model results](https://github.com/robot-agent-exploration/rax/blob/dexjoco/native/docs/DEEPSEEK_MODEL_RESULTS.md)
for DexJoCo task scores, configurations, API pricing and the Click Mouse harness comparison.
