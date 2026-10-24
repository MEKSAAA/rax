"""Every generated agent workspace matches its recorded golden index.

When a text or generator change is intended, review the rendered difference
(`scripts/render_workspaces.py --out` on both revisions, then `diff -r`), and
refresh the index with `scripts/render_workspaces.py --index local_tests/goldens/workspaces.json`.
"""
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT/'scripts'))

import render_workspaces  # noqa: E402


def test_workspaces_match_their_goldens():
    expected = json.loads((PROJECT/'local_tests/goldens/workspaces.json').read_text())
    rendered = render_workspaces.render_all()
    assert sorted(rendered) == sorted(expected)
    for name in expected:
        changed = sorted(path for path in set(expected[name]) | set(rendered[name])
                         if expected[name].get(path) != rendered[name].get(path))
        assert not changed, (name, changed)
