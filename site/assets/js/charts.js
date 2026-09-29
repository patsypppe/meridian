// Hand-built SVG charts. Each renders at its container's measured width so text
// stays legible at 320px instead of shrinking with a fixed viewBox.

import { clear, fmt3, linear, logScale, svg } from "./dom.js";

const TICKS_01 = [0, 0.2, 0.4, 0.6, 0.8, 1];

function frame(container, width, height, label) {
  clear(container);
  const root = svg("svg", {
    class: "chart",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": label,
  });
  container.append(root);
  return root;
}

function direction(before, after) {
  if (after < before - 1e-9) return "down";
  if (after > before + 1e-9) return "up";
  return "same";
}

// Per-task pass^k, baseline ring → head dot.
export function dumbbell(container, gate, { width, tip }) {
  // Narrow: each task's label sits on its own line above its track.
  const stacked = width < 480;
  const rowH = stacked ? 46 : 34;
  const top = 8;
  const labelW = stacked ? 8 : Math.min(176, Math.max(118, width * 0.36));
  const right = 54;
  const height = top + gate.tasks.length * rowH + 30;
  const x = linear([0, 1], [labelW, width - right]);
  const root = frame(container, width, height, `Per-task pass^3 for ${gate.title}`);
  const ticks = stacked ? [0, 0.5, 1] : TICKS_01;

  for (const t of ticks) {
    root.append(svg("line", { class: "grid", x1: x(t), x2: x(t), y1: top, y2: height - 24 }));
    root.append(svg("text", { x: x(t), y: height - 8, "text-anchor": "middle", text: t.toFixed(1) }));
  }

  const failures = new Map();
  for (const f of gate.failures) if (!failures.has(f.task)) failures.set(f.task, f.detail);
  const alwaysFails = new Set(gate.flipped.now_always_fails);

  gate.tasks.forEach((task, i) => {
    const rowTop = top + i * rowH;
    const cy = stacked ? rowTop + 32 : rowTop + rowH / 2;
    const labelY = stacked ? rowTop + 13 : cy + (alwaysFails.has(task.task) ? -2 : 4);
    const dir = direction(task.baseline, task.head);
    const delta = task.head - task.baseline;
    const lines = [
      task.task,
      `baseline ${task.baseline_c}/${task.n} passed → pass^3 ${fmt3(task.baseline)}`,
      `head     ${task.head_c}/${task.n} passed → pass^3 ${fmt3(task.head)}`,
    ];
    if (failures.has(task.task)) lines.push(`grader: ${failures.get(task.task)}`);
    const row = svg("g", {
      class: "row",
      tabindex: 0,
      role: "listitem",
      "aria-label": `${task.task}: baseline ${fmt3(task.baseline)}, head ${fmt3(task.head)}`,
    });
    row.append(svg("rect", { class: "row-hit", x: 0, y: rowTop + 1, width, height: rowH - 2, rx: 2 }));
    row.append(svg("text", { class: "task-label", x: 0, y: labelY, text: task.task }));
    if (alwaysFails.has(task.task)) {
      const tagX = stacked ? width - 2 : 0;
      row.append(svg("text", { class: "tag-fail", x: tagX, y: stacked ? labelY : cy + 11, "text-anchor": stacked ? "end" : "start", text: stacked ? "now always fails" : "now fails every trial" }));
    }
    if (dir !== "same") {
      row.append(svg("line", { class: `link ${dir}`, x1: x(task.baseline), x2: x(task.head), y1: cy, y2: cy }));
    }
    row.append(svg("circle", { class: "base-dot", cx: x(task.baseline), cy, r: dir === "same" ? 8 : 6 }));
    row.append(svg("circle", { class: `head-dot ${dir}`, cx: x(task.head), cy, r: 5 }));
    row.append(
      svg("text", {
        x: width - 2,
        y: cy + 4,
        "text-anchor": "end",
        class: dir === "down" ? "strong" : "",
        text: dir === "same" ? "±0" : `${delta > 0 ? "+" : "−"}${Math.abs(delta).toFixed(3)}`,
      }),
    );
    const show = () => tip.show(lines, row);
    row.addEventListener("pointerenter", show);
    row.addEventListener("focus", show);
    row.addEventListener("pointerleave", () => tip.hide());
    row.addEventListener("blur", () => tip.hide());
    root.append(row);
  });
  root.setAttribute("role", "list");
  root.setAttribute("aria-label", `Per-task pass^3, baseline to head, ${gate.title}`);
}

// Suite pass^k: baseline, head, the tolerance band and the blind zone below the MDE.
export function ruler(container, gate, { width }) {
  const domain = [0.25, 0.525];
  const pad = 18;
  const height = 150;
  const x = linear(domain, [pad, width - pad]);
  const root = frame(
    container,
    width,
    height,
    `Suite pass^3: baseline ${fmt3(gate.baseline_suite)}, head ${fmt3(gate.head_suite)}, tolerance ${fmt3(gate.tolerance)}` +
      (gate.mde ? `, minimum detectable effect ${fmt3(gate.mde)}` : ""),
  );
  const axisY = 96;
  const bandTop = 42;
  const base = gate.baseline_suite;
  const head = gate.head_suite;

  const defs = svg("defs");
  const pattern = svg("pattern", { id: "hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  pattern.append(svg("line", { class: "hatch-line", x1: 0, y1: 0, x2: 0, y2: 7 }));
  defs.append(pattern);
  root.append(defs);

  if (gate.mde) {
    const edge = Math.max(domain[0], base - gate.mde);
    root.append(svg("rect", { class: "blind", x: x(edge), y: bandTop, width: x(base) - x(edge), height: axisY - bandTop }));
    root.append(svg("line", { class: "blind-edge", x1: x(edge), x2: x(edge), y1: bandTop - 12, y2: axisY }));
    const labelLeft = x(edge) + 5;
    const label = width < 520 ? `MDE ${fmt3(gate.mde)}` : `MDE ${fmt3(gate.mde)}: smaller drops mostly go unseen`;
    root.append(svg("text", { x: labelLeft, y: bandTop - 16, text: label }));
  }
  root.append(svg("rect", { class: "tol-band", x: x(base - gate.tolerance), y: bandTop, width: x(base) - x(base - gate.tolerance), height: axisY - bandTop }));

  root.append(svg("line", { class: "axis-line", x1: pad, x2: width - pad, y1: axisY, y2: axisY }));
  const step = width < 480 ? 0.1 : 0.05;
  for (let t = step === 0.1 ? 0.3 : 0.25; t <= 0.5251; t += step) {
    root.append(svg("line", { class: "axis-line", x1: x(t), x2: x(t), y1: axisY, y2: axisY + 5 }));
    root.append(svg("text", { x: x(t), y: axisY + 18, "text-anchor": "middle", text: t.toFixed(2) }));
  }

  root.append(svg("line", { class: "marker-base", x1: x(base), x2: x(base), y1: 14, y2: axisY }));
  root.append(svg("text", { x: x(base) - 5, y: 16, "text-anchor": "end", class: "strong", text: `baseline ${fmt3(base)}` }));

  const dir = direction(base, head);
  const colour = { down: "var(--fail-fill)", up: "var(--pass-fill)", same: "var(--ink)" }[dir];
  if (dir !== "same") {
    root.append(svg("line", { class: "marker-head", stroke: colour, x1: x(head), x2: x(head), y1: bandTop, y2: axisY + 30 }));
    root.append(svg("line", { stroke: colour, "stroke-width": 2, x1: x(base), x2: x(head) + 6, y1: 70, y2: 70 }));
    root.append(svg("path", { fill: colour, d: `M${x(head)} 70 l8 -5 v10 z` }));
    root.append(svg("text", { x: (x(base) + x(head)) / 2, y: 64, "text-anchor": "middle", class: "strong", text: `−${fmt3(base - head)}` }));
  }
  root.append(
    svg("text", {
      x: x(head),
      y: height - 6,
      "text-anchor": x(head) > width - 90 ? "end" : "middle",
      class: "strong",
      text: dir === "same" ? `head ${fmt3(head)} (identical)` : `head ${fmt3(head)}`,
    }),
  );
}

// The paired bootstrap: 10,000 resampled mean differences, exact values.
export function bootstrap(container, gate, { width }) {
  const dist = gate.bootstrap;
  if (!dist) {
    clear(container);
    return;
  }
  const height = 190;
  const padL = 40;
  const padR = 16;
  const lo = Math.min(-0.45, ...dist.values.map((v) => v.value));
  const x = linear([lo, 0.06], [padL, width - padR]);
  const maxCount = Math.max(...dist.values.map((v) => v.count));
  const y = linear([0, maxCount], [150, 18]);
  const root = frame(
    container,
    width,
    height,
    `Bootstrap distribution of the mean per-task difference; ${(dist.share_at_or_above_zero * 100).toFixed(2)}% of resamples show no regression, so p = ${dist.share_at_or_above_zero.toFixed(3)}`,
  );
  const barW = Math.max(6, Math.min(26, (x(0) - x(-0.0571)) * 0.6));

  root.append(svg("line", { class: "axis-line", x1: padL, x2: width - padR, y1: 150, y2: 150 }));
  for (let t = -0.4; t <= 0.001; t += 0.1) {
    const v = Math.abs(t) < 1e-9 ? 0 : t;
    root.append(svg("text", { x: x(v), y: 168, "text-anchor": "middle", text: v === 0 ? "0" : `−${Math.abs(v).toFixed(1)}` }));
  }
  root.append(svg("text", { x: padL - 6, y: 22, "text-anchor": "end", text: maxCount.toLocaleString("en-US") }));
  root.append(svg("text", { x: padL - 6, y: 152, "text-anchor": "end", text: "0" }));

  for (const v of dist.values) {
    const bx = x(v.value) - barW / 2;
    root.append(svg("rect", { class: v.at_or_above_zero ? "bar-pos" : "bar-neg", x: bx, y: y(v.count), width: barW, height: 150 - y(v.count) }));
    if (v.at_or_above_zero) {
      const narrow = width < 520;
      root.append(svg("text", {
        x: narrow ? x(v.value) + barW / 2 + 3 : x(v.value) - 8,
        y: narrow ? Math.max(y(v.count) - 4, 30) : 30,
        "text-anchor": narrow ? "start" : "end",
        class: "strong",
        text: narrow ? String(v.count) : `${v.count} of ${dist.iterations.toLocaleString("en-US")} ≥ 0`,
      }));
    }
  }
  root.append(svg("line", { class: "zero-line", x1: x(0), x2: x(0), y1: 10, y2: 156 }));
  root.append(svg("line", { stroke: "var(--ink)", "stroke-dasharray": "3 3", x1: x(dist.observed_mean), x2: x(dist.observed_mean), y1: 10, y2: 156 }));
  root.append(svg("text", { x: x(dist.observed_mean) - 5, y: 12, "text-anchor": "end", text: `observed −${Math.abs(dist.observed_mean).toFixed(3)}` }));
  root.append(svg("text", { x: padL, y: height - 2, text: width < 520 ? "resampled mean, head − baseline" : `mean of head − baseline over a resample of the ${gate.comparable_tasks} tasks` }));
}

// MDE against task count, holding the observed spread fixed.
export function mdeCurve(container, model, { width, onPick }) {
  const height = Math.max(240, Math.min(320, width * 0.5));
  const pad = { l: 44, r: 16, t: 16, b: 36 };
  const x = logScale([3, 1000], [pad.l, width - pad.r]);
  const yMax = Math.min(1, Math.max(0.3, Math.ceil(model.mdeAt(3) * 10) / 10));
  const y = linear([0, yMax], [height - pad.b, pad.t]);
  const root = frame(
    container,
    width,
    height,
    `Minimum detectable effect falls with the square root of the task count. At ${model.tasks} tasks it is ${fmt3(model.mde)}.`,
  );

  for (let t = 0; t <= yMax + 1e-9; t += 0.1) {
    root.append(svg("line", { class: "grid", x1: pad.l, x2: width - pad.r, y1: y(t), y2: y(t) }));
    root.append(svg("text", { x: pad.l - 6, y: y(t) + 4, "text-anchor": "end", text: t.toFixed(1) }));
  }
  const xTicks = width < 420 ? [3, 10, 100, 1000] : [3, 7, 10, 30, 100, 300, 1000];
  for (const t of xTicks) {
    root.append(svg("text", { x: x(t), y: height - pad.b + 18, "text-anchor": "middle", text: String(t) }));
  }
  root.append(svg("text", { x: width - pad.r, y: height - 2, "text-anchor": "end", text: "comparable tasks (log)" }));
  root.append(svg("line", { class: "axis-line", x1: pad.l, x2: width - pad.r, y1: y(0), y2: y(0) }));

  const points = [];
  for (let i = 0; i <= 120; i += 1) {
    const n = 3 * (1000 / 3) ** (i / 120);
    points.push([x(n), y(Math.min(yMax, model.mdeAt(n)))]);
  }
  const line = points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  root.append(svg("path", { class: "curve-area", d: `${line}L${x(1000)},${y(0)}L${x(3)},${y(0)}Z` }));

  const refs = [
    ...model.drops.map((d) => ({ value: d.value, cls: d.cls, label: width < 480 ? d.short : d.label })),
    { value: model.target, cls: "ref-tol", label: `target ${fmt3(model.target)}` },
  ];
  refs.forEach((ref) => {
    if (ref.value > yMax) return;
    root.append(svg("line", { class: `ref-line ${ref.cls}`, x1: pad.l, x2: width - pad.r, y1: y(ref.value), y2: y(ref.value) }));
    root.append(svg("text", { x: width - pad.r - 2, y: y(ref.value) - 5, "text-anchor": "end", text: ref.label }));
  });
  root.append(svg("path", { class: "curve", d: line }));

  if (model.needed && model.needed <= 1000 && model.needed >= 3) {
    root.append(svg("circle", { cx: x(model.needed), cy: y(model.target), r: 4.5, fill: "var(--pass-fill)", stroke: "var(--paper)", "stroke-width": 2 }));
  }

  const cx = x(model.tasks);
  const cyv = Math.min(yMax, model.mde);
  root.append(svg("line", { class: "cursor", x1: cx, x2: cx, y1: pad.t, y2: y(0) }));
  root.append(svg("circle", { class: "cursor-dot", cx, cy: y(cyv), r: 6 }));
  const anchorEnd = cx > width * 0.6;
  root.append(
    svg("text", {
      x: cx + (anchorEnd ? -9 : 9),
      y: Math.max(pad.t + 12, y(cyv) - 10),
      "text-anchor": anchorEnd ? "end" : "start",
      class: "strong",
      text: `${model.tasks} tasks → ${fmt3(model.mde)}`,
    }),
  );

  const hit = svg("rect", { x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b, fill: "transparent", style: "cursor:crosshair" });
  const pick = (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const n = Math.round(Math.min(1000, Math.max(3, x.invert(px))));
    onPick(n);
  };
  hit.addEventListener("pointerdown", (event) => {
    hit.setPointerCapture(event.pointerId);
    pick(event);
  });
  hit.addEventListener("pointermove", (event) => {
    if (hit.hasPointerCapture(event.pointerId)) pick(event);
  });
  root.append(hit);
}
