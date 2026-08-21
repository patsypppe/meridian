"""Token and cost ceilings, enforced where the spend happens.

The ledger lives in the proxy rather than in the agent because a limit the agent
enforces on itself is not a limit. The trial container never sees these numbers
and cannot raise them.

Costs are integers throughout — micro-cents internally, whole cents at the
boundary. Money in a float is a rounding argument waiting to happen, and any
value that reaches a hashed structure must not be a float at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MICROCENTS_PER_CENT = 1_000_000

# Micro-cents per million tokens, input and output. Approximate and explicitly
# not a billing system: the number exists so a runaway agent stops, and so a run
# can report roughly what it cost.
PRICE_TABLE_MICROCENTS_PER_MTOK: dict[str, tuple[int, int]] = {
    "claude-sonnet-5": (300_000_000, 1_500_000_000),
    "claude-opus-5": (1_500_000_000, 7_500_000_000),
    "claude-haiku-4-5-20251001": (100_000_000, 500_000_000),
}
DEFAULT_PRICE_MICROCENTS_PER_MTOK = (300_000_000, 1_500_000_000)


def cost_microcents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Integer micro-cents for one call."""
    per_in, per_out = PRICE_TABLE_MICROCENTS_PER_MTOK.get(model, DEFAULT_PRICE_MICROCENTS_PER_MTOK)
    return (input_tokens * per_in + output_tokens * per_out) // 1_000_000


class BudgetExceeded(Exception):
    """A ceiling was crossed.

    Surfaced to the agent as a 429 with a structured body. The agent sees a
    failed call, so the trial is a `fail` — the agent did not achieve the
    required state within its budget — and never a `harness_error`.
    """

    def __init__(self, scope: str, limit: str, used: int, allowed: int) -> None:
        super().__init__(
            f"{scope} exceeded its {limit} budget: {used} used against a limit of {allowed}"
        )
        self.scope = scope
        self.limit = limit
        self.used = used
        self.allowed = allowed

    def as_payload(self) -> dict[str, object]:
        return {
            "type": "error",
            "error": {
                "type": "budget_exceeded",
                "scope": self.scope,
                "limit": self.limit,
                "used": self.used,
                "allowed": self.allowed,
                "message": str(self),
            },
        }


@dataclass
class TrialLimits:
    """Per-trial ceilings, taken from the task definition, not from the agent."""

    max_tokens: int
    budget_cents: int


@dataclass
class TrialUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    microcents: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class BudgetLedger:
    """Usage for a whole run, keyed by trial."""

    limits: dict[str, TrialLimits] = field(default_factory=dict)
    run_budget_cents: int | None = None
    usage: dict[str, TrialUsage] = field(default_factory=dict)
    # Which models were actually called. Recorded because the manifest must pin
    # the model, and the harness never chooses it -- the agent does.
    models: set[str] = field(default_factory=set)

    def _key(self, task_slug: str, trial_index: int) -> str:
        return f"{task_slug}:{trial_index}"

    def usage_for(self, task_slug: str, trial_index: int) -> TrialUsage:
        return self.usage.setdefault(self._key(task_slug, trial_index), TrialUsage())

    @property
    def run_microcents(self) -> int:
        return sum(entry.microcents for entry in self.usage.values())

    @property
    def run_cents(self) -> int:
        # Rounded up: reporting a cost lower than what was actually spent is the
        # one direction that matters.
        return -(-self.run_microcents // MICROCENTS_PER_CENT)

    def check_before(self, task_slug: str, trial_index: int) -> None:
        """Refuse a call that a already-exhausted trial or run is trying to make."""
        limits = self.limits.get(task_slug)
        usage = self.usage_for(task_slug, trial_index)
        if limits is not None:
            if usage.total_tokens >= limits.max_tokens:
                raise BudgetExceeded(
                    f"{task_slug} trial {trial_index}",
                    "max_tokens",
                    usage.total_tokens,
                    limits.max_tokens,
                )
            spent_cents = -(-usage.microcents // MICROCENTS_PER_CENT)
            if spent_cents >= limits.budget_cents:
                raise BudgetExceeded(
                    f"{task_slug} trial {trial_index}",
                    "budget_cents",
                    spent_cents,
                    limits.budget_cents,
                )
        if self.run_budget_cents is not None and self.run_cents >= self.run_budget_cents:
            raise BudgetExceeded("run", "budget_cents", self.run_cents, self.run_budget_cents)

    def charge(
        self,
        task_slug: str,
        trial_index: int,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TrialUsage:
        usage = self.usage_for(task_slug, trial_index)
        self.models.add(model)
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.microcents += cost_microcents(model, input_tokens, output_tokens)
        return usage

    def snapshot(self) -> dict[str, object]:
        return {
            "models": sorted(self.models),
            "run_cents": self.run_cents,
            "run_microcents": self.run_microcents,
            "run_budget_cents": self.run_budget_cents,
            "trials": {
                key: {
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "microcents": entry.microcents,
                }
                for key, entry in sorted(self.usage.items())
            },
        }

    def run_budget_exhausted(self) -> bool:
        return self.run_budget_cents is not None and self.run_cents >= self.run_budget_cents
