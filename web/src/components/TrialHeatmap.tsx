import type { Outcome, RunResult } from "../schema";

const LABEL: Record<Outcome, string> = {
  pass: "pass",
  fail: "fail",
  timeout: "timeout",
  harness_error: "harness error",
};

/** A task × trial grid: where the failures are, and whether they cluster. */
export function TrialHeatmap({ run }: { run: RunResult }) {
  const excluded = new Set(run.excluded_task_slugs);
  const tasks = run.tasks.filter((t) => !excluded.has(t.task_slug));
  return (
    <section aria-label="Trial outcomes">
      <h2>Trials</h2>
      <ul className="legend" aria-hidden="true">
        {(Object.keys(LABEL) as Outcome[]).map((outcome) => (
          <li key={outcome}>
            <span className={`cell ${outcome}`} /> {LABEL[outcome]}
          </li>
        ))}
      </ul>
      <div className="heatmap">
        {tasks.map((task) => (
          <div className="heat-row" key={task.task_slug}>
            <span className="heat-label">{task.task_slug}</span>
            <span className="heat-cells">
              {[...task.trials]
                .sort((a, b) => a.trial_index - b.trial_index)
                .map((trial) => {
                  const cause = trial.error_cause ? ` (${trial.error_cause})` : "";
                  const label = `${task.task_slug} trial ${trial.trial_index}: ${LABEL[trial.outcome]}${cause}`;
                  return (
                    <span
                      key={trial.trial_index}
                      className={`cell ${trial.outcome}`}
                      role="img"
                      aria-label={label}
                      title={label}
                    />
                  );
                })}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
