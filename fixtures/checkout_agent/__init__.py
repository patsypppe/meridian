"""The system under test.

Deliberately not part of the harness. Meridian never imports this package: it
ships it into the trial container and lets the in-container entrypoint resolve
the adapter spec there. That is what makes "framework-agnostic" structural rather
than aspirational.
"""
