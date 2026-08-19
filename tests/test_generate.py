from pathlib import Path

import pytest

WORLDS = Path(__file__).parent / "worlds"
MINIMAL = WORLDS / "minimal.wbt"
SECOND = WORLDS / "second.wbt"
VARIANT = WORLDS / "variant" / "minimal.wbt"


def test_world_ids_compose_with_parametrize(pytester: pytest.Pytester, place_world) -> None:
    minimal = place_world(MINIMAL, "worlds/minimal.wbt")
    second = place_world(SECOND, "worlds/second.wbt")
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({second!r})
        @pytest.mark.webots_world({minimal!r})  # bottom-most marker applies first, like stacked parametrize
        @pytest.mark.parametrize("speed", [1.0, 2.0])
        def test_drive(webots, speed):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "*test_drive?worlds/minimal.wbt-1.0?*",
            "*test_drive?worlds/minimal.wbt-2.0?*",
            "*test_drive?worlds/second.wbt-1.0?*",
            "*test_drive?worlds/second.wbt-2.0?*",
        ],
        consecutive=True,
    )


def test_collection_groups_by_world(pytester: pytest.Pytester, place_world) -> None:
    minimal = place_world(MINIMAL, "worlds/minimal.wbt")
    second = place_world(SECOND, "worlds/second.wbt")
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = [
            pytest.mark.webots_world({minimal!r}),
            pytest.mark.webots_world({second!r}),
        ]

        def test_one(webots):
            pass

        def test_two(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "*test_one?worlds/minimal.wbt?*",
            "*test_two?worlds/minimal.wbt?*",
            "*test_one?worlds/second.wbt?*",
            "*test_two?worlds/second.wbt?*",
        ],
        consecutive=True,
    )


def test_closest_node_overrides_module_marker(pytester: pytest.Pytester, place_world) -> None:
    minimal = place_world(MINIMAL, "worlds/minimal.wbt")
    second = place_world(SECOND, "worlds/second.wbt")
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = pytest.mark.webots_world({minimal!r})

        @pytest.mark.webots_world({second!r})
        def test_own_world(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_own_world?worlds/second.wbt?*"])
    result.stdout.no_fnmatch_line("*test_own_world?worlds/minimal.wbt?*")


def test_same_stem_in_different_directories(pytester: pytest.Pytester, place_world) -> None:
    here = place_world(MINIMAL, "worlds/minimal.wbt")
    variant = place_world(VARIANT, "worlds/variant/minimal.wbt")
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({variant!r})
        @pytest.mark.webots_world({here!r})
        def test_collide(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_collide?worlds/minimal.wbt?*", "*test_collide?worlds/variant/minimal.wbt?*"])


def test_duplicate_world_errors(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(MINIMAL)!r})
        @pytest.mark.webots_world({str(MINIMAL)!r}, scope="function")
        def test_dup(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*more than once*"])


def test_invalid_mode_errors(pytester: pytest.Pytester, place_world) -> None:
    world = place_world(MINIMAL, "worlds/minimal.wbt")
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({world!r}, mode="fastt")
        def test_typo(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*mode must be one of*'fastt'*"])


def test_unresolvable_world_lists_attempts(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.webots_world("missing.wbt")
        def test_missing(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*'missing.wbt' not found; tried:*", "*missing.wbt"])


def test_worlds_dir_ini_resolution(pytester: pytest.Pytester) -> None:
    pytester.makeini(
        f"""
        [pytest]
        webots_worlds_dir = {WORLDS}
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.webots_world("minimal.wbt")
        def test_from_dir(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_from_dir?minimal.wbt?*"])


def test_resolve_world_hook_wins(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        f"""
        from pathlib import Path

        def pytest_webots_resolve_world(name, config):
            if name == "virtual":
                return Path({str(SECOND)!r})
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.webots_world("virtual")
        def test_hooked(webots):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_hooked?virtual?*"])  # the alias, not the file it resolved to


def test_no_marker_skips(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_bare(webots):
            pass
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1)
