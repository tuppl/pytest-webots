import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from pytest_webots import ControllerSpec, WebotsError
from pytest_webots._core import session as session_module
from pytest_webots._core.session import WebotsSession


class StubInstance:
    world_path = "worlds/stub.wbt"
    alive = True

    def __init__(self, robots: list[str], mode: str | None = None, default_mode: str = "fast") -> None:
        self.robots = {name: f"ipc://1234/{name}" for name in robots}
        self.connected = set(robots)
        self.spec = SimpleNamespace(mode=mode)
        self.settings = SimpleNamespace(mode=default_mode)
        self.modes: list[str] = []
        self.advances: list[tuple[float, str]] = []
        self.time = 0.0

    def set_mode(self, mode: str) -> None:
        self.modes.append(mode)

    def sim_time(self) -> float:
        return self.time

    def advance_to(self, target: float, mode: str) -> float:
        self.advances.append((target, mode))
        dt = 0.032
        steps = math.ceil(round((target - self.time) / dt, 9))
        self.time = round(self.time + max(steps, 0) * dt, 9)  # first boundary at or past target
        return self.time


class FakeProcess:
    events: ClassVar[list[str]] = []
    fail_on: ClassVar[set[str]] = set()

    def __init__(self, spec: ControllerSpec, instance: Any) -> None:
        self.spec = spec
        self.alive = True
        self.returncode: int | None = None

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


def depart(session: WebotsSession, robot: str, returncode: int = 0) -> None:
    """
    Model a controller that ran and exited: Webots drops it from the connected
    set, and the process reports how it went.
    """
    process = session.controllers[robot]
    process.alive = False  # type: ignore[attr-defined]
    process.returncode = returncode  # type: ignore[misc]
    session.world.connected.discard(robot)  # type: ignore[attr-defined]


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


def test_build_failure_launches_nothing(events: list[str]) -> None:
    FakeProcess.fail_on = {"badbuild"}
    session = make_session(events, ["a", "badbuild"])
    with pytest.raises(WebotsError, match="badbuild build failed"):
        session.setup_controllers([spec("a"), spec("badbuild")])
    assert not any(event.startswith("launch:") for event in events)


def test_recrew_replaces_only_departed_controllers(events: list[str]) -> None:
    session = make_session(events, ["dead", "live"])
    session.setup_controllers([spec("dead"), spec("live")])
    live = session.controllers["live"]
    depart(session, "dead")
    stubs = session.recrew_departed()
    assert [stub.spec.robot for stub in stubs] == ["dead"]
    assert stubs[0].spec.path.name == "stub.py"
    assert events.count("launch:dead") == 2
    assert events.count("launch:live") == 1
    assert session.controllers["live"] is live
    # The stub needs no build.
    assert events.count("build:dead") == 1


def test_recrew_with_dead_instance_launches_nothing(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    depart(session, "a")
    session.world.alive = False  # type: ignore[misc]
    assert session.recrew_departed() == []
    assert events.count("launch:a") == 1


def test_recrew_stubs_are_reaped_by_terminate(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    depart(session, "a")
    stubs = session.recrew_departed()
    assert session.controllers["a"] is stubs[0]
    session.terminate_controllers()
    assert events.count("terminate:a") == 1


def test_pending_spec_launches_without_rebuilding(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a", autostart=False)])
    assert events == ["build:a"]
    session.launch_controller("a")
    # Built again at launch: cheap (digest cache) and correct for sources
    # generated between setup and launch.
    assert events == ["build:a", "build:a", "launch:a"]


def test_clean_only_leaves_a_crashed_controller_dead(events: list[str]) -> None:
    # The loud hang is the point: a crash must not be papered over by a stub.
    session = make_session(events, ["crashed"])
    session.setup_controllers([spec("crashed")])
    depart(session, "crashed", returncode=139)
    assert session.recrew_departed(clean_only=True) == []
    assert events.count("launch:crashed") == 1


def test_clean_only_reseats_a_controller_that_finished(events: list[str]) -> None:
    session = make_session(events, ["done"])
    session.setup_controllers([spec("done")])
    depart(session, "done", returncode=0)
    stubs = session.recrew_departed(clean_only=True)
    assert [stub.spec.robot for stub in stubs] == ["done"]


def test_teardown_reseats_a_crashed_controller_too(events: list[str]) -> None:
    # Teardown only needs the reset to land, so it is not fussy about why.
    session = make_session(events, ["crashed"])
    session.setup_controllers([spec("crashed")])
    depart(session, "crashed", returncode=139)
    assert [stub.spec.robot for stub in session.recrew_departed()] == ["crashed"]


def test_a_still_connected_controller_is_never_reseated(events: list[str]) -> None:
    session = make_session(events, ["live"])
    session.setup_controllers([spec("live")])
    assert session.recrew_departed(clean_only=True) == []
    assert events.count("launch:live") == 1


def test_stepping_reseats_a_departed_controller(events: list[str]) -> None:
    session = make_session(events, ["done"])
    session.setup_controllers([spec("done")])
    depart(session, "done")
    session.world.step = lambda ms=None: 0  # type: ignore[attr-defined, misc]
    session.step()
    assert events.count("launch:done") == 2  # the seat was taken before the step blocked


def test_launching_over_a_live_controller_evicts_it(events: list[str]) -> None:
    # Webots allows one connection per robot, so the seat must be freed first.
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    first = session.controllers["a"]
    session.launch_controller("a", "/controllers/replacement")
    assert events.index("terminate:a") < events.index("launch:a", events.index("terminate:a"))
    assert session.controllers["a"] is not first


def test_launching_over_a_dead_controller_does_not_terminate_it(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    depart(session, "a")
    session.launch_controller("a", "/controllers/replacement")
    assert "terminate:a" not in events  # nothing to evict


def test_a_dead_process_counts_as_departed_before_webots_says_so(events: list[str]) -> None:
    # The disconnect log and the process exit race; catching only the log leaves
    # a window where the seat is empty and nothing reports it.
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    process = session.controllers["a"]
    process.alive = False  # type: ignore[attr-defined]
    process.returncode = 0  # type: ignore[misc]
    # world.connected still lists it: Webots has not logged the disconnect yet
    assert "a" in session.world.connected  # type: ignore[attr-defined]
    assert [stub.spec.robot for stub in session.recrew_departed(clean_only=True)] == ["a"]


def test_a_disconnected_but_running_controller_is_left_alone(events: list[str]) -> None:
    # Dropped from connected while still running: it may reconnect itself.
    session = make_session(events, ["a"])
    session.setup_controllers([spec("a")])
    session.world.connected.discard("a")  # type: ignore[attr-defined]
    assert session.recrew_departed(clean_only=True) == []
    assert events.count("launch:a") == 1


def test_ops_spelling_reseats_like_the_method_spelling(events: list[str]) -> None:
    # webots.ops.foo() and webots.agent_op("foo") are the same call; a plugin op
    # may step, so both have to reseat first.
    session = make_session(events, ["done"])
    session.setup_controllers([spec("done")])
    depart(session, "done")
    session.world.agent_op = lambda op, params: "ok"  # type: ignore[attr-defined]
    assert session.ops.survival_time() == "ok"
    assert events.count("launch:done") == 2


def test_pause_freezes_and_gated_calls_refuse(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    assert session.paused
    assert session.world.modes == ["pause"]  # type: ignore[attr-defined]
    for action, call in (("step", session.step), ("reset", session.reset), ("reload", session.reload)):
        with pytest.raises(WebotsError, match=f"cannot {action} while the simulation is paused"):
            call()


def test_play_restores_the_configured_mode(events: list[str]) -> None:
    session = WebotsSession(StubInstance(["a"], mode="realtime"))  # type: ignore[arg-type]
    session.pause()
    session.play()
    assert session.world.modes == ["pause", "realtime"]  # type: ignore[attr-defined]
    assert not session.paused


def test_play_on_a_pause_configured_world_falls_through_to_fast(events: list[str]) -> None:
    session = WebotsSession(StubInstance(["a"], mode="pause"))  # type: ignore[arg-type]
    assert session.paused  # booted paused: the session starts knowing it
    session.play()
    assert session.world.modes == ["fast"]  # type: ignore[attr-defined]


def test_play_refuses_pause_as_a_target(events: list[str]) -> None:
    session = make_session(events, ["a"])
    with pytest.raises(WebotsError, match="cannot target"):
        session.play(mode="pause")


def test_ensure_playing_resumes_only_when_needed(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.ensure_playing()
    assert session.world.modes == []  # type: ignore[attr-defined]  # not paused: no-op
    session.pause()
    session.ensure_playing()
    assert session.world.modes == ["pause", "fast"]  # type: ignore[attr-defined]
    session.pause()
    session.world.alive = False  # type: ignore[misc]
    session.ensure_playing()  # dead world: nothing to talk to, must not raise
    assert session.world.modes == ["pause", "fast", "pause"]  # type: ignore[attr-defined]


def test_play_to_lands_on_a_boundary_and_stays_paused(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    assert session.play_to(1000) == 1024  # first 32 ms boundary past the target
    assert session.world.advances == [(1.0, "fast")]  # type: ignore[attr-defined]
    assert session.paused


def test_play_to_pauses_a_running_world_first(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.play_to(32)
    assert session.world.modes[0] == "pause"  # type: ignore[attr-defined]
    assert session.paused


def test_play_to_refuses_to_rewind(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    session.play_to(1000)
    with pytest.raises(WebotsError, match=r"at 1024 ms; cannot rewind to 500"):
        session.play_to(500)


def test_play_to_at_the_current_time_is_a_no_op(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    landed = session.play_to(1000)
    assert session.play_to(landed) == landed
    assert len(session.world.advances) == 1  # type: ignore[attr-defined]


def test_play_to_chunks_long_advances(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    session.play_to(40_000)
    assert [t for t, _ in session.world.advances] == [16.0, 32.0, 40.0]  # type: ignore[attr-defined]


def test_play_to_uses_the_configured_mode(events: list[str]) -> None:
    session = WebotsSession(StubInstance(["a"], mode="realtime"))  # type: ignore[arg-type]
    session.pause()
    session.play_to(32)
    assert session.world.advances == [(0.032, "realtime")]  # type: ignore[attr-defined]


def test_play_for_advances_from_here(events: list[str]) -> None:
    session = make_session(events, ["a"])
    session.pause()
    session.play_to(1000)  # at 1024
    assert session.play_for(1000) == 2048  # 2024 rounds up to the next boundary
