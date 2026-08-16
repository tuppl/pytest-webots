from __future__ import annotations

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.pluginmanager.hasplugin("xdist"):
        return
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None:
            continue
        spec = callspec.params.get("webots_world")
        if spec is not None:
            item.add_marker(pytest.mark.xdist_group(f"webots:{spec.path}"))
