"""Configuration: load it, validate it, hash it.

The resolved configuration is canonically hashed into the manifest, so two runs
that disagree about anything are provably distinguishable. Probabilities and
tolerances are decimal **strings** rather than floats for that reason — float
formatting is not stable across platforms, and a config hash that drifts makes
every historical manifest unverifiable.
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from meridian.hashing import content_hash

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ProxyMode(StrEnum):
    """How the model proxy behaves.

    An MVP-only key, recorded as a deliberate divergence from the PRD
    (`HANDOFF §3.2`): the PRD asks for a secret broker, and the proxy satisfies
    that requirement plus three others.
    """

    RECORD = "record"
    REPLAY = "replay"
    PASSTHROUGH = "passthrough"


class RunMode(StrEnum):
    """What the run is for. `GATE` is the mode whose numbers someone acts on."""

    EXPLORATORY = "exploratory"
    GATE = "gate"


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError(f"invalid_config: {field_name} {value!r} is not a decimal") from exc


class ExecutionConfig(BaseModel):
    model_config = _FROZEN

    n_trials: int = Field(default=5, ge=1, le=1000)
    k: int = Field(default=3, ge=1)
    max_concurrent_trials: int = Field(default=4, ge=1, le=64)
    proxy_mode: ProxyMode = ProxyMode.REPLAY
    unsafe_shared_env: bool = False
    budget_cents: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _k_fits_in_n(self) -> ExecutionConfig:
        if self.k > self.n_trials:
            raise ValueError(
                f"invalid_config: k={self.k} exceeds n_trials={self.n_trials}; pass^k is a "
                f"statement about k trials drawn from the n actually run"
            )
        return self


class GateConfig(BaseModel):
    model_config = _FROZEN

    tolerance: str = "0.02"
    require_significance: bool = True
    significance_level: str = "0.05"
    fail_on_inconclusive: bool = False
    max_harness_error_rate: str = "0.05"

    @field_validator("tolerance", "significance_level", "max_harness_error_rate")
    @classmethod
    def _in_unit_interval(cls, value: str, info: Any) -> str:
        parsed = _decimal(value, str(info.field_name))
        if not (Decimal(0) <= parsed <= Decimal(1)):
            raise ValueError(f"invalid_config: {info.field_name} {value!r} is not within [0, 1]")
        return value

    @property
    def tolerance_value(self) -> float:
        return float(Decimal(self.tolerance))

    @property
    def significance_value(self) -> float:
        return float(Decimal(self.significance_level))

    @property
    def max_harness_error_rate_value(self) -> float:
        return float(Decimal(self.max_harness_error_rate))


class StatsConfig(BaseModel):
    model_config = _FROZEN

    bootstrap_iterations: int = Field(default=10_000, ge=100, le=1_000_000)
    bootstrap_seed: int = 0


class StoreConfig(BaseModel):
    model_config = _FROZEN

    database_url: str | None = None


class MeridianConfig(BaseModel):
    model_config = _FROZEN

    suite_path: str = "./suites"
    execution: ExecutionConfig = ExecutionConfig()
    gate: GateConfig = GateConfig()
    stats: StatsConfig = StatsConfig()
    store: StoreConfig = StoreConfig()

    def config_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    def validate_for(self, mode: RunMode) -> None:
        """Reject configurations whose numbers must never be acted on.

        Both refusals below produce a *number* rather than an error if allowed
        through, and a wrong number that looks right is worse than a crash.
        """
        if mode is not RunMode.GATE:
            return
        if self.execution.proxy_mode is ProxyMode.PASSTHROUGH:
            raise ValueError(
                "invalid_config: proxy_mode 'passthrough' is not allowed in gate mode. "
                "Passthrough records nothing, so the run cannot be replayed and its "
                "verdict cannot be audited."
            )
        if self.execution.unsafe_shared_env:
            raise ValueError(
                "invalid_config: unsafe_shared_env is not allowed in gate mode. Trials "
                "sharing state produce correlated failures and inflated scores — the exact "
                "failure mode Meridian exists to rule out."
            )


def _coerce_env(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


ENV_KEYS: dict[str, tuple[str, ...]] = {
    "MERIDIAN_N_TRIALS": ("execution", "n_trials"),
    "MERIDIAN_K": ("execution", "k"),
    "MERIDIAN_MAX_CONCURRENT_TRIALS": ("execution", "max_concurrent_trials"),
    "MERIDIAN_PROXY_MODE": ("execution", "proxy_mode"),
    "MERIDIAN_BUDGET_CENTS": ("execution", "budget_cents"),
    "MERIDIAN_TOLERANCE": ("gate", "tolerance"),
    "MERIDIAN_DATABASE_URL": ("store", "database_url"),
}


def load_config(
    path: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> MeridianConfig:
    """Resolve configuration: defaults → file → environment → explicit overrides."""
    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if config_path.is_file():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if loaded:
                data = dict(loaded)

    environment = os.environ if env is None else env
    for key, (section, field_name) in ENV_KEYS.items():
        if key in environment:
            data.setdefault(section, {})
            data[section][field_name] = _coerce_env(environment[key])

    for section, values in (overrides or {}).items():
        merged = dict(data.get(section, {}))
        merged.update({k: v for k, v in values.items() if v is not None})
        data[section] = merged

    return MeridianConfig.model_validate(data)
