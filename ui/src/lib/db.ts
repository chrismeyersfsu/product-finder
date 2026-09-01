/**
 * Read-only data access for the UI. Owns every SQL statement in the UI
 * and nothing else: opens the product-finder SQLite db readonly, never
 * writes, never creates it, and renders "no db yet" as empty results
 * rather than throwing. Server-side only — never import from client
 * scripts. Schema is owned by packages/core (storage.py); this module
 * only reads it.
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
  last_seen: string;
  median_price?: number;
  pct_vs_median?: number;
}
export interface Observation {
  site_slug: string;
  url: string;
  title: string;
  price: number;
  score: number | null;
  kind: string;
  observed_at: string;
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
  site_results: Record<string, { listings?: number; error?: string | null; strategy?: string }>;
}
export interface DealFilters {
  minScore?: number;
  maxPrice?: number;
  site?: string;
  includeHardFails?: boolean;
  limit?: number;
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
    let sql = "SELECT * FROM listings WHERE product_slug = ?";
    const args: unknown[] = [productSlug];
    if (f.minScore != null) { sql += " AND score >= ?"; args.push(f.minScore); }
    if (f.maxPrice != null) { sql += " AND price IS NOT NULL AND price <= ?"; args.push(f.maxPrice); }
    if (f.site) { sql += " AND site_slug = ?"; args.push(f.site); }
    if (!f.includeHardFails) sql += " AND hard_fails = '[]'";
    sql += " ORDER BY score DESC NULLS LAST, price ASC NULLS LAST LIMIT ?";
    args.push(f.limit ?? 100);
    return db.prepare(sql).all(...args);
  });
  const listings: Listing[] = rows.map((r: any) => ({ ...r, hard_fails: JSON.parse(r.hard_fails) }));
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
  return listings;
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

export function history(productSlug: string, kind?: string): Observation[] {
  return withDb([] as Observation[], (db) => {
    let sql =
      "SELECT site_slug, url, title, price, score, kind, observed_at FROM price_history WHERE product_slug = ?";
    const args: unknown[] = [productSlug];
    if (kind) { sql += " AND kind = ?"; args.push(kind); }
    sql += " ORDER BY observed_at";
    return db.prepare(sql).all(...args) as Observation[];
  });
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
