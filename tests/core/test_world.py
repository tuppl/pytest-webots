import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pytest_webots import ControllerProcess, ControllerSpec, WebotsInstance, WorldSpec
from pytest_webots._core import ports
from pytest_webots._core import world as world_module
from pytest_webots._core.config import Settings, discover_webots_home, webots_binary
from pytest_webots._core.errors import (
    PortAllocationError,
    WebotsCrashedError,
    WebotsQuitError,
    WorldBootTimeout,
)

MakeSettings = Callable[..., Settings]
WORLDS = Path(__file__).parent.parent / "worlds"
PROBE = Path(__file__).parent.parent / "controllers" / "probe" / "probe.py"

requires_webots = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")


class FakeProc:
    """
    Stands in for Webots: yields the given stdout lines, then the pipe ends.
    """

    def __init__(self, lines: list[str], exit_code: int | None, delay: float) -> None:
        self.pid = 4242
        self.returncode = exit_code
        self._exit_code = exit_code
        self.stdout = self._emit(lines, delay)

    @staticmethod
    def _emit(lines: list[str], delay: float) -> Iterator[str]:
        for line in lines:
            time.sleep(delay)
            yield line

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        self._exit_code = 0

    def kill(self) -> None:
        self._exit_code = -9

    def wait(self, timeout: float | None = None) -> int | None:
        return self._exit_code


def fake_webots(
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str],
    exit_code: int | None = None,
    delay: float = 0.0,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProc(lines, exit_code, delay))


class HungProc(FakeProc):
    """
    Stands in for a Webots that stopped answering but has not exited.
    """

    def __init__(self) -> None:
        super().__init__([], None, 0.0)

    def wait(self, timeout: float | None = None) -> int | None:
        if self._exit_code is None:
            raise subprocess.TimeoutExpired(cmd="webots", timeout=timeout or 0)
        return self._exit_code


def stub_instance(tmp_path: Path, make_settings: MakeSettings, port: int, **overrides: object) -> WebotsInstance:
    home = webots_binary(tmp_path / "webots")
    home.parent.mkdir(parents=True, exist_ok=True)
    home.touch()
    # inject_supervisor off by default: boot then ends at readiness, with no agent to launch
    defaults: dict[str, object] = {
        "home": tmp_path / "webots",
        "inject_supervisor": False,
        "startup_timeout": 5.0,
    }
    defaults.update(overrides)
    return WebotsInstance(WorldSpec(path=tmp_path / "world.wbt", timeout=5), make_settings(**defaults), port=port)


def live_instance(
    tmp_path: Path,
    make_settings: MakeSettings,
    port: int,
    world: str = "minimal.wbt",
    timeout: float = 120.0,
    **overrides: object,
) -> WebotsInstance:
    # Boot from a copy so the Webots GUI-state sidecar lands in tmp_path, not the repo.
    path = tmp_path / world
    path.write_text((WORLDS / world).read_text())
    settings = make_settings(home=discover_webots_home(None), startup_timeout=timeout, **overrides)
    return WebotsInstance(WorldSpec(path=path, timeout=timeout), settings, port=port)


def hold(count: int, start: int = 40000) -> list[socket.socket]:
    """
    Listen on `count` consecutive ports, as a rival Webots would.
    """
    port = start
    while port + count <= ports.MAX_PORT:
        held: list[socket.socket] = []
        for offset in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("", port + offset))
                sock.listen(1)
            except OSError:
                sock.close()
                for open_socket in held:
                    open_socket.close()
                held = []
                break
            held.append(sock)
        if held:
            return held
        port += count
    raise AssertionError(f"no run of {count} free ports")


def test_boot_follows_the_announced_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(
        monkeypatch,
        [
            "WARNING: Could not listen to extern controllers on port 1234. Using port 1240 instead.",
            "ipc://1240/probe",
        ],
    )
    instance = stub_instance(tmp_path, make_settings, port=1234)
    try:
        instance.boot()
        assert instance.port == 1240
        assert "serving port 1240" in instance.output()
        assert instance.robots["probe"] == "ipc://1240/probe"
    finally:
        instance.shutdown(force=True)


def test_boot_keeps_the_port_when_webots_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(monkeypatch, ["ipc://1234/probe"])
    instance = stub_instance(tmp_path, make_settings, port=1234)
    try:
        instance.boot()
        assert instance.port == 1234
        assert "serving port" not in instance.output()
    finally:
        instance.shutdown(force=True)


def test_adoption_is_idempotent_across_wait_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(monkeypatch, ["ipc://1240/probe"])
    instance = stub_instance(tmp_path, make_settings, port=1234)
    try:
        instance.boot()
        instance._wait_ready()  # the reload path re-enters readiness without a respawn
        assert instance.port == 1240
        assert instance.output().count("serving port 1240") == 1
    finally:
        instance.shutdown(force=True)


def test_reboot_does_not_adopt_a_stale_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(monkeypatch, ["ipc://1240/probe"])
    instance = stub_instance(tmp_path, make_settings, port=1234)
    instance.boot()
    instance.shutdown(force=True)
    fake_webots(monkeypatch, ["ipc://1240/probe"])
    try:
        instance.boot()
        assert instance.port == 1240  # already there; nothing to follow
        assert instance.output().count("serving port 1240") == 0
    finally:
        instance.shutdown(force=True)


def test_port_exhaustion_reports_the_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(
        monkeypatch,
        ["failed to open TCP server in the port range [1234-1244]"],
        exit_code=1,
        delay=0.05,  # the reader is still behind when boot notices the exit
    )
    instance = stub_instance(tmp_path, make_settings, port=1234)
    try:
        with pytest.raises(PortAllocationError, match=r"no free port in \[1234, 1244\]"):
            instance.boot()
    finally:
        instance.shutdown(force=True)


def test_port_exhaustion_moves_to_a_new_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    fake_webots(monkeypatch, ["failed to open TCP server in the port range [1234-1244]"], exit_code=1)
    instance = stub_instance(tmp_path, make_settings, port=1234)
    instance.reallocate_port = ports.PortAllocator(1300).acquire
    try:
        with pytest.raises(PortAllocationError):
            instance.boot()
        assert instance.port >= 1300
    finally:
        instance.shutdown(force=True)


def test_boot_leaves_a_busy_port_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    with sock:
        taken = sock.getsockname()[1]
        fake_webots(monkeypatch, [f"ipc://{taken + 1}/probe"])
        instance = stub_instance(tmp_path, make_settings, port=taken)
        instance.reallocate_port = ports.PortAllocator(taken + 1).acquire
        try:
            instance.boot()
            assert instance.port != taken
        finally:
            instance.shutdown(force=True)


def test_injected_world_name_includes_the_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_settings: MakeSettings
) -> None:
    captured: dict[str, str] = {}

    def fake_inject(path: Path, name: str, token: str) -> Path:
        captured["token"] = token
        return path

    monkeypatch.setattr(world_module, "inject_supervisor", fake_inject)
    monkeypatch.setattr(WebotsInstance, "_start_agent", lambda self: None)
    fake_webots(monkeypatch, ["ipc://1234/pytest-supervisor"])
    instance = stub_instance(tmp_path, make_settings, port=1234, inject_supervisor=True)
    try:
        instance.boot()
        assert captured["token"] == f"1234-{os.getpid()}"
    finally:
        instance.shutdown(force=True)


@requires_webots
def test_boot_follows_webots_onto_a_free_port(tmp_path: Path, make_settings: MakeSettings) -> None:
    blocked = hold(1)
    instance = live_instance(tmp_path, make_settings, port=blocked[0].getsockname()[1])
    requested = instance.port
    controller = ControllerProcess(ControllerSpec(robot="probe", path=PROBE), instance)
    try:
        instance.boot()
        assert instance.port != requested  # Webots retried upward and said so in the URLs
        assert instance.sim_time() >= 0  # the agent found it on the port it actually serves
        controller.start()  # start() returns only once Webots reports the robot connected
        assert controller.alive
    finally:
        controller.terminate()
        instance.shutdown(force=True)
        for sock in blocked:
            sock.close()


@requires_webots
def test_boot_reports_the_range_when_webots_finds_no_port(tmp_path: Path, make_settings: MakeSettings) -> None:
    blocked = hold(11)  # Webots gives up after trying base..base+10
    instance = live_instance(tmp_path, make_settings, port=blocked[0].getsockname()[1])
    try:
        with pytest.raises(PortAllocationError, match="no free port"):
            instance.boot()
    finally:
        instance.shutdown(force=True)
        for sock in blocked:
            sock.close()


@requires_webots
def test_reboot_moves_off_a_port_taken_while_down(tmp_path: Path, make_settings: MakeSettings) -> None:
    instance = live_instance(tmp_path, make_settings, port=ports.PortAllocator(41000).acquire())
    instance.reallocate_port = ports.PortAllocator(42000).acquire
    blocked: list[socket.socket] = []
    try:
        instance.boot()
        served = instance.port
        instance.shutdown()
        blocked = [socket.socket(socket.AF_INET, socket.SOCK_STREAM)]
        blocked[0].bind(("", served))
        blocked[0].listen(1)
        instance.boot()
        assert instance.alive
        assert instance.port >= 42000
    finally:
        instance.shutdown(force=True)
        for sock in blocked:
            sock.close()


@requires_webots
def test_boot_timeout(tmp_path: Path, make_settings: MakeSettings) -> None:
    instance = live_instance(
        tmp_path,
        make_settings,
        port=1334,
        world="empty.wbt",
        timeout=5.0,
        inject_supervisor=False,  # nothing will ever announce a URL
    )
    try:
        with pytest.raises(WorldBootTimeout, match="did not become ready"):
            instance.boot()
    finally:
        instance.shutdown(force=True)
    assert not instance.alive


def failed_instance(
    tmp_path: Path, make_settings: MakeSettings, proc: FakeProc
) -> tuple[WebotsInstance, list[BaseException]]:
    instance = stub_instance(tmp_path, make_settings, port=1234)
    instance._proc = proc  # type: ignore[assignment]
    crashes: list[BaseException] = []
    instance.on_crashed = crashes.append
    return instance, crashes


def test_clean_exit_is_a_quit_not_a_crash(tmp_path: Path, make_settings: MakeSettings) -> None:
    # A controller calling simulationQuit(0) is a normal shutdown; reporting it
    # as a crash sends the reader hunting a failure that never happened.
    instance, crashes = failed_instance(tmp_path, make_settings, FakeProc([], 0, 0.0))
    with pytest.raises(WebotsQuitError, match="was quit"):
        instance._handle_failure(RuntimeError("socket closed"))
    assert crashes == []  # the crash hook is for crashes


def test_clean_exit_is_not_caught_as_a_crash(tmp_path: Path, make_settings: MakeSettings) -> None:
    instance, _ = failed_instance(tmp_path, make_settings, FakeProc([], 0, 0.0))
    with pytest.raises(WebotsQuitError) as excinfo:
        instance._handle_failure(RuntimeError("socket closed"))
    assert not isinstance(excinfo.value, WebotsCrashedError)


def test_nonzero_exit_is_a_crash_that_names_the_ambiguity(tmp_path: Path, make_settings: MakeSettings) -> None:
    # simulationQuit(3) and a genuine crash both surface as 3.
    instance, crashes = failed_instance(tmp_path, make_settings, FakeProc([], 3, 0.0))
    with pytest.raises(WebotsCrashedError, match=r"exited with code 3.*simulationQuit\(3\)"):
        instance._handle_failure(RuntimeError("socket closed"))
    assert len(crashes) == 1


def test_still_running_is_a_hang(tmp_path: Path, make_settings: MakeSettings) -> None:
    instance, crashes = failed_instance(tmp_path, make_settings, HungProc())
    with pytest.raises(WebotsCrashedError, match="stopped responding"):
        instance._handle_failure(RuntimeError("timed out"))
    assert len(crashes) == 1


def test_failure_keeps_the_underlying_error_as_the_cause(tmp_path: Path, make_settings: MakeSettings) -> None:
    instance, _ = failed_instance(tmp_path, make_settings, FakeProc([], 0, 0.0))
    original = RuntimeError("agent closed the connection during 'step'")
    with pytest.raises(WebotsQuitError) as excinfo:
        instance._handle_failure(original)
    assert excinfo.value.__cause__ is original
