"""
Test controller that dies badly: the seat it leaves must stay empty.
"""

import sys
import time

from controller import Robot

robot = Robot()
print("crasher controller ready", flush=True)
time.sleep(1.0)
for _ in range(5):
    robot.step(32)
print("crasher: exploding", flush=True)
sys.exit(3)
