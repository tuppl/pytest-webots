"""
Port probing and allocation for Webots instances.
"""

from __future__ import annotations

import socket

from .errors import PortAllocationError

MIN_PORT = 1
MAX_PORT = 65535


def available(port: int) -> bool:
    """
    True when nothing holds the port.

    Probes on all interfaces, as Webots binds, so a listener on a specific
    non-loopback address counts as taken.
    """
    if not MIN_PORT <= port <= MAX_PORT:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True


def ephemeral() -> int:
    """
    An OS-assigned free port, for the Windows agent transport.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class PortAllocator:
    """
    Hands out ports upward from a base and never comes back down.

    A released port is not reissued, so an instance can shut down and reboot
    without another world claiming its number in the meantime.
    """

    def __init__(self, base: int) -> None:
        self._next = base

    def acquire(self) -> int:
        port = self._next
        while port <= MAX_PORT:
            if available(port):
                self._next = port + 1
                return port
            port += 1
        raise PortAllocationError(
            f"no free port in [{self._next}, {MAX_PORT}]; lower --webots-port-base or free some ports"
        )
