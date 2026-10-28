"""Run/resume RAX on RoboWits; keep failed/missing trials in the fixed denominator.

Run with the RAX host environment activated. Credentials stay in provider-home.
"""
import argparse
import hashlib
import fcntl
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.robowits import tasks as T

ROBOWITS_10 = tuple('robowits_' + n for n in '01 02 03 04 06 09 13 16 17 25'.split())


def write(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def trial_status(config, expected, returncode=0):
    """CLI exit is not evidence that any evaluation ran."""
    root = Path(config).parent
    path = root / 'formal-scores.json'
    scores = json.loads(path.read_text()) if path.exists() else {}
    rows = scores.get('episodes', [])
    if (scores.get('score_complete') and len(rows) == expected
            and all(row.get('source') == 'native_outcome' and row.get('score') in (0, 1) for row in rows)):
        return 'finished'
    if returncode:
        return 'runtime_error'
    sims = list((root / 'native/results').glob('*/sim.log'))
    if sims and not any((p.parent/'sim/reset.json').exists() for p in sims):
        return 'infrastructure_error'
    return 'incomplete_trial'


def run_preflight(root, task, gpu):
    """Fail before spending model tokens if this task cannot reset/observe/step."""
    with (root / f'{task}.preflight.log').open('a') as log:
        result = subprocess.run([sys.executable, 'scripts/robowits_doctor.py',
                                 '--task', task, '--gpu', str(gpu)],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return result.returncode == 0


def report(root, manifest):
    rows = []
    for task, entry in manifest['tasks'].items():
        path = Path(entry.get('config', '/nonexistent')).parent / 'formal-scores.json'
        scores = json.loads(path.read_text()) if path.is_file() else {}
        episodes = scores.get('episodes', [])
        success = sum(float(e.get('score', 0) or 0) for e in episodes)
        rows.append(dict(task=task, status=entry['status'], successes=success,
                         expected=manifest['formal_episodes'], recorded=len(episodes),
                         complete=bool(scores.get('score_complete', False)),
                         config=entry.get('config')))
    total = len(rows) * manifest['formal_episodes']
    write(root / 'summary.json', dict(tasks=rows, expected_episodes=total,
          successes=sum(r['successes'] for r in rows),
          success_rate_fixed_denominator=sum(r['successes'] for r in rows) / total if total else 0,
          completed_tasks=sum(r['complete'] for r in rows),
          completed_episodes=sum(r['recorded'] for r in rows if r['complete']),
          infrastructure_errors=[r['task'] for r in rows if r['status']=='infrastructure_error'],
          complete=all(r['complete'] for r in rows),
          protocol='Unrecorded episodes count as zero; incomplete runs are explicitly flagged.'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True, help='Persistent batch directory; reused to resume')
    p.add_argument('--provider-home', type=Path, required=True)
    p.add_argument('--profile', default='chatgpt')
    p.add_argument('--tasks', nargs='+', help='Explicit task IDs; overrides --suite for new batches')
    p.add_argument('--suite', choices=['robowits-10','all-seed'], default='robowits-10')
    p.add_argument('--gpu', default='1')
    p.add_argument('--episodes', type=int, default=8)
    p.add_argument('--formal-episodes', type=int, default=5)
    p.add_argument('--adopt-config', type=Path, action='append', default=[], help='Use an existing trial; does not launch it again')
    p.add_argument('--report-only', action='store_true')
    p.add_argument('--skip-preflight', action='store_true', help='Skip simulator startup check; normally keep this enabled')
    p.add_argument('--resume', action='store_true', help='Explicitly resume a batch paused for review')
    a = p.parse_args()
    root = a.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    lock = None
    if not a.report_only:
        lock = (root / 'batch.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('Another batch runner owns this output directory')
    selected = a.tasks or (list(ROBOWITS_10) if a.suite == 'robowits-10' else [t for t in T.task_names() if not T.task_info(t)['mutation']])
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if a.tasks and set(a.tasks) != set(manifest['tasks']):
            p.error('Existing manifest has a different task list; use a new output directory or explicitly migrate the queue')
        if not a.report_only and (manifest['episodes'] != a.episodes or manifest['formal_episodes'] != a.formal_episodes):
            p.error('Resume must preserve the original episode budgets')
    else:
        profile = tomllib.loads((a.provider_home / (a.profile + '.config.toml')).read_text())
        manifest = dict(benchmark='robowits', suite='custom' if a.tasks else a.suite, model=profile.get('rax', {}).get('model'), provider_profile=a.profile,
            episodes=a.episodes, formal_episodes=a.formal_episodes,
            created=time.time(), source_commit=subprocess.check_output(['git','-C',str(T.source_root()),'rev-parse','HEAD'],text=True).strip(),
            tasks={t:dict(status='pending', dataset_sha256=hashlib.sha256(T.eval_path(t).read_bytes()).hexdigest()) for t in selected})
    for config in a.adopt_config:
        data = json.loads(config.read_text())
        task = data['task']
        if data.get('benchmark') != 'robowits' or len(data['exploration_seeds']) != manifest['episodes'] or data['formal_episodes'] != manifest['formal_episodes']:
            p.error('Adopted trial must match benchmark and budgets')
        if task not in manifest['tasks']: p.error(f'Adopted task {task} is outside batch')
        manifest['tasks'][task].update(config=str(config.resolve()), status='adopted')
    if a.adopt_config or not manifest_path.exists():
        write(manifest_path, manifest)
    if a.report_only:
        report(root, manifest); return
    if manifest.get('queue_status', '').startswith('paused') and not a.resume:
        p.error('Batch is paused for review; use --resume only after resolving the audit findings')
    if a.resume:
        manifest['queue_status'] = 'running'
        manifest.pop('pause_after_current', None)
        write(manifest_path, manifest)
    for task, entry in manifest['tasks'].items():
        if entry['status'] != 'pending': continue
        if not a.skip_preflight and not run_preflight(root, task, a.gpu):
            entry.update(status='infrastructure_error', phase='preflight', finished=time.time())
            write(manifest_path, manifest); report(root, manifest)
            continue
        try:
            prepared = subprocess.check_output([sys.executable,'scripts/configure_auto_research.py',
                '--benchmark','robowits','--task',task,'--sim-gpu',a.gpu,
                '--episodes',str(a.episodes),'--formal-episodes',str(a.formal_episodes),
                '--formal-workers','1','--exploration-envs','1','--subagents','0',
                '--external-perception','off','--demonstration-context','none'],cwd=ROOT,text=True)
        except subprocess.CalledProcessError as exc:
            entry.update(status='configuration_error', returncode=exc.returncode, finished=time.time())
            write(manifest_path, manifest); report(root, manifest)
            continue
        config = prepared.strip().splitlines()[-1]
        entry.update(config=config,status='running',started=time.time())
        write(manifest_path,manifest)
        print(f'{task}: {config}',flush=True)
        with (root/f'{task}.log').open('a') as log:
            result = subprocess.run(['bash','scripts/start_auto_research_agent.sh','--config',config,
                '--provider-home',str(a.provider_home.resolve()),'--profile',a.profile,'--non-interactive'],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        entry.update(status=trial_status(config, manifest['formal_episodes'], result.returncode),returncode=result.returncode,finished=time.time())
        write(manifest_path,manifest); report(root,manifest)
    report(root,manifest)


if __name__ == '__main__': main()
