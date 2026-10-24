"""Benchmarks are parallel backends: shared code dispatches through the registry, so a new
benchmark needs a registry entry and an adapter, and never falls back to another one."""
from pathlib import Path
import re
import sys
import types

import pytest

from services import benchmarks
from services.benchmarks import BENCHMARKS, HOOKS, Benchmark
from bench import TASK

PROJECT = Path(__file__).resolve().parents[2]
# Shared code: nothing here may compare benchmark names.
SHARED = ('services/controller', 'services/dashboard', 'services/workspace', 'services/exploration', 'services/agent_formal',
          'services/mcp_contract.py', 'services/scoring.py', 'services/simulator', 'harness',
          'scripts/configure_auto_research.py', 'scripts/render_workspaces.py')
NAMES = '|'.join(map(re.escape, BENCHMARKS))
NAME_CHECK = re.compile(rf"""(==|!=|\bin)\s*\(?\s*['"]({NAMES})['"]|['"]({NAMES})['"]\s*(==|!=)""")


def test_shared_code_never_compares_benchmark_names():
    hits = []
    for entry in SHARED:
        path = PROJECT/entry
        for source in ([path] if path.is_file() else sorted(path.rglob('*'))):
            if source.suffix not in ('.py', '.js') or '__pycache__' in source.parts:
                continue
            for number, line in enumerate(source.read_text().splitlines(), 1):
                if NAME_CHECK.search(line):
                    hits.append(f'{source.relative_to(PROJECT)}:{number}: {line.strip()}')
    assert not hits, 'Dispatch through services.benchmarks instead:\n' + '\n'.join(hits)


@pytest.mark.parametrize('name', sorted(BENCHMARKS))
def test_every_adapter_implements_every_hook(name):
    import importlib
    adapter = importlib.import_module(BENCHMARKS[name].adapter)
    assert not {hook for hook in HOOKS | {'tool_definitions'} if not callable(getattr(adapter, hook, None))}
    assert BENCHMARKS[name].robot['benchmark'] == BENCHMARKS[name].label
    # Episode reviews are a setup field only where the simulator has the ground truth.
    assert BENCHMARKS[name].episode_reviews != ('episode_reviews' in BENCHMARKS[name].fixed)


def test_planner_files_are_each_benchmarks_own():
    for name, entry in BENCHMARKS.items():
        assert entry.planner_files and all((PROJECT/path).is_file() for path in entry.planner_files)
        assert not [path for path in entry.planner_files
                    if any(path.startswith(f'services/{other}/') for other in BENCHMARKS if other != name)]


def test_default_benchmarks_setup_defaults_are_the_dashboard_defaults():
    from services.benchmarks import DEFAULT
    from services.dashboard.sessions import DEFAULTS
    defaults = BENCHMARKS[DEFAULT].setup_defaults
    assert defaults == {k: DEFAULTS[k] for k in defaults}


@pytest.fixture
def fake(monkeypatch):
    """A third benchmark with its own catalog, rules and setup fields."""
    calls = []
    adapter = types.ModuleType('fake_benchmark_adapter')
    adapter.tool_definitions = lambda: [{'name': 'fake_status'}, {'name': 'fake_observe'}, {'name': 'other'}]
    adapter.backend = lambda config: ('fake-backend', config)
    adapter.task_names = lambda: ['fake_task']
    adapter.task_timeouts = lambda task: {'episode_seconds': 123, 'native_step_limit': 7}
    adapter.validate_config = lambda config: calls.append(('config', config.task))
    adapter.validate_settings = lambda values: calls.append(('settings', values['task']))
    adapter.validate_layouts = lambda config: calls.append(('layouts', config.task))
    adapter.exploration_seeds = lambda task, collection, start, episodes, **_: tuple(range(start, start+episodes))
    adapter.task_context = lambda config, demonstration, *, live_formal: '# Fake task\n'
    adapter.refresh_workspace = lambda config, workspace: calls.append(('refresh', workspace))
    adapter.fetch_demonstration = lambda root, task, kind: calls.append(('fetch', Path(root).name))
    adapter.provision_demonstration = lambda config, target, root: {'kind': 'none', 'image_count': 0}
    adapter.episode_reviewer = lambda root: None
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)
    bench = Benchmark(
        name='fake', label='FakeSim', adapter=adapter.__name__,
        robot_tools=frozenset({'fake_status', 'fake_observe'}), geometry_tools=frozenset(),
        unscoped_tools=frozenset(), mutating=frozenset(), previewable=frozenset(),
        status_tool='fake_status', observe_tool='fake_observe', tool_prefixes=('fake_',),
        episode_tool_prefixes=('fake_',), simulator_modules=('services.fake.server',), startup_seconds=60,
        robot={'benchmark': 'FakeSim', 'cameras': ('cam_front',)}, vocabulary=r'\bfakesim\b',
        setup_defaults={'formal_episodes': 3}, cli_defaults={'formal_workers': 1},
        fixed={'observation_profile': ('rgbd', 'FakeSim has a depth camera.')})
    monkeypatch.setitem(BENCHMARKS, 'fake', bench)
    return bench, calls


def test_a_new_benchmark_is_dispatched_never_defaulted(fake, tmp_path):
    bench, calls = fake
    assert benchmarks.for_task('fake_task') is bench
    assert bench.backend('c') == ('fake-backend', 'c')
    assert [t['name'] for t in bench.definitions()] == ['fake_status', 'fake_observe']
    with pytest.raises(ValueError, match='Unknown task'):
        benchmarks.for_task('not_a_task')

    from services.controller.config import Configuration, Limits
    limits = Limits(60, 512, 1, 32, 32, 4 * 1024 * 1024)
    config = Configuration(tmp_path / 'controller-test', 'fake_task', (1, 2), 4, 0,
                           'GPU-aaaa', limits, limits, limits, benchmark='fake')
    from services.controller.batch import validate_layouts
    validate_layouts(config)
    from harness.codex_cli.auto_research import refresh_reviews
    refresh_reviews(config, tmp_path)
    assert calls == [('config', 'fake_task'), ('layouts', 'fake_task'), ('refresh', tmp_path)]

    from services.controller.demonstrations import provision_demonstration
    object.__setattr__(config, 'root', tmp_path)
    assert provision_demonstration(config, tmp_path/'ws', tmp_path/'demos')['kind'] == 'none'


def test_dashboard_setup_uses_the_benchmarks_own_fields(fake, tmp_path):
    bench, calls = fake
    from services.dashboard.sessions import DEFAULTS, ResearchSessions
    research = ResearchSessions(tmp_path, lambda: [TASK, 'fake_task'])
    values = research.settings({**DEFAULTS, 'task': 'fake_task', 'observation_profile': 'rgb-only',
                                'episode_reviews': 'on'})
    # Its fixed field is applied; other benchmarks' fixed fields are not.
    assert (values['benchmark'], values['observation_profile'], values['episode_reviews']) == ('fake', 'rgbd', 'on')
    assert ('settings', 'fake_task') in calls
    state = research.status()
    assert state['benchmarks']['fake_task'] == 'fake'
    assert state['benchmark_info']['fake'] == {'label': 'FakeSim', 'task_tag': '', 'score_label': 'Formal average score',
                                               'layout_note': ''}
    assert state['benchmark_defaults']['fake'] == {'formal_episodes': 3}


def test_workspaces_take_the_benchmarks_facts(fake, tmp_path):
    from services.mcp_contract import Contract
    from services.workspace import Setup
    from services.workspace.validate import foreign_terms
    context = Setup(Contract(benchmark='fake')).context()
    assert context['fake'] is True and not any(context[name] for name in BENCHMARKS if name != 'fake')
    assert 'policies' not in context and context['partial_credit'] is False
    assert context['robot'] == {'benchmark': 'FakeSim', 'cameras': ('cam_front',)}
    other = next(b for name, b in BENCHMARKS.items() if name != 'fake')
    (tmp_path/'AGENTS.md').write_text(f'Use {other.observe_tool}.\n')
    assert foreign_terms(tmp_path, 'fake') and not foreign_terms(tmp_path, other.name)
    (tmp_path/'AGENTS.md').write_text('A FakeSim note.\n')
    assert foreign_terms(tmp_path, other.name) and not foreign_terms(tmp_path, 'fake')


def test_configure_applies_cli_defaults_and_refuses_fixed_fields(fake, monkeypatch):
    sys.path.insert(0, str(PROJECT/'scripts'))
    try:
        import configure_auto_research as configure
    finally:
        sys.path.remove(str(PROJECT/'scripts'))
    import argparse
    parser = argparse.ArgumentParser()
    args = argparse.Namespace(benchmark='fake', formal_workers=4, observation_profile='rgbd')
    monkeypatch.setattr(sys, 'argv', ['configure', '--benchmark', 'fake'])
    configure.apply_benchmark(parser, args)
    assert (args.formal_workers, args.observation_profile) == (1, 'rgbd')
    monkeypatch.setattr(sys, 'argv', ['configure', '--observation-profile=rgb-only'])
    args.observation_profile = 'rgb-only'
    with pytest.raises(SystemExit):
        configure.apply_benchmark(parser, args)


@pytest.mark.parametrize('name', sorted(BENCHMARKS))
def test_cli_and_dashboard_share_each_benchmarks_setup_defaults(name, monkeypatch):
    sys.path.insert(0, str(PROJECT/'scripts'))
    try:
        import configure_auto_research as configure
    finally:
        sys.path.remove(str(PROJECT/'scripts'))
    from services.dashboard.sessions import DEFAULTS
    argv = ['configure', '--task', 't', '--sim-gpu', '0', '--benchmark', name]
    monkeypatch.setattr(sys, 'argv', argv)
    parser = configure.arguments()
    args = parser.parse_args(argv[1:])
    configure.apply_benchmark(parser, args)
    bench = BENCHMARKS[name]
    form = {**DEFAULTS, **bench.setup_defaults}  # What the dashboard form loads for this benchmark.
    for key in bench.setup_defaults:
        assert getattr(args, key) == form[key], key
    with pytest.raises(SystemExit):
        parser.parse_args([*argv[1:], '--formal-ep', '3'])  # Abbreviations would bypass apply_benchmark.
