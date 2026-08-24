"""
Test controller that finishes its work and returns from main, as a controller
whose task has ended would.
"""

import time

from controller import Robot

robot = Robot()
print("finisher controller ready", flush=True)
time.sleep(1.0)  # outlast the connection poll so the launch is confirmed
for _ in range(5):
    robot.step(32)
print("finisher: work done", flush=True)
