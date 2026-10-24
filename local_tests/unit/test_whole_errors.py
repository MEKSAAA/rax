"""Records keep an error's whole message, not only its type name."""
import json
import types
import uuid

from services.controller import supervisor as sup
from services.controller.progress_report import detail
from services.controller.recording import RunRecording
from bench import BENCH


def recording(tmp_path):
    return RunRecording(tmp_path, 'autonomous-' + uuid.uuid4().hex)


def test_a_failed_boundary_observation_keeps_the_gateway_error(tmp_path):
    s = object.__new__(sup.Supervisor)
    s.config = types.SimpleNamespace(benchmark=BENCH.name)
    s.gateway = types.SimpleNamespace(handle=lambda token, request: {
        'error': {'code': -32000, 'message': 'Simulator exited with code -11'}})
    s._recording = recording(tmp_path)
    s._boundary('end', 'token')
    expected = 'RuntimeError: Boundary observation failed: Simulator exited with code -11'
    assert s._recording.errors == [f'end_observation: {expected}']
    trace = json.loads((s._recording.root/'mcp.jsonl').read_text())
    assert (trace['event'], trace['error']) == ('boundary_unavailable', expected)


def test_recording_errors_keep_the_message(tmp_path):
    output, bundle = tmp_path/'output', tmp_path/'bundle'
    output.mkdir(), bundle.mkdir()
    (output/'stdout.log').write_text('out')
    (output/'stderr.log').mkdir()
    errors = recording(tmp_path).finish(bundle, output, {})['recording_errors']
    assert errors[0].startswith('stderr.log: IsADirectoryError: [Errno 21] Is a directory')
    assert errors[1] == "logs.json: FileNotFoundError: [Errno 2] No such file or directory: 'logs.json'"


def test_the_progress_report_shows_a_failure_record_as_type_and_reason(tmp_path):
    row = {'formal_outcome': 'interrupted', 'error': {
        'type': 'FileNotFoundError', 'operation': 'formal', 'stage': 'worker_result',
        'reason': "[Errno 2] No such file or directory: 'state.json'"}}
    assert detail(row, tmp_path) == "FileNotFoundError: [Errno 2] No such file or directory: 'state.json'"
