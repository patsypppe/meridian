import type { CurvePoint } from "../analysis";

const W = 520;
const H = 220;
const PAD = { left: 44, right: 16, top: 12, bottom: 34 };

/**
 * How reliability decays as you ask for more consecutive successes. Pass@k climbs
 * towards 1 while pass^k falls towards 0: the gap between them is the cost of
 * relying on retries.
 */
export function PassKCurve({ points }: { points: CurvePoint[] }) {
  if (points.length < 2) {
    return (
      <section aria-label="pass^k curve">
        <h2>Reliability curve</h2>
        <p className="muted">Needs at least 2 gradeable trials per task to draw.</p>
      </section>
    );
  }
  const kMax = points[points.length - 1]?.k ?? 1;
  const x = (k: number) => PAD.left + ((k - 1) / Math.max(kMax - 1, 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - v) * (H - PAD.top - PAD.bottom);
  const path = (pick: (p: CurvePoint) => number) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.k).toFixed(1)},${y(pick(p)).toFixed(1)}`).join(" ");

  return (
    <section aria-label="pass^k curve">
      <h2>Reliability curve</h2>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="pass at k and pass hat k as k grows" className="chart">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="axis">
              {t.toFixed(2)}
            </text>
          </g>
        ))}
        {points.map((p) => (
          <text key={p.k} x={x(p.k)} y={H - 12} textAnchor="middle" className="axis">
            {p.k}
          </text>
        ))}
        <text x={W / 2} y={H - 1} textAnchor="middle" className="axis">
          k
        </text>
        <path d={path((p) => p.passAtK)} className="line at" fill="none" />
        <path d={path((p) => p.passHatK)} className="line hat" fill="none" />
        {points.map((p) => (
          <g key={p.k}>
            <circle cx={x(p.k)} cy={y(p.passAtK)} r={3} className="dot at" />
            <circle cx={x(p.k)} cy={y(p.passHatK)} r={3} className="dot hat" />
          </g>
        ))}
      </svg>
      <ul className="legend">
        <li>
          <span className="swatch at" /> pass@k (at least one of k succeeds)
        </li>
        <li>
          <span className="swatch hat" /> pass^k (all k succeed)
        </li>
      </ul>
    </section>
  );
}
