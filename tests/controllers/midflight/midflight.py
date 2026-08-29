"""
Test controller that finishes partway through a long agent request, so the
departure lands after the request was dispatched rather than before it.
"""

import time

from controller import Robot

robot = Robot()
print("midflight controller ready", flush=True)
time.sleep(1.0)  # outlast the connection poll so the launch is confirmed
for _ in range(60):
    robot.step(32)
print("midflight: work done", flush=True)
