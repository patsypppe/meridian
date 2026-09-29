// §01 — the gate verdict instrument: picker, banner, per-task chart, decision
// trace, suite ruler and the bootstrap that decided significance.

import { bootstrap, dumbbell, ruler } from "./charts.js";
import { bind, clear, el, fmt3, fmtPct, setText } from "./dom.js";

export const VERDICT_LABEL = {
  pass: "PASS",
  pass_with_warning: "PASS · WARNING",
  fail: "FAIL",
  inconclusive: "INCONCLUSIVE",
};

const SHORT_TITLE = {
  "degraded-prompt": "Prompt loses a sentence",
  "invoice-rounding": "Invoice rounded down",
  unchanged: "No change",
};

// The checks in gate/decide.py, in order, with where this run landed on each.
export function traceSteps(g) {
  const pct = (v) => fmtPct(v, 1);
  const steps = [
    { text: "run completed within its cost cap", val: g.run_status, state: "ok" },
    { text: `harness errors ≤ ${pct(g.max_harness_error_rate)}`, val: pct(g.harness_error_rate), state: "ok" },
    { text: `stale cassettes ≤ ${pct(g.max_stale_cassette_rate)}`, val: pct(g.stale_cassette_rate), state: "ok" },
    { text: "a comparable baseline exists", val: "yes", state: "ok" },
  ];
  const withinTolerance = g.drop <= g.tolerance;
  steps.push({
    text: `drop ≤ tolerance ${fmt3(g.tolerance)}`,
    val: `${g.drop > 0 ? "−" : ""}${fmt3(Math.abs(g.drop))}`,
    state: withinTolerance ? "decided" : "ok",
    mark: withinTolerance ? "→ PASS" : "exceeds",
  });
  if (withinTolerance) {
    steps.push({ text: `paired bootstrap p ≤ ${g.significance_level.toFixed(3)}`, val: "not needed", state: "skipped" });
  } else {
    const significant = g.p_value <= g.significance_level;
    steps.push({
      text: `paired bootstrap p ≤ ${g.significance_level.toFixed(3)}`,
      val: `p = ${fmt3(g.p_value)}`,
      state: "decided",
      mark: significant ? "→ FAIL" : "→ PASS · WARNING",
    });
  }
  return steps;
}

function renderTrace(g) {
  const list = clear(bind("trace"));
  for (const step of traceSteps(g)) {
    const val = el("span", { class: "val" }, [
      step.val,
      step.mark ? el("span", { class: `mark ${markClass(step.mark)}`, text: ` ${step.mark}` }) : null,
    ]);
    list.append(el("li", { "data-state": step.state }, [el("span", { text: step.text }), val]));
  }
}

function markClass(mark) {
  if (mark.includes("FAIL")) return "t-fail";
  if (mark.includes("WARNING")) return "t-warn";
  if (mark.includes("PASS")) return "t-pass";
  return "t-muted";
}

function rulerCaption(g) {
  if (!g.mde) {
    return "Every task moved by exactly the same amount (here: not at all), so there is no spread to estimate sensitivity from. The gate says so rather than quoting a number: “Not estimated … Treat the verdict as directional.”";
  }
  const drop = fmt3(g.drop);
  const inBlind = g.drop < g.mde;
  let outcome;
  if (!inBlind) outcome = "A drop that size is one this run would catch reliably.";
  else if (g.verdict === "fail") {
    outcome = `A drop below the MDE is still caught some of the time, and this one was (p = ${fmt3(g.p_value)}). The MDE is what the run catches reliably, at 80% power, not a hard floor.`;
  } else {
    outcome = `It was seen and reported, but over ${g.comparable_tasks} tasks it could not be told apart from noise (p = ${fmt3(g.p_value)}), so the gate warned instead of blocking.`;
  }
  return (
    `The drop of ${drop} ${inBlind ? "lands inside" : "clears"} the blind zone: with ${g.comparable_tasks} tasks, ` +
    `regressions smaller than ${fmt3(g.mde)} would more often than not go unnoticed. ` +
    `Resolving the ${fmt3(g.tolerance)} tolerance would take about ${g.required_tasks_for_tolerance} comparable tasks. ${outcome}`
  );
}

function bootCaption(g) {
  if (!g.bootstrap) {
    return "The drop never exceeded the tolerance, so the gate did not need a significance test. A paired bootstrap only runs when there is a drop worth testing.";
  }
  const b = g.bootstrap;
  const atZero = Math.round(b.share_at_or_above_zero * b.iterations);
  const verdict = g.p_value <= g.significance_level ? "significant: the gate blocks." : "not significant: the gate warns and lets it through.";
  return (
    `The ${g.comparable_tasks} per-task differences, resampled with replacement ${b.iterations.toLocaleString("en-US")} times (seed ${b.seed}). ` +
    `${atZero.toLocaleString("en-US")} resamples show no regression, so p = ${fmt3(g.p_value)} against α = ${g.significance_level.toFixed(3)}: ${verdict} ` +
    "Resampling tasks, not trials, is deliberate: trials within a task are correlated."
  );
}

export function mountVerdict(data, tip) {
  const picker = clear(bind("picker"));
  const buttons = data.gates.map((g, i) =>
    el(
      "button",
      {
        type: "button",
        role: "tab",
        id: `tab-${g.name}`,
        "aria-controls": "instrument",
        "aria-selected": i === 0 ? "true" : "false",
        tabindex: i === 0 ? 0 : -1,
        "data-verdict": g.verdict,
      },
      [el("span", { class: "dot", "aria-hidden": "true" }), `${SHORT_TITLE[g.name] || g.title} · ${VERDICT_LABEL[g.verdict]}`],
    ),
  );
  buttons.forEach((b) => picker.append(b));

  let current = 0;
  const draw = () => {
    const g = data.gates[current];
    const panelWidth = (name) => Math.max(260, bind(name).clientWidth);
    document.getElementById("instrument").setAttribute("aria-labelledby", `tab-${g.name}`);
    const banner = bind("banner");
    banner.dataset.verdict = g.verdict;
    const stamp = setText("banner-stamp", VERDICT_LABEL[g.verdict]);
    stamp.className = `stamp ${g.verdict}`;
    setText("banner-reason", g.reason);
    const blocks = setText("banner-blocks", g.blocks_merge ? "exit 1 · blocks the merge" : "exit 0 · merge may proceed");
    blocks.className = `blocks ${g.blocks_merge ? "t-fail" : "t-muted"}`;
    setText("change", g.change);
    const flipped = g.flipped.now_always_fails.length + g.flipped.broken.length;
    setText("flip-count", flipped ? `${flipped} task(s) now always fail` : "no task flipped");
    dumbbell(bind("dumbbell"), g, { width: panelWidth("dumbbell"), tip });
    renderTrace(g);
    ruler(bind("ruler"), g, { width: panelWidth("ruler") });
    setText("ruler-caption", rulerCaption(g));
    bootstrap(bind("histogram"), g, { width: panelWidth("histogram") });
    setText("boot-meta", g.bootstrap ? `${g.bootstrap.iterations.toLocaleString("en-US")} resamples · seed ${g.bootstrap.seed}` : "not run");
    setText("boot-caption", bootCaption(g));
    setText("comment", g.comment_markdown);
  };

  const select = (index, focus) => {
    current = (index + buttons.length) % buttons.length;
    buttons.forEach((b, i) => {
      b.setAttribute("aria-selected", i === current ? "true" : "false");
      b.tabIndex = i === current ? 0 : -1;
    });
    if (focus) buttons[current].focus();
    draw();
  };

  buttons.forEach((b, i) => {
    b.addEventListener("click", () => select(i, false));
    b.addEventListener("keydown", (event) => {
      const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (event.key in moves) {
        event.preventDefault();
        select(current + moves[event.key], true);
      } else if (event.key === "Home") {
        event.preventDefault();
        select(0, true);
      } else if (event.key === "End") {
        event.preventDefault();
        select(buttons.length - 1, true);
      }
    });
  });

  draw();
  return { redraw: draw };
}

export function mountSpecimen(data) {
  const g = data.gates.find((gate) => gate.verdict === "fail") || data.gates[0];
  const stamp = setText("specimen-stamp", VERDICT_LABEL[g.verdict]);
  stamp.className = `stamp ${g.verdict}`;
  setText("specimen-reason", g.reason);
  setText("specimen-run", g.head_run);
  const always = g.flipped.now_always_fails;
  setText("specimen-flipped", always.length ? `Now fails every trial: ${always.join(", ")}` : "");
  setText("specimen-sens", g.sensitivity_text);
}
