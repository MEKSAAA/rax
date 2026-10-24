from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = PROJECT_ROOT / "auto_research_agent"


def test_agent_workspace_template_contains_only_agent_visible_surface():
    # Skills live in agent_skills/ and are installed per workspace by the generator.
    assert {path.name for path in AGENT_ROOT.iterdir() if path.name != "__pycache__"} == {
        "AGENTS.md", "FORMAL_AGENTS.md", "START_PROMPT.md", "api"}
    assert not (AGENT_ROOT / "runtime").exists()
    files = [path for path in AGENT_ROOT.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    assert files
    assert not [path for path in files if path.suffix in {".sh", ".toml"}]


def test_trusted_harness_is_outside_agent_workspace():
    assert (PROJECT_ROOT / "harness" / "codex_cli" / "auto_research.py").is_file()
    assert (PROJECT_ROOT / "harness" / "claude_cli" / "auto_research.py").is_file()
    assert (PROJECT_ROOT / "services" / "controller" / "frontend.py").is_file()
    assert not (AGENT_ROOT / "harness").exists()
    assert not (AGENT_ROOT / "mcp").exists()
    assert not (AGENT_ROOT / "docs").exists()
