# CLAUDE.md — read this first, every session

You are working on **Meridian**, an agent evaluation harness. The contract for
this work is `docs/HANDOFF.md` (MD-HND-001). Read the work package you are on
before writing code.

## The four rules — non-negotiable

1. **Every trial starts from a clean, isolated environment.** No shared
   filesystem, database, network namespace, or process between trials. Enforced
   by the contamination probe in `suites/checkout-agent/tasks/`, which must pass
   with isolation on and **fail** with `--unsafe-shared-env`.
2. **Grade outcomes, not trajectories.** Assertions run against the final state
   of the environment. Tool calls, turns, and tokens are recorded as secondary
   efficiency signals and never determine pass or fail.
3. **Report pass^k, not a mean score.** `pass^k = C(c,k)/C(n,k)`. Never
   `(c/n)**k`.
4. **Every run is reproducible from its manifest.** Digests, not tags. Integers,
   not floats, inside anything hashed.

If a design decision conflicts with one of these, the design decision loses.

## Layout rules

- Pure logic — `stats/`, `gate/decide.py`, `manifest/build.py`,
  `grading/graders/` — has **no I/O and no Docker imports**. These are unit
  tested exhaustively and must stay testable without a daemon.
- All container security options live in exactly one file:
  `src/meridian/runtime/isolation.py`. A reviewer audits isolation by reading
  one file.
- The harness process **never imports the system under test**, and never imports
  `langgraph`. Only `adapters/langgraph_adapter.py` does, and it runs inside the
  trial container.

## Things that are wrong even when they look right

- Retrying an agent failure. Only `harness_error` retries, exactly once.
  A `fail` or `timeout` is never retried — retrying is how a harness reports
  numbers better than reality.
- A grader exception classified as a `fail`. It is a `harness_error`.
- `auto_remove=True` on a trial container. State must be extracted before removal.
- Cancelling an asyncio task to enforce a timeout. Kill the container.
- Bootstrap resampling trials. Resample **tasks** — trials within a task are
  correlated.
- Floats in a hashed structure. Integer cents, integer milliseconds, decimal
  strings for probabilities.
- A gate that FAILs because no baseline exists. That verdict is PASS.

## Exit-code contract

`meridian gate` is the **only** command that returns non-zero for a product
verdict (`1` = FAIL). Every other command reserves non-zero exclusively for
harness errors. CI must never confuse "the agent got worse" with "the tool
crashed".

## Commands

```bash
make check         # ruff + mypy --strict + unit  — must be green before any commit
make integration   # Docker-backed tests
make sweep         # remove leaked trial containers
```

## Out of scope for the MVP — do not build

LLM-as-judge (needs a calibration workflow and 30 human labels first), the
two-axis result cache (nothing expensive to memoize with deterministic graders —
ship the key derivation only), error-analysis workspace, drift detection,
multi-tenancy enforcement, a web UI beyond a static HTML report, and any adapter
beyond LangGraph plus the trivial subprocess one.
