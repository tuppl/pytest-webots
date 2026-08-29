"""
Webots process lifecycle: launch, readiness, reset, reload, shutdown.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from . import ports
from .errors import PortAllocationError, WebotsCrashedError, WebotsError, WebotsQuitError, WorldBootTimeout
from .inject import inject_supervisor, remove_injected
from .process import EXIT_GRACE, OutputReader, controller_env, exit_code, terminate
from .supervisor.proxy import AgentClient, AgentConnectionError, SupervisorProxy, decode_result, encode_args

if TYPE_CHECKING:
    from .config import Settings
    from .markers import WorldSpec

_URL_RE = re.compile(r"^(?:ipc|tcp)://(?P<port>\d+)/(?P<robot>.+)$")
_PORT_RANGE_RE = re.compile(r"failed to open TCP server in the port range \[(\d+)-(\d+)\]")
_CONNECTED_RE = re.compile(r"^INFO: '(.+)' extern controller: connected\.")
_DISCONNECTED_RE = re.compile(r"^INFO: '(.+)' extern controller: disconnected")
_AGENT_SCRIPT = Path(__file__).parent / "supervisor" / "agent.py"


class WorldHooks(Protocol):
    """
    Notifications a world raises as it runs.

    The instance passes itself, so an implementation needs no reference back and
    can be handed to the constructor. `_fixtures` implements this over pytest's
    hook relay; `_core` stays free of pytest.
    """

    def world_args(self, world: Path) -> tuple[str, ...]: ...

    def started(self, instance: WebotsInstance) -> None: ...

    def stopping(self, instance: WebotsInstance) -> None: ...

    def crashed(self, instance: WebotsInstance, error: BaseException) -> None: ...

    def before_reset(self, instance: WebotsInstance) -> None: ...

    def after_reset(self, instance: WebotsInstance) -> None: ...


class NoHooks:
    """
    The default: a world constructed without an observer still runs.
    """

    def world_args(self, world: Path) -> tuple[str, ...]:
        return ()

    def started(self, instance: WebotsInstance) -> None: ...

    def stopping(self, instance: WebotsInstance) -> None: ...

    def crashed(self, instance: WebotsInstance, error: BaseException) -> None: ...

    def before_reset(self, instance: WebotsInstance) -> None: ...

    def after_reset(self, instance: WebotsInstance) -> None: ...


class WebotsInstance:
    """
    A running (or restartable) Webots process for one world.
    """

    def __init__(
        self,
        spec: WorldSpec,
        settings: Settings,
        port: int,
        *,
        hooks: WorldHooks | None = None,
        allocate_port: Callable[[], int] | None = None,
    ) -> None:
        self.spec = spec
        self.settings = settings
        self.port = port
        self._hooks = hooks or NoHooks()
        self._allocate_port = allocate_port
        self.robots: dict[str, str] = {}
        self.connected: set[str] = set()
        self._connect_gen: dict[str, int] = {}  # monotonic per robot, never reset
        self._proc: subprocess.Popen[str] | None = None
        self._reader = OutputReader(f"webots-out-{port}", on_line=self._parse_line)
        self._lock = threading.Lock()
        self._injected: Path | None = None
        self._agent_proc: subprocess.Popen[str] | None = None
        self._agent_reader = OutputReader(f"agent-out-{port}", maxlen=200)
        self._client: AgentClient | None = None
        self._agent_address: str | None = None
        self._boot_failures = 0
        self._boot_error: BaseException | None = None
        self._announced_port: int | None = None

    def __repr__(self) -> str:
        return f"<WebotsInstance {self.spec} port={self.port} {'running' if self.alive else 'stopped'}>"

    @property
    def world_path(self) -> str:
        return str(self.spec.path)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int:
        if self._proc is None:
            raise WebotsError("Webots is not running")
        return self._proc.pid

    @property
    def supervisor(self) -> SupervisorProxy:
        return SupervisorProxy(self.proxy_call)

    def command(self) -> list[str]:
        cmd = [
            str(self.settings.webots_binary),
            "--batch",
            "--stdout",
            "--stderr",
            f"--mode={self._boot_mode()}",
            f"--port={self.port}",
            "--extern-urls",
        ]
        if self.settings.headless:
            cmd += ["--no-rendering", "--minimize"]
        cmd += self.settings.extra_args
        cmd += self.spec.args
        cmd += self._hooks.world_args(self.spec.path)
        cmd.append(str(self._injected if self._injected is not None else self.spec.path))
        return cmd

    def _boot_mode(self) -> str:
        mode = self.spec.mode or self.settings.mode
        return "fast" if mode == "pause" else mode

    def boot(self) -> None:
        if self.alive:
            return
        if self._boot_failures >= self.settings.max_restarts:
            raise WebotsError(
                f"world {self.world_path} failed to boot {self._boot_failures} consecutive times; giving up"
            ) from self._boot_error
        if not ports.available(self.port):
            self._reallocate_port()  # something took the port while this instance was down
        try:
            self._boot()
        except BaseException as error:
            self._boot_failures += 1
            self._boot_error = error
            self.shutdown(force=True)
            if isinstance(error, PortAllocationError):
                self._reallocate_port()
            raise
        self._boot_failures = 0
        self._boot_error = None
        self._hooks.started(self)

    def _reallocate_port(self) -> None:
        if self._allocate_port is None:
            return
        with suppress(Exception):
            self.port = self._allocate_port()

    def _boot(self) -> None:
        with self._lock:
            self._announced_port = None
        self.robots.clear()
        self.connected.clear()
        if self.settings.inject_supervisor:
            token = f"{self.port}-{os.getpid()}"  # pid too: two runs on one port must not share the file
            self._injected = inject_supervisor(self.spec.path, self.settings.supervisor_name, token)
        self._proc = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=sys.platform != "win32",
        )
        self._reader.start(self._proc)
        self._wait_ready()
        if self.settings.inject_supervisor:
            self._start_agent()
        if (self.spec.mode or self.settings.mode) == "pause":
            if not self.settings.inject_supervisor:
                raise WebotsError(
                    "mode='pause' needs the injected supervisor to freeze the world at boot; "
                    "remove --webots-no-inject or drop mode='pause'"
                )
            self.set_mode("pause")

    def ensure_running(self) -> None:
        if not self.alive:
            self.boot()

    def step(self, ms: int | None = None) -> int:
        return int(self._request({"op": "step", "ms": ms}))

    def reset(self) -> None:
        self._hooks.before_reset(self)
        self._request({"op": "reset"})
        self._hooks.after_reset(self)

    def reload(self) -> None:
        """
        Reload the world from disk: heavier than reset, restarts the agent too.
        """
        with self._lock:
            self.robots.clear()  # before the reload so re-announced URLs aren't wiped
            self.connected.clear()
        self._request({"op": "reload"}, expect_disconnect=True)
        self._teardown_agent()
        self._wait_ready()
        self._start_agent()
        if (self.spec.mode or self.settings.mode) == "pause":
            self.set_mode("pause")

    def advance_to(self, target: float, mode: str) -> float:
        return float(self._request({"op": "advance_to", "target": target, "mode": mode}))

    def set_mode(self, mode: str) -> None:
        self._request({"op": "set_mode", "mode": mode})

    def sim_time(self) -> float:
        return float(self._request({"op": "time"}))

    def agent_op(self, op: str, params: dict[str, Any]) -> Any:
        """
        Invoke an op on the supervisor agent, including plugin-registered ones.
        """
        payload: dict[str, Any] = {"op": op}
        for key, value in params.items():
            payload[key] = encode_args([value])[0]
        return decode_result(self._request(payload), self.proxy_call)

    def _wait_ready(self) -> None:
        """
        Ready when the injected supervisor announces its URL; it sits last in
        the world file, so by then every robot has been discovered. Without
        injection, the first URL is the signal.
        """
        timeout = self.spec.timeout if self.spec.timeout is not None else self.settings.startup_timeout
        deadline = time.monotonic() + timeout
        wanted = self.settings.supervisor_name if self.settings.inject_supervisor else None
        while time.monotonic() < deadline:
            with self._lock:
                ready = wanted in self.robots if wanted is not None else bool(self.robots)
                announced = self._announced_port
            if ready:
                self._adopt_port(announced)
                return
            if not self.alive:
                raise self._boot_failure(f"Webots exited while booting {self.world_path}")
            time.sleep(0.05)
        raise self._boot_failure(f"world {self.world_path} did not become ready within {timeout:.0f}s", timeout=True)

    def _adopt_port(self, announced: int | None) -> None:
        """
        Follow the port Webots actually bound. A busy port makes it retry
        upward and serve on the one it got, announcing that port in the URLs;
        the announcement is the only reliable source, and controllers and the
        agent both read self.port after readiness.
        """
        if announced is None or announced == self.port:
            return
        self._reader.append(
            f"INFO: pytest-webots: Webots is serving port {announced}, not the requested {self.port}; following it."
        )
        self.port = announced

    def _boot_failure(self, summary: str, timeout: bool = False) -> WebotsError:
        """
        Compose a boot failure, draining the reader first when the process is
        gone so the line explaining the exit makes it into the message.
        """
        if not self.alive:
            self._reader.join(timeout=1.0)
        output = self.output()
        if match := _PORT_RANGE_RE.search(output):
            return PortAllocationError(
                f"Webots found no free port in [{match.group(1)}, {match.group(2)}] for {self.world_path}:\n{output}"
            )
        if timeout:
            return WorldBootTimeout(f"{summary}:\n{output}")
        return WebotsError(f"{summary}:\n{output}")

    def connection_generation(self, name: str) -> int:
        """
        Count of connect events seen for this robot; snapshot it before a
        launch so readiness can demand a connect that is provably new.
        """
        with self._lock:
            return self._connect_gen.get(name, 0)

    def _parse_line(self, line: str) -> bool:
        """
        Track discovery and connection state from Webots' one stdout stream.

        Returns False for URL announcements, which are data rather than log.
        """
        if match := _URL_RE.match(line):
            with self._lock:
                if self._announced_port is None:
                    self._announced_port = int(match["port"])
                self.robots[match["robot"]] = line
            return False
        if match := _CONNECTED_RE.match(line):
            with self._lock:
                name = match.group(1)
                self.connected.add(name)
                self._connect_gen[name] = self._connect_gen.get(name, 0) + 1
        elif match := _DISCONNECTED_RE.match(line):
            with self._lock:
                self.connected.discard(match.group(1))
        return True

    def _start_agent(self) -> None:
        if sys.platform == "win32":
            self._agent_address = f"tcp:{ports.ephemeral()}"
        else:
            self._agent_address = str(Path(tempfile.gettempdir()) / f"pytest-webots-{self.port}-{os.getpid()}.sock")
            Path(self._agent_address).unlink(missing_ok=True)
        home = self.settings.home
        assert home is not None
        env = controller_env(home, f"ipc://{self.port}/{self.settings.supervisor_name}")
        self._agent_proc = subprocess.Popen(
            [sys.executable, str(_AGENT_SCRIPT), self._agent_address] + [str(p) for p in self.settings.agent_plugins],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._agent_reader.start(self._agent_proc)

        def abort() -> str | None:
            proc = self._agent_proc
            if proc is not None and proc.poll() is not None:
                return f"supervisor agent exited with code {proc.returncode}:\n{self.agent_output()}"
            return None

        self._client = AgentClient(self._agent_address)
        try:
            self._client.connect(timeout=self.settings.startup_timeout, abort=abort)
        except AgentConnectionError as error:
            raise WebotsError(
                f"supervisor agent failed to start for {self.world_path}: {error}\n{self.output()}"
            ) from error

    def _request(self, payload: dict[str, Any], expect_disconnect: bool = False) -> Any:
        if self._client is None:
            raise WebotsError("no supervisor agent is connected (is injection disabled via --webots-no-inject?)")
        try:
            return self._client.request(payload, expect_disconnect=expect_disconnect)
        except AgentConnectionError as error:
            self._handle_failure(error)
            raise AssertionError("unreachable") from error

    def proxy_call(self, target: int | None, method: str, args: list[Any]) -> Any:
        result = self._request({"op": "call", "target": target, "method": method, "args": encode_args(args)})
        return decode_result(result, self.proxy_call)

    def _handle_failure(self, error: BaseException) -> None:
        returncode = exit_code(self._proc, EXIT_GRACE)
        self._reader.join(timeout=1.0)  # let the lines explaining the exit land
        clean = returncode == 0
        failure: WebotsError
        if clean:
            failure = WebotsQuitError(
                f"the simulation running {self.world_path} was quit "
                f"(a controller called simulationQuit, or the window was closed)"
            )
        elif returncode is None:
            failure = WebotsCrashedError(f"Webots stopped responding while running {self.world_path}:\n{self.output()}")
        else:
            # simulationQuit(N) and a genuine crash both surface as N, and
            # nothing in the output separates them; name both possibilities.
            failure = WebotsCrashedError(
                f"Webots exited with code {returncode} while running {self.world_path} "
                f"(crashed, or a controller called simulationQuit({returncode})):\n{self.output()}"
            )
        failure.__cause__ = error
        if not clean:
            self._hooks.crashed(self, failure)
        self.shutdown(force=True)
        raise failure

    def kill(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            proc.kill()
        else:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, 9)

    def shutdown(self, force: bool = False) -> None:
        if self.settings.keep_alive and not force:
            return
        self._teardown_agent()
        if self._proc is not None:
            if self._proc.poll() is None:
                self._hooks.stopping(self)
            terminate(self._proc, kill=self.kill)  # group kill: the Linux binary is a wrapper
            self._reader.join()
            self._proc = None
        remove_injected(self._injected)
        self._injected = None

    def agent_output(self) -> str:
        return self._agent_reader.text()

    def _teardown_agent(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._agent_proc is not None:
            terminate(self._agent_proc)
            self._agent_proc = None
        self._agent_reader.join()
        if self._agent_address is not None and not self._agent_address.startswith("tcp:"):
            Path(self._agent_address).unlink(missing_ok=True)
        self._agent_address = None

    def output(self) -> str:
        return self._reader.text()
