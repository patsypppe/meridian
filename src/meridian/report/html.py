"""A static HTML run report.

Self-contained: no fetches, no fonts, no scripts. A report that needs the network
to render is a report you cannot open from a CI artifact on a plane, which is
when you most want it.

The visual job is one comparison: `pass@k` and `pass^k` on the same row, with the
gap between them drawn rather than described.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from meridian.models.run import Outcome, RunResult, TaskResult
from meridian.stats.bootstrap import bootstrap_ci
from meridian.stats.passk import pass_at_k, pass_hat_k

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;
  --surface: #ffffff;
  --border: #e3e1dc;
  --ink: #1b1a17;
  --muted: #6f6b63;
  --accent: #1c5d4a;
  --warn: #8a5a12;
  --bad: #a3302a;
  --track: #eceae5;
  --radius: 10px;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Helvetica, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16151300; --surface: #1e1d1a; --border: #33312c; --ink: #f0eee9;
    --muted: #a19c92; --accent: #5fc0a2; --warn: #d9a441; --bad: #e0736c;
    --track: #2a2823;
  }
  body { background: #161513; }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 3rem 1.5rem 5rem;
  background: var(--bg); color: var(--ink);
  font-family: var(--sans); line-height: 1.55;
  font-size: 15px;
}
main { max-width: 62rem; margin: 0 auto; }
header { margin-bottom: 2.5rem; }
h1 { font-size: 1.55rem; margin: 0 0 .3rem; letter-spacing: -.015em; }
.sub { color: var(--muted); font-family: var(--mono); font-size: .82rem; }
.headline {
  display: flex; flex-wrap: wrap; gap: 1px;
  background: var(--border); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; margin: 2rem 0;
}
.headline > div { background: var(--surface); padding: 1rem 1.25rem; flex: 1 1 9rem; }
.headline .k { color: var(--muted); font-size: .72rem; text-transform: uppercase;
  letter-spacing: .07em; }
.headline .v { font-size: 1.7rem; font-variant-numeric: tabular-nums;
  font-family: var(--mono); margin-top: .2rem; }
.wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); }
table { border-collapse: collapse; width: 100%; min-width: 44rem; }
th, td { padding: .6rem .9rem; text-align: left; border-bottom: 1px solid var(--border); }
th { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); font-weight: 600; }
tbody tr:last-child td { border-bottom: none; }
td.num { font-family: var(--mono); font-variant-numeric: tabular-nums; text-align: right; }
.bar { position: relative; height: .5rem; background: var(--track);
  border-radius: 999px; min-width: 7rem; }
.bar span { position: absolute; inset: 0 auto 0 0; border-radius: 999px; }
.bar .at { background: color-mix(in oklab, var(--accent) 28%, transparent); }
.bar .hat { background: var(--accent); }
.gap { color: var(--muted); font-family: var(--mono); font-size: .78rem; }
.tag { font-family: var(--mono); font-size: .72rem; padding: .1rem .45rem;
  border-radius: 5px; border: 1px solid var(--border); color: var(--muted); }
.tag.bad { color: var(--bad); border-color: color-mix(in oklab, var(--bad) 40%, var(--border)); }
.tag.warn { color: var(--warn); border-color: color-mix(in oklab, var(--warn) 40%, var(--border)); }
h2 { font-size: .95rem; margin: 2.5rem 0 .75rem; letter-spacing: -.01em; }
.note { color: var(--muted); font-size: .85rem; margin: .5rem 0 0; }
.detail { font-family: var(--mono); font-size: .78rem; color: var(--muted); }
dl.pins { display: grid; grid-template-columns: max-content 1fr; gap: .3rem 1.25rem;
  font-family: var(--mono); font-size: .78rem; margin: 0; }
dl.pins dt { color: var(--muted); }
dl.pins dd { margin: 0; overflow-wrap: anywhere; }
"""


def _e(text: object) -> str:
    return html.escape(str(text))


def _row(task: TaskResult, k: int) -> str:
    if task.n == 0:
        return (
            f"<tr><td>{_e(task.task_slug)}</td><td colspan='5' class='detail'>"
            f"no gradeable trials — {task.harness_errors} harness error(s)</td></tr>"
        )
    effective_k = min(k, task.n)
    at_k = pass_at_k(task.n, task.c, effective_k)
    hat_k = pass_hat_k(task.n, task.c, effective_k)
    tag = ""
    if hat_k == 0.0:
        tag = "<span class='tag bad'>never passes</span>"
    elif hat_k < 1.0:
        tag = "<span class='tag warn'>unreliable</span>"
    return (
        f"<tr>"
        f"<td>{_e(task.task_slug)} {tag}</td>"
        f"<td class='num'>{task.c}/{task.n}</td>"
        f"<td class='num'>{at_k:.2f}</td>"
        f"<td class='num'>{hat_k:.2f}</td>"
        f"<td><div class='bar'>"
        f"<span class='at' style='width:{at_k * 100:.1f}%'></span>"
        f"<span class='hat' style='width:{hat_k * 100:.1f}%'></span>"
        f"</div></td>"
        f"<td class='gap'>&minus;{at_k - hat_k:.2f}</td>"
        f"</tr>"
    )


def _failures(run: RunResult, limit: int = 12) -> str:
    rows: list[str] = []
    for task in run.tasks:
        for trial in task.trials:
            if trial.outcome is Outcome.PASS or not trial.detail:
                continue
            rows.append(
                f"<tr><td>{_e(task.task_slug)}</td>"
                f"<td class='num'>{trial.trial_index}</td>"
                f"<td><span class='tag bad'>{_e(trial.outcome)}</span></td>"
                f"<td class='detail'>{_e(trial.detail[:220])}</td></tr>"
            )
            if len(rows) >= limit:
                break
    if not rows:
        return "<p class='note'>Every trial passed.</p>"
    return (
        "<div class='wrap'><table><thead><tr><th>task</th><th>trial</th>"
        "<th>outcome</th><th>why</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render(run: RunResult, *, manifest_hash: str | None = None) -> str:
    """A complete, self-contained HTML document."""
    scored = [t for t in run.tasks if t.n > 0]
    suite_hat = (
        sum(pass_hat_k(t.n, t.c, min(run.k, t.n)) for t in scored) / len(scored) if scored else 0.0
    )
    interval = ""
    if len(scored) > 1:
        lo, hi = bootstrap_ci([(t.n, t.c, run.k) for t in scored], pass_hat_k, iterations=4000)
        interval = f"[{lo:.2f}, {hi:.2f}]"

    status_tag = (
        "<span class='tag warn'>partial — halted at its cost cap</span>"
        if run.status.value == "halted_budget"
        else ""
    )
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    rows = "".join(_row(task, run.k) for task in run.tasks)
    pins = {
        "run": run.run_id,
        "suite": f"{run.suite_slug} v{run.suite_version}",
        "n / k": f"{run.n_requested} / {run.k}",
        "status": run.status.value,
        "harness errors": f"{run.harness_error_rate:.1%}",
        "cost": f"{run.cost_cents}c",
        "wall clock": f"{run.duration_ms / 1000:.1f}s",
    }
    if manifest_hash or run.manifest_hash:
        pins["manifest"] = manifest_hash or run.manifest_hash or ""
    pin_html = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in pins.items())

    excluded = (
        f"<p class='note'>Excluded from the aggregate: "
        f"{_e(', '.join(run.excluded_task_slugs))} — harness self-tests, "
        f"which measure Meridian rather than the agent.</p>"
        if run.excluded_task_slugs
        else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meridian — {_e(run.suite_slug)} {_e(run.run_id)}</title>
<style>{STYLE}</style></head>
<body><main>
<header>
  <h1>{_e(run.suite_slug)} {status_tag}</h1>
  <p class="sub">{_e(run.run_id)} · generated {_e(generated)}</p>
</header>

<div class="headline">
  <div><div class="k">suite pass^{run.k}</div><div class="v">{suite_hat:.2f}</div></div>
  <div><div class="k">95% CI over tasks</div><div class="v">{_e(interval) or "—"}</div></div>
  <div><div class="k">tasks scored</div><div class="v">{len(scored)}</div></div>
  <div><div class="k">cost</div><div class="v">{run.cost_cents}c</div></div>
</div>

<h2>Per task</h2>
<div class="wrap"><table>
<thead><tr><th>task</th><th class="num">passed</th><th class="num">pass@{run.k}</th>
<th class="num">pass^{run.k}</th><th>at k vs hat k</th><th>gap</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="note">The pale bar is pass@{run.k} — at least one of {run.k} attempts works.
The solid bar is pass^{run.k} — all {run.k} work. The gap is the part a mean score
hides.</p>
{excluded}

<h2>Failing trials</h2>
{_failures(run)}

<h2>What this run pinned</h2>
<dl class="pins">{pin_html}</dl>
<p class="note">Reproduce with <code>meridian replay {_e(run.run_id)}</code>.</p>
</main></body></html>
"""
