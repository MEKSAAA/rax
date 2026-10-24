"""Trusted, MCP-only execution backend for autonomous robot controllers.

Nothing in this package is exposed to the agent or its bundles; they reach it only
through MCP (auto_research_agent/api/runtime.py).
The existing interactive launchers are deliberately not changed by this backend.
"""
