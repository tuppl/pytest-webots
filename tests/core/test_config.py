from pathlib import Path

import pytest

from pytest_webots._core import config as config_mod
from pytest_webots._core.config import discover_webots_home, webots_binary


def make_fake_home(root: Path) -> Path:
    binary = webots_binary(root)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return root


def test_discovery_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = make_fake_home(tmp_path / "webots")
    monkeypatch.setenv("WEBOTS_HOME", str(home))
    assert discover_webots_home(None) == home


def test_discovery_explicit_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_home = make_fake_home(tmp_path / "env")
    explicit_home = make_fake_home(tmp_path / "explicit")
    monkeypatch.setenv("WEBOTS_HOME", str(env_home))
    assert discover_webots_home(str(explicit_home)) == explicit_home


def test_discovery_explicit_invalid_errors(tmp_path: Path) -> None:
    with pytest.raises(pytest.UsageError, match="not found"):
        discover_webots_home(str(tmp_path / "nowhere"))


def test_discovery_none_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WEBOTS_HOME", raising=False)
    monkeypatch.setattr(config_mod, "_DEFAULT_HOMES", (tmp_path / "absent",))
    assert discover_webots_home(None) is None


SETTINGS_PROBE = """
    from pytest_webots._core.config import SETTINGS_KEY

    def test_probe(request):
        settings = request.config.stash[SETTINGS_KEY]
        with open({path!r}, "w") as out:
            out.write(f"{{settings.startup_timeout}} {{settings.port_base}}")
"""


def read_settings(pytester: pytest.Pytester, tmp_path: Path, *args: str) -> tuple[float, int]:
    record = tmp_path / "settings.txt"
    pytester.makepyfile(SETTINGS_PROBE.format(path=str(record)))
    pytester.runpytest("-p", "no:cacheprovider", *args).assert_outcomes(passed=1)
    timeout, port = record.read_text().split()
    return float(timeout), int(port)


def test_zero_on_the_command_line_is_a_value_not_an_absence(pytester: pytest.Pytester, tmp_path: Path) -> None:
    # `or` would discard these and silently substitute the ini defaults.
    assert read_settings(pytester, tmp_path, "--webots-startup-timeout=0", "--webots-port-base=0") == (0.0, 0)


def test_omitted_options_fall_back_to_the_ini_values(pytester: pytest.Pytester, tmp_path: Path) -> None:
    pytester.makeini("[pytest]\nwebots_startup_timeout = 12\nwebots_port_base = 2345\n")
    assert read_settings(pytester, tmp_path) == (12.0, 2345)


def test_command_line_beats_the_ini_values(pytester: pytest.Pytester, tmp_path: Path) -> None:
    pytester.makeini("[pytest]\nwebots_startup_timeout = 12\nwebots_port_base = 2345\n")
    assert read_settings(pytester, tmp_path, "--webots-startup-timeout=99", "--webots-port-base=5000") == (99.0, 5000)
