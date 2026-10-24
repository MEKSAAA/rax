"""LIBERO-PRO simulator process: the official LIBERO environment for one episode.

Runs only in ``runtime/envs/libero_pro`` (Python 3.8, the official requirements) with the pinned
LIBERO-PRO checkout on PYTHONPATH and ``LIBERO_CONFIG_PATH`` at the runtime-owned config
(services/libero_pro/process.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--collection", type=int, choices=(0, 1, 2, 3, 4, 5, 6), required=True,
                        help="0: exploration layouts; 1: official LIBERO; 2-5: LIBERO-PRO swap, object, language, "
                             "task; 6: swap exploration layouts (optional second stage)")
    parser.add_argument("--episode-timeout-seconds", type=int, default=0)
    parser.add_argument("--depth", dest="depth", action="store_true", default=True)
    parser.add_argument("--no-depth", dest="depth", action="store_false")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    os.environ.setdefault("MUJOCO_GL", "egl")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    from services.libero_pro.session import LiberoProSession
    from services.simulator.rpc import serve

    session = LiberoProSession(output, args.task, args.collection, include_depth=args.depth)
    print(json.dumps({"event": "environment_loaded", "task": args.task, "collection": args.collection}), flush=True)
    try:
        serve(session, args.port, episode_timeout_seconds=args.episode_timeout_seconds)
    finally:
        try:
            if session.episode_id is not None and session.video is not None:
                session.finish(session.finish_reason or "process_exit")
        finally:
            session.close()


if __name__ == "__main__":
    main()
