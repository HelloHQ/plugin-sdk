"""A test double of the HelloHQ app's Tier-1 sidecar host, speaking the real NDJSON protocol.

It mirrors what the app (HelloHQ/hellohq) does for a sidecar run, so the shipped
plugin.py is exercised the way a production build runs it:

* launch: ``python -I -c <bootstrap> plugin.py`` with no PYTHONPATH and an
  allowlisted environment, from a scratch working directory. The bootstrap is
  the app's network audit hook + ``runpy.run_path`` (lib/app/utils/service/
  plugin_sidecar_sandbox.dart ``pythonAuditHookBootstrap``), plus a recording
  audit hook for these tests.
* context: the bridge pre-fetches each granted read into
  ``context[<permission id>]`` and omits a denied one (plugin_sidecar_bridge.dart);
  ``read:portfolio_names`` is a list of ``{"id", "name"}``.
* protocol: wait for ``ready`` (10 s), send ``{"id", "function": "run",
  "args": {"context", "input"}}`` with the runner's first input
  ``{"function": "main", "args": {}}``, read the reply (30 s), ping, shutdown
  (exit within 3 s) (plugin_sidecar_service.dart).

Every message in both directions is validated against plugin-protocol's
sidecar schemas: ``lifecycle.schema.json`` or ``host-calls.schema.json`` for a
typed message, ``envelope.schema.json`` for an RPC message. A host call (any other typed message) fails the run: Hello
Python must not make one.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_FIRST_INPUT = {"function": "main", "args": {}}

STARTUP_TIMEOUT = 10.0
CALL_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 3.0

# The app's bootstrap (network audit hook, then run the script as __main__),
# with a second, recording hook in front: every audited file open outside the
# interpreter's own install, and every socket, process and file-system-change event is
# written to stderr as "HQAUDIT <event> <arg>" for the tests to inspect.
BOOTSTRAP = r"""
import sys, runpy, os
_HQ_ROOTS = tuple({os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)})
_HQ_SCRIPT = os.path.realpath(sys.argv[1])
_HQ_WATCH = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
             "urllib.", "http.", "ctypes.", "shutil.", "os.remove", "os.rename", "os.mkdir", "os.chdir")
def _hq_record(event, args):
    if event == "open":
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            real = os.path.realpath(os.fsdecode(path))
            if real == _HQ_SCRIPT or real.startswith(_HQ_ROOTS):
                return
        sys.__stderr__.write("HQAUDIT open %r\n" % (path,))
    elif event.startswith(_HQ_WATCH):
        sys.__stderr__.write("HQAUDIT %s\n" % event)
sys.addaudithook(_hq_record)
_HQ_BLOCKED = (
    "socket.connect", "socket.bind", "socket.getaddrinfo",
    "socket.gethostbyname", "socket.gethostbyaddr", "socket.sethostname",
    "socket.sendto", "socket.sendmsg",
)
def _hq_net_guard(event, args):
    if event in _HQ_BLOCKED:
        raise PermissionError("HelloHQ plugin sandbox: direct network access is blocked.")
sys.addaudithook(_hq_net_guard)
_hq_script = sys.argv[1]
sys.argv = [_hq_script]
runpy.run_path(_hq_script, run_name="__main__")
"""


def _protocol_dir() -> Path:
    env = os.environ.get("HELLOHQ_PLUGIN_PROTOCOL_DIR")
    for root in ([Path(env)] if env else []) + [
        ROOT.parents[2] / "plugin-protocol",
        ROOT.parents[3] / "plugin-protocol",
    ]:
        if (root / "sidecar" / "envelope.schema.json").is_file():
            return root / "sidecar"
    if os.environ.get("HELLOHQ_REQUIRE_PROTOCOL") == "1":
        pytest.fail("plugin-protocol checkout not found (set HELLOHQ_PLUGIN_PROTOCOL_DIR)")
    pytest.skip("plugin-protocol checkout not found (set HELLOHQ_PLUGIN_PROTOCOL_DIR)")


class ProtocolValidator:
    def __init__(self) -> None:
        jsonschema = pytest.importorskip("jsonschema")
        d = _protocol_dir()
        self.lifecycle = jsonschema.Draft202012Validator(json.loads((d / "lifecycle.schema.json").read_text()))
        self.envelope = jsonschema.Draft202012Validator(json.loads((d / "envelope.schema.json").read_text()))
        self.host_calls = jsonschema.Draft202012Validator(json.loads((d / "host-calls.schema.json").read_text()))

    def check(self, msg: dict[str, Any], direction: str) -> None:
        if "type" in msg:
            # A typed message is a lifecycle message or a host call / host-call reply.
            if self.lifecycle.is_valid(msg) or self.host_calls.is_valid(msg):
                return
            errors = [e.message for e in self.lifecycle.iter_errors(msg)]
        else:
            errors = [e.message for e in self.envelope.iter_errors(msg)]
        assert not errors, f"{direction} message violates the protocol schema: {msg} -> {errors}"


def app_context(names: list[str] | None) -> dict[str, Any]:
    """The bridge's context snapshot: ``None`` = read:portfolio_names not granted (key omitted)."""
    if names is None:
        return {}
    return {"read:portfolio_names": [{"id": f"ptf_{i}", "name": n} for i, n in enumerate(names)]}


@dataclass
class SidecarRun:
    reply: dict[str, Any]
    from_plugin: list[dict[str, Any]] = field(default_factory=list)
    to_plugin: list[dict[str, Any]] = field(default_factory=list)
    audit: list[str] = field(default_factory=list)
    exit_code: int | None = None


def run_sidecar(plugin: Path, context: dict[str, Any], tmp_path: Path, *, input: Any = APP_FIRST_INPUT) -> SidecarRun:
    validator = ProtocolValidator()
    cwd = tmp_path / "run"
    cwd.mkdir(exist_ok=True)
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP") if k in os.environ}
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", BOOTSTRAP, str(plugin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    run = SidecarRun(reply={})

    def send(msg: dict[str, Any]) -> None:
        validator.check(msg, "host -> plugin")
        run.to_plugin.append(msg)
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def receive(timeout: float) -> dict[str, Any]:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"sidecar sent nothing within {timeout}s") from None
        assert line is not None, f"sidecar exited early: {proc.stderr.read()}"
        msg = json.loads(line)
        assert isinstance(msg, dict), line
        validator.check(msg, "plugin -> host")
        run.from_plugin.append(msg)
        return msg

    try:
        ready = receive(STARTUP_TIMEOUT)
        assert ready["type"] == "ready", ready
        send({"id": 1, "function": "run", "args": {"context": context, "input": input}})
        while True:
            msg = receive(CALL_TIMEOUT)
            if msg.get("id") == 1:
                run.reply = msg
                break
            # Hello Python makes no host call; events are discarded for a declarative plugin.
            assert msg.get("type") == "event", f"unexpected host call from the plugin: {msg}"
        send({"type": "ping", "seq": 7})
        assert receive(CALL_TIMEOUT) == {"type": "pong", "seq": 7}
        send({"type": "shutdown"})
        run.exit_code = proc.wait(timeout=SHUTDOWN_TIMEOUT)
        stderr = proc.stderr.read()
        run.audit = [line[len("HQAUDIT ") :] for line in stderr.splitlines() if line.startswith("HQAUDIT ")]
        assert lines.get(timeout=1) is None, "plugin wrote after its reply"
        return run
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
