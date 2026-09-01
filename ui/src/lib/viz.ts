/** Pure SVG-chart math shared by the chart components: linear scales
 * and clean axis ticks. No DOM, no data access. */
export interface Scale { (v: number): number; domain: [number, number] }

export function scaleLinear(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const f = ((v: number) => r0 + ((v - d0) / span) * (r1 - r0)) as Scale;
  f.domain = domain;
  return f;
}

/** Round tick values covering [min, max] — 1/2/5 steps. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) { min = min - 1; max = max + 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step / 1000; t += step)
    ticks.push(Math.round(t * 100) / 100 || 0);
  return ticks;
}

/** Pad a numeric domain by frac on each side. */
export function pad(domain: [number, number], frac = 0.08): [number, number] {
  const span = domain[1] - domain[0] || Math.abs(domain[0]) || 1;
  return [domain[0] - span * frac, domain[1] + span * frac];
}
