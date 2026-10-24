"""The same controller uses a bound exploration slot or an isolated context."""
import io
import json
from pathlib import Path
import re
import shutil

from services.mcp_contract import Contract
from services.mcp_workspace import compose, render

import pytest

from auto_research_agent.api.runtime import Context
from bench import OBSERVE, STEP, TASK


def context_with_replies(count):
    # Genuine stdlib client protocol, without a simulator or paid API request.
    result = {'content': [{'type': 'text', 'text': '{}'},
                          {'type': 'image', 'mimeType': 'image/png', 'data': 'png'}]}
    reader = io.StringIO(''.join(json.dumps({'id': i, 'result': result}) + '\n'
                                for i in range(1, count + 1)))
    writer = io.StringIO()
    return Context(reader, writer), writer, result


@pytest.mark.parametrize('slot', [None, 0, 3])
def test_same_controller_has_no_slot_or_transport_logic(slot):
    session, wire, expected = context_with_replies(4)

    def main(robot):
        observation = robot.call(OBSERVE)
        robot.call(STEP, actions=[[0.] * 14])
        robot.call('status')
        robot.call('gemini_generate', messages=[{'role': 'user', 'content': 'test'}])
        return observation

    # None is the single-environment context supplied by the isolated runner.
    assert main(session if slot is None else session.env(slot)) == expected
    calls = [json.loads(line)['params'] for line in wire.getvalue().splitlines()]
    for call in calls[:2]:
        if slot is None:
            assert 'env_id' not in call['arguments']
        else:
            assert call['arguments']['env_id'] == slot
    assert calls[1]['arguments']['actions'] == [[0.] * 14]
    assert all('env_id' not in call['arguments'] for call in calls[2:])


def test_bound_wrapper_routes_lifecycle_but_rejects_global_evaluation_and_rebinding():
    session, wire, _ = context_with_replies(3)
    robot = session.env(1)
    for name in ('start_episode', 'evaluate', 'finish'):
        robot.call(name)
    for name in ('register', 'rehearse', 'submit'):
        with pytest.raises(ValueError, match='session Context'):
            robot.call(name, bundle='frozen')
    with pytest.raises(ValueError, match='already bound'):
        robot.call(OBSERVE, env_id=0)
    for invalid in (True, -1, 1.0, '1'):
        with pytest.raises(ValueError):
            session.env(invalid)
    calls = [json.loads(line)['params'] for line in wire.getvalue().splitlines()]
    assert len(calls) == 3
    assert all(call['arguments'] == {'env_id': 1} for call in calls)


@pytest.mark.parametrize('count', [1, 3])
def test_portable_skill_entrypoint_example_executes_with_either_context(count):
    root = Path(__file__).resolve().parents[2]
    skill = root/'agent_skills/auto-research/autonomous-control/SKILL.md'
    examples = re.findall(r'```python\n(.*?)```', render(skill.read_text(),
        Contract(environments=count)), flags=re.S)
    namespace = {}
    exec(compile(examples[0], str(skill), 'exec'), namespace)
    for slot in (None, 1):
        context, wire, _ = context_with_replies(1)
        namespace['main'](context if slot is None else context.env(slot))
        params = json.loads(wire.getvalue())['params']
        assert params == {'name': OBSERVE,
                          'arguments': {} if slot is None else {'env_id': slot}}
    compile(examples[1], str(skill), 'exec')  # Development-only imports/paths.


@pytest.mark.parametrize('profile', ['rgbd', 'rgb-only'])
@pytest.mark.parametrize('count', [1, 3])
def test_workspace_entrypoints_and_skill_links_resolve(profile, count, tmp_path):
    source = Path(__file__).resolve().parents[2]/'auto_research_agent'
    root = tmp_path/'workspace'
    shutil.copytree(source, root, ignore=shutil.ignore_patterns('runtime', '__pycache__'))
    compose(root, Contract(observation_profile=profile, environments=count), task=TASK)
    paths = [root/'AGENTS.md', *root.glob('.agents/skills/**/*.md')]
    for path in paths:
        prose = re.sub(r'```.*?```', '', path.read_text(), flags=re.S)
        for target in re.findall(r'\]\(([^)]+)\)', prose):
            if '://' in target or target.startswith(('/', '#')):
                continue
            assert (path.parent/target.split('#')[0]).exists(), (path, target)
