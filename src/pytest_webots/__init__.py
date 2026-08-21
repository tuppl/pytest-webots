"""
pytest plugin for running Webots simulations with tests.
"""

from ._core.controller import ControllerProcess
from ._core.errors import WebotsCrashedError, WebotsError, WorldBootTimeout
from ._core.markers import ControllerSpec, FixtureRef, WorldSpec, fixture_ref
from ._core.session import WebotsSession
from ._core.supervisor.proxy import SupervisorProxy
from ._core.world import WebotsInstance

__all__ = [
    "ControllerProcess",
    "ControllerSpec",
    "FixtureRef",
    "SupervisorProxy",
    "WebotsCrashedError",
    "WebotsError",
    "WebotsInstance",
    "WebotsSession",
    "WorldBootTimeout",
    "WorldSpec",
    "fixture_ref",
]
