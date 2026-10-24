from dataclasses import replace

from bench import BENCH, TASK


def test_every_task_has_a_native_horizon_and_an_episode_wall():
    for task in BENCH.task_names():
        timeouts = BENCH.task_timeouts(task)
        assert timeouts['native_step_limit'] > 0 and timeouts['episode_seconds'] > 0


def test_research_timeouts_apply_to_both_phases(tmp_path):
    from local_tests.unit.test_controller_backend import configuration
    from services.controller.config import with_task_timeouts
    config = replace(configuration(tmp_path), task=TASK)
    scaled = with_task_timeouts(config)
    seconds = BENCH.task_timeouts(TASK)['episode_seconds']
    assert scaled.formal.wall_seconds == scaled.development.wall_seconds == seconds
    assert scaled.formal.memory_mb == config.formal.memory_mb
