"""Checks a built workspace must pass, whatever its role.

Each rule reads the rendered files (not the sources) and returns human-readable
problems. Rules are deliberately narrow: they catch contradictions that reviews
found in practice (broken links, text for another setup, tool names the session
does not have) rather than judging prose.
"""
import re
from pathlib import Path

from services.benchmarks import ALL_TOOL_PREFIXES, BENCHMARKS, DEFAULT, benchmark as benchmark_named

LINK = re.compile(r'\]\(([^)#\s]+)(?:#[^)]*)?\)')
FENCE = re.compile(r'```.*?```', re.S)
FRONTMATTER_NAME = re.compile(r'\A---\n(?:.*\n)*?name: *([^\n]+)\n(?:.*\n)*?---\n')
SKILL_NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
# MCP tools named in text; the session must expose them unless the text negates them.
TOOL = re.compile(r'`((?:' + '|'.join(re.escape(p) for p in ALL_TOOL_PREFIXES) + r')[a-z_]+|start_episode|evaluate|finish|register|rehearse|submit|gemini_generate)`')
NEGATION = re.compile(r'\b(no|not|never|without|nothing|unavailable|absent|removed|instead of)\b', re.I)


def prose(path):
    """Text outside fenced examples; each example leaves its line breaks, so reported line
    numbers stay those of the file."""
    return FENCE.sub(lambda example: '\n' * example.group(0).count('\n'), path.read_text())


def markdown(workspace):
    """Markdown files of the workspace itself: links inside it are not followed, but the
    workspace path may lead through links (an agent-storage mount, a linked TMPDIR)."""
    root = Path(workspace)
    return sorted(p for p in root.rglob('*.md') if not p.is_symlink()
                  and not any(parent.is_symlink() for parent in p.parents if root in parent.parents))


def broken_links(workspace):
    problems = []
    for path in markdown(workspace):
        for target in LINK.findall(prose(path)):
            if target.startswith(('http:', 'https:', 'mailto:')):
                continue
            if not (path.parent / target).exists():
                problems.append(f'{path.relative_to(workspace)}: broken link {target}')
    return problems


def skill_names(workspace):
    """Skill folders must match their frontmatter name, which must be a valid skill name."""
    problems = []
    skills = Path(workspace) / '.agents/skills'
    for skill in sorted(skills.glob('*/SKILL.md')) if skills.is_dir() else ():
        folder = skill.parent.name
        match = FRONTMATTER_NAME.match(skill.read_text())
        name = match.group(1).strip() if match else None
        if name != folder:
            problems.append(f'{skill.relative_to(workspace)}: frontmatter name {name!r} != folder {folder!r}')
        if not SKILL_NAME.match(folder):
            problems.append(f'{skill.relative_to(workspace)}: skill name {folder!r} is not lowercase-hyphenated')
    return problems


def absent_skills(workspace, known):
    """Mentions of a skill that this workspace does not install."""
    problems = []
    present = {p.name for p in (Path(workspace) / '.agents/skills').iterdir()} if (Path(workspace) / '.agents/skills').is_dir() else set()
    for path in markdown(workspace):
        text = prose(path)
        for name in sorted(known - present):
            if re.search(rf'(?<![\w/-]){re.escape(name)}(?![\w-])', text):
                problems.append(f'{path.relative_to(workspace)}: mentions absent skill {name}')
    return problems


def tool_mentions(workspace, tools, allowed=frozenset()):
    """Backticked MCP tool names must exist in this session, unless the line negates them."""
    problems = []
    for path in markdown(workspace):
        for number, line in enumerate(prose(path).splitlines(), 1):
            for name in TOOL.findall(line):
                if name in tools or name in allowed or NEGATION.search(line):
                    continue
                problems.append(f'{path.relative_to(workspace)}:{number}: tool `{name}` is not in this session')
    return problems


CODEX_ONLY = ('functions.exec', 'view_image')


def cli_specific(workspace, cli):
    """Codex's functions.exec wrapper and view_image tool mean nothing to Claude Code."""
    if cli == 'codex':
        return []
    return [f'{path.relative_to(workspace)}: Codex-only `{name}` guidance'
            for path in markdown(workspace) for name in CODEX_ONLY if name in prose(path)]


def _lines(workspace):
    """(relative path, line number, line) of every prose line outside fenced examples."""
    for path in markdown(workspace):
        relative = path.relative_to(workspace)
        for number, line in enumerate(prose(path).splitlines(), 1):
            yield relative, number, line


def _forbidden(workspace, pattern, what, *, allow=lambda relative, line: False):
    return [f'{relative}:{number}: {what} {match.group(0)!r}'
            for relative, number, line in _lines(workspace)
            if not allow(relative, line) and (match := pattern.search(line))]


# With one environment there is nothing to select or run in parallel.
ONE_ENVIRONMENT = re.compile(r'env_id|session\.env\(|\.env\(i\)|environment selection|selected environment|'
                             r'\bone (exploration )?environment\b|single exploration environment|'
                             r'single-environment|\bselector\b|python workers?|wait for other workers', re.I)


def single_environment(workspace, environments):
    """With one environment: no env_id, selection, selectors or parallel workers."""
    if environments != 1:
        return []
    return _forbidden(workspace, ONE_ENVIRONMENT, 'one-environment session mentions')


SUBAGENTS = re.compile(r'\bsub-?agents?\b|\bworkers?\b', re.I)


def no_subagents(workspace, subagents):
    """Without subagents, nothing about delegating to them."""
    return [] if subagents else _forbidden(workspace, SUBAGENTS, 'no-subagent session mentions')


DEMONSTRATION = re.compile(r'\bdemo(s|nstrat\w*)?\b', re.I)


def no_demonstrations(workspace, demonstrations):
    """Without demonstrations, nothing mentions them, not even to say there are none."""
    if demonstrations:
        return []
    return _forbidden(workspace, DEMONSTRATION, 'mentions absent demonstrations:')


# A terminal-state demonstration has no video or image sequence to follow.
SEQUENCE_DEMONSTRATION = re.compile(r'demo(nstration)? video|image sequence|completion[_ -]sequence|'
                                    r'\bfor a sequence\b', re.I)


def terminal_state_only(workspace, kind):
    """With a terminal-state demonstration, nothing describes a sequence one."""
    if kind != 'terminal_state':
        return []
    return _forbidden(workspace, SEQUENCE_DEMONSTRATION, 'terminal-state workspace describes a sequence:')


# Files and fields of the complete expert recordings (expert_content 'full').
EXPERT_RECORDING = re.compile(r'trajectory\.jsonl|trajectory-index|episode\.npz|raw-array-catalog|'
                              r'\bvla_(action|state)|\benv_obj_|\bhand_action_|\brobot_root_')


def camera_only_experts(workspace, camera_only):
    """With camera-only expert episodes, nothing names the complete recordings' files or fields."""
    if not camera_only:
        return []
    return _forbidden(workspace, EXPERT_RECORDING, 'camera-only experts, names a recording field:')


def foreign_terms(workspace, benchmark):
    """A workspace never mentions another benchmark's robot, cameras, tools or skills."""
    own = benchmark_named(benchmark)
    return [problem for other in BENCHMARKS.values() if other is not own and other.vocabulary
            for problem in _forbidden(workspace, re.compile(other.vocabulary, re.I),
                                      f'{own.label} workspace mentions {other.label}:')]


# Live-copy mode has no bundle evaluation and frames nothing as an evaluation: no bundles,
# submission, rehearsal, formal batch, held-out layouts or scores. The `evaluate` tool is
# exploration's own success check and may be named. Benchmarks scored with partial credit
# state it in their workspaces and ask the agent to maximize it.
EVALUATION_TERMS = (r'\bbundl\w*|\bregist(ered|ering|ration)\b|\bsubmi(t|ts|tted|tting|ssion)\b|\brehears\w*|\bformal\b|held-out|'
                    r'\w*evaluat\w*')
SCORE_TERMS = r'|\bscor(e|es|ed|ing)\b'
LIVE_COPY_TERMS = {False: re.compile(EVALUATION_TERMS + SCORE_TERMS, re.I), True: re.compile(EVALUATION_TERMS, re.I)}
EVALUATE_TOOL = re.compile(r'`evaluate(\([^`]*\))?`')


def live_copy_terms(workspace, live_copy, benchmark=DEFAULT):
    """An exploring agent in live-copy mode reads its work as practice, never as an evaluation."""
    if not live_copy:
        return []
    terms = LIVE_COPY_TERMS[benchmark_named(benchmark).partial_credit]
    return [f'{relative}:{number}: live-copy workspace mentions {match.group(0)!r}'
            for relative, number, line in _lines(workspace)
            if (match := terms.search(EVALUATE_TOOL.sub('', line)))]


# A formal copy plays one episode: no exploration or bundle machinery, even negated, and
# no evaluation framing either.
FORMAL_COPY_TERMS = re.compile(r'`(start_episode|evaluate|finish|register|rehearse|submit)`|\bstart_episode\b|'
                               r'\bregist(er|ers|ered|ration)\b|exploration episodes?|later reuse|later attempts?|'
                               + EVALUATION_TERMS, re.I)
FORMAL_COPY_SCORE_TERMS = re.compile(SCORE_TERMS[1:], re.I)


def formal_copy_terms(workspace, formal_copy, benchmark=DEFAULT):
    if not formal_copy:
        return []
    return (_forbidden(workspace, FORMAL_COPY_TERMS, 'formal copy mentions')
            + ([] if benchmark_named(benchmark).partial_credit
               else _forbidden(workspace, FORMAL_COPY_SCORE_TERMS, 'formal copy mentions')))


def cli_files(workspace, cli):
    """Codex-only skill metadata has no reader in another CLI's workspace."""
    if cli == 'codex':
        return []
    root = Path(workspace)
    return [f'{path.relative_to(root)}: Codex-only skill metadata'
            for path in sorted(root.rglob('agents/openai.yaml'))
            if not any(parent.is_symlink() for parent in path.parents if root in parent.parents)]


# Words for the agent harness's own machinery, which the agent has no referent for.
JARGON = re.compile(r'\b(host|operator|relay|router|harness|trial|lifecycle|in this session|new sessions|'
                    r'transfer cap|supervisor|sandbox|upstream|developer|docker image|isolated|'
                    r'single-environment|selector|state restoration|not something the tools enforce|'
                    r'per native task step|session mcp contract|mcp contracts?)\b', re.I)
# The evaluations are "the formal batch" and "agent formal"; no other names.
EVALUATION_NAMES = re.compile(r'\b(bundle evaluation|bundle formal|held-out batch|formal submission|formal runs?|'
                              r'formal evaluation|formal outcomes?)\b', re.I)
# Task-domain nouns: instructions and skills describe the workflow, never one task.
TASK_WORDS = re.compile(r'\b(toasts?|toasters?|bread|levers?|mahjong|kong|xylophone|mugs?|toolbox|laptops?|'
                        r'headphones?|chargers?|dolls?|bottles?|dustbin|equations?|coins?|screws?|clothes|eggs?|'
                        r'vases?|cups?|bowls?|conveyor|radio|modem|popcorn|microwave|tripod|trash|trumpet|onions?|'
                        r'slices? of|tiles?)\b', re.I)
# Files that carry the task by design.
TASK_FILES = ('TASK.md',)


def wording(workspace):
    """Jargon and other evaluation names anywhere; task-specific words outside the task's own files."""
    problems = []
    for relative, number, line in _lines(workspace):
        task_file = relative.name in TASK_FILES or 'demonstrations' in relative.parts
        for pattern, what in ((JARGON, 'jargon'), (EVALUATION_NAMES, 'evaluation name')):
            if match := pattern.search(line):
                problems.append(f'{relative}:{number}: {what} {match.group(0)!r}')
        if not task_file and (match := TASK_WORDS.search(line)):
            problems.append(f'{relative}:{number}: task-specific word {match.group(0)!r}')
    return problems


def task_nouns(workspace, benchmark):
    """Skills describe the method and the robot; the benchmark's task nouns belong in TASK.md."""
    pattern = benchmark_named(benchmark).task_nouns
    if not pattern:
        return []
    return _forbidden(workspace, re.compile(pattern, re.I), 'skill names a task noun',
                      allow=lambda relative, line: '.agents' not in relative.parts)


# Published skills describe the interface and method without private experiment provenance.
EXPERIMENT_PROVENANCE = re.compile(r'\b(?:earlier|previous)\s+(?:agents|robots|controllers)\b|'
                                   r'\[(?:several|few|one) runs?\b|\bone lineage\b', re.I)


def experimental_provenance(workspace):
    return _forbidden(workspace, EXPERIMENT_PROVENANCE, 'private experiment provenance',
                      allow=lambda relative, line: '.agents' not in relative.parts)


def blank_runs(workspace):
    """Doubled blank lines outside examples usually mean an empty conditional left a gap."""
    problems = []
    for path in markdown(workspace):
        fenced, empty = False, 0
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith('```'):
                fenced, empty = not fenced, 0
                continue
            empty = empty + 1 if not fenced and not line.strip() else 0
            if empty == 2:
                problems.append(f'{path.relative_to(workspace)}:{number}: doubled blank line')
    return problems


def validate(workspace, *, tools, cli='codex', environments=1, subagents=0, benchmark=DEFAULT,
             demonstrations=False, demonstration_kind=None, expert_demonstrations=0, expert_content='full',
             formal_copy=False,
             live_copy=False, known_skills=frozenset(), allowed_mentions=frozenset()):
    """Every rule for one built workspace, given the setup facts it was built from.

    expert_demonstrations: complete expert episodes are mounted, which the text may describe
    as demonstrations even without demonstration images; expert_content 'images': they hold
    front-camera observations only."""
    workspace = Path(workspace)
    return (broken_links(workspace) + skill_names(workspace) + absent_skills(workspace, known_skills)
            + tool_mentions(workspace, tools, allowed_mentions) + cli_specific(workspace, cli)
            + cli_files(workspace, cli) + single_environment(workspace, environments)
            + no_subagents(workspace, subagents)
            + no_demonstrations(workspace, demonstrations or bool(expert_demonstrations))
            + terminal_state_only(workspace, demonstration_kind if demonstrations else None)
            + camera_only_experts(workspace, bool(expert_demonstrations) and expert_content == 'images')
            + foreign_terms(workspace, benchmark) + formal_copy_terms(workspace, formal_copy, benchmark)
            + live_copy_terms(workspace, live_copy, benchmark)
            + task_nouns(workspace, benchmark) + experimental_provenance(workspace)
            + blank_runs(workspace) + wording(workspace))
