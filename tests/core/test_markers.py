from pathlib import Path
from types import SimpleNamespace

import pytest

from pytest_webots import ControllerSpec, WorldSpec, fixture_ref
from pytest_webots._core.markers import (
    MarkerError,
    _resolve_refs,
    collect_controller_specs,
    collect_world_specs,
    world_ids,
)


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


class FakeNode:
    def __init__(self, path: Path, *decorators: pytest.MarkDecorator) -> None:
        self.nodeid = "tests/test_fake.py::test_fake"
        self.path = path
        self._marks = [decorator.mark for decorator in decorators]

    def iter_markers_with_node(self, name: str) -> list[tuple[object, pytest.Mark]]:
        return [(self, mark) for mark in self._marks if mark.name == name]


def test_fixture_ref_path_resolves_then_anchors(tmp_path: Path) -> None:
    (tmp_path / "ctrl.py").write_text("")
    node = FakeNode(tmp_path / "test_x.py", pytest.mark.webots_controller("bot", fixture_ref("ctrl")))

    def resolver(name: str) -> str:
        assert name == "ctrl"
        return "ctrl.py"

    specs = collect_controller_specs(node, tmp_path, resolver)
    assert specs[0].path == (tmp_path / "ctrl.py").resolve()


def test_fixture_ref_nested_in_args_env_and_cwd(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text("")
    workdir = tmp_path / "work"
    workdir.mkdir()
    values = {"flag": "--x", "home": "/h", "dir": str(workdir)}
    node = FakeNode(
        tmp_path / "test_x.py",
        pytest.mark.webots_controller(
            "bot",
            "probe.py",
            args=[fixture_ref("flag"), "--plain"],
            env={"HOME": fixture_ref("home")},
            cwd=fixture_ref("dir"),
        ),
    )
    specs = collect_controller_specs(node, tmp_path, values.__getitem__)
    assert specs[0].args == ("--x", "--plain")
    assert specs[0].env == {"HOME": "/h"}
    assert specs[0].cwd == workdir.resolve()


def test_resolve_refs_preserves_list_tuple_and_dict_shapes() -> None:
    node = FakeNode(Path("/p/test_x.py"))
    resolved = _resolve_refs(
        {"a": [fixture_ref("x")], "b": (fixture_ref("x"), 1), "c": "plain"},
        {"x": 9}.__getitem__,
        node,
    )
    assert resolved == {"a": [9], "b": (9, 1), "c": "plain"}
    assert isinstance(resolved["a"], list)
    assert isinstance(resolved["b"], tuple)


def test_marker_without_fixture_ref_never_calls_resolver(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text("")
    calls: list[str] = []
    node = FakeNode(
        tmp_path / "test_x.py",
        pytest.mark.webots_controller("bot", "probe.py", args=["--x"], env={"K": "v"}),
    )
    collect_controller_specs(node, tmp_path, lambda name: calls.append(name))
    assert calls == []


def test_fixture_ref_in_world_marker_is_a_collection_error(tmp_path: Path) -> None:
    node = FakeNode(tmp_path / "test_x.py", pytest.mark.webots_world(fixture_ref("world_path")))
    settings = SimpleNamespace(worlds_dir=None)
    with pytest.raises(MarkerError, match="collection time"):
        collect_world_specs(node, settings, tmp_path, lambda name: None)


def test_fixture_ref_nested_in_world_kwargs_is_rejected(tmp_path: Path) -> None:
    node = FakeNode(
        tmp_path / "test_x.py",
        pytest.mark.webots_world("arena.wbt", args=[fixture_ref("extra")]),
    )
    settings = SimpleNamespace(worlds_dir=None)
    with pytest.raises(MarkerError, match="fixture_ref"):
        collect_world_specs(node, settings, tmp_path, lambda name: None)


def test_resolver_failure_becomes_marker_error_naming_the_fixture(tmp_path: Path) -> None:
    def resolver(name: str) -> str:
        raise RuntimeError("boom")

    node = FakeNode(tmp_path / "test_x.py", pytest.mark.webots_controller("bot", fixture_ref("missing")))
    with pytest.raises(MarkerError, match=r"fixture_ref\('missing'\).*boom") as excinfo:
        collect_controller_specs(node, tmp_path, resolver)
    assert not isinstance(excinfo.value, RuntimeError)


def test_bare_assertion_error_gets_the_circular_diagnosis(tmp_path: Path) -> None:
    def resolver(name: str) -> str:
        raise AssertionError

    node = FakeNode(tmp_path / "test_x.py", pytest.mark.webots_controller("bot", fixture_ref("looped")))
    with pytest.raises(MarkerError, match=r"circular.*launch_controller"):
        collect_controller_specs(node, tmp_path, resolver)


def test_label_does_not_split_one_world_into_two() -> None:
    # The registry keys on WorldSpec; two spellings of one file must stay equal
    # or the world would boot twice.
    path = Path("/p/worlds/arena.wbt")
    assert WorldSpec(path=path, label="worlds/arena.wbt") == WorldSpec(path=path, label="../p/worlds/arena.wbt")
    assert len({WorldSpec(path=path, label="a"), WorldSpec(path=path, label="b")}) == 1
