"""Operator-only native reward aggregation, separate from binary success scoring."""
import json
from pathlib import Path


def formal_progress(root, formal_scores):
    root=Path(root)
    by_id={}
    patterns=['native/results/*/sim/operator_state.json',
              'formal-workers/episode-*/native/results/*/sim/operator_state.json',
              'agent-formal/episode-*/native/results/*/sim/operator_state.json']
    for pattern in patterns:
        for state in root.glob(pattern):
            metadata=json.loads(state.read_text())
            outcome=state.with_name('evaluation_outcome.json')
            if metadata.get('episode_mode')=='formal' and outcome.exists():
                by_id[metadata['episode_id']]=json.loads(outcome.read_text())
    rows=[]
    for episode in formal_scores.get('episodes',[]):
        value=by_id.get(episode.get('episode_id'),{})
        available=(not value.get('invalid_reason') and value.get('native_reward_steps',0)>0
                   and value.get('native_reward_steps')==value.get('native_control_steps')
                   and value.get('native_max_reward') is not None)
        rows.append({'episode_id':episode.get('episode_id'),'available':available,
                     'sum_reward':value.get('native_sum_reward') if available else None,
                     'max_reward':value.get('native_max_reward') if available else None})
    complete=(bool(formal_scores.get('score_complete')) and bool(rows)
              and len(rows)==formal_scores.get('episode_count') and all(r['available'] for r in rows))
    return {'schema':'robowits_native_progress_v1','complete':complete,'episodes':rows,
            'avg_max_reward':sum(r['max_reward'] for r in rows)/len(rows) if complete else None,
            'avg_sum_reward':sum(r['sum_reward'] for r in rows)/len(rows) if complete else None,
            'missing_policy':'unavailable, never reconstructed from images or binary success'}
