/** Formatting helpers: money, compact counts, dates. Pure. */
export const money = (v: number | null | undefined): string =>
  v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const moneyCents = (v: number | null | undefined): string =>
  v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD" });
export const pct = (v: number | null | undefined, digits = 0): string =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
export const day = (iso: string): string => iso.slice(0, 10);
export const windowLabel = (days: number): string =>
  days % 7 === 0 ? `${days / 7}w` : `${days}d`;
/** "$0.147/oz" — pack-size price from units.py; sub-dollar values keep 3
 * decimals so per-ounce prices don't all round to $0.15. */
export const perUnit = (v: number | null | undefined, unit: string | null | undefined): string =>
  v == null || !unit ? "—" : `$${v.toFixed(v < 1 ? 3 : 2)}/${unit}`;
