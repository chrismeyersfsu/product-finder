/**
 * Chart tokens for the UI, instantiated from the dataviz reference
 * palette. Owns the fixed site->color assignment (color follows the
 * entity — never reassigned by filtering or rank) and the light/dark
 * categorical steps. Site slots are allocated on a stable
 * first-priority-then-alphabetical roster so the same site wears the
 * same hue on every chart and every visit; sites past the slot cap
 * fold into the muted "other" ink per the series-count ladder.
 */

// Categorical slots (validated order — see scripts/validate_palette.js run in ui/ci notes)
export const CAT_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
export const CAT_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
export const OTHER = "#898781"; // muted ink, both modes
// Diverging pair (polarity charts: improvement above/below zero)
export const DIV_POS = { light: "#2a78d6", dark: "#3987e5" };
export const DIV_NEG = { light: "#e34948", dark: "#e66767" };

// Sites likely to matter get the leading slots; assignment never depends
// on what a page happens to display.
const PRIORITY = ["ebay", "reddit-hardwareswap", "swappa", "craigslist", "mercari", "newegg"];

/** Stable slot index for a site, or -1 => "other". Cap: `max` slots. */
export function siteSlot(site: string, knownSites: string[], max = 6): number {
  const roster = [
    ...PRIORITY.filter((s) => knownSites.includes(s)),
    ...knownSites.filter((s) => !PRIORITY.includes(s)).sort(),
  ];
  const i = roster.indexOf(site);
  return i >= 0 && i < max ? i : -1;
}

export function siteColor(site: string, knownSites: string[], mode: "light" | "dark", max = 6): string {
  const slot = siteSlot(site, knownSites, max);
  if (slot < 0) return OTHER;
  return (mode === "light" ? CAT_LIGHT : CAT_DARK)[slot] ?? OTHER;
}

/** CSS custom-property reference for a site's color — theme-aware via
 * the tokens in global.css; same fixed assignment as siteColor. */
export function siteVar(site: string, knownSites: string[], max = 6): string {
  const slot = siteSlot(site, knownSites, max);
  return slot < 0 ? "var(--other)" : `var(--cat-${slot + 1})`;
}
