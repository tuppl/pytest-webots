"""
Per-test session object handed to tests by the webots fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .world import WebotsInstance


class WebotsSession:
    """
    What a test sees: the running world and, in later phases, controllers and the supervisor.
    """

    def __init__(self, instance: WebotsInstance) -> None:
        self._instance = instance

    @property
    def world(self) -> WebotsInstance:
        return self._instance

    @property
    def port(self) -> int:
        return self._instance.port
