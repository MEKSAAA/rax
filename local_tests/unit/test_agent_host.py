"""What the agent CLI's sandbox hides: the operator's credentials and what the project links to."""
import os

import pytest

from harness import agent_host


def files(root, *relatives):
    for relative in relatives:
        (root/relative).parent.mkdir(parents=True, exist_ok=True)
        (root/relative).write_text('x')


@pytest.fixture
def host(tmp_path, monkeypatch):
    home, project = tmp_path/'home', tmp_path/'project'
    project.mkdir()
    monkeypatch.setattr(agent_host, 'HOME', home)
    monkeypatch.setattr(agent_host, 'PROJECT', project)
    return home, project


def hidden(tmp_path, carved=False):
    (tmp_path/'sessions/s1').mkdir(parents=True, exist_ok=True)
    return set(agent_host.hidden_paths(tmp_path/'sessions/s1', carved=carved))


def test_the_operators_credentials_are_hidden_but_not_the_codex_install(tmp_path, host):
    home, _ = host
    files(home, '.codex/auth.json', '.codex/accounts/second.json', '.codex/secrets/openrouter_api_key',
          '.codex/openrouter.config.toml', '.codex/history.jsonl', '.codex/sessions/2026/10/rollout.jsonl',
          '.codex/packages/standalone/bin/codex', '.codex-other/auth.json', '.codex-other/config.toml',
          '.config/gh/hosts.yml', '.config/rax/claude-experiment.token', '.claude/.credentials.json',
          '.ssh/id_ed25519', '.netrc', '.config/unrelated/settings.json')
    paths = hidden(tmp_path)
    # Whole directories, so the list stays short: ~/.codex covers its logins, account switcher,
    # keys, provider profiles and transcripts.
    for relative in ('.codex', '.codex-other/auth.json', '.config/gh', '.config/rax', '.claude', '.ssh',
                     '.netrc'):
        assert (home/relative).resolve() in paths, relative
    for relative in ('.codex-other/config.toml', '.config/unrelated', '.config'):
        assert (home/relative).resolve() not in paths, relative
    assert (tmp_path/'sessions').resolve() in paths  # Every session's directory.
    # Codex re-runs its own binary in the sandbox: its install is readable again inside ~/.codex.
    assert agent_host.reexposed_paths() == [(home/'.codex/packages').resolve()]


def test_both_clis_read_the_codex_install_inside_the_hidden_codex_home(tmp_path, host):
    """Claude Code re-allows reading it inside the hidden ~/.codex; Codex's sandbox cannot, so for
    Codex ~/.codex is hidden entry by entry, all but the install."""
    from harness.claude_cli import auto_research as claude
    from harness.codex_cli import auto_research as codex
    from test_controller_backend import configuration
    home, _ = host
    files(home, '.codex/auth.json', '.codex/sessions/rollout.jsonl', '.codex/packages/standalone/bin/codex')
    (home/'.codex/elsewhere').symlink_to(tmp_path/'elsewhere')  # Not part of ~/.codex: never hidden for it.
    config = configuration(tmp_path/'sessions')  # Beside the home, as on a real host.
    workspace = config.root/'agent-workspace'
    workspace.mkdir(parents=True)
    deployed = {'workspace': workspace}
    codex_home = (home/'.codex').resolve()
    filesystem = codex.permissions(config, deployed, tmp_path/'s/mcp.sock')['filesystem']
    hidden = {path for path, mode in filesystem.items() if mode == 'deny' and path.startswith(str(codex_home))}
    assert hidden == {str(codex_home/'auth.json'), str(codex_home/'sessions')}
    sandbox = claude.settings(config, deployed, tmp_path/'s/mcp.sock')['sandbox']['filesystem']
    assert str(codex_home) in sandbox['denyRead'] and str(codex_home/'packages') in sandbox['allowRead']


def test_the_runtime_is_hidden_but_its_python_environments(tmp_path, host):
    """Whole for Claude Code, which re-allows reading the environments inside it; entry by entry
    for Codex. A runtime inside the checkout (not a link) is carved out of the repository alike."""
    _, project = host
    runtime = tmp_path/'storage/runtime'
    for name in ('envs/host/bin', 'huggingface-cache', 'auto-research'):
        (runtime/name).mkdir(parents=True)
    (project/'runtime').symlink_to(runtime)
    envs = (runtime/'envs').resolve()
    assert runtime.resolve() in hidden(tmp_path) and agent_host.reexposed_paths() == [envs]
    carved = hidden(tmp_path, carved=True)
    assert {(runtime/'huggingface-cache').resolve(), (runtime/'auto-research').resolve()} <= carved
    assert not any(envs.is_relative_to(path) for path in carved)
    (project/'runtime').unlink()
    runtime.rename(project/'runtime')
    carved = hidden(tmp_path, carved=True)
    assert (project/'runtime/huggingface-cache').resolve() in carved
    assert not any((project/'runtime/envs').resolve().is_relative_to(path) for path in carved)


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads unreadable directories')
def test_project_links_the_operator_cannot_read_are_skipped_not_fatal(tmp_path, host):
    _, project = host
    locked = tmp_path/'locked'
    (locked/'assets').mkdir(parents=True)
    (project/'assets').symlink_to(locked/'assets')
    locked.chmod(0)
    try:
        paths = hidden(tmp_path)
    finally:
        locked.chmod(0o700)
    assert (locked/'assets').resolve() not in paths  # No more readable to the agent than to the operator.


@pytest.mark.skipif(os.geteuid() == 0, reason='root lists unreadable directories')
def test_a_runtime_that_cannot_be_listed_is_hidden_whole(tmp_path, host):
    _, project = host
    runtime = tmp_path/'runtime'
    (runtime/'envs').mkdir(parents=True)
    (project/'runtime').symlink_to(runtime)
    runtime.chmod(0o100)  # Searchable, not listable: its secrets cannot be told from its environments.
    try:
        paths = hidden(tmp_path, carved=True)
    finally:
        runtime.chmod(0o700)
    assert runtime.resolve() in paths
