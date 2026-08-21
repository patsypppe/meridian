"""Adapters run **inside** the trial container, never in the harness process.

The harness never imports the system under test — it ships this package into the
container and lets the entrypoint resolve the adapter spec there. That is what
makes "framework-agnostic" a structural property rather than a claim: the harness
has no way to depend on a framework it cannot import.
"""
