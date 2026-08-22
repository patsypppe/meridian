# Plan: Meridian — Close the Definition of Done

## Summary

Meridian is functionally complete and usable: WP-0 → WP-10b are merged, 277 tests pass
(230 unit / 41 integration / 6 e2e), `ruff` and `mypy --strict` are clean, a full
`meridian run --n 5 --k 3` completes and prints per-task `pass@3`/`pass^3`, zero containers
leak, and the demo PR (#12) is real and closed-unmerged because the gate said no.

Four things remain before `docs/HANDOFF.md` §17 is satisfied: the entire WP-11 work package
is uncommitted; three of the seven §11.4 chaos failure modes have no test; the self-eval
catches 4 of 5 seeded regressions where WP-11 requires 5; and §8.6 cache-key derivation was
never built. This plan closes all four, plus two accepted WP-12 stretch items.

## User Story

As an engineer shipping an agent,
I want Meridian's own Definition of Done to be met and its self-eval to catch every seeded
regression,
So that when the gate says my agent got worse, I can believe it.

## Problem → Solution

**Current state** — a working harness whose finishing work sits uncommitted in a dirty tree,
whose chaos coverage is 4/7, whose benchmark misses a real regression it should catch, and
which is missing one small deliverable the handoff explicitly carved *out* of the "do not
build" list.

**Desired state** — `main` carries WP-11; all seven §11.4 failure modes have a named test
asserting outcome classification *and* that the run reports partial results; the self-eval
reports 5/5 true positives from a suite with the statistical power to earn that number; and
`§8.6` cache keys ship as tested pure functions.

## Metadata

- **Complexity**: Large
- **Source PRD**: `docs/HANDOFF.md` (MD-HND-001) — §17 Definition of Done, §11.4, §8.6, WP-11, WP-12
- **PRD Phase**: WP-11 (land) + WP-12 (partial) + §11.4 remediation
- **Estimated Files**: ~40 (5 new task triplets = 15, 4 test files, 3 new modules, ~8 updated)

---

## UX Design

**Internal change — no user-facing UX transformation**, with two exceptions:

### CLI table gains a quarantine line (Task 9)

**Before**
```
task                      pass     n   pass@3   pass^3
────────────────────────────────────────────────────────────
flaky-task                   3     5     0.90     0.10
────────────────────────────────────────────────────────────
suite pass^3                                     0.49
excluded from the aggregate: contamination-probe, contamination-writer
```

**After**
```
task                      pass     n   pass@3   pass^3
────────────────────────────────────────────────────────────
flaky-task                   3     5     0.90     0.10   QUARANTINED
────────────────────────────────────────────────────────────
suite pass^3                                     0.52
excluded from the aggregate: contamination-probe, contamination-writer
quarantined (variance > 0.25 across 3 runs): flaky-task
```

The rule from `MD-CAP-05` is that exclusion is *visible*. A task silently dropped from the
aggregate is how a suite quietly starts reporting a better number than reality.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `meridian run` table | no quarantine concept | quarantined tasks marked and named below the aggregate | Task 9 |
| `meridian run` on OOM | generic harness error, retried once | `fail`, named as a memory-limit breach, never retried | Task 3 |
| Suite size | 7 eval tasks | 12 eval tasks | Task 7 — run wall-clock grows ~70% |
| `benchmarks/self-eval.json` | `true_positive_rate: 4/5` | `5/5`, `seeded_regressions: 5` | Task 8 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/HANDOFF.md` | 1030-1046, 601-616, 956-993, 1145-1164 | §11.4 chaos, §8.6 keys, WP-11/12, §17 DoD |
| P0 | `src/meridian/runtime/trial_runner.py` | 164-250, 359-380, 431-450 | Where OOM classification and the proxy-error defect live |
| P0 | `tests/integration/test_trial_lifecycle.py` | 1-99, 100-218 | The exact chaos-test pattern to mirror |
| P0 | `src/meridian/hashing.py` | 1-68 | `content_hash` / `canonical_json` — the basis for §8.6 keys |
| P0 | `envs/checkout/seed/seed_db.py` | 15-23 | Orders and coupons; new tasks need new rows here |
| P0 | `envs/checkout/seed/policy.md` | all | The rules new tasks must be consistent with |
| P1 | `suites/checkout-agent/tasks/expired-coupon.yaml` | all | The task YAML shape to copy exactly |
| P1 | `src/meridian/runtime/proxy/stub_provider.py` | 118-200 | How the stand-in provider derives behaviour; new tasks must fit it |
| P1 | `benchmarks/self_eval.py` | 76-215, 280-302 | `Seeded`, `REGRESSIONS`, `measure_true_positives` |
| P1 | `src/meridian/report/table.py` | 21-62 | Where the quarantine line goes |
| P1 | `src/meridian/store/schema.sql` | 29-47 | `task.state` already permits `'quarantined'` |
| P1 | `src/meridian/runtime/scheduler.py` | 43-114 | Task-major ordering; quarantine reads across runs |
| P2 | `tests/conftest.py` | 53-99 | The autouse leak fixture every Docker test inherits |
| P2 | `src/meridian/gate/decide.py` | 36-95 | `GateInput`, verdict ordering |
| P2 | `src/meridian/stats/significance.py` | 45+ | `paired_regression_p_value` — the function Task 7 gives power to |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Docker OOM detection | docker-py `Container.attrs["State"]["OOMKilled"]` | Exit 137 is *also* plain SIGKILL. Only the `OOMKilled` boolean distinguishes an OOM from our own timeout kill. Requires `container.reload()` after wait. |
| Docker `ImageNotFound` | `docker.errors.ImageNotFound` (subclass of `NotFound` → `APIError`) | Already caught by the existing `except Exception` in `snapshots/registry.py:55`; the runner path must classify it as a harness error, not swallow it. |
| OpenTelemetry Python | `opentelemetry-sdk`, `opentelemetry-api` | Use a no-op `TracerProvider` by default so spans cost nothing and add no required dependency at runtime. Never let a span export failure fail a trial. |

---

## Patterns to Mirror

### NAMING_CONVENTION — tests are named as sentences you would notice deleting
```python
# SOURCE: tests/integration/test_trial_lifecycle.py:86
async def test_agent_failure_is_not_retried(docker_client: Any, suite: Suite) -> None:
    """The most important test in the repository.

    Retrying an agent failure produces a number better than reality while nothing
    appears to break — the exact failure mode Meridian exists to prevent.
    """
```

### CHAOS_INJECTION — patch the *class*, not the instance
```python
# SOURCE: tests/integration/test_trial_lifecycle.py:117-137
async def test_infra_fault_is_retried_once(
    docker_client: Any, suite: Suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docker.errors
    from docker.models.containers import ContainerCollection

    task = task_running(suite.task("contamination-probe"), "touch_output")
    # `client.containers` builds a fresh collection on every access, so patching
    # the instance is a no-op. The class is the seam.
    original = ContainerCollection.create
    calls = {"n": 0}

    def flaky_create(self: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise docker.errors.APIError("simulated daemon fault")
        return original(self, **kwargs)

    monkeypatch.setattr(ContainerCollection, "create", flaky_create)
    result = await run(make_runner(docker_client, suite), task, "life-infra")

    assert result.outcome is Outcome.PASS, result.detail
    assert result.attempts == 2, "an infrastructure fault retries exactly once"
```

### ERROR_HANDLING — harness faults are a distinct type, never a bare raise
```python
# SOURCE: src/meridian/runtime/trial_runner.py:359-378
        except Exception as exc:
            raise HarnessFault(
                f"waiting on the container failed: {type(exc).__name__}: {exc}"
            ) from exc
```

### OUTCOME_CLASSIFICATION — a comment states *why* it is never retried
```python
# SOURCE: src/meridian/runtime/trial_runner.py:220-233
        if phase.timed_out:
            # Never retried: the agent had its wall clock and did not finish.
            return TrialResult(
                run_id=spec.run_id,
                task_slug=spec.task_slug,
                trial_index=spec.trial_index,
                seed=spec.seed,
                outcome=Outcome.TIMEOUT,
                detail=f"exceeded the {task.limits.timeout_seconds}s deadline",
                attempts=attempt,
                efficiency=self._efficiency(adapter_result, started),
                container_removed=phase.container_removed,
                started_unix_ms=started,
            )
```

### PURE_FUNCTION_MODULE — module docstring argues the design, `__all__` is explicit
```python
# SOURCE: src/meridian/hashing.py:1-22
"""Canonical JSON and content hashing — one implementation, used everywhere.
...
The float rule is enforced, not documented. Floats are not stable across
platforms and a drifting hash silently invalidates every historical manifest, so
`canonical_json` refuses to serialize one and names the path where it found it.
"""

__all__ = ["FloatInHashedStructureError", "canonical_json", "content_hash"]
```

### PROBE_PATTERN — deterministic argv-returning callables, no model in the loop
```python
# SOURCE: fixtures/probes.py:26-33
def always_fails(ctx: Any) -> list[str]:
    """Fail every time. Used to prove agent failures are never retried."""
    return ["/bin/sh", "-c", "echo the agent could not do it >&2; exit 3"]


def hang(ctx: Any) -> list[str]:
    """Run past any sane deadline. Used to prove the container is killed."""
    return ["/bin/sh", "-c", "sleep 900"]
```

### TEST_STRUCTURE — module-level marker pair, module-scoped suite fixture
```python
# SOURCE: tests/integration/test_trial_lifecycle.py:25-35
pytestmark = [pytest.mark.integration, requires_docker]

FIXTURES_ROOT = REPO_ROOT / "fixtures"
TIMEOUT_SECONDS = 10
KILL_GRACE_SECONDS = 5


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(CHECKOUT_SUITE)
```

### TASK_YAML — every task pins a digest and carries provenance
```yaml
# SOURCE: suites/checkout-agent/tasks/expired-coupon.yaml
slug: expired-coupon
description: >
  An order arrives with a coupon that expired yesterday. The agent must decline
  the coupon, charge full price, and record the reason on the invoice.

provenance:
  trace_id: "0af7651916cd43dd8448eb211c80319c"
  session_id: "sess_2026-08-14T09:22Z"
  failure_code: "coupon-expiry-ignored"

environment:
  snapshot: "sha256:1259042900c0cb1bf623c36f5e995ab873b2b2e3fdc2844c9dd7415831880d0c"
  workdir: /work
  network: proxy-only
  resources: {cpus: 2.0, memory_mb: 2048, pids: 256}

input:
  prompt_file: prompts/expired-coupon.txt
  files:
    - src: seed/order-1042.json
      dest: /work/orders/order-1042.json

limits: {timeout_seconds: 300, max_tokens: 60000, budget_cents: 25}

outcome_assertions:
  - kind: json_path_equals
    path: /work/out/invoice-1042.json
    json_path: "$.total_cents"
    expected: 4999
  - kind: sqlite_query_equals
    database: /work/store.db
    query: "SELECT status FROM orders WHERE id = 1042"
    expected: [["invoiced"]]

efficiency_expectations: {max_tool_calls: 12, max_turns: 8}
tags: [pricing, coupons, boundary]
```

### SEEDED_REGRESSION — a regression that changes nothing must raise
```python
# SOURCE: benchmarks/self_eval.py:84-104
class RegressionNotApplied(RuntimeError):
    """A seeded regression changed nothing, so the number it produces is a lie."""


def _swap_file(path: Path, replacement: str) -> Callable[[], None]:
    if replacement == original:
        raise RegressionNotApplied(f"the seeded edit to {path} changed nothing")
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| *(all currently-dirty files)* | COMMIT | Task 1 — land WP-11 |
| `fixtures/probes.py` | UPDATE | Add `exhaust_memory` probe |
| `src/meridian/runtime/trial_runner.py` | UPDATE | OOM detection + classification; proxy-error reclassification |
| `tests/integration/test_trial_lifecycle.py` | UPDATE | OOM + image-pull chaos tests |
| `tests/integration/test_proxy.py` | UPDATE | Proxy-unreachable chaos test |
| `tests/integration/test_orchestrator_partial.py` | CREATE | §11.4's "run continues, reports partial results" |
| `envs/checkout/seed/seed_db.py` | UPDATE | Orders 1048–1052 + two new coupons |
| `suites/checkout-agent/seed/order-104{8,9}.json`, `order-105{0,1,2}.json` | CREATE | 5 new orders, all non-round-dollar totals |
| `suites/checkout-agent/prompts/*.txt` (×5) | CREATE | One prompt per new task |
| `suites/checkout-agent/tasks/*.yaml` (×5) | CREATE | One task per new order |
| `suites/checkout-agent/suite.yaml` | UPDATE | Description says "Three eval tasks" — already wrong at 7, will be 12 |
| `fixtures/cassettes/checkout-agent/*.json` | UPDATE | Re-record all 12 |
| `src/meridian/cache/__init__.py`, `src/meridian/cache/keys.py` | CREATE | §8.6 |
| `tests/unit/test_cache_keys.py` | CREATE | §8.6 tests |
| `src/meridian/stats/variance.py` | CREATE | Quarantine variance statistic |
| `src/meridian/suites/quarantine.py` | CREATE | Quarantine decision (pure) |
| `tests/unit/test_quarantine.py` | CREATE | Quarantine tests |
| `src/meridian/models/run.py` | UPDATE | `quarantined_task_slugs` on `RunResult` |
| `src/meridian/report/table.py`, `report/html.py` | UPDATE | Render quarantine visibly |
| `src/meridian/tracing.py` | CREATE | OTel spans, no-op by default |
| `src/meridian/config.py` | UPDATE | `ObservabilityConfig` |
| `docs/runbook.md` | UPDATE | Entries for OOM-as-fail and quarantine |
| `README.md` | UPDATE | Self-eval table to 5/5, suite size 12 |
| `benchmarks/self-eval.json` | UPDATE | Regenerated |

## NOT Building

- **LLM-as-judge.** §3.2 and WP-12 both forbid it; a judge without calibration is worse than
  no judge. Cache *keys* ship; no cache storage, no judge.
- **Cache storage / the two-axis cache itself.** §8.6 says "specify now, wire later."
- **A web UI** beyond the existing static HTML report.
- **Multi-tenancy enforcement.** `tenant_id` columns stay unenforced by design.
- **A second vertical or a non-LangGraph adapter** beyond the existing subprocess one.
- **Drift detection, error-analysis workspace.** §3.2 out of scope.
- **Re-tuning the gate's tolerance or significance level to manufacture 5/5.** Explicitly
  rejected by the README: "a benchmark that adjusts the thing it is measuring measures
  nothing." Task 7 earns the number with more tasks instead.

---

## Step-by-Step Tasks

### Task 1: Land WP-11 onto `main`

- **ACTION**: Commit the working tree and open a PR from `wp-11-self-eval`.
- **IMPLEMENT**: Two commits, because they are two different claims:
  1. `fix: recording replaces the tapes a run touches` — `src/meridian/runtime/proxy/cassette.py`,
     `tests/unit/test_proxy_units.py`, `tests/e2e/test_gate.py`, and the seven re-recorded
     cassettes (the ~41,000-line shrink is this fix taking effect, not noise).
  2. `feat: the harness measures itself (WP-11)` — `benchmarks/`, `docs/runbook.md`,
     `README.md`, `.github/workflows/self-eval.yml`, `Makefile`, `pyproject.toml`, `ci.yml`.
- **MIRROR**: Commit style from `git log` — `feat:`/`fix:` + a claim, e.g.
  `feat: Postgres store with a schema that cannot drift (WP-10b)`.
- **IMPORTS**: n/a.
- **GOTCHA**: The cassette shrink looks like data loss in review. Say in the commit body
  *why* it shrinks: recording used to append across runs, so tapes accumulated stale takes
  and replay served the oldest one forever.
- **VALIDATE**: `make check && uv run pytest -m integration && uv run pytest -m e2e`, then
  `git status --short` is empty.

### Task 2: Add the `exhaust_memory` probe

- **ACTION**: Add a probe that allocates past any sane container memory limit.
- **IMPLEMENT**: In `fixtures/probes.py`:
  ```python
  def exhaust_memory(ctx: Any) -> list[str]:
      """Allocate until the cgroup kills us. Used to prove an OOM is the agent's."""
      # `yes` into memory is portable and needs no interpreter in the image.
      return ["/bin/sh", "-c", "yes meridian | head -c 4000000000 | tail -n 1"]
  ```
- **MIRROR**: PROBE_PATTERN above — docstring states which rule the probe defends.
- **IMPORTS**: none beyond the existing `from typing import Any`.
- **GOTCHA**: The probe must exceed the *task's* `memory_mb`, so the test must lower
  `Limits`/resources to something small (e.g. 64 MB) rather than allocate 4 GB for real.
  Add a `memory_mb` override to `task_running` alongside the existing `timeout_seconds` one.
- **VALIDATE**: `docker run --memory 64m <checkout-digest> /bin/sh -c "yes | head -c 4000000000 | tail -n1"; echo $?` → `137`.

### Task 3: Classify an OOM kill as `fail`, never a harness error

- **ACTION**: Detect `OOMKilled` and classify it as a statement about the agent.
- **IMPLEMENT**:
  1. Add `oom_killed: bool` to `ContainerPhase` (`trial_runner.py:79`).
  2. In `_wait` (line 359), after a clean wait, `container.reload()` and read
     `container.attrs.get("State", {}).get("OOMKilled", False)`; return it alongside the code.
  3. In `_verdict` (line 204), immediately **after** the `phase.timed_out` branch:
     ```python
     if phase.oom_killed:
         # Never retried, for the same reason a timeout is not: the task set the
         # ceiling and the agent walked into it. Retrying would report a number
         # better than reality.
         return TrialResult(
             ...,
             outcome=Outcome.FAIL,
             detail=f"exceeded the {task.environment.resources.memory_mb}MB memory limit",
             ...,
         )
     ```
- **MIRROR**: OUTCOME_CLASSIFICATION above — copy the `timed_out` block verbatim and change
  the outcome, detail, and comment.
- **IMPORTS**: none new.
- **GOTCHA (critical)**: **Exit 137 is not sufficient.** Our own `_kill` on timeout also
  produces 137. Only `State.OOMKilled` distinguishes them, and it is only populated after
  `container.reload()`. Check `timed_out` *first* so a timeout is never relabelled an OOM.
- **VALIDATE**: New test below; plus `uv run mypy` stays clean (`ContainerPhase` is typed).

### Task 4: Chaos test — container OOM

- **ACTION**: Add `test_an_oom_is_the_agents_failure_and_is_not_retried`.
- **IMPLEMENT**: In `tests/integration/test_trial_lifecycle.py`:
  ```python
  async def test_an_oom_is_the_agents_failure_and_is_not_retried(
      docker_client: Any, suite: Suite
  ) -> None:
      """A memory ceiling is set by the task, so walking into it is the agent's doing."""
      task = task_running(suite.task("contamination-probe"), "exhaust_memory", memory_mb=64)
      result = await run(make_runner(docker_client, suite), task, "life-oom")

      assert result.outcome is Outcome.FAIL, result.detail
      assert "memory" in result.detail.lower()
      assert result.attempts == 1, "an OOM is a statement about the agent and is never retried"
  ```
- **MIRROR**: `test_timeout_kills_the_container` (line 100) — same shape, same assertion trio.
- **IMPORTS**: already present in the file.
- **GOTCHA**: The autouse `no_leaked_trial_containers` fixture will fail this test if the
  OOM path skips teardown. Verify the `finally` that destroys the container still runs.
- **VALIDATE**: `uv run pytest -m integration -k oom -q`.

### Task 5: Chaos test — image pull failure

- **ACTION**: Add `test_a_missing_image_is_a_harness_error_not_an_agent_failure`.
- **IMPLEMENT**: In `tests/integration/test_trial_lifecycle.py`:
  ```python
  async def test_a_missing_image_is_a_harness_error_not_an_agent_failure(
      docker_client: Any, suite: Suite, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """An image Meridian cannot obtain says nothing about the agent."""
      import docker.errors
      from docker.models.containers import ContainerCollection

      task = task_running(suite.task("contamination-probe"), "touch_output")
      calls = {"n": 0}

      def missing_image(self: Any, **kwargs: Any) -> Any:
          calls["n"] += 1
          raise docker.errors.ImageNotFound("no such image: sha256:deadbeef")

      monkeypatch.setattr(ContainerCollection, "create", missing_image)
      result = await run(make_runner(docker_client, suite), task, "life-nopull")

      assert result.outcome is Outcome.HARNESS_ERROR
      assert result.attempts == 2, "retried once, then reported"
      assert calls["n"] == 2
  ```
- **MIRROR**: CHAOS_INJECTION above — identical seam, different exception.
- **IMPORTS**: local `import docker.errors` inside the test, matching the existing pattern.
- **GOTCHA**: `ImageNotFound` subclasses `NotFound` subclasses `APIError`. If the runner
  catches `APIError` broadly this passes for the wrong reason — assert the *detail* names
  the image so the classification is real and not incidental.
- **VALIDATE**: `uv run pytest -m integration -k missing_image -q`.

### Task 6: Chaos test — proxy unreachable (expect to fix a real defect)

- **ACTION**: Add `test_an_unreachable_proxy_is_a_harness_error_not_an_agent_failure` and
  correct the misclassification it exposes.
- **IMPLEMENT**:
  1. Test in `tests/integration/test_proxy.py`: start a trial whose `proxy_base_url` points
     at a closed port, and assert `Outcome.HARNESS_ERROR`.
  2. **Expected failure**: `_classify` (`trial_runner.py:445-447`) currently turns *any*
     `result.error` into `Outcome.FAIL, f"agent error: {result.error}"`. A proxy outage
     therefore gets blamed on the agent — a harness fault reported as a regression, which is
     exactly what Rule 2 and the exit-code contract exist to prevent.
  3. Fix: have the adapter tag transport failures reaching the proxy distinctly (e.g.
     `AdapterResult.error_kind = "proxy_unreachable"`), and in `_classify` raise
     `HarnessFault` for that kind before the `Outcome.FAIL` branch.
- **MIRROR**: ERROR_HANDLING above — `raise HarnessFault(...) from exc`.
- **IMPORTS**: `from meridian.runtime.trial_runner import HarnessFault` where needed.
- **GOTCHA**: Do **not** widen this to "any error mentioning connection" — an agent that
  fails its own HTTP call to something else is still an agent failure. The signal must come
  from the adapter knowing it could not reach *the proxy*, not from string-matching.
- **VALIDATE**: `uv run pytest -m integration -k proxy_unreachable -q`. Write the test
  first, watch it fail with `outcome == FAIL`, then fix.

### Task 7: Grow the suite to 12 tasks, weighted to non-round-dollar totals

- **ACTION**: Add five eval tasks chosen specifically to give the paired test power over the
  `break-the-invoice-rounding` regression.
- **IMPLEMENT**: The diagnosis, computed from the current seeds:

  | order | coupon | subtotal | invoiced total | rounding regression bites? |
  |---|---|---|---|---|
  | 1041 | SPRING10 (valid) | 5000 | 4500 | ✗ already whole dollars |
  | 1042 | SUMMER20 (expired) | 4999 | 4999 | ✓ → 4900 |
  | 1043 | none, no items | 0 | refusal | ✗ no invoice |
  | 1044 | LAUNCH15 (expired) | 12000 | 12000 | ✗ |
  | 1045 | SUMMER20 (expired) | 30000 | 30000 | ✗ |
  | 1046 | BOUNDARY7 (valid) | 8000 | 7440 | ✓ → 7400 |
  | 1047 | ZZZFAKE (unknown) | 2500 | 2500 | ✗ |

  Only **2 of 7** tasks move under that regression, which is why the drop was 0.114 at
  `p=0.091`. Every new task must therefore invoice a **non-whole-dollar total**.

  1. `envs/checkout/seed/seed_db.py`: extend `ORDERS` to `range(1041, 1053)`; add coupons
     `AUTUMN12` (12%, `2026-12-31`) and `HALFOFF50` (50%, `2026-01-15`, expired).
  2. Five seeds under `suites/checkout-agent/seed/`, each with a multi-item order whose
     invoiced total ends in non-zero cents. Suggested (verify arithmetic against
     `policy.md`'s "nearest cent, ties away from zero" and `_discount` at
     `stub_provider.py:114`):

     | order | coupon | items | subtotal | total | ends in |
     |---|---|---|---|---|---|
     | 1048 | AUTUMN12 (valid) | 3 × 1333 | 3999 | 3519 | 19 ✓ |
     | 1049 | SPRING10 (valid) | 1 × 2499 + 2 × 875 | 4249 | 3824 | 24 ✓ |
     | 1050 | HALFOFF50 (expired) | 2 × 1737 | 3474 | 3474 | 74 ✓ |
     | 1051 | BOUNDARY7 (valid) | 5 × 1111 | 5555 | 5166 | 66 ✓ |
     | 1052 | WELCOME5 (valid) | 1 × 9999 | 9999 | 9499 | 99 ✓ |

  3. Five prompts mirroring `prompts/expired-coupon.txt` verbatim, order id swapped.
  4. Five task YAMLs mirroring TASK_YAML above, with `provenance.synthetic_reason` set
     (these are authored, not harvested from a trace — and the schema requires one or the
     other, per `schema.sql:58`).
  5. `make images` to rebuild and repin, then re-record all 12 cassettes.
- **MIRROR**: TASK_YAML and the existing seed JSON shape.
- **IMPORTS**: n/a.
- **GOTCHA (critical)**: Editing `seed_db.py` changes the image digest. **Every** task YAML
  pins that digest, so all 12 must be repinned — that is what
  `--write-ref --update-suite` in `make images` is for. Do not hand-edit digests.
- **GOTCHA**: Compute each expected `total_cents` with `_discount(subtotal, percent_off)` —
  `(subtotal * percent * 2 + 100) // 200` — not with float arithmetic. A float here produces
  an off-by-one-cent assertion that fails intermittently and wastes an afternoon.
- **GOTCHA**: The stand-in provider derives behaviour from the conversation, so new tasks
  need **no** stub changes as long as they use the existing tool vocabulary
  (`read_order` → `read_policy` → `query_coupon` → `write_invoice`).
- **VALIDATE**:
  ```bash
  uv run meridian validate --suite ./suites/checkout-agent   # 12 tasks, all pinned
  make images
  uv run meridian run --suite ./suites/checkout-agent --n 5 --k 3 --proxy-mode record
  ```
  Then confirm each new task's invoice total is non-round in the run output.

### Task 8: Regenerate the self-eval and confirm 5/5

- **ACTION**: Re-run the benchmark against the 12-task suite.
- **IMPLEMENT**: `make self-eval` (~30–50 min at 12 tasks). Then update `README.md`'s table
  to 5/5 and replace "The miss, stated plainly" with a short note on *why* the suite grew —
  keeping the honesty, changing the outcome.
- **MIRROR**: `measure_true_positives` (`benchmarks/self_eval.py:280`) is unchanged; only
  its input suite grows.
- **IMPORTS**: n/a.
- **GOTCHA**: If `break-the-invoice-rounding` still lands above `p=0.05`, **do not** loosen
  `--tolerance` or `significance_level`. Add rounding-sensitive tasks until it resolves, or
  report the honest number again. The README's own argument forbids tuning the threshold.
- **GOTCHA**: The benchmark must record into a throwaway directory, never
  `fixtures/cassettes/` — see the `--cassettes` flag added in `tests/e2e/test_gate.py`.
- **VALIDATE**: `jq '.true_positive_rate' benchmarks/self-eval.json` → `"rate": 1.0`,
  `"detected": 5`. `false_regression_rate.rate` must stay `0.0`.

### Task 9: §8.6 cache-key derivation

- **ACTION**: Ship `reference_key` and `judge_score_key` as tested pure functions.
- **IMPLEMENT**: `src/meridian/cache/keys.py`:
  ```python
  """Cache keys, specified now and wired later (`HANDOFF §8.6`).

  There is nothing expensive to memoize while graders are deterministic, so no
  cache ships. The *keys* ship anyway: fixing their shape before judges arrive is
  what stops a cache epoch from being retrofitted across stored rows later.

  `v1:` is a cache epoch. Bumping it invalidates everything without a migration.
  `output_hash` hashes the graded artifact, not the transcript — over half of
  model outputs across candidate versions are byte-identical, which is precisely
  why this cache is worth having.
  """

  from __future__ import annotations

  import hashlib

  __all__ = ["CACHE_EPOCH", "judge_score_key", "reference_key"]

  CACHE_EPOCH = "v1"


  def _sha256(text: str) -> str:
      return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


  def reference_key(sample_id: str, generation_config_hash: str) -> str:
      return _sha256(f"ref:{CACHE_EPOCH}:{sample_id}:{generation_config_hash}")


  def judge_score_key(
      sample_id: str, output_hash: str, judge_config_hash: str, metric: str
  ) -> str:
      return _sha256(
          f"judge:{CACHE_EPOCH}:{sample_id}:{output_hash}:{judge_config_hash}:{metric}"
      )
  ```
- **MIRROR**: PURE_FUNCTION_MODULE above — docstring argues the design, explicit `__all__`,
  `sha256:` prefix consistent with `hashing.content_hash`.
- **IMPORTS**: stdlib `hashlib` only. **No I/O, no Docker** — `src/meridian/cache/` joins
  the pure-logic set named in `CLAUDE.md`.
- **GOTCHA**: Do not reuse `content_hash` here. §8.6 specifies hashing a *concatenated
  string*, not canonical JSON; using `content_hash` would silently produce different keys
  than the spec and the divergence would only surface when judges land.
- **VALIDATE**: `tests/unit/test_cache_keys.py` — per `CLAUDE.md`'s coverage rule, every
  function gets a passing and a failing case:
  - same inputs → same key (twice)
  - each argument, varied independently, changes the key (4 cases for `judge_score_key`)
  - a bumped `CACHE_EPOCH` changes every key
  - `reference_key` and `judge_score_key` never collide for the same `sample_id`
  - keys are `sha256:`-prefixed and 71 chars

### Task 10: Flaky-task quarantine (`MD-CAP-05`)

- **ACTION**: Variance above threshold across three runs quarantines a task and excludes it
  from the aggregate **visibly**.
- **IMPLEMENT**:
  1. `src/meridian/stats/variance.py` — pure: `pass_rate_variance(rates: Sequence[float]) -> float`.
  2. `src/meridian/suites/quarantine.py` — pure:
     `should_quarantine(rates: Sequence[float], *, threshold: float, min_runs: int = 3) -> bool`.
     Returns `False` with fewer than `min_runs` observations; a task is never quarantined on
     thin evidence.
  3. `RunResult.quarantined_task_slugs: tuple[str, ...] = ()` in `models/run.py`.
  4. `report/table.py`: mark the row and add the named line below the aggregate (see UX
     above); exclude quarantined tasks from the `scored` list at line 49.
  5. `store/repo.py`: persist `task.state = 'quarantined'`.
- **MIRROR**: `stats/passk.py` for the pure-stat module shape; `excluded_task_slugs`
  rendering at `report/table.py:58` for the visibility line.
- **IMPORTS**: `from meridian.stats.variance import pass_rate_variance`.
- **GOTCHA**: `schema.sql:45` **already** has
  `CONSTRAINT task_state_known CHECK (state IN ('active', 'quarantined', 'withdrawn'))`.
  No migration is needed for the column — but `tests/integration/test_store.py::test_migrations_match_the_canonical_schema`
  diffs migrations against `schema.sql`, so do not edit one without the other.
- **GOTCHA**: Quarantine reads across *runs*, and the scheduler is single-run. The decision
  belongs to the CLI/orchestrator layer reading history from the store — keep
  `quarantine.py` pure and pass it the rates.
- **VALIDATE**: `tests/unit/test_quarantine.py` — a stable task is not quarantined; a task
  alternating 0.0/1.0/0.0 is; two runs never quarantine regardless of variance; a
  quarantined task is absent from the aggregate and present in the printed line.

### Task 11: OpenTelemetry spans (`MD-PRD-001` §10.1)

- **ACTION**: Emit one span per run and per trial, no-op unless configured.
- **IMPLEMENT**:
  1. `src/meridian/tracing.py` — a `tracer()` helper returning a no-op tracer when
     observability is disabled.
  2. `ObservabilityConfig` in `config.py`: `enabled: bool = False`, `endpoint: str = ""`,
     `service_name: str = "meridian"`.
  3. Wrap `run_task` / `run_trial` in spans carrying `run_id`, `task_slug`, `trial_index`,
     `outcome`, `attempts`.
  4. Add `opentelemetry-sdk` as an **optional** dependency group.
- **MIRROR**: `config.py`'s frozen-model pattern (`model_config = _FROZEN`) and `StrEnum`
  style.
- **IMPORTS**: `from opentelemetry import trace` — guarded, inside the function.
- **GOTCHA**: A span export failure must **never** fail a trial. Wrap export in
  `contextlib.suppress(Exception)`. An observability feature that can fail a run is worse
  than no observability.
- **GOTCHA**: Do not put trial durations as floats into anything hashed. Spans are outside
  the manifest — keep them that way (Rule 4).
- **VALIDATE**: `uv run pytest -m unit -k tracing` with an in-memory span exporter asserting
  span names and attributes; `make check` clean with the optional dep absent.

### Task 12: Documentation and final verification

- **ACTION**: Update the runbook and re-verify the whole DoD.
- **IMPLEMENT**:
  1. `docs/runbook.md`: new entries — "A trial failed and the reason is a memory limit"
     (why it is `fail`, not a harness error) and "A task was quarantined" (what variance
     triggered it, how to un-quarantine).
  2. `README.md`: self-eval table 5/5; note the suite is 12 tasks.
  3. `docs/demo/README.md`: regenerate captured output at 12 tasks.
- **MIRROR**: The runbook's existing per-entry shape — symptom, how to recognise it, what to
  do — across its 17 current entries.
- **IMPORTS**: n/a.
- **GOTCHA**: §11.5 requires a runbook entry for each capability's *most likely* failure.
  Quarantine's is "a task I care about vanished from the aggregate."
- **VALIDATE**: Walk §17 line by line (checklist below).

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `reference_key` is stable | same `(sample_id, cfg_hash)` twice | identical key | no |
| `reference_key` varies on sample | `s1` vs `s2` | different keys | no |
| `reference_key` varies on config | same sample, 2 cfg hashes | different keys | no |
| `judge_score_key` varies on metric | metric `a` vs `b` | different keys | no |
| `judge_score_key` varies on output | 2 output hashes | different keys | no |
| epoch bump invalidates | `CACHE_EPOCH` patched | all keys change | yes |
| key namespaces never collide | same `sample_id` both fns | different keys | yes |
| `pass_rate_variance` of constant | `[0.5, 0.5, 0.5]` | `0.0` | yes |
| `pass_rate_variance` of alternating | `[0.0, 1.0, 0.0]` | high | yes |
| `should_quarantine` thin evidence | 2 runs, any variance | `False` | yes |
| `should_quarantine` stable task | `[0.8, 0.82, 0.79]` | `False` | no |
| `should_quarantine` flaky task | `[0.0, 1.0, 0.0]` | `True` | no |
| quarantined task excluded | run with 1 quarantined | absent from aggregate, named in output | yes |
| tracing no-op when disabled | disabled config | no exporter, no raise | yes |

### Integration / E2E Tests

| Test | Asserts |
|---|---|
| `test_an_oom_is_the_agents_failure_and_is_not_retried` | `FAIL`, detail names memory, `attempts == 1` |
| `test_a_missing_image_is_a_harness_error_not_an_agent_failure` | `HARNESS_ERROR`, `attempts == 2` |
| `test_an_unreachable_proxy_is_a_harness_error_not_an_agent_failure` | `HARNESS_ERROR`, not `FAIL` |
| `test_a_run_continues_when_one_task_faults` | remaining tasks still graded; run status reports partial |

### Edge Cases Checklist
- [ ] OOM vs timeout both exit 137 — disambiguated by `State.OOMKilled`, timeout checked first
- [ ] `ImageNotFound` is an `APIError` subclass — assert on detail, not just type
- [ ] Proxy-unreachable must not be string-matched from arbitrary agent errors
- [ ] New task totals computed with integer `_discount`, never floats
- [ ] Image digest changes on `seed_db.py` edit — all 12 tasks repinned
- [ ] Quarantine with < 3 runs never fires
- [ ] Span export failure never fails a trial
- [ ] Benchmark records to a throwaway dir, never `fixtures/cassettes/`

---

## Validation Commands

### Static Analysis
```bash
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks
uv run mypy
```
EXPECT: `All checks passed!`, `Success: no issues found`

### Unit Tests
```bash
uv run pytest -m unit
```
EXPECT: all pass (currently 230; expect ~255 after this plan)

### Full Suite
```bash
make check
uv run pytest -m integration    # currently 41; expect ~45
uv run pytest -m e2e            # currently 6
```
EXPECT: no regressions

### Container Hygiene
```bash
docker ps -a --filter "label=meridian.run" -q | wc -l
```
EXPECT: `0`

### Suite Validation
```bash
uv run meridian validate --suite ./suites/checkout-agent
uv run meridian run --suite ./suites/checkout-agent --n 5 --k 3 --proxy-mode replay
```
EXPECT: 12 eval tasks + 2 probes, all digest-pinned; run exits `0`

### Benchmark
```bash
make self-eval
jq '.true_positive_rate, .false_regression_rate' benchmarks/self-eval.json
```
EXPECT: `"rate": 1.0` / `"detected": 5`; false-regression `"rate": 0.0`

### Manual Validation
- [ ] `git status --short` empty after Task 1
- [ ] PR opened from `wp-11-self-eval`, `ci` and `meridian gate` both green
- [ ] Quarantine line visible in the CLI table for a deliberately flaky task
- [ ] `docs/runbook.md` has an entry for OOM and for quarantine

---

## Acceptance Criteria — `docs/HANDOFF.md` §17

- [ ] `make check` green: ruff, `mypy --strict`, unit, integration
- [ ] `meridian run --suite checkout-agent --n 5 --k 3` prints per-task pass@k and pass^k
- [ ] Contamination probe passes with isolation and **fails** without it, both in CI
- [ ] An agent failure is never retried; an infra fault retries exactly once — tested by name
- [ ] Zero containers with a `meridian.run` label survive the integration suite
- [ ] `meridian replay <run-id>` reproduces recorded pass^k at fidelity 1.0 across ≥5 runs
- [ ] The golden manifest hash test exists and passes
- [ ] A PR on a real repository fails the check with a comment naming the flipped task, and
      passes on revert — captured in `docs/demo/`
- [ ] The exit-code contract is tested: only `gate` returns non-zero for a product verdict
- [ ] `benchmarks/self-eval.json` is **committed**, regenerated by CI, and reports
      false-regression rate, true-positive rate on five seeded regressions, and replay fidelity
- [ ] README leads with the self-eval table and explains pass^k with worked arithmetic
- [ ] `docs/runbook.md` has an entry per failure mode from §11.4
- [ ] No LLM judge, no web UI, no multi-tenancy, no second vertical

### Additionally, from this plan
- [ ] All seven §11.4 chaos modes have a named test asserting classification **and** that the
      run reports partial results
- [ ] §8.6 cache keys ship as tested pure functions with no storage
- [ ] Flaky-task quarantine excludes visibly
- [ ] OTel spans emit, and cannot fail a trial

## Completion Checklist
- [ ] Code follows the discovered patterns above
- [ ] `HarnessFault` used for harness faults; no bare raises
- [ ] `src/meridian/cache/` has no I/O and no Docker imports (`CLAUDE.md` layout rule)
- [ ] Every new function in `stats/`, `gate/`, `manifest/`, `grading/graders/`, `cache/` has a
      passing **and** a failing case (§11.2)
- [ ] No floats in anything hashed (Rule 4)
- [ ] No hardcoded digests — repin via `make images`
- [ ] Documentation updated
- [ ] No scope additions beyond this plan

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Adding 5 tasks still leaves `p > 0.05` | Medium | High — Task 8 fails | All 5 chosen to be rounding-sensitive, taking 2/7 → 7/12. If short, add more rounding-sensitive tasks; **never** loosen the threshold. |
| `seed_db.py` edit invalidates every pinned digest | High | Medium | Expected and handled by `make images --write-ref --update-suite`. Never hand-edit digests. |
| OOM misdetected as timeout (both exit 137) | High | High | Check `timed_out` first; rely on `State.OOMKilled` after `reload()`, never on the exit code. |
| Task 6 exposes a real defect and grows in scope | Medium | Medium | It is a genuine bug (harness fault reported as agent regression). Fix narrowly via an adapter-level error kind; do not string-match. |
| Re-recording 12 cassettes overwrites committed fixtures | Medium | High | Always record via `--cassettes <tmp>`; the WP-11 fix makes recording *replace* tapes. |
| Quarantine touches scheduler, report, and store at once | Medium | Medium | Keep the decision pure in `suites/quarantine.py`; wire at the CLI layer only. |
| Self-eval wall-clock grows past CI limits | Medium | Low | `self-eval.yml` already sets `timeout-minutes: 300`; raise `stable_runs` input if needed. |

## Notes

**Verified state at plan time** (all commands run, not assumed):
`ruff` clean · `mypy --strict` clean over 68 files · 230 unit + 41 integration + 6 e2e all
pass · full `meridian run --n 5 --k 3` exits 0 with a manifest hash · 0 leaked containers or
volumes · 0 TODO/FIXME/NotImplementedError in `src/`, `benchmarks/`, `fixtures/` · demo PR #12
confirmed real and CLOSED via `gh`.

**Chaos coverage today** — covered: daemon-unavailable (`test_infra_fault_is_retried_once`,
`test_persistent_infra_fault_is_a_harness_error`), agent hang (`test_timeout_kills_the_container`),
grader exception (`test_container_removed_on_grader_exception`,
`test_an_unevaluatable_assertion_is_a_harness_error`), Postgres loss
(`test_an_unreachable_database_is_not_fatal_to_a_run`). Missing: image pull, OOM, proxy
unreachable — Tasks 4, 5, 6.

**Why the TPR miss is a suite-size problem, not a gate problem** — computed from the seeds:
only orders 1042 (4999) and 1046 (7440) invoice a non-whole-dollar total, so
`(total_cents // 100) * 100` moves just 2 of 7 tasks. The paired test resamples *tasks*
(Rule: never resample trials), so 2 movers cannot clear `p=0.05`. Task 7 raises that to 7 of
12. This is the README's own prescribed fix — "more tasks rather than a looser threshold" —
and it is why growing the suite was chosen over raising `n`.

**Deliberate ordering** — Task 1 first so `main` is never behind. Tasks 2–6 (chaos) are
independent of Tasks 7–8 (suite) and can proceed in parallel. Task 7 must precede Task 8;
Task 8 must precede the README edit in Task 12. Tasks 9–11 are independent of everything else.
