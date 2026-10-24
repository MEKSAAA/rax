"""Dedicated Claude experiment login: token storage, identity guards, launch and dashboard wiring."""
import json
import stat
import time

import pytest

from harness.claude_cli import experiment_login as login
from bench import TASK


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    monkeypatch.setenv('RAX_CLAUDE_EXPERIMENT_DIR', str(tmp_path/'rax'))
    (tmp_path/'home').mkdir()
    return tmp_path


def accounts(monkeypatch, experiment, personal):
    monkeypatch.setattr(login, 'account', lambda directory, claude=None:
                        {'email': experiment if directory is not None else personal,
                         'organization': 'Lab', 'org_id': 'o', 'subscription': 'max', 'logged_in': True})


def test_install_stores_a_private_token_and_identity_only(isolated):
    record = login.install('sk-ant-oat01-secret\n', {'email': 'robot@lab.example', 'organization': 'Lab'})
    token, metadata = login.token_file(), login.metadata_file()
    assert token.read_text() == 'sk-ant-oat01-secret\n'
    for path in (token, metadata):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token.parent.stat().st_mode) == 0o700
    assert 'secret' not in metadata.read_text() and record['email'] == 'robot@lab.example'
    assert sorted(p.name for p in login.root().iterdir()) == ['claude-experiment.json', 'claude-experiment.token']
    status = login.status()
    assert status['email'] == 'robot@lab.example' and status['token_age_days'] == 0
    for bad in ('', 'has space', '{"claudeAiOauth": {}}'):
        with pytest.raises(ValueError):
            login.install(bad, {'email': 'robot@lab.example'})


def test_status_explains_a_missing_token(isolated):
    with pytest.raises(RuntimeError, match='setup_claude_experiment_login'):
        login.status()


def test_install_command_refuses_the_personal_account(isolated, monkeypatch, capsys):
    accounts(monkeypatch, 'me@personal.example', 'me@personal.example')
    monkeypatch.setattr('sys.stdin', type('S', (), {'isatty': lambda self: False, 'readline': lambda self: 'tok\n'})())
    assert login.main(['install']) == 1
    assert 'personal Claude login' in capsys.readouterr().err
    assert not login.token_file().exists()
    accounts(monkeypatch, 'robot@lab.example', 'me@personal.example')
    assert login.main(['install']) == 0
    assert login.token_file().read_text() == 'tok\n'


def test_check_rejects_revoked_and_personal_tokens(isolated, monkeypatch):
    login.install('sk-ant-oat01-x', {'email': 'robot@lab.example'})
    responses = {}
    monkeypatch.setattr(login, '_request', lambda method, path, headers, body=None, timeout=30: responses[path])
    responses['/v1/messages/count_tokens'] = (401, b'{}')
    with pytest.raises(RuntimeError, match='rejected'):
        login.check()
    responses['/v1/messages/count_tokens'] = (200, b'{"input_tokens": 3}')
    responses['/api/oauth/profile'] = (200, json.dumps({'account': {'email_address': 'me@personal.example'}}).encode())
    with pytest.raises(RuntimeError, match='personal account'):
        login.check(personal_email='me@personal.example')
    responses['/api/oauth/profile'] = (200, json.dumps({'account': {'email_address': 'other@lab.example'}}).encode())
    with pytest.raises(RuntimeError, match='not the experiment account'):
        login.check()
    responses['/api/oauth/profile'] = (404, b'')
    result = login.check(personal_email='me@personal.example')
    assert result['verified'] and result['token_account'] is None
    responses['/api/oauth/profile'] = (200, json.dumps({'account': {'email_address': 'ROBOT@lab.example'}}).encode())
    assert login.check()['token_account'] == 'ROBOT@lab.example'


def test_usage_reads_the_rate_limit_headers_of_a_one_token_call(isolated, monkeypatch):
    login.install('sk-ant-oat01-x', {'email': 'robot@lab.example'})
    sent, answer = [], {'status': 429, 'headers': {
        'anthropic-ratelimit-unified-status': 'rejected', 'anthropic-ratelimit-unified-5h-utilization': '1.0',
        'anthropic-ratelimit-unified-7d-utilization': '0.42', 'anthropic-ratelimit-unified-reset': '1800000000'}}

    class Response:
        status = property(lambda self: answer['status'])
        def read(self, size):
            return b'{}'
        def getheader(self, key):
            return answer['headers'].get(key)

    class Connection:
        def __init__(self, host, timeout):
            sent.append(host)
        def request(self, method, path, body, headers):
            sent.append((method, path, json.loads(body), headers))
        def getresponse(self):
            if answer['status'] is None:
                raise ConnectionRefusedError('refused')
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(login.http.client, 'HTTPSConnection', Connection)
    row = login.usage()
    assert row == {'http_status': 429, 'status': 'rejected', 'five_hour': 1.0, 'seven_day': 0.42, 'reset': 1800000000}
    host, (method, path, body, headers) = sent
    assert (host, method, path) == ('api.anthropic.com', 'POST', '/v1/messages')
    assert body['max_tokens'] == 1 and body['model'] == login.PROVIDERS['claude-login']['fast']
    assert headers['Authorization'] == 'Bearer sk-ant-oat01-x' and headers['anthropic-beta'] == login.OAUTH_BETA
    assert login.usage_reset(row) == 1800000000
    # Only an exhausted window waits: not an ordinary 429, a rejected token or an unreachable API.
    answer['headers'].pop('anthropic-ratelimit-unified-reset')
    assert 599 <= login.usage_reset(login.usage()) - time.time() <= 601  # No reset named: ten minutes on.
    answer['headers']['anthropic-ratelimit-unified-status'] = 'allowed'
    assert login.usage_reset(login.usage()) is None
    answer.update(status=401, headers={})
    assert login.usage() == {'http_status': 401, 'status': None, 'five_hour': None, 'seven_day': None, 'reset': None}
    assert login.usage_reset(login.usage()) is None
    answer['status'] = None
    row = login.usage()
    assert row == {'http_status': None, 'status': 'unreachable', 'error': 'ConnectionRefusedError: refused'}
    assert login.usage_reset(row) is None


def test_deploy_defaults_to_the_experiment_token_and_records_the_account(isolated, monkeypatch):
    from harness.claude_cli import auto_research as claude
    from test_controller_backend import configuration
    login.install('sk-ant-oat01-x', {'email': 'robot@lab.example', 'organization': 'Lab'})
    config = configuration(isolated/'session')
    monkeypatch.setattr(claude, 'claude_binary', lambda path=None: isolated/'claude')
    monkeypatch.setattr(claude, 'prepare_workspace', lambda config, name: {
        'workspace': (isolated/'ws').resolve(), 'home': (isolated/'claude-home').resolve(),
        'configuration': isolated/'c.json', 'bridge': isolated/'b.py'})
    (isolated/'ws').mkdir(); (isolated/'claude-home').mkdir()
    deployed = claude.deploy(config, provider='claude-login')
    record = json.loads(deployed['login'].read_text())
    # Each launch reads the experiment token itself, so a rotated token applies from the next launch.
    assert record['key_file'] == str(login.token_file()) and 'credentials_file' not in record
    assert record['account'] == {'email': 'robot@lab.example', 'organization': 'Lab'}
    assert 'sk-ant' not in deployed['login'].read_text()
    assert not list(config.root.glob('*.token'))  # No per-session token copy.
    # Claude Code reads the token from its environment, at launch, from the experiment token file.
    socket = isolated/'s.sock'
    assert claude.agent_environment(config, deployed, socket)['CLAUDE_CODE_OAUTH_TOKEN'] == 'sk-ant-oat01-x'
    login.install('sk-ant-oat01-rotated', {'email': 'robot@lab.example', 'organization': 'Lab'})
    assert claude.agent_environment(config, deployed, socket)['CLAUDE_CODE_OAUTH_TOKEN'] == 'sk-ant-oat01-rotated'


def test_sessions_prepared_with_the_personal_login_cannot_launch(isolated, monkeypatch):
    from harness.claude_cli import auto_research as claude
    from test_controller_backend import configuration
    config = configuration(isolated/'old')
    record = isolated/'agent-login.json'
    record.write_text(json.dumps({'provider': 'claude-login', 'key_file': None,
                                  'credentials_file': str(isolated/'home/.claude/.credentials.json')}))
    started = []
    monkeypatch.setattr(claude, 'start_frontend', lambda *a, **k: started.append(a))
    with pytest.raises(RuntimeError, match='personal Claude login'):
        claude.launch(config, {'workspace': isolated, 'claude_home': isolated, 'login': record}, prompt='x')
    assert started == []


def test_dashboard_prepare_reports_a_missing_token_and_shows_the_account(isolated):
    from services.dashboard.sessions import ResearchSessions as Sessions
    model = Sessions(isolated/'project', lambda: [TASK])
    with pytest.raises(ValueError, match='setup_claude_experiment_login'):
        model.prepare({'agent_cli': 'claude', 'agent_provider': 'claude-login'})
    session = isolated/'session'
    session.mkdir()
    config = {'agent_cli': 'claude', 'agent_provider': 'claude-login'}
    login.install('sk-ant-oat01-x', {'email': 'robot@lab.example'})
    assert Sessions.agent(session, config) == {'cli': 'claude', 'provider': 'claude-login',
                                               'account': 'robot@lab.example', 'pending': True,
                                               'effort': 'xhigh'}
    (session/'agent-login.json').write_text(json.dumps({'account': {'email': 'launched@lab.example'}}))
    assert Sessions.agent(session, config)['account'] == 'launched@lab.example'
    assert Sessions.agent(session, {'agent_cli': 'codex', 'agent_effort': 'max'}) == {
        'cli': 'codex', 'provider': None, 'account': None, 'effort': 'max'}


def test_a_missing_api_key_names_the_launcher_flag_that_supplies_it(monkeypatch):
    from harness.claude_cli.login import Credential
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    with pytest.raises(ValueError, match=r'^The anthropic provider needs --claude-key-file or ANTHROPIC_API_KEY$'):
        Credential('anthropic')
    with pytest.raises(ValueError, match=r'^The openrouter provider needs --key-file or OPENROUTER_API_KEY$'):
        Credential('openrouter')
