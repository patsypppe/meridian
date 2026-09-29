// §02 — the sensitivity explorer. Same arithmetic as stats/power.py, driven by
// the spreads the real gates observed.

import { mdeCurve } from "./charts.js";
import { bind, clear, el, fmt3, fmtInt, setText } from "./dom.js";
import { mdeFromSpread, requiredTasks } from "./stats.js";

const MIN_TASKS = 3;
// Slider stops: one arrow press is always a visible change, from the 7 tasks
// this suite has up to the few hundred the tolerance would need.
const STOPS = [3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20, 25, 30, 35, 40, 50, 60, 70, 80, 100,
  120, 150, 175, 200, 225, 262, 300, 315, 350, 400, 450, 500, 600, 700, 800, 900, 1000];

const toTasks = (v) => STOPS[Math.min(STOPS.length - 1, Math.max(0, Math.round(v)))];
const toSlider = (n) => {
  let best = 0;
  STOPS.forEach((stop, i) => {
    if (Math.abs(Math.log(stop / n)) < Math.abs(Math.log(STOPS[best] / n))) best = i;
  });
  return best;
};

export function mountExplorer(data) {
  const measured = data.gates.filter((g) => g.spread);
  const first = measured[0];
  const state = {
    spread: first ? first.spread : 0.2,
    tasks: first ? first.comparable_tasks : 7,
    target: first ? first.tolerance : 0.03,
    power: data.stats.power,
    alpha: data.stats.alpha,
    preset: first ? first.name : null,
  };

  const inputs = {
    spread: document.getElementById("in-spread"),
    tasks: document.getElementById("in-tasks"),
    target: document.getElementById("in-target"),
    power: document.getElementById("in-power"),
    alpha: document.getElementById("in-alpha"),
  };
  bind("controls").addEventListener("submit", (event) => event.preventDefault());
  setText("suite-tasks", String(first ? first.comparable_tasks : 7));

  const presets = clear(bind("presets"));
  const chips = measured.map((g) =>
    el("button", { type: "button", class: "chip", "aria-pressed": "false", "data-name": g.name, text: `${g.name} · s=${fmt3(g.spread)}` }),
  );
  chips.forEach((chip, i) => {
    chip.addEventListener("click", () => {
      state.spread = measured[i].spread;
      state.tasks = measured[i].comparable_tasks;
      state.preset = measured[i].name;
      update();
    });
    presets.append(chip);
  });

  const drops = data.gates
    .filter((g) => g.drop > 0)
    .map((g) => ({
      value: g.drop,
      cls: g.verdict === "fail" ? "ref-fail" : "ref-warn",
      label: `${g.name} drop ${fmt3(g.drop)}`,
      short: `${g.name.split("-")[0]} ${fmt3(g.drop)}`,
    }));

  function syncInputs() {
    inputs.spread.value = String(state.spread);
    // Leave the slider where the user put it when it already maps to this task
    // count; snapping it back would swallow small keyboard steps.
    if (toTasks(Number(inputs.tasks.value)) !== state.tasks) inputs.tasks.value = String(toSlider(state.tasks));
    inputs.tasks.max = String(STOPS.length - 1);
    inputs.target.value = String(state.target);
    inputs.power.value = String(state.power);
    inputs.alpha.value = String(state.alpha);
    inputs.tasks.setAttribute("aria-valuetext", `${state.tasks} tasks`);
    setText("out-spread", fmt3(state.spread));
    setText("out-tasks", fmtInt(state.tasks));
    setText("out-target", fmt3(state.target));
    setText("out-power", `${Math.round(state.power * 100)}%`);
    setText("out-alpha", state.alpha.toFixed(2));
    chips.forEach((chip) => {
      const on = chip.dataset.name === state.preset && Math.abs(state.spread - measured.find((g) => g.name === chip.dataset.name).spread) < 1e-9;
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function sentence(mde, needed) {
    const caught = drops.filter((d) => d.value >= mde).map((d) => d.label.split(" drop")[0]);
    const missed = drops.filter((d) => d.value < mde).map((d) => d.label.split(" drop")[0]);
    let text = `With ${fmtInt(state.tasks)} tasks, drops smaller than ${fmt3(mde)} would more often than not go unnoticed.`;
    if (missed.length) text += ` The real ${missed.join(" and ")} drop${missed.length > 1 ? "s sit" : " sits"} inside that zone.`;
    if (caught.length && !missed.length) text += " Both real drops would be caught reliably.";
    else if (caught.length) text += ` The ${caught.join(" and ")} drop clears it.`;
    text += ` Catching ${fmt3(state.target)} reliably takes about ${fmtInt(needed)} tasks.`;
    return text;
  }

  function update() {
    syncInputs();
    const mde = mdeFromSpread(state.spread, state.tasks, state.alpha, state.power);
    const needed = requiredTasks(state.spread, state.target, state.alpha, state.power);
    setText("mde", fmt3(mde));
    setText("needed", fmtInt(needed));
    setText(
      "needed-note",
      needed <= state.tasks
        ? `at the same spread · you already have ${fmtInt(state.tasks)}`
        : `at the same spread · ${fmtInt(needed - state.tasks)} more than ${fmtInt(state.tasks)}`,
    );
    setText("sentence", sentence(mde, needed));
    const model = {
      tasks: state.tasks,
      mde,
      target: state.target,
      needed,
      drops,
      mdeAt: (n) => mdeFromSpread(state.spread, n, state.alpha, state.power),
    };
    mdeCurve(bind("curve"), model, {
      width: Math.max(260, bind("curve").clientWidth),
      onPick: (n) => {
        state.tasks = n;
        update();
      },
    });
  }

  inputs.spread.addEventListener("input", () => {
    state.spread = Number(inputs.spread.value);
    state.preset = null;
    update();
  });
  inputs.tasks.addEventListener("input", () => {
    state.tasks = Math.max(MIN_TASKS, toTasks(Number(inputs.tasks.value)));
    update();
  });
  inputs.target.addEventListener("input", () => {
    state.target = Number(inputs.target.value);
    update();
  });
  inputs.power.addEventListener("input", () => {
    state.power = Number(inputs.power.value);
    update();
  });
  inputs.alpha.addEventListener("input", () => {
    state.alpha = Number(inputs.alpha.value);
    update();
  });

  update();
  return {
    redraw: update,
    // Exposed for verification against the Python implementation.
    compute: (spread, tasks, target, alpha = data.stats.alpha, power = data.stats.power) => ({
      mde: mdeFromSpread(spread, tasks, alpha, power),
      needed: requiredTasks(spread, target, alpha, power),
    }),
  };
}
