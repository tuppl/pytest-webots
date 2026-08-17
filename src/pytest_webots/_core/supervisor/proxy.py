"""
Client-side transport and reflective proxy for the supervisor agent.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from typing import Any

from ..errors import WebotsError

_REQUEST_TIMEOUT = 30.0


class AgentConnectionError(WebotsError):
    """
    The agent socket failed; the caller decides whether that means a crash.
    """


class AgentError(WebotsError):
    """
    The agent reached Webots but the requested operation raised.
    """


class AgentClient:
    def __init__(self, address: str) -> None:
        self._address = address
        self._sock: socket.socket | None = None
        self._stream: Any = None

    def connect(self, timeout: float, abort: Callable[[], str | None] | None = None) -> None:
        """
        Retry until the agent answers a ping; ``abort`` may end the wait early
        by returning a failure description (e.g. "the agent process died").
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._sock = self._open_socket()
                self._sock.settimeout(_REQUEST_TIMEOUT)
                self._stream = self._sock.makefile("rw", encoding="utf-8")
                self.request({"op": "ping"})
                return
            except (OSError, AgentConnectionError):
                self.close()
                reason = abort() if abort is not None else None
                if reason is not None:
                    raise AgentConnectionError(reason) from None
                if time.monotonic() > deadline:
                    raise AgentConnectionError(f"could not reach supervisor agent at {self._address}") from None
                time.sleep(0.1)

    def _open_socket(self) -> socket.socket:
        if self._address.startswith("tcp:"):
            return socket.create_connection(("127.0.0.1", int(self._address[4:])), timeout=_REQUEST_TIMEOUT)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_REQUEST_TIMEOUT)
        sock.connect(self._address)
        return sock

    def request(self, payload: dict[str, Any], expect_disconnect: bool = False) -> Any:
        if self._stream is None:
            raise AgentConnectionError("agent connection is closed")
        try:
            self._stream.write(json.dumps(payload) + "\n")
            self._stream.flush()
            line = self._stream.readline()
        except OSError as error:
            if expect_disconnect:
                return None
            raise AgentConnectionError(f"agent connection failed during {payload.get('op')!r}: {error}") from error
        if not line:
            if expect_disconnect:
                return None
            raise AgentConnectionError(f"agent closed the connection during {payload.get('op')!r}")
        response = json.loads(line)
        if "error" in response:
            raise AgentError(f"{response['type']}: {response['error']}")
        return response["ok"]

    def close(self) -> None:
        for closable in (self._stream, self._sock):
            if closable is not None:
                try:
                    closable.close()
                except OSError:
                    pass
        self._stream = None
        self._sock = None


class SupervisorProxy:
    """
    Reflective stand-in for the Supervisor object living inside Webots.

    Attribute access returns a bound remote method; results that are Webots
    objects come back as child proxies against the agent's handle table.
    """

    def __init__(self, call: Callable[[int | None, str, list[Any]], Any], handle: int | None = None) -> None:
        self._call = call
        self._handle = handle

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any) -> Any:
            return self._call(self._handle, name, list(args))

        method.__name__ = name
        return method

    def __repr__(self) -> str:
        target = "Supervisor" if self._handle is None else f"handle {self._handle}"
        return f"<SupervisorProxy {target}>"


def encode_args(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for arg in args:
        if isinstance(arg, SupervisorProxy):
            out.append({"__handle__": arg._handle})
        elif isinstance(arg, (list, tuple)):
            out.append(encode_args(list(arg)))
        else:
            out.append(arg)
    return out


def decode_result(value: Any, call: Callable[[int | None, str, list[Any]], Any]) -> Any:
    if isinstance(value, dict) and "__handle__" in value:
        return SupervisorProxy(call, handle=value["__handle__"])
    if isinstance(value, list):
        return [decode_result(v, call) for v in value]
    return value
