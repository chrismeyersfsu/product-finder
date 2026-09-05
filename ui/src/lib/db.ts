/**
 * Read-only data access for the UI. Owns every SELECT in the UI and
 * nothing else: opens the product-finder SQLite db readonly, never
 * writes (src/lib/hide.ts, lib/pin.ts and lib/products.ts own the
 * UI's writes; lib/settings.ts owns settings writes), never creates
 * it, and renders "no db yet" as empty results rather than throwing.
 * Server-side only — never import from client scripts. Schema is owned
 * by packages/core (storage.py); this module only reads it. Deals never
 * include hidden listings (hidden_at set); pinned ones (pinned_at set)
 * still show, sorted into their own bucket by the results page.
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
/** Every column of a `products` row, JSON fields parsed. */
export interface ProductFull {
  slug: string;
  name: string;
  description: string;
  queries: string[];
  criteria: Record<string, unknown>[];
  extractors: Record<string, unknown>;
  manual_checks: string[];
  sites: string[];
  max_price: number | null;
  created_at: string;
  updated_at: string;
}
/** One row of the products list: name/slug, query and site scope, and
 * roll-ups over its listings. */
export interface ProductSummary {
  slug: string;
  name: string;
  queryCount: number;
  sites: string[];
  listingCount: number;
  qualifyingCount: number;
  flaggedCount: number;
  lastSeen: string | null;
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
  /** Pinned listings float to the top of the results page, in their
   *  own bucket. Always null on a db that predates the pinned_at
   *  column — see pinningAvailable(). */
  pinned_at: string | null;
  image_url: string | null;
  est_value: number | null;
  median_price?: number;
  pct_vs_median?: number;
  pct_vs_est?: number;
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

/** Whether this db has been migrated with the listings.pinned_at
 *  column yet. A positive answer is cached for the process (columns
 *  don't disappear); a negative one is re-probed on every call, because
 *  the migration runs in the MCP/scrape container and may land after
 *  this server first looked (PRAGMA table_info is cheap). deals() and
 *  the results page use this to fall back to "no pins" — an empty
 *  pinned bucket, no Pin button — on an older db instead of offering a
 *  control that would always fail. */
let pinnedColumnPresent = false;
export function pinningAvailable(): boolean {
  if (!pinnedColumnPresent) {
    pinnedColumnPresent = withDb(false, (db) =>
      (db.prepare("PRAGMA table_info(listings)").all() as { name: string }[]).some((c) => c.name === "pinned_at")
    );
  }
  return pinnedColumnPresent;
}

export function listProducts(): Product[] {
  return withDb([] as Product[], (db) =>
    db
      .prepare("SELECT slug, name, description, manual_checks, max_price FROM products ORDER BY name COLLATE NOCASE")
      .all()
      .map((r: any) => ({ ...r, manual_checks: JSON.parse(r.manual_checks) }))
  );
}

/** One product, every column, or null when the slug is unknown. */
export function productFull(slug: string): ProductFull | null {
  return withDb(null as ProductFull | null, (db) => {
    const r: any = db.prepare("SELECT * FROM products WHERE slug = ?").get(slug);
    if (!r) return null;
    return {
      ...r,
      queries: JSON.parse(r.queries),
      criteria: JSON.parse(r.criteria),
      extractors: JSON.parse(r.extractors),
      manual_checks: JSON.parse(r.manual_checks),
      sites: JSON.parse(r.sites),
    };
  });
}

/** One row per product for the products list: query/site scope plus
 * roll-ups over its listings (qualifying = passes every hard fail and
 * isn't hidden; flagged = carries at least one flag note). */
export function productSummaries(): ProductSummary[] {
  return withDb([] as ProductSummary[], (db) => {
    return db
      .prepare(
        `SELECT p.slug, p.name, p.queries, p.sites,
                COUNT(l.id) AS listing_count,
                SUM(CASE WHEN l.hard_fails = '[]' AND l.hidden_at IS NULL THEN 1 ELSE 0 END) AS qualifying_count,
                SUM(CASE WHEN l.flags != '[]' THEN 1 ELSE 0 END) AS flagged_count,
                MAX(l.last_seen) AS last_seen
         FROM products p
         LEFT JOIN listings l ON l.product_slug = p.slug
         GROUP BY p.slug
         ORDER BY p.name COLLATE NOCASE`
      )
      .all()
      .map((r: any) => ({
        slug: r.slug,
        name: r.name,
        queryCount: JSON.parse(r.queries).length,
        sites: JSON.parse(r.sites),
        listingCount: r.listing_count,
        qualifyingCount: r.qualifying_count ?? 0,
        flaggedCount: r.flagged_count ?? 0,
        lastSeen: r.last_seen,
      }));
  });
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
    // Pinned rows sort first so they always survive the LIMIT — a pin on
    // a low-scoring listing would otherwise vanish from the page entirely
    // once a product has more than `limit` qualifying rows. The page
    // re-sorts within each bucket, so this only decides who gets in.
    const pinnedFirst = pinningAvailable() ? "(pinned_at IS NOT NULL) DESC, " : "";
    sql += ` ORDER BY ${pinnedFirst}score DESC NULLS LAST, price ASC NULLS LAST LIMIT ?`;
    args.push(f.limit ?? 100);
    return db.prepare(sql).all(...args);
  });
  const listings: Listing[] = rows.map((r: any) => ({
    ...r,
    hard_fails: JSON.parse(r.hard_fails),
    flags: JSON.parse(r.flags ?? "[]"),
    pinned_at: r.pinned_at ?? null, // absent entirely on a pre-migration db
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

/** Generic JSON-typed settings read, mirroring storage.py's
 *  get_setting (the write side is lib/settings.ts's setSetting, which
 *  mirrors set_setting). `fallback` covers a missing key, a missing
 *  db, and unparseable JSON alike. */
export function getSetting<T>(key: string, fallback: T): T {
  return withDb(fallback, (db) => {
    const r: any = db.prepare("SELECT value FROM settings WHERE key = ?").get(key);
    if (!r) return fallback;
    try {
      return JSON.parse(r.value) as T;
    } catch {
      return fallback;
    }
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
