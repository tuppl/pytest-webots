# Guide

## Running a test with a world

pytest-webots lets you run a Webots world in conjunction with a test. It will also manage the world's boot, reset, shutdown, and logging for you. This is enabled when using a marker:

```python
@pytest.mark.webots_world(path,
                          *,
                          scope="session",
                          mode=None,
                          args=None,
                          timeout=None)
def test_world(webots): ...
```

The marker has the following parameters:

| parameter | type | description |
|---|---|---|
| `path` | `str \| Path` | Path to the Webots world file. Relative paths resolve against `webots_worlds_dir`, then the test file's directory, then root directory. |
| `scope` | `"session" \| "module" \| "class" \| "function"` | World lifetime. `session` boots once and resets between tests, `function` boots fresh every test. |
| `mode` | `"realtime" \| "fast"` | Simulation mode for this world, overriding `webots_mode`. |
| `args` | `Sequence[str]` | Extra Webots arguments for this world. |
| `timeout` | `float` | Boot timeout in seconds for this world. |

Tests will automatically boot up the worlds they need. If multiple tests utilise the same world, pytest-webots will keep that world open so that it only needs to be booted once. The world state is reset between each test. When there are no more tests to run for that world, it is shutdown.

If the world crashes, the currently-running test using the world will fail with `WebotsCrashedError`. The next test that needs the world will boot it again. However, if a test consecutively fails to boot up a world `webots_max_restarts` times then that test and all future tests needing that world will error out instead of retrying.

For example, these two tests will use the same world file:

```python
@pytest.mark.webots_world("worlds/arena.wbt")
def test_move(webots):
    ball = webots.supervisor.getFromDef("BALL")
    ball.getField("translation").setSFVec3f([0, 0, 5])


@pytest.mark.webots_world("worlds/arena.wbt")
def test_fresh(webots):
    ball = webots.supervisor.getFromDef("BALL")
    assert ball.getPosition() == pytest.approx([0.0, 0.0, 1.0])
```

`arena.wbt` will be booted once. `test_move` will run its test then the world is reset. `test_fresh` will run its test but since it's the last test requiring the world, `arena.wbt` will shutdown.

If the simulation was quit, a `WebotsQuitError` will be raised.

## The `webots` fixture

A `webots` fixture is provided that allows you to control the world from pytest. It has the following properties:

| property | description |
|---|---|
| `webots.supervisor` | Proxy to the [Supervisor API](https://cyberbotics.com/doc/reference/supervisor) inside Webots. Chained as `.getFromDef("X").getField("translation").setSFVec3f([...])`. |
| `webots.step(ms=None)` | Advance the supervisor by `ms`. Default is one basic time step. |
| `webots.reset()` | `simulationReset` plus physics reset. |
| `webots.reload()` | Reload the world from disk. Any node taken beforehand stops working. |
| `webots.sim_time()` | Simulation time in seconds, as of the supervisor's last step. It does not tick on its own: call `webots.step()` first to read the current time. |
| `webots.controllers` | `dict[str, ControllerProcess]` — `.alive`, `.returncode`, `.logs`, `.restart()`. |
| `webots.launch_controller(robot, path=None, ...)` | Launch a controller mid-test, or start an `autostart=False` one. |
| `webots.ops.my_op(x=1)` | Call an op you registered on the agent. |
| `webots.logs` | Captured Webots output. |
| `webots.world` | The Webots process behind the simulation. |
| `webots.world.world_path` | The world file this instance was booted from. |
| `webots.world.port` | The TCP port Webots is actually serving. |
| `webots.world.robots` | `dict[str, str]` mapping each extern robot to the URL it announced at boot. |
| `webots.world.alive` / `.pid` | Process state. |
| `webots.world.kill()` | Hard-kill the whole Webots process group. |
| `webots.world.output()` | Captured Webots output. |
| `webots.world.command()` | The argv this instance was launched with. |
| `webots.world.spec` / `.settings` | The resolved `WorldSpec` and `Settings`. |


## Running a test with a controller

An external controller can be attached to the world for a test:

```python
@pytest.mark.webots_controller(robot,
                               path,
                               *,
                               build=None,
                               args=None,
                               env=None,
                               cwd=None,
                               autostart=True,
                               protocol="ipc",
                               ip_address=None)
def test_controller(webots): ...
```

The marker has the following parameters:

| parameter | type | description |
|---|---|---|
| `robot` | `str` | The `name` of a `Robot` node in the world, which must set `controller "<extern>"`. |
| `path` | `str \| Path \| FixtureRef` | The controller directory in Webots layout — `controllers/my_bot/` runs `my_bot`, `my_bot.exe` or `my_bot.py` inside it. A path to a single file also works for controllers not using the Webots layout. Relative paths resolve against the test file's directory, then the root directory. |
| `build` | `str \| Sequence[str] \| False \| None` | Build command which dispatches to `pytest_webots_build_controller`. `False` does not build the controller. Leaving this alone will build the `Makefile` automatically (if one exists). |
| `args` | `Sequence[str \| FixtureRef]` | Extra arguments for the controller process. |
| `env` | `Mapping[str, str \| FixtureRef]` | Extra environment variables for the controller process. |
| `cwd` | `str \| Path \| FixtureRef` | Working directory for the controller process. Defaults to the directory holding it. |
| `autostart` | `bool` | `False` declares the controller without launching it, leaving the test to decide when it connects. |
| `protocol` | `"ipc" \| "tcp"` | External controller protocol. |
| `ip_address` | `str` | Webots host to reach over TCP. Requires `protocol="tcp"`. |

The Webots world must have a robot with a `name` and `controller "<extern>"`:

```
Robot {
  name "my_bot"
  controller "<extern>"
}
```

```python
@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("my_bot", "controllers/my_bot")
def test_bot(webots):
    assert webots.controllers["my_bot"].alive
```

At test setup, every declared controller is built, with build results cached against the controller's source files. A failed build fails every test declaring that controller (until the source changes, prompting a rebuild). Successfully-built controllers are then launched, each confirmed connected before the test body runs.

Controllers are always scoped per test.

Python controllers run under the pytest interpreter with Webots' `controller` package on `PYTHONPATH`, so it can import your virtual environment. Any other controller starts through Webots' `webots-controller` launcher.

## Controller values from fixtures

A controller marker value can come from a fixture with `fixture_ref` instead of a path string. Pass the fixture's *name* to resolve the value at test setup before any controller builds or launches:

```python
from pytest_webots import fixture_ref


@pytest.fixture
def controller_path(tmp_path):
    return build_something(tmp_path)


@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("probe", fixture_ref("controller_path"))
def test_thing(webots):
    assert webots.controllers["probe"].alive
```

The referenced fixture does not need to appear in the test signature, and may be of any scope, from any plugin, and parameterised. Any marker value may be a `fixture_ref`, including nested inside a list, tuple or dict value. Combined with `@pytest.mark.parametrize`, one marker can launch a different controller per parameter:

```python
@pytest.fixture
def role_flag(role):
    return f"--role={role}"


@pytest.mark.parametrize("role", ["striker", "keeper"])
@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("player", "controllers/player", args=[fixture_ref("role_flag")])
def test_roles(webots, role): ...
```

`webots_world` markers cannot take a `fixture_ref` since worlds parameterise the test at collection time, before any fixture exists. Use the `pytest_webots_resolve_world` hook to compute world paths instead.

## Synchronous vs asynchronous controllers

It is strongly recommended your robots are synchronous so that Webots will wait for each of your controllers to step before advancing the simulation time - this will yield the most predictable robot behaviour.

If all the robots in the world are asynchronous, the robots will run their programs and it's unknown how far the simulation time will progress. The simulation **can** advance before the test body runs.

## Running a test with multiple controllers

Multiple external controllers can be attached to the world for a test:

```python
@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("follower", "controllers/follower")
@pytest.mark.webots_controller("leader", "controllers/leader")
def test_pair(webots): ...
```

The bottom-most controller will launch first with each controller confirmed connected before the next controller launches.

Any controller that crashes will not take down its peers or the test. Crashed controllers are not restarted but it is possible to relaunch it mid-test.

## Launching a controller mid-test

`webots.launch_controller` is for launches that depend on something happening *during* the test — a precondition staged in the world, a controller started partway through a scenario. If the launch merely depends on a value computed before the test, keep it declarative with `fixture_ref` instead.

Declare the controller marker with `autostart=False`, then use the `webots` fixture to launch it once the precondition is in place:

```python
@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("explorer", "controllers/explorer", autostart=False)
def test_steers_around_a_crate(webots):
    # Precondition: put crate in front of the robot.
    crate = webots.supervisor.getFromDef("CRATE")
    crate.getField("translation").setSFVec3f([2, 0, 0.5])

    # Launch the controller by name.
    webots.launch_controller("explorer")
```

Alternatively, a controller can be launched without a declared marker:

```python
@pytest.mark.webots_world("worlds/arena.wbt")
def test_steers_around_a_crate(webots):
    # Launch the controller by name and path.
    webots.launch_controller("explorer", "controllers/explorer2")
```

Crashed controllers can also be restarted:

```python
explorer = webots.controllers["explorer"]
if not explorer.alive:
    explorer.restart()
```

## Parameterising a test with multiple worlds

A test case can be parameterised to run over multiple worlds by stacking markers:

```python
@pytest.mark.webots_world("worlds/maze.wbt")
@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.parametrize("speed", [1.0, 2.0])
def test_drive(webots, speed): ...
```

The above will create 4 unique tests:

```
test_drive[worlds/arena.wbt-1.0]
test_drive[worlds/arena.wbt-2.0]
test_drive[worlds/maze.wbt-1.0]
test_drive[worlds/maze.wbt-2.0]
```

Each marker carries its own `scope`, and can be different for each stacked marker. Mixing them gives each world its own lifetime:

```python
@pytest.mark.webots_world("worlds/maze.wbt", scope="function")
@pytest.mark.webots_world("worlds/arena.wbt")  # default: session
@pytest.mark.parametrize("run", [1, 2])
def test_mixed(webots, run): ...
```

Over those four runs `arena.wbt` boots once and is reset between its two tests, while `maze.wbt` boots and shuts down again for each of its two.

## Module-scoped worlds and controllers

To give every test in a module (or class) the same worlds and controllers, assign the markers to `pytestmark`:

```python
pytestmark = [
    pytest.mark.webots_world("worlds/arena.wbt"),
    pytest.mark.webots_controller("alice", "controllers/alice"),
    pytest.mark.webots_controller("bob", "controllers/bob"),
]


def test_one(webots): ...
def test_two(webots): ...
```

The Webot's world scope still defaults to `session` despite module-placement. If you need module scope then pass `scope="module"`.

Tests that have their own world markers will **ignore** world markers from `pytestmark`, and like-wise for controller markers.

## Building a controller with a custom build system

Build the controller with a custom build system that the automatic `Makefile` path doesn't cover.

### With a command

Pass a sequence of strings to `build=` to run it as-is in the controller's directory. For example, a script:

```python
@pytest.mark.webots_controller("my_bot", "controllers/my_bot", build=("./build.sh",))
```

or a CLI command directly:

```python
@pytest.mark.webots_controller("my_bot", "controllers/my_bot", build=("ninja", "-C", "build"))
```

Command builds use the built-in result caching: the build is skipped while the source hash is unchanged. Use `--webots-rebuild` to force a rebuild.

### With a hook

Pass a string to `build=` to execute more complex builds in a pytest hook:

```python
@pytest.mark.webots_controller("my_bot", "controllers/my_bot", build="cmake")
```

```python
# conftest.py
import subprocess


def pytest_webots_build_controller(spec, config):
    if spec.build == "cmake":
        source = spec.path.parent
        subprocess.run(["cmake", "-S", str(source), "-B", str(source / "build")], check=True)
        subprocess.run(["cmake", "--build", str(source / "build")], check=True)
        return True
```

Hook builds get no caching: the hook runs on every test declaring the controller. Raise when the build fails, as `check=True` does above, to fail the test.

## Extending the agent

The agent is the injected supervisor that allows pytest-webots to manage a Webots world. It can be extended with plugins that allow you to write code that is callable in pytest-land and runs in Webots-land. 

A plugin is a Python file exporting `register(agent)`. It is executed inside Webots, so it can import the standard library and Webots' bundled `controller` package, but not `pytest_webots`.

```python
# plugins/agent_ext.py
def register(agent):
    @agent.op("step_until_settled")
    def step_until_settled(agent, request):
        node = request["node"]  # proxies arrive as live Webots objects
        while max(abs(v) for v in node.getVelocity()) > request["threshold"]:
            if agent.supervisor.step(agent.basic_time_step) == -1:
                return False
        return True

    @agent.op("survey")
    def survey(agent, request):
        node = request["node"]
        x, y, z = node.getPosition()
        linear = node.getVelocity()[:3]
        return {
            "position": {"x": x, "y": y, "z": z},
            "speed": max(abs(v) for v in linear),
            "contacts": node.getNumberOfContactPoints(),
        }
```

Point `webots_agent_plugins` at the file, in `pytest.ini`:

```ini
[pytest]
webots_agent_plugins = plugins/agent_ext.py
```

or in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
webots_agent_plugins = ["plugins/agent_ext.py"]
```

Relative paths resolve against the directory holding the config file.

Call the op from a test through `webots.ops`:

```python
@pytest.mark.webots_world("worlds/arena.wbt")
def test_crate_lands_flat(webots):
    crate = webots.supervisor.getFromDef("CRATE")
    assert webots.ops.step_until_settled(node=crate, threshold=0.01)

    reading = webots.ops.survey(node=crate)
    assert reading["position"]["z"] == pytest.approx(0.5, abs=0.05)
    assert reading["speed"] < 0.01
```

`webots.ops.<name>(**kwargs)` is shorthand for `webots.agent_op("<name>", **kwargs)`. Both take keyword arguments only.

The agent reserves the following op names: `ping`, `step`, `reset`, `reset_physics`, `reload`, `quit`, `time`, `basic_time_step`, `call`, `release`.

## pytest-xdist

pytest-webots is compatible with pytest-xdist. Every world-parameterised test carries an `xdist_group` marker, so `--dist loadgroup` keeps the tests sharing a world on one worker instead of booting a copy per worker.

```sh
pytest -n 4 --dist loadgroup
```

## CI

Run the suite in the official Webots image under a virtual display:

```yaml
integration:
  runs-on: ubuntu-latest
  container:
    image: cyberbotics/webots:R2025a-ubuntu22.04
  steps:
    - uses: actions/checkout@v7
    - run: apt-get update && apt-get install -y xvfb build-essential curl
    - uses: astral-sh/setup-uv@v10
      with:
        python-version: "3.12"
    - run: uv sync --locked
    - run: xvfb-run -a uv run pytest -o webots_startup_timeout=180
```
