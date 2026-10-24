"""Operator launch controls never invoke robot lifecycle tools or bypass gates."""
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import signal
import sys
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import io
import numpy as np
from PIL import Image

import pytest

from services.controller.billing import NANODOLLARS
from services.dashboard.sessions import ResearchSessions, DEFAULTS, tail
from services.dashboard.server import Handler as DashboardHandler
from bench import BENCH, CAMERA, OTHER_TASK, TASK


@pytest.fixture
def dashboard(tmp_path):
    return ResearchSessions(tmp_path, lambda: [TASK, OTHER_TASK])


def test_new_sessions_use_formal_defaults(dashboard):
    research = dashboard.settings({})
    assert research['formal_episodes'] == DEFAULTS['formal_episodes']
    # Retired: episode budgets follow the task's native steps (scripts/configure_auto_research.py).
    assert 'formal_seconds' not in dashboard.settings({'formal_seconds': 2400})
    assert dashboard.settings({'formal_episodes': 12})['formal_episodes'] == 12
    for retired in ({'mode': 'teacher-student'}, {'student_model': 'openai/gpt-6-sol'}):
        with pytest.raises(ValueError, match='Unknown auto-research setup field'):
            dashboard.settings(retired)


def test_saved_legacy_defaults_migrate_without_mutating_prepared_runs(dashboard):
    dashboard.profile.parent.mkdir(parents=True)
    # Profiles saved while other modes existed carry mode and student fields; they are ignored.
    dashboard.profile.write_text(json.dumps({'formal_seconds':600, 'formal_episodes':50, 'mode':'auto-research',
                                             'student_model':'openai/gpt-6-sol'}))
    seconds = BENCH.task_timeouts(DEFAULTS['task'])['episode_seconds']
    current = dashboard.status()['profile']
    assert current['formal_episodes'] == 50
    assert not {'formal_seconds', 'mode', 'student_model'} & set(current)
    dashboard.profile.write_text(json.dumps({'formal_seconds':600, 'formal_episodes':30,
                                 '_timeout_defaults_version':1, '_formal_defaults_version':1}))
    assert dashboard.status()['profile']['formal_episodes'] == 30


def session(dashboard, ident='20260924T120000Z_0123456789'):
    root = dashboard.root/ident
    root.mkdir(parents=True)
    # Saved as older launches did: a retired mode field and the agent GPU fields.
    (root/'operator-input.json').write_text(json.dumps({'task':TASK, 'mode':'auto-research', 'exploration_seeds':[1, 2, 3],
        'sim_gpu':'GPU-aaaa', 'controller_gpu':'GPU-bbbb', 'training_gpu':'GPU-bbbb'}))
    return root


def test_sessions_saved_with_agent_gpus_still_list_and_open(dashboard):
    root = session(dashboard)
    listed = {row['run_id']: row for row in dashboard.status()['sessions']}[root.name]
    detail = dashboard.detail(root.name)
    for row in (listed, detail):
        assert row['sim_gpu'] == 'GPU-aaaa' and row['exploration_limit'] == 3
        assert 'research_gpu' not in row and row['stage'] == 'Not started'
    assert detail['configuration']['training_gpu'] == 'GPU-bbbb'  # The saved record is shown as saved.


def test_live_native_frames_are_read_only_and_pinned_to_step(dashboard):
    root = session(dashboard)
    sim = root/'native/results/native-run/sim'
    directory = sim/'episode/observations'
    directory.mkdir(parents=True)
    frame = directory/'000012.npz'
    colors = {camera: 30 + 70*index for index, camera in enumerate(BENCH.robot['cameras'][:3])}
    np.savez(frame, **{c: np.full((18, 24, 3), color, dtype=np.uint8) for c, color in colors.items()})
    state = {'episode_id': 'episode', 'step_id': 12, 'episode_mode': 'exploration',
        'episode_step_limit': 400, 'simulator_pid': -1, 'cameras': list(colors),
        'observation_file': 'episode/observations/000012.npz', 'updated_at_unix_s': 123}
    state_path = sim/'operator_state.json'
    state_path.write_text(json.dumps(state))
    before = {str(p): p.stat().st_mtime_ns for p in root.rglob('*')}
    live = dashboard.detail(root.name)['live_observation']
    assert live['available'] and not live['active'] and live['step_id'] == 12
    for camera, color in colors.items():
        raw = dashboard.frame(root.name, camera, 'native-run', 12)
        with Image.open(io.BytesIO(raw)) as image:
            assert image.format == 'JPEG' and image.size == (24, 18)
            assert abs(np.asarray(image).mean()-color) < 2
    assert before == {str(p): p.stat().st_mtime_ns for p in root.rglob('*')}
    for run, step in [('old-run', 12), ('native-run', 11)]:
        with pytest.raises(FileNotFoundError, match='changed'):
            dashboard.frame(root.name, CAMERA, run, step)
    state['observation_file'] = '../../secret.npz'
    state_path.write_text(json.dumps(state))
    assert not dashboard.live_observation(root)['available']
    with pytest.raises(ValueError):
        dashboard.frame(root.name, CAMERA, 'native-run', 12)
    state['observation_file'] = 'episode/observations/000012.npz'
    state_path.write_text(json.dumps(state))
    frame.unlink()
    frame.symlink_to(root/'operator-input.json')
    with pytest.raises(OSError):
        dashboard.frame(root.name, CAMERA, 'native-run', 12)


def test_live_metadata_switches_native_episode_at_reset(dashboard):
    root = session(dashboard)
    for index in range(2):
        sim = root/f'native/results/run{index}/sim'
        sim.mkdir(parents=True)
        (sim/'operator_state.json').write_text(json.dumps({'episode_id': f'ep{index}', 'step_id': 0}))
        import os
        os.utime(sim/'operator_state.json', (100+index, 100+index))
    live = dashboard.detail(root.name)['live_observation']
    assert live['native_run'] == 'run1' and live['episode_id'] == 'ep1'
    assert not live['available']


def test_settings_validate_before_any_command(dashboard):
    for edit in ({'episodes':True}, {'task':'bad'}, {'sim_gpu':'0;touch bad'}, {'sim_gpu':''},
                 {'episodes':1001}, {'formal_episodes':0},
                 {'exec_prompt':'anything'}, {'episodes':1},
                 {'demonstration_context':'custom_url'}):
        with pytest.raises(ValueError):
            dashboard.prepare({**DEFAULTS, **edit})
    assert not dashboard.root.exists()


def test_the_form_accepts_exactly_what_the_server_accepts():
    import re
    from services.dashboard.sessions import BOUNDS
    page = (Path(__file__).resolve().parents[2]/'services/dashboard/static/index.html').read_text()
    inputs = {name: (int(low), int(high)) for name, low, high in re.findall(
        r'<input name="(\w+)" type="number" min="(\d+)" max="(\d+)"', page)}
    assert inputs == {name: bounds for name, bounds in BOUNDS.items() if name in inputs}
    assert inputs['formal_workers'] == (1, 16)
    assert set(inputs) == {'episodes', 'exploration_envs', 'subagents', 'transfer_mb', 'formal_episodes',
                           'formal_workers'}  # The seeds have no maximum on the page.


def test_prepare_is_config_only_and_pins_selected_task(dashboard, monkeypatch):
    commands = []
    def configure(command, **kwargs):
        commands.append(command)
        root = session(dashboard)
        return SimpleNamespace(returncode=0, stdout=str(root/'operator-input.json')+'\n', stderr='')
    monkeypatch.setattr(subprocess, 'run', configure)
    result = dashboard.prepare({**DEFAULTS, 'task':OTHER_TASK, 'demonstration_context':'completion_sequence',
                                'development_seconds': 3600, 'training_seconds': 3600, 'research_gpu': '1'})
    assert commands[0][commands[0].index('--task')+1] == OTHER_TASK
    assert 'configure_auto_research.py' in commands[0][1]
    assert commands[0][commands[0].index('--demonstration-context')+1] == 'completion_sequence'
    assert '--development-seconds' not in commands[0]
    assert 'development_seconds' not in json.loads(dashboard.profile.read_text())
    assert '--training-seconds' not in commands[0]
    assert 'training_seconds' not in json.loads(dashboard.profile.read_text())
    # An older page still sends the retired research GPU; the agent has none.
    assert commands[0][commands[0].index('--sim-gpu')+1] == '0' and '--research-gpu' not in commands[0]
    assert 'research_gpu' not in json.loads(dashboard.profile.read_text())
    assert result['launchable'] and result['stage'] == 'Not started'
    assert '--config' in result['command']
    assert json.loads(dashboard.profile.read_text())['task'] == OTHER_TASK
    assert dashboard.status()['profile']['demonstration_context'] == 'completion_sequence'
    assert not list(dashboard.root.glob('*/launch-request.json'))


def test_configure_writes_only_the_simulator_gpu(tmp_path, monkeypatch, capsys):
    """The command the dashboard runs: the agent has no GPU, so only --sim-gpu exists."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
    try:
        import configure_auto_research as configure
    finally:
        sys.path.pop(0)
    def check_output(command, **kwargs):
        assert command[0] == 'nvidia-smi', command  # No image to inspect: agents run natively.
        return '0, GPU-aaaa\n1, GPU-bbbb\n'
    monkeypatch.setattr(configure.subprocess, 'check_output', check_output)
    monkeypatch.setattr(configure, 'PROJECT', tmp_path)
    monkeypatch.setenv('RAX_ARTIFACT_ROOT', str(tmp_path))
    base = ['configure', '--task', TASK, '--demonstration-context', 'none']
    monkeypatch.setattr(sys, 'argv', [*base, '--sim-gpu', '1', '--research-gpu', '0'])
    with pytest.raises(SystemExit):
        configure.main()
    assert 'unrecognized arguments: --research-gpu' in capsys.readouterr().err
    monkeypatch.setattr(sys, 'argv', [*base, '--sim-gpu', '1'])
    configure.main()
    path = Path(capsys.readouterr().out.strip())
    saved = json.loads(path.read_text())
    assert saved['sim_gpu'] == 'GPU-bbbb' and not {'controller_gpu', 'training_gpu'} & set(saved)
    from services.controller.config import Configuration
    assert Configuration.from_dict(saved).sim_gpu == 'GPU-bbbb'


@pytest.mark.skipif('adaptation_source' in BENCH.fixed, reason=f'{BENCH.label} has no adaptation')
def test_adaptation_source_is_saved_as_a_run_id_and_passed_as_its_path(dashboard, monkeypatch):
    source = session(dashboard, '20260920T000000Z_abcdef0123')
    (source/'state.json').write_text('{}')
    commands, prepared = [], iter(['20260924T120000Z_0123456781', '20260924T120000Z_0123456782'])
    def configure(command, **kwargs):
        commands.append(command)
        root = session(dashboard, next(prepared))
        return SimpleNamespace(returncode=0, stdout=str(root/'operator-input.json')+'\n', stderr='')
    monkeypatch.setattr(subprocess, 'run', configure)
    dashboard.prepare({'task': TASK, 'adaptation_source': source.name})
    assert commands[0][commands[0].index('--adaptation-source')+1] == str(source)
    profile = dashboard.status()['profile']
    assert profile['adaptation_source'] == source.name
    # The page posts the saved form back unchanged; it used to fail "Invalid auto-research session ID".
    dashboard.prepare({k: v for k, v in profile.items() if k != 'formal_seconds'})
    assert commands[1][commands[1].index('--adaptation-source')+1] == str(source)


def test_saved_profile_does_not_publish_retired_time_allowances(dashboard):
    dashboard.profile.parent.mkdir(parents=True, exist_ok=True)
    dashboard.profile.write_text(json.dumps({**DEFAULTS, 'development_seconds': 3600, 'training_seconds': 3600,
                                             'research_gpu': '1'}))
    assert 'development_seconds' not in dashboard.status()['profile']
    assert 'training_seconds' not in dashboard.status()['profile']
    assert 'research_gpu' not in dashboard.status()['profile']


def test_prepare_passes_default_count_to_launcher(dashboard, monkeypatch):
    commands = []
    def configure(command, **kwargs):
        commands.append(command)
        root = session(dashboard)
        return SimpleNamespace(returncode=0, stdout=str(root/'operator-input.json')+'\n', stderr='')
    monkeypatch.setattr(subprocess, 'run', configure)
    dashboard.prepare({})
    assert len(commands) == 1  # Preparation does not launch an experiment.
    command = commands[0]
    assert command[command.index('--formal-episodes')+1] == str(DEFAULTS['formal_episodes'])
    assert '--mode' not in command and not any(a.startswith('--student') for a in command)
    assert dashboard.status()['profile']['formal_episodes'] == DEFAULTS['formal_episodes']


def test_session_listing_respects_server_start_and_paths(dashboard, tmp_path):
    old = session(dashboard, '20260924T115959Z_0123456789')
    current = session(dashboard)
    dashboard.started_at = datetime(2026, 9, 24, 12, tzinfo=timezone.utc).timestamp()
    assert [r['run_id'] for r in dashboard.status()['sessions']] == [current.name]
    with pytest.raises(ValueError, match='predates'):
        dashboard.detail(old.name)
    with pytest.raises(ValueError):
        dashboard.detail('../private')
    link = dashboard.root/'20260924T120001Z_0123456789'
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError):
        dashboard.directory(link.name)
    assert old.is_dir()


def test_active_old_sessions_survive_dashboard_restart(dashboard, monkeypatch):
    old = session(dashboard, '20260924T115959Z_0123456789')
    hidden = session(dashboard, '20260924T115958Z_0123456789')
    dashboard.started_at = datetime(2026,9,24,12,tzinfo=timezone.utc).timestamp()
    running = {old.name}
    monkeypatch.setattr(dashboard, 'alive', lambda directory, lifecycle:directory.name in running)
    assert [r['run_id'] for r in dashboard.status()['sessions']] == [old.name]
    assert dashboard.directory(old.name) == old
    running.clear()
    # Do not hide the selected run when it finishes during this server lifetime.
    assert dashboard.directory(old.name) == old
    with pytest.raises(ValueError, match='predates'):
        dashboard.directory(hidden.name)


def test_history_view_offers_resume_only_where_resume_is_allowed(dashboard, monkeypatch):
    old = session(dashboard, '20260924T115959Z_0123456789')
    seen = session(dashboard, '20260924T115958Z_0123456789')
    for root in (old, seen):
        (root/'agent-lifecycle.json').write_text(json.dumps({'status': 'exited'}))
        (root/'agent-workspace').mkdir()
    dashboard.started_at = datetime(2026,9,24,12,tzinfo=timezone.utc).timestamp()
    running = {seen.name}
    monkeypatch.setattr(dashboard, 'alive', lambda directory, lifecycle: directory.name in running)
    dashboard.directory(seen.name)  # This server saw it running; then it crashed.
    running.clear()
    launched = []
    monkeypatch.setattr(subprocess, 'Popen', lambda argv, **kwargs: launched.append(argv) or SimpleNamespace(pid=7, wait=lambda: 0))

    assert dashboard.detail(old.name, history=True)['resumable'] is False  # Review-only: resume() refuses it.
    with pytest.raises(ValueError, match='predates'):
        dashboard.resume(old.name)
    assert dashboard.detail(seen.name, history=True)['resumable'] is True
    assert dashboard.resume(seen.name)['status'] == 'resuming' and launched[0][-1] == '--resume'


def test_a_launcher_gone_during_formal_reads_as_interrupted(dashboard, monkeypatch):
    root = session(dashboard)
    (root/'agent-lifecycle.json').write_text(json.dumps({'status': 'formal'}))
    running = {root.name}
    monkeypatch.setattr(dashboard, 'alive', lambda directory, lifecycle: directory.name in running)
    assert dashboard.summary(root)['status'] == 'formal'
    running.clear()  # Killed during the hours-long formal phase.
    assert dashboard.summary(root)['status'] == 'interrupted'


def test_stage_budget_usage_and_results_are_read_only(dashboard):
    root = session(dashboard)
    state = {'interactive_success':{'episode':'first'}, 'exploration_started':2,
        'formal_reserved':False, 'active':None, 'bundles':{'bundle':{'sha256':'hash'}},
        'gemini_spend':{'reported':100000000, 'reserved':200000000, 'blocked':False},
        'results':[{'mode':'rehearsal', 'task_complete':False, 'reason':'exit', 'returncode':0}]}
    path = root/'state.json'
    path.write_text(json.dumps(state))
    original = path.read_bytes()
    rollout = root/'codex-home/sessions/test.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({'type':'token_usage_record', 'payload':{
        'usage':{'total_tokens':123}, 'thread_token_usage':{'total_tokens':123}}})+'\n')
    detail = dashboard.detail(root.name)
    assert detail['manual_success'] and detail['exploration_used'] == 2
    assert detail['gemini']['limit_usd'] is None and detail['gemini']['remaining_usd'] is None
    assert detail['usage']['total_session_usage']['total_tokens'] == 123
    assert detail['results'][0]['task_complete'] is False  # Exit 0 is not task success.
    assert dashboard.detail(root.name)['usage']['total_session_usage']['total_tokens'] == 123
    assert path.read_bytes() == original
    state['results'].append({'mode':'rehearsal', 'bundle':'bundle', 'sha256':'hash',
        'task_complete':True, 'reason':'exit', 'returncode':0})
    path.write_text(json.dumps(state))
    assert dashboard.detail(root.name)['rehearsed_bundles'] == ['bundle']
    assert dashboard.detail(root.name)['stage'] == 'Qualifying rehearsal recorded'
    state['results'][-1]['recording_errors'] = ['incomplete frames']
    path.write_text(json.dumps(state))
    assert dashboard.detail(root.name)['rehearsed_bundles'] == []
    state.update(formal_reserved=True)
    path.write_text(json.dumps(state))
    assert dashboard.detail(root.name)['stage'] == 'Formal reserved; no retry'
    state['results'].append({'mode':'formal', 'task_complete':True})
    path.write_text(json.dumps(state))
    assert dashboard.detail(root.name)['stage'] == 'Formal complete'


@pytest.mark.skipif(not shutil.which('node'), reason='node renders the page script')
@pytest.mark.parametrize('limit', [None, 10])
def test_page_renders_gemini_spend_with_and_without_a_session_limit(dashboard, monkeypatch, tmp_path, limit):
    monkeypatch.setattr('services.dashboard.sessions.SESSION_LIMIT', limit and limit*NANODOLLARS)
    root = session(dashboard)
    (root/'state.json').write_text(json.dumps({'exploration_started':1, 'results':[],
        'gemini_spend':{'reported':100000000, 'reserved':200000000, 'blocked':False}}))
    detail = dashboard.detail(root.name)
    data = tmp_path/'research-state.json'
    data.write_text(json.dumps({'selected':detail, 'sessions':[detail]}))
    static = Path(__file__).resolve().parents[2]/'services/dashboard/static/app.js'
    rendered = json.loads(subprocess.run([shutil.which('node'), str(Path(__file__).with_name('fixtures')/'render_research.js'),
                                          str(static), str(data)], capture_output=True, text=True, check=True).stdout)
    assert rendered['error'] is None
    rows = {label: (value, title) for label, value, title in rendered['rows']}
    assert rows['Task'][0] == TASK
    assert rows['Gemini spend'] == (('$0.100', 'Reserved $0.2000; no session limit. Codex costs are separate.')
                                    if limit is None else
                                    ('$0.100 / $10', 'Reserved $0.2000; remaining $9.700. Codex costs are separate.'))


def test_codex_usage_sums_the_main_and_every_subagent_thread(dashboard):
    root = session(dashboard)
    (root/'state.json').write_text('{}')
    day = root/'codex-home/sessions/2026/10/13'
    day.mkdir(parents=True)

    def rollout(name, source, total):
        (day/name).write_text(''.join(json.dumps(record)+'\n' for record in (
            {'type': 'session_meta', 'payload': {'id': name, 'thread_source': source}},
            {'type': 'token_usage_record', 'payload': {'usage': {'total_tokens': total},
                                                       'thread_token_usage': {'total_tokens': total}}})))
    rollout('rollout-a-main.jsonl', 'user', 1_000_000)
    rollout('rollout-b-sub.jsonl', 'subagent', 2_000)
    rollout('rollout-c-sub.jsonl', 'subagent', 30_000)  # Sorted last: it alone used to be shown.
    (day/'rollout-d-old-sub.jsonl').write_text(''.join(json.dumps(record)+'\n' for record in (  # Older Codex: source only.
        {'type': 'session_meta', 'payload': {'id': 'd', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'a'}}}}},
        {'type': 'token_usage_record', 'payload': {'usage': {'total_tokens': 400}, 'thread_token_usage': {'total_tokens': 400}}})))
    usage = dashboard.detail(root.name)['usage']
    assert usage['total_session_usage']['total_tokens'] == 1_032_400
    assert usage['main_usage']['total_tokens'] == 1_000_000
    assert usage['subagent_usage']['total_tokens'] == 32_400
    assert usage['threads'] == {'main': 1, 'subagents': 3}
    assert dashboard.detail(root.name)['usage'] == usage  # Incremental readers do not double count.


def test_launch_uses_fixed_argv_and_cannot_launch_twice(dashboard, monkeypatch):
    root = session(dashboard)
    calls = []
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=42, wait=lambda: 0)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    assert dashboard.launch(root.name)['status'] == 'launching'
    assert calls[0][0][-1] == '--non-interactive'
    assert calls[0][1]['stdin'] == subprocess.DEVNULL
    assert not calls[0][1].get('shell')
    with pytest.raises(ValueError, match='already launched'):
        dashboard.launch(root.name)
    assert len(calls) == 1


def test_stop_rejects_unowned_process_and_bounds_log_reads(dashboard):
    root = session(dashboard)
    (root/'agent-lifecycle.json').write_text(json.dumps({'pid':1, 'status':'running'}))
    with pytest.raises(ValueError, match='No matching live launcher'):
        dashboard.stop(root.name)
    assert dashboard.detail(root.name)['status'] == 'interrupted'
    log = root/'agent.log'
    log.write_text('a'*70000+'end')
    assert len(tail(log)) == 65536 and tail(log).endswith('end')


@pytest.mark.skipif(not hasattr(ctypes.CDLL(None), 'pidfd_open'),
                    reason='pidfd_open unavailable (kernel < 5.3)')
def test_stop_signals_only_verified_owned_process_with_pidfd(dashboard):
    root = session(dashboard)
    # Harmless stand-in carries the exact session identity; no model or robot.
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)',
        'harness.codex_cli.auto_research', '--config', str(root/'operator-input.json')])
    try:
        (root/'agent-lifecycle.json').write_text(json.dumps({'pid':process.pid, 'status':'running'}))
        assert dashboard.detail(root.name)['alive']
        assert dashboard.stop(root.name)['status'].startswith('stop requested')
        assert process.wait(timeout=5) == -signal.SIGTERM
        assert not dashboard.detail(root.name)['alive']
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_artifact_view_is_bounded_and_published_only(dashboard):
    root = session(dashboard)
    output = root/'published/runs/rehearsal-test'
    output.mkdir(parents=True)
    (output/'stdout.log').write_text('controller marker')
    logical = 'runtime/autonomous_controller/runs/rehearsal-test/'
    assert dashboard.artifact(root.name, logical+'stdout.log')[0] == b'controller marker'
    for invalid in ('../../operator-input.json', '/etc/passwd', logical+'../../../../operator-input.json'):
        with pytest.raises(ValueError):
            dashboard.artifact(root.name, invalid)
    (output/'private.json').symlink_to(root/'operator-input.json')
    with pytest.raises((OSError, ValueError)):
        dashboard.artifact(root.name, logical+'private.json')
    (output/'large.log').write_bytes(b'x'*(4*1024*1024+1))
    with pytest.raises(ValueError):
        dashboard.artifact(root.name, logical+'large.log')


def test_research_http_requires_auth_and_has_no_evaluation_override(dashboard):
    class Handler(DashboardHandler):
        research = dashboard
        token = 'fixture-token'
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base+'/api/research/state')
        assert error.value.code == 401
        request = urllib.request.Request(base+'/api/research/state', headers={'X-Operator-Token':'fixture-token'})
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['sessions'] == []
        request = urllib.request.Request(base+'/api/research/submit', data=b'{}',
            headers={'X-Operator-Token':'fixture-token', 'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 404
        with urllib.request.urlopen(base+'/app.js') as response:
            assert response.headers['Content-Type'].startswith('application/javascript')
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_a_task_prepares_with_its_benchmarks_fixed_setup(tmp_path, monkeypatch):
    """A task selects its benchmark; the benchmark's fixed fields override what the form sent."""
    research = ResearchSessions(tmp_path, lambda: [TASK])
    values = research.settings({**DEFAULTS, 'task': TASK})
    assert values['benchmark'] == BENCH.name
    for name, (value, _) in BENCH.fixed.items():
        if name in values and name != 'adaptation_source':
            assert research.settings({**DEFAULTS, 'task': TASK, name: values[name]})[name] == values[name]
    commands = []
    def configure(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=str(session(research)/'operator-input.json')+'\n', stderr='')
    monkeypatch.setattr(subprocess, 'run', configure)
    research.prepare({**DEFAULTS, 'task': TASK})
    assert commands[0][commands[0].index('--benchmark')+1] == BENCH.name
    state = research.status()
    assert state['benchmarks'] == {TASK: BENCH.name}
    assert state['benchmark_defaults'][BENCH.name] == BENCH.setup_defaults
    assert set(state['benchmark_fixed'][BENCH.name]) == set(BENCH.fixed)


def test_launcher_named_through_a_link_is_alive(dashboard, tmp_path):
    root = session(dashboard)
    link = tmp_path/'runtime-link'
    link.symlink_to(root.parent)
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)',
        'harness.codex_cli.auto_research', '--config', str(link/root.name/'operator-input.json')])
    try:
        (root/'agent-lifecycle.json').write_text(json.dumps({'pid':process.pid, 'status':'running'}))
        assert dashboard.detail(root.name)['alive']
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_launcher_named_by_a_relative_path_is_found_from_any_directory(dashboard, tmp_path, monkeypatch):
    """A launcher started as `--config runtime/auto-research/<run>/...` from the project is alive and
    a session user for a caller elsewhere (an operator script): its own working directory resolves it."""
    from harness import session_gpus
    root = session(dashboard)
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'harness.codex_cli.auto_research',
        '--config', str((root/'operator-input.json').relative_to(dashboard.project))], cwd=dashboard.project)
    elsewhere = tmp_path/'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    try:
        (root/'agent-lifecycle.json').write_text(json.dumps({'pid':process.pid, 'status':'running'}))
        assert dashboard.detail(root.name)['alive']
        assert process.pid in session_gpus.users(root)
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_an_older_page_or_profile_with_a_claude_account_choice_still_prepares(dashboard, monkeypatch):
    """claude-login has one experiment account: a retired agent_account is dropped, never passed on."""
    commands = []
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append(command) or SimpleNamespace(
        returncode=0, stdout=str(session(dashboard)/'operator-input.json')+'\n', stderr=''))
    values = dashboard.settings({'agent_account': 'second'})
    assert 'agent_account' not in values and values['agent_provider'] == 'openrouter'
    dashboard.prepare({**DEFAULTS, 'agent_account': 'second'})
    assert not any('account' in part for part in commands[0])
    assert 'agent_account' not in json.loads(dashboard.profile.read_text())
    dashboard.profile.write_text(json.dumps({**DEFAULTS, 'agent_account': 'second'}))
    assert 'agent_account' not in dashboard.status()['profile'] and 'claude_accounts' not in dashboard.status()
