"""
Test controller that holds the simulation clock still on request.

Steps continuously until the file named by argv[1] appears, then stops stepping
for a moment. A synchronous robot that is not stepping gates the whole world,
so nothing can advance for as long as the hold lasts.
"""

import sys
import time
from pathlib import Path

from controller import Robot

HOLD_SECONDS = 2.0

hold = Path(sys.argv[1])
robot = Robot()
print("pacer controller ready", flush=True)
while robot.step(32) != -1:
    if hold.exists():
        print("pacer holding", flush=True)
        time.sleep(HOLD_SECONDS)
        hold.unlink(missing_ok=True)
        print("pacer resumed", flush=True)
