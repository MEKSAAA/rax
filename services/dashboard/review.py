"""Read-only episode review: every simulator episode of a run and its trajectory video.

The native simulator records each episode's cameras as ``sim/sensors.mp4``, so review streams
those files directly; nothing is re-encoded and nothing in the run is modified.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from services.scoring import FORMAL_WORKER_DIRECTORIES, episode_score

TIMESTAMP = re.compile(r"\d{8}T\d{6}Z")
# Simulator episodes of an auto-research session directory: its own, parallel exploration's,
# and each formal worker's (bundle formal in formal-workers/, live agent copies in agent-formal/).
SESSION_PATTERNS = ("native/results/*/sim", "exploration/episode-*/native/results/*/sim",
                    *(f"{workers}/episode-*/native/results/*/sim" for workers in FORMAL_WORKER_DIRECTORIES))
WORKER = re.compile(r"(%s)/episode-(\d+)/" % "|".join(map(re.escape, FORMAL_WORKER_DIRECTORIES)))


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _results(directory):
    """Controller records by native episode ID, from a session and its formal workers."""
    rows = {}
    workers = (path for name in FORMAL_WORKER_DIRECTORIES for path in sorted(directory.glob(f"{name}/episode-*/state.json")))
    for state in (directory / "state.json", *workers):
        for result in _read(state).get("results", []):
            if result.get("episode_id"):
                rows[result["episode_id"]] = result
    return rows


def _started(sim):
    match = TIMESTAMP.search(sim.parent.name)
    if match:
        return datetime.strptime(match[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    return sim.stat().st_mtime


def _formal_index(relative, record):
    # A formal worker records itself as episode 1; its directory has the batch index.
    worker = WORKER.match(relative)
    return int(worker[2]) if worker else record.get("formal_episode_index")


def episodes(directory, patterns=SESSION_PATTERNS):
    """Chronological episodes of one run directory (already validated by its caller)."""
    directory = Path(directory)
    root = directory.resolve()
    results = _results(directory)
    rows = []
    for pattern in patterns:
        for sim in directory.glob(pattern):
            if sim.is_symlink() or not sim.resolve().is_relative_to(root):
                continue
            summary, state, reset, outcome = (_read(sim / name) for name in (
                "summary.json", "operator_state.json", "reset.json", "evaluation_outcome.json"))
            episode_id = summary.get("episode_id") or state.get("episode_id") or reset.get("episode_id")
            record = results.get(episode_id, {})
            mode = record.get("mode") or state.get("episode_mode") or "unknown"
            relative = str(sim.relative_to(directory))
            if relative.startswith("agent-formal/"):
                mode = "agent_formal"  # A live copy of the agent, not the frozen bundle.
            video = sim / "sensors.mp4"
            reward = state.get("reward") or {}
            # An episode whose launcher died with it (state.json: supervisor_interrupted) never wrote
            # its summary; it is over, and its last native snapshot is its score.
            interrupted = record.get("reason") == "supervisor_interrupted"
            score = episode_score(sim, episode_id) if summary or interrupted else {"score": None}
            complete = record.get("task_complete")
            if complete is None and outcome:
                complete = outcome.get("valid_for_success_rate") is True and outcome.get("native_success") is True
            rows.append({
                "episode": relative,
                "episode_id": episode_id,
                "mode": "exploration" if mode == "interactive" else mode,
                "formal_episode_index": _formal_index(relative, record),
                "steps": summary.get("step_id", state.get("step_id")),
                "step_limit": state.get("episode_step_limit"),
                "reason": summary.get("reason") or state.get("reason") or record.get("reason"),
                "native_success": summary.get("success", reward.get("native_success")),
                "native_score": score.get("score"),
                "task_complete": complete,
                "bundle": record.get("bundle"),
                "live": not summary and not interrupted,
                "video": video.is_file() and not video.is_symlink(),
                "video_bytes": video.stat().st_size if video.is_file() else 0,
                "started_at": _started(sim),
            })
    return sorted(rows, key=lambda row: (row["started_at"], row["episode"]))


def video_path(directory, episode, patterns=SESSION_PATTERNS):
    """The sensors.mp4 of a listed episode; anything else is rejected."""
    directory = Path(directory)
    if (not isinstance(episode, str) or Path(episode).is_absolute() or ".." in Path(episode).parts
            or not any(Path(episode).match(p) for p in patterns)):
        raise ValueError("Unknown episode")
    sim = directory / episode
    if sim.is_symlink() or not sim.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Unknown episode")
    video = sim / "sensors.mp4"
    if video.is_symlink() or not video.is_file():
        raise FileNotFoundError("No video recorded for this episode")
    return video
