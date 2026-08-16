from __future__ import annotations

from collections.abc import Generator

import pytest

from ._fixtures import SESSION_KEY


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when == "call" and report.failed:
        session = item.stash.get(SESSION_KEY, None)
        if session is not None:
            report.sections.append(("webots output", session.logs))
            for robot, process in session.controllers.items():
                report.sections.append((f"webots controller {robot!r}", process.logs))
    return report
