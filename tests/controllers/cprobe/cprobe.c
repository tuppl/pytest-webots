#include <stdio.h>
#include <webots/robot.h>

int main() {
  wb_robot_init();
  printf("cprobe controller ready\n");
  fflush(stdout);
  while (wb_robot_step(32) != -1) {
  }
  wb_robot_cleanup();
  return 0;
}
