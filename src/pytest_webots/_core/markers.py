"""
Marker parsing: webots_world / webots_controller markers to specs; world path resolution.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest

if TYPE_CHECKING:
    from .config import Settings

WorldScope = Literal["function", "class", "module", "session"]
SCOPE_ORDER: tuple[WorldScope, ...] = ("function", "class", "module", "session")
SIMULATION_MODES: tuple[str, ...] = ("pause", "realtime", "fast")

_WORLD_KWARGS = frozenset({"scope", "mode", "args", "timeout"})
_CONTROLLER_KWARGS = frozenset({"build", "args", "env", "cwd", "autostart", "protocol", "ip_address"})

ControllerProtocol = Literal["ipc", "tcp"]


MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")  # make's own lookup order


def has_makefile(directory: Path) -> bool:
    return any((directory / name).is_file() for name in MAKEFILE_NAMES)


class MarkerError(Exception):
    """
    A webots marker is malformed or names an unresolvable world.
    """


@dataclass(frozen=True)
class FixtureRef:
    name: str


def fixture_ref(name: str) -> FixtureRef:
    """
    Defer a webots_controller marker value to a fixture, resolved at webots setup.
    """
    return FixtureRef(name)


def _ref_failure_detail(name: str, error: Exception) -> str:
    circular = (
        f"fixture {name!r} depends on the webots fixture, which is circular; "
        f"move the logic into the fixture, or use webots.launch_controller in the test"
    )
    if isinstance(error, AssertionError) and not str(error):
        return circular
    if isinstance(error, pytest.FixtureLookupError):
        if error.argname == "webots" and error.msg is None:
            return circular
        return error.msg or f"no fixture named {error.argname!r}"
    return f"{type(error).__name__}: {error}".rstrip(": ")


def _resolve_refs(value: Any, resolve: Callable[[str], Any], item: pytest.Item) -> Any:
    if isinstance(value, FixtureRef):
        try:
            return resolve(value.name)
        except Exception as error:
            raise MarkerError(
                f"{item.nodeid}: webots_controller could not resolve fixture_ref({value.name!r}): "
                f"{_ref_failure_detail(value.name, error)}"
            ) from error
    if isinstance(value, Mapping):
        return {key: _resolve_refs(v, resolve, item) for key, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, resolve, item) for v in value]
    if isinstance(value, tuple):
        return tuple(_resolve_refs(v, resolve, item) for v in value)
    return value


def _contains_ref(value: Any) -> bool:
    if isinstance(value, FixtureRef):
        return True
    if isinstance(value, Mapping):
        return any(_contains_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_ref(v) for v in value)
    return False


@dataclass(frozen=True)
class WorldSpec:
    path: Path
    scope: WorldScope = "session"
    mode: str | None = None
    args: tuple[str, ...] = ()
    timeout: float | None = None
    label: str = field(default="", compare=False)

    def __str__(self) -> str:
        return self.label or self.path.stem


@dataclass(frozen=True)
class ControllerSpec:
    robot: str
    path: Path
    build: str | tuple[str, ...] | Literal[False] | None = None  # None = auto-detect, False = never build
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    autostart: bool = True
    protocol: ControllerProtocol = "ipc"
    ip_address: str | None = None

    def __str__(self) -> str:
        return self.robot


def collect_world_specs(
    definition: pytest.Function,
    settings: Settings,
    rootpath: Path,
    resolve_hook: Callable[[str], Path | None],
) -> list[WorldSpec]:
    """
    Build one WorldSpec per stacked webots_world marker on the closest node that has any.
    """
    pairs = list(definition.iter_markers_with_node("webots_world"))
    if not pairs:
        return []
    # Marker application order, matching pytest's stacked-parametrize convention:
    # bottom-most decorator first; pytestmark lists in list order.
    closest = pairs[0][0]
    marks = [mark for node, mark in pairs if node is closest]

    specs: list[WorldSpec] = []
    seen: set[Path] = set()
    for mark in marks:
        spec = _world_spec(mark, definition, settings, rootpath, resolve_hook)
        if spec.path in seen:
            raise MarkerError(
                f"{definition.nodeid}: world {spec.path} appears more than once in the webots_world stack"
            )
        seen.add(spec.path)
        specs.append(spec)
    return specs


def _world_spec(
    mark: pytest.Mark,
    definition: pytest.Function,
    settings: Settings,
    rootpath: Path,
    resolve_hook: Callable[[str], Path | None],
) -> WorldSpec:
    if len(mark.args) != 1:
        raise MarkerError(
            f"{definition.nodeid}: webots_world takes exactly one world path per marker, got {mark.args!r}"
        )
    unknown = set(mark.kwargs) - _WORLD_KWARGS
    if unknown:
        raise MarkerError(f"{definition.nodeid}: webots_world got unexpected kwargs {sorted(unknown)}")
    if any(_contains_ref(v) for v in (*mark.args, *mark.kwargs.values())):
        raise MarkerError(
            f"{definition.nodeid}: webots_world cannot take a fixture_ref: worlds resolve at collection time, "
            f"before any fixture exists. Use the pytest_webots_resolve_world hook instead."
        )
    scope = mark.kwargs.get("scope", "session")
    if scope not in SCOPE_ORDER:
        raise MarkerError(f"{definition.nodeid}: webots_world scope must be one of {SCOPE_ORDER}, got {scope!r}")
    mode = mark.kwargs.get("mode")
    if mode is not None and mode not in SIMULATION_MODES:
        raise MarkerError(f"{definition.nodeid}: webots_world mode must be one of {SIMULATION_MODES}, got {mode!r}")
    name = str(mark.args[0])
    path = _resolve_world(name, definition, settings, rootpath, resolve_hook)
    args = tuple(str(a) for a in mark.kwargs.get("args") or ())
    timeout = mark.kwargs.get("timeout")
    return WorldSpec(
        path=path,
        scope=scope,
        mode=mode,
        args=args,
        timeout=float(timeout) if timeout is not None else None,
        label=name,
    )


def _resolve_world(
    name: str,
    definition: pytest.Function,
    settings: Settings,
    rootpath: Path,
    resolve_hook: Callable[[str], Path | None],
) -> Path:
    hooked = resolve_hook(name)
    if hooked is not None:
        return Path(hooked).resolve()
    given = Path(name).expanduser()
    if given.is_absolute():
        if given.is_file():
            return given
        raise MarkerError(f"{definition.nodeid}: world file not found: {given}")
    tried = []
    anchors = []
    if settings.worlds_dir is not None:
        anchors.append(settings.worlds_dir)
    anchors.append(definition.path.parent)
    anchors.append(rootpath)
    for anchor in anchors:
        candidate = anchor / given
        if candidate.is_file():
            return candidate.resolve()
        tried.append(candidate)
    locations = "\n  ".join(str(p) for p in tried)
    raise MarkerError(f"{definition.nodeid}: world {name!r} not found; tried:\n  {locations}")


def collect_controller_specs(
    item: pytest.Item,
    rootpath: Path,
    resolve_fixture: Callable[[str], Any],
) -> list[ControllerSpec]:
    """
    Build one ControllerSpec per stacked webots_controller marker on the closest node that has any.

    Marker application order is launch order, same convention as worlds.
    """
    pairs = list(item.iter_markers_with_node("webots_controller"))
    if not pairs:
        return []
    closest = pairs[0][0]
    marks = [mark for node, mark in pairs if node is closest]

    specs: list[ControllerSpec] = []
    seen: set[str] = set()
    for mark in marks:
        spec = _controller_spec(mark, item, rootpath, resolve_fixture)
        if spec.robot in seen:
            raise MarkerError(f"{item.nodeid}: robot {spec.robot!r} has more than one webots_controller marker")
        seen.add(spec.robot)
        specs.append(spec)
    return specs


def _controller_spec(
    mark: pytest.Mark,
    item: pytest.Item,
    rootpath: Path,
    resolve_fixture: Callable[[str], Any],
) -> ControllerSpec:
    if len(mark.args) != 2:
        raise MarkerError(f"{item.nodeid}: webots_controller takes (robot, path) positionally, got {mark.args!r}")
    unknown = set(mark.kwargs) - _CONTROLLER_KWARGS
    if unknown:
        raise MarkerError(f"{item.nodeid}: webots_controller got unexpected kwargs {sorted(unknown)}")
    args = _resolve_refs(tuple(mark.args), resolve_fixture, item)
    kwargs = _resolve_refs(dict(mark.kwargs), resolve_fixture, item)
    robot = str(args[0])
    build = kwargs.get("build")
    path = _resolve_controller(str(args[1]), item, rootpath, build=build)
    cwd = kwargs.get("cwd")
    protocol = kwargs.get("protocol", "ipc")
    ip_address = kwargs.get("ip_address")
    if protocol not in ("ipc", "tcp"):
        raise MarkerError(f"{item.nodeid}: webots_controller protocol must be 'ipc' or 'tcp', got {protocol!r}")
    if ip_address is not None and protocol != "tcp":
        raise MarkerError(f"{item.nodeid}: webots_controller ip_address requires protocol='tcp'")
    return ControllerSpec(
        robot=robot,
        path=path,
        build=tuple(build) if isinstance(build, (list, tuple)) else build,
        args=tuple(str(a) for a in kwargs.get("args") or ()),
        env={str(k): str(v) for k, v in (kwargs.get("env") or {}).items()},
        cwd=_resolve_anchored(str(cwd), item, rootpath) if cwd else None,
        autostart=bool(kwargs.get("autostart", True)),
        protocol=protocol,
        ip_address=str(ip_address) if ip_address is not None else None,
    )


def _resolve_controller(name: str, item: pytest.Item, rootpath: Path, build: object = None) -> Path:
    resolved = _resolve_anchored(name, item, rootpath)
    if resolved.is_file():
        return resolved
    binary = resolved / (f"{resolved.name}.exe" if sys.platform == "win32" else resolved.name)
    candidates = [binary, resolved / f"{resolved.name}.py"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if build is not False and (build is not None or has_makefile(resolved)):
        return binary.resolve()
    tried = "\n  ".join(str(c) for c in candidates)
    raise MarkerError(
        f"{item.nodeid}: no controller found for {name!r}; tried:\n  {tried}\n"
        f"Nothing there can build one either: add a makefile to {resolved}, or pass build= with a command."
    )


def _resolve_anchored(name: str, item: pytest.Item, rootpath: Path) -> Path:
    """
    Resolve against stable anchors, never the process CWD (other plugins chdir per test).
    """
    given = Path(name).expanduser()
    if given.is_absolute():
        if given.exists():
            return given
        raise MarkerError(f"{item.nodeid}: path not found: {given}")
    tried = []
    for anchor in (item.path.parent, rootpath):
        candidate = anchor / given
        if candidate.exists():
            return candidate.resolve()
        tried.append(candidate)
    locations = "\n  ".join(str(p) for p in tried)
    raise MarkerError(f"{item.nodeid}: path {name!r} not found; tried:\n  {locations}")


def world_ids(specs: Sequence[WorldSpec]) -> list[str]:
    return [str(spec) for spec in specs]


def widest_scope(specs: Sequence[WorldSpec]) -> WorldScope:
    return SCOPE_ORDER[max(SCOPE_ORDER.index(spec.scope) for spec in specs)]
