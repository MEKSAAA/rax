"""A recipe says which sources make one kind of workspace (a role).

Recipes are values: a role is another recipe derived with dataclasses.replace, so
the exploration and formal-copy workspaces share everything they do not override.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT = Path(__file__).resolve().parents[2]
SKILL_SOURCE = PROJECT / 'agent_skills'


@dataclass(frozen=True)
class Vocab:
    """Named words a shared source uses instead of spelling out paths and tool names."""
    memory: str             # memory folder
    notes: str              # the agent's running notes file
    frames: str             # where published frames and manifests live
    start: str              # episode start/reset tool
    evaluate: str           # trusted evaluation tool
    finish: str             # close/abandon tool
    inventory: str          # environment inventory and budget
    episode_modes: str      # the episode kinds an evidence record may come from


@dataclass(frozen=True)
class SkillRule:
    """Leave a skill out when `when(setup)` holds."""
    skill: str
    when: Callable


@dataclass(frozen=True)
class Recipe:
    name: str
    template: Path                      # document sources, copied then rendered
    # agent_skills/<layer>/ dirs, later layers add or replace files; '{robot}' and
    # '{perception}' are filled from the setup (Setup.axes), and absent layers are skipped.
    skill_layers: tuple
    omit_skills: tuple = ()             # SkillRule values
    agents_source: str = 'AGENTS.md'    # this template file becomes the workspace AGENTS.md
    drop_documents: tuple = ()          # template files this role does not get
    role_documents: tuple = ()          # template files that belong to other roles only
    variant: str | None = None          # use <name>.<variant>.md skill files in place of <name>.md
    remove_files: tuple = ()            # skill-relative files this role does not get
    options: frozenset = field(default_factory=frozenset)  # workspace options the role implies
    vocab: Vocab | None = None
    render_dirs: tuple = ('.agents/skills',)    # besides top-level *.md
