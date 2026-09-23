"""
Extern supervisor controller run inside Webots.

Standalone script: imports only the Webots-bundled ``controller`` package and
the standard library, never pytest_webots itself. Launched as
``agent.py <socket-address> [plugin-file ...]``; speaks newline-delimited JSON
over that socket.

Plugin files export ``register(agent)`` and add ops to the dispatch table:

    def register(agent):
        @agent.op("ball_height")
        def ball_height(agent, request):
            return agent.supervisor.getFromDef(request["ball"]).getPosition()[2]

Handlers receive the decoded request (handles already resolved to objects) and
return plain Python; results are encoded centrally, so returning a Webots
object hands the client a proxy while scalars, lists and dicts cross as
themselves. ``__handle__`` is reserved as the proxy marker and must not appear
as a key in returned data. Built-in names are reserved; a plugin claiming one
raises, but plugins may replace ops registered by earlier plugins.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any


def _op_ping(agent: Agent, request: dict[str, Any]) -> Any:
    return "pong"


def _op_step(agent: Agent, request: dict[str, Any]) -> Any:
    ms = request.get("ms") or agent.basic_time_step
    return agent.supervisor.step(int(ms))


def _op_reset(agent: Agent, request: dict[str, Any]) -> Any:
    agent.supervisor.simulationReset()
    agent.supervisor.step(agent.basic_time_step)
    agent.supervisor.simulationResetPhysics()
    return None


def _op_reset_physics(agent: Agent, request: dict[str, Any]) -> Any:
    agent.supervisor.simulationResetPhysics()
    return None


def _op_reload(agent: Agent, request: dict[str, Any]) -> Any:
    agent.supervisor.worldReload()
    agent.supervisor.step(agent.basic_time_step)
    return None


def _op_quit(agent: Agent, request: dict[str, Any]) -> Any:
    agent.supervisor.simulationQuit(int(request.get("status") or 0))
    agent.supervisor.step(agent.basic_time_step)
    return None


def _op_time(agent: Agent, request: dict[str, Any]) -> Any:
    return agent.supervisor.getTime()


def _op_basic_time_step(agent: Agent, request: dict[str, Any]) -> Any:
    return agent.basic_time_step


def _settle_paused(sup: Any) -> None:
    last = sup.getTime()
    agreements = 0
    for _ in range(75):
        time.sleep(0.04)
        now = sup.getTime()
        agreements = agreements + 1 if now == last else 0
        last = now
        if agreements >= 2:
            return


def _op_set_mode(agent: Agent, request: dict[str, Any]) -> Any:
    modes = {
        "pause": agent.supervisor.SIMULATION_MODE_PAUSE,
        "realtime": agent.supervisor.SIMULATION_MODE_REAL_TIME,
        "fast": agent.supervisor.SIMULATION_MODE_FAST,
    }
    mode = request["mode"]
    if mode not in modes:
        raise ValueError(f"unknown simulation mode: {mode} (valid: {', '.join(sorted(modes))})")
    agent.supervisor.simulationSetMode(modes[mode])
    if mode == "pause":
        _settle_paused(agent.supervisor)
    return None


def _op_advance_to(agent: Agent, request: dict[str, Any]) -> Any:
    sup = agent.supervisor
    modes = {"realtime": sup.SIMULATION_MODE_REAL_TIME, "fast": sup.SIMULATION_MODE_FAST}
    target = float(request["target"])
    sup.simulationSetMode(modes[request.get("mode") or "fast"])
    try:
        while sup.getTime() < target:
            if sup.step(agent.basic_time_step) == -1:
                break  # the simulation ended under us; report where it stopped
    finally:
        sup.simulationSetMode(sup.SIMULATION_MODE_PAUSE)
        _settle_paused(sup)
    return sup.getTime()


def _op_call(agent: Agent, request: dict[str, Any]) -> Any:
    target = agent.supervisor if request.get("target") is None else agent.handles[request["target"]]
    method = getattr(target, request["method"])
    return method(*(request.get("args") or []))


def _op_release(agent: Agent, request: dict[str, Any]) -> Any:
    agent.handles.pop(request["handle"], None)
    return None


_BUILTIN_OPS: dict[str, Callable[[Agent, dict[str, Any]], Any]] = {
    "ping": _op_ping,
    "step": _op_step,
    "reset": _op_reset,
    "reset_physics": _op_reset_physics,
    "reload": _op_reload,
    "quit": _op_quit,
    "time": _op_time,
    "basic_time_step": _op_basic_time_step,
    "set_mode": _op_set_mode,
    "advance_to": _op_advance_to,
    "call": _op_call,
    "release": _op_release,
}


class Agent:
    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor
        self.basic_time_step = int(supervisor.getBasicTimeStep())
        self.handles: dict[int, Any] = {}
        self.next_handle = 1
        self.ops: dict[str, Callable[[Agent, dict[str, Any]], Any]] = dict(_BUILTIN_OPS)

    def op(self, name: str) -> Callable[[Callable[[Agent, dict[str, Any]], Any]], Callable[..., Any]]:
        if name in _BUILTIN_OPS:
            raise ValueError(
                f"cannot replace built-in op {name!r}; the plugin must pick another name. "
                f"Built-ins are: {', '.join(sorted(_BUILTIN_OPS))}"
            )

        def register(handler: Callable[[Agent, dict[str, Any]], Any]) -> Callable[..., Any]:
            self.ops[name] = handler
            return handler

        return register

    def encode(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [self.encode(v) for v in value]
        if isinstance(value, dict):
            return {key: self.encode(v) for key, v in value.items()}
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = value
        return {"__handle__": handle}

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict) and "__handle__" in value:
            return self.handles[value["__handle__"]]
        if isinstance(value, dict):
            return {key: self.decode(v) for key, v in value.items()}
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        return value

    def dispatch(self, request: dict[str, Any]) -> Any:
        op = request["op"]
        handler = self.ops.get(op)
        if handler is None:
            raise ValueError(f"unknown op: {op} (available: {', '.join(sorted(self.ops))})")
        return handler(self, request)


def load_plugins(agent: Agent, paths: list[str]) -> None:
    for index, path in enumerate(paths):
        spec = importlib.util.spec_from_file_location(f"pytest_webots_agent_plugin_{index}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load agent plugin {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.register(agent)


def make_server(address: str) -> socket.socket:
    if address.startswith("tcp:"):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", int(address[4:])))
    else:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(address)
    server.listen(1)
    return server


def serve(agent: Agent, server: socket.socket) -> None:
    while True:
        conn, _ = server.accept()
        with conn, conn.makefile("rw", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                request = json.loads(line)
                try:
                    decoded = {key: agent.decode(value) for key, value in request.items()}
                    response = {"ok": agent.encode(agent.dispatch(decoded))}
                except Exception as error:  # noqa: BLE001 - deliver errors to the client, keep serving
                    response = {"error": traceback.format_exc(), "type": type(error).__name__}
                stream.write(json.dumps(response) + "\n")
                stream.flush()


def main() -> None:
    from controller import Supervisor  # type: ignore[import-not-found]  # via PYTHONPATH set by the launcher

    supervisor = Supervisor()
    agent = Agent(supervisor)
    try:
        load_plugins(agent, sys.argv[2:])
    except Exception:  # noqa: BLE001 - any plugin failure must reach the captured output
        traceback.print_exc()
        sys.exit(3)
    server = make_server(sys.argv[1])
    serve(agent, server)


if __name__ == "__main__":
    main()
