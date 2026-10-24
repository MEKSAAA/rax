# Tests

`local_tests/unit/` holds the regression suite and `local_tests/goldens/` the reviewed
workspace renderings it compares against. Run everything from the repository root with
the host environment's Python.

## Unit suite

```bash
runtime/envs/host/bin/python -m pytest -m "not gpu" -q local_tests/unit
```

It uses fake simulators, fake robot backends and a local mock model provider. It makes no
paid model call, downloads nothing and needs no credentials, and it never starts MuJoCo:
`test_libero_pro_backend.py` imports every host module with libero, robosuite and MuJoCo
blocked, and checks that the simulator-side modules are valid Python 3.8.

The suite reads the task instructions from the LIBERO data tree, so it needs the pinned
checkout ([Setup, step 1](../docs/SETUP.md#1-sources)) and `runtime/libero-pro`
([Setup, step 3](../docs/SETUP.md#3-libero-pro-environment-and-data)). Without them, the
workspace rendering tests fail. Every test writes under pytest's temporary directory, which the suite sets as the
artifact root (`RAX_ARTIFACT_ROOT`) for its duration.

Some tests use more of the machine and skip, with the reason, when it is missing
(`-rs` lists the skips):

| Needs | Tests |
| --- | --- |
| The pinned LIBERO-PRO checkout and `runtime/libero-pro` | `test_libero_pro_backend.py`: the catalog against the official suite order, and every collection's BDDL and init states |
| bubblewrap, and `runtime/` under the host's artifact root | Real sandboxes: bundle isolation, offline Python, output limits and timeouts (`test_bundle_sandbox.py`), the agent's development Python (`test_development_python.py`), parallel formal workers (`test_parallel_submit.py`) |
| A `codex` binary on `PATH` | `test_auto_research_codex.py`: the real Codex CLI in its sandbox against a local fake model endpoint: hidden paths, no network or GPU, the robot tools, freezing during a rehearsal |
| A `claude` binary on `PATH`, and socat | `test_auto_research_claude.py`: real Claude Code, its sandbox and the robot tools against a local fake Messages endpoint |
| Node.js | A syntax check of the dashboard script |

When `RAX_ARTIFACT_ROOT` is set to a directory that does not hold `runtime/`, the
real-sandbox tests skip with `Writable runtime/ under the artifact root ... required`.

## Simulator check

The suite has no GPU tests. The real simulator is checked by the doctor, which starts the
official environment on one GPU for each task and collection and runs the simulator-side
checks; see [Setup, step 6](../docs/SETUP.md#6-check-the-installation):

```bash
runtime/envs/host/bin/python scripts/libero_pro_doctor.py --gpu 0 --task libero_spatial_0
```

## Workspace goldens

`test_workspace_goldens.py` renders every workspace setup and compares it with
`local_tests/goldens/workspaces.json`. After an intended change to agent instructions or
skills, review the rendered difference, then refresh the index:

```bash
runtime/envs/host/bin/python scripts/render_workspaces.py --out /tmp/before   # before the change
runtime/envs/host/bin/python scripts/render_workspaces.py --out /tmp/after    # after it
diff -ru /tmp/before /tmp/after
runtime/envs/host/bin/python scripts/render_workspaces.py --index local_tests/goldens/workspaces.json
```
