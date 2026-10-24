"""Stdlib MCP client for robot and Gemini calls from Python.

`Context()` connects ordinary Python to the same MCP tools you call directly.
`main()` instead runs a controller's `main(ctx)` over standard input and output, with
the robot and Gemini tools only; there, Python prints are logged and native or
subprocess output goes to stderr.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import socket

_channel_lock = threading.RLock()
# Session-wide tools: status, Gemini and the benchmark's pure computation tools, which need no
# episode or environment (the registry's unscoped_tools in services/benchmarks.py).
SESSION_TOOLS = frozenset({'gemini_generate', 'status'})


class PrintLog:
    """Keep Python prints separate from both MCP results and stderr."""
    encoding = "utf-8"

    def __init__(self, channel):
        self.channel = channel

    def write(self, text):
        with _channel_lock:
            for offset in range(0, len(text), 8192):
                self.channel.write(json.dumps({"jsonrpc": "2.0", "method": "logs/write",
                    "params": {"text": text[offset:offset + 8192]}}) + "\n")
            self.channel.flush()
        return len(text)

    def flush(self):
        with _channel_lock:
            self.channel.flush()

    def isatty(self):
        return False

    def fileno(self):
        return 1  # Native/subprocess writes are safely captured on stderr.


class Context:
    def __init__(self, reader=None, writer=None):
        """Connect ordinary Python to the MCP tools, or use the given reader and writer."""
        self._socket = None
        self._request_lock = threading.RLock()
        if reader is None and writer is None:
            self._socket = socket.socket(socket.AF_UNIX)
            try:
                self._socket.connect(os.environ['RAX_MCP_SOCKET'])
                reader, writer = self._socket.makefile('r'), self._socket.makefile('w')
            except BaseException:
                self._socket.close()
                raise
        self._reader, self._writer, self._sequence = reader, writer, 0
        if self._socket:
            try:
                self.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                             'clientInfo': {'name': 'python-controller', 'version': '2'}})
            except BaseException:
                self.close()
                raise

    def close(self):
        if self._socket:
            self._writer.close()
            self._reader.close()
            self._socket.close()
            self._socket = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def request(self, method, params):
        with self._request_lock:
            return self._request(method, params)

    def env(self, env_id):
        """A context whose robot and episode calls go to exploration environment `env_id`."""
        if type(env_id) is not int or env_id < 0:
            raise ValueError('env_id must be a nonnegative integer')
        return Environment(self, env_id)

    def _request(self, method, params):
        self._sequence += 1
        request = {"jsonrpc": "2.0", "id": self._sequence, "method": method, "params": params}
        self._writer.write(json.dumps(request, allow_nan=False) + "\n")
        self._writer.flush()
        line = self._reader.readline(32 * 1024 * 1024)
        response = json.loads(line)
        if response.get("id") != self._sequence:
            raise RuntimeError("MCP request failed: identity mismatch")
        if "error" in response:
            raise RuntimeError("MCP tool failed: " + json.dumps({"error": response["error"]}, allow_nan=False))
        return response["result"]

    def call(self, tool, **arguments):
        result = self.request("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            # Same detail as a JSON-RPC error, so a controller sees the same
            # failure text whichever context runs it.
            text = next((b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"), "")
            raise RuntimeError("MCP tool failed: " + text)
        return result


class Environment:
    def __init__(self, context, env_id):
        self.context, self.env_id = context, env_id

    def call(self, tool, **arguments):
        # Pure computations, Gemini and status are session-wide, not robot-slot operations.
        if tool not in SESSION_TOOLS:
            # Bundle tools are session-wide; the robot tools and the episode tools are per environment.
            if tool in {'register', 'rehearse', 'submit'}:
                raise ValueError(f'{tool} is not environment-scoped; call it on the session Context')
            if 'env_id' in arguments:
                raise ValueError('Environment is already bound')
            arguments['env_id'] = self.env_id
        return self.context.call(tool, **arguments)


def main():
    mode, project, entrypoint = sys.argv[1:]
    # Keep the protocol on a private non-inherited descriptor. Native libraries
    # and subprocesses writing fd 1 cannot corrupt MCP: their output goes to the
    # captured stderr stream instead.
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), 1)
    sys.stdout = PrintLog(channel)
    sys.path.insert(0, project)  # The sandbox starts it in the workspace.
    spec = importlib.util.spec_from_file_location("submitted_controller", Path(project) / entrypoint)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = Context(sys.stdin, channel)
    context.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "python-controller", "version": "2"}})
    module.main(context)


if __name__ == "__main__":
    main()
