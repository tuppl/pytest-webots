from __future__ import annotations

from pathlib import Path

import pytest

from ._core.config import SETTINGS_KEY
from ._core.markers import collect_world_specs, widest_scope, world_ids


@pytest.hookimpl(tryfirst=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "webots_world" not in metafunc.fixturenames:
        return
    config = metafunc.config

    def resolve_hook(name: str) -> Path | None:
        return config.hook.pytest_webots_resolve_world(name=name, config=config)

    specs = collect_world_specs(metafunc.definition, config.stash[SETTINGS_KEY], config.rootpath, resolve_hook)
    if not specs:
        return
    metafunc.parametrize("webots_world", specs, indirect=True, ids=world_ids(specs), scope=widest_scope(specs))
