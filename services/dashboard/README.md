# Operator dashboard

One local web page to prepare, launch, monitor, stop and resume sessions, and to review
finished ones. Every action runs the same terminal command described in
[Usage](../../docs/USAGE.md).

```bash
bash scripts/run_operator_dashboard.sh                 # http://127.0.0.1:8765
bash scripts/run_operator_dashboard.sh --port 8800 --open
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | `127.0.0.1`, `localhost` or `::1`. Other addresses are refused: the page is privileged |
| `--port` | 8765 | Port |
| `--open` | off | Open the URL in a browser |

The script prints a URL with a fresh access token (`#token=...`); open that URL. The
token is set as a cookie and changes on every start. Never give the URL to an agent.
On a remote machine, forward the port and open the printed URL locally:

```bash
ssh -L 8765:127.0.0.1:8765 <host>
```

The dashboard holds no session state. Stopping or restarting it stops no agent or
simulator; it needs a restart only to load a new version of its own code. A session
created before the dashboard started is actionable only if this dashboard has seen it
running; otherwise it is review-only, so resume it from the terminal.

## The page

- **New session:** the session settings, then **Prepare**, which runs
  `scripts/configure_auto_research.py` and starts nothing. The backend's defaults load
  into the form, and fields that do not apply are fixed, with the reason as a tooltip. The
  form remembers the last prepared settings (`runtime/operator/auto-research.json`).
  Preparing a Claude `claude-login` session first checks the experiment token.
- **Session:** stage, exploration used, manual success, formal success rate, live-copy
  results, agent tokens (main agent and subagents), Gemini spend, the
  agent CLI and provider, and subagent counts. Below them:
  - the terminal launch command, to copy;
  - **Launch:** runs the prepared configuration once, headless, after a confirmation;
  - **Resume:** relaunches a stopped, exited, failed or crashed session in its own
    conversation, headless;
  - **Stop:** signals the session's launcher, as `scripts/stop_auto_research_agent.sh`.
- **Live cameras:** the latest saved camera frames of the running episode. The dashboard
  reads saved files and never calls the robot.
- **Trajectory videos:** every simulator episode (exploration, rehearsal, formal and
  live copies) with the simulator's own recording, its steps and result.
- **Results:** program runs with their task outcome, reason, output and final images.
- **Logs and configuration:** the headless agent log, the MCP service log, and the
  configuration and raw state.

The session list shows sessions created after the dashboard started, plus older ones
that are still running. Tick **Earlier runs** (or open the URL with `#history=1`) to
review any saved session read-only; launch, resume and stop stay off there, and the
terminal commands still work.

## Code

| Module | Role |
| --- | --- |
| `server.py` | HTTP routes, token and cookie authentication, MP4 range streaming |
| `sessions.py` | Sessions under `runtime/auto-research/`: settings, prepare, launch, resume, stop, monitoring |
| `review.py` | Episode listing and video lookup |
| `observations.py` | Live-process checks for saved observations |
| `static/` | The page (`index.html`) and its script (`app.js`) |

Tests: `local_tests/unit/test_dashboard_server.py` and `test_dashboard_sessions.py`.
