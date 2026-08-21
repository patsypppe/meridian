"""Frozen, validated domain models.

Every definition model is `frozen=True, extra="forbid"`. Validation is a product
requirement (`MD-FR-01`), not a convenience: a task whose meaning drifts because
a typo was silently ignored is worse than a task that fails to load.
"""
