"""Agent documentation must be usable without internet; retain legal notices.

Links between workspace files are checked on every rendered workspace by the
workspace validator (test_workspace_validation).
"""
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parents[2]


def test_agent_authored_files_have_no_web_references():
    for root in (PROJECT/'auto_research_agent', PROJECT/'agent_skills', PROJECT/'agent_blocks'):
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix not in {'.md', '.py', '.txt', '.yaml'}:
                continue
            if 'licenses' in path.relative_to(root).parts or path == root/'README.md':
                continue  # Do not alter third-party license texts, including their URLs.
            # An XML namespace (an SVG the robot model draws) names a format, not a web page.
            text = re.sub(r'xmlns(:\w+)?="[^"]*"', '', path.read_text())
            assert not re.search(r'https?://|www\.', text), path
