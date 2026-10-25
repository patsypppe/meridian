import { describe, expect, it } from "vitest";
import vectors from "./__fixtures__/stats-vectors.json";
import { passAtK, passHatK, tInterval, tInvCdf, wilsonInterval } from "./stats";

// The reference values come from the Python implementation (see
// benchmarks/export_web_vectors.py); these tests assert the TypeScript agrees.
const TOLERANCE = 1e-7;

describe("agreement with the Python statistics", () => {
  it.each(vectors.passk)("pass@k and pass^k for n=$n c=$c k=$k", (v) => {
    expect(passAtK(v.n, v.c, v.k)).toBeCloseTo(v.pass_at_k, 10);
    expect(passHatK(v.n, v.c, v.k)).toBeCloseTo(v.pass_hat_k, 10);
  });

  it.each(vectors.t_quantile)("t quantile p=$p df=$df", (v) => {
    expect(Math.abs(tInvCdf(v.p, v.df) - v.value)).toBeLessThan(TOLERANCE);
  });

  it.each(vectors.t_interval)("t interval of $values", (v) => {
    const [lo, hi] = tInterval(v.values);
    expect(Math.abs(lo - v.lower)).toBeLessThan(TOLERANCE);
    expect(Math.abs(hi - v.upper)).toBeLessThan(TOLERANCE);
  });

  it.each(vectors.wilson)("Wilson interval for $successes of $n", (v) => {
    const [lo, hi] = wilsonInterval(v.successes, v.n);
    expect(Math.abs(lo - v.lower)).toBeLessThan(TOLERANCE);
    expect(Math.abs(hi - v.upper)).toBeLessThan(TOLERANCE);
  });
});

describe("the estimator is C(c,k)/C(n,k), not (c/n)^k", () => {
  it("an agent passing 4 of 5 has pass^3 = 0.4", () => {
    expect(passHatK(5, 4, 3)).toBeCloseTo(0.4, 12);
    expect(passHatK(5, 4, 3)).not.toBeCloseTo((4 / 5) ** 3, 3);
  });

  it("is zero when fewer than k trials passed", () => {
    expect(passHatK(10, 2, 3)).toBe(0);
  });

  it("pass@k reaches 1 when failures cannot fill k slots", () => {
    expect(passAtK(10, 8, 3)).toBe(1);
  });

  it("rejects impossible inputs", () => {
    expect(() => passHatK(5, 6, 3)).toThrow(RangeError);
    expect(() => passHatK(5, 3, 6)).toThrow(RangeError);
    expect(() => passHatK(5, 3, 0)).toThrow(RangeError);
    expect(() => passHatK(5.5, 3, 1)).toThrow(RangeError);
  });
});

describe("intervals", () => {
  it("Wilson at zero successes is exactly [0, upper>0.2]", () => {
    const [lo, hi] = wilsonInterval(0, 10);
    expect(lo).toBe(0);
    expect(hi).toBeGreaterThan(0.2);
  });

  it("Wilson stays inside [0, 1]", () => {
    for (const s of [0, 1, 5, 9, 10]) {
      const [lo, hi] = wilsonInterval(s, 10);
      expect(lo).toBeGreaterThanOrEqual(0);
      expect(hi).toBeLessThanOrEqual(1);
    }
  });

  it("a t interval needs at least two values", () => {
    expect(() => tInterval([0.5])).toThrow(RangeError);
  });

  it("t quantile is symmetric and approaches the normal", () => {
    expect(tInvCdf(0.025, 5)).toBeCloseTo(-tInvCdf(0.975, 5), 10);
    expect(tInvCdf(0.975, 10_000)).toBeCloseTo(1.96, 2);
  });
});
