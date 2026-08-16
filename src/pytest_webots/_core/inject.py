"""
Supervisor robot injection into copied .wbt world files.
"""

from __future__ import annotations

from pathlib import Path

from .errors import WebotsError

_SUPERVISOR_TEMPLATE = """
Robot {{
  name "{name}"
  controller "<extern>"
  supervisor TRUE
  synchronization FALSE
}}
"""


def inject_supervisor(world: Path, name: str, token: str) -> Path:
    """
    Copy the world to a sibling file with a supervisor Robot appended.

    The copy lives in the same directory as the original so relative
    EXTERNPROTO and texture paths still resolve.
    """
    text = world.read_text()
    if not text.startswith("#VRML_SIM"):
        raise WebotsError(f"{world} does not look like a Webots world file (missing #VRML_SIM header)")
    if f'"{name}"' in text:
        raise WebotsError(
            f"{world} already contains a robot named {name!r}; rename it, change webots_supervisor_name, "
            f"or disable injection with --webots-no-inject"
        )
    injected = world.with_name(f"{world.stem}.pytest-{token}.wbt")
    injected.write_text(text + _SUPERVISOR_TEMPLATE.format(name=name))
    return injected


def remove_injected(injected: Path | None) -> None:
    if injected is not None:
        injected.unlink(missing_ok=True)
        injected.with_name(f".{injected.stem}.wbproj").unlink(missing_ok=True)
