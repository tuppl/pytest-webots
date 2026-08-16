"""
Webots process lifecycle: launch, readiness, reset, reload, shutdown.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings
    from .markers import WorldSpec

_URL_RE = re.compile(r"^(?:ipc|tcp)://\d+/(.+)$")
_TERMINATE_GRACE = 5.0


class WebotsError(RuntimeError):
    """
    Base error for Webots process failures.
    """


class WorldBootTimeout(WebotsError):
    """
    The world did not reach readiness within the startup timeout.
    """


class WebotsInstance:
    """
    A running (or restartable) Webots process for one world.
    """

    def __init__(self, spec: WorldSpec, settings: Settings, port: int) -> None:
        self.spec = spec
        self.settings = settings
        self.port = port
        self.robots: dict[str, str] = {}
        self.on_started: Callable[[], None] | None = None
        self.on_stopping: Callable[[], None] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._output: deque[str] = deque(maxlen=1000)
        self._lock = threading.Lock()

    @property
    def world(self) -> str:
        return str(self.spec.path)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def command(self) -> list[str]:
        cmd = [
            str(self.settings.webots_binary),
            "--batch",
            "--stdout",
            "--stderr",
            f"--mode={self.spec.mode or self.settings.mode}",
            f"--port={self.port}",
            "--extern-urls",
        ]
        if self.settings.headless:
            cmd += ["--no-rendering", "--minimize"]
        cmd += self.settings.extra_args
        cmd += self.spec.args
        cmd.append(str(self.spec.path))
        return cmd

    def boot(self) -> None:
        if self.alive:
            return
        self.robots.clear()
        self._output.clear()
        self._proc = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_output, name=f"webots-out-{self.port}", daemon=True)
        self._reader.start()
        self._wait_ready()
        if self.on_started is not None:
            self.on_started()

    def ensure_running(self) -> None:
        if not self.alive:
            self.boot()

    def _wait_ready(self) -> None:
        """
        Ready when the first extern controller URL appears on stdout.
        """
        timeout = self.spec.timeout if self.spec.timeout is not None else self.settings.startup_timeout
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.robots:
                return
            if not self.alive:
                raise WebotsError(f"Webots exited while booting {self.world}:\n{self.output()}")
            time.sleep(0.05)
        self.shutdown(force=True)
        raise WorldBootTimeout(f"world {self.world} did not become ready within {timeout:.0f}s:\n{self.output()}")

    def wait_for_robot(self, name: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                url = self.robots.get(name)
            if url is not None:
                return url
            if not self.alive:
                break
            time.sleep(0.05)
        raise WebotsError(
            f"robot {name!r} did not announce an extern controller URL "
            f"(discovered: {sorted(self.robots) or 'none'}):\n{self.output()}"
        )

    def _read_output(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            match = _URL_RE.match(line)
            if match:
                with self._lock:
                    self.robots[match.group(1)] = line
            elif line:
                self._output.append(line)

    def shutdown(self, force: bool = False) -> None:
        """
        Terminate the process; a no-op when already dead or under --webots-keep-alive.
        """
        if self.settings.keep_alive and not force:
            return
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            if self.on_stopping is not None:
                self.on_stopping()
            proc.terminate()
            try:
                proc.wait(_TERMINATE_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None
        self._proc = None

    def output(self) -> str:
        return "\n".join(self._output)
