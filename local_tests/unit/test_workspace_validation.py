"""Every generated workspace passes the workspace validator (services/workspace/validate.py)."""
from pathlib import Path
import sys
import tempfile

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT/'scripts'))

import render_workspaces  # noqa: E402


@pytest.mark.parametrize('case', sorted(render_workspaces.cases()))
def test_generated_workspace_is_consistent(case):
    with tempfile.TemporaryDirectory(prefix='workspace-validate-') as scratch:
        workspace = render_workspaces.render_case(case, Path(scratch))
        problems = render_workspaces.problems(case, workspace)
        assert not problems, '\n'.join(problems)
