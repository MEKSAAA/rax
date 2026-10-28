"""Exercise the actual RoboWits process through RAX RPC, without a model call."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.robowits.process import RoboWitsProcess
from services.robowits.tasks import runtime_root
from services.robowits.tools import pixel_to_world, world_to_pixel

def main():
    p=argparse.ArgumentParser();p.add_argument('--task',default='robowits_06');p.add_argument('--gpu',default='1')
    p.add_argument('--collection',type=int,default=0);p.add_argument('--layout',type=int,default=0)
    a=p.parse_args()
    import time
    root=runtime_root()/'diagnostics'/f'robowits-{time.time_ns()}'
    sim=RoboWitsProcess(output_root=root)
    try:
        sim.start(task=a.task,collection=a.collection,layout_id=a.layout,run_id='doctor',sim_gpu=a.gpu)
        obs=sim.request('observe')
        assert np.asarray(obs['state']).shape==(14,)
        report={'task':a.task,'collection':a.collection,'cameras':{},'state':obs['state']}
        from PIL import Image
        for c in ('ego','wrist_right','wrist_left'):
            assert obs[c].shape==(480,848,3)
            Image.fromarray(obs[c]).save(root/(c+'.png'))
            d=obs[c+'_depth_m'];assert d.shape==(480,848)
            assert np.isfinite(d).any() and (d>0).any()
            v,u=np.argwhere(np.isfinite(d)&(d>0))[len(np.argwhere(np.isfinite(d)&(d>0)))//2]
            x=pixel_to_world(d,obs['camera_intrinsics'][c],obs['camera_poses'][c],int(u),int(v),0)
            q=world_to_pixel(x['position_m'],obs['camera_intrinsics'][c],obs['camera_poses'][c])
            assert np.linalg.norm(np.asarray(q[:2])-[u,v])<1e-5
            report['cameras'][c]={'shape':list(obs[c].shape),'depth_range':[float(d.min()),float(d.max())]}
        held=sim.request('chunk_step',actions=np.asarray([obs['state']]*5))
        assert held['step_id']==5
        report['hold_drift_m']=float(np.linalg.norm(np.asarray(held['frames'][-1]['state'][:6])-obs['state'][:6]))
        sim.finish('doctor')
        report['outcome']=json.loads((sim.output_dir/'sim/evaluation_outcome.json').read_text())
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2));print(root)
    finally:sim.stop()
if __name__=='__main__':main()
