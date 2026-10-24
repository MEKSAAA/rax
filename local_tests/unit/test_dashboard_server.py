"""Operator dashboard: episode review, HTTP routes and page wiring."""
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

import pytest

from services.dashboard.server import Handler, STATIC, installed_tasks
from services.dashboard.sessions import ResearchSessions
from bench import TASK

RUN = '20260928T120000Z_0123456789'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def project(tmp_path):
    root = tmp_path/'project'
    (root/'runtime/operator').mkdir(parents=True)
    (root/'runtime/auto-research').mkdir(parents=True)
    return root


def research_session(root):
    session = root/'runtime/auto-research'/RUN
    write(session/'operator-input.json', {'task': TASK, 'exploration_seeds': [1, 2, 3], 'mode': 'auto-research'})
    write(session/'state.json', {'results': [{'mode': 'formal', 'episode_id': 'f2', 'task_complete': True}]})
    sims = {'exploration': session/f'native/results/{TASK}_exploration_20260928T120100Z_a/sim',
            'formal': session/f'formal-workers/episode-002/native/results/{TASK}_formal_20260928T130000Z_b/sim'}
    write(sims['exploration']/'operator_state.json', {'episode_id': 'e1', 'episode_mode': 'exploration', 'step_id': 7})
    write(sims['formal']/'summary.json', {'episode_id': 'f2', 'step_id': 600, 'success': True})
    write(sims['formal']/'operator_state.json', {'episode_id': 'f2', 'episode_mode': 'formal', 'episode_step_limit': 600})
    (sims['formal']/'sensors.mp4').write_bytes(bytes(range(256)) * 4)
    return session


def test_review_covers_research_sessions(tmp_path):
    root = project(tmp_path)
    research_session(root)
    research = ResearchSessions(root, lambda: [TASK], time.time())

    with pytest.raises(ValueError, match='predates'):
        research.episodes(RUN)  # The live view hides runs older than the server.
    rows = research.episodes(RUN, history=True)
    assert [(r['mode'], r['live'], r['video'], r['formal_episode_index'], r['task_complete']) for r in rows] == [
        ('exploration', True, False, None, None), ('formal', False, True, 2, True)]
    assert research.status(history=True)['selected']['launchable'] is False
    from services.benchmarks import BENCHMARKS
    # The registry's catalogs, in registry order.
    assert installed_tasks()() == [task for bench in BENCHMARKS.values() for task in bench.task_names()]
    for bad in ('../..', 'native/results/x/sim/../../..', 'results/sim_mcp/x/sim', str(Path('/etc'))):
        with pytest.raises((ValueError, FileNotFoundError)):
            research.video(RUN, bad, history=True)


def test_review_lists_live_agent_copies_with_their_records(tmp_path):
    root = project(tmp_path)
    session = research_session(root)
    copy = session/'agent-formal/episode-003'
    sim = copy/f'native/results/{TASK}_formal_20260928T140000Z_c/sim'
    write(copy/'state.json', {'results': [{'mode': 'formal', 'driver': 'agent', 'episode_id': 'a3',
                                           'formal_episode_index': 1, 'task_complete': False}]})
    write(sim/'summary.json', {'episode_id': 'a3', 'step_id': 420, 'success': False})
    write(sim/'operator_state.json', {'episode_id': 'a3', 'episode_mode': 'formal', 'episode_step_limit': 600})
    # Newer than the exploration episode's state, also on a coarse file clock.
    later = (sim/'operator_state.json').stat().st_mtime + 1
    os.utime(sim/'operator_state.json', (later, later))
    (sim/'sensors.mp4').write_bytes(b'agent copy video')
    aside = session/'agent-formal.interrupted-20260928T150000Z/episode-001/native/results/x/sim'
    write(aside/'summary.json', {'episode_id': 'old', 'step_id': 3})  # Moved aside on resume: not listed.
    research = ResearchSessions(root, lambda: [TASK], time.time())

    rows = research.episodes(RUN, history=True)
    assert [(r['mode'], r['formal_episode_index'], r['episode_id'], r['task_complete'], r['steps']) for r in rows] == [
        ('exploration', None, 'e1', None, 7), ('formal', 2, 'f2', True, 600), ('agent_formal', 3, 'a3', False, 420)]
    assert research.video(RUN, str(sim.relative_to(session)), history=True).read_bytes() == b'agent copy video'
    newest, state = ResearchSessions.latest_native(session)
    assert newest == sim and state['episode_id'] == 'a3'  # The live view follows agent copies too.


@pytest.fixture
def server(tmp_path, monkeypatch):
    root = project(tmp_path)
    research_session(root)
    monkeypatch.setattr(Handler, 'research', ResearchSessions(root, lambda: [TASK], None), raising=False)
    monkeypatch.setattr(Handler, 'token', 'secret-token', raising=False)
    monkeypatch.setattr(Handler, 'log_message', lambda *_: None)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def request(method, path, token=None, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=10)
        headers = {**({'X-Operator-Token': token} if token else {}), **(headers or {})}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data
    yield request
    httpd.shutdown()
    httpd.server_close()


def test_http_auth_routes_and_video_ranges(server):
    response, page = server('GET', '/')
    assert response.status == 200 and b'/app.js' in page
    assert server('GET', '/app.js')[0].status == 200
    for path in ('/api/research/state', '/api/research/episodes?run_id='+RUN):
        assert server('GET', path)[0].status == 401
        assert server('GET', path, 'wrong')[0].status == 401
        assert server('GET', path, 'secret-tokén')[0].status == 401  # Non-ASCII: refused, not a dropped connection.
        assert server('GET', path, headers={'Cookie': 'dashboard_token=é'})[0].status == 401
    assert server('POST', '/api/research/launch', body={'run_id': RUN})[0].status == 401

    response, data = server('GET', '/api/research/state', 'secret-token')
    assert response.status == 200 and json.loads(data)['selected']['run_id'] == RUN
    assert server('GET', '/api/research/artifact?run_id='+RUN+'&path=../../etc/passwd', 'secret-token')[0].status == 400
    assert server('GET', '/api/direct/state', 'secret-token')[0].status == 404
    assert server('POST', '/api/research/launch', 'secret-token', {'run_id': RUN, 'x': 1})[0].status == 400
    assert server('POST', '/api/research/submit', 'secret-token', {})[0].status == 404

    response, _ = server('POST', '/api/session', 'secret-token')
    cookie = response.getheader('Set-Cookie')
    assert response.status == 204 and 'HttpOnly' in cookie
    rows = json.loads(server('GET', '/api/research/episodes?run_id='+RUN, 'secret-token')[1])
    path = '/api/research/video?' + urllib.parse.urlencode({'run_id': RUN, 'episode': rows[1]['episode']})
    response, data = server('GET', path, headers={'Cookie': cookie.split(';')[0], 'Range': 'bytes=1020-'})
    assert response.status == 206 and data == bytes(range(252, 256))
    assert response.getheader('Content-Range') == 'bytes 1020-1023/1024'
    response, data = server('GET', path, 'secret-token', headers={'Range': 'bytes=10-19'})
    assert response.status == 206 and data == bytes(range(10, 20))


def test_page_script_uses_only_elements_in_the_page():
    html = (STATIC/'index.html').read_text()
    script = (STATIC/'app.js').read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    used = set(re.findall(r"\$\('([A-Za-z_]+)'\)", script)) | set(re.findall(r"text\('([A-Za-z_]+)'", script))
    assert used and used <= ids, sorted(used - ids)
    assert 'unsafe-inline' not in (Path(__file__).resolve().parents[2]/'services/dashboard/server.py').read_text().split('script-src')[1].split(';')[0]
    node = shutil.which('node')
    if node:
        subprocess.run([node, '--check', str(STATIC/'app.js')], check=True)


def test_ipv6_loopback_binds_and_serves(monkeypatch):
    import socket
    from services.dashboard.server import IPv6Server
    if not socket.has_ipv6:
        pytest.skip('no IPv6 on this host')
    monkeypatch.setattr(Handler, 'log_message', lambda *_: None)
    try:
        httpd = IPv6Server(('::1', 0), Handler)
    except OSError:
        pytest.skip('IPv6 loopback unavailable')
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection('::1', httpd.server_port, timeout=10)
        connection.request('GET', '/')
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
