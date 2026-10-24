"""The fast storage tier that must hold session artifacts.

`RAX_ARTIFACT_ROOT` selects it explicitly. Otherwise it is the target of the
repository's `runtime` link, which `scripts/bootstrap_sources.sh` places on the chosen
storage.
"""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENVIRONMENT = "RAX_ARTIFACT_ROOT"


def artifact_root() -> Path:
    configured = os.environ.get(ENVIRONMENT)
    if configured:
        return Path(configured).resolve()
    runtime = PROJECT / "runtime"
    if runtime.exists():
        return runtime.resolve()
    raise RuntimeError(
        f"No artifact root: set {ENVIRONMENT} or create a 'runtime' symlink in {PROJECT}"
    )


def require_artifact_path(path, what="Controller artifacts") -> Path:
    path = Path(path).resolve()
    root = artifact_root()
    if not path.is_relative_to(root):
        raise ValueError(f"{what} must be under {root} (set {ENVIRONMENT} to change it)")
    return path
