# Meridian dashboard

A static React + TypeScript app for reading Meridian run results. Load a `result.json`
(from `meridian run --out result.json`) and see:

- **Headline numbers**: suite pass^k with a 95% Student-t interval over tasks, trials passed,
  the harness-error rate broken down by cause, and cost / wall-clock time.
- **Per-task table**: pass@k and pass^k, with a note when k had to be lowered because trials
  were lost to harness errors.
- **Trial grid**: every trial of every task, colour-coded, with accessible labels — so you
  can see whether failures cluster.
- **Reliability curve**: pass@k vs pass^k as k grows. The gap between them is what relying
  on retries costs you.
- **Comparison**: load a second run as a baseline for a paired, per-task diff, regressions first.

Everything runs in the browser; a run file is never uploaded anywhere.

## Develop

```bash
cd web
npm ci
npm run dev        # http://localhost:5173
npm test           # Vitest + Testing Library
npm run typecheck  # tsc --strict
npm run build      # production bundle in dist/
```

## Design notes

- **One source of truth for the maths.** `src/stats.ts` re-implements `meridian.stats.passk`
  and `meridian.stats.intervals`. `src/__fixtures__/stats-vectors.json` holds values exported
  from the Python implementation (`uv run python -m benchmarks.export_web_vectors`); the
  TypeScript tests assert agreement, and a Python test fails if the file goes stale. A change
  to either side that moves a number breaks CI instead of silently desynchronising them.
- **Run files are untrusted input.** They are validated with Zod at the boundary; fields added
  after the first release (such as `error_cause`) are optional so older results still load, and
  a file that is not a run result is explained rather than crashing the page.
- **Honest intervals.** With only a handful of tasks a percentile bootstrap undercovers, so the
  dashboard shows a Student-t interval on `tasks − 1` degrees of freedom.
- **The sample data** (`public/sample-*.json`) is reconstructed from the per-task counts of two
  real runs and says so in its `detail` field; regenerate with
  `uv run python -m benchmarks.make_web_samples`.
