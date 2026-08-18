"""
Process table for running Webots instances and port allocation.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from .ports import PortAllocator
from .world import WebotsInstance

if TYPE_CHECKING:
    from .config import Settings
    from .markers import WorldSpec


def _worker_offset(worker_id: str | None) -> int:
    if worker_id is None:
        return 0
    match = re.search(r"(\d+)$", worker_id)
    return int(match.group(1)) if match else 0


class WorldRegistry:
    """
    Tracks live Webots instances and hands out ports; not a lifetime manager.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[WorldSpec, WebotsInstance] = {}
        self._ports = PortAllocator(settings.port_base + _worker_offset(settings.worker_id))

    def get_or_create(self, spec: WorldSpec) -> tuple[WebotsInstance, bool]:
        instance = self._instances.get(spec)
        if instance is not None:
            return instance, False
        instance = WebotsInstance(spec, self._settings, port=self._ports.acquire())
        instance.reallocate_port = self._ports.acquire
        self._instances[spec] = instance
        return instance, True

    def sweep(self) -> None:
        """
        Shut down anything still running at session end.
        """
        if self._settings.keep_alive:
            return
        for instance in self._instances.values():
            with suppress(Exception):  # one failure must not strand the rest
                instance.shutdown()


REGISTRY_KEY: pytest.StashKey[WorldRegistry] = pytest.StashKey()
