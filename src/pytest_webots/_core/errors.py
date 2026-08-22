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
    The Webots process died unexpectedly or hung while a test was using it.
    """


class WebotsQuitError(WebotsError):
    """
    The simulation was quit while a test was using it.

    Deliberate, not a failure of Webots: a controller called ``simulationQuit``
    or the window was closed. Sibling of ``WebotsCrashedError`` rather than a
    subclass, so catching a crash does not catch a normal shutdown.
    """


class BuildError(WebotsError):
    """
    A controller build failed or produced no output.
    """


class PortAllocationError(WebotsError):
    """
    No usable port was found, either by the allocator or by Webots itself.
    """
