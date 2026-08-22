"""
Shared subprocess handling: termination, output capture, controller environment.

Webots, the supervisor agent and every extern controller are all spawned,
drained and reaped the same way; keeping that in one place is what stops the
three lifecycles from drifting apart.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from .config import python_controller_path

if TYPE_CHECKING:
    from pathlib import Path

TERMINATE_GRACE = 5.0
EXIT_GRACE = 5.0  # long enough for a quitting process to finish exiting
_READER_JOIN = 2.0


def terminate(
    proc: subprocess.Popen[str] | None,
    kill: Callable[[], None] | None = None,
    grace: float = TERMINATE_GRACE,
) -> None:
    """
    Ask the process to stop, then insist.

    ``kill`` overrides the hard-kill step for processes that need more than
    SIGKILL on the direct child — Webots is a wrapper script on Linux, so its
    whole group has to go.
    """
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        if kill is not None:
            kill()
        else:
            proc.kill()
        proc.wait()


def exit_code(proc: subprocess.Popen[str] | None, grace: float) -> int | None:
    """
    The process's exit code, or None while it is still running.

    A closing socket or pipe races the process actually exiting, so polling the
    instant a failure is noticed reports a process on its way out as still
    running. Waiting out the grace period is what separates the two.
    """
    if proc is None:
        return None
    try:
        return proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        return None


def controller_env(home: Path, url: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    Environment for a Python extern controller: the bundled ``controller``
    package on PYTHONPATH, ahead of anything already there.
    """
    env = os.environ.copy()
    if extra:
        env.update(extra)
    env["WEBOTS_HOME"] = str(home)
    env["WEBOTS_CONTROLLER_URL"] = url
    bundled = str(python_controller_path(home))
    env["PYTHONPATH"] = bundled + os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else bundled
    return env


class OutputReader:
    """
    Drains a process's stdout on a daemon thread into a bounded buffer.

    ``on_line`` sees every line before it is stored, so a caller can parse the
    stream (URLs, connection notices) without a second reader.
    """

    def __init__(self, name: str, maxlen: int = 1000, on_line: Callable[[str], bool] | None = None) -> None:
        self._name = name
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._on_line = on_line
        self._thread: threading.Thread | None = None

    def start(self, proc: subprocess.Popen[str]) -> None:
        self._lines.clear()
        self._thread = threading.Thread(target=self._drain, args=(proc,), name=self._name, daemon=True)
        self._thread.start()

    def _drain(self, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:  # pragma: no cover - always a pipe here
            return
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if self._on_line is not None and not self._on_line(line):
                continue  # the parser consumed it
            if line:
                self._lines.append(line)

    def join(self, timeout: float = _READER_JOIN) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def append(self, line: str) -> None:
        """
        Add a line the plugin itself is reporting, alongside the process's own.
        """
        self._lines.append(line)

    def clear(self) -> None:
        self._lines.clear()

    def text(self) -> str:
        return "\n".join(self._lines)
