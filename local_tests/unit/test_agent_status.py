"""Agent-visible status in the auto-research MCP."""
from dataclasses import replace

from services.controller.frontend import ResearchFrontend
from test_controller_backend import Backend, Runner, source, supervisor  # noqa: F401


def test_status_reports_timeout_time_bundles_and_formal_allowance(supervisor, tmp_path):  # noqa: F811
    s, backend = supervisor
    object.__setattr__(s.config, 'formal', replace(s.config.formal, wall_seconds=45))
    front = ResearchFrontend(s, tmp_path/'workspace')
    ident, manifest = s.register(source(tmp_path))
    status = front.status()
    assert status['episode_timeout_seconds'] == 45
    assert 'development_seconds_remaining' not in status
    assert status['bundles'] == {ident: {'project_directory': 'code/controller',
                                         'sha256': manifest['sha256'], 'rehearsal_qualified': False}}
    assert status['api_budget']['phase'] == 'development'
    assert status['formal_api_budget']['phase'] == 'formal'
    backend.success = True
    s.run(ident, formal=False)
    assert front.status()['bundles'][ident]['rehearsal_qualified'] is True
    assert 'development_seconds_remaining' not in front.status()
    assert 'development_reserved_seconds' not in s.state


def test_status_reports_the_episode_limit_and_no_session_budget(supervisor, tmp_path):  # noqa: F811
    s, _ = supervisor
    status = ResearchFrontend(s, tmp_path/'workspace').status()
    assert status['episode_timeout_seconds'] == s.config.formal.wall_seconds
    assert 'session_timeout_seconds' not in status
