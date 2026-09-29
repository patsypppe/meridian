// The page's sensitivity arithmetic must match the Python that wrote the PR
// comments. showcase.json carries Python's own numbers (stats/power.py via
// site/data/export.py); this recomputes them in JavaScript and compares.
//
//   node --test site/tests/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  invNormalCdf,
  mdeFromSpread,
  minimumDetectableEffect,
  passHatK,
  requiredTasks,
  zSum,
} from "../assets/js/stats.js";

const data = JSON.parse(readFileSync(new URL("../data/showcase.json", import.meta.url)));

test("z-sum matches Python's NormalDist to the last few ulps", () => {
  assert.ok(Math.abs(zSum(data.stats.alpha, data.stats.power) - data.stats.z_sum) < 1e-12);
});

test("inverse normal CDF is symmetric and hits known quantiles", () => {
  assert.ok(Math.abs(invNormalCdf(0.975) - 1.959963984540054) < 1e-12);
  assert.ok(Math.abs(invNormalCdf(0.025) + invNormalCdf(0.975)) < 1e-15);
  assert.throws(() => invNormalCdf(0));
});

for (const gate of data.gates) {
  test(`${gate.name}: MDE and required tasks match the Python export`, () => {
    const mde = minimumDetectableEffect(gate.differences);
    if (gate.mde === null) {
      assert.equal(mde, null, "an unestimable MDE must stay unestimable, never 0");
      return;
    }
    assert.ok(Math.abs(mde - gate.mde) < 1e-6, `${mde} vs ${gate.mde}`);
    assert.ok(Math.abs(mdeFromSpread(gate.spread, gate.comparable_tasks) - gate.mde) < 1e-6);
    assert.equal(requiredTasks(gate.spread, gate.tolerance), gate.required_tasks_for_tolerance);
  });
}

test("pass^k is C(c,k)/C(n,k), not (c/n)^k", () => {
  for (const task of data.run.tasks) {
    assert.ok(Math.abs(passHatK(task.n, task.c, data.run.k) - task.pass_hat_k) < 1e-6);
  }
  assert.equal(passHatK(5, 4, 3), 0.4);
});

test("required tasks refuses a non-positive effect, like the Python", () => {
  assert.throws(() => requiredTasks(0.2, 0));
});
