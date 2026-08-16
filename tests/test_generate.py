from pathlib import Path

import pytest

WORLDS = Path(__file__).parent / "worlds"
MINIMAL = WORLDS / "minimal.wbt"
SECOND = WORLDS / "second.wbt"
VARIANT = WORLDS / "variant" / "minimal.wbt"


def test_world_ids_compose_with_parametrize(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(SECOND)!r})
        @pytest.mark.webots_world({str(MINIMAL)!r})  # bottom-most marker applies first, like stacked parametrize
        @pytest.mark.parametrize("speed", [1.0, 2.0])
        def test_drive(webots_world, speed):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "*test_drive?minimal-1.0?*",
            "*test_drive?minimal-2.0?*",
            "*test_drive?second-1.0?*",
            "*test_drive?second-2.0?*",
        ],
        consecutive=True,
    )


def test_collection_groups_by_world(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = [
            pytest.mark.webots_world({str(MINIMAL)!r}),
            pytest.mark.webots_world({str(SECOND)!r}),
        ]

        def test_one(webots_world):
            pass

        def test_two(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "*test_one?minimal?*",
            "*test_two?minimal?*",
            "*test_one?second?*",
            "*test_two?second?*",
        ],
        consecutive=True,
    )


def test_closest_node_overrides_module_marker(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = pytest.mark.webots_world({str(MINIMAL)!r})

        @pytest.mark.webots_world({str(SECOND)!r})
        def test_own_world(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_own_world?second?*"])
    result.stdout.no_fnmatch_line("*test_own_world?minimal?*")


def test_stem_collision_prefixes_parent(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(VARIANT)!r})
        @pytest.mark.webots_world({str(MINIMAL)!r})
        def test_collide(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_collide?worlds-minimal?*", "*test_collide?variant-minimal?*"])


def test_duplicate_world_errors(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(MINIMAL)!r})
        @pytest.mark.webots_world({str(MINIMAL)!r}, scope="function")
        def test_dup(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*more than once*"])


def test_unresolvable_world_lists_attempts(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.webots_world("missing.wbt")
        def test_missing(webots_world):
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
        def test_from_dir(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_from_dir?minimal?*"])


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
        def test_hooked(webots_world):
            pass
        """
    )
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*test_hooked?second?*"])


def test_no_marker_skips(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_bare(webots_world):
            pass
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1)
