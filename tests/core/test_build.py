import dataclasses
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from pytest_webots import ControllerSpec
from pytest_webots._core.build import run_build
from pytest_webots._core.config import Settings
from pytest_webots._core.errors import BuildError

MakeSettings = Callable[..., Settings]


def command_spec(directory: Path) -> ControllerSpec:
    (directory / "src.txt").write_text("v1")
    return ControllerSpec(
        robot="probe",
        path=directory / "out.bin",
        build=("sh", "-c", "cat src.txt > out.bin"),
    )


def test_raw_command_builds_and_caches(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = command_spec(tmp_path)
    settings = make_settings()
    assert run_build(spec, settings) is True
    assert spec.path.read_text() == "v1"
    assert run_build(spec, settings) is False  # cache hit


def test_source_change_triggers_rebuild(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = command_spec(tmp_path)
    settings = make_settings()
    run_build(spec, settings)
    source = tmp_path / "src.txt"
    source.write_text("v2")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 10_000_000_000))
    assert run_build(spec, settings) is True
    assert spec.path.read_text() == "v2"


def test_rebuild_flag_forces(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = command_spec(tmp_path)
    run_build(spec, make_settings())
    assert run_build(spec, make_settings(rebuild=True)) is True


def test_no_build_setting_skips(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = command_spec(tmp_path)
    assert run_build(spec, make_settings(build=False)) is False
    assert not spec.path.exists()


def test_build_false_spec_skips(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = dataclasses.replace(command_spec(tmp_path), build=False)
    assert run_build(spec, make_settings()) is False


def test_missing_output_is_an_error(tmp_path: Path, make_settings: MakeSettings) -> None:
    (tmp_path / "src.txt").write_text("v1")
    spec = ControllerSpec(robot="probe", path=tmp_path / "out.bin", build=("sh", "-c", "true"))
    with pytest.raises(BuildError, match="expected output"):
        run_build(spec, make_settings())


def test_unknown_backend_points_at_hook(tmp_path: Path, make_settings: MakeSettings) -> None:
    spec = ControllerSpec(robot="probe", path=tmp_path / "out.bin", build="cmake")
    with pytest.raises(BuildError, match="pytest_webots_build_controller"):
        run_build(spec, make_settings())


def test_custom_make_path_is_used(tmp_path: Path, make_settings: MakeSettings) -> None:
    fake_make = tmp_path / "tools" / "my-make"
    fake_make.parent.mkdir()
    fake_make.write_text('#!/bin/sh\n[ "$1" = "-C" ] && echo fake > "$2/out.bin"\n')
    fake_make.chmod(0o755)
    controller = tmp_path / "ctrl"
    controller.mkdir()
    (controller / "Makefile").write_text("all:\n")
    spec = ControllerSpec(robot="probe", path=controller / "out.bin", build="make")
    assert run_build(spec, make_settings(make=str(fake_make))) is True
    assert (controller / "out.bin").read_text().strip() == "fake"


def test_failing_command_raises_with_output(tmp_path: Path, make_settings: MakeSettings) -> None:
    (tmp_path / "src.txt").write_text("v1")
    spec = ControllerSpec(robot="probe", path=tmp_path / "out.bin", build=("sh", "-c", "echo broken >&2; exit 9"))
    with pytest.raises(BuildError, match="code 9") as excinfo:
        run_build(spec, make_settings())
    assert "broken" in str(excinfo.value)
