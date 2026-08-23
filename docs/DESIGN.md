# Design

How pytest-webots is put together: the two-layer package split, where each pytest hook fires, and what runs in which process.

## Two layers

Modules sibling to `plugin.py` are named for the pytest pipeline stage they serve and hold **only** hookimpls and fixtures that delegate. Domain logic lives in `_core/`, which defines no hookimpls and no fixtures.

```mermaid
flowchart TB
    subgraph L1["pipeline layer — hookimpls and fixtures only"]
        direction LR
        plugin["plugin.py<br/><i>pytest11 entry point</i>"]
        opts["_options.py"]
        conf["_configure.py"]
        gen["_generate.py"]
        coll["_collect.py"]
        fix["_fixtures.py"]
        rep["_report.py"]
        sess["_session.py"]
    end

    subgraph L2["_core — domain logic, importable without pytest running"]
        direction LR
        markers["markers.py"]
        registry["registry.py"]
        world["world.py"]
        session["session.py"]
        build["build.py"]
        controller["controller.py"]
    end

    subgraph L3["inside Webots — standalone scripts"]
        direction LR
        agent["supervisor/agent.py"]
        stub["supervisor/stub.py"]
    end

    plugin -->|"pytest_plugins manifest"| L1
    L1 --> L2
    world -.->|"launches"| agent
    session -.->|"launches"| stub

    style L1 fill:none
    style L2 fill:none
    style L3 fill:none
```

Pipeline modules import `_core` only, with one exception: `_report` imports the `SESSION_KEY` stash key from `_fixtures`, because that is where the session is stashed onto the item.

Where domain code must fire this plugin's own hooks — `world_started`, `world_stopping`, `world_crashed`, `before/after_reset` and `world_args` all originate inside `WebotsInstance` — `_core` declares a `WorldHooks` **Protocol** and takes an implementation at construction. `_configure` supplies the one that relays to `config.hook`; the `NoHooks` default means an instance built without an observer still runs, so no call site needs a null guard. That keeps `_core` free of any pytest coupling beyond types, and a world is fully wired the moment it exists rather than after two other modules finish assigning to it.

The notification passes the instance rather than closing over it, which is what lets the implementation be constructed before any world is.

`supervisor/agent.py` and `supervisor/stub.py` run **inside Webots** as extern controllers. They import only the standard library and the Webots-bundled `controller` package, never `pytest_webots` itself.

### `_core` dependency direction

```mermaid
flowchart TD
    errors["errors.py"]
    config["config.py"]
    ports["ports.py"]
    markers["markers.py"]
    inject["inject.py"]
    proxy["supervisor/proxy.py"]
    world["world.py"]
    registry["registry.py"]
    controller["controller.py"]
    build["build.py"]
    session["session.py"]

    ports --> errors
    inject --> errors
    proxy --> errors
    controller --> errors
    controller --> config
    build --> errors
    build --> config
    build --> markers
    world --> ports
    world --> config
    world --> errors
    world --> inject
    world --> proxy
    registry --> ports
    registry --> world
    session --> controller
    session --> errors
    session --> markers
```

`errors.py` and `config.py` are the base and do not depend on each other — `config` raises `pytest.UsageError` for user-facing misconfiguration, while `errors` carries runtime failures. `markers.py` has no runtime dependency on the others; it imports `config` only under `TYPE_CHECKING`.

## Pipeline stages

```mermaid
flowchart TD
    S1["<b>1 · plugin load</b><br/>entry point reads the pytest_plugins manifest<br/><i>plugin.py</i>"]
    S2["<b>2 · option declaration</b><br/>pytest_addoption — every CLI flag and ini key<br/><i>_options.py</i>"]
    S3["<b>3 · configure</b><br/>hookspecs installed · Settings built from ini/CLI<br/>· WorldRegistry stashed · markers registered<br/><i>plugin.py · _configure.py</i>"]
    S4["<b>4 · collection</b><br/>pytest_generate_tests <i>(tryfirst)</i><br/>stacked webots_world markers → WorldSpec →<br/>parametrize(indirect, scope=widest)<br/><i>_generate.py</i>"]
    S5["<b>5 · post-collection</b><br/>pytest_collection_modifyitems <i>(tryfirst)</i><br/>xdist_group marker per world<br/><i>_collect.py</i>"]
    S6["<b>6 · per-test setup</b><br/>boot or revive · resolve · build · launch<br/><i>_fixtures.py</i>"]
    S7["<b>7 · per-test teardown</b><br/>re-crew · reset · terminate<br/><i>_fixtures.py</i>"]
    S8["<b>8 · report</b><br/>pytest_runtest_makereport <i>(wrapper)</i><br/>Webots and controller logs onto failed reports<br/><i>_report.py</i>"]
    S9["<b>9 · session end</b><br/>pytest_sessionfinish — sweep anything still running<br/><i>_session.py</i>"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7 --> S8 --> S9
    S8 -.->|"next test"| S6

    style S6 stroke-width:3px
    style S7 stroke-width:3px
```

`pytest_generate_tests` is `tryfirst` so the world parameter is applied before the builtin `parametrize` marks and therefore leads test ids. `pytest_collection_modifyitems` is `tryfirst` so xdist's later-registered worker implementation sees the markers. Grouping same-world tests is then pytest's own `reorder_items`, not ours.

### Stage 6 — setup

```mermaid
flowchart TD
    A(["webots fixture setup"]) --> B["_webots_world:<br/>registry.get_or_create(spec)"]
    B --> C{"process alive?"}
    C -->|no| D["boot: inject supervisor into a<br/>sibling world copy · spawn Webots<br/>· parse --extern-urls · start agent"]
    C -->|yes| E
    D --> E["collect_controller_specs<br/><i>fixture_ref resolved here via<br/>request.getfixturevalue</i>"]
    E --> F["pytest_webots_controllers hook<br/><i>appends derived specs</i>"]
    F --> G["build <b>every</b> spec<br/><i>declaration = requirement</i>"]
    G --> H["launch autostart specs in order,<br/>each confirmed connected<br/>before the next starts"]
    H --> I(["test body"])

    G -.->|"build fails"| X["error the test"]
    H -.->|"launch fails"| X

    X -.-> Y(["stage 7 · leave the world clean"])

    style X stroke-dasharray: 4 4
    style Y stroke-dasharray: 4 4
```

Builds run before any launch so a failure surfaces while nothing is connected. Build results are cached against a source digest — **including failures**, so a broken controller fails every test declaring it while only the first pays for the build.

Neither failure has its own recovery: both fall into the same teardown as a test that finished normally, which is what lets a failed setup keep the world rather than spend a reboot on it.

### Stage 7 — teardown

One path, entered from a finished test body and from a failed setup alike. The ordering is load-bearing and non-obvious.

```mermaid
flowchart TD
    A(["test body done"]) --> B
    A2(["setup failed"]) --> B{"scope is function<br/>or world already dead?"}
    B -->|yes| T
    B -->|no| C["recrew_departed:<br/>attach a stub to every robot whose<br/>controller launched and has since exited"]
    C --> D{"world still alive?"}
    D -->|no| T
    D -->|yes| E["reset:<br/>simulationReset → step to land it<br/>→ simulationResetPhysics"]
    E --> T["<b>finally:</b> terminate all controllers,<br/>stubs included"]
    T --> F(["next test reuses this world"])
```

Three constraints shape it:

- **The reset happens while controllers are connected.** Webots blocks stepping on a `synchronization TRUE` robot with nobody attached, and the reset's landing step is a step like any other. Terminating first would hang it.
- **A departed controller is re-crewed, not tolerated.** Webots keeps a departed robot's slot open and waits for a new connection, so a controller that legitimately finishes its work — a game reaching game over — would otherwise stall the reset until the agent socket times out. A stub reconnects in about 60 ms; without it the teardown costs 30 seconds and a misdiagnosed crash. Stubs are used rather than restarting the real controller so no user code re-runs.
- **A failed setup takes this path too, rather than a recovery of its own.** A launch that fails leaves controllers terminated but still on the session, which is exactly the shape re-crewing handles, so the world is reset and kept instead of shut down. A build that fails launched nothing, so re-crewing finds nothing and the reset is a formality.

Termination sits in a `finally` so a failed reset cannot strand the test's controllers.

## Process model

```mermaid
flowchart LR
    subgraph P1["pytest process"]
        direction TB
        fixture["webots fixture"]
        wsession["WebotsSession"]
        winstance["WebotsInstance"]
        sproxy["SupervisorProxy"]
        fixture --> wsession --> winstance
        wsession --> sproxy
    end

    subgraph P2["Webots process"]
        sim["simulation"]
    end

    subgraph P3["extern controller processes"]
        direction TB
        agent["agent.py<br/><i>injected supervisor</i>"]
        ctrl["the test's controllers"]
        stub["stub.py<br/><i>only when one departs</i>"]
    end

    winstance -->|"spawn · stdout reader thread"| sim
    winstance -->|"spawn"| ctrl
    wsession -->|"spawn on teardown"| stub
    winstance -->|"spawn"| agent
    sproxy <-->|"JSON lines over a unix socket<br/>(localhost TCP on Windows)"| agent
    agent <-->|"Supervisor API"| sim
    ctrl <-->|"ipc:// or tcp://"| sim
    stub <-->|"ipc://"| sim
```

One Webots process per distinct `WorldSpec`, booted from a temporary sibling copy of the world with a supervisor robot appended, reused across the tests that share it and reset in between.

**One stdout stream carries everything.** Readiness, robot discovery, and controller connection tracking are all parsed from the stream Webots is launched with — `--extern-urls` plus the extern-controller connect and disconnect notices. Readiness is the injected supervisor announcing its URL; because it sits last in the world file, every other robot has been discovered by then.

**Connection waits cannot be satisfied by stale state.** Each robot has a monotonically increasing connect generation. A launch snapshots the generation first and then waits for a higher one, so a connection from a previous test can never count — and a controller that connects and finishes inside one poll interval still counts as started.

**`WebotsInstance` outlives its process.** Crashes surface as `WebotsCrashedError` for the test that hit them, and the next test gets a reboot, bounded by `webots_max_restarts` consecutive boot failures. Webots runs in its own process group; on Linux the binary is a wrapper script, so group kills are needed to catch its child.

**How a failure ended decides what the caller is told.** A failed agent RPC means the instance is finished, but not why.

```mermaid
flowchart TD
    A["agent RPC fails"] --> B["wait out the exit grace<br/><i>the socket closes before the<br/>process does</i>"]
    B --> C{"exit code?"}
    C -->|"0"| D["<b>WebotsQuitError</b><br/>simulationQuit, or the<br/>window was closed"]
    C -->|"still running"| E["<b>WebotsCrashedError</b><br/>stopped responding"]
    C -->|"anything else"| F["<b>WebotsCrashedError</b><br/>crashed, or simulationQuit(N)"]
    E --> G["pytest_webots_world_crashed"]
    F --> G
    D -.->|"not a crash"| H(["hook stays silent"])

    style D stroke-width:3px
```

The grace period is what separates "quit" from "hung": the agent socket closes before the process finishes exiting, so polling the instant an RPC fails would report every clean quit as merely unresponsive. A non-zero code stays ambiguous — `simulationQuit(3)` and a genuine crash both surface as `3`, and nothing in the output separates them — so the message names both rather than guessing.

### The agent

`supervisor/agent.py` serves the Supervisor API to the test process. Its ops live in a dispatch table: built-ins (`step`, `reset`, `reload`, `call`, `release`, …) plus anything `webots_agent_plugins` files register through `register(agent)`. A plugin may replace an op registered by an earlier plugin, but claiming a built-in name raises.

Requests are decoded and results encoded centrally, so a plugin op exchanges Webots objects with the test as handles and proxies exactly like the built-in `call` op does. `SupervisorProxy` on the pytest side turns those handles back into chainable objects.

## Ports and xdist

Under xdist, workers each collect for themselves and run every stage independently. The worker id offsets the port base each worker scans upward from, and the port alone isolates workers — it is part of the IPC rendezvous path, so overriding `WEBOTS_TMPDIR` would break the rendezvous on Linux rather than help.

Ports are never reissued once handed out, so an instance can shut down and reboot without another world claiming its number. A port two processes race for resolves itself: Webots retries upward, announces the port it settled on, and the instance follows the announcement rather than its own request.

## Test layout

`tests/` mirrors the package split.

`tests/core/` tests the domain layer directly — `pytest`, a stubbed `subprocess.Popen`, or a real Webots instance constructed by hand, but never the plugin's own hooks. There is one module per `_core` module with standalone logic worth isolating: `test_build`, `test_config`, `test_markers`, `test_ports`, `test_registry`, `test_world`, `test_controller`, `test_session`, `test_agent`.

The files above it are named for the pipeline stage they drive through pytest itself, mostly via `pytester`: `test_generate`, `test_collect`, `test_fixtures`, `test_controllers` (the controller half of stage 6), `test_report`. Shared helpers are fixtures in `tests/conftest.py`, so a test module never imports another.

Worlds in `tests/worlds/` cover both scheduling modes deliberately. `sync.wbt` holds a `synchronization TRUE` robot: without one, a departed controller never blocks, and the whole class of teardown stalls goes undetected.
