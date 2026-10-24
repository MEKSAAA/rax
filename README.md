# RAX on LIBERO-PRO (container branch)

**[Project website](https://robot-agent-exploration.com/)** · [Paper](https://robot-agent-exploration.com/rax.pdf) · [All branches](https://github.com/robot-agent-exploration/rax/tree/main)

This branch runs RAX on LIBERO and LIBERO-PRO. A coding agent (Codex CLI, Claude Code,
DeepSeek Harness or Kimi Code) learns a tabletop manipulation task by exploring the
simulator through MCP tools. It keeps notes and verified skills in its workspace, and then
is evaluated on the task's official LIBERO init states, or on one of LIBERO-PRO's
perturbations of them. Evaluation either runs the robot program the agent wrote, or runs
live copies of the agent.

The agent and its programs run in an offline Docker container. They have no GPU, no
network, and no access to the simulator's ground truth. Robot, Gemini and episode access
go through one host-owned MCP service.

## Benchmark

| Backend | Robot | Tasks | Exploration layouts | Formal layouts |
| --- | --- | --- | --- | --- |
| [LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO) | Franka Panda arm with a parallel-jaw gripper, LIBERO's official OSC_POSE delta control at 20 Hz, `agentview` and `robot0_eye_in_hand` cameras | the 40 tasks of LIBERO's four suites, `libero_spatial_0`..`9`, `libero_object_0`..`9`, `libero_goal_0`..`9`, `libero_10_0`..`9` | fresh seeded resets of the original task, never an official init state | the 50 official init states of each task: LIBERO's own (collection 1), or LIBERO-PRO's swap, object, language or task perturbation (collections 2-5) |

The simulator is LIBERO's official environment, unmodified, in robosuite and MuJoCo, from
the pinned LIBERO-PRO checkout with its released perturbation files. It runs natively in its
own conda environment (Python 3.8) and renders headless on one GPU through EGL; one GPU is
enough. The backend sits behind the harness's benchmark interface: an entry in
`services/benchmarks.py` (its tools, robot facts, setup defaults and capabilities) and the
adapter `services/libero_pro/benchmark.py`. The harness calls only that interface; see
[Adding a backend](docs/ARCHITECTURE.md#adding-a-simulator-backend).

Sessions run in parallel. Each session owns its simulators, its MCP service, its agent
container and its storage, so several sessions (on one or more GPUs) never share state.
Inside a session, exploration can use up to eight simulator environments at once, and
formal evaluation runs several episodes concurrently.

## How a session runs

1. **Explore.** The agent reads `TASK.md` (the official instruction and, if selected, the
   final frames of official demonstrations), then solves the task by hand: it observes,
   moves the arm and gripper through MCP tools, and checks the result. Each exploration
   episode uses a new layout, and the operator sets the episode budget.
2. **Compile.** The agent turns what worked into `code/<project>/controller.py:main(ctx)`,
   a program made of step functions that check what they observe, and tests it unattended.
3. **Evaluate (formal).** One of two modes, chosen when the session is prepared:
   - **Code (default):** the agent registers its program, passes one clean rehearsal, and
     submits it. The frozen program then plays the 50 official init states alone.
   - **Agent formal:** after exploration, live copies of the agent each play one formal
     episode, carrying their memory and code.

A finished session's frozen program can then be scored on LIBERO-PRO's perturbations by
post-hoc formal batches, and an optional second stage continues a finished session on
fresh layouts of the swap perturbation; see [LIBERO-PRO backend](docs/LIBERO_PRO_BACKEND.md).

The operator dashboard prepares, launches, monitors, stops and resumes sessions. Every
dashboard action is also a terminal command.

## Quick start

```bash
git clone --branch libero-pro/container https://github.com/robot-agent-exploration/rax.git
cd rax
export RAX_RUNTIME_ROOT=/fast-disk/rax/runtime            # where environments, data and sessions live
bash scripts/bootstrap_sources.sh                         # pinned official LIBERO-PRO source
bash scripts/setup_host_env.sh                            # the harness's own Python environment
bash scripts/setup_libero_pro_env.sh   # the official LIBERO simulator environment
runtime/envs/host/bin/python scripts/setup_libero_pro_data.py                 # LIBERO config and perturbation files
runtime/envs/host/bin/python scripts/build_agent_image.py # offline agent image (~12 GB)
runtime/envs/host/bin/python scripts/setup_libero_pro_demos.py --gpu 0 --task libero_spatial_0   # demonstrations
# Install an agent CLI and a model credential (docs/SETUP.md, step 5),
# e.g. an OpenRouter key in OPENROUTER_API_KEY or ~/.codex/secrets/openrouter_api_key.
bash scripts/doctor.sh                                    # checks the host
runtime/envs/host/bin/python scripts/libero_pro_doctor.py --gpu 0 --task libero_spatial_0   # checks the simulator
bash scripts/run_operator_dashboard.sh                    # prepare and launch sessions
```

The full guides:

- [Agent harnesses](https://github.com/robot-agent-exploration/rax/blob/main/docs/AGENT_HARNESSES.md): Codex CLI, Claude Code, DeepSeek Harness and Kimi Code; providers, authentication and setup.
- [DeepSeek serving](https://github.com/robot-agent-exploration/rax/blob/main/docs/DEEPSEEK_SERVING.md): four-H200 setup, tuning and protocol checks.
- [Setup](docs/SETUP.md): host requirements, every installation step, credentials for the
  agent CLIs, the demonstrations, and the tests.
- [Usage](docs/USAGE.md): preparing, launching, monitoring, stopping and resuming
  sessions; agent formal, the swap second stage and parallel exploration; results and scores.
- [LIBERO-PRO backend](docs/LIBERO_PRO_BACKEND.md): tasks, layout collections, tools,
  observations, scoring, perturbation batches and demonstrations.
- [Architecture](docs/ARCHITECTURE.md): services, isolation, the MCP contract, the
  evaluation lifecycle, and how to add a backend.

## Repository layout

| Path | Contents |
| --- | --- |
| `auto_research_agent/` | The agent's instructions (`AGENTS.md`, `FORMAL_AGENTS.md`, `START_PROMPT.md`) and its in-container Python API (`api/`) |
| `agent_skills/`, `agent_blocks/` | Every agent skill and shared text block, written once and layered per robot, observation profile and environment count ([README](agent_skills/README.md)) |
| `harness/` | Launchers for Codex CLI and Claude Code, model relays, session control |
| `services/controller/` | The per-session MCP frontend, supervisor, sandbox, gateway and formal evaluation |
| `services/libero_pro/` | The LIBERO-PRO backend: host adapter, simulator server, task catalog and layout collections, task rules, demonstrations and checks |
| `services/simulator/` | The RPC between the host and a simulator process |
| `services/exploration/` | Parallel exploration environments |
| `services/agent_formal/` | Live-agent formal evaluation |
| `services/workspace/` | The workspace generator |
| `services/dashboard/` | The operator dashboard ([README](services/dashboard/README.md)) |
| `scripts/` | Setup, launch, stop, dashboard and doctor scripts |
| `local_tests/` | Unit and integration tests ([README](local_tests/README.md)) |

## Acknowledgements

This branch builds on [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) and
[LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO), LIBERO-PRO's perturbation release
(`zhouxueyang/LIBERO-Pro` on Hugging Face), LIBERO's official demonstrations
(`yifengzhu-hf/LIBERO-datasets` on Hugging Face), [robosuite](https://robosuite.ai) and
[MuJoCo](https://mujoco.org). `dependencies.lock` pins every version. The LIBERO and
LIBERO-PRO sources, data and demonstrations are used under their own licenses.

## License

RAX's original source code and accompanying documentation are licensed under the
[Apache License 2.0](LICENSE). Copyright 2026 RAX contributors.

Third-party code, simulator software, assets, model weights and datasets retain their
own licenses. See [NOTICE](NOTICE) and the relevant upstream releases.

## Citation

If you find RAX useful, please cite:

```bibtex
@misc{huang2026scaling,
  title        = {Scaling Online Exploration for Robot Agents},
  author       = {Huang, Runhan and Zhang, Xiangcheng and Chen, Haoyu
                  and Li, Yitong and Wang, Yen-Jen and Lyu, Bohan
                  and Ye, Haotian and Hong, Chuye and Wang, Yixuan
                  and Su, Chloe and Xiong, Haoyu and Wu, Yangzhen
                  and Xie, Enze and Manderson, Travis and Ermon, Stefano
                  and Abbeel, Pieter and Torralba, Antonio and Song, Dawn
                  and Ye, Weirui and Du, Yilun},
  year         = {2026},
  howpublished = {Preprint},
  url          = {https://robot-agent-exploration.com/}
}
```
