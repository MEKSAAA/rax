"""Agent frontend permissions, tool contracts, and fixed Codex launcher config."""
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from dataclasses import replace

import pytest

from test_controller_backend import configuration, supervisor
from services.controller.frontend import ResearchFrontend
from harness.codex_cli.auto_research import deploy, startup_prompt, DISABLED_FEATURES
from harness.codex_cli.model_relay import MODEL
from bench import STEP, TASK


def data(reply):
    return json.loads(reply['content'][0]['text'])


@pytest.fixture
def frontend(supervisor, tmp_path):
    f = ResearchFrontend(supervisor[0], tmp_path/'agent')
    yield f
    f.close()


def test_frontend_soft_guidance_and_native_images(supervisor, frontend):
    s, backend = supervisor
    f = frontend
    initial = {t['name'] for t in f.tools()}
    assert STEP in initial and 'register' in initial
    assert 'gemini_generate' in initial
    assert not {'operate', 'enter_recorder', 'exit_recorder', 'discard_recorder'} & initial
    assert not {'shell_exec', 'shell_poll', 'shell_stop', 'read_file', 'view_image'} & initial
    assert not any(name.startswith('research_') for name in initial)
    assert 'research_write_file' not in initial and 'train' not in initial
    assert 'private_request_formal_episode' not in initial
    assert not f.status()['manual_success_confirmed']
    bundle = f.workspace/'code/test'
    bundle.mkdir(parents=True)
    (bundle/'controller.py').write_text('def main(ctx): pass')
    first = f.call('start_episode', {})
    assert first['content'][1] == backend.image and 'structuredContent' not in first
    registered = data(f.call('register', {'source': 'code/test'}))
    assert registered['bundle'] in s.state['bundles'] and s.state['active']['mode'] == 'interactive'
    with pytest.raises(RuntimeError, match='rehearsal'):
        f.call('submit', {'bundle': registered['bundle']})
    assert s.state['active']['mode'] == 'interactive'
    backend.success = True
    assert data(f.call('evaluate', {}))['manual_success_confirmed']
    assert initial == {t['name'] for t in f.tools()}


def test_frontend_paths_and_native_recording_bypass_are_rejected(supervisor, tmp_path, frontend):
    s, backend = supervisor
    f = frontend
    for name in ('/etc/passwd', '../private/state.json', 'code/../../private/configuration.json'):
        with pytest.raises((ValueError, PermissionError)):
            f.source(name)
    (f.workspace/'link').symlink_to(tmp_path/'private')
    with pytest.raises(ValueError):
        f.call('register', {'source': 'link'})
    f.call('start_episode', {})
    backend.success = True
    f.call('evaluate', {})
    f.call(STEP, {'actions': [[0]*14]})
    assert backend.step == 1


def test_protocol_errors_do_not_disclose_private_paths(supervisor, tmp_path, frontend):
    f = frontend
    reply = f.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                     'params': {'name': 'read_file', 'arguments': {'path': 'missing'}}})
    assert reply['id'] == 7 and reply['result']['isError']
    assert str(tmp_path) not in json.dumps(reply)


def test_public_result_preserves_execution_and_evaluation_diagnostics():
    source = {'error_type': 'RuntimeError', 'error': {'reason': 'Container exited early'},
              'evaluation_error': 'TimeoutError', 'evaluation_failure': {'reason': 'Evaluation timed out'},
              'container': 'private-container', 'workspace_path': '/private'}
    result = ResearchFrontend.public_result(source)
    assert result['error'] == source['error'] and result['evaluation_failure'] == source['evaluation_failure']
    assert 'container' not in result and 'workspace_path' not in result


def test_large_inline_image_through_frontend_stdio(frontend):
    import io
    import os
    from PIL import Image
    import base64
    from services.controller.gemini import GeminiRouter, MODEL
    raw = io.BytesIO()
    Image.frombytes('RGB', (768, 768), os.urandom(768*768*3)).save(raw, format='PNG')
    block = {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(raw.getvalue()).decode()}
    captured = []
    def send(payload):
        captured.append(payload)
        return {'model': MODEL, 'choices': [{'message': {'content': 'seen'}}],
                'usage': {'total_tokens': 10}}
    frontend.api.gemini = GeminiRouter('host-only-test-key', send=send)
    request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'gemini_generate', 'arguments': {
            'messages': [{'role': 'user', 'content': [block]}]}}}) + '\n'
    assert len(request) > 2*1024*1024
    output = io.StringIO()
    frontend.serve(io.StringIO(request), output)
    result = json.loads(output.getvalue())['result']
    assert not result.get('isError'), result
    assert data(result)['usage']['total_tokens'] == 10
    assert captured[0]['messages'][0]['content'][0]['image_url']['url'].endswith(block['data'])


def test_frontend_oversized_wire_request_closes_stream(frontend, monkeypatch):
    import io
    monkeypatch.setattr('services.controller.frontend.MAX_REQUEST_BYTES', 16)
    output = io.StringIO()
    frontend.serve(io.StringIO('x'*17 + '\n' + '{}\n'), output)
    assert len(output.getvalue().splitlines()) == 1
    assert json.loads(output.getvalue())['error']['message'] == 'Request exceeds wire limit'


@pytest.fixture
def demo_cache(tmp_path, monkeypatch):
    """Deterministic video decoder; real JPEG export, no OpenCV/network dependency."""
    import sys
    from types import SimpleNamespace
    import numpy as np
    from PIL import Image
    cache = tmp_path/'runtime/reference-demos/website'
    cache.mkdir(parents=True)
    for task in (TASK,):
        (cache/f'{task}.mp4').write_bytes(b'\x00\x00\x00\x18ftypisom')
    class Capture:
        def __init__(self, _):
            self.index = 0
        def isOpened(self):
            return True
        def read(self):
            self.index += 1
            return (True, np.full((8, 12, 3), self.index*50, dtype=np.uint8)) if self.index <= 3 else (False, None)
        def release(self):
            pass
    def write(path, frame, _):
        Image.fromarray(frame).save(path, format='JPEG')
        return True
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(VideoCapture=Capture, imwrite=write, IMWRITE_JPEG_QUALITY=1))
    import urllib.request
    def no_network(*a, **k):
        pytest.fail('Cached demonstration must not require network')
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    return cache


def test_deploy_copies_skills_rubric_and_disables_other_execution(tmp_path, monkeypatch, demo_cache):
    kind, task = 'none', TASK
    def fake_storage(root, *_):
        path = root/'agent-storage'
        path.mkdir()
        return path
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision', fake_storage)
    provider = tmp_path/'provider'
    provider.mkdir()
    (provider/'openrouter.config.toml').write_text('model_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:1"\nwire_api="responses"\n')
    config = replace(configuration(tmp_path), task=task, demonstration_context=kind)
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    deployed = deploy(config, provider_home=provider, codex='/usr/bin/true')
    settings = tomllib.loads((deployed['codex_home']/'config.toml').read_text())
    assert set(settings['mcp_servers']) == {'auto_research'}
    assert all(settings['features'][key] is False for key in DISABLED_FEATURES)
    assert settings['web_search'] == 'disabled' and settings['approval_policy'] == 'never'
    assert settings['sandbox_mode'] == 'danger-full-access'  # Docker is the outer boundary.
    assert settings['features']['shell_tool'] and settings['features']['unified_exec']
    assert settings['model_auto_compact_token_limit'] == 256000
    assert settings['model_reasoning_effort'] == config.agent_effort == 'xhigh'
    assert settings['model_auto_compact_token_limit_scope'] == 'total'
    assert settings['model_providers']['host_relay']['base_url'] == 'http://127.0.0.1:17777'
    from harness.codex_cli.auto_research import CALL_SECONDS
    allowance = CALL_SECONDS + 300  # Sessions have no wall clock; one call gets 20 days.
    assert settings['model_providers']['host_relay']['stream_idle_timeout_ms'] == allowance*1000
    assert settings['mcp_servers']['auto_research']['tool_timeout_sec'] == allowance
    assert not (deployed['codex_home']/'auth.json').exists()
    assert (deployed['workspace']/'.agents/skills/autonomous-control/SKILL.md').is_file()
    assert not (deployed['workspace']/'.agents/skills/policies').exists()  # No policy skill or package.
    from services.controller.storage import AGENT_TEMPLATE
    from services.mcp_contract import Contract
    from services.mcp_workspace import install_skills, render
    installed = tmp_path/'installed'
    install_skills(installed/'.agents/skills')
    demonstrations = kind != 'none'
    images = {'none': 0, 'terminal_state': 1}.get(kind, 3)
    assert (deployed['workspace']/'.agents/skills/in-context-action-learning').exists() == demonstrations
    for name in ('robot-interface', 'object-localization', 'experience-memory', 'autonomous-control',
                 'manual-robot-control', 'gemini'):
        relative = Path('.agents/skills')/name
        assert (deployed['workspace']/relative/'SKILL.md').is_file()
        for source in (installed/relative).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                copied = deployed['workspace']/source.relative_to(installed)
                if source.name.endswith('.formal-episode.md'):
                    assert not copied.exists()  # Formal-copy variant only.
                    continue
                if source.suffix == '.md':
                    from services.mcp_contract import Contract
                    from services.mcp_workspace import render
                    assert copied.read_text() == render(source.read_text(), Contract.from_config(config), task=task,
                                                        demonstrations=demonstrations, demonstration_kind=kind,
                                                        demonstration_images=images)
                else:
                    assert copied.read_bytes() == source.read_bytes()
    from services.controller.storage import RUNTIME_SOURCE, runtime_digest
    copied_api = deployed['workspace']/'api/runtime.py'
    assert copied_api.read_bytes() == RUNTIME_SOURCE.read_bytes()
    original_digest = runtime_digest()
    copied_api.write_text('# Agent can edit its own copy, not the trusted runner SDK.\n')
    assert runtime_digest() == original_digest
    assert not (deployed['workspace']/'services').exists()
    assert 'https://' not in (deployed['workspace']/'TASK.md').read_text()
    assert 'Python' in (deployed['workspace']/'AGENTS.md').read_text()
    context = json.loads((config.root/'demonstration-context.json').read_text())
    assert context['kind'] == kind
    target = deployed['workspace']/'runtime/demonstrations'
    assert not list(target.rglob('*.mp4'))  # Only pixels, never video/data/actions.
    assert context['manifest_path'] is None and not list(target.iterdir())
    for name in ('AGENTS.md', 'START_PROMPT.md'):
        assert (deployed['workspace']/name).read_text() == render((AGENT_TEMPLATE/name).read_text(), Contract.from_config(config), task=task,
                                                                   demonstrations=demonstrations, demonstration_kind=kind,
                                                                   demonstration_images=images)
    assert startup_prompt(deployed['workspace']) == render((AGENT_TEMPLATE/'START_PROMPT.md').read_text(), Contract.from_config(config), task=task).strip()
    assert (deployed['configuration'].stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        deploy(config, provider_home=provider, codex='/usr/bin/true')


def test_profile_model_key_serves_relay_and_openrouter_key_stays_for_gemini(tmp_path, monkeypatch, demo_cache):
    def fake_storage(root, *_):
        path = root/'agent-storage'
        path.mkdir()
        return path
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision', fake_storage)
    provider = tmp_path/'provider'
    (provider/'secrets').mkdir(parents=True)
    for name, key in (('openai_key', 'sk-profile-fixture'), ('openrouter_key', 'sk-or-fixture')):
        (provider/'secrets'/name).write_text(key+'\n')
        (provider/'secrets'/name).chmod(0o600)
    (provider/'native.config.toml').write_text('model_provider="openai"\n[model_providers.openai]\nname="OpenAI"\n'
        'base_url="https://api.openai.com/v1"\nwire_api="responses"\n[rax]\nkey_file="secrets/openai_key"\nnative_models=true\n')
    config = configuration(tmp_path)
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    deployed = deploy(config, provider_home=provider, profile='native', key_file=provider/'secrets/openrouter_key',
                      codex='/usr/bin/true')
    relay = json.loads(deployed['relay_configuration'].read_text())
    assert relay == {'base_url': 'https://api.openai.com/v1', 'headers': {'Authorization': 'Bearer sk-profile-fixture'},
                     'model': MODEL, 'native_models': True}
    assert 'sk-' not in (deployed['codex_home']/'config.toml').read_text()


def test_chatgpt_profile_stores_only_the_login_path(tmp_path, monkeypatch, demo_cache):
    def fake_storage(root, *_):
        path = root/'agent-storage'
        path.mkdir()
        return path
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision', fake_storage)
    provider = tmp_path/'provider'
    provider.mkdir()
    (provider/'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt',
        'tokens': {'access_token': 'h.e30.s', 'account_id': 'acct-fixture'}}))
    (provider/'openrouter_key').write_text('sk-or-fixture\n')
    (provider/'openrouter_key').chmod(0o600)
    (provider/'login.config.toml').write_text('model_provider="chatgpt"\n[model_providers.chatgpt]\nname="ChatGPT"\n'
        'base_url="https://chatgpt.com/backend-api/codex"\nwire_api="responses"\n[rax]\nchatgpt_auth="auth.json"\nnative_models=true\n')
    config = configuration(tmp_path)
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    deployed = deploy(config, provider_home=provider, profile='login', key_file=provider/'openrouter_key',
                      codex='/usr/bin/true')
    assert json.loads(deployed['relay_configuration'].read_text()) == {
        'base_url': 'https://chatgpt.com/backend-api/codex', 'headers': {}, 'model': MODEL, 'native_models': True,
        'chatgpt_auth': str((provider/'auth.json').resolve())}


def test_profile_model_is_the_sessions_only_model(tmp_path, monkeypatch, demo_cache):
    """A profile's [rax] model reaches Codex's config and the relay, which refuses any other."""
    def fake_storage(root, *_):
        path = root/'agent-storage'
        path.mkdir()
        return path
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision', fake_storage)
    provider = tmp_path/'provider'
    login = tmp_path/'elsewhere/auth.json'
    login.parent.mkdir()
    login.write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'access_token': 'h.e30.s', 'account_id': 'acct-fixture'}}))
    provider.mkdir()
    # An absolute login path: an account logged in under its own CODEX_HOME.
    (provider/'other.config.toml').write_text('model_provider="chatgpt"\n[model_providers.chatgpt]\nname="ChatGPT"\n'
        'base_url="https://chatgpt.com/backend-api/codex"\nwire_api="responses"\n[rax]\n'
        f'chatgpt_auth="{login}"\nnative_models=true\nmodel="openai/gpt-6.1-sol"\n')
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    deployed = deploy(configuration(tmp_path), provider_home=provider, profile='other', codex='/usr/bin/true')
    relay = json.loads(deployed['relay_configuration'].read_text())
    assert relay['model'] == 'openai/gpt-6.1-sol' and relay['chatgpt_auth'] == str(login.resolve())
    assert tomllib.loads((deployed['codex_home']/'config.toml').read_text())['model'] == 'openai/gpt-6.1-sol'
    # A resumed session and its agent-formal copies start their relay from this stored file.
    from harness.codex_cli.model_relay import ModelRelay
    ModelRelay(tmp_path/'model.sock', **relay)
    (provider/'bad.config.toml').write_text((provider/'other.config.toml').read_text().replace(
        'model="openai/gpt-6.1-sol"', 'model="bad model"'))
    with pytest.raises(ValueError, match="Invalid model in provider profile 'bad'"):
        deploy(configuration(tmp_path/'second'), provider_home=provider, profile='bad', codex='/usr/bin/true')


def deploy_profile(tmp_path, monkeypatch, demo_cache, profile, **kwargs):
    """deploy() with a provider profile's TOML text; returns the stored relay configuration."""
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision',
                        lambda root, *_: (root/'agent-storage').mkdir() or root/'agent-storage')
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    provider = tmp_path/'provider'
    provider.mkdir(parents=True, exist_ok=True)
    (provider/'test.config.toml').write_text(profile)
    deployed = deploy(configuration(tmp_path/'session'), provider_home=provider, profile='test', codex='/usr/bin/true',
                      **kwargs)
    return deployed['relay_configuration'].read_text()


def openrouter_key(tmp_path):
    path = tmp_path/'openrouter_key'
    path.write_text('sk-or-fixture\n')
    path.chmod(0o600)
    return path


EXAMPLE = ('model_provider="example"\n[model_providers.example]\nname="Example"\n'
           'base_url="https://api.example.com/v1"\nwire_api="responses"\n')


def test_an_env_key_profile_sends_its_own_key_and_never_the_openrouter_key(tmp_path, monkeypatch, demo_cache):
    monkeypatch.setenv('EXAMPLE_API_KEY', 'sk-example-fixture')
    stored = deploy_profile(tmp_path, monkeypatch, demo_cache, EXAMPLE+'env_key="EXAMPLE_API_KEY"\n',
                            key_file=openrouter_key(tmp_path))
    assert json.loads(stored)['headers'] == {'Authorization': 'Bearer sk-example-fixture'}
    assert 'sk-or-fixture' not in stored


def test_an_unset_env_key_is_named(tmp_path, monkeypatch, demo_cache):
    monkeypatch.delenv('EXAMPLE_API_KEY', raising=False)
    with pytest.raises(ValueError, match=r"Provider profile 'test' reads its key from \$EXAMPLE_API_KEY, which is not set"):
        deploy_profile(tmp_path, monkeypatch, demo_cache, EXAMPLE+'env_key="EXAMPLE_API_KEY"\n',
                       key_file=openrouter_key(tmp_path))


def test_a_profile_without_a_credential_gets_no_openrouter_key(tmp_path, monkeypatch, demo_cache):
    """A local model server: the OpenRouter key (kept for Gemini) never leaves for another provider."""
    stored = deploy_profile(tmp_path, monkeypatch, demo_cache, EXAMPLE, key_file=openrouter_key(tmp_path))
    assert json.loads(stored)['headers'] == {} and 'sk-or-fixture' not in stored


def test_the_openrouter_profile_reads_openrouter_api_key_without_a_key_file(tmp_path, monkeypatch, demo_cache):
    profile = ('model_provider="openrouter"\n[model_providers.openrouter]\nname="OpenRouter"\n'
               'base_url="https://openrouter.ai/api/v1"\nwire_api="responses"\n')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-env-fixture')
    stored = deploy_profile(tmp_path/'env', monkeypatch, demo_cache, profile, key_file=None)
    assert json.loads(stored)['headers'] == {'Authorization': 'Bearer sk-or-env-fixture'}
    stored = deploy_profile(tmp_path/'file', monkeypatch, demo_cache, profile, key_file=openrouter_key(tmp_path))
    assert json.loads(stored)['headers'] == {'Authorization': 'Bearer sk-or-fixture'}  # An explicit file wins.
    monkeypatch.delenv('OPENROUTER_API_KEY')
    with pytest.raises(ValueError, match='The OpenRouter profile needs a key'):
        deploy_profile(tmp_path/'none', monkeypatch, demo_cache, profile, key_file=None)


def test_the_openrouter_profile_needs_no_provider_file(tmp_path, monkeypatch, demo_cache):
    """A fresh host with only an OpenRouter key: the default profile is built in; any other
    profile without a provider table is still refused by name."""
    monkeypatch.setattr('harness.codex_cli.workspace_storage.provision',
                        lambda root, *_: (root/'agent-storage').mkdir() or root/'agent-storage')
    from services.controller.demonstrations import provision_demonstration
    monkeypatch.setattr('harness.codex_cli.auto_research.provision_demonstration',
        lambda config, workspace, _, **kw: provision_demonstration(config, workspace, demo_cache.parent, **kw))
    from harness.codex_cli.model_relay import MODEL
    empty = tmp_path/'provider'
    empty.mkdir()
    deployed = deploy(configuration(tmp_path/'session'), provider_home=empty, profile='openrouter',
                      codex='/usr/bin/true', key_file=openrouter_key(tmp_path))
    assert json.loads(deployed['relay_configuration'].read_text()) == {
        'base_url': 'https://openrouter.ai/api/v1', 'headers': {'Authorization': 'Bearer sk-or-fixture'}, 'model': MODEL}
    with pytest.raises(ValueError, match="Provider profile 'other' needs model_provider"):
        deploy(configuration(tmp_path/'other'), provider_home=empty, profile='other', codex='/usr/bin/true',
               key_file=openrouter_key(tmp_path))


def test_the_launcher_key_file_is_optional(tmp_path, monkeypatch):
    from harness.codex_cli import auto_research
    monkeypatch.setenv('HOME', str(tmp_path))
    assert auto_research.openrouter_key_file() is None  # The relay and Gemini then read OPENROUTER_API_KEY.
    default = tmp_path/'.codex/secrets/openrouter_api_key'
    default.parent.mkdir(parents=True)
    default.write_text('sk-or-fixture')
    assert auto_research.openrouter_key_file() == default
    assert auto_research.openrouter_key_file(tmp_path/'chosen') == tmp_path/'chosen'
    from harness.claude_cli.messages_relay import Credential
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-env-fixture')
    assert Credential('openrouter', key_file=None).headers() == {'Authorization': 'Bearer sk-or-env-fixture'}


def test_agent_binaries_must_be_native_executables(tmp_path):
    from harness.claude_cli.auto_research import claude_binary
    from harness.codex_cli.auto_research import native_binary
    wrapper = tmp_path/'codex'
    wrapper.write_text('#!/usr/bin/env node\nrequire("../lib/codex.js")\n')
    wrapper.chmod(0o755)
    with pytest.raises(RuntimeError, match='is not a native executable'):
        native_binary(wrapper, 'codex')
    with pytest.raises(RuntimeError, match='is not a native executable'):
        claude_binary(wrapper)
    with pytest.raises(RuntimeError, match='binary is required'):
        native_binary(tmp_path/'missing', 'codex')
    assert native_binary('/usr/bin/true', 'codex') == Path('/usr/bin/true').resolve()


def test_demo_context_validation_and_legacy_default(tmp_path):
    from dataclasses import asdict
    from services.controller.config import Configuration
    config = configuration(tmp_path)
    with pytest.raises(ValueError, match='demonstration_context'):
        replace(config, demonstration_context='video_url')
    legacy = asdict(config)
    legacy.pop('demonstration_context')
    assert Configuration.from_dict(legacy).demonstration_context == 'none'


def test_status_returns_only_trusted_demo_descriptor(supervisor, frontend):
    context = {'kind':'completion_sequence', 'image_count':3, 'root':'runtime/demonstrations',
        'manifest_path':'runtime/demonstrations/manifest.json'}
    (supervisor[0].config.root/'demonstration-context.json').write_text(json.dumps(context))
    assert data(frontend.call('status', {}))['demonstration_context'] == context


def test_startup_prompt_uses_deployed_draft_and_preserves_explicit_override(tmp_path):
    prompt = tmp_path/'START_PROMPT.md'
    prompt.write_text('Custom draft for this deployment.\n')
    assert startup_prompt(tmp_path) == 'Custom draft for this deployment.'
    prompt.unlink()
    assert startup_prompt(tmp_path, 'Diagnostic only; do not start an episode.') == 'Diagnostic only; do not start an episode.'


def fake_launch(tmp_path, monkeypatch, events, *, stopped=False):
    """The Codex launcher with Docker, the relay and the frontend replaced by recorders."""
    from types import SimpleNamespace
    from harness.codex_cli import auto_research
    config = configuration(tmp_path)
    config.root.mkdir(parents=True)
    provider = tmp_path/'relay.json'
    provider.write_text('{}')
    deployed = {'relay_configuration': provider, 'workspace': tmp_path,
                'codex_home': tmp_path, 'configuration': tmp_path/'config.json'}

    class Relay:
        def __init__(self, socket, **kwargs):
            assert kwargs['client_write_timeout'] == auto_research.CALL_SECONDS+300
            assert 'max_calls' not in kwargs  # Calls are counted, never capped.
            self.socket = socket

        def start(self):
            (self.socket.parent/'mcp.sock').touch()

        def close(self):
            events.append('relay closed')

    class Process:
        returncode = None
        terminated = False

        def __init__(self, command, **kwargs):
            self.args = command
            self.name = 'agent' if command[0].startswith('fake-agent') else 'server'
            events.append(self.name+' started')
            if self.name == 'agent':
                assert kwargs['stdin'] is subprocess.DEVNULL  # Codex exec would wait on an open stdin.
            self.stderr = iter([b'working\n', b'ERROR: Reconnecting... 1/2\n',
                                b'ERROR: Selected model is at capacity. Please try a different model.\n'])

        def wait(self, timeout=None):
            if self.name == 'agent' and not self.terminated:
                assert timeout is None  # A session has no wall-clock budget.
                events.append('unbounded wait')
                if stopped:
                    raise KeyboardInterrupt  # The operator's stop.
            self.returncode = 1 if self.name == 'agent' else 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            events.append(self.name+' terminated')

    monkeypatch.setattr(auto_research, 'DockerSandbox', lambda *a: SimpleNamespace(preflight=lambda: None))
    monkeypatch.setattr(auto_research.workspace_storage, 'validate', lambda *a: None)
    monkeypatch.setattr(auto_research, 'ModelRelay', Relay)
    monkeypatch.setattr(auto_research, 'container_command', lambda *a, prompt, **k: ['fake-agent', prompt])
    monkeypatch.setattr(auto_research.subprocess, 'Popen', Process)
    monkeypatch.setattr(auto_research.subprocess, 'run', lambda command, **k: events.append(command[:3]))
    return auto_research, config, deployed


@pytest.mark.parametrize('stopped', [False, True])
def test_launcher_waits_without_a_deadline_and_preserves_cleanup(tmp_path, monkeypatch, stopped):
    events = []
    auto_research, config, deployed = fake_launch(tmp_path, monkeypatch, events, stopped=stopped)
    if stopped:
        with pytest.raises(KeyboardInterrupt):
            auto_research.launch(config, deployed, prompt='test')
        assert 'agent terminated' in events
    else:
        assert auto_research.launch(config, deployed, prompt='test').returncode == 1
        # Codex's last ERROR line, the provider's message, is kept whole.
        assert deployed['agent_error'] == 'ERROR: Selected model is at capacity. Please try a different model.'
    assert 'unbounded wait' in events
    assert ['docker', 'rm', '--force'] in events
    assert 'server terminated' in events and 'relay closed' in events


def test_one_frontend_serves_every_agent_run_of_a_launch(tmp_path, monkeypatch):
    events = []
    auto_research, config, deployed = fake_launch(tmp_path, monkeypatch, events)
    prompts = []
    def agent_runs(run):
        prompts.append(run('first'))
        events.append('between runs')
        return run('second')
    assert auto_research.launch(config, deployed, prompt='first', agent_runs=agent_runs).returncode == 1
    assert events.count('server started') == 1 and events.count('agent started') == 2
    # The frontend, owner of the open episodes, is still up between the runs; each run starts
    # from a removed container of the same name; cleanup stops the frontend once, at the end.
    between = events.index('between runs')
    assert 'server terminated' not in events[:between] and 'relay closed' not in events[:between]
    assert events[between+1] == ['docker', 'rm', '--force'] and events[between+2] == 'agent started'
    assert events.index('server terminated') > events.index('agent started', between)


@pytest.mark.parametrize('options,interactive,prompt', [([], True, 'Draft startup.'),
    (['--non-interactive'], False, 'Draft startup.'), (['--exec-prompt', 'Diagnostic.'], False, 'Diagnostic.')])
def test_launcher_mode_and_durable_lifecycle(tmp_path, monkeypatch, options, interactive, prompt):
    from types import SimpleNamespace
    from harness.codex_cli import auto_research
    config = configuration(tmp_path)
    source = tmp_path/'input.json'
    source.write_text('{}')
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    (workspace/'START_PROMPT.md').write_text('Draft startup.\n')
    monkeypatch.setattr(auto_research.Configuration, 'from_dict', lambda _: config)
    monkeypatch.setattr(auto_research.Configuration, '__post_init__', lambda _: None)  # Local fixture storage.
    monkeypatch.setattr(auto_research, 'deploy', lambda *a, **k: {'workspace':workspace})
    monkeypatch.setattr(auto_research.signal, 'signal', lambda *a: None)
    calls = []
    def launch(*args, **kwargs):
        calls.append(kwargs)
        record = json.loads((config.root/'agent-lifecycle.json').read_text())
        assert record['status'] == 'running' and not record.get('finished_at')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(auto_research, 'launch', launch)
    monkeypatch.setattr(auto_research.sys, 'argv', ['auto-research', '--config', str(source), *options])
    with pytest.raises(SystemExit) as exit:
        auto_research.main()
    assert exit.value.code == 0
    assert calls[0]['interactive'] is interactive and calls[0]['prompt'] == prompt
    path = config.root/'agent-lifecycle.json'
    record = json.loads(path.read_text())
    assert record['status'] == 'exited' and record['finished_at'] >= record['started_at']
    with pytest.raises(FileExistsError):
        auto_research.main()
    assert json.loads(path.read_text()) == record


def test_each_agent_turn_is_told_of_episodes_refunded_since_the_last(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from harness.codex_cli import auto_research
    config = configuration(tmp_path)
    source = tmp_path/'input.json'
    source.write_text('{}')
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    (workspace/'START_PROMPT.md').write_text('Draft startup.\n')
    pool = config.root/'exploration'
    pool.mkdir(parents=True)
    rows = [{'episode': 1, 'env_id': 0, 'seed': 0, 'status': 'closed'},
            {'episode': 2, 'env_id': 1, 'seed': 1, 'status': 'active'}]  # Left live by the stopped frontend.
    (pool/'state.json').write_text(json.dumps({'schema': 1, 'env_count': 2, 'seeds': [0, 1, 2, 3],
                                               'episodes': rows, 'sealed': False}))
    monkeypatch.setattr(auto_research.Configuration, 'from_dict', lambda _: config)
    monkeypatch.setattr(auto_research.Configuration, '__post_init__', lambda _: None)  # Local fixture storage.
    monkeypatch.setattr(auto_research, 'deploy', lambda *a, **k: {'workspace': workspace})
    monkeypatch.setattr(auto_research.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(auto_research, 'latest_session', lambda home: 'thread')
    monkeypatch.setattr(auto_research, 'MAX_RELAUNCHES', 1)
    prompts = []
    def launch(*args, prompt, agent_runs, **kwargs):
        def run(prompt):
            prompts.append(prompt)
            state = json.loads((pool/'state.json').read_text())
            state['episodes'].append({'episode': 3, 'env_id': 0, 'seed': 2, 'status': 'error',
                                      'error': 'RuntimeError: worker exited (-9)'})  # Fails during the first turn.
            (pool/'state.json').write_text(json.dumps(state))
            return 0
        return SimpleNamespace(returncode=agent_runs(run))
    monkeypatch.setattr(auto_research, 'launch', launch)
    monkeypatch.setattr(auto_research.sys, 'argv', ['auto-research', '--config', str(source), '--non-interactive'])
    with pytest.raises(SystemExit) as exit:
        auto_research.main()
    assert exit.value.code == 0 and len(prompts) == 2
    first, second = prompts
    assert first.startswith('Draft startup.\n\nHarness note:') and 'episode 2 (interrupted: The session stopped while this episode was live)' in first
    assert 'episode 3' not in first
    assert second.startswith(auto_research.CONTINUE_PROMPT)
    assert 'episode 3 (error: RuntimeError: worker exited (-9))' in second and 'episode 2 ' not in second


def test_a_codex_error_resumes_the_conversation_against_the_same_frontend(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from harness.codex_cli import auto_research
    config = configuration(tmp_path)
    source = tmp_path/'input.json'
    source.write_text('{}')
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    (workspace/'START_PROMPT.md').write_text('Draft startup.\n')
    monkeypatch.setattr(auto_research.Configuration, 'from_dict', lambda _: config)
    monkeypatch.setattr(auto_research.Configuration, '__post_init__', lambda _: None)  # Local fixture storage.
    monkeypatch.setattr(auto_research, 'deploy', lambda *a, **k: {'workspace': workspace})
    monkeypatch.setattr(auto_research.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(auto_research, 'latest_session', lambda home: 'thread')
    monkeypatch.setattr(auto_research, 'MAX_RELAUNCHES', 0)
    monkeypatch.setattr(auto_research, 'ERROR_PAUSE_SECONDS', 0)
    error = 'ERROR: Selected model is at capacity. Please try a different model.'
    launches, prompts = [], []
    def launch(config, deployed, *, prompt, agent_runs, **kwargs):
        launches.append(prompt)
        def run(prompt):
            prompts.append((prompt, deployed.get('resume_session')))
            deployed['agent_error'] = error if len(prompts) == 1 else None
            return 1 if len(prompts) == 1 else 0
        return SimpleNamespace(returncode=agent_runs(run))
    monkeypatch.setattr(auto_research, 'launch', launch)
    monkeypatch.setattr(auto_research.sys, 'argv', ['auto-research', '--config', str(source), '--non-interactive'])
    with pytest.raises(SystemExit) as exit:
        auto_research.main()
    assert exit.value.code == 0 and launches == ['Draft startup.']  # One launch: one frontend.
    assert prompts == [('Draft startup.', None), (auto_research.ERROR_PROMPT.format(error=error), 'thread')]
    record = json.loads((config.root/'agent-lifecycle.json').read_text())
    assert [(e['returncode'], e['error']) for e in record['agent_errors']] == [(1, error)]


def agent_formal_launch(tmp_path, monkeypatch, *options):
    from harness.codex_cli import auto_research
    config = configuration(tmp_path)
    source = tmp_path/'input.json'
    source.write_text('{}')
    monkeypatch.setattr(auto_research.Configuration, 'from_dict', lambda _: config)
    monkeypatch.setattr(auto_research.Configuration, '__post_init__', lambda _: None)  # Local fixture storage.
    monkeypatch.setattr(auto_research.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(auto_research, 'redeploy', lambda c: {'workspace': tmp_path/'workspace'})
    monkeypatch.setattr(auto_research, 'launch', lambda *a, **k: pytest.fail('the exploring agent relaunched'))
    monkeypatch.setattr(auto_research.sys, 'argv', ['auto-research', '--config', str(source), '--agent-formal', *options])
    return auto_research, config


def test_agent_formal_flag_plays_live_copies_on_a_closed_session(tmp_path, monkeypatch):
    from services.agent_formal import checkpoint
    auto_research, config = agent_formal_launch(tmp_path, monkeypatch, '--resume')
    config.root.mkdir(parents=True)
    (config.root/'configuration.json').write_text('{}')  # Deployed when it was first launched.
    (config.root/'exploration-complete.json').write_text('{}')
    (config.root/'agent-lifecycle.json').write_text(json.dumps(
        {'pid': 1, 'started_at': 0, 'finished_at': 1, 'status': 'exited', 'formal': 'completed'}))
    calls = []
    monkeypatch.setattr(checkpoint, 'after_exploration', lambda c, d, **k: calls.append(k) or
                        {'status': 'completed', 'agent_formal': {'status': 'completed'}})
    with pytest.raises(SystemExit) as exit:
        auto_research.main()
    assert exit.value.code == 0 and [k['live_copies'] for k in calls] == [True]
    record = json.loads((config.root/'agent-lifecycle.json').read_text())
    assert record['status'] == 'exited' and record['agent_formal'] == 'completed' and len(record['attempts']) == 1


@pytest.mark.parametrize('options', [(), ('--resume',)])
def test_agent_formal_flag_needs_a_closed_session(tmp_path, monkeypatch, options):
    auto_research, config = agent_formal_launch(tmp_path, monkeypatch, *options)
    with pytest.raises(SystemExit, match='exploration has closed'):
        auto_research.main()


def test_invalid_lifecycle_arguments_name_the_field(supervisor, tmp_path):
    from services.controller.frontend import ResearchFrontend
    s, _ = supervisor
    f = ResearchFrontend(s, tmp_path/'workspace')
    try:
        with pytest.raises(ValueError, match='unexpected: env_id'):
            f.call('start_episode', {'env_id': 0})
        with pytest.raises(ValueError, match='missing: source'):
            f.call('register', {})
    finally:
        f.api.revoke()


def test_a_frontend_that_fails_to_start_reports_its_own_error(tmp_path, monkeypatch):
    """The launcher passes the frontend's error through whole (its exit code and traceback), not a
    pointer to its log."""
    events = []
    auto_research, config, deployed = fake_launch(tmp_path, monkeypatch, events)
    Process, Relay = auto_research.subprocess.Popen, auto_research.ModelRelay

    class Failing(Process):
        def __init__(self, command, **kwargs):
            super().__init__(command, **kwargs)
            if self.name == 'server':
                kwargs['stdout'].write(b"Traceback (most recent call last):\n  File \"frontend.py\"\n"
                                       b"ValueError: A trial's saved configuration cannot change\n")
                self.returncode = 1

    class Silent(Relay):
        def start(self):
            pass  # The frontend, which opens mcp.sock, never got that far.

    monkeypatch.setattr(auto_research.subprocess, 'Popen', Failing)
    monkeypatch.setattr(auto_research, 'ModelRelay', Silent)
    (config.root/'frontend.log').write_bytes(b'RuntimeError: the earlier launch\'s simulator error\n')
    with pytest.raises(RuntimeError) as raised:
        auto_research.launch(config, deployed, prompt='go')
    assert str(raised.value) == ("Host MCP startup failed: the frontend exited with code 1; frontend.log ends:\n"
                                 "Traceback (most recent call last):\n  File \"frontend.py\"\n"
                                 "ValueError: A trial's saved configuration cannot change")
    assert 'agent started' not in events
    # Each launch appends under its own header: the earlier launch's error stays readable.
    log = (config.root/'frontend.log').read_text().splitlines()
    assert log[0] == "RuntimeError: the earlier launch's simulator error" and log[1] == ''
    assert re.fullmatch(rf'===== launch \S+ \(launcher pid {os.getpid()}\) =====', log[2])
    assert log[3:] == ['Traceback (most recent call last):', '  File "frontend.py"',
                       "ValueError: A trial's saved configuration cannot change"]


def test_a_lifecycle_deadline_is_a_timeout_of_that_call(supervisor, frontend, monkeypatch):
    """The startup or evaluation wall limit (ExecutionDeadline, a BaseException) answers the
    call that hit it with a timeout error; the server keeps serving."""
    from services.controller.sandbox import ExecutionDeadline
    s, backend = supervisor
    def stuck(*, formal, seed):
        raise ExecutionDeadline('Execution wall-time budget exhausted')
    monkeypatch.setattr(backend, 'start', stuck)
    call = lambda name: frontend.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                         'params': {'name': name, 'arguments': {}}})
    reply = call('start_episode')
    assert reply['result']['isError']
    error = json.loads(reply['result']['content'][0]['text'])['error']
    assert (error['type'], error['operation'], error['stage']) == ('TimeoutError', 'start_episode', 'lifecycle')
    assert error['reason'] == ('start_episode did not finish within its wall-time limit (environment startup): '
                               'Execution wall-time budget exhausted')
    assert s.state['active'] is None
    status = call('status')
    assert 'error' not in status and not status['result'].get('isError')


def test_a_failed_start_is_kept_in_the_operator_record(supervisor, frontend, monkeypatch):
    """The agent's reply is not the only record: the result row, its published result.json
    and an mcp_error event in mcp.jsonl all keep the whole error."""
    s, backend = supervisor
    def broken(*, formal, seed):
        raise RuntimeError('simulator process exited with code -11; its log: CUDA error: out of memory')
    monkeypatch.setattr(backend, 'start', broken)
    reply = frontend.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                             'params': {'name': 'start_episode', 'arguments': {}}})
    reason = 'simulator process exited with code -11; its log: CUDA error: out of memory'
    assert json.loads(reply['result']['content'][0]['text'])['error']['reason'] == reason
    row = s.state['results'][-1]
    assert row['start_failure'] == {'type': 'RuntimeError', 'operation': 'start_episode',
                                    'stage': 'episode_start', 'reason': reason}
    [published] = [json.loads(p.read_text()) for p in (s.config.root/'published/runs').glob('interactive-*/result.json')]
    events = [json.loads(line) for line in (s.config.root/'mcp.jsonl').read_text().splitlines()]
    errors = [e for e in events if e['event'] == 'mcp_error']
    assert [(e['tool'], e['stage'], e['reason']) for e in errors] == [('start_episode', 'lifecycle', reason)]
    assert published['start_failure']['reason'] == reason
