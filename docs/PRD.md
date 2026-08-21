# MD-PRD-001 — Meridian Product Requirements

> **Status of this file.** The authoritative MD-PRD-001 lives outside this
> repository. This file is the **derived extract** the implementation actually
> depends on: the capability catalog with MVP tiering, the architecture sketch
> referenced by `HANDOFF §15.8`, the schema subset from PRD §6.2, and the
> configuration reference from PRD §7.4. When the full PRD is dropped in, replace
> this file wholesale — nothing in `src/` reads it, and `docs/HANDOFF.md` is
> authoritative on implementation while the PRD is authoritative on product
> intent.

---

## §3 Capability catalog

Tier legend: **MVP** = in the G0 two-week slice · **v1** = next gate ·
**later** = has an unmet dependency.

| ID | Capability | Tier | Built in |
|---|---|---|---|
| `MD-CAP-01` | Task definition format | MVP | WP-1 |
| `MD-CAP-02` | Task provenance | MVP | WP-1 |
| `MD-CAP-05` | Flaky-task quarantine | v1 | WP-12 stretch |
| `MD-CAP-07` | Snapshot management | MVP | WP-2 |
| `MD-CAP-08` | Per-trial isolation runtime | MVP | WP-3 |
| `MD-CAP-09` | Framework-agnostic adapter | MVP | WP-5 |
| `MD-CAP-11` | Trial lifecycle control | MVP | WP-3, WP-10 |
| `MD-CAP-12` | Run manifest and replay | MVP | WP-8 |
| `MD-CAP-13` | Deterministic outcome graders | MVP | WP-6 |
| `MD-CAP-14` | LLM-as-judge | MVP¹ | **deferred to v1** |
| `MD-CAP-15` | Judge calibration | v1 | — |
| `MD-CAP-18` | Statistics engine | MVP | WP-7 |
| `MD-CAP-19` | Two-axis result cache | v1 | WP-12, keys only |
| `MD-CAP-21` | Error-analysis workspace | later | needs Lumen trace import (AXIS G2) |
| `MD-CAP-23` | Review queue | later | needs Lumen trace import (AXIS G2) |
| `MD-CAP-24` | CI gate | MVP | WP-9 |
| `MD-CAP-26` | Online scoring and drift detection | later | needs Lumen trace import (AXIS G2) |
| `MD-CAP-29` | Multi-tenancy, RBAC, SSO | later | single tenant for G0 |
| `MD-CAP-30` | Secret injection and configuration | MVP | WP-4 |

¹ `MD-CAP-14` is tiered MVP in the catalog but excluded from the G0 slice by
Appendix C. `HANDOFF §3.2` follows Appendix C and records the divergence. A judge
without a calibration workflow is worse than no judge; it returns in v1 gated on
≥30 labeled examples.

---

## §5.1 Architecture

```
                    ┌──────────────────────────────────────────┐
   meridian CLI ───▶│  suite loader → scheduler → trial runner │
   (Typer)          └────────────┬─────────────────────────────┘
                                 │  one container per trial
                    ┌────────────▼─────────────┐   internal network
                    │  trial container         │◀──────────────────┐
                    │  · digest-pinned image   │  no credentials   │
                    │  · non-root, read-only   │  no route out     │
                    │  · cap_drop ALL, no-new- │                   │
                    │    privileges, pids cap  │            ┌──────┴───────┐
                    │  · adapter entrypoint    │            │ model proxy  │
                    └────────────┬─────────────┘            │ record/replay│
                                 │ extract final state      │ budget · key │
                    ┌────────────▼─────────────┐            └──────────────┘
                    │ graders (outcome only)   │
                    └────────────┬─────────────┘
                                 │
       ┌─────────────────────────┼──────────────────────────┐
       ▼                         ▼                          ▼
  stats engine            manifest builder            store (Postgres)
  pass@k / pass^k         canonical hash              run · trial · score
  cluster bootstrap       replay comparison
       │
       ▼
  gate decision ──▶ exit code + PR comment
```

The **secret broker** role in the PRD is satisfied by the model proxy
(`HANDOFF §9.4`), which additionally provides egress control, per-trial budget
enforcement, and deterministic replay. That consolidation is the second recorded
divergence from the PRD.

---

## §6.2 Schema — MVP subset

Seven tables ship in G0: `tenant`, `suite_version`, `task`, `task_provenance`,
`run`, `trial`, `score`. `tenant_id` columns exist on every row-owning table and
carry a single fixed tenant; enforcement is deliberately absent so that adding it
later is an additive migration rather than a rewrite.

Two constraints carry product meaning and live in the database rather than in
application code, because application code can be bypassed and a constraint
cannot:

```sql
CONSTRAINT task_snapshot_pinned CHECK (snapshot_digest LIKE 'sha256:%'),
CONSTRAINT task_has_outcome     CHECK (jsonb_array_length(definition->'outcome_assertions') > 0)
```

Deferred until their capabilities are in scope: `judge_version`,
`calibration_record`, `cache_judge_score`, `cache_reference`, `audit_log`.

---

## §7.4 Configuration reference

Resolution order, lowest to highest precedence: built-in defaults →
`meridian.yaml` → `MERIDIAN_*` environment variables → CLI flags. The resolved
configuration is canonically hashed into `config_hash` and recorded in the
manifest, so two runs that disagree are provably distinguishable.

| Key | Type | Default | Notes |
|---|---|---|---|
| `suite.path` | path | `./suites` | Root searched for suite directories |
| `execution.n_trials` | int | `5` | Trials per task |
| `execution.k` | int | `3` | The *k* in pass^k |
| `execution.max_concurrent_trials` | int | `4` | Scheduler semaphore |
| `execution.proxy_mode` | enum | `replay` | `record \| replay \| passthrough`. **MVP-only key** — a PRD divergence recorded in `HANDOFF §3.2`. `passthrough` is rejected in gate mode. |
| `execution.unsafe_shared_env` | bool | `false` | Disables Rule 1. Reachable in tests, rejected in gate mode. |
| `execution.budget_cents` | int \| null | `null` | Run-level cost ceiling; exceeding it yields `halted_budget` |
| `gate.tolerance` | decimal string | `"0.02"` | Allowed pass^k drop |
| `gate.require_significance` | bool | `true` | Paired bootstrap before FAIL |
| `gate.significance_level` | decimal string | `"0.05"` | |
| `gate.fail_on_inconclusive` | bool | `false` | Flips INCONCLUSIVE to FAIL |
| `gate.max_harness_error_rate` | decimal string | `"0.05"` | Above this, the run is INCONCLUSIVE |
| `stats.bootstrap_iterations` | int | `10000` | |
| `stats.bootstrap_seed` | int | `0` | Recorded in the manifest |
| `store.database_url` | str | from `MERIDIAN_DATABASE_URL` | |

Probabilities and tolerances are decimal **strings** in configuration, not
floats, because the resolved configuration is hashed and float formatting is not
stable across platforms (`HANDOFF §8.5`).

---

## §10.1 Telemetry

Spans: `invoke_agent` (one per trial) and `execute_tool` (one per tool call),
carrying `run_id`, `task_slug`, `trial_index`, and the outcome classification.
`gen_ai.*` semantic-convention attributes are `Development` status upstream and
will churn; treat any code depending on their exact names as provisional.

---

## §11.5 Definition of done, per capability

A capability is done when: its functional requirements pass as named tests; its
failure paths are exercised; its configuration keys are documented above;
`docs/runbook.md` has an entry for its most likely failure; and it appears in
`benchmarks/self-eval.json` if it affects a measurable property.
