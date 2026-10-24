"""Demonstration files shared by every benchmark's demonstration provider: the context kinds,
JPEG checks and the atomic replacement of a trial's demonstration directory."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

DEMONSTRATION_CONTEXTS = (
    "none",
    "terminal_state",
    "completion_sequence",
)


class DemonstrationError(ValueError):
    """A selected visual demonstration cannot be safely supplied."""


def _validate_jpeg(path: Path) -> int:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(2)
            stream.seek(-2, os.SEEK_END)
            footer = stream.read(2)
    except OSError as exc:
        raise DemonstrationError(f"Cannot read terminal demo image: {path}") from exc
    if (
        not 4 <= size <= 16 * 1024 * 1024
        or header != b"\xff\xd8"
        or footer != b"\xff\xd9"
    ):
        raise DemonstrationError("Cached terminal demonstration is not a valid JPEG")
    return size


def _replace_directory(staging: Path, target: Path) -> None:
    """Replace the operator-owned trial directory without exposing partial data."""

    backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.backup")
    moved_old = False
    try:
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_dir():
                raise DemonstrationError(
                    "Trial demonstration path must be a real directory"
                )
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except Exception:
        if moved_old and not os.path.lexists(target):
            os.replace(backup, target)
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup)
