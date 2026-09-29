// §03, §05, §06 and the colophon: the reference run, the probe, the audit and
// the self-measurement, all read straight from showcase.json.

import { bind, clear, el, fmt3, setText, svg } from "./dom.js";
import { comb, passHatK } from "./stats.js";

// ---------- §03 pass^k ----------

function workedExample(task, k) {
  const exact = passHatK(task.n, task.c, k);
  const passAt = task.pass_at_k;
  return [
    `${task.task}: c = ${task.c} of n = ${task.n} trials passed`,
    `pass^${k} = C(${task.c},${k}) / C(${task.n},${k}) = ${comb(task.c, k)} / ${comb(task.n, k)} = ${fmt3(exact)}`,
    `pass@${k} = ${fmt3(passAt)} · mean = ${fmt3(task.mean)} · (c/n)^${k} would say ${fmt3((task.c / task.n) ** k)}`,
  ];
}

function setWorked(lines) {
  const node = clear(bind("worked"));
  lines.forEach((line, i) => {
    if (i) node.append(el("br"));
    node.append(line);
  });
}

export function mountPassK(data) {
  const run = data.run;
  const k = run.k;
  const table = bind("grid");
  const head = el("thead", {}, [
    el("tr", {}, [
      el("th", { scope: "col", text: "task" }),
      el("th", { scope: "col", text: `trials (n=${run.n})` }),
      el("th", { scope: "col", class: "col-hide-s", text: "mean" }),
      el("th", { scope: "col", text: `pass@${k}` }),
      el("th", { scope: "col", text: `pass^${k}` }),
    ]),
  ]);
  const body = el("tbody");
  const scored = run.tasks.filter((t) => !run.excluded.includes(t.task));
  for (const task of scored) {
    const cells = el("span", { class: "cells", role: "img", "aria-label": `${task.c} of ${task.n} trials passed` });
    for (const trial of task.trials) {
      cells.append(el("span", { class: `cell ${trial.outcome === "pass" ? "pass" : "fail"}`, title: `trial ${trial.index}: ${trial.outcome}${trial.detail ? ` — ${trial.detail}` : ""}` }));
    }
    const row = el("tr", { tabindex: 0 }, [
      el("th", { scope: "row", class: "mono", text: task.task }),
      el("td", {}, [cells]),
      el("td", { class: "col-hide-s", text: fmt3(task.mean) }),
      el("td", { text: fmt3(task.pass_at_k) }),
      el("td", { class: task.pass_hat_k < 1 ? "t-fail" : "t-pass", text: fmt3(task.pass_hat_k) }),
    ]);
    const show = () => setWorked(workedExample(task, k));
    row.addEventListener("pointerenter", show);
    row.addEventListener("focus", show);
    body.append(row);
  }
  const foot = el("tfoot", {}, [
    el("tr", {}, [
      el("td", { text: "suite (unweighted over tasks)" }),
      el("td", {}),
      el("td", { class: "col-hide-s", text: fmt3(run.suite_mean) }),
      el("td", { text: "" }),
      el("td", { class: "t-fail", text: fmt3(run.suite_pass_hat_k) }),
    ]),
  ]);
  table.append(head, body, foot);

  setText("mean", `${(run.suite_mean * 100).toFixed(1)}%`);
  setText("passhat", fmt3(run.suite_pass_hat_k));
  setText("ci", `[${fmt3(run.ci95[0])}, ${fmt3(run.ci95[1])}]`);
  setText(
    "grid-caption",
    `Run ${run.run_id} · status ${run.status} · harness errors ${(run.harness_error_rate * 100).toFixed(1)}% · wall clock ${(run.duration_ms / 1000).toFixed(1)}s · ${run.cost_cents}¢ metered by the proxy's budget ledger, replayed from cassettes, so no real API spend.`,
  );
  const example = scored.find((t) => t.c < t.n && t.c >= k) || scored[0];
  setWorked(workedExample(example, k));
}

// ---------- §05 probe + audit ----------

function volumeSketch(shared) {
  const root = svg("svg", { class: "volumes", viewBox: "0 0 300 92", role: "img", "aria-label": shared ? "Both trials mount one shared volume; the marker survives." : "Each trial mounts its own volume; the marker is gone." });
  const ctr = (x, label) => {
    root.append(svg("rect", { class: "ctr", x, y: 4, width: 118, height: 26, rx: 2 }));
    root.append(svg("text", { x: x + 59, y: 21, "text-anchor": "middle", text: label }));
  };
  ctr(14, "writer trial");
  ctr(168, "probe trial");
  root.append(svg("line", { class: "wire", x1: 73, y1: 30, x2: 73, y2: 52 }));
  root.append(svg("line", { class: "wire", x1: 227, y1: 30, x2: 227, y2: 52 }));
  if (shared) {
    root.append(svg("rect", { class: "vol shared", x: 14, y: 52, width: 272, height: 30, rx: 3 }));
    root.append(svg("text", { x: 160, y: 71, "text-anchor": "middle", text: "one shared /work volume" }));
  } else {
    root.append(svg("rect", { class: "vol", x: 14, y: 52, width: 118, height: 30, rx: 3 }));
    root.append(svg("rect", { class: "vol", x: 168, y: 52, width: 118, height: 30, rx: 3 }));
    root.append(svg("text", { x: 82, y: 71, "text-anchor": "middle", text: "/work · vol A" }));
    root.append(svg("text", { x: 227, y: 71, "text-anchor": "middle", text: "/work · vol B" }));
  }
  // The marker the writer leaves behind, always in the writer's /work.
  root.append(svg("rect", { class: "marker", x: 22, y: 58, width: 8, height: 18, rx: 1 }));
  return root;
}

function probeColumn(direction, title, cmd, shared) {
  const steps = el("ul", { class: "probe-steps" });
  for (const trial of direction.trials) {
    const ok = trial.outcome === "pass";
    steps.append(el("li", {}, [el("span", { text: trial.task }), el("span", { class: ok ? "t-pass" : "t-fail", text: trial.outcome })]));
  }
  const probe = direction.trials.find((t) => t.task === "contamination-probe");
  const detail = probe.outcome === "pass"
    ? el("p", { class: "probe-detail ok", text: "the marker written by the previous trial is not reachable" })
    : el("p", { class: "probe-detail", text: probe.detail });
  return el("div", { class: "probe-col" }, [
    el("h3", { text: title }),
    el("p", { class: "cmd", text: cmd }),
    steps,
    detail,
    volumeSketch(shared),
  ]);
}

const ADVERSARY_NOTES = {
  null: "exits successfully having touched nothing",
  "empty-scaffold": "creates the output directory and leaves it empty",
  "plausible-garbage": "writes well-formed JSON with invented values",
};

export function mountProbe(data) {
  const container = clear(bind("probe"));
  container.append(
    probeColumn(data.probe.isolated, "Isolation on (default)", "IsolationPolicy()  ·  one volume per trial", false),
    probeColumn(data.probe.shared, "--unsafe-shared-env", "IsolationPolicy(unsafe_shared_env=True)  ·  rejected in gate mode", true),
  );

  const audit = data.audit;
  const list = clear(bind("adversaries"));
  const weak = new Map();
  for (const f of audit.findings) weak.set(f.adversary, (weak.get(f.adversary) || 0) + 1);
  for (const name of audit.adversaries) {
    const count = weak.get(name) || 0;
    list.append(
      el("li", {}, [
        el("code", { text: name }),
        el("span", { text: ADVERSARY_NOTES[name] || "" }),
        el("strong", { class: count ? "t-fail" : "t-pass", text: count ? `${count} passed` : `0 / ${audit.audited_tasks.length} passed` }),
      ]),
    );
  }
  setText(
    "audit-summary",
    audit.weak_task_count === 0
      ? `No task in the reference suite passed for an agent that did no work: ${audit.adversaries.length} adversaries × ${audit.audited_tasks.length} tasks. That is the floor, not the ceiling.`
      : `${audit.weak_task_count} task(s) can be passed without doing the work.`,
  );
}

// ---------- §06 self-measurement ----------

const SEEDED = [
  ["remove-expiry-instruction", "planner no longer says to check coupon expiry"],
  ["truncate-the-context", "a context guard drops all but the last two messages"],
  ["remove-a-tool", "query_coupon is gone"],
  ["starve-the-token-budget", "max_tokens drops below what any task needs"],
  ["break-the-invoice-rounding", "write_invoice rounds totals down to a dollar"],
];

function measure(title, figure, extra, text) {
  return el("article", { class: "measure" }, [el("h3", { text: title }), figure, extra, el("p", { text: text })]);
}

export function mountSelfEval(data) {
  const se = data.self_eval;
  const fr = se.false_regression_rate;
  const tp = se.true_positive_rate;
  const rf = se.replay_fidelity;
  const missed = new Map(tp.missed.map((m) => [m.name, m]));

  setText("se-false", `${fr.false_fails}/${fr.runs}`);
  setText("se-true", `${tp.detected}/${tp.seeded_regressions}`);
  setText("se-replay", `${rf.exact}/${rf.manifests}`);

  const ticks = el("ul", { class: "ticks", "aria-label": `${fr.runs} gate runs, ${fr.false_fails} false FAILs` });
  for (let i = 0; i < fr.runs; i += 1) ticks.append(el("li", { class: i < fr.false_fails ? "hit-fail" : "" }));

  const seeded = el("ul", { class: "seeded" });
  for (const [name, what] of SEEDED) {
    const miss = missed.get(name);
    seeded.append(el("li", {}, [el("span", { text: `${name} — ${what}` }), el("span", { class: miss ? "t-warn" : "t-pass", text: miss ? miss.verdict : "caught" })]));
  }

  const replayTicks = el("ul", { class: "ticks", "aria-label": `${rf.exact} of ${rf.manifests} replays exact` });
  for (let i = 0; i < rf.manifests; i += 1) replayTicks.append(el("li", { class: i < rf.exact ? "" : "hit-fail" }));

  const fig = (value, of) => el("div", { class: "figure" }, [value, el("small", { text: ` / ${of}` })]);
  const container = clear(bind("selfeval"));
  container.append(
    measure("False-regression rate", fig(String(fr.false_fails), fr.runs), ticks, "The unchanged agent, gated repeatedly against a fixed baseline. Every FAIL here would be the gate crying wolf."),
    measure("True-positive rate", fig(String(tp.detected), tp.seeded_regressions), seeded, "Five regressions of known shape, seeded into the system under test, never into the harness or its thresholds."),
    measure("Replay fidelity", fig(String(rf.exact), rf.manifests), replayTicks, "Archived runs re-materialized from their manifests alone. Drift would mean an input the manifest does not pin."),
    measure(
      "Contamination probe",
      el("div", { class: "figure" }, [el("span", { class: "t-pass", text: se.contamination_probe.with_isolation }), el("small", { text: " / " }), el("span", { class: "t-fail", text: se.contamination_probe.without_isolation })]),
      null,
      `n=${se.config.n} · k=${se.config.k} · tolerance ${se.config.tolerance} · baseline pass^3 ${fmt3(se.baseline_suite_pass_hat_k)} · mean run ${(se.run_duration_ms.mean / 1000).toFixed(1)}s · generated ${se.generated_at.slice(0, 10)} · Meridian ${se.meridian_version}`,
    ),
  );

  const miss = tp.missed[0];
  const rounding = data.gates.find((g) => g.name === "invoice-rounding");
  if (miss && rounding) {
    setText(
      "miss",
      `${miss.name} returned ${miss.verdict}: “${miss.reason}”. The same seeded bug, regenerated for this page, dropped suite pass^3 by ${fmt3(rounding.drop)} over ${rounding.comparable_tasks} tasks (p = ${fmt3(rounding.p_value)}) against a minimum detectable effect of ${fmt3(rounding.mde)}. Seven tasks cannot resolve it and no threshold can fix that; about ${rounding.required_tasks_for_tolerance} tasks could. It is reported here instead of tuned away.`,
    );
  } else if (miss) {
    setText("miss", `${miss.name} returned ${miss.verdict}: “${miss.reason}”.`);
  }
}

// ---------- colophon ----------

export function mountColophon(data) {
  const dl = bind("colophon");
  const row = (term, value) => dl.append(el("dt", { text: term }), el("dd", {}, [value]));
  row("Generated", `${data.generated_at} · Meridian ${data.meridian_version}`);
  row("Measured at", el("a", { href: `https://github.com/patsypppe/meridian/commit/${data.git_head}`, text: data.git_head.slice(0, 12) }));
  row("Reference run", `${data.run.run_id} · manifest ${data.run.manifest ? `${data.run.manifest.slice(0, 23)}…` : "—"}`);
  row("Gate runs", data.gates.map((g) => `${g.name}: ${g.head_run} vs ${g.baseline_run}`).join(" · "));
  row("Regenerate", el("code", { text: "bash site/data/regenerate.sh" }));
}
