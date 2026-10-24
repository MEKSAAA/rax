"""Partial score survives controller failure without becoming binary success."""
import json
from types import SimpleNamespace

import pytest

from services.scoring import episode_score, formal_scores
from bench import TASK


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def snapshot(root, number, percent, *, success=False, unstable=False, mode='formal', workers='formal-workers'):
    sim = root/f'{workers}/episode-{number:03d}/native/results/run/sim'
    write(sim/'operator_state.json', {'episode_id': f'episode{number}', 'step_id': 544,
        'episode_mode': mode, 'reward': {'native_score_percent': percent,
        'native_success': success, 'unstable_native_layout': unstable}})
    write(sim/'evaluation_outcome.json', {'native_score': None, 'complete': False, 'status': 'incomplete'})
    return sim


def test_legacy_native_fallback_uses_last_score_not_success_label(tmp_path):
    sim = snapshot(tmp_path, 1, 50)
    assert episode_score(sim, 'episode1')['score'] == .5
    assert episode_score(sim, 'other')['score'] is None
    sim = snapshot(tmp_path, 1, 0, success=True)
    assert episode_score(sim)['score'] == 1
    sim = snapshot(tmp_path, 1, 100, success=True, unstable=True)
    assert episode_score(sim)['score'] is None


@pytest.mark.parametrize('bad', [None, True, float('nan'), -5, 101, '50'])
def test_missing_or_invalid_score_not_fabricated(tmp_path, bad):
    assert episode_score(snapshot(tmp_path, 1, bad))['score'] is None


def test_batch_retains_partial_credit_on_error_and_fixed_denominator(tmp_path):
    rows = [{'formal_episode_index': n, 'episode_id': f'episode{n}',
             'formal_outcome': 'error' if n==2 else 'success'} for n in range(1, 5)]
    snapshot(tmp_path, 1, 0, success=True)
    snapshot(tmp_path, 2, 50)
    snapshot(tmp_path, 3, 100, unstable=True)
    batch = {'status': 'completed', 'episode_count': 4, 'episodes': rows}
    score = formal_scores(tmp_path, batch)
    assert score['average_score'] == .375
    assert score['average_score_percent'] == 37.5
    assert score['recorded_score_count'] == 2 and score['missing_score_count'] == 2
    assert not score['score_complete']
    batch['status'] = 'running'
    assert formal_scores(tmp_path, batch)['average_score'] is None


def test_agent_formal_copies_are_scored(tmp_path):
    snapshot(tmp_path, 1, 25, workers='agent-formal')
    batch = {'status': 'completed', 'episode_count': 2,
             'episodes': [{'formal_episode_index': 1, 'episode_id': 'episode1', 'driver': 'agent'}]}
    score = formal_scores(tmp_path, batch)
    assert score['episodes'][0]['score'] == .25 and score['average_score'] == .125


def test_exploration_snapshot_is_not_a_formal_score(tmp_path):
    snapshot(tmp_path, 1, 100, success=True, mode='exploration')
    batch = {'status':'completed', 'episode_count':1, 'episodes':[{'index':0, 'episode_id':'episode1'}]}
    score = formal_scores(tmp_path, batch)
    assert score['average_score'] == 0 and score['recorded_score_count'] == 0


def test_operator_dashboard_enriches_archive_without_changing_agent_state(tmp_path):
    from services.dashboard.sessions import ResearchSessions
    root = tmp_path/'runtime/auto-research/20260927T235115Z_bb7cb7a096'
    write(root/'operator-input.json', {'task':TASK, 'mode':'auto-research'})
    write(root/'state.json', {'results':[{'mode':'formal','formal_episode_index':1,
        'episode_id':'episode1'}], 'formal_batch':{'status':'completed', 'episode_count':1,
        'completed_episodes':1, 'success_count':0, 'success_rate':0}})
    snapshot(root, 1, 50)
    before = (root/'state.json').read_bytes()
    result = ResearchSessions(tmp_path, [TASK]).summary(root)
    assert result['formal_batch']['score_summary']['average_score_percent'] == 50
    assert (root/'state.json').read_bytes() == before
    assert 'score_summary' not in json.loads(before)['formal_batch']


def test_every_formal_driver_directory_is_scored():
    from services.controller.supervisor import DRIVERS
    from services.agent_formal.worker import DIRECTORY
    from services.scoring import FORMAL_WORKER_DIRECTORIES
    assert {d['workers'] for d in DRIVERS.values()} | {DIRECTORY} <= set(FORMAL_WORKER_DIRECTORIES)


def test_official_seed_groups_report_mean_and_std(tmp_path):
    """Formal layouts that are official seeds' episodes: the report groups them by seed."""
    rows = []
    for n, (seed, success) in enumerate([(0, True), (0, False), (1, True), (1, True), (2, False), (2, False)], 1):
        sim = tmp_path / 'formal-workers' / f'episode-{n:03d}' / 'native' / 'results' / f'run{n}' / 'sim'
        write(sim / 'operator_state.json', {'episode_id': f'e{n}', 'episode_mode': 'formal', 'official_seed': seed,
                                            'reward': {'native_success': success}})
        write(sim / 'evaluation_outcome.json', {'native_score': 1.0 if success else 0.0})
        rows.append({'formal_episode_index': n, 'episode_id': f'e{n}'})
    score = formal_scores(tmp_path, {'status': 'completed', 'episode_count': 6, 'episodes': rows})
    seeds = score['official_seeds']
    assert {k: v['average_score'] for k, v in seeds['per_seed'].items()} == {'0': .5, '1': 1.0, '2': 0.0}
    assert seeds['mean'] == pytest.approx(.5) and seeds['std'] == pytest.approx((1/6) ** .5)
    assert score['average_score'] == pytest.approx(.5)
    # Benchmarks without official seeds keep the report unchanged.
    snapshot(tmp_path / 'other', 1, 50)
    plain = formal_scores(tmp_path / 'other', {'status': 'completed', 'episode_count': 1,
                                               'episodes': [{'formal_episode_index': 1, 'episode_id': 'episode1'}]})
    assert 'official_seeds' not in plain
