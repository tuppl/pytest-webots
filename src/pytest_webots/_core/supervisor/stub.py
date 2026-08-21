"""
Keep-alive extern controller attached to a robot whose real controller has
exited, so a synchronous robot cannot block the teardown reset. Runs as a
plain Python extern controller; never imported by pytest_webots.
"""

from controller import Robot  # type: ignore[import-not-found]  # via PYTHONPATH set by the launcher

robot = Robot()
while robot.step(int(robot.getBasicTimeStep())) != -1:
    pass
