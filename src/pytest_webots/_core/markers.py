"""
Marker parsing: webots_world / webots_controller markers to specs; world path resolution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pytest

    from .config import Settings

WorldScope = Literal["function", "class", "module", "session"]
SCOPE_ORDER: tuple[WorldScope, ...] = ("function", "class", "module", "session")

_WORLD_KWARGS = frozenset({"scope", "mode", "args", "timeout"})
_CONTROLLER_KWARGS = frozenset({"build", "args", "env", "cwd", "autostart", "protocol", "ip_address"})

ControllerProtocol = Literal["ipc", "tcp"]


class MarkerError(Exception):
    """
    A webots marker is malformed or names an unresolvable world.
    """


@dataclass(frozen=True)
class WorldSpec:
    path: Path
    scope: WorldScope = "session"
    mode: str | None = None
    args: tuple[str, ...] = ()
    timeout: float | None = None


@dataclass(frozen=True)
class ControllerSpec:
    robot: str
    path: Path
    build: str | tuple[str, ...] | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    autostart: bool = True
    protocol: ControllerProtocol = "ipc"
    ip_address: str | None = None


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
    scope = mark.kwargs.get("scope", "session")
    if scope not in SCOPE_ORDER:
        raise MarkerError(f"{definition.nodeid}: webots_world scope must be one of {SCOPE_ORDER}, got {scope!r}")
    name = str(mark.args[0])
    path = _resolve_world(name, definition, settings, rootpath, resolve_hook)
    args = tuple(str(a) for a in mark.kwargs.get("args") or ())
    timeout = mark.kwargs.get("timeout")
    return WorldSpec(
        path=path,
        scope=scope,
        mode=mark.kwargs.get("mode"),
        args=args,
        timeout=float(timeout) if timeout is not None else None,
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


def collect_controller_specs(item: pytest.Item, rootpath: Path) -> list[ControllerSpec]:
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
        spec = _controller_spec(mark, item, rootpath)
        if spec.robot in seen:
            raise MarkerError(f"{item.nodeid}: robot {spec.robot!r} has more than one webots_controller marker")
        seen.add(spec.robot)
        specs.append(spec)
    return specs


def _controller_spec(mark: pytest.Mark, item: pytest.Item, rootpath: Path) -> ControllerSpec:
    if len(mark.args) != 2:
        raise MarkerError(f"{item.nodeid}: webots_controller takes (robot, path) positionally, got {mark.args!r}")
    unknown = set(mark.kwargs) - _CONTROLLER_KWARGS
    if unknown:
        raise MarkerError(f"{item.nodeid}: webots_controller got unexpected kwargs {sorted(unknown)}")
    robot = str(mark.args[0])
    path = _resolve_controller(str(mark.args[1]), item, rootpath)
    cwd = mark.kwargs.get("cwd")
    build = mark.kwargs.get("build")
    protocol = mark.kwargs.get("protocol", "ipc")
    ip_address = mark.kwargs.get("ip_address")
    if protocol not in ("ipc", "tcp"):
        raise MarkerError(f"{item.nodeid}: webots_controller protocol must be 'ipc' or 'tcp', got {protocol!r}")
    if ip_address is not None and protocol != "tcp":
        raise MarkerError(f"{item.nodeid}: webots_controller ip_address requires protocol='tcp'")
    return ControllerSpec(
        robot=robot,
        path=path,
        build=tuple(build) if isinstance(build, (list, tuple)) else build,
        args=tuple(str(a) for a in mark.kwargs.get("args") or ()),
        env={str(k): str(v) for k, v in (mark.kwargs.get("env") or {}).items()},
        cwd=_resolve_anchored(str(cwd), item, rootpath) if cwd else None,
        autostart=bool(mark.kwargs.get("autostart", True)),
        protocol=protocol,
        ip_address=str(ip_address) if ip_address is not None else None,
    )


def _resolve_controller(name: str, item: pytest.Item, rootpath: Path) -> Path:
    resolved = _resolve_anchored(name, item, rootpath)
    if resolved.is_file():
        return resolved
    # A directory means the Webots layout: controllers/<name>/<name>[.py]
    candidates = [resolved / resolved.name, resolved / f"{resolved.name}.py"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    tried = "\n  ".join(str(c) for c in candidates)
    raise MarkerError(f"{item.nodeid}: no controller found for {name!r}; tried:\n  {tried}")


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
    """
    World IDs from file stems; parent-directory prefixes disambiguate colliding stems.
    """
    stems = [spec.path.stem for spec in specs]
    ids = []
    for spec, stem in zip(specs, stems):
        ids.append(f"{spec.path.parent.name}-{stem}" if stems.count(stem) > 1 else stem)
    return ids


def widest_scope(specs: Sequence[WorldSpec]) -> WorldScope:
    return SCOPE_ORDER[max(SCOPE_ORDER.index(spec.scope) for spec in specs)]
