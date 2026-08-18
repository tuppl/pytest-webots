from pathlib import Path

import pytest

from pytest_webots._core.config import discover_webots_home

pytestmark = pytest.mark.skipif(discover_webots_home(None) is None, reason="no Webots installation found")

PROBE = Path(__file__).parent / "controllers" / "probe"


def test_failure_report_shows_webots_and_controller_output(pytester: pytest.Pytester) -> None:
    world = Path(__file__).parent / "worlds" / "second.wbt"
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.webots_world({str(world)!r})
        @pytest.mark.webots_controller("probe", {str(PROBE)!r})
        def test_fails(webots):
            assert False, "deliberate"
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*webots output*",
            "*extern controller: connected*",
            "*webots controller 'probe'*",
            "*probe controller ready*",
        ]
    )
