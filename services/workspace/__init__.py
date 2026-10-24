"""Agent workspace generation: recipes (per role) + setup facts -> files."""
from .build import build, install_skills
from .contract_files import describe, write_contract
from .recipes import recipe_for
from .setup import OPTIONS, Setup
from .templates import render

__all__ = ['OPTIONS', 'Setup', 'build', 'describe', 'install_skills', 'recipe_for', 'render', 'write_contract']
