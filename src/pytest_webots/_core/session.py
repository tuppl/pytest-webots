"""
Per-test session object handed to tests by the webots fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .controller import ControllerProcess
from .errors import WebotsError
from .markers import ControllerProtocol, ControllerSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .supervisor.proxy import SupervisorProxy
    from .world import WebotsInstance

# Lives beside agent.py: a directory with no module shadowing Webots' ``controller``.
_STUB_CONTROLLER = Path(__file__).parent / "supervisor" / "stub.py"


class AgentOps:
    """
    Method-style access to agent ops: ``webots.ops.step_until(threshold=0.01)``
    invokes the op named ``step_until`` with keyword arguments as request fields.
    """

    def __init__(self, instance: WebotsInstance) -> None:
        self._instance = instance

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**params: Any) -> Any:
            return self._instance.agent_op(name, params)

        call.__name__ = name
        return call


class WebotsSession:
    """
    What a test sees: the running world, the supervisor, and this test's controllers.
    """

    def __init__(self, instance: WebotsInstance, builder: Callable[[ControllerSpec], None] | None = None) -> None:
        self._instance = instance
        self._builder = builder
        self.ops = AgentOps(instance)
        self.controllers: dict[str, ControllerProcess] = {}
        self._pending: dict[str, ControllerSpec] = {}
        self.launch_attempted = False

    def __repr__(self) -> str:
        controllers = ", ".join(sorted(self.controllers)) or "none"
        return f"<WebotsSession {self._instance.spec} controllers={controllers}>"

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

    def agent_op(self, op: str, **params: Any) -> Any:
        """
        Invoke a supervisor-agent op, e.g. one registered by a webots_agent_plugins file.
        """
        return self._instance.agent_op(op, params)

    def setup_controllers(self, specs: list[ControllerSpec]) -> None:
        """
        Validate every robot name, build every spec, then launch autostart
        specs sequentially in declaration order.

        A declared controller is a requirement: builds run before any launch so
        a failure surfaces while nothing is connected. A launch failure
        terminates the controllers this call already launched, so no orphan
        stays attached to a world that outlives the test.
        """
        known = set(self._instance.robots)
        unknown = [spec.robot for spec in specs if spec.robot not in known]
        if unknown:
            raise WebotsError(
                f"no robot named {', '.join(repr(r) for r in unknown)} in {self._instance.world_path}; "
                f"extern robots available: {sorted(known) or 'none'}"
            )
        if self._builder is not None:
            for spec in specs:
                self._builder(spec)
        try:
            for spec in specs:
                if spec.autostart:
                    self.launch_attempted = True
                    self._launch(spec, build=False)
                else:
                    self._pending[spec.robot] = spec
        except BaseException:
            self.terminate_controllers()
            raise

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
                    f"no robot named {robot!r} in {self._instance.world_path}; "
                    f"extern robots available: {sorted(self._instance.robots) or 'none'}"
                )
        return self._launch(spec)

    def _launch(self, spec: ControllerSpec, build: bool = True) -> ControllerProcess:
        if build and self._builder is not None:
            self._builder(spec)
        process = ControllerProcess(spec, self._instance)
        process.start()
        self.controllers[spec.robot] = process
        return process

    def recrew_departed(self) -> list[ControllerProcess]:
        """
        Attach a stub controller to every robot whose controller this test
        launched and has since exited. Webots keeps a departed synchronous
        robot's slot open and blocks stepping until someone reconnects, so the
        teardown reset needs every such robot re-crewed.

        Stubs replace the dead entries in ``self.controllers``, so
        ``terminate_controllers`` reaps them.
        """
        if not self._instance.alive:
            return []
        stubs = []
        for robot, process in list(self.controllers.items()):
            if process.alive:
                continue
            try:
                stubs.append(self._launch(ControllerSpec(robot=robot, path=_STUB_CONTROLLER), build=False))
            except WebotsError:
                if self._instance.alive:
                    raise
                # Webots exited under the launch (e.g. a controller called
                # simulationQuit); nothing left to re-crew for.
                break
        return stubs

    def terminate_controllers(self) -> None:
        for process in self.controllers.values():
            process.terminate()
