"""
Process table for running Webots instances and port allocation.
"""

from __future__ import annotations

import re
import socket
from typing import TYPE_CHECKING

import pytest

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
        self._port_base = settings.port_base + _worker_offset(settings.worker_id)

    def get_or_create(self, spec: WorldSpec) -> tuple[WebotsInstance, bool]:
        instance = self._instances.get(spec)
        if instance is not None:
            return instance, False
        instance = WebotsInstance(spec, self._settings, port=self._free_port())
        self._instances[spec] = instance
        return instance, True

    def _free_port(self) -> int:
        taken = {instance.port for instance in self._instances.values()}
        port = self._port_base
        while True:
            if port not in taken and _port_available(port):
                return port
            port += 1

    def sweep(self) -> None:
        """
        Shut down anything still running at session end.
        """
        if self._settings.keep_alive:
            return
        for instance in self._instances.values():
            instance.shutdown()


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


REGISTRY_KEY: pytest.StashKey[WorldRegistry] = pytest.StashKey()
