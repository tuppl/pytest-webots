"""
Example controller: report simulation time every second of simulated time.
"""

from controller import Robot

robot = Robot()
print("explorer online", flush=True)
next_report = 1.0
while robot.step(32) != -1:
    if robot.getTime() >= next_report:
        print(f"explorer at t={robot.getTime():.1f}s", flush=True)
        next_report += 1.0
