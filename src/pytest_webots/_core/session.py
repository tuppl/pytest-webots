"""
Per-test session object handed to tests by the webots fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .supervisor.proxy import SupervisorProxy
    from .world import WebotsInstance


class WebotsSession:
    """
    What a test sees: the running world, the supervisor, and, in later phases, controllers.
    """

    def __init__(self, instance: WebotsInstance) -> None:
        self._instance = instance

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
