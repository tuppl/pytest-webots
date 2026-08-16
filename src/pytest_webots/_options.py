from __future__ import annotations

import pytest


@pytest.hookimpl
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("webots", "Webots simulation")
    group.addoption("--webots-home", metavar="DIR", help="Webots installation directory (overrides WEBOTS_HOME).")
    group.addoption("--webots-gui", action="store_true", help="Run Webots with its GUI instead of headless.")
    group.addoption(
        "--webots-mode",
        choices=("pause", "realtime", "fast"),
        help="Simulation mode (overrides the webots_mode ini value).",
    )
    group.addoption(
        "--webots-keep-alive",
        action="store_true",
        help="Leave Webots instances and injected worlds in place after the session, for debugging.",
    )
    group.addoption("--webots-no-build", action="store_true", help="Skip controller builds.")
    group.addoption("--webots-rebuild", action="store_true", help="Force controller rebuilds, ignoring the cache.")
    group.addoption("--webots-no-inject", action="store_true", help="Do not inject the supervisor robot into worlds.")
    group.addoption(
        "--webots-port-base",
        type=int,
        metavar="PORT",
        help="Base TCP port for Webots instances (overrides the webots_port_base ini value).",
    )
    group.addoption(
        "--webots-startup-timeout",
        type=float,
        metavar="SECONDS",
        help="Seconds to wait for a world to boot (overrides the webots_startup_timeout ini value).",
    )

    parser.addini("webots_home", help="Webots installation directory.")
    parser.addini("webots_worlds_dir", help="Directory world names resolve against, relative to rootdir.")
    parser.addini("webots_mode", default="fast", help="Simulation mode: pause, realtime, or fast.")
    parser.addini("webots_headless", type="bool", default=True, help="Run Webots without rendering.")
    parser.addini("webots_args", type="args", help="Extra command line arguments for Webots.")
    parser.addini("webots_startup_timeout", default="60", help="Seconds to wait for a world to boot.")
    parser.addini("webots_max_restarts", default="3", help="Consecutive failed boots of a world before giving up.")
    parser.addini("webots_port_base", default="1234", help="Base TCP port for Webots instances.")
    parser.addini("webots_supervisor_name", default="pytest-supervisor", help="Name of the injected supervisor robot.")
    parser.addini("webots_inject_supervisor", type="bool", default=True, help="Inject a supervisor robot into worlds.")
    parser.addini("webots_build", type="bool", default=True, help="Build controllers before launching them.")
