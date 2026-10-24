"""Compatibility layer: workspace generation lives in services.workspace.

Only call compose() on a newly copied workspace, before giving it to an agent.
Skills come from agent_skills/: shared/, then auto-research/ (whose files add to
or replace shared ones). Conditional text is Jinja
in the sources (see services/workspace/templates.py).
"""
from services.mcp_contract import Contract
from services.workspace import OPTIONS, Setup, build, recipe_for
from services.workspace import describe as _describe
from services.workspace import install_skills as _install_skills
from services.workspace import render as _render
from services.workspace import write_contract as _write_contract
from services.workspace.recipe import SKILL_SOURCE


def render(text: str, contract: Contract, options=frozenset(), *, task=None, demonstrations=False,
           demonstration_kind=None, demonstration_images=0) -> str:
    return _render(text, Setup(contract, frozenset(options), task, demonstrations=demonstrations,
                               demonstration_kind=demonstration_kind, demonstration_images=demonstration_images))


def describe(contract: Contract, options=frozenset()) -> str:
    return _describe(contract, options)


def write_contract(workspace, contract, options=frozenset()):
    _write_contract(workspace, contract, options)


def install_skills(skills, source=SKILL_SOURCE):
    """Copy shared skills, then the auto-research skills over them."""
    from services.workspace.build import layers
    from services.workspace.recipes import AUTO_RESEARCH
    # The default setup: the default benchmark's robot with depth.
    _install_skills(skills, layers(AUTO_RESEARCH, Setup(Contract())), source)


def compose(workspace, contract, *, task, options=frozenset(), demonstrations=False):
    """Generate a workspace in place over an already copied template."""
    options = frozenset(options)
    build(recipe_for(options, contract.benchmark), Setup(contract, options, task, demonstrations=demonstrations),
          workspace, copy_template=False)
