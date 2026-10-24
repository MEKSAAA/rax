"""The benchmark the shared harness tests run against: the registry's default, with one of its
tasks, and its robot tool names, so the same tests hold for every backend."""
from services.benchmarks import benchmark

BENCH = benchmark()
TASK = BENCH.task_names()[0]
STEP, OBSERVE, STATUS = BENCH.robot['step'], BENCH.observe_tool, BENCH.status_tool
PIXEL, CAMERA = BENCH.robot['pixel'], BENCH.robot['overview_camera']
# A second task of the same benchmark.
OTHER_TASK = BENCH.task_names()[1]
