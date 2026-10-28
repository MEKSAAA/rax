"""RoboWits contract tests: protocol separation, actions and geometry; no Genesis import."""
import json
import sys
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
from services.robowits import tasks as T, tools
from services.benchmarks import benchmark
from services.simulator.io import RejectedBeforeAction


def test_catalog_and_disjoint_layouts():
    assert len([i for i in T.CATALOG.values() if not i['mutation']]) == 30
    for task in T.task_names():
        assert T.layout(task,0,0)['kind']=='random_reset'
        if T.task_info(task)['formal_layouts']:
            assert T.layout(task,1,0)['kind']=='official_json'
        assert T.native_step_limit(task)==1000
    with pytest.raises(ValueError): T.layout('robowits_06',1,50)
    with pytest.raises(ValueError): T.layout('robowits_06',0,-1)
    with pytest.raises(ValueError): T.layout('robowits_06',2,0)


def test_actions_absolute_not_delta_and_reject_bad_rows():
    row=[.4,-.2,-.1,.4,.2,-.1,0,1.5,0,0,-1.5,0,.08,.08]
    tools.validate_actions([row])
    for bad in [[],[[0]*7],[[float('nan')]*14],[row[:-1]+[-.01]],[[3]+row[1:]],[row]*101]:
        with pytest.raises(RejectedBeforeAction): tools.validate_actions(bad)


def test_pixel_geometry_and_frame():
    K={'matrix':[[100,0,20],[0,100,15],[0,0,1]]}
    pose={'position_m':[.2,.3,.4],'quaternion_wxyz':[1,0,0,0]}
    d=np.full((30,40),.5)
    q=tools.pixel_to_world(d,K,pose,20,15,0)
    assert q['frame']=='robot_base'
    np.testing.assert_allclose(q['position_m'],[.2,.3,-.1])
    np.testing.assert_allclose(tools.world_to_pixel(q['position_m'],K,pose),[20,15,.5])
    with pytest.raises(RejectedBeforeAction): tools.pixel_to_world(d,K,pose,100,15)


def test_host_imports_no_simulator():
    from services.robowits.backend import RoboWitsBackend
    from services.robowits.process import simulator_environment
    assert not {'gs_gym','genesis'} & sys.modules.keys()
    env=simulator_environment('GPU-fixture',{})
    assert env['CUDA_VISIBLE_DEVICES']=='GPU-fixture'
    assert str(T.source_root()) in env['PYTHONPATH']
    assert benchmark('robowits').graded_score is False


def test_task_prompt_has_no_scene_ground_truth(tmp_path):
    from services.robowits.benchmark import task_context
    from test_controller_backend import configuration
    c=replace(configuration(tmp_path),benchmark='robowits',task='robowits_06',demonstration_context='none')
    text=task_context(c,{},live_formal=False)
    assert T.instruction(c.task) in text
    assert 'white_block_4' not in text
    assert '1000' in text


def test_batch_report_preserves_missing_denominator(tmp_path):
    from scripts.evaluate_robowits import report
    trial=tmp_path/'trial';trial.mkdir()
    (trial/'formal-scores.json').write_text(json.dumps({'episodes':[{'score':1},{'score':0}], 'score_complete':True}))
    manifest={'formal_episodes':2,'tasks':{'robowits_06':{'status':'finished','config':str(trial/'operator-input.json')},
                                       'robowits_01':{'status':'runtime_error'}}}
    report(tmp_path,manifest)
    summary=json.loads((tmp_path/'summary.json').read_text())
    assert summary['expected_episodes']==4
    assert summary['success_rate_fixed_denominator']==.25
    assert summary['complete'] is False


@pytest.mark.parametrize('collection',[0,1])
def test_native_constructor_preserves_task_specific_options(tmp_path,monkeypatch,collection):
    from types import SimpleNamespace
    from services.robowits.session import RoboWitsSession
    calls={}
    class Env:
        task_description='native instruction'
        def __init__(self,**kwargs): calls.update(kwargs)
        def seed(self,value): calls['explicit_seed']=value
        def set_eval_dataset_idx(self,value): calls['dataset_index']=value
    monkeypatch.setitem(sys.modules,'gs_gym',SimpleNamespace(get_task_class=lambda name:Env))
    session=RoboWitsSession(tmp_path,'robowits_26',collection,include_depth=True)
    session.layout=T.layout('robowits_26',collection,3)
    session._build()
    # Task 26 supplies its own config_overrides: passing another one breaks construction.
    assert 'config_overrides' not in calls
    assert calls['max_episode_steps']==1000
    assert calls['explicit_seed']==(10003 if collection==0 else 42)
    assert ('eval_dataset_json' in calls)==bool(collection)
    if collection: assert calls['dataset_index']==3


def test_zero_exit_without_formal_scores_is_not_finished(tmp_path):
    from scripts.evaluate_robowits import trial_status
    config=tmp_path/'operator-input.json'
    assert trial_status(config,5,0)=='incomplete_trial'
    sim=tmp_path/'native/results/failed';sim.mkdir(parents=True)
    (sim/'sim.log').write_text('PIL.Image.DecompressionBombError')
    assert trial_status(config,5,0)=='infrastructure_error'
    (tmp_path/'formal-scores.json').write_text(json.dumps({'score_complete':True,'episodes':[{'native_complete':True,'source':'native_outcome','score':0}]*5}))
    assert trial_status(config,5,0)=='finished'
    assert trial_status(config,6,0)!='finished'


def test_preflight_calls_only_simulator_diagnostic(tmp_path,monkeypatch):
    from scripts import evaluate_robowits as batch
    from types import SimpleNamespace
    calls=[]
    def fail(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(batch.subprocess,'run',fail)
    assert not batch.run_preflight(tmp_path,'robowits_04','1')
    assert len(calls)==1
    assert calls[0][1]=='scripts/robowits_doctor.py'
    assert 'start_auto_research_agent.sh' not in ' '.join(calls[0])
