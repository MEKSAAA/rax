"""Trusted adapter to the configured benchmark's simulator backend, never exposed to the agent or its bundles."""
from __future__ import annotations


def make_backend(config):
    """The configured benchmark's trusted backend (services/benchmarks.py)."""
    from services.benchmarks import for_config
    return for_config(config).backend(config)
