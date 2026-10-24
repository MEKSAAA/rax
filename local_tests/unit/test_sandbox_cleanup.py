"""Every sandbox cleanup step runs when an earlier one fails, and docker's error stays whole."""
import re
import subprocess
from types import SimpleNamespace

import pytest

from services.controller import sandbox
from services.controller.config import Limits


def test_a_hung_docker_kill_still_unmounts_and_removes_the_container(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command[:2])
        if command[1] == 'kill':
            raise subprocess.TimeoutExpired(command, kwargs.get('timeout'))
        if command[1] == 'rm':
            return SimpleNamespace(returncode=1, stderr=b'Error response from daemon: removal in progress')
        raise AssertionError(command)
    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    monkeypatch.setattr(sandbox.workspace_storage, 'helper', lambda *a, **k: None)  # Mount never appears.
    bundle = tmp_path/'bundle'
    bundle.mkdir()
    runner = sandbox.DockerSandbox('sha256:' + 'a' * 64, Limits(60, 512, 1, 32, 32, 4 * 1024 * 1024))
    output = tmp_path/'run'
    with pytest.raises(RuntimeError, match='Output mount did not propagate') as raised:
        runner.run(bundle, mode='rehearsal', entrypoint='controller.py', output=output)
    assert calls == [['docker', 'kill'], ['docker', 'rm']]  # The kill timeout did not skip removal.
    [note] = raised.value.__notes__
    assert re.fullmatch(r'Controller sandbox cleanup failed: RuntimeError: docker rm --force '
                        r'rax-controller-[0-9a-f]{32} failed \(exit 1\): '
                        r'Error response from daemon: removal in progress', note)
    assert (output/'logs.json').is_file() and not (output/'writable').exists()
