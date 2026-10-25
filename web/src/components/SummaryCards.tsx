import type { RunSummary } from "../analysis";
import { fmt, fmtCost, fmtDuration, fmtPercent } from "../format";

export function SummaryCards({ summary }: { summary: RunSummary }) {
  const interval = summary.suiteInterval
    ? `95% CI [${fmt(summary.suiteInterval[0])}, ${fmt(summary.suiteInterval[1])}] (t, df=${
        summary.tasks.filter((t) => t.passHatK !== null).length - 1
      })`
    : "needs at least 2 gradeable tasks for an interval";
  return (
    <section aria-label="Run summary" className="cards">
      <article className="card">
        <h3>{`Suite pass^${summary.k}`}</h3>
        <p className="big" data-testid="suite-score">
          {fmt(summary.suitePassHatK)}
        </p>
        <p className="muted">{interval}</p>
      </article>
      <article className="card">
        <h3>Trials passed</h3>
        <p className="big">
          {summary.passedTrials} / {summary.totalTrials}
        </p>
        <p className="muted">{summary.tasks.length} scored tasks</p>
      </article>
      <article className="card">
        <h3>Harness errors</h3>
        <p className="big" data-testid="harness-rate">
          {fmtPercent(summary.harnessErrorRate)}
        </p>
        <p className="muted">
          {summary.harnessErrorsByCause.length === 0
            ? "none"
            : summary.harnessErrorsByCause.map(([cause, n]) => `${cause}: ${n}`).join(", ")}
        </p>
      </article>
      <article className="card">
        <h3>Cost · time</h3>
        <p className="big">{fmtCost(summary.costCents)}</p>
        <p className="muted">{fmtDuration(summary.durationMs)} wall clock</p>
      </article>
    </section>
  );
}
