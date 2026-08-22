"""
Test controller that ends the simulation the way a finished task would.

Steps a few times, then calls simulationQuit with the status given as argv[1]
(default 0). Webots exits, taking every controller and the agent with it.
"""

import sys

from controller import Supervisor

status = int(sys.argv[1]) if len(sys.argv) > 1 else 0
robot = Supervisor()
print("quitter controller ready", flush=True)
for _ in range(3):
    robot.step(32)
print(f"quitter: simulationQuit({status})", flush=True)
robot.simulationQuit(status)
robot.step(32)
