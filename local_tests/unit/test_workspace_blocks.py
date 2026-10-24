"""Registered text blocks: unique ids, every block used, every use registered."""
from pathlib import Path
import re

import pytest

from services.mcp_contract import Contract
from services.workspace import Setup, render
from services.workspace.templates import registry

PROJECT = Path(__file__).resolve().parents[2]
SOURCES = ('auto_research_agent', 'agent_skills', 'agent_blocks')
USE = re.compile(r'block\("([a-z0-9.-]+)"')


def uses():
    found = {}
    for root in SOURCES:
        for path in (PROJECT/root).rglob('*.md'):
            for ident in USE.findall(path.read_text()):
                found.setdefault(ident, []).append(path.relative_to(PROJECT).as_posix())
    return found


def test_every_block_is_registered_and_used():
    registered, used = set(registry()), set(uses())
    assert used <= registered, used - registered
    assert registered <= used, registered - used


def test_blocks_render_and_are_recorded():
    seen = set()
    text = render('{{ block("session.contract-pointer") }}\n', Setup(Contract()), used=seen)
    assert text.startswith('All robot interaction') and '`MCP_SESSION.md`' in text and seen == {'session.contract-pointer'}
    with pytest.raises(ValueError, match='Unknown workspace block'):
        render('{{ block("no.such-block") }}\n', Setup(Contract()))
