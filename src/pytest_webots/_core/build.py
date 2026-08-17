"""
Controller build backends: make, cmake, raw command; caching and locking.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock

from .config import make_home
from .errors import BuildError

if TYPE_CHECKING:
    from .config import Settings
    from .markers import ControllerSpec

_STAMP = ".pytest-webots.build-stamp"
_LOCK = ".pytest-webots.build-lock"
_BUILD_TIMEOUT = 300.0
_EXCLUDED_SUFFIXES = {".o", ".d", ".class", ".jar"}


def run_build(spec: ControllerSpec, settings: Settings) -> bool:
    """
    Build the controller if its backend and cache say so; True when a build ran.

    Safe under pytest-xdist: a file lock in the controller directory serializes
    workers, and the source hash lets all but the first skip the work.
    """
    if not settings.build or spec.build is False:
        return False
    directory = spec.path.parent
    command = _backend_command(spec, directory, settings)
    if command is None:
        return False

    with FileLock(directory / _LOCK):
        stamp = directory / _STAMP
        digest = _source_digest(directory, spec.path)
        if not settings.rebuild and stamp.exists() and stamp.read_text() == digest and spec.path.exists():
            return False
        _run(command, directory, settings)
        if not spec.path.exists():
            raise BuildError(f"build of {directory} succeeded but expected output {spec.path} is missing")
        stamp.write_text(_source_digest(directory, spec.path))  # rehash
    return True


def _backend_command(spec: ControllerSpec, directory: Path, settings: Settings) -> list[list[str]] | None:
    if isinstance(spec.build, tuple):
        return [list(spec.build)]
    if spec.build == "make" or (spec.build is None and (directory / "Makefile").is_file()):
        return [[settings.make, "-C", str(directory)]]
    if spec.build is None:
        return None
    raise BuildError(
        f"no built-in backend for build={spec.build!r} ({directory}); built-ins are 'make' and a raw "
        f"command tuple. Other build systems integrate via the pytest_webots_build_controller hook."
    )


def _run(commands: list[list[str]], directory: Path, settings: Settings) -> None:
    env = os.environ.copy()
    if settings.home is not None:
        env["WEBOTS_HOME"] = str(settings.home)
        env["WEBOTS_HOME_PATH"] = str(make_home(settings.home))
    for command in commands:
        result = subprocess.run(
            command,
            cwd=str(directory),
            env=env,
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            raise BuildError(
                f"build command {' '.join(command)} failed with code {result.returncode}:\n"
                f"{result.stdout}\n{result.stderr}"
            )


def _source_digest(directory: Path, output: Path) -> str:
    """
    Hash of (path, mtime, size) for every source file; build outputs excluded.

    Seeded with the platform so a binary built on one OS or arch never
    cache-hits on another (e.g. a repo volume shared with a container).
    """
    entries = [f"{sys.platform}-{os.uname().machine if hasattr(os, 'uname') else ''}"]
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if relative.parts[0] == "build" or relative.name.startswith(".pytest-webots."):
            continue
        if path == output or path.suffix in _EXCLUDED_SUFFIXES:
            continue
        if sys.platform == "win32" and path.suffix == ".exe":
            continue
        stat = path.stat()
        entries.append(f"{relative}\0{stat.st_mtime_ns}\0{stat.st_size}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()
