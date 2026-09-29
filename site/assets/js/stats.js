// The sensitivity arithmetic, ported line for line from
// src/meridian/stats/power.py so the explorer quotes the numbers the gate would.
//
//   delta = (z_alpha + z_beta) * s / sqrt(n)            minimum_detectable_effect
//   n     = ceil(round(((z_alpha + z_beta) * s / d)^2, 9))   required_tasks
//
// The inverse normal CDF is Wichura's AS241, the same algorithm (and the same
// coefficients) as Python's statistics.NormalDist.inv_cdf, so z-values agree to
// the last few ulps rather than to "about three decimals".

export const DEFAULT_ALPHA = 0.05;
export const DEFAULT_POWER = 0.8;
export const MIN_TASKS_FOR_POWER = 3;

export function invNormalCdf(p) {
  if (!(p > 0 && p < 1)) throw new RangeError(`p must be in (0, 1), got ${p}`);
  const q = p - 0.5;
  let num;
  let den;
  if (Math.abs(q) <= 0.425) {
    const r = 0.180625 - q * q;
    num = (((((((2.5090809287301226727e3 * r + 3.3430575583588128105e4) * r +
      6.7265770927008700853e4) * r + 4.5921953931549871457e4) * r +
      1.3731693765509461125e4) * r + 1.9715909503065514427e3) * r +
      1.3314166789178437745e2) * r + 3.387132872796366608) * q;
    den = (((((((5.226495278852545561e3 * r + 2.8729085735721942674e4) * r +
      3.930789580009271061e4) * r + 2.1213794301586595867e4) * r +
      5.3941960214247511077e3) * r + 6.8718700749205790830e2) * r +
      4.2313330701600911252e1) * r + 1.0);
    return num / den;
  }
  let r = q <= 0 ? p : 1 - p;
  r = Math.sqrt(-Math.log(r));
  if (r <= 5) {
    r -= 1.6;
    num = (((((((7.7454501427834140764e-4 * r + 2.27238449892691845833e-2) * r +
      2.4178072517745061177e-1) * r + 1.27045825245236838258) * r +
      3.64784832476320460504) * r + 5.7694972214606914055) * r +
      4.6303378461565452959) * r + 1.42343711074968357734);
    den = (((((((1.05075007164441684324e-9 * r + 5.475938084995344946e-4) * r +
      1.51986665636164571966e-2) * r + 1.4810397642748007459e-1) * r +
      6.8976733498510000455e-1) * r + 1.6763848301838038494) * r +
      2.05319162663775882187) * r + 1.0);
  } else {
    r -= 5;
    num = (((((((2.01033439929228813265e-7 * r + 2.71155556874348757815e-5) * r +
      1.2426609473880784386e-3) * r + 2.6532189526576123093e-2) * r +
      2.9656057182850489123e-1) * r + 1.7848265399172913358) * r +
      5.4637849111641143699) * r + 6.6579046435011037772);
    den = (((((((2.04426310338993978564e-15 * r + 1.4215117583164458887e-7) * r +
      1.8463183175100546818e-5) * r + 7.868691311456132591e-4) * r +
      1.4875361290850614852e-2) * r + 1.3692988092273580531e-1) * r +
      5.9983220655588793769e-1) * r + 1.0);
  }
  const x = num / den;
  return q < 0 ? -x : x;
}

// `z_alpha + z_beta` for a one-sided test (power.py::_z).
export function zSum(alpha = DEFAULT_ALPHA, power = DEFAULT_POWER) {
  return invNormalCdf(1 - alpha) + invNormalCdf(power);
}

// Sample standard deviation with Bessel's correction (power.py::_stdev).
export function stdev(differences) {
  const n = differences.length;
  const mean = differences.reduce((a, b) => a + b, 0) / n;
  const variance = differences.reduce((acc, d) => acc + (d - mean) ** 2, 0) / (n - 1);
  return Math.sqrt(variance);
}

// minimum_detectable_effect(differences) — null means "this suite cannot answer that".
export function minimumDetectableEffect(differences, alpha = DEFAULT_ALPHA, power = DEFAULT_POWER) {
  const n = differences.length;
  if (n < MIN_TASKS_FOR_POWER) return null;
  const spread = stdev(differences);
  const scale = Math.max(0, ...differences.map(Math.abs));
  if (spread <= 1e-12 * Math.max(scale, 1)) return null;
  return (zSum(alpha, power) * spread) / Math.sqrt(n);
}

// The same quantity from a spread and a task count, which is what the explorer varies.
export function mdeFromSpread(spread, tasks, alpha = DEFAULT_ALPHA, power = DEFAULT_POWER) {
  if (tasks < MIN_TASKS_FOR_POWER || !(spread > 0)) return null;
  return (zSum(alpha, power) * spread) / Math.sqrt(tasks);
}

// Python's round(x, 9) for the magnitudes seen here: round half to even at the 9th decimal.
function roundTo9(x) {
  const scaled = x * 1e9;
  const floor = Math.floor(scaled);
  const diff = scaled - floor;
  let rounded;
  if (Math.abs(diff - 0.5) < 1e-7) rounded = floor % 2 === 0 ? floor : floor + 1;
  else rounded = Math.round(scaled);
  return rounded / 1e9;
}

// required_tasks(spread, effect).
export function requiredTasks(spread, effect, alpha = DEFAULT_ALPHA, power = DEFAULT_POWER) {
  if (!(effect > 0)) throw new RangeError(`effect must be positive, got ${effect}`);
  if (!(spread > 0)) throw new RangeError(`spread must be positive, got ${spread}`);
  const exact = ((zSum(alpha, power) * spread) / effect) ** 2;
  return Math.ceil(roundTo9(exact));
}

// C(n, k), exact for the small integers pass^k uses.
export function comb(n, k) {
  if (k < 0 || k > n) return 0;
  let out = 1;
  for (let i = 1; i <= k; i += 1) out = (out * (n - k + i)) / i;
  return Math.round(out);
}

// pass^k = C(c, k) / C(n, k)   (passk.py::pass_hat_k)
export function passHatK(n, c, k) {
  if (c < k) return 0;
  return comb(c, k) / comb(n, k);
}
