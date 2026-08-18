from pathlib import Path

import pytest

from pytest_webots._core.supervisor.agent import Agent, load_plugins


class FakeSupervisor:
    def getBasicTimeStep(self) -> float:
        return 32.0

    def step(self, ms: int) -> int:
        self.last_step = ms
        return 0

    def getTime(self) -> float:
        return 1.5

    def getSelf(self) -> object:
        return object()


@pytest.fixture
def agent() -> Agent:
    return Agent(FakeSupervisor())


def test_builtin_ops(agent: Agent) -> None:
    assert agent.dispatch({"op": "ping"}) == "pong"
    assert agent.dispatch({"op": "basic_time_step"}) == 32
    assert agent.dispatch({"op": "time"}) == 1.5
    assert agent.dispatch({"op": "step", "ms": None}) == 0
    assert agent.supervisor.last_step == 32


def test_call_and_handle_lifecycle(agent: Agent) -> None:
    result = agent.encode(agent.dispatch({"op": "call", "target": None, "method": "getSelf", "args": []}))
    assert result["__handle__"] == 1
    assert result["__type__"] == "object"
    assert 1 in agent.handles
    agent.dispatch({"op": "release", "handle": 1})
    assert 1 not in agent.handles


def test_unknown_op_lists_available(agent: Agent) -> None:
    with pytest.raises(ValueError, match="unknown op: warp.*available.*ping"):
        agent.dispatch({"op": "warp"})


def test_plugin_registers_and_overrides(agent: Agent, tmp_path: Path) -> None:
    plugin = tmp_path / "ext.py"
    plugin.write_text(
        """
def register(agent):
    @agent.op("double")
    def double(agent, request):
        return request["x"] * 2

    @agent.op("ping")
    def ping(agent, request):
        return "overridden"
"""
    )
    load_plugins(agent, [str(plugin)])
    assert agent.dispatch({"op": "double", "x": 21}) == 42
    assert agent.dispatch({"op": "ping"}) == "overridden"


def test_broken_plugin_raises(agent: Agent, tmp_path: Path) -> None:
    plugin = tmp_path / "bad.py"
    plugin.write_text('raise RuntimeError("boom at import")\n')
    with pytest.raises(RuntimeError, match="boom at import"):
        load_plugins(agent, [str(plugin)])
