"""
Per-test session object handed to tests by the webots fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .controller import ControllerProcess
from .errors import WebotsError
from .markers import ControllerProtocol, ControllerSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .supervisor.proxy import SupervisorProxy
    from .world import WebotsInstance


class WebotsSession:
    """
    What a test sees: the running world, the supervisor, and this test's controllers.
    """

    def __init__(self, instance: WebotsInstance, builder: Callable[[ControllerSpec], None] | None = None) -> None:
        self._instance = instance
        self._builder = builder
        self.controllers: dict[str, ControllerProcess] = {}
        self._pending: dict[str, ControllerSpec] = {}

    @property
    def world(self) -> WebotsInstance:
        return self._instance

    @property
    def port(self) -> int:
        return self._instance.port

    @property
    def supervisor(self) -> SupervisorProxy:
        return self._instance.supervisor

    @property
    def logs(self) -> str:
        return self._instance.output()

    def step(self, ms: int | None = None) -> int:
        return self._instance.step(ms)

    def reset(self) -> None:
        self._instance.reset()

    def reload(self) -> None:
        self._instance.reload()

    def sim_time(self) -> float:
        return self._instance.sim_time()

    def setup_controllers(self, specs: list[ControllerSpec]) -> None:
        """
        Validate robot names, then launch autostart specs sequentially in declaration order.
        """
        known = set(self._instance.robots)
        unknown = [spec.robot for spec in specs if spec.robot not in known]
        if unknown:
            raise WebotsError(
                f"no robot named {', '.join(repr(r) for r in unknown)} in {self._instance.world}; "
                f"extern robots available: {sorted(known) or 'none'}"
            )
        for spec in specs:
            if spec.autostart:
                self._launch(spec)
            else:
                self._pending[spec.robot] = spec

    def launch_controller(
        self,
        robot: str,
        path: str | Path | None = None,
        *,
        args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        protocol: ControllerProtocol = "ipc",
        ip_address: str | None = None,
    ) -> ControllerProcess:
        """
        Launch a controller mid-test; with no path, start a declared autostart=False spec.
        """
        if path is None:
            spec = self._pending.pop(robot, None)
            if spec is None:
                raise WebotsError(f"no pending webots_controller spec for robot {robot!r}; pass a path")
        else:
            spec = ControllerSpec(
                robot=robot,
                path=Path(path).resolve(),
                args=tuple(args),
                env=dict(env or {}),
                cwd=cwd,
                protocol=protocol,
                ip_address=ip_address,
            )
            if robot not in self._instance.robots:
                raise WebotsError(
                    f"no robot named {robot!r} in {self._instance.world}; "
                    f"extern robots available: {sorted(self._instance.robots) or 'none'}"
                )
        return self._launch(spec)

    def _launch(self, spec: ControllerSpec) -> ControllerProcess:
        if self._builder is not None:
            self._builder(spec)
        process = ControllerProcess(spec, self._instance)
        process.start()
        self.controllers[spec.robot] = process
        return process

    def terminate_controllers(self) -> None:
        for process in self.controllers.values():
            process.terminate()
