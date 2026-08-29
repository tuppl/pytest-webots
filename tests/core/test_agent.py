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
    SIMULATION_MODE_PAUSE = 0
    SIMULATION_MODE_REAL_TIME = 1
    SIMULATION_MODE_FAST = 2

    def __init__(self) -> None:
        self.modes: list[int] = []

    def simulationSetMode(self, mode: int) -> None:
        self.modes.append(mode)

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
    with pytest.raises(ValueError, match=r"unknown op: warp.*available.*ping"):
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


def test_set_mode_maps_names_to_supervisor_constants(agent: Agent) -> None:
    for name, constant in (("pause", 0), ("realtime", 1), ("fast", 2)):
        agent.dispatch({"op": "set_mode", "mode": name})
        assert agent.supervisor.modes[-1] == constant


def test_set_mode_rejects_unknown_modes(agent: Agent) -> None:
    with pytest.raises(ValueError, match=r"unknown simulation mode: warp.*fast.*pause.*realtime"):
        agent.dispatch({"op": "set_mode", "mode": "warp"})


class TickingSupervisor(FakeSupervisor):
    """
    A supervisor whose clock moves when stepped, unlike the fixed-time fake.
    """

    def __init__(self, stop_after: int | None = None, raise_after: int | None = None) -> None:
        super().__init__()
        self.time = 0.0
        self.steps = 0
        self.stop_after = stop_after
        self.raise_after = raise_after

    def getTime(self) -> float:
        return self.time

    def step(self, ms: int) -> int:
        self.steps += 1
        if self.raise_after is not None and self.steps > self.raise_after:
            raise RuntimeError("sim gone")
        if self.stop_after is not None and self.steps > self.stop_after:
            return -1
        self.time = round(self.time + ms / 1000.0, 9)
        return 0


def test_advance_to_runs_then_repauses() -> None:
    agent = Agent(TickingSupervisor())
    landed = agent.dispatch({"op": "advance_to", "target": 0.096, "mode": "fast"})
    assert landed == pytest.approx(0.096)
    assert agent.supervisor.modes == [FakeSupervisor.SIMULATION_MODE_FAST, FakeSupervisor.SIMULATION_MODE_PAUSE]


def test_advance_to_honours_realtime() -> None:
    agent = Agent(TickingSupervisor())
    agent.dispatch({"op": "advance_to", "target": 0.032, "mode": "realtime"})
    assert agent.supervisor.modes[0] == FakeSupervisor.SIMULATION_MODE_REAL_TIME


def test_advance_to_stops_when_the_simulation_ends() -> None:
    agent = Agent(TickingSupervisor(stop_after=2))
    landed = agent.dispatch({"op": "advance_to", "target": 0.320, "mode": "fast"})
    assert landed == pytest.approx(0.064)  # reports where it stopped, no hang


def test_advance_to_repauses_even_when_a_step_raises() -> None:
    # The finally is the guard against stranding the world running.
    agent = Agent(TickingSupervisor(raise_after=1))
    with pytest.raises(RuntimeError, match="sim gone"):
        agent.dispatch({"op": "advance_to", "target": 0.320, "mode": "fast"})
    assert agent.supervisor.modes[-1] == FakeSupervisor.SIMULATION_MODE_PAUSE
