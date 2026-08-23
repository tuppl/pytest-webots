# pytest-webots

[![PyPI](https://img.shields.io/pypi/v/pytest-webots)](https://pypi.org/project/pytest-webots/)
[![Python](https://img.shields.io/pypi/pyversions/pytest-webots)](https://pypi.org/project/pytest-webots/)
[![Tests](https://github.com/tuppl/pytest-webots/actions/workflows/ci.yml/badge.svg)](https://github.com/tuppl/pytest-webots/actions/workflows/ci.yml)

pytest plugin for running [Webots](https://cyberbotics.com) simulations with tests. Declare the worlds and controllers a test needs with markers. The plugin handles process lifecycle, world reuse, fast state reset, controller builds, and crash recovery.

The full guide lives in [docs/GUIDE.md](docs/GUIDE.md); the internals are described in [docs/DESIGN.md](docs/DESIGN.md).

## Requirements

- [Webots R2025a](https://github.com/cyberbotics/webots/releases/tag/R2025a)

## Features

- Declare a world with `@pytest.mark.webots_world`. Stack markers to parameterise the test over several worlds.
- Webots worlds are process-isolated and reset between tests.
- [Supervisor API](https://cyberbotics.com/doc/reference/supervisor) is available through the `webots` fixture.
- Declare extern controllers with `@pytest.mark.webots_controller`. Stack markers to run several robots in one test.
- C/C++ controllers are auto-built and cached.
- Other build systems can be attached with a hook.

## Installation

```sh
pip install pytest-webots
```

## Quick start

```python
import pytest


@pytest.mark.webots_world("worlds/arena.wbt")
@pytest.mark.webots_controller("my_bot", "controllers/my_bot")
def test_drive(webots):
    robot = webots.supervisor.getFromDef("ROBOT")
    robot.getField("translation").setSFVec3f([0, 0, 1])
    webots.step(64)
    assert webots.controllers["my_bot"].alive
```

## Reference

### Options

| option | ini | default | description |
|---|---|---|---|
| `--webots-home DIR` | `webots_home` | auto-discovered | Webots installation directory. Falls back to `WEBOTS_HOME`, then platform defaults such as `/Applications/Webots.app` and `/usr/local/webots`. |
| | `webots_worlds_dir` | none | Directory world names resolve against, relative to root directory. |
| `--webots-mode MODE` | `webots_mode` | `fast` | Simulation mode: `realtime` or `fast`. |
| `--webots-gui` | `webots_headless` | `true` | Run Webots without rendering. `--webots-gui` shows the window instead. |
| `--webots-startup-timeout SECONDS` | `webots_startup_timeout` | `60` | Seconds to wait for a world to boot. |
| `--webots-port-base PORT` | `webots_port_base` | `1234` | Lowest TCP port to try. Ports are assigned upward on demand and not reused. |
| `--webots-no-build` | `webots_build` | `true` | Build controllers before launching them. `--webots-no-build` skips every build. |
| `--webots-rebuild` | | `false` | Force controller rebuilds, ignoring the source-hash cache. |
| `--webots-keep-alive` | | `false` | Leave Webots instances and injected worlds in place after the session, for debugging. |
| `--webots-no-inject` | `webots_inject_supervisor` | `true` | Inject the supervisor robot that serves the Supervisor API. Without it there is no `webots.supervisor`. |
| | `webots_supervisor_name` | `pytest-supervisor` | Name of the injected supervisor robot. |
| | `webots_args` | none | Extra command line arguments for every Webots instance. |
| | `webots_max_restarts` | `3` | Consecutive failed boots of a world before giving up on it. |
| | `webots_make` | `make` | Path to the make executable. On Windows, the Webots-packaged MSYS make. |
| | `webots_agent_plugins` | none | Python files loaded into the supervisor agent inside Webots; each defines `register(agent)`. |

### Markers

| marker | description |
|---|---|
| `@pytest.mark.webots_world(path, *, scope, mode, args, timeout)` | Boot a world for this test. Stack to parameterise test with several worlds. |
| `@pytest.mark.webots_controller(robot, path, *, build, args, env, cwd, autostart, protocol, ip_address)` | Attach an extern controller to a named robot. Stack for several robots. |

Markers also work at module and class level with `pytestmark`.

### Fixtures

| fixture | description |
|---|---|
| `webots` | Per-test handle on the simulation: the Supervisor API, stepping and resetting, and this test's controllers. |

### Hooks

Implement in `conftest.py` like any pytest hook:

| hook | description |
|---|---|
| `pytest_webots_resolve_world(name, config)` | Map a marker name to a world path (firstresult). |
| `pytest_webots_world_args(world, config)` | Extra Webots arguments per world. |
| `pytest_webots_world_started/_stopping(instance)` | World lifecycle. |
| `pytest_webots_world_crashed(instance, error)` | Fires on crash detection, before any restart. |
| `pytest_webots_before_reset/_after_reset(instance)` | Around the between-test reset. |
| `pytest_webots_controllers(item, instance)` | Additional `ControllerSpec`s for a test, with no marker involved. |
| `pytest_webots_build_controller(spec, config)` | Integrate a build system by dispatching on `spec.build` (firstresult). |

## Todo

- Support simulation pause.