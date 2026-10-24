"""Stopping a session ends its launcher, frontend and simulator; none is left running."""
import signal
import subprocess
import sys
import time

import pytest

from test_controller_backend import supervisor
from services.controller import frontend as frontend_module
from services.controller.frontend import ResearchFrontend
from harness import agent_host
from harness.codex_cli import auto_research

REHEARSE = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'rehearse', 'arguments': {'bundle': 'b1'}}}


def state(pid):
    stat = open(f'/proc/{pid}/stat').read()
    return stat[stat.rindex(')')+2]


def thawed(pid):
    """The state of a process continued with SIGCONT, once the kernel has run it again."""
    deadline = time.monotonic() + 5
    while state(pid) in 'TR' and time.monotonic() < deadline:
        time.sleep(.005)
    return state(pid)


@pytest.fixture
def rehearsing(supervisor, tmp_path, monkeypatch):
    """A frontend whose live agent process a rehearsal freezes."""
    s, _ = supervisor
    agent = subprocess.Popen(['sleep', '60'])
    agent_host.record(agent, tmp_path/'agent.pid')
    f = ResearchFrontend(s, tmp_path/'agent', agent_pid_file=tmp_path/'agent.pid')
    s.state['bundles']['b1'] = {'directory': 'b1'}
    monkeypatch.setattr(frontend_module, 'verify', lambda *a: None)
    yield s, f, agent
    f.close()
    agent.kill()
    agent.wait()


def test_a_stop_during_a_rehearsal_ends_the_frontend_and_thaws_the_agent(rehearsing, monkeypatch):
    s, f, agent = rehearsing
    seen = []
    def rehearsal(bundle, *, formal):
        seen.append(state(agent.pid))
        f.stopping = True  # What the frontend's SIGTERM handler does before raising.
        raise SystemExit(0)
    monkeypatch.setattr(s, 'run', rehearsal)
    with pytest.raises(SystemExit):
        f.handle(REHEARSE)
    assert seen == ['T'] and thawed(agent.pid) == 'S'


def test_a_stop_ends_the_frontend_even_when_tool_cleanup_raises_instead(rehearsing, monkeypatch):
    s, f, agent = rehearsing
    def rehearsal(bundle, *, formal):
        f.stopping = True
        raise RuntimeError('environment shutdown failed')  # Replaced the stop's SystemExit.
    monkeypatch.setattr(s, 'run', rehearsal)
    with pytest.raises(SystemExit):
        f.handle(REHEARSE)
    assert thawed(agent.pid) == 'S'


def test_a_rehearsal_whose_agent_already_exited_still_runs(rehearsing, monkeypatch):
    s, f, agent = rehearsing
    agent.kill()
    agent.wait()
    monkeypatch.setattr(s, 'run', lambda bundle, *, formal: {'reason': 'episode_ended'})
    reply = f.handle(REHEARSE)
    assert not reply['result'].get('isError') and 'episode_ended' in reply['result']['content'][0]['text']


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
    stop_agent = agent_host.stop
    def agent_stop(process):
        signal.raise_signal(signal.SIGTERM)  # The operator clicks Stop again during cleanup.
        stop_agent(process)
    monkeypatch.setattr(agent_host, 'stop', agent_stop)
    try:
        with pytest.raises(KeyboardInterrupt):
            auto_research.stop_services(agent, server)
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
    class Process:
        def __init__(self, name):
            self.name, self.returncode = name, None
        def poll(self):
            return self.returncode
        def terminate(self):
            events.append(self.name+' terminated')
        def wait(self, timeout):
            events.append(f'{self.name} waited {timeout}s')
            self.returncode = 0
    def agent_stop(process):
        events.append('agent stop failed')
        raise subprocess.TimeoutExpired('agent', 10)
    monkeypatch.setattr(agent_host, 'stop', agent_stop)
    with pytest.raises(subprocess.TimeoutExpired):
        auto_research.stop_services(Process('agent'), Process('server'))
    assert events == ['agent stop failed', 'server terminated',
                      f'server waited {auto_research.SERVER_STOP_SECONDS}s']


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
