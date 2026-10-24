"""Agent documentation must be usable without internet; retain legal notices."""
from pathlib import Path
import re
import shutil

from services.mcp_contract import Contract
from services.mcp_workspace import compose
import pytest
from bench import TASK


def test_agent_authored_files_have_no_web_references():
    root = Path(__file__).resolve().parents[2]/'auto_research_agent'
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix not in {'.md', '.py', '.txt', '.yaml'}:
            continue
        if 'licenses' in path.relative_to(root).parts:
            continue  # Do not alter third-party license texts, including their URLs.
        assert not re.search(r'https?://|www\.', path.read_text()), path


def test_gemini_instruction_links_resolve_to_local_files(tmp_path):
    from services.mcp_workspace import install_skills
    install_skills(tmp_path)  # Links resolve in the installed layout.
    skill = tmp_path/'gemini'
    for document in skill.rglob('*.md'):
        for target in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            assert '://' not in target
            assert (document.parent/target.split('#', 1)[0]).is_file(), (document, target)


@pytest.mark.parametrize('profile', ['rgbd', 'rgb-only'])
@pytest.mark.parametrize('count', [1, 3])
def test_stage_and_skill_entry_links_resolve(tmp_path, profile, count):
    project = Path(__file__).resolve().parents[2]
    root = tmp_path/'auto_research_agent'
    shutil.copytree(project/'auto_research_agent', root, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    compose(root, Contract(observation_profile=profile, environments=count), task=TASK)
    documents = [*root.glob('*.md'), *root.glob('.agents/skills/**/*.md')]
    for document in documents:
        # Templates contain example links to files the agent will create later.
        prose = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', document.read_text(), flags=re.M | re.S)
        for target in re.findall(r'\]\(([^)]+)\)', prose):
            assert '://' not in target, (document, target)
            relative = target.split('#', 1)[0]
            assert (document.parent/relative if relative else document).is_file(), (document, target)

