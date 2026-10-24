"""Each workspace validator rule catches a violating sample (services/workspace/validate.py)."""
from pathlib import Path
import re

import pytest

from services.benchmarks import BENCHMARKS
from services.workspace import validate as rules

PROJECT = Path(__file__).resolve().parents[2]


def workspace(tmp_path, text, name='AGENTS.md'):
    path = tmp_path/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return tmp_path


@pytest.mark.parametrize('text', [
    'The host starts the episode.', 'The operator decides.', 'A relay forwards calls.', 'The router.',
    'The harness runs it.', 'This trial ends.', 'The lifecycle tools.', 'Only in this session.',
    'New sessions start fresh.', 'The supervisor stops it.', 'Inside the sandbox.', 'The upstream code.',
    'A developer-only script.', 'It is not in the Docker image.', 'Isolated runs.', 'A single-environment context.',
    'Without a selector.', 'No state restoration.', 'This is not something the tools enforce.',
    '6 seconds per native task step.', '# Session MCP contract',
    'Follow the MCP contract.', 'Act through the current MCP contracts.'])
def test_jargon(tmp_path, text):
    assert rules.wording(workspace(tmp_path, text + '\n'))


@pytest.mark.parametrize('text', ['The bundle evaluation runs.', 'Bundle formal.', 'The held-out batch.',
                                  'Before formal submission.', 'Formal runs read it.', 'The formal evaluation.',
                                  'Formal outcomes.'])
def test_other_evaluation_names(tmp_path, text):
    assert rules.wording(workspace(tmp_path, text + '\n'))


def test_the_formal_batch_and_agent_formal_are_the_names(tmp_path):
    text = 'The formal batch runs your code; agent formal copies play live. A formal episode ends.\n'
    assert not rules.wording(workspace(tmp_path, text))


def test_task_words_outside_task_files(tmp_path):
    assert rules.wording(workspace(tmp_path, 'Grasp the mug.\n', '.agents/skills/x/SKILL.md'))
    assert not rules.wording(workspace(tmp_path/'task', 'Grasp the mug.\n', 'TASK.md'))


@pytest.mark.parametrize('text', ['Pass `env_id`.', 'robot = session.env(1)', 'Use the selected environment.',
                                  'You have one exploration environment.', 'Use the single exploration environment.',
                                  'Use a separate Context per concurrent Python worker.',
                                  'Wait for other workers first.', 'The single-environment context.'])
def test_one_environment(tmp_path, text):
    root = workspace(tmp_path, text + '\n')
    assert rules.single_environment(root, 1)
    assert not rules.single_environment(root, 3)


def test_no_subagents(tmp_path):
    root = workspace(tmp_path, 'Spawn subagents; each worker reports.\n')
    assert rules.no_subagents(root, 0)
    assert not rules.no_subagents(root, 2)


def test_no_demonstrations(tmp_path):
    root = workspace(tmp_path, 'Read the demonstration first.\n', '.agents/skills/x/SKILL.md')
    assert rules.no_demonstrations(root, False)
    assert not rules.no_demonstrations(root, True)
    task = workspace(tmp_path/'task', 'Rubric.\n\nNo visual demonstration is supplied.\n', 'TASK.md')
    assert rules.no_demonstrations(task, False)
    assert not rules.no_demonstrations(workspace(tmp_path/'plain', 'Rubric.\n', 'TASK.md'), False)
    other = workspace(tmp_path/'other', 'Demo clarification: use the demos.\n', 'TASK.md')
    assert rules.no_demonstrations(other, False)


@pytest.mark.parametrize('text', ['If a demo video is provided, follow it.', 'Infer goals from the image sequence.',
                                  'Record `completion_sequence` evidence.', 'Preserve their order for a sequence.'])
def test_terminal_state_demonstration_describes_no_sequence(tmp_path, text):
    root = workspace(tmp_path, text + '\n', '.agents/skills/x/SKILL.md')
    assert rules.terminal_state_only(root, 'terminal_state')
    assert not rules.terminal_state_only(root, 'completion_sequence')
    assert not rules.terminal_state_only(root, None)


@pytest.mark.skipif(len(BENCHMARKS) < 2, reason='One benchmark: nothing is foreign')
def test_foreign_terms(tmp_path):
    own, other = list(BENCHMARKS.values())[:2]
    root = workspace(tmp_path, f'Call `{other.robot["step"]}`.\n')
    assert rules.foreign_terms(root, own.name)
    assert not rules.foreign_terms(root, other.name)


@pytest.mark.parametrize('text', ['You cannot call `submit`.', 'There is no `evaluate` here.',
                                  'Do not `finish`.', 'No `register` either.', 'Never `rehearse`.',
                                  'No start_episode.', 'A rehearsal covers it.', 'Before submission.',
                                  'After registration.', 'The exploration episode.', 'For later reuse.',
                                  'So a later attempt can reuse it.', 'A held-out layout.', '# The formal episode',
                                  'The result is your score.', 'It is scored as it stands.', 'Before evaluation.',
                                  'Your bundle.'])
def test_formal_copy_terms(tmp_path, text):
    root = workspace(tmp_path, text + '\n')
    assert rules.formal_copy_terms(root, True)
    assert not rules.formal_copy_terms(root, False)


@pytest.mark.parametrize('text', ['Register a bundle.', 'Then submit it.', 'Before submission.',
                                  'Rehearse it first.', 'Before registering it.', 'The rehearsal passes.', 'The formal batch runs.',
                                  'Copies play the held-out layouts.', 'Both evaluations follow.',
                                  'It is evaluated later.', 'Reevaluate after acting.', 'The evaluator decides.',
                                  'Your score is hidden.', 'Exploration is not scored.'])
def test_live_copy_terms(tmp_path, text):
    root = workspace(tmp_path, text + '\n')
    assert rules.live_copy_terms(root, True)
    assert not rules.live_copy_terms(root, False)


def test_live_copy_may_name_the_evaluate_tool(tmp_path):
    root = workspace(tmp_path, 'Confirm success with `evaluate` or `evaluate(env_id=i)`.\n')
    assert not rules.live_copy_terms(root, True)
    assert rules.validate(workspace(tmp_path/'v', 'Submit the bundle.\n'), tools=set(), live_copy=True)


def test_codex_skill_metadata_in_another_cli(tmp_path):
    root = workspace(tmp_path, 'interface: {}\n', '.agents/skills/x/agents/openai.yaml')
    assert rules.cli_files(root, 'claude')
    assert not rules.cli_files(root, 'codex')


@pytest.mark.parametrize('text, name', [('In `functions.exec`, forward images.\n', 'functions.exec'),
                                        ('Pass a Codex `view_image` result.\n', 'view_image')])
def test_codex_exec_guidance_in_another_cli(tmp_path, text, name):
    root = workspace(tmp_path, text)
    assert rules.cli_specific(root, 'claude') == [f'AGENTS.md: Codex-only `{name}` guidance']
    assert not rules.cli_specific(root, 'codex')


def test_absent_tool_unless_negated(tmp_path):
    root = workspace(tmp_path, 'Call `rehearse` now.\nThere is no `submit`.\n')
    assert [p for p in rules.tool_mentions(root, {'status'}) if 'rehearse' in p]
    assert not [p for p in rules.tool_mentions(root, {'status'}) if 'submit' in p]


def test_problems_after_an_example_report_their_own_line(tmp_path):
    root = workspace(tmp_path, 'Intro.\n```python\nctx.call("rehearse")\n```\nCall `rehearse` now.\n')
    assert rules.tool_mentions(root, set()) == ['AGENTS.md:5: tool `rehearse` is not in this session']


@pytest.mark.parametrize('text', ['Pick up the black bowl.', 'Open the top drawer.', 'Turn on the stove.',
                                  'Put the moka pot on the stove.', 'Place the ketchup in the basket.'])
def test_task_nouns_in_skills(tmp_path, text):
    skill = workspace(tmp_path, text + '\n', '.agents/skills/x/SKILL.md')
    assert rules.task_nouns(skill, 'libero_pro')
    # TASK.md names the task by design.
    assert not rules.task_nouns(workspace(tmp_path/'task', text + '\n', 'TASK.md'), 'libero_pro')


@pytest.mark.parametrize('text', ['Earlier agents measured this.', 'Previous controllers failed.',
                                  'Walking [few runs] works.', 'This comes from one lineage.'])
def test_skills_have_no_private_experiment_provenance(tmp_path, text):
    root = workspace(tmp_path, text + '\n', '.agents/skills/controller-field-guide/SKILL.md')
    assert rules.experimental_provenance(root)


def test_controller_guidance_needs_no_experiment_history(tmp_path):
    root = workspace(tmp_path, 'Measure tracking error before choosing a threshold.\n',
                     '.agents/skills/controller-field-guide/SKILL.md')
    assert not rules.experimental_provenance(root)
    notes = workspace(tmp_path/'notes', 'Earlier agents measured this.\n', 'memory/PROGRESS.md')
    assert not rules.experimental_provenance(notes)


def test_expert_episodes_allow_demonstration_wording(tmp_path):
    root = workspace(tmp_path, 'Read the expert demonstrations.\n')
    assert rules.validate(root, tools=set(), demonstrations=False)
    assert not rules.validate(root, tools=set(), demonstrations=False, expert_demonstrations=3)


@pytest.mark.parametrize('text', ['Read trajectory.jsonl.', 'The episode.npz arrays.', 'The vla_action_* frames.',
                                  'Its env_obj_hammer pose.', 'The hand_action_left targets.'])
def test_camera_only_experts_name_no_recording_field(tmp_path, text):
    root = workspace(tmp_path, text + '\n', '.agents/skills/learn-from-experts/SKILL.md')
    assert rules.camera_only_experts(root, True)
    assert not rules.camera_only_experts(root, False)
    assert not rules.camera_only_experts(workspace(tmp_path/'ok', 'Read front_rgb.mp4 and depth.npz.\n'), True)


def test_broken_link_and_blank_runs(tmp_path):
    root = workspace(tmp_path, 'See [x](missing.md).\n\n\nNext.\n')
    assert rules.broken_links(root) and rules.blank_runs(root)


def test_a_workspace_reached_through_a_link_is_checked_but_links_inside_it_are_not(tmp_path):
    storage = workspace(tmp_path/'storage', 'Call `rehearse` now.\n')
    workspace(tmp_path/'outside', 'Call `submit` now.\n')
    (storage/'linked').symlink_to(tmp_path/'outside')
    (tmp_path/'agent-workspace').symlink_to(storage)
    problems = rules.tool_mentions(tmp_path/'agent-workspace', set())
    assert problems == ['AGENTS.md:1: tool `rehearse` is not in this session']


def test_validate_passes_the_setup_facts(tmp_path):
    root = workspace(tmp_path, 'Spawn a subagent and read the demonstration.\n')
    assert rules.validate(root, tools=set(), subagents=0, demonstrations=False)
    assert not rules.validate(root, tools=set(), subagents=2, demonstrations=True)


# A Jinja tag that ends a text line swallows the newline after it (trim_blocks), joining
# that line to the next in the rendered text; such conditionals belong on their own lines.
INLINE_TAG_AT_END = re.compile(r'\S.*\{%[^%]*%\}\s*$')


def test_sources_never_end_a_text_line_with_a_tag():
    sources = [*(PROJECT/'auto_research_agent').glob('*.md'), *(PROJECT/'agent_skills').rglob('*.md'),
               *(PROJECT/'agent_blocks').rglob('*.md')]
    offending = [f'{path.relative_to(PROJECT)}:{number}' for path in sources
                 for number, line in enumerate(path.read_text().splitlines(), 1)
                 if INLINE_TAG_AT_END.match(line) and not line.lstrip().startswith('{%')]
    assert not offending, offending
