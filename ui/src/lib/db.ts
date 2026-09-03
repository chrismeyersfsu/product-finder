/**
 * Read-only data access for the UI. Owns every SELECT in the UI and
 * nothing else: opens the product-finder SQLite db readonly, never
 * writes (src/lib/hide.ts owns the UI's one write), never creates it,
 * and renders "no db yet" as empty results rather than throwing.
 * Server-side only — never import from client scripts. Schema is owned
 * by packages/core (storage.py); this module only reads it. Deals never
 * include hidden listings (hidden_at set).
 */
import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

export interface Product {
  slug: string;
  name: string;
  description: string;
  manual_checks: string[];
  max_price: number | null;
}
export interface Listing {
  id: number;
  site_slug: string;
  url: string;
  title: string;
  price: number | null;
  currency: string;
  condition: string | null;
  seller_rating: number | null;
  seller_feedback_count: number | null;
  score: number | null;
  hard_fails: string[];
  flags: string[];
  distance_mi: number | null;
  unit_qty: number | null;
  unit: "oz" | "ct" | null;
  unit_price: number | null;
  first_seen: string;
  last_seen: string;
  hidden_at: string | null;
  image_url: string | null;
  est_value: number | null;
  median_price?: number;
  pct_vs_median?: number;
  pct_vs_est?: number;
}
export interface Observation {
  site_slug: string;
  url: string;
  title: string;
  price: number;
  score: number | null;
  kind: string;
  observed_at: string;
  distance_mi: number | null;
}
export interface BacktestRow {
  id: number;
  product_slug: string;
  created_at: string;
  params: Record<string, unknown>;
  results?: Record<string, unknown>;
}
export interface SiteRow {
  slug: string;
  name: string;
  kind: string;
  enabled: boolean;
}
export interface SearchRun {
  id: number;
  product_slug: string;
  started_at: string;
  site_results: {
    per_site?: Record<string, number>;
    strategies?: Record<string, string>;
    errors?: Record<string, string>;
  };
}
export interface DealFilters {
  minScore?: number;
  maxPrice?: number;
  sites?: string[];
  includeHardFails?: boolean;
  limit?: number;
  /** Miles from home; rows with no known distance are dropped when set. */
  maxDistance?: number;
  /** Only listings first seen within this many days. */
  newWithinDays?: number;
}
/** A hidden listing with its product's name, for the Hidden page. */
export interface HiddenListing extends Listing {
  product_slug: string;
  product_name: string;
}

/** ISO timestamp `days` ago, in the same UTC form storage.py writes. */
export function sinceIso(days: number): string {
  return new Date(Date.now() - days * 86400e3).toISOString().slice(0, 19) + "+00:00";
}

function dbPath(): string | null {
  const candidates = [
    process.env.PF_DB,
    path.resolve(process.cwd(), "product_finder.db"),
    path.resolve(process.cwd(), "..", "product_finder.db"),
  ].filter((p): p is string => !!p);
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return null;
}

function open(): Database.Database | null {
  const p = dbPath();
  if (!p) return null;
  try {
    return new Database(p, { readonly: true, fileMustExist: true });
  } catch {
    return null;
  }
}

/** Runs fn against the db; a missing/unreadable db yields `empty`. */
function withDb<T>(empty: T, fn: (db: Database.Database) => T): T {
  const db = open();
  if (!db) return empty;
  try {
    return fn(db);
  } catch {
    return empty; // table may not exist yet on an older db
  } finally {
    db.close();
  }
}

export function hasDb(): boolean {
  return dbPath() !== null;
}

export function listProducts(): Product[] {
  return withDb([] as Product[], (db) =>
    db
      .prepare("SELECT slug, name, description, manual_checks, max_price FROM products ORDER BY slug")
      .all()
      .map((r: any) => ({ ...r, manual_checks: JSON.parse(r.manual_checks) }))
  );
}

export function deals(productSlug: string, f: DealFilters = {}): Listing[] {
  const rows = withDb([] as any[], (db) => {
    let sql = "SELECT * FROM listings WHERE product_slug = ? AND hidden_at IS NULL";
    const args: unknown[] = [productSlug];
    if (f.newWithinDays != null) { sql += " AND first_seen >= ?"; args.push(sinceIso(f.newWithinDays)); }
    if (f.minScore != null) { sql += " AND score >= ?"; args.push(f.minScore); }
    if (f.maxPrice != null) { sql += " AND price IS NOT NULL AND price <= ?"; args.push(f.maxPrice); }
    if (f.sites?.length) {
      sql += ` AND site_slug IN (${f.sites.map(() => "?").join(",")})`;
      args.push(...f.sites);
    }
    if (!f.includeHardFails) sql += " AND hard_fails = '[]'";
    if (f.maxDistance != null) { sql += " AND distance_mi IS NOT NULL AND distance_mi <= ?"; args.push(f.maxDistance); }
    sql += " ORDER BY score DESC NULLS LAST, price ASC NULLS LAST LIMIT ?";
    args.push(f.limit ?? 100);
    return db.prepare(sql).all(...args);
  });
  const listings: Listing[] = rows.map((r: any) => ({
    ...r,
    hard_fails: JSON.parse(r.hard_fails),
    flags: JSON.parse(r.flags ?? "[]"),
  }));
  const prices = listings.map((l) => l.price).filter((p): p is number => p != null && p > 0);
  if (prices.length) {
    const sorted = [...prices].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
    for (const l of listings)
      if (l.price != null && l.price > 0) {
        l.median_price = median;
        l.pct_vs_median = Math.round(((l.price - median) / median) * 1000) / 10;
      }
  }
  for (const l of listings)
    if (l.price != null && l.price > 0 && l.est_value != null && l.est_value > 0)
      l.pct_vs_est = Math.round(((l.price - l.est_value) / l.est_value) * 1000) / 10;
  return listings;
}

/** Every hidden listing, most recently hidden first. */
export function hiddenListings(productSlug?: string): HiddenListing[] {
  return withDb([] as HiddenListing[], (db) => {
    let sql =
      "SELECT l.*, p.name AS product_name FROM listings l JOIN products p ON p.slug = l.product_slug" +
      " WHERE l.hidden_at IS NOT NULL";
    const args: unknown[] = [];
    if (productSlug) { sql += " AND l.product_slug = ?"; args.push(productSlug); }
    sql += " ORDER BY l.hidden_at DESC, l.id DESC";
    return db.prepare(sql).all(...args).map((r: any) => ({
      ...r,
      hard_fails: JSON.parse(r.hard_fails),
      flags: JSON.parse(r.flags ?? "[]"),
    }));
  });
}

export function listingSites(productSlug: string): string[] {
  return withDb([] as string[], (db) =>
    db
      .prepare("SELECT DISTINCT site_slug FROM listings WHERE product_slug = ? ORDER BY site_slug")
      .all(productSlug)
      .map((r: any) => r.site_slug)
  );
}

export function listBacktests(productSlug?: string): BacktestRow[] {
  return withDb([] as BacktestRow[], (db) => {
    const sql =
      "SELECT id, product_slug, params, created_at FROM backtests" +
      (productSlug ? " WHERE product_slug = ?" : "") +
      " ORDER BY id DESC";
    const rows = productSlug ? db.prepare(sql).all(productSlug) : db.prepare(sql).all();
    return rows.map((r: any) => ({ ...r, params: JSON.parse(r.params) }));
  });
}

export function getBacktest(id: number): BacktestRow | null {
  return withDb(null as BacktestRow | null, (db) => {
    const r: any = db.prepare("SELECT * FROM backtests WHERE id = ?").get(id);
    return r ? { ...r, params: JSON.parse(r.params), results: JSON.parse(r.results) } : null;
  });
}

/** Observations don't carry a location; distance comes from the
 *  matching listing (same product + url) when we still have it. */
export function history(productSlug: string, kind?: string, maxDistance?: number): Observation[] {
  return withDb([] as Observation[], (db) => {
    let sql =
      "SELECT h.site_slug, h.url, h.title, h.price, h.score, h.kind, h.observed_at, l.distance_mi" +
      " FROM price_history h LEFT JOIN listings l ON l.product_slug = h.product_slug AND l.url = h.url" +
      " WHERE h.product_slug = ?";
    const args: unknown[] = [productSlug];
    if (kind) { sql += " AND h.kind = ?"; args.push(kind); }
    if (maxDistance != null) { sql += " AND l.distance_mi IS NOT NULL AND l.distance_mi <= ?"; args.push(maxDistance); }
    sql += " ORDER BY h.observed_at";
    return db.prepare(sql).all(...args) as Observation[];
  });
}

/** Short label for where distances are measured from ("27705"), or null
 *  when no home is set. Deliberately never the street address. */
export function homeHint(): string | null {
  return withDb(null as string | null, (db) => {
    const r: any = db.prepare("SELECT value FROM settings WHERE key = 'home'").get();
    if (!r) return null;
    const address: string = JSON.parse(r.value)?.address ?? "";
    const zip = address.match(/\b\d{5}\b(?!.*\b\d{5}\b)/)?.[0];
    if (zip) return zip;
    const parts = address.split(",").map((s) => s.trim()).filter(Boolean);
    return parts.length > 1 ? parts.slice(-2).join(", ") : null;
  });
}


export function historySites(productSlug: string): string[] {
  return withDb([] as string[], (db) =>
    db
      .prepare("SELECT DISTINCT site_slug FROM price_history WHERE product_slug = ? ORDER BY site_slug")
      .all(productSlug)
      .map((r: any) => r.site_slug)
  );
}

export function listSites(): SiteRow[] {
  return withDb([] as SiteRow[], (db) =>
    db
      .prepare("SELECT slug, name, kind, enabled FROM sites ORDER BY slug")
      .all()
      .map((r: any) => ({ ...r, enabled: !!r.enabled }))
  );
}

export function recentRuns(limit = 20): SearchRun[] {
  return withDb([] as SearchRun[], (db) =>
    db
      .prepare("SELECT id, product_slug, started_at, site_results FROM search_runs ORDER BY id DESC LIMIT ?")
      .all(limit)
      .map((r: any) => ({ ...r, site_results: JSON.parse(r.site_results) }))
  );
}
