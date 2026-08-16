"""
Extern supervisor controller run inside Webots.

Standalone script: imports only the Webots-bundled ``controller`` package and
the standard library, never pytest_webots itself. Launched with the socket
address as its only argument; speaks newline-delimited JSON over that socket.
"""

from __future__ import annotations

import json
import socket
import sys
import traceback
from typing import Any


class Agent:
    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor
        self.basic_time_step = int(supervisor.getBasicTimeStep())
        self.handles: dict[int, Any] = {}
        self.next_handle = 1

    def encode(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [self.encode(v) for v in value]
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = value
        return {"__handle__": handle, "__type__": type(value).__name__}

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict) and "__handle__" in value:
            return self.handles[value["__handle__"]]
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        return value

    def dispatch(self, request: dict[str, Any]) -> Any:
        op = request["op"]
        if op == "ping":
            return "pong"
        if op == "step":
            ms = request.get("ms") or self.basic_time_step
            return self.supervisor.step(int(ms))
        if op == "reset":
            # simulationReset applies at the end of a step; step to land it, then kill inertia.
            self.supervisor.simulationReset()
            self.supervisor.step(self.basic_time_step)
            self.supervisor.simulationResetPhysics()
            return None
        if op == "reset_physics":
            self.supervisor.simulationResetPhysics()
            return None
        if op == "reload":
            self.supervisor.worldReload()
            self.supervisor.step(self.basic_time_step)
            return None
        if op == "quit":
            self.supervisor.simulationQuit(int(request.get("status") or 0))
            self.supervisor.step(self.basic_time_step)
            return None
        if op == "time":
            return self.supervisor.getTime()
        if op == "basic_time_step":
            return self.basic_time_step
        if op == "call":
            target = self.supervisor if request.get("target") is None else self.handles[request["target"]]
            method = getattr(target, request["method"])
            args = [self.decode(a) for a in request.get("args") or []]
            return self.encode(method(*args))
        if op == "release":
            self.handles.pop(request["handle"], None)
            return None
        raise ValueError(f"unknown op: {op}")


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
                    response = {"ok": agent.dispatch(request)}
                except Exception as error:  # noqa: BLE001 - deliver errors to the client, keep serving
                    response = {"error": traceback.format_exc(), "type": type(error).__name__}
                stream.write(json.dumps(response) + "\n")
                stream.flush()


def main() -> None:
    from controller import Supervisor  # type: ignore[import-not-found]  # via PYTHONPATH set by the launcher

    supervisor = Supervisor()
    server = make_server(sys.argv[1])
    serve(Agent(supervisor), server)


if __name__ == "__main__":
    main()
