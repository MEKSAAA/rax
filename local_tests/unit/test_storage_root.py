"""The artifact storage root: RAX_ARTIFACT_ROOT, else the runtime link's target."""
from dataclasses import replace
from pathlib import Path

import pytest

from services import storage_root


def test_environment_selects_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv(storage_root.ENVIRONMENT, str(tmp_path))
    assert storage_root.artifact_root() == tmp_path.resolve()


def test_without_the_variable_the_runtime_link_target_is_the_root(tmp_path, monkeypatch):
    """bootstrap_sources.sh links runtime/ to the chosen storage; sessions created there validate."""
    monkeypatch.delenv(storage_root.ENVIRONMENT, raising=False)
    project, target = tmp_path/'project', tmp_path/'fast'/'runtime'
    project.mkdir()
    target.mkdir(parents=True)
    (project/'runtime').symlink_to(target)
    monkeypatch.setattr(storage_root, 'PROJECT', project)
    assert storage_root.artifact_root() == target.resolve()
    assert storage_root.require_artifact_path(project/'runtime'/'auto-research'/'s') == target.resolve()/'auto-research'/'s'
    with pytest.raises(ValueError, match='Controller artifacts must be under'):
        storage_root.require_artifact_path(tmp_path/'elsewhere')


def test_without_the_variable_or_a_runtime_link_there_is_no_root(tmp_path, monkeypatch):
    monkeypatch.delenv(storage_root.ENVIRONMENT, raising=False)
    monkeypatch.setattr(storage_root, 'PROJECT', tmp_path)
    with pytest.raises(RuntimeError, match=f"set {storage_root.ENVIRONMENT} or create a 'runtime' symlink in {tmp_path}"):
        storage_root.artifact_root()


def test_configuration_accepts_sessions_under_a_configured_root(tmp_path, monkeypatch):
    from test_controller_backend import configuration
    base = configuration(tmp_path)  # Built under the tests' artifact root (conftest).
    monkeypatch.setenv(storage_root.ENVIRONMENT, str(tmp_path))
    config = replace(base, root=tmp_path/'auto-research'/'session')
    assert config.root == tmp_path/'auto-research'/'session'
    with pytest.raises(ValueError, match='Controller artifacts must be under'):
        replace(config, root=Path('/elsewhere/session'))
