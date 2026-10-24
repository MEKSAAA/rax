import os
from pathlib import Path

import pytest

from services import storage_root

# The artifact root this host is configured with, before the session fixture below replaces it.
# Docker and GPU integration tests write real sessions there.
HOST_ARTIFACT_ROOT = storage_root.artifact_root()


@pytest.fixture(scope='session', autouse=True)
def artifact_root_is_pytest_tmp(tmp_path_factory):
    """Every test's artifacts live under pytest's temporary directory, whatever the host's storage
    layout, so the production rule that session roots sit under the artifact root holds for tmp_path."""
    root = tmp_path_factory.getbasetemp().resolve()
    previous = os.environ.get(storage_root.ENVIRONMENT)
    os.environ[storage_root.ENVIRONMENT] = str(root)
    yield root
    if previous is None:
        os.environ.pop(storage_root.ENVIRONMENT, None)
    else:
        os.environ[storage_root.ENVIRONMENT] = previous


@pytest.fixture
def host_artifact_root(monkeypatch):
    """For integration tests that write real sessions under the host's own artifact root."""
    monkeypatch.setenv(storage_root.ENVIRONMENT, str(HOST_ARTIFACT_ROOT))
    return HOST_ARTIFACT_ROOT


@pytest.fixture(autouse=True)
def no_real_claude_login(tmp_path_factory, monkeypatch):
    """Tests never read or check the operator's Claude experiment token."""
    monkeypatch.setenv('RAX_CLAUDE_EXPERIMENT_DIR', str(tmp_path_factory.mktemp('claude-experiment')))
