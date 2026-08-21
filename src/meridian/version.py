"""Single source of truth for the harness version.

The version is written into every run manifest, so a bump invalidates nothing
retroactively but does make historical runs attributable to a specific harness.
"""

from __future__ import annotations

__version__ = "0.1.0"
