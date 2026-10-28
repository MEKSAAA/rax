"""Import-light task catalog and disjoint exploration/formal layout protocol."""
import json
import os
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name('catalog.json').read_text())
CAMERAS = ('ego', 'wrist_right', 'wrist_left')
IMAGE_SIZE = 848
IMAGE_WIDTH, IMAGE_HEIGHT = 848, 480
CONTROL_HZ = 1 / 0.0333
ACTION_DIM = STATE_DIM = 14
FRAME_STRIDE = 5
EXPLORATION_COLLECTIONS = {0: ('random', 10000)}
COLLECTION_SUFFIX = {1: 'official'}
COLLECTION_NAMES = {0: 'random exploration', 1: 'official evaluation JSON'}


def source_root():
    return Path(os.environ.get('ROBOWITS_SOURCE', Path(__file__).resolve().parents[2]/'ROBOWITS')).resolve()


def runtime_root():
    return Path(__file__).resolve().parents[2]/'runtime'


def data_root():
    return source_root()/'dataset/robowits'


def task_names():
    return sorted(CATALOG)


def task_info(task):
    if task not in CATALOG:
        raise ValueError(f'Unknown RoboWits task: {task}')
    return dict(CATALOG[task], task=task, official_name=CATALOG[task]['registered_name'], native_step_limit=1000)


def instruction(task, collection=0):
    return task_info(task)['instruction']


def native_step_limit(task):
    return task_info(task)['native_step_limit']


def eval_path(task):
    i = task_info(task)
    return data_root()/('eval_dataset_mutation' if i['mutation'] else 'eval_dataset_seed')/(i['variant']+'.json')


def layout(task, collection, layout_id):
    i = task_info(task)
    if type(layout_id) is not int or layout_id < 0:
        raise ValueError('layout id must be a nonnegative integer')
    if collection == 0:
        if layout_id >= 1000:
            raise ValueError('exploration layout out of range')
        return {'kind': 'random_reset', 'seed': 10000 + layout_id}
    if collection == 1 and layout_id < i['formal_layouts']:
        return {'kind': 'official_json', 'seed': 42, 'init_index': layout_id}
    raise ValueError('Invalid collection or official layout index')


def validate_layouts(task, collection, layout_ids):
    if collection == 1 and not eval_path(task).is_file():
        raise FileNotFoundError(eval_path(task))
    for index in layout_ids:
        layout(task, collection, index)


def task_timeouts(task):
    return {'native_step_limit': native_step_limit(task), 'episode_seconds': 7200, 'startup_seconds': 900}
