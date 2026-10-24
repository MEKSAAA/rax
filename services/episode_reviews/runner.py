"""Background reviews of finished exploration episodes (operator-side), for every benchmark whose
simulator writes ground truth.

Reviews are part of closing an episode: the session's episode management (the supervisor
for single-environment episodes and rehearsals, the exploration pool for parallel ones)
reports each closed episode to the session's ``EpisodeReviews``, owned by its MCP service
(``services.controller.frontend``), so no dashboard is involved. While the operator has
reviews on, every closed exploration or rehearsal episode gets one review from the
session's benchmark's analysis (its adapter's ``review_analysis()``, see
``services/episode_reviews/__init__.py``):
- the operator record: ``<session>/operator-analysis/<episode_id>.json`` (0600), never
  readable by the agent;
- the agent's copy: ``review.md`` in that episode's own published run directory (the
  ``artifacts.directory`` its ``finish`` or ``rehearse`` reply named), which the agent reads
  read-only under ``runtime/autonomous_controller/runs/`` in its workspace. TASK.md says where.

Formal episodes are never reviewed. Gemini costs go to ``operator-analysis/usage.jsonl``
and never to the agent's own budget.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from services.dashboard import review
from services.episode_reviews.common import REVIEWABLE_MODES, SESSION_BUDGET_USD, read_json as _read, rgb_images

ANALYSIS_DIR = "operator-analysis"
REVIEW_FILE = "review.md"
KEY_FILE = Path.home() / ".codex/secrets/openrouter_api_key"
WORKERS = 4  # Concurrent reviews, one per parallel exploration environment.
WAIT_SECONDS = 180  # For a closed episode's simulator files to appear.
MAX_ATTEMPTS = 2


def _write(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = value if isinstance(value, str) else json.dumps(value, indent=2, allow_nan=False) + "\n"
    temporary.write_text(data, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def analysis_dir(session):
    path = Path(session) / ANALYSIS_DIR
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def settings(session):
    value = _read(Path(session) / ANALYSIS_DIR / "settings.json")
    return {"enabled": bool(value.get("enabled", False)), "enabled_at": value.get("enabled_at"),
            "detailed": value.get("detailed") is True}


def set_settings(session, *, enabled, detailed=False):
    """Reviews on or off; detailed reviews also give the agent the ground-truth report and exact
    numbers (and, with partial credit, the episode's partial score)."""
    if type(enabled) is not bool or type(detailed) is not bool:
        raise ValueError("enabled and detailed must be true or false")
    previous = settings(session)
    # Episodes that closed while reviews were off stay unreviewed.
    enabled_at = (previous["enabled_at"] if previous["enabled"] else time.time()) if enabled else None
    _write(analysis_dir(session) / "settings.json", {"enabled": enabled, "enabled_at": enabled_at,
                                                     "detailed": enabled and detailed, "updated_at": time.time()})
    return settings(session)


def spent(session):
    total = 0.0
    try:
        for line in (Path(session) / ANALYSIS_DIR / "usage.jsonl").read_text().splitlines():
            total += float(json.loads(line).get("cost_usd") or 0.0)
    except (OSError, ValueError):
        pass
    return total


def record_path(session, episode_id):
    if not isinstance(episode_id, str) or not episode_id or "/" in episode_id or episode_id.startswith("."):
        raise ValueError("Invalid episode")
    return Path(session) / ANALYSIS_DIR / f"{episode_id}.json"


def records(session):
    directory = Path(session) / ANALYSIS_DIR
    return {path.stem: _read(path) for path in sorted(directory.glob("*.json")) if path.name != "settings.json"}


def analysis_for(session):
    """The review analysis of the session's benchmark (its adapter's ``review_analysis()``), or None
    when that simulator has no ground truth to review."""
    from services import benchmarks
    name = _read(Path(session) / "configuration.json").get("benchmark", benchmarks.DEFAULT)
    return benchmarks.benchmark(name).review_analysis()


def _episode_artifacts(session):
    """Published trace and final images, and the exploration episode number, by episode ID."""
    rows = {}
    for entry in _read(Path(session) / "exploration" / "state.json").get("episodes", []):
        if entry.get("episode_id"):
            rows[entry["episode_id"]] = {"number": entry.get("episode"), "artifacts": entry.get("artifacts") or {}}
    for entry in _read(Path(session) / "state.json").get("results", []):
        if entry.get("episode_id"):
            number = entry.get("episode") if entry.get("mode") == "interactive" else None
            rows.setdefault(entry["episode_id"], {"number": number, "artifacts": entry.get("artifacts") or {}})
    return rows


def _published(session, logical):
    """A published file from its agent-visible logical path (``runtime/autonomous_controller/...``)."""
    relative = PurePosixPath(logical).relative_to("runtime/autonomous_controller")
    if ".." in relative.parts:
        raise ValueError("Invalid published path")
    return Path(session) / "published" / str(relative)


def review_episode(session, row, *, router_factory, instruction="", analysis=None):
    """Review one finished episode row from ``review.episodes`` with the benchmark's ``analysis``
    (default: the session's); returns its operator record."""
    session = Path(session)
    analysis = analysis or analysis_for(session)
    episode_id = row["episode_id"]
    path = record_path(session, episode_id)
    previous = _read(path)
    meta = _episode_artifacts(session).get(episode_id, {})
    artifacts = meta.get("artifacts", {})
    record = {"episode_id": episode_id, "episode_number": meta.get("number"), "sim": row["episode"],
              "mode": row["mode"], "attempts": previous.get("attempts", 0) + 1, "started_at": time.time()}
    report = analysis.ground_truth_report(session / row["episode"])
    if report is None or not artifacts.get("directory"):
        _write(path, {**record, "status": "unavailable",
                      "detail": "the simulator wrote no goal report" if report is None else "no published run directory"})
        return _read(path)
    record["report"] = report
    if spent(session) >= SESSION_BUDGET_USD:
        _write(path, {**record, "status": "budget_exhausted"})
        return _read(path)
    trace = analysis.condensed_trace(_published(session, artifacts["mcp_trace"])) if artifacts.get("mcp_trace") else []
    images = rgb_images([_published(session, image["path"]) for image in artifacts.get("final_images", [])])
    detailed, score = settings(session)["detailed"], row.get("native_score")
    record["detailed"] = detailed
    try:
        explanation, cost = analysis.explain(router_factory(), report, trace, images, task_instruction=instruction,
                                             detailed=detailed, score=score)
    except Exception as exc:  # noqa: BLE001 - a failed review is recorded, never raised into the dashboard
        _write(path, {**record, "status": "error", "detail": f"{type(exc).__name__}: {exc}"[:500]})
        return _read(path)
    with (analysis_dir(session) / "usage.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "episode_id": episode_id, "cost_usd": cost}) + "\n")
    agent_text = analysis.agent_review(report, explanation, episode_number=record["episode_number"],
                                       detailed=detailed, score=score, run=artifacts["directory"])
    # Beside the episode's own trace and frames, so it cannot be confused with another episode's.
    _write(_published(session, artifacts["directory"]) / REVIEW_FILE, agent_text, mode=0o644)
    _write(path, {**record, "status": "ready", "explanation": explanation, "agent_review": agent_text,
                  "cost_usd": cost, "finished_at": time.time()})
    return _read(path)


def pending(session):
    """Finished, reviewable episode rows of a session that still need a review."""
    rows, since = [], settings(session)["enabled_at"] or 0
    for row in review.episodes(session):
        if row["live"] or row["mode"] not in REVIEWABLE_MODES or not row.get("episode_id"):
            continue
        try:
            if (Path(session) / row["episode"] / "summary.json").stat().st_mtime < since:
                continue
        except OSError:
            continue
        record = _read(record_path(session, row["episode_id"]))
        if record.get("status") in ("ready", "unavailable", "budget_exhausted"):
            continue
        if record.get("status") == "error" and record.get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        rows.append(row)
    return rows


def _router():
    from services.controller.gemini import GeminiRouter, load_key
    key = load_key(KEY_FILE) if KEY_FILE.is_file() else load_key()
    return GeminiRouter(key)


_UNRESOLVED = object()


class EpisodeReviews:
    """Reviews each exploration or rehearsal episode of one session as it closes.

    The session's episode management calls ``closed(episode_id)`` when an episode ends;
    the review runs in the background (it takes about a minute) so closing never waits for
    it. ``start`` catches up on episodes that closed while no MCP service ran. The analysis
    is the session's benchmark's; a benchmark without one is never reviewed.
    """

    def __init__(self, session, *, router_factory=_router, workers=WORKERS, wait_seconds=WAIT_SECONDS,
                 poll_seconds=2.0):
        self.session = Path(session)
        self.router_factory = router_factory
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds
        self.executor = ThreadPoolExecutor(workers, thread_name_prefix="episode-review")
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._queued = set()
        self._instruction = None
        self._analysis = _UNRESOLVED

    def start(self):
        if settings(self.session)["enabled"]:
            for row in pending(self.session):
                self._submit(row["episode_id"])
        return self

    def closed(self, episode_id):
        """An episode just closed: review it if reviews are on. Never raises into the caller."""
        try:
            if episode_id and settings(self.session)["enabled"] and not self.stopped.is_set():
                self._submit(episode_id)
        except Exception as exc:  # noqa: BLE001 - a review never breaks episode closing
            _log(exc)

    def _submit(self, episode_id):
        with self._lock:
            if episode_id in self._queued:
                return
            self._queued.add(episode_id)
        self.executor.submit(self._review, episode_id)

    def analysis(self):
        if self._analysis is _UNRESOLVED:
            self._analysis = analysis_for(self.session)
        return self._analysis

    def instruction(self):
        if self._instruction is None:
            task = _read(self.session / "configuration.json").get("task", "")
            self._instruction = self.analysis().task_instruction(task) if task else ""
        return self._instruction

    def _review(self, episode_id):
        """Wait for the closed episode's simulator files, then review it (one retry on error)."""
        try:
            analysis = self.analysis()
            if analysis is None:
                return None  # The session's simulator writes no ground truth to review.
            deadline = time.monotonic() + self.wait_seconds
            record = None
            while not self.stopped.is_set():
                rows = {row["episode_id"]: row for row in review.episodes(self.session) if row.get("episode_id")}
                row = rows.get(episode_id)
                if row and not row["live"]:
                    if row["mode"] not in REVIEWABLE_MODES:
                        return None
                    if not any(r["episode_id"] == episode_id for r in pending(self.session)):
                        return record
                    record = review_episode(self.session, row, router_factory=self.router_factory,
                                            instruction=self.instruction(), analysis=analysis)
                    if record.get("status") != "error":
                        return record
                elif time.monotonic() > deadline:
                    return None
                self.stopped.wait(self.poll_seconds)
        except Exception as exc:  # noqa: BLE001 - recorded on stderr, never raised into the session
            _log(exc)
        return None

    def close(self):
        self.stopped.set()
        self.executor.shutdown(wait=False, cancel_futures=True)


def _log(exc):
    print(f"[episode-reviews] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
