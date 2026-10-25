export function fmt(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

export function fmtSigned(value: number | null, digits = 2): string {
  if (value === null) return "—";
  const text = Math.abs(value).toFixed(digits);
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text;
}

export function fmtPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function fmtDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 90) return `${seconds.toFixed(1)}s`;
  const minutes = seconds / 60;
  if (minutes < 90) return `${minutes.toFixed(1)}m`;
  return `${(minutes / 60).toFixed(1)}h`;
}

export function fmtCost(cents: number): string {
  return cents < 100 ? `${cents}¢` : `$${(cents / 100).toFixed(2)}`;
}
