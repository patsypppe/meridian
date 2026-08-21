# Meridian — Implementation Handoff

**Document ID:** MD-HND-001 · **Version:** 1.0
**Owner:** Pranav T P · **Date:** August 21, 2026
**Implements:** MD-PRD-001 §3.2 (MVP tier), Appendix C · **Gate:** AXIS G0, August 21 – September 4, 2026
**Audience:** the engineer and the coding agent building this, jointly.

---

## 0. How to use this document

This is the contract between you and the coding agent. It is written to be handed to Claude Code at the start of a session, in whole or in part.

**Working method that fits this document:**

1. Create the repo, drop `CLAUDE.md` at the root and this file at `docs/HANDOFF.md`, and copy `MD-PRD-001` in as `docs/PRD.md`.
2. Work one **work package** at a time (§10). Each is sized for one to two focused sessions and has explicit acceptance tests.
3. Start each session with the prompt for that work package from `PROMPTS.md`. Do not ask the agent to "build Meridian" — it will produce a plausible skeleton with no working isolation, which is the one thing that matters.
4. After each work package, run the acceptance commands yourself before moving on. A package is not done because the agent says so; it is done because the named test passes.

**If you read only one section, read §2.** Those four rules are the product. Everything else is implementation detail that can be renegotiated.

---

## 1. What you are building, in one paragraph

Meridian answers a question teams currently cannot answer: *did this change make my agent better or worse, and can I reproduce that answer tomorrow?* It runs a suite of tasks against an agent, each trial in a fresh isolated container, grades the **final state of the environment** rather than the agent's transcript, repeats each task *n* times because agents are non-deterministic, reports **pass^k** (all *k* trials succeed) alongside pass@k (at least one of *k* succeeds), and fails a pull request when pass^k regresses beyond tolerance. Every run emits a manifest that pins every input, so any historical result can be replayed exactly.

The MVP is not a platform. It is one command — `meridian gate` — that a CI job can trust.

---

## 2. The four rules

These are non-negotiable. If a design decision conflicts with one of these, the design decision loses.

### Rule 1 — Every trial starts from a clean, isolated environment

Trials share nothing: no filesystem, no database, no network namespace, no process. Shared state produces correlated failures and inflated scores, which is the specific failure mode that makes most in-house eval scripts untrustworthy ([Anthropic, *Demystifying evals for AI agents*](https://anthropic.com/engineering/demystifying-evals-for-ai-agents)).

This is enforced, not asserted: the repository ships a **contamination probe** (§11.3) — a pair of tasks where the second asserts that a marker written by the first does not exist. The probe must pass with isolation on and **fail** when isolation is deliberately disabled, because a test that cannot fail proves nothing.

### Rule 2 — Grade outcomes, not trajectories

Assertions run against the final state of the environment: files, database rows, exit codes, test results. The agent is free to reach that state any way it likes. Step-by-step trajectory grading is banned as a primary signal because agents find valid alternative paths and a trajectory grader punishes them for it. Transcript-derived numbers — tool calls, turns, tokens, wall clock — are recorded as **secondary efficiency signals** and never determine pass or fail.

### Rule 3 — Report pass^k, not a mean score

For a task run *n* times with *c* successes:

```
pass@k  = 1 − C(n−c, k) / C(n, k)     probability at least one of k sampled trials passes
pass^k  = C(c, k)     / C(n, k)       probability all k sampled trials pass
```

A mean score hides the distribution that matters. An agent at 80% mean looks fine and has a pass^5 near 0.33 — meaning a user running the same task five times sees a failure most of the time. **pass^k is the number a business can plan against.** The gate is built on it.

### Rule 4 — Every run is reproducible from its manifest

A run manifest pins: suite version and content hash, every task definition hash, every container image **digest** (never a tag), the adapter spec and the system-under-test commit SHA, model identifiers, prompt hashes, per-trial seeds, the harness version, and the resolved configuration hash. `meridian replay <run-id>` re-materializes those inputs and must reproduce the recorded pass^k exactly for the fixture agent, which uses a recorded model transcript (§9.4).

**Never cut, under any schedule pressure:** per-trial isolation, the pass^k statistic, the manifest. A version of this that shares an environment across trials and reports a mean score is the thing every team already has and does not trust.

---

## 3. Scope

### 3.1 In scope — build these

| Capability | Name | Functional requirements |
|---|---|---|
| `MD-CAP-01` | Task definition format | `MD-FR-01` |
| `MD-CAP-02` | Task provenance | `MD-FR-02` |
| `MD-CAP-07` | Snapshot management | `MD-FR-08` |
| `MD-CAP-08` | Per-trial isolation runtime | `MD-FR-09` |
| `MD-CAP-09` | Framework-agnostic adapter (LangGraph reference only) | `MD-FR-10`, `MD-FR-11` |
| `MD-CAP-11` | Trial lifecycle control | `MD-FR-13`, `MD-FR-14` |
| `MD-CAP-12` | Run manifest and replay | `MD-FR-15`, `MD-FR-16` |
| `MD-CAP-13` | Deterministic outcome graders | `MD-FR-17` |
| `MD-CAP-18` | Statistics engine | `MD-FR-23`, `MD-FR-24` |
| `MD-CAP-24` | CI gate | `MD-FR-32`, `MD-FR-33` |
| `MD-CAP-30` | Secret injection and configuration | `MD-FR-39` |

### 3.2 Out of scope — do not build these

| Excluded | Why, and when it returns |
|---|---|
| **LLM-as-judge and calibration** (`MD-CAP-14`, `MD-CAP-15`) | The three fixture tasks grade deterministically with zero model calls in the grading path. Judges without a calibration workflow are worse than no judges. Returns in v1, gated on a labeled set of ≥30 examples. |
| **Two-axis result cache** (`MD-CAP-19`) | The cache exists to memoize *judge* calls. With deterministic graders there is nothing expensive to memoize, so building it now optimizes a path with no cost on it. Ship the cache-key derivation as a pure function with tests (§10, WP-11 stretch) so it is ready, and wire it when judges land. |
| Error-analysis workspace, review queue (`MD-CAP-21`, `MD-CAP-23`) | Depends on trace import from Lumen, which does not exist until AXIS G2. |
| Online scoring, drift detection (`MD-CAP-26`) | Same dependency. |
| Multi-tenancy, RBAC, SSO (`MD-CAP-29`) | Single tenant, single user. Keep `tenant_id` columns in the schema so the migration is additive, but do not build enforcement. |
| Web UI beyond a static HTML report | The CLI table and the PR comment carry the demo. |
| Adapters other than LangGraph | The adapter *interface* must be framework-agnostic and have two implementations — LangGraph and a trivial `subprocess` adapter — so the abstraction is proven. A third adds nothing. |

**Two deliberate divergences from MD-PRD-001, recorded so nobody discovers them by surprise.**

1. `MD-CAP-14` (LLM-as-judge) is marked **MVP** in the PRD capability catalog but excluded from the two-week slice by the PRD's own Appendix C. This handoff follows Appendix C. The reasoning is in the table above; if it is ever revisited, the catalog tier is the thing to change, not this document.
2. The **model proxy** (§9.4) is an implementation mechanism this handoff introduces; the PRD specifies a "secret broker" component (§5.1) and requires secret injection (`MD-CAP-30`) but does not mandate a proxy. The proxy satisfies that requirement and three others at once. The PRD configuration reference (§7.4) therefore gains one MVP-only key, `execution.proxy_mode`, with values `record | replay | passthrough`. Fold it back into the PRD when convenient.

### 3.3 The one thing that must exist at the end

A pull request, on a real repository, where the CI check fails with a comment naming which tasks flipped — and passes when the change is reverted. Everything else is supporting evidence for that screenshot.

---

## 4. Prerequisites

Target environment is macOS on Apple Silicon; everything also runs on Linux. The agent should assume nothing is installed and verify.

| Requirement | Version | Check | Notes |
|---|---|---|---|
| Python | 3.12.x | `python3 --version` | 3.13 is fine; pin `>=3.12,<3.14` |
| uv | ≥ 0.5 | `uv --version` | Package manager and venv. Faster than pip and produces a lockfile. |
| Docker Desktop | ≥ 4.30 | `docker version` | Must expose the Unix socket at `/var/run/docker.sock` |
| Postgres | 16 or 17 | via Docker Compose | Do not install natively; use the compose service |
| Git | any recent | `git --version` | |
| GitHub CLI | ≥ 2.50 | `gh --version` | For the Action demo |

**Model access.** The fixture agent needs one model API key for the *recording* pass only. After recording, replay runs offline from the cassette, which means CI never needs a key and the demo works on a plane. Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in `.env` for recording; `.env` is gitignored and never enters a container.

**Apple Silicon note.** Build environment images for `linux/arm64` locally. If CI runs on `ubuntu-latest` (amd64), the digest differs by architecture — pin per-architecture digests in the task definition or build multi-arch with `docker buildx`. This will bite; §16 covers it.

---

## 5. Repository layout

```
meridian/
├── CLAUDE.md                      # agent instructions — read first, every session
├── README.md                      # leads with the self-eval table
├── pyproject.toml
├── uv.lock
├── docker-compose.yml             # postgres + model proxy for local dev
├── .env.example
├── .github/
│   └── workflows/
│       ├── ci.yml                 # lint, typecheck, unit, integration
│       └── gate.yml               # the meridian gate demo on the fixture agent
├── docs/
│   ├── HANDOFF.md                 # this document
│   ├── PRD.md                     # MD-PRD-001
│   └── runbook.md                 # operator runbook, one entry per failure mode
├── src/meridian/
│   ├── __init__.py
│   ├── version.py                 # __version__, used in the manifest
│   ├── cli.py                     # Typer app — the only user-facing entry point
│   ├── config.py                  # meridian.yaml loading, validation, hashing
│   ├── models/
│   │   ├── task.py                # TaskDefinition, OutcomeAssertion, Provenance
│   │   ├── suite.py               # Suite, SuiteVersion
│   │   ├── run.py                 # RunRequest, RunResult, TrialResult, Manifest
│   │   └── adapter.py             # AgentAdapter protocol, TrialContext, AdapterResult
│   ├── suites/
│   │   ├── loader.py              # parse + validate a suite directory
│   │   └── validate.py            # the rules that reject a bad suite
│   ├── snapshots/
│   │   ├── build.py               # docker build → resolve digest
│   │   └── registry.py            # digest resolution and pinning checks
│   ├── runtime/
│   │   ├── trial_runner.py        # container lifecycle — the heart of Rule 1
│   │   ├── isolation.py           # container security options, one place
│   │   ├── proxy/
│   │   │   ├── server.py          # recording/replaying model proxy
│   │   │   ├── cassette.py        # request/response recording format
│   │   │   └── budget.py          # per-trial token and cost ceiling
│   │   ├── secrets.py             # short-lived injection, never into the container
│   │   └── scheduler.py           # bounded concurrency, per-trial dispatch
│   ├── adapters/
│   │   ├── base.py                # spec-string resolution "kind:module:attr"
│   │   ├── langgraph_adapter.py
│   │   └── subprocess_adapter.py  # proves the interface is not LangGraph-shaped
│   ├── grading/
│   │   ├── pipeline.py            # runs assertions against final state
│   │   └── graders/
│   │       ├── filesystem.py      # file_exists, file_matches, json_path_equals
│   │       ├── sqlite.py          # sqlite_query_equals
│   │       └── process.py         # exit_code, pytest, stdout_matches
│   ├── stats/
│   │   ├── passk.py               # pass@k, pass^k — pure functions, no I/O
│   │   ├── bootstrap.py           # cluster bootstrap over tasks
│   │   └── significance.py        # paired one-sided test
│   ├── manifest/
│   │   ├── build.py               # assemble + canonical-hash a manifest
│   │   └── replay.py              # re-materialize and compare
│   ├── gate/
│   │   ├── decide.py              # the decision rule — pure function
│   │   └── comment.py             # PR comment rendering
│   ├── store/
│   │   ├── migrations/            # alembic
│   │   ├── schema.sql             # canonical DDL, kept in sync with alembic
│   │   └── repo.py                # data access, no ORM models leaking upward
│   ├── report/
│   │   ├── table.py               # the CLI table
│   │   └── html.py                # static HTML run report
│   └── telemetry.py               # OpenTelemetry setup, one place
├── suites/
│   └── checkout-agent/
│       ├── suite.yaml
│       └── tasks/
│           ├── happy-path.yaml
│           ├── expired-coupon.yaml
│           ├── missing-field.yaml
│           ├── contamination-writer.yaml
│           └── contamination-probe.yaml
├── envs/
│   └── checkout/
│       ├── Dockerfile             # the environment snapshot
│       └── seed/                  # initial state materialized into the container
├── fixtures/
│   └── checkout-agent/            # the system under test
│       ├── agent.py               # LangGraph graph, deliberately breakable
│       ├── prompts/
│       │   ├── planner.md
│       │   └── planner.degraded.md   # the seeded regression
│       └── cassettes/             # recorded model interactions
├── benchmarks/
│   └── self-eval.json             # regenerated by CI, committed
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

**Layout rules the agent must respect.** Pure logic (`stats/`, `gate/decide.py`, `manifest/build.py`, `grading/graders/`) has **no I/O and no Docker imports** — these are the functions that get unit-tested exhaustively and they must stay testable without a daemon. All container security options live in exactly one place, `runtime/isolation.py`, so a reviewer can audit isolation by reading one file.

---

## 6. Technology choices

| Layer | Choice | Why this, and what would change it |
|---|---|---|
| Language | Python 3.12 | The agent frameworks and the graders both live here. Go would be better for the proxy; not worth two languages in an MVP. |
| Packaging | `uv` + `pyproject.toml` | Lockfile determinism matters because the harness version enters the manifest. |
| CLI | Typer | Type hints become the interface. Click is equivalent; do not use argparse. |
| Models | Pydantic v2 | Validation is a product requirement (`MD-FR-01`), not a convenience. `model_config = ConfigDict(frozen=True, extra="forbid")` on every definition model. |
| Containers | `docker` Python SDK 7.x | Never shell out to the `docker` binary — you lose structured errors and reliable cleanup. |
| Database | Postgres 17 via Compose, `psycopg` 3 | Direct SQL through a thin repository layer. No ORM: the schema is the spec and an ORM hides it. |
| Migrations | Alembic | `store/schema.sql` is the readable canonical copy; a test asserts they agree. |
| Analytics | DuckDB | Only where a query over run history is genuinely analytical. Do not reach for it early. |
| Statistics | stdlib `math.comb` + `numpy` | `pass^k` is exact combinatorics, not simulation. numpy for the bootstrap only. **No scipy.** |
| HTTP | `httpx` | Proxy and adapter clients. |
| Proxy | Starlette + `uvicorn` | Small ASGI app; it must be boring. |
| Logging | `structlog` → JSON | Every log line carries `run_id`, `task_slug`, `trial_index`. |
| Tracing | `opentelemetry-sdk` | Emit `invoke_agent` / `execute_tool` spans per `MD-PRD-001` §10.1. Note in code comments that `gen_ai.*` attributes are `Development` status and will churn. |
| Lint / format | `ruff` (lint + format) | One tool. |
| Types | `mypy --strict` on `src/meridian` | Strict from commit one; retrofitting strict typing is miserable. |
| Tests | `pytest`, `pytest-asyncio`, `testcontainers` | testcontainers for the Postgres integration tests. |

Pin exact versions in `uv.lock` and commit it. The lock hash is part of the run manifest.

---

## 7. Domain model

### 7.1 Task definition

The task file is the primary user-authored artifact. Everything about it is validated on load.

```yaml
# suites/checkout-agent/tasks/expired-coupon.yaml
slug: expired-coupon
description: >
  An order arrives with a coupon that expired yesterday. The agent must decline
  the coupon, charge full price, and record the reason on the invoice.

provenance:                                   # MD-FR-02 — required
  trace_id: "0af7651916cd43dd8448eb211c80319c"
  session_id: "sess_2026-08-14T09:22Z"
  failure_code: "coupon-expiry-ignored"
  # OR, if no real trace motivated it:
  # synthetic_reason: "boundary case: expiry exactly at T-1 day"

environment:
  snapshot: "sha256:9f2c1e..."               # MD-FR-08 — digest, never a tag
  workdir: /work
  network: none                              # none | proxy-only
  resources:
    cpus: 2.0
    memory_mb: 2048
    pids: 256

input:
  prompt_file: prompts/expired-coupon.txt
  files:
    - src: seed/order-1042.json
      dest: /work/orders/order-1042.json

limits:
  timeout_seconds: 300
  max_tokens: 60000
  budget_cents: 25

outcome_assertions:                          # MD-FR-17 — at least one required
  - kind: json_path_equals
    path: /work/out/invoice-1042.json
    json_path: "$.total_cents"
    expected: 4999
  - kind: json_path_equals
    path: /work/out/invoice-1042.json
    json_path: "$.coupon.applied"
    expected: false
  - kind: json_path_matches
    path: /work/out/invoice-1042.json
    json_path: "$.coupon.decline_reason"
    pattern: "(?i)expired"
  - kind: sqlite_query_equals
    database: /work/store.db
    query: "SELECT status FROM orders WHERE id = 1042"
    expected: [["invoiced"]]

efficiency_expectations:                     # recorded, never pass/fail
  max_tool_calls: 12
  max_turns: 8

tags: [pricing, coupons, boundary]
```

**Validation rules — each is a test.**

| Rule | Error |
|---|---|
| `outcome_assertions` non-empty | `SuiteValidationError: task 'X' has no outcome assertion` |
| `environment.snapshot` matches `^sha256:[0-9a-f]{64}$` | `unpinned snapshot` |
| `provenance` has `trace_id` **or** `synthetic_reason` | `provenance required` |
| `slug` unique within the suite version, `^[a-z0-9-]{3,48}$` | `invalid or duplicate slug` |
| Every referenced file exists relative to the suite directory | `missing input file` |
| `limits.timeout_seconds` between 10 and 3600 | `timeout out of range` |
| Unknown keys anywhere | `extra fields not permitted` (Pydantic `extra="forbid"`) |

### 7.2 Adapter interface

The abstraction that keeps Meridian framework-agnostic. It is deliberately narrow.

```python
# src/meridian/models/adapter.py
from typing import Protocol, Sequence
from pydantic import BaseModel, ConfigDict

class TrialContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    task_slug: str
    trial_index: int
    seed: int
    workdir: str                  # path inside the container
    prompt: str
    model_base_url: str           # points at the Meridian proxy, never a provider
    max_tokens: int
    deadline_unix_ms: int

class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    started_unix_ms: int
    duration_ms: int
    ok: bool

class AdapterResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    completed: bool               # the agent finished its own loop
    turns: int
    tool_calls: Sequence[ToolCall]
    input_tokens: int
    output_tokens: int
    transcript_path: str          # written inside the container
    error: str | None = None      # agent-level error, NOT a harness error

class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    supports_streaming: bool
    supports_tools: bool

class AgentAdapter(Protocol):
    def capabilities(self) -> AdapterCapabilities: ...
    async def invoke(self, ctx: TrialContext) -> AdapterResult: ...
    async def teardown(self) -> None: ...
```

Adapters are addressed by a spec string: `langgraph:fixtures.checkout_agent.agent:graph`, parsed as `kind:module:attribute`. The adapter runs **inside** the trial container, invoked by an entrypoint the runner installs. The harness process never imports the system under test.

### 7.3 Run manifest

```python
class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    meridian_version: str
    created_unix_ms: int
    commit_sha: str | None
    suite_slug: str
    suite_version: int
    suite_content_hash: str
    config_hash: str
    adapter_spec: str
    sut_commit_sha: str | None
    n_trials: int
    k: int
    tasks: list["ManifestTask"]      # slug, definition_hash, snapshot_digest
    trials: list["ManifestTrial"]    # task_slug, trial_index, seed, cassette_hash
    models: list["ManifestModel"]    # model_id, provider, prompt_hashes
    env: dict[str, str]              # python, docker, platform, lock hash
```

`manifest_hash = sha256(canonical_json(manifest))` where canonical JSON means sorted keys, `separators=(",", ":")`, UTF-8, no floats (encode all numbers as integers or decimal strings). **This function must be pure and unit-tested against a golden fixture** — if it drifts, every historical manifest hash becomes unverifiable.

### 7.4 Database schema (MVP subset)

Take these tables verbatim from `MD-PRD-001` §6.2 and implement only these seven for the MVP: `tenant`, `suite_version`, `task`, `task_provenance`, `run`, `trial`, `score`. Keep `tenant_id` columns and insert a single fixed tenant row; do not build tenancy enforcement.

Two constraints carry product meaning and must exist in the database rather than in application code:

```sql
CONSTRAINT task_snapshot_pinned CHECK (snapshot_digest LIKE 'sha256:%'),
CONSTRAINT task_has_outcome     CHECK (jsonb_array_length(definition->'outcome_assertions') > 0)
```

Defer `judge_version`, `calibration_record`, `cache_judge_score`, `cache_reference`, and `audit_log` until their capabilities are in scope. Write the migration for them anyway if it is cheap — an empty table costs nothing and keeps the schema honest to the PRD.

---

## 8. Algorithms, specified precisely

These are the parts an agent will confidently get wrong. Each is a pure function with a mandatory unit test against the worked example given here.

### 8.1 pass@k and pass^k

Both are **combinatorial estimators over sampling without replacement** from the *n* trials actually run. They are not `(c/n)**k`.

```python
# src/meridian/stats/passk.py
from math import comb

def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k trials drawn without replacement passes."""
    if not (0 <= c <= n) or not (1 <= k <= n):
        raise ValueError(f"invalid n={n} c={c} k={k}")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)

def pass_hat_k(n: int, c: int, k: int) -> float:
    """Probability that ALL k trials drawn without replacement pass."""
    if not (0 <= c <= n) or not (1 <= k <= n):
        raise ValueError(f"invalid n={n} c={c} k={k}")
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)
```

**Mandatory test table.** These exact rows go in `tests/unit/test_passk.py`.

| n | c | k | pass@k | pass^k | Why |
|---|---|---|---|---|---|
| 5 | 5 | 3 | 1.0 | 1.0 | perfect |
| 5 | 0 | 3 | 0.0 | 0.0 | total failure |
| 5 | 4 | 1 | 0.8 | 0.8 | k=1 collapses both to c/n |
| 5 | 4 | 3 | 1.0 | 0.4 | `C(4,3)/C(5,3) = 4/10` — **the headline number** |
| 5 | 3 | 3 | 1.0 | 0.1 | `C(3,3)/C(5,3) = 1/10` |
| 5 | 2 | 3 | 0.9 | 0.0 | c<k |
| 10 | 8 | 5 | 1.0 | 0.2222… | `C(8,5)/C(10,5) = 56/252` |

The row to internalize and to put in the README: **an agent passing 4 of 5 trials has pass^3 = 0.4.** Eighty percent looks acceptable; a 40% chance that three consecutive attempts all work does not.

**Suite aggregation.** Suite-level pass^k is the unweighted mean of per-task pass^k across tasks in `state = 'active'`. Quarantined and withdrawn tasks are excluded from the aggregate and listed separately in the report. Do not weight by trial count; every task counts once, or a task with more trials silently dominates.

### 8.2 Confidence intervals — cluster bootstrap over tasks

Trials within a task are correlated; trials across tasks are not. **Resample tasks, not trials.** Resampling trials produces intervals that are far too narrow and is the single most common statistical error in eval tooling.

```python
# src/meridian/stats/bootstrap.py
import numpy as np

def bootstrap_ci(
    per_task: list[tuple[int, int, int]],   # (n, c, k) per task
    stat_fn,                                 # pass_hat_k or pass_at_k
    iterations: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile interval for the suite-level statistic, resampling TASKS."""
    rng = np.random.default_rng(seed)
    values = np.array([stat_fn(n, c, k) for n, c, k in per_task])
    m = len(values)
    if m == 0:
        raise ValueError("no tasks")
    idx = rng.integers(0, m, size=(iterations, m))
    means = values[idx].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi
```

The bootstrap seed is recorded in the manifest so an interval is reproducible.

### 8.3 Paired significance test

The gate compares head against a baseline **over the same task set**, so the test must be paired. Use a one-sided paired bootstrap on the per-task difference. The null hypothesis is "head is not worse"; a small p-value means the regression is real rather than noise.

```python
# src/meridian/stats/significance.py
def paired_regression_p_value(
    baseline: dict[str, float],    # task_slug -> pass^k
    head: dict[str, float],
    iterations: int = 10_000,
    seed: int = 0,
) -> float:
    """One-sided p-value for the hypothesis that head regressed against baseline."""
    slugs = sorted(set(baseline) & set(head))
    if not slugs:
        raise ValueError("no overlapping tasks")
    d = np.array([head[s] - baseline[s] for s in slugs])   # negative = regression
    observed = d.mean()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(iterations, len(d)))
    resampled = d[idx].mean(axis=1)
    # fraction of resamples at least as good as observed, i.e. evidence against regression
    return float((resampled >= 0).mean()) if observed < 0 else 1.0
```

Tasks present in only one of the two runs are excluded from the test and **named explicitly in the report** — a silently shrinking task set is how a gate stops working without anyone noticing.

### 8.4 The gate decision rule

This is a pure function. It takes numbers and configuration and returns a verdict. It touches nothing else, and it is the most heavily tested function in the repository.

```python
# src/meridian/gate/decide.py
from enum import Enum
from pydantic import BaseModel

class Verdict(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNING = "pass_with_warning"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"

class GateInput(BaseModel):
    baseline_suite_passhat_k: float | None
    head_suite_passhat_k: float
    per_task_baseline: dict[str, float]
    per_task_head: dict[str, float]
    harness_error_rate: float
    run_status: str                       # 'complete' | 'halted_budget' | ...
    tolerance: float                      # default 0.02
    require_significance: bool            # default True
    significance_level: float             # default 0.05
    fail_on_inconclusive: bool            # default False
    max_harness_error_rate: float = 0.05

def decide(g: GateInput, p_value_fn) -> tuple[Verdict, str]:
    if g.run_status == "halted_budget":
        return (Verdict.FAIL if g.fail_on_inconclusive else Verdict.INCONCLUSIVE,
                "run halted at its cost cap before completing")
    if g.harness_error_rate > g.max_harness_error_rate:
        return (Verdict.FAIL if g.fail_on_inconclusive else Verdict.INCONCLUSIVE,
                f"harness error rate {g.harness_error_rate:.1%} exceeds threshold")
    if g.baseline_suite_passhat_k is None:
        return Verdict.PASS, "no baseline; recording head as the new baseline"

    drop = g.baseline_suite_passhat_k - g.head_suite_passhat_k
    if drop <= g.tolerance:
        return Verdict.PASS, f"pass^k moved {-drop:+.3f}, within tolerance {g.tolerance}"

    if g.require_significance:
        p = p_value_fn(g.per_task_baseline, g.per_task_head)
        if p > g.significance_level:
            return (Verdict.PASS_WITH_WARNING,
                    f"pass^k dropped {drop:.3f} but p={p:.3f} > {g.significance_level}")
        return Verdict.FAIL, f"pass^k dropped {drop:.3f}, p={p:.3f}"

    return Verdict.FAIL, f"pass^k dropped {drop:.3f} beyond tolerance {g.tolerance}"
```

**Exit code contract — do not violate this.** `meridian gate` is the *only* command that returns a non-zero exit code for a product verdict: `0` for PASS, PASS_WITH_WARNING, and INCONCLUSIVE (unless `fail_on_inconclusive`), `1` for FAIL. Every other command reserves non-zero exclusively for harness errors. CI must never confuse "the agent got worse" with "the tool crashed", because the response to each is completely different.

**Flipped-task reporting.** Independently of the verdict, the report always lists tasks where `pass^k` crossed 1.0 → <1.0 (broken) or <1.0 → 1.0 (fixed). This is the content of the PR comment and the thing a reviewer actually reads.

### 8.5 Canonical hashing

Used for manifest hashes, task definition hashes, suite content hashes, and config hashes. One implementation, one test.

```python
# src/meridian/manifest/build.py
import hashlib, json
from typing import Any

def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")

def content_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
```

Rules: no floats anywhere in a hashed structure — represent costs as integer cents, durations as integer milliseconds, and probabilities as decimal strings. Floats are not stable across platforms and will silently break hash equality. A golden-fixture test pins one manifest and its hash; if that test fails, someone changed the hashing and every historical manifest just became unverifiable.

### 8.6 Cache keys (specify now, wire later)

Even though the cache is out of MVP scope (§3.2), derive and test the key functions now so the shape is fixed before judges arrive.

```
reference_key   = sha256("ref:v1:"   + sample_id + ":" + generation_config_hash)
judge_score_key = sha256("judge:v1:" + sample_id + ":" + output_hash
                                     + ":" + judge_config_hash + ":" + metric)
```

The `v1:` prefix is a cache epoch — bumping it invalidates everything without a migration. `output_hash` is the hash of the graded artifact, not of the transcript; over half of model outputs across candidate versions are byte-identical, which is precisely why this cache works.

---

## 9. Component specifications

### 9.1 Trial runner — the heart of Rule 1

`src/meridian/runtime/trial_runner.py`. One function, `run_trial(task, trial_index, ctx) -> TrialResult`, with a strict lifecycle:

1. **Resolve** the snapshot digest. If the image is not present locally, pull by digest. Never resolve a tag at run time.
2. **Create** the container with the security options from `isolation.py` (§9.2). Do not start it yet.
3. **Materialize** input files by copying an archive into the container. Do not bind-mount the host filesystem — a bind mount is a shared-state channel and defeats Rule 1.
4. **Attach** the trial network: `network_mode="none"` if `environment.network == none`; otherwise attach only to the internal `meridian-trial-<run_id>` network whose sole other member is the proxy.
5. **Start** and invoke the entrypoint with the serialized `TrialContext` on stdin. The entrypoint resolves the adapter spec, runs it, writes the transcript, and exits.
6. **Wait** with a hard deadline. On timeout, `container.kill()` then `remove(force=True)`. Do not merely cancel the Python future — an abandoned container keeps running and consuming tokens.
7. **Extract** final state: copy out the assertion-relevant paths and the transcript as a tar archive. Do this *before* removing the container, obviously, and do it in the same `try` block.
8. **Destroy** the container in a `finally`, unconditionally, and record whether destruction succeeded. A leaked container is a harness error, not a silent condition.
9. **Grade** outside the container, against the extracted state.

**Outcome classification, and getting this wrong invalidates everything:**

| Situation | Outcome | Retried? |
|---|---|---|
| Assertions pass | `pass` | — |
| Assertions fail | `fail` | **Never** |
| Agent raised, returned `error`, or ran out of turns | `fail` | **Never** |
| Deadline exceeded | `timeout` | **Never** |
| Docker daemon unreachable, image pull failed, container create failed, extraction failed, proxy unreachable | `harness_error` | Once |
| Grader itself raised | `harness_error` | Once |

**An agent failure is never retried.** Retrying agent failures is how a harness reports a number that is better than reality, which is the exact thing this product exists to prevent. Encode this as a test: `test_agent_failure_is_not_retried`.

### 9.2 Isolation options — one file, auditable

`src/meridian/runtime/isolation.py` exports a single function returning the container kwargs. Every option is deliberate and commented with its reason.

```python
def container_kwargs(task, run_id: str, trial_index: int) -> dict:
    return dict(
        image=task.environment.snapshot,          # digest-pinned
        user="10001:10001",                       # never root
        read_only=True,                           # rootfs immutable
        tmpfs={"/tmp": "size=64m,mode=1777",
               task.environment.workdir: "size=512m,mode=0755"},
        network_mode="none",                      # overridden only for proxy-only
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        pids_limit=task.environment.resources.pids,
        mem_limit=f"{task.environment.resources.memory_mb}m",
        nano_cpus=int(task.environment.resources.cpus * 1e9),
        auto_remove=False,                        # we remove explicitly, so we can extract first
        labels={"meridian.run": run_id,
                "meridian.trial": str(trial_index)},
        environment={},                           # NO secrets, ever — see §9.4
    )
```

The `meridian.run` label exists so an orphan sweeper can find and remove leaked containers on startup. Write that sweeper; you will need it within a day of starting.

### 9.3 Scheduler

Bounded concurrency with a semaphore sized by `execution.max_concurrent_trials`. Trials are dispatched **task-major, trial-minor** — all *n* trials of task A before task B — so a partial run still yields complete statistics for the tasks that finished. Progress goes to stderr; results go to stdout. Never interleave them.

### 9.4 Model proxy — secrets, budget, and determinism in one component

This is the design decision worth defending in an interview: a single small component solves four problems at once.

- **Secrets never enter the container.** The trial container has no API key. It talks to `http://proxy:8080/v1` on an internal network; the proxy attaches the real credential. This satisfies `MD-CAP-30` and means a compromised agent cannot exfiltrate a key it never had.
- **Egress is controlled by construction.** The trial network is `internal: true` and contains exactly two members. The agent cannot reach the internet even if it tries, because there is no route — not because a filter said no.
- **Budget is enforced where the spend happens.** The proxy counts tokens per trial and returns a `429` with a structured error once `limits.max_tokens` or `limits.budget_cents` is exceeded. The agent sees a tool failure and the trial is classified `fail`, not `harness_error`.
- **Replay is exact.** In `record` mode the proxy writes each request/response pair to a cassette keyed by `sha256(canonical_json(request))`. In `replay` mode it serves from the cassette and **fails closed** on a cache miss. This is what makes Rule 4 a real guarantee rather than an aspiration: replay reproduces the recorded pass^k exactly because the model is no longer a source of variance.

Cassette entries record the request hash, the response body, token counts, and a timestamp. The cassette hash goes in the manifest per trial.

Modes: `record` (call upstream, write cassette), `replay` (cassette only, error on miss), `passthrough` (no cassette — used only for exploratory local runs, and **forbidden in gate mode**; the config validator must reject it).

### 9.5 Graders

Each grader is a pure function `(assertion, extracted_state_dir) -> GraderOutcome` where `GraderOutcome` is `pass | fail | error` plus a human-readable detail string. A grader that raises produces `error`, which becomes a `harness_error` trial — never a `fail`. Confusing "my assertion is broken" with "the agent is broken" is how a suite quietly becomes meaningless.

MVP grader set:

| Kind | Parameters | Notes |
|---|---|---|
| `file_exists` | `path`, `should_exist` | |
| `file_matches` | `path`, `pattern` | Regex, `re.DOTALL` off by default |
| `json_path_equals` | `path`, `json_path`, `expected` | Exact structural equality |
| `json_path_matches` | `path`, `json_path`, `pattern` | For free-text fields |
| `sqlite_query_equals` | `database`, `query`, `expected` | Rows as a list of lists; ordering significant unless the query has ORDER BY |
| `exit_code` | `expected` | From the adapter result |
| `pytest` | `path`, `selector` | Runs pytest inside a *fresh* grading container on the extracted state |

Failure detail must name the actual value, not just "assertion failed" — the detail string ends up in the PR comment and is the only thing a developer reads.

### 9.6 Replay

`meridian replay <run-id>`:

1. Load the manifest; verify `manifest_hash` still matches.
2. Verify every referenced image digest is available; abort with a clear error if not.
3. Re-run every trial with the recorded seeds, the proxy in `replay` mode against the recorded cassettes.
4. Compare the replayed per-task pass^k against the recorded values.
5. Report **replay fidelity**: the fraction of tasks matching exactly, and a diff for any that do not.

Replay is a first-class correctness check on the harness itself. Any drift means something outside the manifest is influencing results, and finding out what is the most valuable debugging session you will have.

---

## 10. Work packages

Twelve packages across fourteen days. Each is one to two focused sessions. **Do them in order** — the dependency chain is real, and WP-3 in particular blocks everything downstream.

Every package ends with commands you run yourself. If the command does not pass, the package is not done, regardless of what the session summary claims.

---

### WP-0 — Repository bootstrap · Aug 21

**Objective.** A repository where `make check` passes on an empty codebase, so every later package inherits working tooling instead of fighting it.

**Files.** `pyproject.toml`, `uv.lock`, `Makefile`, `.gitignore`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `src/meridian/__init__.py`, `src/meridian/version.py`, `tests/unit/test_smoke.py`, `CLAUDE.md`, `docs/HANDOFF.md`, `docs/PRD.md`.

**Key decisions.** `ruff` for lint and format; `mypy --strict` scoped to `src/meridian`; `pytest` markers `unit`, `integration`, `e2e` registered in `pyproject.toml` with `--strict-markers`. Compose service `postgres:17` on port 5433 to avoid colliding with anything local.

**Acceptance.**
```bash
uv sync && make check          # ruff + mypy --strict + pytest -m unit  → all green
docker compose up -d postgres && psql postgres://meridian@localhost:5433/meridian -c 'select 1'
```

**Pitfalls.** Do not let the agent add dependencies "for later" — `scipy`, `pandas`, `sqlalchemy-orm`, `fastapi` are all unnecessary in the MVP and each one becomes a load-bearing import within a day.

---

### WP-1 — Task schema, suite loader, validation · Aug 21–22 · `MD-CAP-01`, `MD-CAP-02` · `MD-FR-01`, `MD-FR-02`

**Objective.** A suite directory parses into frozen, validated models, and every invalid suite is rejected with a message naming the file, the task, and the rule.

**Files.** `models/task.py`, `models/suite.py`, `suites/loader.py`, `suites/validate.py`, `cli.py` (the `suite publish` subcommand only), `tests/unit/test_suite_validation.py`.

**Interfaces.** `load_suite(path: Path) -> Suite` raises `SuiteValidationError` with a list of structured errors — never a bare string, because the CLI renders them as a table.

**Acceptance.**
```bash
uv run meridian suite publish ./suites/checkout-agent          # exit 0, prints content hash
uv run pytest tests/unit/test_suite_validation.py -v           # every rule in §7.1 has a red-then-green test
```
Write one deliberately-invalid fixture suite per validation rule under `tests/fixtures/invalid-suites/` and assert the specific error code, not just that it raised.

**Definition of done.** All seven validation rules from §7.1 enforced; `suite_content_hash` is stable across runs and changes when any task changes; models are `frozen=True, extra="forbid"`.

**Pitfalls.** The agent will want to make `provenance` optional because it is annoying in tests. It is required — `MD-FR-02` — and `synthetic_reason` is the escape hatch. Tasks whose origin nobody remembers are how suites rot.

---

### WP-2 — Snapshot management · Aug 22 · `MD-CAP-07` · `MD-FR-08`

**Objective.** Environments are built from a Dockerfile and referenced everywhere by **digest**.

**Files.** `snapshots/build.py`, `snapshots/registry.py`, `envs/checkout/Dockerfile`, `envs/checkout/seed/`, `cli.py` (`snapshot build`).

**Interfaces.** `build_snapshot(path, tag) -> str` returns `sha256:...`. `resolve_digest(ref) -> str` raises if given a tag in a context requiring a digest.

**Acceptance.**
```bash
uv run meridian snapshot build ./envs/checkout --tag checkout:dev   # prints sha256:...
uv run pytest tests/integration/test_snapshot.py -m integration
```
The integration test builds twice and asserts the digest is identical, then asserts `load_suite` rejects a task whose `environment.snapshot` is a tag.

**Pitfalls.** Apple Silicon builds `linux/arm64`; GitHub runners are `linux/amd64`. Either build multi-arch with `docker buildx build --platform linux/amd64,linux/arm64 --push` to a registry, or — simpler for the MVP — build the image in CI as a job step and let the task pin a digest resolved at that moment via a `snapshot_ref` indirection file. Decide this in WP-2, not in WP-9 when CI is failing and you are out of time.

---

### WP-3 — Trial isolation runtime · Aug 23–24 · `MD-CAP-08`, `MD-CAP-11` · `MD-FR-09`, `MD-FR-13`, `MD-FR-14`

**This is the most important package in the project.** Budget two full sessions and do not move on until the contamination probe demonstrably fails with isolation off.

**Objective.** `run_trial` creates a locked-down container per trial, materializes inputs, runs a command, extracts final state, and destroys the container — with correct outcome classification and retry policy.

**Files.** `runtime/isolation.py`, `runtime/trial_runner.py`, `runtime/scheduler.py`, `models/run.py`, `tests/integration/test_isolation.py`, `tests/integration/test_trial_lifecycle.py`.

**Acceptance.**
```bash
uv run pytest tests/integration/test_isolation.py -m integration -v
```
Required tests, by name:

| Test | Asserts |
|---|---|
| `test_container_is_non_root` | `id -u` inside the container returns `10001` |
| `test_rootfs_is_read_only` | writing outside `tmpfs` mounts fails |
| `test_no_network_by_default` | a DNS lookup fails inside the container |
| `test_contamination_probe_passes_with_isolation` | writer task then probe task → probe passes |
| `test_contamination_probe_fails_without_isolation` | same pair with `--unsafe-shared-env` → probe **fails** |
| `test_timeout_kills_container` | container is gone within 5s of the deadline; no orphan with the run label |
| `test_agent_failure_is_not_retried` | a task whose agent always errors produces exactly one attempt |
| `test_infra_fault_retried_once` | a simulated daemon fault produces exactly two attempts |
| `test_container_removed_on_grader_exception` | container count unchanged after a raising grader |

**Definition of done.** Zero containers with a `meridian.run` label remain after the full integration suite. Add an autouse fixture that asserts this after every test — it will catch leaks the day they are introduced.

**Pitfalls.** (1) `auto_remove=True` deletes the container before you can extract state; leave it `False` and remove explicitly. (2) Cancelling the asyncio task does not stop the container — kill the container. (3) `docker cp`-equivalent extraction returns a tar stream, not files; handle the archive properly rather than assuming a path. (4) The `--unsafe-shared-env` flag must be genuinely reachable in tests but rejected by config validation in gate mode.

---

### WP-4 — Model proxy: secrets, budget, cassettes · Aug 24–25 · `MD-CAP-30` · `MD-FR-39`

**Objective.** The container never holds a credential, never reaches the internet, cannot exceed its token budget, and can be replayed exactly.

**Files.** `runtime/proxy/server.py`, `runtime/proxy/cassette.py`, `runtime/proxy/budget.py`, `runtime/secrets.py`, `tests/integration/test_proxy.py`.

**Interfaces.** The proxy is an ASGI app started as a container on the internal trial network. Modes `record | replay | passthrough`. Endpoint shape mirrors the provider's messages API closely enough that the adapter needs only a `base_url` override.

**Acceptance.**
```bash
uv run pytest tests/integration/test_proxy.py -m integration -v
```

| Test | Asserts |
|---|---|
| `test_no_credentials_in_container_env` | inspecting the trial container shows an empty relevant env; the key never appears |
| `test_cassette_roundtrip` | record then replay produces byte-identical responses |
| `test_replay_fails_closed_on_miss` | an unrecorded request returns a structured error, not a live call |
| `test_budget_exceeded_returns_429` | a trial exceeding `max_tokens` gets 429 and is classified `fail`, not `harness_error` |
| `test_passthrough_rejected_in_gate_mode` | config validation refuses `mode: passthrough` when `--mode gate` |

**Pitfalls.** Cassette keys must hash the **canonical** request (§8.5) with volatile fields — request ids, timestamps — stripped, or every replay misses. Decide the strip-list explicitly and test it.

---

### WP-5 — Adapter interface and in-container entrypoint · Aug 25–26 · `MD-CAP-09` · `MD-FR-10`, `MD-FR-11`

**Objective.** The harness invokes an agent it does not import, through a narrow interface with two independent implementations.

**Files.** `models/adapter.py`, `adapters/base.py`, `adapters/langgraph_adapter.py`, `adapters/subprocess_adapter.py`, the entrypoint installed into the container, `tests/integration/test_adapters.py`.

**Acceptance.**
```bash
uv run pytest tests/integration/test_adapters.py -m integration -v
```
The decisive test is `test_same_suite_runs_on_two_adapters`: the identical three-task suite runs through both the LangGraph adapter and the subprocess adapter and produces structurally identical `TrialResult` objects. If that test requires special-casing, the abstraction is wrong — fix the interface, not the test.

**Definition of done.** `meridian run --n 5 --k 3 --suite checkout-agent` completes end to end and prints a per-task table with pass/fail counts and efficiency metrics. This is the first moment the product exists.

**Pitfalls.** Do not let LangGraph types leak into `models/adapter.py`. The harness must not import `langgraph` at all — only the adapter module does, and it runs inside the container.

---

### WP-6 — Deterministic graders · Aug 27 · `MD-CAP-13` · `MD-FR-17`

**Objective.** A three-task suite grades with **zero model calls in the grading path**.

**Files.** `grading/pipeline.py`, `grading/graders/*.py`, `tests/unit/test_graders.py`.

**Acceptance.**
```bash
uv run pytest tests/unit/test_graders.py -v
uv run meridian run --suite checkout-agent --n 3 --k 2 --proxy-mode replay
```
The run must complete with the proxy in `replay` mode, proving grading never calls a model.

**Definition of done.** Every grader in the §9.5 table implemented with both a passing and a failing test; a raising grader produces `harness_error`, asserted by `test_grader_exception_is_harness_error`; failure details name actual versus expected values.

**Pitfalls.** JSON path comparison should be exact structural equality after parsing, not string comparison — key order and whitespace must not matter.

---

### WP-7 — Statistics engine · Aug 28–29 · `MD-CAP-18` · `MD-FR-23`, `MD-FR-24`

**Objective.** Correct pass@k, pass^k, cluster-bootstrap intervals, and a paired significance test — all pure functions.

**Files.** `stats/passk.py`, `stats/bootstrap.py`, `stats/significance.py`, `tests/unit/test_passk.py`, `tests/unit/test_bootstrap.py`, `tests/unit/test_significance.py`.

**Acceptance.**
```bash
uv run pytest tests/unit/test_passk.py tests/unit/test_bootstrap.py tests/unit/test_significance.py -v
```
`test_passk.py` must contain the full table from §8.1 as parametrized cases. `test_bootstrap.py` must include `test_resamples_tasks_not_trials`, which constructs a case where trial-level resampling and task-level resampling give visibly different widths and asserts the task-level result. `test_significance.py` must include `test_identical_runs_are_not_significant` and `test_large_uniform_regression_is_significant`.

**Pitfalls.** The agent will reach for `scipy.stats` or implement pass^k as `(c/n)**k`. Both are wrong here — the first is an unnecessary dependency, the second is a different estimator that will disagree with the published definition and make your README indefensible.

---

### WP-8 — Run manifest and replay · Aug 30 · `MD-CAP-12` · `MD-FR-15`, `MD-FR-16`

**Objective.** Every run writes a manifest that fully determines it, and `meridian replay` reproduces the recorded pass^k exactly.

**Files.** `manifest/build.py`, `manifest/replay.py`, `cli.py` (`replay`), `tests/unit/test_canonical_hash.py`, `tests/e2e/test_replay.py`, `tests/fixtures/golden-manifest.json`.

**Acceptance.**
```bash
uv run pytest tests/unit/test_canonical_hash.py -v          # golden hash pinned
uv run meridian run --suite checkout-agent --n 5 --k 3      # note the run id
uv run meridian replay <run-id>                             # identical per-task pass^k
uv run pytest tests/e2e/test_replay.py -m e2e
```

**Definition of done.** Replay fidelity is 1.0 across at least five archived runs; the golden manifest hash test exists and is documented as intentionally brittle.

**Pitfalls.** Floats in the manifest. Timestamps that are not excluded from the hash. Forgetting the uv lock hash, the harness version, or the SUT commit SHA — each omission is a silent hole in the reproducibility claim.

---

### WP-9 — Gate, CLI, and GitHub Action · Aug 31 – Sep 1 · `MD-CAP-24` · `MD-FR-32`, `MD-FR-33`

**Objective.** A pull request fails with a comment naming the flipped tasks, and passes when reverted. **This is the deliverable.**

**Files.** `gate/decide.py`, `gate/comment.py`, `cli.py` (`gate`, `diff`), `.github/workflows/gate.yml`, `tests/unit/test_gate_decide.py`, `tests/e2e/test_gate.py`.

**Acceptance.**
```bash
uv run pytest tests/unit/test_gate_decide.py -v
```
`test_gate_decide.py` must cover, as named tests: pass within tolerance; fail beyond tolerance with significance; pass-with-warning beyond tolerance without significance; inconclusive on `halted_budget`; inconclusive on excess harness errors; pass when no baseline exists; `fail_on_inconclusive` flipping the last two to FAIL.

Then the real proof:
```bash
gh pr create --title "degrade planner"      # switches prompts/planner.md → planner.degraded.md
# CI check fails, comment names expired-coupon
git revert HEAD && git push                 # CI check passes
```

**Definition of done.** Screenshot of the failing check and its comment saved to `docs/demo/`. The exit-code contract from §8.4 has a test.

**Pitfalls.** The Action needs a baseline. Resolve it as the most recent successful run on the merge-base commit; if none exists, the verdict is PASS with the message "no baseline" — never FAIL. A gate that fails on first run gets disabled by the team within a day.

---

### WP-10 — Cost governor and run report · Sep 2 · `MD-CAP-11` (budget half)

**Objective.** A run halts cleanly at its cost cap and reports `halted_budget`; a human-readable report exists.

**Files.** `runtime/proxy/budget.py` (run-level aggregation), `report/table.py`, `report/html.py`, `tests/integration/test_budget_halt.py`.

**Acceptance.**
```bash
uv run meridian run --suite checkout-agent --n 20 --k 3 --budget 0.50   # halts, status halted_budget
uv run pytest tests/integration/test_budget_halt.py -m integration
```
A halted run must still report statistics for tasks that completed, clearly marked partial. Gate treats it as INCONCLUSIVE (§8.4).

---

### WP-11 — Self-evaluation, README, demo · Sep 3–4

**Objective.** The harness measures itself. **A harness that cannot measure itself has no standing to measure anything else** — this is the line the README opens with.

**Files.** `benchmarks/self_eval.py`, `benchmarks/self-eval.json`, `README.md`, `docs/runbook.md`, `docs/demo/`.

**What `self-eval.json` must contain.**
```json
{
  "generated_at": "2026-09-03T14:02:11Z",
  "meridian_version": "0.1.0",
  "false_regression_rate": {"runs": 30, "false_fails": 0, "rate": 0.0},
  "true_positive_rate": {"seeded_regressions": 5, "detected": 5, "rate": 1.0},
  "replay_fidelity": {"manifests": 5, "exact": 5, "rate": 1.0},
  "run_duration_ms": {"mean": 0, "p95": 0},
  "run_cost_cents": {"mean": 0},
  "contamination_probe": {"with_isolation": "pass", "without_isolation": "fail"}
}
```

**Method.** False-regression rate: run the *unchanged* fixture agent through the gate 30 times against a fixed baseline and count how often it reports FAIL. Anything above zero means the gate is noisy and either `n` is too low or the tolerance is too tight — and reporting that honestly is worth more than a clean number. True-positive rate: five seeded regressions of known magnitude (remove the expiry check, truncate the context, drop a tool, lower `max_tokens`, swap in a weaker model), each of which must be caught.

**Acceptance.**
```bash
uv run python -m benchmarks.self_eval --out benchmarks/self-eval.json
```
CI regenerates this on every push to `main` and fails if it cannot.

---

### WP-12 — Stretch, only if ahead

Ordered by value: (1) cache-key derivation as tested pure functions per §8.6, no storage. (2) Flaky-task quarantine (`MD-CAP-05`, `MD-FR-05`, `MD-FR-06`) — variance above threshold across three runs moves a task to `quarantined` and excludes it from the aggregate *visibly*. (3) The subprocess adapter running a non-Python agent, which strengthens the framework-agnostic claim. (4) OpenTelemetry spans per `MD-PRD-001` §10.1.

**Do not** start judges. They are a v1 capability with a calibration workflow attached, and a judge without calibration is worse than no judge at all.

---

## 11. Test strategy

### 11.1 The pyramid

| Layer | Marker | Runs on | Speed | What lives here |
|---|---|---|---|---|
| Unit | `unit` | every commit, no Docker | < 5s total | statistics, gate decision, hashing, validation, graders |
| Integration | `integration` | every commit, Docker required | < 3min | isolation, proxy, adapters, snapshots, budget |
| End-to-end | `e2e` | main and nightly | < 10min | full run, replay, gate against the fixture agent |

`pytest.ini` options: `--strict-markers`, `-ra`, `--tb=short`. Unmarked tests fail the lint step — every test declares its layer.

### 11.2 Coverage rule, stated as behavior not percentage

Coverage percentage is not a target. The rule is: **every function in `stats/`, `gate/`, `manifest/`, and `grading/graders/` has both a passing case and a failing case.** These are the pure functions whose correctness the entire product rests on; everywhere else, one integration test per failure path is sufficient.

### 11.3 The contamination probe

Two tasks, and the pair is the point.

```yaml
# contamination-writer.yaml — writes a marker
outcome_assertions:
  - kind: file_exists
    path: /work/marker.txt
    should_exist: true

# contamination-probe.yaml — asserts the marker is NOT there
outcome_assertions:
  - kind: file_exists
    path: /work/marker.txt
    should_exist: false
```

Run writer then probe. With isolation, the probe passes. With `--unsafe-shared-env`, the probe fails. **Both directions are asserted.** A test that can only pass proves nothing, and the failing direction is what you show in the demo.

### 11.4 Chaos and failure injection

A fixture layer that can inject, on demand: Docker daemon unavailable, image pull failure, container OOM, agent hang past deadline, proxy unreachable, grader exception, and Postgres connection loss mid-run. Each has one test asserting the correct outcome classification and — this is the part that matters — that the run continues and reports partial results rather than aborting.

### 11.5 Definition of done, per capability

Adapted from `MD-PRD-001` §11.5 to MVP reality. A capability is done when:

1. Its functional requirements pass their acceptance criteria as named tests.
2. Its failure paths are exercised by at least one test.
3. Its configuration keys are documented in the config reference.
4. `docs/runbook.md` has an entry for its most likely failure and how to recognize it.
5. It appears in `benchmarks/self-eval.json` if it affects a measurable property.

---

## 12. Continuous integration

Two workflows, doing different jobs.

**`ci.yml`** — runs on every push and pull request: `ruff check`, `ruff format --check`, `mypy --strict src/meridian`, `pytest -m unit`, then `pytest -m integration` on a Docker-enabled runner. Postgres comes from a service container. This workflow is about the harness's own correctness.

**`gate.yml`** — runs on every pull request: builds the environment snapshot, resolves its digest, runs `meridian gate` against the fixture agent with the merge-base as baseline, and comments on the PR. This workflow is the product demonstrating itself, and it is the thing you screenshot.

Three details that matter. `fetch-depth: 0` — merge-base resolution silently breaks with a shallow clone. `--proxy-mode replay` — CI runs offline against cassettes, so the gate needs no API key and cannot be flaky because a provider was slow. `if: always()` on the comment step — a failing gate must still explain itself, or the developer sees a red X with no reason and disables the check.

---

## 13. The fixture agent — your system under test

You need an agent that is realistic enough to be interesting and controllable enough to break on demand.

**Domain: a checkout/invoicing agent.** It reads an order, applies a discount policy, writes an invoice, and updates a database. Chosen because it has genuine business logic, deterministic correct answers, a natural boundary case, and file plus database side effects that grade cleanly.

**Environment (`envs/checkout/`).** A small Python image with a seeded SQLite database at `/work/store.db` (tables `orders`, `coupons`), order JSON files under `/work/orders/`, an empty `/work/out/`, and a coupon policy document at `/work/policy.md`. Non-root, read-only rootfs, `/work` on tmpfs.

**Agent (`fixtures/checkout-agent/agent.py`).** A small LangGraph graph: `plan → act → verify → finish`, with tools `read_order`, `read_policy`, `query_db`, `write_invoice`, `update_order_status`. The planner prompt lives in `prompts/planner.md` so it can be swapped.

**The three real tasks.**

| Task | What it exercises | Correct outcome |
|---|---|---|
| `happy-path` | Baseline competence | Valid coupon applied, invoice total reflects discount, order marked invoiced |
| `expired-coupon` | The boundary case the regression targets | Coupon declined, full price charged, decline reason recorded |
| `missing-field` | Graceful failure | Agent refuses, writes no invoice, order left `pending`, reason recorded |

Plus the two contamination tasks from §11.3, which are harness self-tests and are excluded from the reported suite aggregate.

**The seeded regression.** `prompts/planner.degraded.md` is `planner.md` with the sentence instructing the agent to check coupon expiry removed. Swapping the prompt flips `expired-coupon` from pass to fail while leaving the other two intact — which is exactly the shape of a real regression: narrow, plausible, and invisible in a mean score. With `n=5, k=3`, suite pass^3 moves from 1.0 to roughly 0.67, well beyond a 0.03 tolerance.

**Five seeded regressions for the self-eval** (WP-11): remove the expiry instruction; truncate the policy document before it reaches the agent; remove the `query_db` tool; halve `max_tokens`; substitute a smaller model. Each has a known expected magnitude, and the true-positive rate is how many the gate catches.

---

## 14. Demo script

Seven minutes, in this order. Record it once at the end of WP-11.

1. **Show a task file.** Point at `provenance` — "this task exists because of this trace" — and at the digest-pinned snapshot.
2. **Run it.** `meridian run --suite checkout-agent --n 5 --k 3`. Show the per-task table with pass@3 and pass^3 side by side, and say the sentence: *"this agent passes four of five trials, which is a pass^3 of 0.4."*
3. **Open the pull request** that swaps in the degraded planner prompt.
4. **Show the failing check** and the comment naming `expired-coupon` as the flipped task.
5. **Revert. Show it pass.**
6. **Replay the failing run** and show identical pass^k, then show what the manifest pins.
7. **Run the contamination probe with isolation disabled** and show it fail. This is the slide that separates the project from a wrapper around pytest — it proves the isolation claim has teeth.

Steps 4 and 7 are the two that get remembered. Everything else is setup.

---

## 15. README outline

The README is a deliverable, not documentation. Order matters.

1. **One sentence, then the self-eval table.** Not a logo, not a feature list. The numbers first.
2. **The four rules** (§2) and where each is enforced in code, with file links.
3. **Quickstart**: install, define a task, run, gate. Under ten commands.
4. **Why pass^k rather than a mean score**, with the `C(4,3)/C(5,3) = 0.4` arithmetic worked out. This is the section people quote.
5. **The isolation model** and the contamination test, including the failing direction.
6. **Reproducibility**: what a manifest pins and how replay works.
7. **What is deliberately not built yet**, listed by capability ID with a one-line reason each — so a reader sees a roadmap rather than an omission. Link to `docs/PRD.md`.
8. **Architecture diagram** from `MD-PRD-001` §5.1.

---

## 16. Gotchas — the specific ways this goes wrong

Ordered by how much time each will cost you if it is not anticipated.

**1. Architecture mismatch between your laptop and CI.** You build `linux/arm64`; the runner is `linux/amd64`; the digest differs; the task's pinned digest does not exist on the runner. Decide the strategy in WP-2 — multi-arch build, or build-in-CI with a written `snapshot_ref` — not on day eleven.

**2. Retrying agent failures.** The single most damaging bug this codebase can have, because it produces numbers that look better than reality and nothing crashes. Only `harness_error` retries, only once. There is a named test; keep it.

**3. Orphaned containers.** Timeouts that cancel the Python future without killing the container leak resources and keep spending tokens. Kill the container, then remove it, in a `finally`. Add the run-label sweeper and the autouse leak-check fixture on day one.

**4. `pass^k` implemented as `(c/n)**k`.** A different estimator that will disagree with the definition in your README. Use `comb(c,k)/comb(n,k)`. The test table in §8.1 exists to catch exactly this.

**5. Bootstrap resampling trials instead of tasks.** Produces confidence intervals that are far too narrow, which makes the gate overconfident and eventually wrong in a way nobody can explain. Resample tasks.

**6. Floats in hashed structures.** Manifest hashes stop matching across platforms and replay silently degrades. Integers only inside anything that gets hashed.

**7. Shallow clone in CI.** `fetch-depth: 0` or merge-base resolution returns nothing and the gate has no baseline.

**8. Cassette keys including volatile fields.** Request ids and timestamps in the key mean every replay misses. Strip them, and test the strip-list.

**9. Grader exceptions counted as agent failures.** Makes a broken assertion look like a regression and sends you debugging the wrong system. Grader raises → `harness_error`.

**10. The gate failing on the first run because no baseline exists.** Verdict is PASS with an explanatory message. A gate that fails on day one gets disabled on day two.

**11. Config drift between `schema.sql` and Alembic.** Add a test that applies all migrations to an empty database and diffs the result against `schema.sql`.

**12. Scope creep toward judges.** The pull is strong because judges feel like the interesting part. They are v1, they need a calibration workflow and thirty human labels, and adding them uncalibrated actively damages the product's central claim. Resist.

---

## 17. Definition of done for the MVP

Every one of these must be true on September 4, 2026.

- [ ] `make check` green: ruff, `mypy --strict`, unit, integration.
- [ ] `meridian run --suite checkout-agent --n 5 --k 3` completes and prints per-task pass@k and pass^k.
- [ ] The contamination probe passes with isolation and **fails** without it, both asserted in CI.
- [ ] An agent failure is never retried; an infra fault retries exactly once. Both tested by name.
- [ ] Zero containers with a `meridian.run` label survive the integration suite.
- [ ] `meridian replay <run-id>` reproduces recorded pass^k with fidelity 1.0 across ≥5 archived runs.
- [ ] The golden manifest hash test exists and passes.
- [ ] A pull request on a real repository fails the check with a comment naming the flipped task, and passes on revert. Screenshots in `docs/demo/`.
- [ ] The exit-code contract is tested: only `gate` returns non-zero for a product verdict.
- [ ] `benchmarks/self-eval.json` is committed, regenerated by CI, and reports false-regression rate, true-positive rate on five seeded regressions, and replay fidelity.
- [ ] README leads with the self-eval table and explains pass^k with worked arithmetic.
- [ ] `docs/runbook.md` has an entry per failure mode from §11.4.
- [ ] No LLM judge, no web UI, no multi-tenancy, no second vertical.

---

## 18. Cut list, in order

If you are behind, cut in this order and no other.

1. **Static HTML report** (WP-10 half). The CLI table and the PR comment carry the demo.
2. **`sqlite_query_equals` and `pytest` graders.** Keep filesystem and JSON assertions; rewrite the affected task assertions.
3. **Cost governor.** Replace with a hard trial-count limit; gate treats it the same way.
4. **`meridian replay` as a command.** Keep manifest *writing* — the manifest is the reproducibility claim — and demonstrate replay manually.
5. **The subprocess adapter.** Weakens the framework-agnostic claim; acceptable if the interface has no LangGraph types in it.

**Never cut:** per-trial isolation, pass^k, the manifest, or the contamination probe's failing direction. Those four are the product. Everything else is packaging.

---

## Appendix — ID index

| ID | Name | Work package |
|---|---|---|
| `MD-CAP-01` | Task definition format | WP-1 |
| `MD-CAP-02` | Task provenance | WP-1 |
| `MD-CAP-05` | Flaky-task quarantine | WP-12 (stretch) |
| `MD-CAP-07` | Snapshot management | WP-2 |
| `MD-CAP-08` | Per-trial isolation runtime | WP-3 |
| `MD-CAP-09` | Framework-agnostic adapter | WP-5 |
| `MD-CAP-11` | Trial lifecycle control | WP-3, WP-10 |
| `MD-CAP-12` | Run manifest and replay | WP-8 |
| `MD-CAP-13` | Deterministic outcome graders | WP-6 |
| `MD-CAP-18` | Statistics engine | WP-7 |
| `MD-CAP-19` | Two-axis result cache | WP-12 (keys only) |
| `MD-CAP-24` | CI gate | WP-9 |
| `MD-CAP-30` | Secret injection and configuration | WP-4 |

**Referenced sources.** [Anthropic, *Demystifying evals for AI agents*](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) · [Hamel Husain, *LLM Evals FAQ*](https://hamel.dev/blog/posts/evals-faq/) · [Airbnb, *Making LLM evaluation fast enough to iterate on*](https://medium.com/airbnb-engineering/from-weeks-to-a-day-how-we-made-llm-evaluation-fast-enough-to-iterate-on-14e2d35198b4) · [LangChain, *State of Agent Engineering*](https://www.langchain.com/state-of-agent-engineering) · [Thoughtworks Technology Radar Vol. 34](https://www.thoughtworks.com/content/dam/thoughtworks/documents/radar/2026/04/tr_technology_radar_vol_34_en.pdf)

---

*MD-HND-001 v1.0 — August 21, 2026. Implements MD-PRD-001 MVP tier. Supersedes nothing; superseded by the PRD on any question of product intent, and by reality on any question of schedule.*
