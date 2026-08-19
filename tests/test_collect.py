import json
from pathlib import Path

import pytest

from pytest_webots._core.config import discover_webots_home

WORLDS = Path(__file__).parent / "worlds"
MINIMAL = WORLDS / "minimal.wbt"
SECOND = WORLDS / "second.wbt"


def test_group_markers_added_per_world(pytester: pytest.Pytester, tmp_path: Path, place_world) -> None:
    minimal = place_world(MINIMAL, "worlds/minimal.wbt")
    second = place_world(SECOND, "worlds/second.wbt")
    groups_file = tmp_path / "groups.json"
    pytester.makeconftest(
        f"""
        import json
        import pytest

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(config, items):
            groups = {{
                item.nodeid: marker.args[0]
                for item in items
                if (marker := item.get_closest_marker("xdist_group")) is not None
            }}
            {str(groups_file)!r} and __import__("pathlib").Path({str(groups_file)!r}).write_text(json.dumps(groups))
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({second!r})
        @pytest.mark.webots_world({minimal!r})
        def test_grouped(webots):
            pass

        def test_ungrouped():
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q", "-p", "no:cacheprovider")
    assert result.ret == 0
    groups = json.loads(groups_file.read_text())
    nodeid = "test_group_markers_added_per_world.py::test_grouped"
    # Grouping keys on the resolved path, not the id, so two spellings share a worker.
    assert groups[f"{nodeid}[{minimal}]"] == f"webots:{pytester.path / minimal}"
    assert groups[f"{nodeid}[{second}]"] == f"webots:{pytester.path / second}"
    assert "test_group_markers_added_per_world.py::test_ungrouped" not in groups


@pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")
def test_two_workers_boot_each_world_once(pytester: pytest.Pytester, tmp_path: Path) -> None:
    boots_file = tmp_path / "boots.jsonl"
    pytester.makeconftest(
        f"""
        import json

        def pytest_webots_world_started(instance):
            with open({str(boots_file)!r}, "a") as f:
                f.write(json.dumps({{"world": instance.spec.path.name, "port": instance.port}}) + "\\n")
        """
    )
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(MINIMAL)!r})
        def test_m1(webots):
            assert webots.world.alive

        @pytest.mark.webots_world({str(MINIMAL)!r})
        def test_m2(webots):
            assert webots.world.alive

        @pytest.mark.webots_world({str(SECOND)!r})
        def test_s1(webots):
            assert webots.world.alive

        @pytest.mark.webots_world({str(SECOND)!r})
        def test_s2(webots):
            assert webots.world.alive
        """
    )
    result = pytester.runpytest("-n", "2", "--dist", "loadgroup", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=4)
    boots = [json.loads(line) for line in boots_file.read_text().splitlines()]
    worlds = [b["world"] for b in boots]
    assert sorted(worlds) == ["minimal.wbt", "second.wbt"]  # exactly one boot per world
    ports = {b["port"] for b in boots}
    assert len(ports) == 2  # no port collision between instances
