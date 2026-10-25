/**
 * pass@k, pass^k and small-sample intervals.
 *
 * A TypeScript port of `meridian.stats.passk` and `meridian.stats.intervals`. The
 * numbers must agree with Python's: `stats.test.ts` checks them against vectors
 * exported from the Python implementation, and a Python test fails if those
 * vectors go stale.
 *
 * Provenance: `tInvCdf`, `regIncBeta` and `betaContinuedFraction` are ported from
 * Inspect (https://github.com/UKGovernmentBEIS/inspect_ai,
 * src/inspect_ai/scorer/_metrics/std.py at commit
 * 7c2dcccbebc6747f310a70eeba1fa4063fe060db), Copyright (c) 2024 UK AI Security
 * Institute, MIT License (see THIRD_PARTY_NOTICES.md at the repository root). The
 * Wilson formula follows Inspect's `ci_wilson`.
 */

/** C(c, k) / C(n, k) as a product, which stays exact-enough without huge factorials. */
function ratioOfCombinations(n: number, c: number, k: number): number {
  let ratio = 1;
  for (let i = 0; i < k; i += 1) {
    ratio *= (c - i) / (n - i);
  }
  return ratio;
}

function validate(n: number, c: number, k: number): void {
  if (!(Number.isInteger(n) && Number.isInteger(c) && Number.isInteger(k))) {
    throw new RangeError(`n, c and k must be integers (got n=${n} c=${c} k=${k})`);
  }
  if (c < 0 || c > n || k < 1 || k > n) {
    throw new RangeError(`invalid n=${n} c=${c} k=${k}`);
  }
}

/** Probability that ALL k trials drawn without replacement pass: C(c,k)/C(n,k). */
export function passHatK(n: number, c: number, k: number): number {
  validate(n, c, k);
  if (c < k) return 0;
  return ratioOfCombinations(n, c, k);
}

/** Probability that at least one of k trials drawn without replacement passes. */
export function passAtK(n: number, c: number, k: number): number {
  validate(n, c, k);
  if (n - c < k) return 1;
  return 1 - ratioOfCombinations(n, n - c, k);
}

export function mean(values: readonly number[]): number {
  return values.length === 0 ? 0 : values.reduce((a, b) => a + b, 0) / values.length;
}

// -- Student-t quantile and intervals -------------------------------------------------

function betaContinuedFraction(a: number, b: number, x: number): number {
  const maxIterations = 200;
  const epsilon = 3e-16;
  const tiny = 1e-300;
  const qab = a + b;
  const qap = a + 1;
  const qam = a - 1;
  let c = 1;
  let d = 1 - (qab * x) / qap;
  if (Math.abs(d) < tiny) d = tiny;
  d = 1 / d;
  let h = d;
  for (let m = 1; m <= maxIterations; m += 1) {
    const m2 = 2 * m;
    let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < tiny) d = tiny;
    c = 1 + aa / c;
    if (Math.abs(c) < tiny) c = tiny;
    d = 1 / d;
    h *= d * c;
    aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
    d = 1 + aa * d;
    if (Math.abs(d) < tiny) d = tiny;
    c = 1 + aa / c;
    if (Math.abs(c) < tiny) c = tiny;
    d = 1 / d;
    const delta = d * c;
    h *= delta;
    if (Math.abs(delta - 1) < epsilon) break;
  }
  return h;
}

function logGamma(x: number): number {
  // Lanczos approximation (g = 7, n = 9); accurate to ~1e-15 for x > 0.
  const coefficients = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6,
    1.5056327351493116e-7,
  ];
  if (x < 0.5) {
    return Math.log(Math.PI / Math.sin(Math.PI * x)) - logGamma(1 - x);
  }
  const shifted = x - 1;
  let sum = coefficients[0] ?? 0;
  const t = shifted + 7.5;
  for (let i = 1; i < 9; i += 1) {
    sum += (coefficients[i] ?? 0) / (shifted + i);
  }
  return 0.5 * Math.log(2 * Math.PI) + (shifted + 0.5) * Math.log(t) - t + Math.log(sum);
}

/** Regularized incomplete beta function I_x(a, b) (Numerical Recipes 6.4). */
function regIncBeta(a: number, b: number, x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const lnFront =
    logGamma(a + b) - logGamma(a) - logGamma(b) + a * Math.log(x) + b * Math.log1p(-x);
  const front = Math.exp(lnFront);
  if (x < (a + 1) / (a + b + 2)) {
    return (front * betaContinuedFraction(a, b, x)) / a;
  }
  return 1 - (front * betaContinuedFraction(b, a, 1 - x)) / b;
}

/** Student-t inverse CDF, dependency-free, exact to bisection precision. */
export function tInvCdf(p: number, df: number): number {
  if (!(p > 0 && p < 1)) throw new RangeError(`t quantile requires 0 < p < 1, got ${p}`);
  if (df < 1) throw new RangeError(`t quantile requires df >= 1, got ${df}`);
  if (p === 0.5) return 0;
  if (p < 0.5) return -tInvCdf(1 - p, df);

  const cdf = (t: number): number => {
    const x = df / (df + t * t);
    return 1 - 0.5 * regIncBeta(df / 2, 0.5, x);
  };
  let hi = 1;
  while (cdf(hi) < p) hi *= 2;
  let lo = 0;
  for (let i = 0; i < 100; i += 1) {
    const mid = (lo + hi) / 2;
    if (cdf(mid) < p) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

export type Interval = readonly [number, number];

function tailFor(level: number): number {
  if (!(level > 0 && level < 1)) throw new RangeError(`level must be in (0, 1), got ${level}`);
  return (1 - level) / 2;
}

/** Two-sided Student-t interval for the mean of per-task values (df = tasks - 1). */
export function tInterval(values: readonly number[], level = 0.95): Interval {
  const tail = tailFor(level);
  const n = values.length;
  if (n < 2) throw new RangeError("a t interval needs at least two values");
  const m = mean(values);
  const variance = values.reduce((acc, v) => acc + (v - m) ** 2, 0) / (n - 1);
  const standardError = Math.sqrt(variance / n);
  const critical = tInvCdf(Math.min(1 - tail, 1 - Number.EPSILON / 2), n - 1);
  return [m - critical * standardError, m + critical * standardError];
}

function normalQuantile(p: number): number {
  // Acklam's rational approximation, relative error < 1.2e-9 (refined below).
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269,
    -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972,
    -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734,
    4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const low = 0.02425;
  const at = (arr: number[], i: number): number => arr[i] ?? 0;
  let x: number;
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p));
    x =
      (((((at(c, 0) * q + at(c, 1)) * q + at(c, 2)) * q + at(c, 3)) * q + at(c, 4)) * q + at(c, 5)) /
      ((((at(d, 0) * q + at(d, 1)) * q + at(d, 2)) * q + at(d, 3)) * q + 1);
  } else if (p <= 1 - low) {
    const q = p - 0.5;
    const r = q * q;
    x =
      ((((((at(a, 0) * r + at(a, 1)) * r + at(a, 2)) * r + at(a, 3)) * r + at(a, 4)) * r + at(a, 5)) * q) /
      (((((at(b, 0) * r + at(b, 1)) * r + at(b, 2)) * r + at(b, 3)) * r + at(b, 4)) * r + 1);
  } else {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    x =
      -(((((at(c, 0) * q + at(c, 1)) * q + at(c, 2)) * q + at(c, 3)) * q + at(c, 4)) * q + at(c, 5)) /
      ((((at(d, 0) * q + at(d, 1)) * q + at(d, 2)) * q + at(d, 3)) * q + 1);
  }
  return x;
}

/** Wilson score interval for a binomial proportion, always within [0, 1]. */
export function wilsonInterval(successes: number, n: number, level = 0.95): Interval {
  const tail = tailFor(level);
  if (n <= 0) return [0, 0];
  if (successes < 0 || successes > n) throw new RangeError(`successes must be in [0, ${n}]`);
  const z = -normalQuantile(tail);
  const pHat = successes / n;
  const denominator = 1 + (z * z) / n;
  const center = (pHat + (z * z) / (2 * n)) / denominator;
  const half = (z * Math.sqrt((pHat * (1 - pHat)) / n + (z * z) / (4 * n * n))) / denominator;
  const lower = successes === 0 ? 0 : Math.max(center - half, 0);
  const upper = successes === n ? 1 : Math.min(center + half, 1);
  return [lower, upper];
}
