"""
Settings resolution: ini/CLI merge and WEBOTS_HOME discovery.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

if sys.platform == "darwin":
    _BINARY = Path("Contents/MacOS/webots")
    _LAUNCHER = Path("Contents/MacOS/webots-controller")
    _DEFAULT_HOMES = (Path("/Applications/Webots.app"),)
elif sys.platform == "win32":
    _BINARY = Path("msys64/mingw64/bin/webots.exe")
    _LAUNCHER = Path("msys64/mingw64/bin/webots-controller.exe")
    _DEFAULT_HOMES = (Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Webots",)
else:
    _BINARY = Path("webots")
    _LAUNCHER = Path("webots-controller")
    _DEFAULT_HOMES = (Path("/usr/local/webots"), Path("/snap/webots/current/usr/share/webots"))


def webots_binary(home: Path) -> Path:
    return home / _BINARY


def controller_launcher(home: Path) -> Path:
    return home / _LAUNCHER


def python_controller_path(home: Path) -> Path:
    """
    Directory of the Webots-bundled pure-Python controller package.
    """
    if sys.platform == "darwin":
        return home / "Contents/lib/controller/python"
    return home / "lib/controller/python"


def make_home(home: Path) -> Path:
    # macOS expects a /Contents
    if sys.platform == "darwin":
        return home / "Contents"
    return home


def default_make(home: Path | None) -> str:
    """
    Windows installs rarely have make on PATH, but Webots packages one.
    """
    if sys.platform == "win32" and home is not None:
        packaged = home / "msys64/mingw64/bin/make.exe"
        if packaged.exists():
            return str(packaged)
    return "make"


def discover_webots_home(explicit: str | None) -> Path | None:
    """
    Find a Webots installation: explicit setting, then WEBOTS_HOME, then platform defaults.
    """
    if explicit:
        home = Path(explicit).expanduser()
        if not webots_binary(home).exists():
            raise pytest.UsageError(f"webots binary not found under configured Webots home: {home}")
        return home
    candidates = []
    env = os.environ.get("WEBOTS_HOME")
    if env:
        candidates.append(Path(env))
    candidates.extend(_DEFAULT_HOMES)
    for home in candidates:
        if webots_binary(home).exists():
            return home
    return None


@dataclass(frozen=True)
class Settings:
    home: Path | None
    worlds_dir: Path | None
    mode: str
    headless: bool
    extra_args: tuple[str, ...]
    startup_timeout: float
    max_restarts: int
    port_base: int
    supervisor_name: str
    inject_supervisor: bool
    build: bool
    rebuild: bool
    keep_alive: bool
    worker_id: str | None
    make: str = "make"

    @property
    def webots_binary(self) -> Path:
        if self.home is None:
            raise pytest.UsageError(
                "no Webots installation found; set WEBOTS_HOME, the webots_home ini value, or --webots-home"
            )
        return webots_binary(self.home)

    @property
    def controller_launcher(self) -> Path:
        if self.home is None:
            raise pytest.UsageError(
                "no Webots installation found; set WEBOTS_HOME, the webots_home ini value, or --webots-home"
            )
        return controller_launcher(self.home)

    @classmethod
    def from_config(cls, config: pytest.Config) -> Settings:
        explicit = config.getoption("--webots-home") or config.getini("webots_home") or None
        worlds_dir = config.getini("webots_worlds_dir")
        workerinput = getattr(config, "workerinput", None)
        home = discover_webots_home(explicit)
        return cls(
            home=home,
            worlds_dir=config.rootpath / worlds_dir if worlds_dir else None,
            mode=config.getoption("--webots-mode") or config.getini("webots_mode"),
            headless=not config.getoption("--webots-gui") and config.getini("webots_headless"),
            extra_args=tuple(config.getini("webots_args")),
            startup_timeout=config.getoption("--webots-startup-timeout")
            or float(config.getini("webots_startup_timeout")),
            max_restarts=int(config.getini("webots_max_restarts")),
            port_base=config.getoption("--webots-port-base") or int(config.getini("webots_port_base")),
            supervisor_name=config.getini("webots_supervisor_name"),
            inject_supervisor=not config.getoption("--webots-no-inject") and config.getini("webots_inject_supervisor"),
            build=not config.getoption("--webots-no-build") and config.getini("webots_build"),
            rebuild=config.getoption("--webots-rebuild"),
            keep_alive=config.getoption("--webots-keep-alive"),
            worker_id=workerinput["workerid"] if workerinput else None,
            make=config.getini("webots_make") or default_make(home),
        )


SETTINGS_KEY: pytest.StashKey[Settings] = pytest.StashKey()
