import pytest

from pytest_webots import WebotsInstance

pytest_plugins = ["pytester"]

BOOTS: list[str] = []


def pytest_webots_world_started(instance: WebotsInstance) -> None:
    BOOTS.append(instance.spec.path.name)


@pytest.fixture
def world_boots() -> list[str]:
    return BOOTS
