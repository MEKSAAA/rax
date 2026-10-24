"""Episode-review instructions in an exploring agent's workspace, for benchmarks with reviews.

While the operator has reviews on, TASK.md ends with an "Episode reviews" section in the
benchmark's own words (where each review appears and what it says), and AGENTS.md reminds the
agent to read each review before its next attempt. Both follow the setting at deploy and on
every resume (the adapter's ``refresh_workspace``).
"""
from pathlib import Path

from services.episode_reviews.common import read_json

REVIEWS_HEADING = "## Episode reviews"
REVIEW_POINTER = ("While episode reviews are on, read each episode's `review.md` (`TASK.md`, Episode reviews)\n"
                  "before your next attempt: it reports the ground-truth end state your own checks cannot see.\n")


def review_level(session_root):
    """'detailed', 'on' or None, from the operator's review setting for a session."""
    value = read_json(Path(session_root) / "operator-analysis" / "settings.json")
    if value.get("enabled") is not True:
        return None
    return "detailed" if value.get("detailed") is True else "on"


def where(rehearsals=True):
    """The section's opening lines: when and where each review appears. rehearsals: False in
    live-copy mode, which has no bundle tools. Benchmarks continue the last line."""
    if rehearsals:
        return ["About a minute after an exploration or rehearsal episode closes, a review of it appears as",
                "`review.md` in that episode's own run directory: the `artifacts.directory` its `finish` or",
                "`rehearse` reply named, under `runtime/autonomous_controller/runs/`."]
    return ["About a minute after an exploration episode closes, a review of it appears as `review.md` in",
            "that episode's own run directory: the `artifacts.directory` its `finish` reply named, under",
            "`runtime/autonomous_controller/runs/`."]


def with_section(text, lines):
    """TASK.md text with its episode-reviews section replaced by ``lines`` (None removes it)."""
    start = text.find("\n" + REVIEWS_HEADING + "\n")
    if start >= 0:
        end = text.find("\n## ", start + 1)
        text = text[:start] + ("\n" + text[end + 1:] if end >= 0 else "\n")
        text = text.rstrip("\n") + "\n"
    if not lines:
        return text
    return text.rstrip("\n") + "\n\n" + "\n".join([REVIEWS_HEADING, "", *lines, ""])


def with_review_pointer(text, level):
    """AGENTS.md text with the review reminder before its Workflow section, only while reviews are on."""
    text = text.replace(REVIEW_POINTER + "\n", "")
    anchor = "## Workflow\n"
    if level and anchor in text:
        text = text.replace(anchor, REVIEW_POINTER + "\n" + anchor, 1)
    return text


def refresh(config, workspace, section):
    """An exploring agent's TASK.md and AGENTS.md follow the session's current review setting.
    ``section(level, rehearsals=...)`` is the benchmark's TASK.md section lines for a level."""
    level = review_level(config.root)
    lines = section(level, rehearsals=not config.agent_formal) if level else None
    for name, change in (('TASK.md', lambda t: with_section(t, lines)),
                         ('AGENTS.md', lambda t: with_review_pointer(t, level))):
        path = Path(workspace)/name
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text()
        if change(text) != text:
            path.write_text(change(text))
