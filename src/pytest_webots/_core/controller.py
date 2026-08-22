"""
Extern controller processes: launch, log capture, terminate, restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import controller_launcher
from .errors import WebotsError
from .process import OutputReader, controller_env, terminate

if TYPE_CHECKING:
    from .config import Settings
    from .markers import ControllerSpec
    from .world import WebotsInstance

_EXIT_LOG_GRACE = 1.0


class ControllerProcess:
    """
    A running extern controller process for one robot.

    Python controllers run under the current interpreter with the bundled
    ``controller`` package on PYTHONPATH; anything else goes through the
    ``webots-controller`` launcher, which knows the other languages.
    """

    def __init__(self, spec: ControllerSpec, instance: WebotsInstance) -> None:
        self.spec = spec
        self._instance = instance
        self._proc: subprocess.Popen[str] | None = None
        self._reader = OutputReader(f"controller-out-{spec.robot}")

    def __repr__(self) -> str:
        if self._proc is None:
            state = "not started"
        elif self.alive:
            state = f"running pid={self._proc.pid}"
        else:
            state = f"exited {self.returncode}"
        return f"<ControllerProcess {self.spec.robot!r} {state}>"

    @property
    def robot(self) -> str:
        return self.spec.robot

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self._proc is None else self._proc.poll()

    @property
    def logs(self) -> str:
        return self._reader.text()

    def controller_url(self) -> str:
        if self.spec.protocol == "tcp":
            host = self.spec.ip_address or "127.0.0.1"
            return f"tcp://{host}:{self._instance.port}/{self.spec.robot}"
        return f"ipc://{self._instance.port}/{self.spec.robot}"

    def command(self) -> list[str]:
        settings = self._instance.settings
        if self.spec.path.suffix == ".py":
            cmd = [sys.executable, str(self.spec.path)]
        else:
            cmd = [
                str(controller_launcher(self._require_home(settings))),
                f"--protocol={self.spec.protocol}",
                f"--port={self._instance.port}",
                f"--robot-name={self.spec.robot}",
            ]
            if self.spec.protocol == "tcp":
                cmd.append(f"--ip-address={self.spec.ip_address or '127.0.0.1'}")
            cmd.append(str(self.spec.path))
        return cmd + list(self.spec.args)

    def environment(self) -> dict[str, str]:
        if self.spec.path.suffix != ".py":
            # webots-controller sets the language's own paths up itself.
            return os.environ | dict(self.spec.env)
        home = self._require_home(self._instance.settings)
        return controller_env(home, self.controller_url(), dict(self.spec.env))

    def start(self) -> None:
        if self.alive:
            return
        snapshot = self._instance.connection_generation(self.spec.robot)
        if not self.spec.path.exists():
            raise WebotsError(
                f"controller {self.spec.path} does not exist and no build produced it; "
                f"check the path, or that a build is configured and not disabled by "
                f"--webots-no-build or build=False"
            )
        self._proc = subprocess.Popen(
            self.command(),
            cwd=str(self.spec.cwd or self.spec.path.parent),
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader.start(self._proc)
        try:
            self._wait_connected(snapshot)
        except BaseException:
            self.terminate()
            raise

    def _wait_connected(self, snapshot: int) -> None:
        robot = self.spec.robot
        timeout = self._instance.settings.startup_timeout
        deadline = time.monotonic() + timeout
        died_at: float | None = None
        while time.monotonic() < deadline:
            if self._instance.connection_generation(robot) > snapshot:
                return
            if not self.alive:
                if died_at is None:
                    died_at = time.monotonic()
                elif time.monotonic() - died_at > _EXIT_LOG_GRACE:
                    raise WebotsError(
                        f"controller for robot {robot!r} exited with code {self.returncode} "
                        f"before connecting:\n{self.logs}"
                    )
            if not self._instance.alive:
                raise WebotsError(
                    f"Webots exited while the controller for {robot!r} was connecting:\n{self._instance.output()}"
                )
            time.sleep(0.05)
        raise WebotsError(f"controller for robot {robot!r} did not connect within {timeout:.0f}s:\n{self.logs}")

    def terminate(self) -> None:
        terminate(self._proc)
        self._reader.join()

    def restart(self) -> None:
        self.terminate()
        self._proc = None
        self.start()

    @staticmethod
    def _require_home(settings: Settings) -> Path:
        home = settings.home
        if home is None:
            raise WebotsError("no Webots installation found")
        return home
