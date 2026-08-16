"""
Minimal test controller: announce readiness, echo argv, then idle.
"""

import sys

from controller import Robot

robot = Robot()
print("probe controller ready", flush=True)
if len(sys.argv) > 1:
    print(f"probe args: {' '.join(sys.argv[1:])}", flush=True)
while robot.step(32) != -1:
    pass
