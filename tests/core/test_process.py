import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pytest_webots._core.process import (
    EXIT_GRACE,
    OutputReader,
    controller_env,
    exit_code,
    terminate,
)


def spawn(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def test_terminate_stops_a_running_process() -> None:
    proc = spawn("import time; time.sleep(60)")
    terminate(proc)
    assert proc.poll() is not None


def test_terminate_is_a_noop_on_a_finished_process() -> None:
    proc = spawn("pass")
    proc.wait()
    terminate(proc)  # must not raise or hang
    assert proc.poll() == 0


def test_terminate_accepts_none() -> None:
    terminate(None)


def test_terminate_kills_a_process_that_ignores_sigterm() -> None:
    proc = spawn("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)")
    time.sleep(0.3)  # let the handler install before the signal lands
    start = time.monotonic()
    terminate(proc, grace=0.5)
    assert proc.poll() is not None
    assert time.monotonic() - start < 10


def test_terminate_uses_the_kill_override() -> None:
    proc = spawn("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)")
    time.sleep(0.3)
    called: list[bool] = []

    def hard_kill() -> None:
        called.append(True)
        proc.kill()

    terminate(proc, kill=hard_kill, grace=0.5)
    assert called == [True]  # Webots needs a group kill, not proc.kill()


def test_exit_code_reports_a_finished_process() -> None:
    proc = spawn("raise SystemExit(3)")
    assert exit_code(proc, EXIT_GRACE) == 3


def test_exit_code_is_none_while_still_running() -> None:
    proc = spawn("import time; time.sleep(60)")
    try:
        assert exit_code(proc, 0.2) is None  # the grace expires, so it reads as hung
    finally:
        terminate(proc)


def test_exit_code_waits_out_a_process_on_its_way_down() -> None:
    # The point of the grace: a process mid-exit must not read as still running.
    proc = spawn("import time; time.sleep(0.4)")
    assert exit_code(proc, EXIT_GRACE) == 0


def test_exit_code_accepts_none() -> None:
    assert exit_code(None, EXIT_GRACE) is None


def test_reader_captures_output() -> None:
    reader = OutputReader("test")
    proc = spawn("print('first'); print('second')")
    reader.start(proc)
    proc.wait()
    reader.join()
    assert reader.text() == "first\nsecond"


def test_reader_drops_blank_lines_and_keeps_a_bounded_buffer() -> None:
    reader = OutputReader("test", maxlen=3)
    proc = spawn("[print(i) for i in range(10)]\nprint('')")
    proc.wait()
    reader.start(proc)
    reader.join()
    assert reader.text() == "7\n8\n9"


def test_on_line_sees_every_line_and_can_consume_it() -> None:
    seen: list[str] = []

    def parse(line: str) -> bool:
        seen.append(line)
        return not line.startswith("DATA")  # consumed lines stay out of the log

    reader = OutputReader("test", on_line=parse)
    proc = spawn("print('DATA x'); print('log line')")
    proc.wait()
    reader.start(proc)
    reader.join()
    assert seen == ["DATA x", "log line"]
    assert reader.text() == "log line"


def test_append_mixes_plugin_lines_into_the_log() -> None:
    reader = OutputReader("test")
    reader.append("pytest-webots: following the announced port")
    assert "following the announced port" in reader.text()


def test_join_is_safe_before_any_start() -> None:
    OutputReader("test").join()


def test_controller_env_puts_the_bundled_package_first(tmp_path: Path) -> None:
    env = controller_env(tmp_path / "webots", "ipc://1234/probe")
    assert env["WEBOTS_HOME"] == str(tmp_path / "webots")
    assert env["WEBOTS_CONTROLLER_URL"] == "ipc://1234/probe"
    assert env["PYTHONPATH"].split(os.pathsep)[0].endswith("controller/python")


def test_controller_env_preserves_an_existing_pythonpath(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/already/here")
    env = controller_env(tmp_path / "webots", "ipc://1234/probe")
    assert env["PYTHONPATH"].endswith(f"{os.pathsep}/already/here")


def test_controller_env_applies_extras(tmp_path: Path) -> None:
    env = controller_env(tmp_path / "webots", "ipc://1234/probe", {"ROLE": "striker"})
    assert env["ROLE"] == "striker"
