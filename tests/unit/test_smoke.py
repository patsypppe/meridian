"""Smoke test — proves the tooling chain is wired before any real code exists."""

from __future__ import annotations

import pytest

from meridian import __version__


@pytest.mark.unit
def test_version_is_exported() -> None:
    assert __version__ == "0.1.0"
