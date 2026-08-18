"""
Error types shared across the domain layer.
"""

from __future__ import annotations


class WebotsError(RuntimeError):
    """
    Base error for Webots process failures.
    """


class WorldBootTimeout(WebotsError):
    """
    The world did not reach readiness within the startup timeout.
    """


class WebotsCrashedError(WebotsError):
    """
    The Webots process died or hung while a test was using it.
    """


class BuildError(WebotsError):
    """
    A controller build failed or produced no output.
    """


class PortAllocationError(WebotsError):
    """
    No usable port was found, either by the allocator or by Webots itself.
    """
