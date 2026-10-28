"""Drain an existing trial and stop its scheduler before another model session starts."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_robowits import report, trial_status, write


def command(pid):
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
    except FileNotFoundError:
        return ''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scheduler-pid',type=int,required=True)
    p.add_argument('--trial-pid',type=int,required=True)
    p.add_argument('--task',required=True)
    a=p.parse_args();root=a.output.resolve()
    m=json.loads((root/'manifest.json').read_text());config=m['tasks'][a.task]['config']
    if 'scripts/evaluate_robowits.py' not in command(a.scheduler_pid):p.error('Scheduler PID does not match batch runner')
    if config not in command(a.trial_pid):p.error('Trial PID does not match selected task configuration')
    os.kill(a.scheduler_pid,signal.SIGSTOP)
    (root/'pause-monitor.pid').write_text(str(os.getpid()))
    print(f'Draining {a.task}; scheduler {a.scheduler_pid} stopped.',flush=True)
    while config in command(a.trial_pid):time.sleep(5)
    if 'scripts/evaluate_robowits.py' in command(a.scheduler_pid):
        os.kill(a.scheduler_pid,signal.SIGTERM)
        os.kill(a.scheduler_pid,signal.SIGCONT)
    m=json.loads((root/'manifest.json').read_text())
    m['tasks'][a.task].update(status=trial_status(config,m['formal_episodes']),finished=time.time())
    m['queue_status']='paused_for_implementation_audit'
    m['paused_at']=time.time()
    write(root/'manifest.json',m);report(root,m)
    print(f'{a.task} exited; queue paused. No next task launched.',flush=True)


if __name__=='__main__':main()
