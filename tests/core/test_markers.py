from pathlib import Path

from pytest_webots import ControllerSpec, WorldSpec
from pytest_webots._core.markers import world_ids


def spec(label: str, path: str = "") -> WorldSpec:
    return WorldSpec(path=Path(path or f"/p/{label}"), label=label)


def test_ids_are_the_marker_argument() -> None:
    assert world_ids([spec("worlds/arena.wbt"), spec("worlds/maze.wbt")]) == [
        "worlds/arena.wbt",
        "worlds/maze.wbt",
    ]


def test_same_stem_in_different_directories_stays_distinct() -> None:
    assert world_ids([spec("a/worlds/arena.wbt"), spec("b/worlds/arena.wbt")]) == [
        "a/worlds/arena.wbt",
        "b/worlds/arena.wbt",
    ]


def test_hook_resolved_names_show_as_written() -> None:
    assert world_ids([spec("virtual", "/p/worlds/second.wbt")]) == ["virtual"]


def test_unlabelled_specs_fall_back_to_the_stem() -> None:
    assert world_ids([WorldSpec(path=Path("/p/worlds/arena.wbt"))]) == ["arena"]


def test_str_is_the_test_id_not_the_field_dump() -> None:
    # Anything reporting a spec (a CSV row, a log line) should agree with the
    # nodeid rather than dumping every field.
    world = spec("worlds/arena.wbt")
    assert str(world) == "worlds/arena.wbt"
    assert f"{world}" == "worlds/arena.wbt"
    assert str(WorldSpec(path=Path("/p/worlds/arena.wbt"))) == "arena"


def test_repr_still_shows_the_fields() -> None:
    # __str__ is for display; debugging still needs the full dump.
    assert "scope=" in repr(spec("worlds/arena.wbt"))


def test_controller_spec_str_is_the_robot() -> None:
    controller = ControllerSpec(robot="probe", path=Path("/p/controllers/probe/probe.py"))
    assert str(controller) == "probe"
    assert "autostart=" in repr(controller)


def test_label_does_not_split_one_world_into_two() -> None:
    # The registry keys on WorldSpec; two spellings of one file must stay equal
    # or the world would boot twice.
    path = Path("/p/worlds/arena.wbt")
    assert WorldSpec(path=path, label="worlds/arena.wbt") == WorldSpec(path=path, label="../p/worlds/arena.wbt")
    assert len({WorldSpec(path=path, label="a"), WorldSpec(path=path, label="b")}) == 1
