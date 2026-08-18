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
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import ports
from .config import python_controller_path
from .errors import PortAllocationError, WebotsCrashedError, WebotsError, WorldBootTimeout
from .inject import inject_supervisor, remove_injected
from .supervisor.proxy import AgentClient, AgentConnectionError, SupervisorProxy, decode_result, encode_args

if TYPE_CHECKING:
    from .config import Settings
    from .markers import WorldSpec

_URL_RE = re.compile(r"^(?:ipc|tcp)://(?P<port>\d+)/(?P<robot>.+)$")
_PORT_RANGE_RE = re.compile(r"failed to open TCP server in the port range \[(\d+)-(\d+)\]")
_CONNECTED_RE = re.compile(r"^INFO: '(.+)' extern controller: connected\.")
_DISCONNECTED_RE = re.compile(r"^INFO: '(.+)' extern controller: disconnected")
_TERMINATE_GRACE = 5.0
_AGENT_SCRIPT = Path(__file__).parent / "supervisor" / "agent.py"


class WebotsInstance:
    """
    A running (or restartable) Webots process for one world.
    """

    def __init__(self, spec: WorldSpec, settings: Settings, port: int) -> None:
        self.spec = spec
        self.settings = settings
        self.port = port
        self.robots: dict[str, str] = {}
        self.connected: set[str] = set()
        self._connect_gen: dict[str, int] = {}  # monotonic per robot, never reset
        self.hook_args: tuple[str, ...] = ()  # from pytest_webots_world_args, set by the adapter
        self.reallocate_port: Callable[[], int] | None = None  # set by the registry
        self.on_started: Callable[[], None] | None = None
        self.on_stopping: Callable[[], None] | None = None
        self.on_crashed: Callable[[BaseException], None] | None = None
        self.on_before_reset: Callable[[], None] | None = None
        self.on_after_reset: Callable[[], None] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._output: deque[str] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._injected: Path | None = None
        self._agent_proc: subprocess.Popen[str] | None = None
        self._agent_output: deque[str] = deque(maxlen=200)
        self._agent_reader: threading.Thread | None = None
        self._client: AgentClient | None = None
        self._agent_address: str | None = None
        self._boot_failures = 0
        self._boot_error: BaseException | None = None
        self._announced_port: int | None = None

    @property
    def world(self) -> str:
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
        return SupervisorProxy(self._proxy_call)

    def command(self) -> list[str]:
        cmd = [
            str(self.settings.webots_binary),
            "--batch",
            "--stdout",
            "--stderr",
            f"--mode={self.spec.mode or self.settings.mode}",
            f"--port={self.port}",
            "--extern-urls",
        ]
        if self.settings.headless:
            cmd += ["--no-rendering", "--minimize"]
        cmd += self.settings.extra_args
        cmd += self.spec.args
        cmd += self.hook_args
        cmd.append(str(self._injected if self._injected is not None else self.spec.path))
        return cmd

    def boot(self) -> None:
        if self.alive:
            return
        if self._boot_failures >= self.settings.max_restarts:
            raise WebotsError(
                f"world {self.world} failed to boot {self._boot_failures} consecutive times; giving up"
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
        if self.on_started is not None:
            self.on_started()

    def _reallocate_port(self) -> None:
        if self.reallocate_port is None:
            return
        with suppress(Exception):
            self.port = self.reallocate_port()

    def _boot(self) -> None:
        with self._lock:
            self._announced_port = None
        self.robots.clear()
        self.connected.clear()
        self._output.clear()
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
        self._reader = threading.Thread(target=self._read_output, name=f"webots-out-{self.port}", daemon=True)
        self._reader.start()
        self._wait_ready()
        if self.settings.inject_supervisor:
            self._start_agent()

    def ensure_running(self) -> None:
        if not self.alive:
            self.boot()

    def step(self, ms: int | None = None) -> int:
        return int(self._request({"op": "step", "ms": ms}))

    def reset(self) -> None:
        if self.on_before_reset is not None:
            self.on_before_reset()
        self._request({"op": "reset"})
        if self.on_after_reset is not None:
            self.on_after_reset()

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

    def sim_time(self) -> float:
        return float(self._request({"op": "time"}))

    def agent_op(self, op: str, params: dict[str, Any]) -> Any:
        """
        Invoke an op on the supervisor agent, including plugin-registered ones.
        """
        payload: dict[str, Any] = {"op": op}
        for key, value in params.items():
            payload[key] = encode_args([value])[0]
        return decode_result(self._request(payload), self._proxy_call)

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
                raise self._boot_failure(f"Webots exited while booting {self.world}")
            time.sleep(0.05)
        raise self._boot_failure(f"world {self.world} did not become ready within {timeout:.0f}s", timeout=True)

    def _adopt_port(self, announced: int | None) -> None:
        """
        Follow the port Webots actually bound. A busy port makes it retry
        upward and serve on the one it got, announcing that port in the URLs;
        the announcement is the only reliable source, and controllers and the
        agent both read self.port after readiness.
        """
        if announced is None or announced == self.port:
            return
        self._output.append(
            f"INFO: pytest-webots: Webots is serving port {announced}, not the requested {self.port}; following it."
        )
        self.port = announced

    def _boot_failure(self, summary: str, timeout: bool = False) -> WebotsError:
        """
        Compose a boot failure, draining the reader first when the process is
        gone so the line explaining the exit makes it into the message.
        """
        if not self.alive and self._reader is not None:
            self._reader.join(timeout=1.0)
        output = self.output()
        if match := _PORT_RANGE_RE.search(output):
            return PortAllocationError(
                f"Webots found no free port in [{match.group(1)}, {match.group(2)}] for {self.world}:\n{output}"
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

    def connection_active(self, name: str, after: int) -> bool:
        """
        True only for a connection established by a connect event newer than
        ``after`` — a stale pre-relaunch state can never satisfy this.
        """
        with self._lock:
            return self._connect_gen.get(name, 0) > after and name in self.connected

    def _read_output(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if match := _URL_RE.match(line):
                with self._lock:
                    if self._announced_port is None:
                        self._announced_port = int(match["port"])
                    self.robots[match["robot"]] = line
                continue
            if match := _CONNECTED_RE.match(line):
                with self._lock:
                    name = match.group(1)
                    self.connected.add(name)
                    self._connect_gen[name] = self._connect_gen.get(name, 0) + 1
            elif match := _DISCONNECTED_RE.match(line):
                with self._lock:
                    self.connected.discard(match.group(1))
            if line:
                self._output.append(line)

    def _start_agent(self) -> None:
        if sys.platform == "win32":
            self._agent_address = f"tcp:{ports.ephemeral()}"
        else:
            self._agent_address = str(Path(tempfile.gettempdir()) / f"pytest-webots-{self.port}-{os.getpid()}.sock")
            Path(self._agent_address).unlink(missing_ok=True)
        home = self.settings.home
        assert home is not None
        env = os.environ.copy()
        env["WEBOTS_HOME"] = str(home)
        env["WEBOTS_CONTROLLER_URL"] = f"ipc://{self.port}/{self.settings.supervisor_name}"
        bundled = str(python_controller_path(home))
        env["PYTHONPATH"] = bundled + os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else bundled
        self._agent_output.clear()
        self._agent_proc = subprocess.Popen(
            [sys.executable, str(_AGENT_SCRIPT), self._agent_address] + [str(p) for p in self.settings.agent_plugins],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._agent_reader = threading.Thread(
            target=self._read_agent_output, name=f"agent-out-{self.port}", daemon=True
        )
        self._agent_reader.start()

        def abort() -> str | None:
            proc = self._agent_proc
            if proc is not None and proc.poll() is not None:
                return f"supervisor agent exited with code {proc.returncode}:\n{self.agent_output()}"
            return None

        self._client = AgentClient(self._agent_address)
        try:
            self._client.connect(timeout=self.settings.startup_timeout, abort=abort)
        except AgentConnectionError as error:
            raise WebotsError(f"supervisor agent failed to start for {self.world}: {error}\n{self.output()}") from error

    def _request(self, payload: dict[str, Any], expect_disconnect: bool = False) -> Any:
        if self._client is None:
            raise WebotsError("no supervisor agent is connected (is injection disabled via --webots-no-inject?)")
        try:
            return self._client.request(payload, expect_disconnect=expect_disconnect)
        except AgentConnectionError as error:
            self._handle_failure(error)
            raise AssertionError("unreachable") from error

    def _proxy_call(self, target: int | None, method: str, args: list[Any]) -> Any:
        result = self._request({"op": "call", "target": target, "method": method, "args": encode_args(args)})
        return decode_result(result, self._proxy_call)

    def _handle_failure(self, error: BaseException) -> None:
        """
        An agent RPC failed: Webots died, or it hung. Either way this instance
        is done; reap everything and surface a crash.
        """
        crash = WebotsCrashedError(f"Webots crashed or hung while running {self.world}:\n{self.output()}")
        crash.__cause__ = error
        if self.on_crashed is not None:
            self.on_crashed(crash)
        self.shutdown(force=True)
        raise crash

    def kill(self) -> None:
        """
        Hard-kill the whole Webots process group, wrapper children included.
        """
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass

    def shutdown(self, force: bool = False) -> None:
        """
        Terminate everything; a no-op when already dead or under --webots-keep-alive.
        """
        if self.settings.keep_alive and not force:
            return
        self._teardown_agent()
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                if self.on_stopping is not None:
                    self.on_stopping()
                proc.terminate()
                try:
                    proc.wait(_TERMINATE_GRACE)
                except subprocess.TimeoutExpired:
                    self.kill()
                    proc.wait()
            if self._reader is not None:
                self._reader.join(timeout=2)
                self._reader = None
            self._proc = None
        remove_injected(self._injected)
        self._injected = None

    def _read_agent_output(self) -> None:
        proc = self._agent_proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            self._agent_output.append(line.rstrip("\n"))

    def agent_output(self) -> str:
        return "\n".join(self._agent_output)

    def _teardown_agent(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._agent_proc is not None:
            self._agent_proc.terminate()
            try:
                self._agent_proc.wait(_TERMINATE_GRACE)
            except subprocess.TimeoutExpired:
                self._agent_proc.kill()
                self._agent_proc.wait()
            self._agent_proc = None
        if self._agent_reader is not None:
            self._agent_reader.join(timeout=2)
            self._agent_reader = None
        if self._agent_address is not None and not self._agent_address.startswith("tcp:"):
            Path(self._agent_address).unlink(missing_ok=True)
        self._agent_address = None

    def output(self) -> str:
        return "\n".join(self._output)
