"""Each composed agent workspace (profile x environments x options) carries its role's text.

Links, absent skills and tools, environment, subagent, live-copy and formal-copy wording
are checked by the workspace validator on every rendered case (test_workspace_validation).
"""
import re

import pytest

from services.mcp_contract import Contract, PROFILES
from services.workspace import Setup, build, recipe_for
from bench import PIXEL, TASK

# (environments, subagents, options): multi-env, single-env alone, single-env shared by
# subagents, agent formal exploration and an agent formal copy.
VARIANTS = ((1, 0, ()), (3, 0, ()), (1, 2, ()),
            (1, 0, ('agent-formal',)), (3, 0, ('agent-formal',)), (1, 0, ('formal-episode',)))
CASES = [(profile, *variant) for profile in PROFILES for variant in VARIANTS]


def compose(target, contract, options=()):
    options = frozenset(options)
    build(recipe_for(options), Setup(contract, options, TASK), target)


def prose(path):
    # Fenced blocks hold examples (sample tables, code), not live links or instructions.
    return re.sub(r'```.*?```', '', path.read_text(), flags=re.S)


@pytest.mark.parametrize('profile,envs,subagents,options', CASES)
def test_composed_workspace_has_only_its_own_capabilities(tmp_path, profile, envs, subagents, options):
    target = tmp_path/'workspace'
    formal = 'formal-episode' in options
    contract = Contract('isolated' if formal else 'exploration', observation_profile=profile,
                        environments=envs, subagents=subagents)
    compose(target, contract, options)
    assert not (target/'FORMAL_AGENTS.md').exists()
    assert (target/'START_PROMPT.md').exists() == (not formal)
    agents = (target/'AGENTS.md').read_text()
    assert ('Later you continue from what you' in agents) == ('agent-formal' in options)
    assert ('one live episode' in agents) == formal
    assert ('formal batch' in agents) == (not options)
    if formal:
        # A formal copy supervises its carried pipeline live; it has no bundle lifecycle.
        assert 'autonomous-control' in agents
        control = (target/'.agents/skills/autonomous-control/SKILL.md').read_text()
        assert 'Handle what the pipeline does not cover' in control
    skills = {path.name for path in (target/'.agents/skills').iterdir()}
    assert ('multi-agent-exploration' in skills) == bool(contract.subagent_cap)
    assert ('multi-env-exploration' in skills) == (envs > 1)
    session = (target/'MCP_SESSION.md').read_text()
    assert f'Observation profile: {profile}' in session
    if subagents:
        assert f'Up to {subagents} subagents' in session
        assert 'turn-taking' in (target/'.agents/skills/multi-agent-exploration/SKILL.md').read_text()
    if profile == 'rgb-only':
        for path in target.rglob('*.md'):
            assert not re.search(r'pixel_to_position|camera_parameters', prose(path)), path.relative_to(target)
    # RGB-only gets no calibration either: no camera model, table priors or triangulation helper.
    assert not list(target.rglob('CALIBRATION.md')) and not list(target.rglob('triangulation.py'))


@pytest.mark.parametrize('profile', PROFILES)
def test_formal_copy_workspace_uses_its_own_variants(tmp_path, profile):
    target = tmp_path/'workspace'
    compose(target, Contract('isolated', observation_profile=profile), {'formal-episode'})
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
    compose(target, Contract(), {'agent-formal'})
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
    formal = 'formal-episode' in options
    contract = Contract('isolated' if formal else 'exploration', observation_profile=profile, benchmark='libero_pro')
    build(recipe_for(frozenset(options)), Setup(contract, frozenset(options), task), target)
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
