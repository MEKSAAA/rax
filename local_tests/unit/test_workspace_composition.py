"""Every composed agent workspace (profile x environments x options) is self-consistent."""
import re
import shutil
from pathlib import Path

import pytest

from services.mcp_contract import Contract, PROFILES
from services.mcp_workspace import compose
from bench import PIXEL, TASK

PROJECT = Path(__file__).resolve().parents[2]
LINK = re.compile(r'\]\(([^)#\s]+)(?:#[^)]*)?\)')
TEMPLATE = PROJECT/'auto_research_agent'
# (environments, subagents, options): multi-env, single-env alone, single-env shared by
# subagents, agent formal exploration and an agent formal copy.
VARIANTS = ((1, 0, ()), (3, 0, ()), (1, 2, ()),
            (1, 0, ('agent-formal',)), (3, 0, ('agent-formal',)), (1, 0, ('formal-episode',)))
CASES = [(profile, *variant) for profile in PROFILES for variant in VARIANTS]
# Lifecycle tools, not ViT 'register tokens' vocabulary.
LIFECYCLE = re.compile(r'(?<![-\w])(start_episode|register|rehearse|submit)\b(?!\s+(?:tokens|variants))|`evaluate`|`finish`')


def prose(path):
    # Fenced blocks hold examples (sample tables, code), not live links or instructions.
    return re.sub(r'```.*?```', '', path.read_text(), flags=re.S)


@pytest.mark.parametrize('profile,envs,subagents,options', CASES)
def test_composed_workspace_has_only_its_own_capabilities(tmp_path, profile, envs, subagents, options):
    target = tmp_path/'workspace'
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    formal = 'formal-episode' in options
    contract = Contract('isolated' if formal else 'exploration', observation_profile=profile,
                        environments=envs, subagents=subagents)
    compose(target, contract, task=TASK, options=set(options))
    assert not (target/'FORMAL_AGENTS.md').exists()
    assert (target/'START_PROMPT.md').exists() == (not formal)
    agents = (target/'AGENTS.md').read_text()
    assert ('Later you continue from what you' in agents) == ('agent-formal' in options)
    assert ('one live episode' in agents) == formal
    assert ('formal batch' in agents) == (not options)
    if 'agent-formal' in options:
        # Live-copy mode: no bundle tools, and nothing names them.
        assert not re.search(r'`(register|rehearse|submit)`', (target/'MCP_SESSION.md').read_text())
        for path in target.rglob('*.md'):
            assert not re.search(r'`(register|rehearse|submit)`|\bbundle', prose(path)), path.relative_to(target)
    if formal:
        # A formal copy supervises its carried pipeline live; it has no bundle lifecycle.
        assert 'autonomous-control' in agents
        control = (target/'.agents/skills/autonomous-control/SKILL.md').read_text()
        assert 'Handle what the pipeline does not cover' in control
        assert not re.search(r'`(register|rehearse|submit)`', control)
    skills = {path.name for path in (target/'.agents/skills').iterdir()}
    removed = set()  # Both observation modes install one object-localization skill.
    if envs == 1:
        removed.add('multi-env-exploration')
    if not contract.subagent_cap:
        removed.add('multi-agent-exploration')
    assert ('multi-agent-exploration' in skills) == bool(contract.subagent_cap)
    if subagents:
        assert f'Up to {subagents} subagents' in (target/'MCP_SESSION.md').read_text()
        assert 'turn-taking' in (target/'.agents/skills/multi-agent-exploration/SKILL.md').read_text()
    assert not removed & skills
    for path in target.rglob('*.md'):
        raw, text, relative = path.read_text(), prose(path), path.relative_to(target)
        assert not re.search(r'<!-- (capability|otherwise|end-capability)', raw), relative
        for link in LINK.findall(text):
            if not link.startswith(('http:', 'https:', 'mailto:')):
                assert (path.parent/link).exists(), (relative, link)
        for name in removed:
            assert name not in text, (relative, name)
        if profile == 'rgb-only':
            assert not re.search(r'pixel_to_position|camera_parameters', text), relative
        if formal:
            # A formal copy may only be told these tools do not exist.
            for line in text.splitlines():
                assert not LIFECYCLE.search(line) or re.search(r'\bno\b|\bnot\b|cannot|nothing', line), (relative, line)
        if envs == 1:
            for line in text.splitlines():
                assert 'env_id' not in line or re.search(r'not pass .?env_id|no .?env_id|without .?env_id', line, re.I), (relative, line)
    assert not list(target.rglob('CALIBRATION.md')) and not list(target.rglob('triangulation.py'))
    assert f'Observation profile: {profile}' in (target/'MCP_SESSION.md').read_text()


@pytest.mark.parametrize('profile', PROFILES)
def test_formal_copy_workspace_uses_its_own_variants(tmp_path, profile):
    target = tmp_path/'workspace'
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    compose(target, Contract('isolated', observation_profile=profile), task=TASK,
            options={'formal-episode'})
    skills = target/'.agents/skills'
    assert not list(skills.rglob('*.formal-episode.md'))
    assert 'carried' in (skills/'experience-memory/SKILL.md').read_text()
    assert not (skills/'policies').exists()
    assert not (skills/'robot-interface/references/pose-records.md').exists()
    session = (target/'MCP_SESSION.md').read_text()
    assert 'Role: one live episode' in session and '`status`' in session and '`start_episode`' not in session
    agents = (target/'AGENTS.md').read_text()
    assert 'How the episode ends' in agents and 'task_complete' in agents
    assert '.agents/skills/object-localization/SKILL.md' in agents
    localization = (target/'.agents/skills/object-localization/SKILL.md').read_text()
    assert (PIXEL in localization) == (profile == 'rgbd')


def test_exploration_workspaces_drop_formal_variants(tmp_path):
    target = tmp_path/'workspace'
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    compose(target, Contract(), task=TASK, options={'agent-formal'})
    skills = target/'.agents/skills'
    assert not list(skills.rglob('*.formal-episode.md'))
    assert not (skills/'policies').exists()
    assert (skills/'robot-interface/references/pose-records.md').is_file()
    agents = (target/'AGENTS.md').read_text()
    assert 'Practise with your controller' in agents and 'status.next_step' in agents
    assert 'formal' not in agents.lower() and 'submit' not in agents
    session = (target/'MCP_SESSION.md').read_text()
    assert '`start_episode`' in session and '`submit`' not in session


METHOD_SKILLS = {'avoid-overfitting', 'change-discipline', 'engineer-the-controller', 'evidence-and-regression',
                 'keep-trying', 'learn-from-incidents', 'match-precision-to-error', 'measure-success',
                 'prioritize-by-funnel', 'rehearse-the-controller', 'spend-the-budget', 'verify-committal-actions'}
SHARED = {'autonomous-control', 'experience-memory', 'gemini', 'manual-robot-control'}


# The eye-in-hand camera is not a wrist camera by name, so no wrist-camera-inspection.
LIBERO_PRO_ROBOT_SKILLS = {'object-localization', 'robot-interface'}


def libero_workspace(tmp_path, *, profile='rgbd', options=(), task='libero_spatial_0'):
    from services.workspace import Setup, build, recipe_for
    target = tmp_path/'workspace'
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    formal = 'formal-episode' in options
    contract = Contract('isolated' if formal else 'exploration', observation_profile=profile, benchmark='libero_pro')
    build(recipe_for(frozenset(options)), Setup(contract, frozenset(options), task), target, copy_template=False)
    return target, {path.name for path in (target/'.agents/skills').iterdir()}


@pytest.mark.parametrize('options,expected', [
    ((), SHARED | LIBERO_PRO_ROBOT_SKILLS | METHOD_SKILLS),
    (('agent-formal',), SHARED | LIBERO_PRO_ROBOT_SKILLS | (METHOD_SKILLS - {'rehearse-the-controller'})
     | {'transfer-handoff'}),
    (('formal-episode',), SHARED | LIBERO_PRO_ROBOT_SKILLS | {'keep-trying', 'match-precision-to-error',
                                                              'measure-success', 'spend-the-budget',
                                                              'verify-committal-actions'}),
])
def test_libero_pro_skills_per_role(tmp_path, options, expected):
    target, skills = libero_workspace(tmp_path, options=options)
    assert skills == expected  # No planner, preview, expert or other robots' skills.
    text = '\n'.join(path.read_text() for path in target.rglob('*.md'))
    assert 'free-space-move' not in text and 'cuRobo' not in text and 'runtime/experts' not in text


def test_libero_pro_camera_facts(tmp_path):
    target, _ = libero_workspace(tmp_path, profile='rgb-only')
    text = '\n'.join(path.read_text() for path in target.rglob('*.md'))
    assert 'libero_pixel_to_position' not in text
    interface = (target/'.agents/skills/robot-interface/SKILL.md').read_text()
    assert '`agentview`' in interface and '`robot0_eye_in_hand`' in interface
