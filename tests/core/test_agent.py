import json
from pathlib import Path
from typing import Any

import pytest

from pytest_webots._core.supervisor.agent import _BUILTIN_OPS as BUILTIN_OPS
from pytest_webots._core.supervisor.agent import Agent, load_plugins
from pytest_webots._core.supervisor.proxy import SupervisorProxy, decode_result, encode_args


def _no_call(handle: int | None, method: str, args: list[Any]) -> Any:
    raise AssertionError("proxy should not be invoked in these tests")


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
    assert result == {"__handle__": 1}
    assert 1 in agent.handles
    agent.dispatch({"op": "release", "handle": 1})
    assert 1 not in agent.handles


def test_dict_result_crosses_as_data(agent: Agent) -> None:
    wire = json.loads(json.dumps(agent.encode({"speed": 1.5, "pose": [1, 2, 3], "ok": True})))
    assert decode_result(wire, _no_call) == {"speed": 1.5, "pose": [1, 2, 3], "ok": True}


def test_dict_result_carries_nested_proxies(agent: Agent) -> None:
    node = agent.dispatch({"op": "call", "target": None, "method": "getSelf", "args": []})
    wire = json.loads(json.dumps(agent.encode({"node": node, "height": 0.5})))
    result = decode_result(wire, _no_call)
    assert isinstance(result["node"], SupervisorProxy)
    assert result["height"] == 0.5


def test_dict_argument_resolves_nested_proxies(agent: Agent) -> None:
    node = agent.dispatch({"op": "call", "target": None, "method": "getSelf", "args": []})
    handle = agent.encode(node)["__handle__"]
    payload = encode_args([{"target": SupervisorProxy(_no_call, handle=handle), "threshold": 0.01}])[0]
    assert agent.decode(json.loads(json.dumps(payload))) == {"target": node, "threshold": 0.01}


def test_unknown_op_lists_available(agent: Agent) -> None:
    with pytest.raises(ValueError, match="unknown op: warp.*available.*ping"):
        agent.dispatch({"op": "warp"})


def test_plugin_registers_op(agent: Agent, tmp_path: Path) -> None:
    plugin = tmp_path / "ext.py"
    plugin.write_text(
        """
def register(agent):
    @agent.op("double")
    def double(agent, request):
        return request["x"] * 2
"""
    )
    load_plugins(agent, [str(plugin)])
    assert agent.dispatch({"op": "double", "x": 21}) == 42


@pytest.mark.parametrize("name", sorted(BUILTIN_OPS))
def test_builtin_ops_cannot_be_replaced(agent: Agent, name: str) -> None:
    with pytest.raises(ValueError, match=f"cannot replace built-in op '{name}'"):
        agent.op(name)
    assert agent.ops[name] is BUILTIN_OPS[name]


def test_plugin_claiming_builtin_fails_to_load(agent: Agent, tmp_path: Path) -> None:
    plugin = tmp_path / "clash.py"
    plugin.write_text(
        """
def register(agent):
    @agent.op("reset")
    def reset(agent, request):
        return None
"""
    )
    with pytest.raises(ValueError, match="cannot replace built-in op 'reset'"):
        load_plugins(agent, [str(plugin)])


def test_later_plugin_replaces_earlier_op(agent: Agent, tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    first.write_text(
        """
def register(agent):
    @agent.op("greet")
    def greet(agent, request):
        return "first"
"""
    )
    second = tmp_path / "second.py"
    second.write_text(
        """
def register(agent):
    @agent.op("greet")
    def greet(agent, request):
        return "second"
"""
    )
    load_plugins(agent, [str(first), str(second)])
    assert agent.dispatch({"op": "greet"}) == "second"


def test_broken_plugin_raises(agent: Agent, tmp_path: Path) -> None:
    plugin = tmp_path / "bad.py"
    plugin.write_text('raise RuntimeError("boom at import")\n')
    with pytest.raises(RuntimeError, match="boom at import"):
        load_plugins(agent, [str(plugin)])
