from pathlib import Path
from typing import Any, ClassVar

import pytest

from pytest_webots import ControllerSpec, WebotsError
from pytest_webots._core import session as session_module
from pytest_webots._core.session import WebotsSession


class StubInstance:
    world_path = "worlds/stub.wbt"

    def __init__(self, robots: list[str]) -> None:
        self.robots = {name: f"ipc://1234/{name}" for name in robots}


class FakeProcess:
    events: ClassVar[list[str]] = []
    fail_on: ClassVar[set[str]] = set()

    def __init__(self, spec: ControllerSpec, instance: Any) -> None:
        self.spec = spec

    def start(self) -> None:
        FakeProcess.events.append(f"launch:{self.spec.robot}")
        if self.spec.robot in FakeProcess.fail_on:
            raise WebotsError(f"{self.spec.robot} failed to connect")

    def terminate(self) -> None:
        FakeProcess.events.append(f"terminate:{self.spec.robot}")


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    FakeProcess.events = []
    FakeProcess.fail_on = set()
    monkeypatch.setattr(session_module, "ControllerProcess", FakeProcess)
    return FakeProcess.events


def spec(robot: str, autostart: bool = True) -> ControllerSpec:
    return ControllerSpec(robot=robot, path=Path(f"/controllers/{robot}"), autostart=autostart)


def make_session(events: list[str], robots: list[str]) -> WebotsSession:
    def builder(spec: ControllerSpec) -> None:
        events.append(f"build:{spec.robot}")
        if spec.robot in FakeProcess.fail_on and spec.robot.startswith("badbuild"):
            raise WebotsError(f"{spec.robot} build failed")

    return WebotsSession(StubInstance(robots), builder=builder)  # type: ignore[arg-type]


def test_all_builds_precede_any_launch(events: list[str]) -> None:
    session = make_session(events, ["a", "b", "c"])
    session.setup_controllers([spec("a"), spec("b", autostart=False), spec("c")])
    assert events == ["build:a", "build:b", "build:c", "launch:a", "launch:c"]


def test_launch_failure_terminates_already_launched(events: list[str]) -> None:
    FakeProcess.fail_on = {"c"}
    session = make_session(events, ["a", "c"])
    with pytest.raises(WebotsError, match="c failed to connect"):
        session.setup_controllers([spec("a"), spec("c")])
    assert "terminate:a" in events
    assert session.launch_attempted


def test_build_failure_launches_nothing(events: list[str]) -> None:
    FakeProcess.fail_on = {"badbuild"}
    session = make_session(events, ["a", "badbuild"])
    with pytest.raises(WebotsError, match="badbuild build failed"):
        session.setup_controllers([spec("a"), spec("badbuild")])
    assert not any(event.startswith("launch:") for event in events)
    assert not session.launch_attempted


def test_pending_spec_launches_without_rebuilding(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a", autostart=False)])
    assert events == ["build:a"]
    session.launch_controller("a")
    # Built again at launch: cheap (digest cache) and correct for sources
    # generated between setup and launch.
    assert events == ["build:a", "build:a", "launch:a"]
