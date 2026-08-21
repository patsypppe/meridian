"""Meridian — an agent evaluation harness.

Four rules govern everything in this package:

1. Every trial starts from a clean, isolated environment.
2. Grade outcomes, not trajectories.
3. Report pass^k, not a mean score.
4. Every run is reproducible from its manifest.
"""

from __future__ import annotations

from meridian.version import __version__

__all__ = ["__version__"]
