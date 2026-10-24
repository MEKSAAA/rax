<div align="center">

<img src="docs/assets/rax-mark.svg" alt="RAX logo" width="84">

# RAX: Scaling Online Exploration for Robot Agents

[![arXiv](https://img.shields.io/badge/arXiv-2610.22496-b31b1b)](https://arxiv.org/abs/2610.22496) [![Paper](https://img.shields.io/badge/Paper-PDF-b31b1b)](https://arxiv.org/pdf/2610.22496) [![Project WebPage](https://img.shields.io/badge/Project-webpage-00a3a6)](https://robot-agent-exploration.com/) [![Videos](https://img.shields.io/badge/Robot-videos-blue)](https://robot-agent-exploration.com/#tasks) [![Skill Library](https://img.shields.io/badge/Skill-library-6f42c1)](https://github.com/robot-agent-exploration/skills-lib) [![Session Logs](https://img.shields.io/badge/%F0%9F%A4%97%20Session-logs-ffd21e)](https://huggingface.co/datasets/robot-agent-exploration/robot-session-logs)

</div>

> [RAX: Scaling Online Exploration for Robot Agents](https://arxiv.org/abs/2610.22496) \
> [Runhan Huang](https://hrh6666.github.io)<sup>1\*</sup>, [Xiangcheng Zhang](https://scholar.google.com/citations?user=pBnAahcAAAAJ)<sup>1\*†</sup>, [Haoyu Chen](https://tonychy.com)<sup>1‡</sup>, [Yitong Li](https://scholar.google.com/citations?user=xfp2Zv8AAAAJ)<sup>2‡</sup>, [Yen-Jen Wang](https://wangyenjen.github.io)<sup>3‡</sup>, [Bohan Lyu](https://bohanlyu.com)<sup>3‡</sup>, [Haotian Ye](https://haotianye.com)<sup>5</sup>, [Chuye Hong](https://chuye03.github.io)<sup>3</sup>, [Yixuan Wang](https://wangyixuan12.github.io)<sup>2</sup>, [Chloe Su](https://hither1.github.io)<sup>1</sup>, [Haoyu Xiong](https://haoyu-x.github.io)<sup>4</sup>, [Yangzhen Wu](https://thu-wyz.github.io)<sup>3</sup>, [Enze Xie](https://xieenze.github.io)<sup>2</sup>, [Travis Manderson](https://people.csail.mit.edu/travis/)<sup>4</sup>, [Stefano Ermon](https://cs.stanford.edu/~ermon/)<sup>5</sup>, [Pieter Abbeel](https://people.eecs.berkeley.edu/~pabbeel/)<sup>3</sup>, [Antonio Torralba](https://web.mit.edu/torralba/www/)<sup>4</sup>, [Dawn Song](https://dawnsong.io)<sup>3</sup>, [Weirui Ye](https://yewr.github.io)<sup>4</sup>, [Yilun Du](https://yilundu.github.io)<sup>1</sup> \
> <sup>1</sup>Harvard University &nbsp; <sup>2</sup>NVIDIA &nbsp; <sup>3</sup>UC Berkeley &nbsp; <sup>4</sup>MIT &nbsp; <sup>5</sup>Stanford University \
> <sup>\*</sup>Equal contribution &nbsp; <sup>†</sup>Project lead &nbsp; <sup>‡</sup>Core contributor

## 📰 News

- **[2026-10-24]** 🌿 **LIBERO and LIBERO-PRO branch released**, as a container and a native branch,
  with Codex CLI, Claude Code, DeepSeek Harness and Kimi Code. See [Branches](#-branches).
- **[2026-10-21]** 🔥 **DeepSeek and Kimi are here, and you can serve the model yourself!**
  The simulation branches now run **DeepSeek Harness** and **Kimi Code** alongside Codex
  CLI and Claude Code, natively or in a container. **Try local serving:** three commands
  prepare, serve and validate **DeepSeek-V4.1-Flash** with vLLM on one node with four
  H200s, and Claude Code or DeepSeek Harness then drives the robot through your own
  endpoint, no hosted API needed. Kimi Code runs through OpenRouter, the Moonshot API, a
  Kimi account or any compatible endpoint. 👉 [Self-host DeepSeek](docs/DEEPSEEK_SERVING.md) ·
  [Agent harnesses](docs/AGENT_HARNESSES.md) ·
  [DeepSeek model results](https://github.com/robot-agent-exploration/rax/blob/dexjoco/native/docs/DEEPSEEK_MODEL_RESULTS.md)
- **[2026-10-17]** 🌿 **HumanoidArena and DexJoCo branches released**, each as a container and
  a native branch. See [Branches](#-branches).
- **[2026-10-16]** 🗂️ **All real-robot sessions released on Hugging Face.**
  [robot-session-logs](https://huggingface.co/datasets/robot-agent-exploration/robot-session-logs)
  holds every Franka and RB-Y1 session: the agent's full transcripts, every tool call,
  robot state, camera recordings and the code and skills it wrote. See [Session logs](#%EF%B8%8F-session-logs).
- **[2026-10-15]** 📄 **The paper is on arXiv:** [2610.22496](https://arxiv.org/abs/2610.22496).
- **[2026-10-13]** 🌿 **RoboDojo, BEHAVIOR-1K and real-robot branches released:** simulation as
  a container and a native branch, and the real Franka FR3 and Rainbow RB-Y1 harnesses.

## 🌿 Branches

> [!IMPORTANT]
> **The code lives on the branches below.** `main` is an index; switch to a branch for the
> harness, setup guide and usage guide of that setting. Each branch is self-contained.

| Branch | Setting | Contents | Status |
| --- | --- | --- | --- |
| [`robodojo-behavior/container`](https://github.com/robot-agent-exploration/rax/tree/robodojo-behavior/container) | 🖥️ Sim | RoboDojo and BEHAVIOR-1K (Isaac Sim 5.1). The agent runs in an offline Docker container; formal evaluation uses held-out layouts. | ✅ available |
| [`robodojo-behavior/native`](https://github.com/robot-agent-exploration/rax/tree/robodojo-behavior/native) | 🖥️ Sim | The same benchmarks with the agent CLIs running natively, sandboxed by the CLI itself. | ✅ available |
| [`humanoid-arena/container`](https://github.com/robot-agent-exploration/rax/tree/humanoid-arena/container) | 🖥️ Sim | HumanoidArena (Isaac Sim 6.1): Unitree G1 with Dex3 hands under the frozen SONIC whole-body controller. The agent runs in an offline Docker container; formal evaluation uses the official test episodes. | ✅ available |
| [`humanoid-arena/native`](https://github.com/robot-agent-exploration/rax/tree/humanoid-arena/native) | 🖥️ Sim | HumanoidArena with the agent CLIs running natively, sandboxed by the CLI itself. | ✅ available |
| [`dexjoco/container`](https://github.com/robot-agent-exploration/rax/tree/dexjoco/container) | 🖥️ Sim | DexJoCo (MuJoCo): Franka arms with Allegro hands on one GPU. The agent runs in an offline Docker container; formal evaluation uses the official evaluation episodes. | ✅ available |
| [`dexjoco/native`](https://github.com/robot-agent-exploration/rax/tree/dexjoco/native) | 🖥️ Sim | DexJoCo with the agent CLIs running natively, sandboxed by the CLI itself. | ✅ available |
| [`libero-pro/container`](https://github.com/robot-agent-exploration/rax/tree/libero-pro/container) | 🖥️ Sim | LIBERO and LIBERO-PRO (robosuite, MuJoCo): a Franka Panda with a parallel gripper on the four LIBERO suites, on one GPU. The agent runs in an offline Docker container; formal evaluation uses LIBERO's official init states or a LIBERO-PRO perturbation of them. | ✅ available |
| [`libero-pro/native`](https://github.com/robot-agent-exploration/rax/tree/libero-pro/native) | 🖥️ Sim | LIBERO and LIBERO-PRO with the agent CLIs running natively, sandboxed by the CLI itself. | ✅ available |
| [`real/franka`](https://github.com/robot-agent-exploration/rax/tree/real/franka) | 🦾 Real | Franka FR3: one 7-DoF arm, Franka Hand, three ZED cameras. | ✅ available |
| [`real/rainbow`](https://github.com/robot-agent-exploration/rax/tree/real/rainbow) | 🦾 Real | Rainbow RB-Y1: wheeled base, 6-joint torso, two 7-DoF arms, grippers, head ZED 2i. | ✅ available |


---

**RAX** (**R**obot **A**gent e**X**ploration) is an open-source framework for robot agents
built around *scaling exploration*. A general-purpose coding agent (Codex CLI or Claude
Code, with a frozen model) drives a robot through perception and control tools, learns the
task from its own online experience, and compiles what worked into a modular robot program
that runs without the agent.

- 🚫 **No demonstrations.** No action demonstration data, hand-engineered action
  primitives or value-function guidance.
- 📈 **Exploration scales.** Generalization to unseen environments improves consistently
  as online experience grows.
- ⚡ **Exploration compiles into code.** The program the agent writes runs up to
  **241× faster** than the agent itself.
- 🤖 **One harness, every body.** Single-arm, bimanual, dexterous, mobile and humanoid
  robots, in simulation and on real hardware. New robots plug in through their API alone.

## 📊 Results

- 📈 **Scaling exploration.** Mean success on unseen scenes rises from **25%** to **79%** as the exploration budget grows from 4 to 128 episodes, against **41.2** for the best prior SOTA.
- 🖥️ **Simulation.** RAX beats the top baseline models by **+37.4%** on average.
- 🦾 **Real robots.** Learns memory-dependent, articulated, high-precision, deformable and long-horizon mobile manipulation tasks without human demonstrations.

**Exploration compiles into code.** The program RAX writes replaces the agent at run time:

| Task | Agent | Compiled program | Speed-up |
| --- | ---: | ---: | ---: |
| Air fryer | 72 min | 18 s | **241×** |
| Bandu tower | 17 min | 10 s | **103×** |

## 🚀 Getting started

| I want to... | Start here |
| --- | --- |
| Run the simulation benchmarks | [`robodojo-behavior/container`](https://github.com/robot-agent-exploration/rax/tree/robodojo-behavior/container) |
| Run the simulation benchmarks without Docker (e.g. on a cluster that does not allow containers) | [`robodojo-behavior/native`](https://github.com/robot-agent-exploration/rax/tree/robodojo-behavior/native) |
| Run the humanoid benchmarks | [`humanoid-arena/container`](https://github.com/robot-agent-exploration/rax/tree/humanoid-arena/container) or [`humanoid-arena/native`](https://github.com/robot-agent-exploration/rax/tree/humanoid-arena/native) |
| Run the dexterous-hand benchmarks | [`dexjoco/container`](https://github.com/robot-agent-exploration/rax/tree/dexjoco/container) or [`dexjoco/native`](https://github.com/robot-agent-exploration/rax/tree/dexjoco/native) |
| Run LIBERO or its LIBERO-PRO perturbations | [`libero-pro/container`](https://github.com/robot-agent-exploration/rax/tree/libero-pro/container) or [`libero-pro/native`](https://github.com/robot-agent-exploration/rax/tree/libero-pro/native) |
| Run RAX on a Franka or an RB-Y1 | [`real/franka`](https://github.com/robot-agent-exploration/rax/tree/real/franka) or [`real/rainbow`](https://github.com/robot-agent-exploration/rax/tree/real/rainbow) |
| Bring RAX to a new robot | [Real-robot adaptation guide](docs/REAL_ROBOT_ADAPTATION.md), then fork the closest real-robot branch |
| Port the agent skills to a new robot | [`skills/`](skills/README.md) |
| Reuse task skills agents learned on a real robot | [`skills-lib`](https://github.com/robot-agent-exploration/skills-lib) |

### Agent harnesses and model providers

The simulation branches support **Codex CLI, Claude Code, DeepSeek Harness
and Kimi Code** in both native and container execution. Choose the harness
that manages the agent's reasoning and tools, then connect it to OpenRouter,
a supported vendor account or API, or your own model endpoint.

| Guide | What you can do |
| --- | --- |
| [Agent harnesses](docs/AGENT_HARNESSES.md) | Compare backends, choose an authentication method and configure a session. |
| [Self-host DeepSeek](docs/DEEPSEEK_SERVING.md) | Deploy the pinned model on four H200s, tune CUDA Graphs and DSpark, and validate the endpoint. |
| [DeepSeek model results](https://github.com/robot-agent-exploration/rax/blob/dexjoco/native/docs/DEEPSEEK_MODEL_RESULTS.md) | Compare DexJoCo task scores, model configurations and API pricing. |

## 🧠 How RAX works

<p align="center">
  <img src="docs/assets/fig02_architecture.svg" alt="RAX method overview" width="100%">
</p>

1. **Explore.** The agent solves the task by hand from observations, calling the robot's
   tools one step at a time, and records what worked (preconditions, actions and effects)
   in its memory.
2. **Compile.** It turns the successful decision sequence into a program,
   `controller.py:main(ctx)`, made of step functions that check what they observe, and
   tests it unattended.
3. **Evaluate.** In simulation, the frozen program, or a live copy of the agent, plays
   held-out layouts it has never seen. On hardware, the operator resets the scene and the
   program runs end to end.

Everything the agent learns lives in its workspace: `TASK.md`, `AGENTS.md`, `skills/`,
`memory/` (notes, tips, verified skills) and `code/<project>/controller.py`, the compiled
program.

### System architecture

The harness (workspace generator, skills, launcher, sandbox, MCP frontend) never imports a
robot or simulator package. Each simulator or robot is a backend behind the same tool
interface, so one agent, one set of skills and one workflow run on every embodiment.

<table>
<tr><th>🖥️ Simulation</th><th>🦾 Real robot</th></tr>
<tr>
<td>

```
coding agent (Codex / Claude Code)
  in an offline Docker container
  ├─ MCP ─────────────┐
  └─ Bash / Python ───┴─► robot service
                          ├─ harness tools:
                          │   status, episode
                          │   control, Gemini
                          └─ backend
                              RoboDojo │ BEHAVIOR-1K
                              ├─ control · planner
                              └─ cameras
```

</td>
<td>

```
operator ── arm / disarm ──► <session>/operator/
                  (agent cannot write; read every tick)
coding agent (sandboxed CLI)
  ├─ MCP ─────────────┐
  └─ Bash / Python ───┴─► robot service (audit log)
                          ├─ harness tools:
                          │   status, operator, Gemini
                          └─ backend services/<robot>/
                              ├─ vendor control SDK
                              ├─ planner · URDF · cameras
                              └─ recorder (read-only)
```

</td>
</tr>
</table>

The robot service is the only process that commands the robot. On real hardware the
vendor stack stays untouched, safety is layered from a physical e-stop down to the vendor
controller's limits, and the agent's sandbox has no path to the robot except the MCP
tools. The [adaptation guide](docs/REAL_ROBOT_ADAPTATION.md) covers the full design.

## 🧩 Agent skills

[`skills/`](skills/README.md) collects the universal, robot-agnostic skills every branch
gives its agent: manual and autonomous control, experience memory, learning from
demonstrations, transfer handoff, continual learning, multi-env and multi-agent
exploration, VLM perception, camera inspection, object localization and the live
stream.

- 🔌 **Robot-agnostic.** Skills name tools by role; a [table](skills/README.md#tool-roles) maps each role to the tools
  of RoboDojo, BEHAVIOR-1K, Franka and RB-Y1.
- 🌿 **Reflects each branch.** A [matrix](skills/README.md#skills-on-each-branch) links every skill to its version on each
  branch and says [how simulation and real robots differ](skills/README.md#simulation-and-real-robots).
- 🦾 **Ready to port.** A [`robot-interface` template](skills/templates/robot-interface/SKILL.md)
  holds the facts a new robot fills in.

## 📚 Skill library

[**skills-lib**](https://github.com/robot-agent-exploration/skills-lib) is a companion
repository holding the **task skills agents wrote on real robots**. RAX is the general
harness; skills-lib is what its agents learned with it.

- 🦾 **One library per embodiment.** Each robot has its own branch:
  [`rby1`](https://github.com/robot-agent-exploration/skills-lib/tree/rby1) (box, bucket and
  chair moves, cup stacking, whole-body hand reach, a six-step room tidy) and
  [`franka`](https://github.com/robot-agent-exploration/skills-lib/tree/franka).
- 🧾 **Skills with evidence.** Every skill keeps its code, preconditions, checks, failure
  modes, run records and camera frames; it is `verified` only after two unattended
  successful runs.
- 🔁 **Grows across sessions.** The harness clones the robot's library into each
  session's workspace. Guided by the [`continual-learning`](skills/continual-learning/SKILL.md)
  skill, the agent reads it, runs or composes the skills that match, and merges back what
  it improves. Every new real robot should get one; the
  [adaptation guide](docs/REAL_ROBOT_ADAPTATION.md#a-skill-library-that-persists-across-sessions)
  shows how.

## 🗂️ Session logs

The raw logs of the real-robot sessions are on Hugging Face:
[**robot-session-logs**](https://huggingface.co/datasets/robot-agent-exploration/robot-session-logs).
It holds 5 Franka and 7 RB-Y1 sessions. Each one keeps the agent's full transcripts, every
robot tool call with its result, the robot state log and camera recordings, the operator's
instructions, and the code, notes and skills the agent wrote.

## 🦾 Bring RAX to your robot

A new robot needs a backend, not a dataset. The
[real-robot adaptation guide](docs/REAL_ROBOT_ADAPTATION.md) walks through it:

- **Five design principles:** wire the vendor stack without modifying it, measure every
  control mode before advising the agent, universal skills in a sandbox with a skill
  library that persists across sessions, observe on request and record continuously, and
  keep the harness separate from the robot.
- **The backend contract,** layered safety and the shared error shape.
- **The characterization protocol** and how to turn its numbers into skills.
- **A 12-step bring-up checklist,** from mapping the system to a dry agent session with
  a person at the e-stop.

## 🙏 Acknowledgements

The simulation branches build on [RoboDojo](https://github.com/RoboDojo-Benchmark/RoboDojo),
[BEHAVIOR-1K](https://github.com/StanfordVL/BEHAVIOR-1K),
[HumanoidArena](https://github.com/William-wAng618/HumanoidArena),
[SONIC](https://huggingface.co/nvidia/GEAR-SONIC),
[DexJoCo](https://github.com/brave-eai/dexjoco), [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO),
[LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO), [robosuite](https://robosuite.ai), [MuJoCo](https://mujoco.org),
NVIDIA Isaac Sim and Isaac Lab, and [cuRobo](https://github.com/NVlabs/curobo).
Each branch lists the exact versions it pins.

## 📄 License

RAX's original source code and accompanying documentation are licensed under the
[Apache License 2.0](LICENSE). Copyright 2026 RAX contributors.

Third-party code, simulator software, assets, model weights and datasets retain their
own licenses. See [NOTICE](NOTICE) and the relevant upstream releases.

## 📝 Citation

If you find RAX useful, please cite:

```bibtex
@article{huang2026scaling,
  title={Scaling Online Exploration for Robot Agents},
  author={Huang, Runhan and Zhang, Xiangcheng and Chen, Haoyu and Li, Yitong and Wang, Yen-Jen and Lyu, Bohan and Ye, Haotian and Hong, Chuye and Wang, Yixuan and Su, Chloe and others},
  journal={arXiv preprint arXiv:2610.22496},
  year={2026}
}
```
