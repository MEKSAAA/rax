"""Stopping a session ends its launcher, frontend and simulator; none is left running."""
import signal
import subprocess
import sys

import pytest

from test_controller_backend import supervisor
from services.controller import frontend as frontend_module
from services.controller.frontend import ResearchFrontend
from harness.codex_cli import auto_research

AGENT = 'rax-agent-' + 'f'*32
REHEARSE = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'rehearse', 'arguments': {'bundle': 'b1'}}}


@pytest.fixture
def rehearsing(supervisor, tmp_path, monkeypatch):
    """A frontend whose agent container the launcher removes while a rehearsal holds it paused."""
    s, _ = supervisor
    f = ResearchFrontend(s, tmp_path/'agent', agent_container=AGENT)
    s.state['bundles']['b1'] = {'directory': 'b1'}
    monkeypatch.setattr(frontend_module, 'verify', lambda *a: None)
    docker = []
    def run(command, check=False, **kwargs):
        docker.append(command[1])
        if command[1] == 'unpause':  # The container is gone.
            if check:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(frontend_module.subprocess, 'run', run)
    yield s, f, docker
    f.close()


def test_a_stop_during_a_rehearsal_is_not_swallowed_by_the_failed_unpause(rehearsing, monkeypatch):
    s, f, docker = rehearsing
    def rehearsal(bundle, *, formal):
        f.stopping = True  # What the frontend's SIGTERM handler does before raising.
        raise SystemExit(0)
    monkeypatch.setattr(s, 'run', rehearsal)
    with pytest.raises(SystemExit):
        f.handle(REHEARSE)
    assert docker == ['pause', 'unpause']


def test_a_stop_ends_the_frontend_even_when_tool_cleanup_raises_instead(rehearsing, monkeypatch):
    s, f, _ = rehearsing
    def rehearsal(bundle, *, formal):
        f.stopping = True
        raise RuntimeError('environment shutdown failed')  # Replaced the stop's SystemExit.
    monkeypatch.setattr(s, 'run', rehearsal)
    with pytest.raises(SystemExit):
        f.handle(REHEARSE)


def test_a_failed_unpause_after_a_normal_rehearsal_is_still_reported(rehearsing, monkeypatch):
    s, f, docker = rehearsing
    monkeypatch.setattr(s, 'run', lambda bundle, *, formal: {'reason': 'episode_ended'})
    reply = f.handle(REHEARSE)
    assert reply['result']['isError'] and 'CalledProcessError' in reply['result']['content'][0]['text']
    assert docker == ['pause', 'unpause']


def test_a_repeated_stop_does_not_abort_launcher_cleanup(monkeypatch):
    def stop(*_):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    agent = subprocess.Popen(['sleep', '60'])
    # A frontend that takes a while to close its simulator after SIGTERM, then exits cleanly.
    server = subprocess.Popen([sys.executable, '-c', 'import signal, sys, time\n'
        'signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1), sys.exit(3)))\n'
        'print("ready", flush=True)\ntime.sleep(60)'], stdout=subprocess.PIPE, text=True)
    assert server.stdout.readline() == 'ready\n'
    events = []
    def run(command, **kwargs):
        events.append(command[:3])
        signal.raise_signal(signal.SIGTERM)  # The operator clicks Stop again during cleanup.
    monkeypatch.setattr(auto_research.subprocess, 'run', run)
    class Relay:
        def close(self):
            events.append('relay closed')
    try:
        with pytest.raises(KeyboardInterrupt):
            auto_research.stop_services(AGENT, agent, server, Relay())
        assert events == [['docker', 'rm', '--force'], 'relay closed']
        assert agent.returncode == -signal.SIGTERM
        assert server.returncode == 3  # Waited for its own shutdown; never killed mid-cleanup.
        assert signal.getsignal(signal.SIGTERM) is stop
    finally:
        signal.signal(signal.SIGTERM, previous)
        for process in (agent, server):
            if process.poll() is None:
                process.kill()
                process.wait()


def test_every_cleanup_step_runs_after_an_earlier_one_fails(monkeypatch):
    events = []
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(auto_research.subprocess, 'run', run)
    class Process:
        def __init__(self, name):
            self.name, self.returncode = name, None
        def poll(self):
            return self.returncode
        def terminate(self):
            events.append(self.name+' terminated')
        def wait(self, timeout):
            if self.name == 'agent':
                raise subprocess.TimeoutExpired(self.name, timeout)
            events.append(f'{self.name} waited {timeout}s')
            self.returncode = 0
        def kill(self):
            events.append(self.name+' killed')
    class Relay:
        def close(self):
            events.append('relay closed')
    with pytest.raises(subprocess.TimeoutExpired):
        auto_research.stop_services(AGENT, Process('agent'), Process('server'), Relay())
    assert events == ['agent terminated', 'agent killed', 'server terminated',
                      f'server waited {auto_research.SERVER_STOP_SECONDS}s', 'relay closed']


def test_a_closed_terminal_during_cleanup_is_held_like_a_repeated_stop():
    seen = []
    def stop(number, _):
        seen.append(number)
    previous = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        with auto_research.deferred_sigterm():
            for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGHUP):
                signal.raise_signal(number)
            assert seen == []  # Cleanup is not interrupted.
        assert seen == [signal.SIGHUP, signal.SIGTERM]  # Delivered once each afterwards.
        assert signal.getsignal(signal.SIGHUP) is stop and signal.getsignal(signal.SIGTERM) is stop
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class OnlySyscall:
    """A libc older than glibc 2.36: no pidfd wrappers, only syscall(2)."""
    def __init__(self, libc):
        self.syscall = libc.syscall


@pytest.mark.parametrize('route', ['python', 'libc', 'syscall'])
def test_stop_pins_the_launcher_through_a_pidfd_on_every_route(tmp_path, monkeypatch, route):
    """Bundled Pythons may lack os.pidfd_open and older libcs the wrappers; the raw system calls
    still pin the process, and a numeric-PID kill is never used."""
    import ctypes
    import json
    import os
    from harness import session_control
    libc = ctypes.CDLL(None, use_errno=True)
    if route == 'libc' and not hasattr(libc, 'pidfd_open'):
        pytest.skip('host libc has no pidfd wrappers')
    if route != 'python':
        monkeypatch.delattr(os, 'pidfd_open', raising=False)
    if route == 'syscall':
        monkeypatch.setattr(session_control.ctypes, 'CDLL', lambda *a, **k: OnlySyscall(libc))
    monkeypatch.setattr(os, 'kill', lambda *a: pytest.fail('numeric-PID kill'))
    child = subprocess.Popen(['sleep', '60'])
    try:
        (tmp_path/'agent-lifecycle.json').write_text(json.dumps({'pid': child.pid}))
        result = session_control.stop(tmp_path, is_alive=lambda directory, lifecycle: lifecycle['pid'] == child.pid)
        assert result['status'] == 'stop requested; waiting for cleanup'
        assert child.wait(timeout=10) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_the_syscall_route_uses_the_linux_numbers_and_reports_failures(monkeypatch):
    import os
    import types
    from harness import session_control
    calls = []
    class Syscall:
        restype = None
        def __call__(self, number, *args):
            calls.append((number, [getattr(arg, 'value', arg) for arg in args]))
            return {434: 7, 424: 0}[number] if len(calls) < 3 else -1
    monkeypatch.delattr(os, 'pidfd_open', raising=False)
    open_pid, send = session_control.pidfd_calls(types.SimpleNamespace(syscall=Syscall()))
    assert open_pid(4242) == 7 and send(7, signal.SIGTERM) == 0
    assert calls == [(434, [4242, 0]), (424, [7, signal.SIGTERM, None, 0])]
    with pytest.raises(OSError, match='Cannot pin launcher identity'):
        open_pid(4242)


@pytest.mark.parametrize('route', ['python', 'syscall'])
def test_leftover_simulators_are_terminated_without_the_libc_wrappers(monkeypatch, route):
    """The exploration pool's cleanup of a dead worker's simulators pins them the same way, so
    a libc older than glibc 2.36 still stops them instead of leaving them on the GPU."""
    import ctypes
    import os
    from harness import session_control
    from services.exploration import process
    libc = ctypes.CDLL(None, use_errno=True)
    if route == 'syscall':
        monkeypatch.delattr(os, 'pidfd_open', raising=False)
        monkeypatch.setattr(session_control.ctypes, 'CDLL', lambda *a, **k: OnlySyscall(libc))
    monkeypatch.setattr(os, 'kill', lambda *a: pytest.fail('numeric-PID kill'))
    child = subprocess.Popen(['sleep', '60'])
    try:
        record = process.identity(child.pid)
        process.terminate(record)
        assert child.wait(timeout=10) == -signal.SIGTERM
        with pytest.raises(ValueError, match='No matching live process'):
            process.terminate(record)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
