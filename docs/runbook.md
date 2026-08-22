# Operator runbook

One entry per failure mode. Each says what you see, what it means, what Meridian
already did about it, and what is left for you.

**Read the exit code first.** It tells you which kind of problem you have before
you read a single log line.

| Code | Meaning | Who is on the hook |
|---|---|---|
| `0` | The command did its job. For `gate`, the merge may proceed. | Nobody |
| `1` | **Only ever `meridian gate` saying FAIL.** The agent got worse. | The change's author |
| `2` | The harness could not do its job. | Whoever owns Meridian |

A `1` is a code review. A `2` is a page. No other command returns `1`, so CI can
never confuse the two.

---

## Recognising the difference between a bad agent and a broken harness

Every trial ends in one of four outcomes, and the split is the whole design.

| Outcome | Statement about | Retried? |
|---|---|---|
| `pass` | the agent | no |
| `fail` | the agent | **no** |
| `timeout` | the agent | **no** |
| `harness_error` | Meridian | yes, exactly once |

If you find yourself wanting to retry a `fail` or a `timeout`, stop. Retrying an
agent failure is how a harness reports numbers better than reality, which is the
one thing this product exists to prevent. The agent had its wall clock and its
budget and did not finish; that is the measurement, not an accident.

Watch `harness error rate` in the run table. It is reported on every run for a
reason: a suite with a 15% harness error rate is not measuring the agent, and the
gate refuses to draw conclusions from it.

---

## 1. The Docker daemon is unavailable

**You see**

```
cannot reach the Docker daemon. Meridian runs every trial in its own container,
so there is no degraded mode to fall back to — start Docker and retry.
```

Exit code `2`, before any trial starts.

**Why there is no fallback.** Rule 1 is that every trial starts from a clean,
isolated environment. A degraded in-process mode would silently violate it and
produce numbers that look like the real thing.

**Do**: start Docker Desktop, `docker version` to confirm the socket at
`/var/run/docker.sock`, retry.

---

## 2. The environment image is missing, or its digest does not exist here

**You see** a harness error naming a digest that no local image matches — most
often right after cloning, or on a CI runner.

**Cause, nine times out of ten: architecture.** An image ID is
architecture-specific. A digest pinned from an arm64 laptop does not exist on an
amd64 runner. This is gotcha #1 in the handoff and it will bite at least once.

**Do**: rebuild and repin, which is what CI does before every Docker-backed job.

```bash
make images     # builds checkout:dev + meridian-proxy:dev and rewrites the pinned digest
```

Note that repinning **changes the suite file**, and that is correct: a different
environment is a different experiment. Commit the new digest, do not paper over
the mismatch.

---

## 3. A trial container runs out of memory

**You see** the trial classified from its exit code, with the container already
gone. Memory limits come from the task's `resources.memory_mb`.

**Do**: decide which of the two it is before touching anything.

- The agent genuinely needs more memory → raise `resources.memory_mb` in the task
  and note *why* in the task file. This changes the environment, so re-record
  cassettes and re-baseline.
- The agent leaks under a specific input → that is a finding about the agent, not
  a limit to raise. Leave it failing.

---

## 4. The agent hangs past its deadline

**You see** `outcome: timeout`, `exceeded the 300s deadline`, and — this is the
part that matters — the container is gone.

**What Meridian did**: killed and removed the **container**. It did not cancel an
asyncio task and call it a timeout. Cancelling the future leaves the container
running, still burning tokens, still holding its volume; the teardown is in a
`finally` and is unconditional.

**Do**: nothing operationally. A `timeout` is an agent result and is never
retried. If every trial of a task times out, read the task's `timeout_seconds`
against what the agent actually needs — but treat raising it as a change to the
experiment.

---

## 5. Containers or volumes were left behind

**You see** `docker ps -a` full of `meridian-trial-*`, or disk filling up. Usually
after an interrupted run.

**Do**:

```bash
make sweep      # uv run meridian sweep — removes containers and volumes by run label
```

Every trial container carries a run label precisely so a sweeper can find it. The
sweep is safe to run at any time; it only touches labelled resources.

---

## 6. The proxy is unreachable

**You see** every trial failing with `model call failed:` in the agent's own error
text, or an entrypoint failure before that.

**Cause**: the proxy container did not come up, or the trial container is not on
its network. The agent has no credential of its own and no route to the internet —
by design — so if the proxy is down there is nowhere else for the call to go.

**Do**: confirm the proxy image exists (`make images`), then re-run. If it
reproduces, run with `--proxy-mode replay` to take the network out of the picture
entirely and confirm the rest of the pipeline is healthy.

---

## 7. Replay misses the cassette

**You see** one of these, on a replay or a `--proxy-mode replay` gate:

```
<task> trial 2 call 4 sent a request hashing to sha256:abc… but sha256:def… was
recorded; the prompt or model configuration changed
```

```
<task> trial 2 made 7 model calls but only 6 were recorded; the agent's
behaviour changed since recording
```

Both arrive as a `424` from the proxy, and **this is the cassette working, not
breaking.** Replay fails closed: an unrecorded or out-of-order request is an
error, never a live call. A cassette that quietly fell through to the network
would guarantee nothing.

**Do**: the agent changed since the recording, so re-record.

```bash
uv run meridian run --suite ./suites/checkout-agent --n 5 --k 3 --proxy-mode record
```

Recording **replaces** the tapes a run touches. It does not append to them — two
record passes over one directory would otherwise leave the first take in front of
the second, and replay, which reads a tape from the start, would serve the older
agent's calls forever.

**In the gate**, these show up as `stale cassette rate`. Stale-cassette failures
look exactly like a regression and are not one, so past a threshold the gate
returns **INCONCLUSIVE** rather than FAIL, and tells you to re-record or to gate
with `--proxy-mode record`.

---

## 8. A grader raises

**You see** `harness_error` with `grader raised KeyError: 'total_cents'` — **not**
a `fail`.

**Why this classification is load-bearing.** A grader exception means *the
assertion* is broken, not the agent. Calling it a failure makes a broken
assertion look like a regression and sends you off debugging the wrong system,
usually for an afternoon.

**Do**: fix the assertion. The trial's extracted state is what the grader ran
against; reproduce against it directly rather than re-running the agent.

---

## 9. A trial exceeds its token or cost budget

**You see** the agent's own error text carrying a 429 from the proxy:

```
happy-path trial 3 exceeded its max_tokens budget: 61500 used against a limit of 60000
```

**What Meridian did**: refused the call at the proxy. The budget is enforced where
the spend happens, so a runaway agent cannot outrun its limit between checks. The
trial is an agent failure — it had its budget and did not finish inside it.

**Do**: if the limit is genuinely too tight, raise `limits.max_tokens` or
`limits.budget_cents` in the task and re-baseline. If it is not, you have found a
real regression in agent efficiency.

---

## 10. Postgres is unreachable, or drops mid-run

**You see** a run that completed and printed its table, followed by a storage
error — or `meridian history` returning nothing.

**Design note**: the run's results are computed before they are stored. Losing
the database loses the *archive*, not the measurement. The run does not abort and
partial results are still reported.

**Do**:

```bash
make db         # docker compose up -d postgres && alembic upgrade head
```

Then re-run. If the schema is the problem, the migrations are the source of truth
and there is a test that applies them to an empty database and diffs the result —
run `make check` before suspecting drift.

---

## 11. The gate FAILs and you think it is wrong

**You see** exit `1` and a PR comment naming which tasks flipped.

Before overriding, check the comment's own numbers in this order:

1. **Harness error rate.** Above threshold, the gate should have said
   INCONCLUSIVE. If it said FAIL, the errors are not the explanation.
2. **Stale cassette rate.** High means the recording predates the agent — re-record
   (entry 7) and re-run before believing the drop.
3. **Which tasks flipped.** "Now fails every trial" is a much stronger signal than
   a fractional move. A task going 0.4 → 0.0 is a real behavioural change.
4. **The p-value.** The gate requires significance, so a FAIL means the drop was
   not distinguishable from noise only by chance at the configured level.

If all four look right, the gate is right. The comment carries the head run id and
manifest hash; `meridian replay <run-id>` reproduces it exactly.

---

## 12. The gate FAILs because there is no baseline

**It does not.** The verdict is PASS with `no baseline for this commit; recording
head as the new baseline`.

This is deliberate. A gate that fails on day one gets disabled on day two.

**But**: if you expected a baseline and there is not one, the usual cause is a
shallow clone in CI — merge-base resolution silently returns nothing. Check for
`fetch-depth: 0` on the checkout step.

---

## 13. `pass@k` and `pass^k` disagree and you think one is broken

They are supposed to disagree. `pass@3 = 1.00` beside `pass^3 = 0.40` means: this
agent solved the task at least once in three attempts, and would solve it three
times out of three only 40% of the time.

`pass^k = C(c,k)/C(n,k)`, never `(c/n)**k` — a different estimator that will
disagree with the definition in the README.

The reported number is `pass^k`, because "works if you retry it" is not a
statement anybody can ship on.

---

## 14. The contamination probe passes when it should fail

**You see** `contamination-probe: pass` under `--unsafe-shared-env`.

**This is the most serious failure in this document.** The probe exists to make
the isolation claim falsifiable. If it cannot fail, it proves nothing, and Rule 1
is no longer evidenced by anything.

**Do**: stop and read `src/meridian/runtime/isolation.py`. Every container
security option lives in that one file specifically so that this audit is one file
long. Both directions are asserted in CI and in `benchmarks/self-eval.json`; treat
a green passing direction with a broken failing direction as a red build.

---

## 15. You changed proxy behaviour and nothing changed

**You see** an edit to anything under `src/meridian/runtime/proxy/` having no
effect at all: the old behaviour persists, your new log line never appears, and
the fix looks wrong when it is not.

**Cause.** The proxy does not run from your working tree. It runs inside the
`meridian-proxy:dev` **image**, which carries a copy of that source taken at build
time. Until you rebuild, every run exercises the code as it was when the image was
built.

**Do**:

```bash
uv run meridian snapshot build ./envs/proxy --tag meridian-proxy:dev --context .
# or `make images`, which also rebuilds the environment and repins the suite
```

`docker inspect meridian-proxy:dev --format '{{.Created}}'` against the mtime of
the file you edited settles it in one line. The same applies to the environment
image and anything under `envs/` — editing `envs/checkout/seed/` on the host
changes nothing a trial sees until the image is rebuilt and repinned.

---

## 16. `make e2e` or a gate run leaves the cassettes modified

**Expected, for a gate run.** `meridian gate` records by default, and recording
replaces the tapes a run touches. If you meant to gate offline against the
committed recordings, pass `--proxy-mode replay`; if you meant to refresh them,
commit the result.

**Not expected from the test suite.** The e2e gate test records into a temporary
directory precisely so that `make e2e` does not rewrite the committed artifact —
briefly with a *degraded* agent's calls, at that. If e2e starts dirtying
`fixtures/cassettes/`, a `--cassettes` override has been dropped.

---

## 17. The self-eval will not regenerate

**You see** `python -m benchmarks.self_eval` failing, or reporting a true-positive
rate below 1.0.

**First check whether the regression was applied at all.**

```
RegressionNotApplied: the seeded edit to .../tools.py changed nothing
```

That guard exists because the benchmark's quietest failure is an anchor that
drifted: the edit no-ops, the run scores exactly like the baseline, and the report
records a regression the gate "missed" that was never applied.

**The system under test is `fixtures/`**, which is mounted into the trial
container. It is **not** `envs/checkout/seed/`, which is baked into the image and
pinned by digest — editing that on the host changes nothing a trial ever sees.

A genuine miss with the regression correctly applied is a real result. Report it;
do not tune the tolerance to make it go away. A benchmark that tunes the thing it
is measuring measures nothing.

---

## 18. A trial failed and the reason names a memory limit

```
expired-coupon[2] fail: exceeded the 2048MB memory limit and was killed by the cgroup
```

This is a `fail`, not a `harness_error`, and it is not retried. The ceiling comes
from the task's `environment.resources.memory_mb`, so an agent that walks into it
has failed under the budget it was given — the same reasoning that makes a
timeout a statement about the agent.

**If the limit is genuinely too low**, raise `memory_mb` in the task and re-run.
That changes the task definition, so it changes the manifest, and the comparison
against an older baseline is no longer apples-to-apples. Re-baseline deliberately
rather than comparing across the change.

**Do not** confuse this with a timeout. Both surface as exit 137, which is why
the classification reads `State.OOMKilled` from the daemon instead. If you see a
timeout reported as an OOM, that ordering has regressed and
`test_an_oom_is_the_agents_failure_and_is_not_retried` should have caught it.

Note that swap is disabled for trial containers (`memswap_limit == mem_limit`).
Without that, Docker allows twice the requested memory and the ceiling stops
meaning what the task says.

---

## 19. Every trial failed at once and the agent looks catastrophically broken

Check the proxy before you believe it.

An agent that cannot reach the model fails every trial of every task, and the
gate reports the largest regression it has ever seen. The failure mode that costs
an afternoon is reverting a change that was never the cause, watching the revert
not help, and only then looking at the harness.

Meridian classifies this for you: when a trial reports an error *and* the proxy
does not answer its health check, the trial is a `harness_error` rather than a
`fail`, and the gate returns INCONCLUSIVE rather than FAIL. So:

```
harness error rate: 100.0%
```

is the signal. A run that executed nothing reports that it executed nothing.

If instead you see a clean sweep of `fail` with agent-side errors, the proxy was
answering and the failures are real.

---

## 20. `meridian suite audit` says a task can be passed without doing the work

```
weak-task passes for the empty-scaffold agent, which creates the output
directory and leaves it empty
```

The task's assertions are satisfiable without solving it, so every pass it has
ever recorded is worth less than it looked. The usual cause is an assertion that
checks a path **exists**, or that a document **parses**, rather than checking what
it says.

Fix the assertion, not the adversary:

- `file_exists` on a directory → assert on a specific file, or use
  `json_path_equals` on its contents.
- `file_exists` on a document the agent writes → assert a value inside it.
- A `json_path_matches` pattern loose enough to match invented values → tighten it,
  or pin the value with `json_path_equals`.

Re-run the audit after the fix. A clean audit does not prove the assertions are
*right* — only that they are not trivially satisfiable, which is the floor.
