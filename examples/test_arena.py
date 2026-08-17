"""
Example test suite. Run with a Webots installation: pytest examples/
"""

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.webots_world("worlds/arena.wbt")

HERE = Path(__file__).parent

CRATE_START = [0.5, 0.5, 0.2]


@pytest.mark.webots_controller("explorer", "controllers/explorer")
def test_controller_comes_online(webots):
    process = webots.controllers["explorer"]
    assert process.alive
    deadline = time.monotonic() + 10
    while "explorer online" not in process.logs and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "explorer online" in process.logs


def test_supervisor_moves_the_crate(webots):
    crate = webots.supervisor.getFromDef("CRATE")
    assert crate.getPosition() == pytest.approx(CRATE_START)
    crate.getField("translation").setSFVec3f([-0.5, 0.0, 0.2])
    webots.step()
    assert crate.getPosition() == pytest.approx([-0.5, 0.0, 0.2])


def test_state_was_reset_between_tests(webots):
    crate = webots.supervisor.getFromDef("CRATE")
    assert crate.getPosition() == pytest.approx(CRATE_START)


def test_imperative_controller_launch(webots):
    # launch_controller paths are used as-is; give it an absolute path.
    process = webots.launch_controller("explorer", HERE / "controllers/explorer/explorer.py")
    assert process.alive
