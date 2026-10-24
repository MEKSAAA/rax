"""Capabilities must agree across discovery, dispatch, files and workspace."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.mcp_contract import Contract
from services.workspace import Setup, build, recipe_for, render
from services.controller.gateway import Gateway
from services.controller.recording import RunRecording
from bench import BENCH, CAMERA, PIXEL, STEP, TASK


@pytest.mark.parametrize("profile", ["rgbd", "rgb-only"])
@pytest.mark.parametrize("count", [1, 2])
def test_contract_matrix(profile, count):
    c = Contract(observation_profile=profile, environments=count)
    definitions = c.robot_definitions()
    assert {t["name"] for t in definitions} == c.robot_names
    for t in definitions:
        expected = count > 1 and t["name"] not in BENCH.unscoped_tools
        assert ("env_id" in t["inputSchema"].get("properties", {})) == expected
        assert ("env_id" in t["inputSchema"].get("required", [])) == expected
    args = {"actions": [[0.0] * 14]}
    if count > 1:
        with pytest.raises(ValueError, match="env_id"):
            c.require_robot(STEP, args)
        c.require_robot(STEP, {**args, "env_id": count - 1})
        for bad in (True, -1, count, "0", None):
            with pytest.raises(ValueError, match="env_id"):
                c.require_robot(STEP, {**args, "env_id": bad})
    else:
        c.require_robot(STEP, args)
        with pytest.raises(ValueError, match="env_id"):
            c.require_robot(STEP, {**args, "env_id": 0})
    for name in BENCH.unscoped_tools:
        with pytest.raises(ValueError, match="env_id"):
            c.require_robot(name, {"env_id": 0})
    if profile == "rgb-only":
        with pytest.raises(PermissionError):
            c.require_robot(PIXEL, {})
    with pytest.raises(ValueError, match="Multiple environments"):
        Contract(phase="isolated", observation_profile=profile, environments=2)


def test_isolated_gateway_discovery_and_forged_calls():
    c = Contract(phase="isolated", observation_profile="rgb-only")
    calls = []
    backend = SimpleNamespace(
        tools=c.robot_definitions,
        call=lambda name, args: (calls.append((name, args)) or {"step_id": 1}, []))
    gateway = Gateway(backend, None, contract=c, audit=lambda *_: None,
                      charge=lambda *_: None, refund=lambda *_: None, budget=lambda: {})
    names = {t["name"] for t in gateway.definitions()}
    assert "gemini_generate" in names
    assert not {"start_episode", "rehearse", "submit", PIXEL} & names
    token = gateway.acquire()
    for name, args in [(PIXEL, {}),
                       (STEP, {"env_id": 0, "actions": [[0] * 14]}),
                       ("start_episode", {})]:
        response = gateway.handle(token, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}})
        assert "error" in response or response["result"].get("isError")
    assert calls == [] and not gateway.terminal
    for params in (None, [1], STEP):  # A malformed request is an error reply, not a crash.
        response = gateway.handle(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
        assert response["error"]["data"]["type"] == "TypeError"
    assert calls == []


def test_rgb_recording_filters_before_copying_depth(tmp_path):
    native = tmp_path / "native"
    folder = native / "runtime/frames/seq"
    folder.mkdir(parents=True)
    rgb = f"frame_000000_{CAMERA}_rgb.png"
    (folder / rgb).write_bytes(b"rgb")
    # Deliberately absent depth: publication must not even try to read it.
    manifest = {"frames": [{"camera_parameters": {"private": True}, "depth": {CAMERA: {}},
        "files": [{"kind": kind, "path": "runtime/frames/seq/" + name}
                  for kind, name in [("rgb", rgb), ("depth", f"frame_000000_{CAMERA}_depth.png")]]}]}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    recording = RunRecording(tmp_path / "published", "interactive-" + "a" * 32, observation_profile="rgb-only")
    value = {"frame_sequence": {"manifest_path": "runtime/frames/seq/manifest.json",
             "streams": [{"kind": "rgb"}, {"kind": "depth"}]}}
    published = recording.sequence(value, native)
    assert published["streams"] == [{"kind": "rgb"}]
    exported = next(recording.root.rglob("manifest.json"))
    assert "depth" not in exported.read_text()
    assert "camera_parameters" not in exported.read_text()
    assert len(list(recording.root.rglob("*.png"))) == 1


@pytest.mark.parametrize("profile", ["rgbd", "rgb-only"])
def test_workspace_composition_and_references(tmp_path, profile):
    c = Contract(observation_profile=profile)
    target = tmp_path / "workspace"
    build(recipe_for(), Setup(c, task=TASK), target)
    written = json.loads((target / "MCP_CONTRACT.json").read_text())
    assert {k: written[k] for k in c.manifest()} == c.manifest()
    assert written['tools'] == sorted(c.tool_names) and written['role'] == 'exploration'
    localization = target / ".agents/skills/object-localization"
    assert localization.exists()  # One localization skill per observation mode.
    assert (PIXEL in (localization / "SKILL.md").read_text()) == c.observation.geometry
    interface = (target / ".agents/skills/robot-interface/SKILL.md").read_text()
    assert STEP in interface
    for path in [target / "MCP_SESSION.md", target / ".agents/skills/manual-robot-control/SKILL.md"]:
        assert "object-localization/SKILL.md" in path.read_text()


def test_contracts_do_not_mutate_shared_schemas_or_each_other():
    baseline = BENCH.definitions()
    original = copy.deepcopy(baseline)
    a = Contract(observation_profile="rgb-only", environments=3)
    once = a.select(baseline)
    assert a.select(once) == once
    assert baseline == original
    assert PIXEL in Contract().robot_names
    with pytest.raises(ValueError, match="Unknown workspace template name"):
        render("{% if unknown %}\na\n{% endif %}\n", Setup(a))
    with pytest.raises(ValueError, match="markup survived"):
        render("{% raw %}{{ camera_geometry }}{% endraw %}\n", Setup(a))


def test_routing_is_pure_and_rejects_global_or_unavailable_tools():
    contract = Contract(observation_profile="rgb-only", environments=3)
    args = {"env_id": 2, "actions": [[0.0] * 14]}
    before = copy.deepcopy(args)
    index, payload = contract.route(STEP, args)
    assert index == 2 and payload == {"actions": args["actions"]} and args == before
    for name in ("gemini_generate", *BENCH.unscoped_tools, PIXEL, "submit"):
        with pytest.raises(PermissionError):
            contract.route(name, args)
    with pytest.raises(ValueError):
        Contract().route(STEP, args)
