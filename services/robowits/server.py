"""Serve one RoboWits episode through the shared RAX identity-checked RPC protocol."""

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
    parser.add_argument("--collection", type=int, choices=(0, 1), required=True,
                        help="0: random exploration; 1: released evaluation scene JSON")
    parser.add_argument("--episode-timeout-seconds", type=int, default=0)
    parser.add_argument("--depth", dest="depth", action="store_true", default=True)
    parser.add_argument("--no-depth", dest="depth", action="store_false")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    from services.robowits.session import RoboWitsSession
    from services.simulator.rpc import serve

    session = RoboWitsSession(output, args.task, args.collection, include_depth=args.depth)
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
