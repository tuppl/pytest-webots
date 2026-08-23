from collections.abc import Callable
from pathlib import Path

from pytest_webots import WorldSpec
from pytest_webots._core.config import Settings
from pytest_webots._core.registry import WorldRegistry

MakeSettings = Callable[..., Settings]


def test_instances_are_reused_per_world(tmp_path: Path, make_settings: MakeSettings) -> None:
    registry = WorldRegistry(make_settings(port_base=30200))
    spec = WorldSpec(path=tmp_path / "a.wbt")
    first = registry.get_or_create(spec)
    assert registry.get_or_create(spec) is first


def test_worker_id_offsets_the_base(tmp_path: Path, make_settings: MakeSettings) -> None:
    registry = WorldRegistry(make_settings(port_base=30300, worker_id="gw3"))
    instance = registry.get_or_create(WorldSpec(path=tmp_path / "a.wbt"))
    assert instance.port >= 30303


def test_each_world_gets_a_higher_port(tmp_path: Path, make_settings: MakeSettings) -> None:
    registry = WorldRegistry(make_settings(port_base=30400))
    first = registry.get_or_create(WorldSpec(path=tmp_path / "a.wbt"))
    second = registry.get_or_create(WorldSpec(path=tmp_path / "b.wbt"))
    assert second.port > first.port


def test_instances_can_reallocate(tmp_path: Path, make_settings: MakeSettings) -> None:
    registry = WorldRegistry(make_settings(port_base=30500))
    instance = registry.get_or_create(WorldSpec(path=tmp_path / "a.wbt"))
    assert instance._allocate_port is not None
    assert instance._allocate_port() > instance.port


def test_sweep_survives_a_failing_shutdown(tmp_path: Path, make_settings: MakeSettings) -> None:
    registry = WorldRegistry(make_settings(port_base=30600))
    first = registry.get_or_create(WorldSpec(path=tmp_path / "a.wbt"))
    second = registry.get_or_create(WorldSpec(path=tmp_path / "b.wbt"))
    swept: list[str] = []

    def explode() -> None:
        raise RuntimeError("shutdown failed")

    first.shutdown = explode  # type: ignore[method-assign]
    second.shutdown = lambda force=False: swept.append("second")  # type: ignore[method-assign, assignment]
    registry.sweep()
    assert swept == ["second"]
