/**
 * Product CRUD: the UI's other write path, alongside hide.ts. Owns
 * parsing the product form into a validated ProductInput (pure,
 * collects every error rather than stopping at the first), and the
 * INSERT/UPDATE/DELETE against the `products` table. Mirrors
 * storage.py's upsert_product / delete_product exactly: same column
 * set, same ON CONFLICT semantics, created_at kept across an update,
 * updated_at stamped fresh. Opens the db read-write for a single
 * statement (or, for delete, one transaction) and closes it; never
 * creates the db. Never scores or validates listings — this module
 * only ever touches the `products` row and, on delete, that product's
 * `listings` rows (never price_history, mirroring storage.py).
 * Server-side only.
 */
import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

export const CRITERIA_OPS = ["gte", "lte", "eq", "contains", "one_of", "matches", "exists"] as const;
export type CriteriaOp = (typeof CRITERIA_OPS)[number];

export const EXTRACTOR_TYPES = ["int", "float", "str", "bool", "size_gb"] as const;
export type ExtractorType = (typeof EXTRACTOR_TYPES)[number];

export interface CriteriaRule {
  field: string;
  op: CriteriaOp;
  value?: unknown;
  weight?: number;
  required?: boolean;
  reject?: boolean;
  flag?: boolean;
  note?: string;
}

export interface ExtractorSpec {
  pattern: string;
  type: ExtractorType;
  group?: number;
  fields?: string[];
}

export interface ProductInput {
  slug: string;
  name: string;
  description: string;
  queries: string[];
  criteria: CriteriaRule[];
  extractors: Record<string, ExtractorSpec>;
  manual_checks: string[];
  sites: string[];
  max_price: number | null;
}

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;

/** Resolves the product-finder sqlite file (PF_DB in production).
 * Exported so scrapeQueue.ts can derive the scrape-now directory from
 * the same path without duplicating the search order. */
export function dbPath(): string | null {
  const candidates = [
    process.env.PF_DB,
    path.resolve(process.cwd(), "product_finder.db"),
    path.resolve(process.cwd(), "..", "product_finder.db"),
  ].filter((p): p is string => !!p);
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return null;
}

/** Derives a slug from a product name for the simple add flow:
 * lowercase, every run of non `[a-z0-9]` collapsed to one hyphen,
 * leading/trailing hyphens trimmed, capped at 64 chars (trimming a
 * trailing hyphen again after the cap). Pure; may still return a
 * string too short to pass SLUG_RE, which the caller must check. */
export function slugify(name: string): string {
  let s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (s.length > 64) s = s.slice(0, 64).replace(/-+$/g, "");
  return s;
}

function lines(v: FormDataEntryValue | null): string[] {
  return String(v ?? "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Parses + validates a product form. Pure: collects every error
 * (human-readable, e.g. "criteria[2].op must be one of …") rather than
 * stopping at the first, so the caller can re-render them all at once.
 * `opts.slugLocked` fixes the slug for an edit (the form's slug field,
 * if any, is ignored); `opts.knownSites` is the roster the `sites`
 * checkboxes are validated against. On create, a blank slug is
 * derived from the name via slugify(); a blank queries list defaults
 * to [name] — this is what makes the simple ("just a name") and
 * advanced forms interchangeable submissions to the same handler. */
export function parseProductForm(
  form: FormData,
  opts: { slugLocked?: string; knownSites?: string[] } = {}
): { product: ProductInput; errors: string[] } {
  const errors: string[] = [];
  const knownSites = opts.knownSites ?? [];

  const name = String(form.get("name") ?? "").trim();
  if (!name) errors.push("name is required");

  const rawSlug = opts.slugLocked ?? String(form.get("slug") ?? "").trim();
  const derivingSlug = !opts.slugLocked && !rawSlug;
  const slug = derivingSlug ? slugify(name) : rawSlug;
  if (!SLUG_RE.test(slug)) {
    errors.push(
      derivingSlug
        ? "could not make a slug from that name — enter one"
        : "slug must be lowercase letters, digits, and hyphens (2-64 chars), starting with a letter or digit"
    );
  }

  const description = String(form.get("description") ?? "").trim();

  let queries = lines(form.get("queries"));
  if (queries.length === 0 && name) queries = [name];
  if (queries.length === 0) errors.push("at least one query is required");

  const manual_checks = lines(form.get("manual_checks"));

  const sitesRaw = form.getAll("sites").map(String);
  const sites = sitesRaw.filter((s) => knownSites.includes(s));
  for (const s of sitesRaw) if (!knownSites.includes(s)) errors.push(`unknown site "${s}"`);

  let max_price: number | null = null;
  const maxPriceRaw = String(form.get("max_price") ?? "").trim();
  if (maxPriceRaw) {
    const n = Number(maxPriceRaw);
    if (!Number.isFinite(n) || n <= 0) errors.push("max_price must be a positive number");
    else max_price = n;
  }

  const criteria = parseCriteria(String(form.get("criteria") ?? "").trim() || "[]", errors);
  const extractors = parseExtractors(String(form.get("extractors") ?? "").trim() || "{}", errors);

  return {
    product: { slug, name, description, queries, criteria, extractors, manual_checks, sites, max_price },
    errors,
  };
}

function parseCriteria(raw: string, errors: string[]): CriteriaRule[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    errors.push(`criteria is not valid JSON: ${(e as Error).message}`);
    return [];
  }
  if (!Array.isArray(parsed)) {
    errors.push("criteria must be a JSON array of rule objects");
    return [];
  }
  const rules: CriteriaRule[] = [];
  parsed.forEach((rule: unknown, i: number) => {
    if (!isPlainObject(rule)) {
      errors.push(`criteria[${i}] must be an object`);
      return;
    }
    if (typeof rule.field !== "string" || !rule.field) errors.push(`criteria[${i}].field must be a non-empty string`);
    if (typeof rule.op !== "string" || !CRITERIA_OPS.includes(rule.op as CriteriaOp))
      errors.push(`criteria[${i}].op must be one of ${CRITERIA_OPS.join(", ")}`);
    if (rule.op !== "exists" && !("value" in rule)) errors.push(`criteria[${i}].value is required unless op is exists`);
    if (rule.op === "one_of" && "value" in rule && !Array.isArray(rule.value))
      errors.push(`criteria[${i}].value must be an array when op is one_of`);
    if (rule.weight !== undefined && typeof rule.weight !== "number") errors.push(`criteria[${i}].weight must be a number`);
    for (const b of ["required", "reject", "flag"] as const)
      if (rule[b] !== undefined && typeof rule[b] !== "boolean") errors.push(`criteria[${i}].${b} must be a boolean`);
    if (rule.note !== undefined && typeof rule.note !== "string") errors.push(`criteria[${i}].note must be a string`);
    rules.push(rule as unknown as CriteriaRule);
  });
  return rules;
}

function parseExtractors(raw: string, errors: string[]): Record<string, ExtractorSpec> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    errors.push(`extractors is not valid JSON: ${(e as Error).message}`);
    return {};
  }
  if (!isPlainObject(parsed)) {
    errors.push("extractors must be a JSON object of name -> spec");
    return {};
  }
  const out: Record<string, ExtractorSpec> = {};
  for (const [name, specRaw] of Object.entries(parsed)) {
    if (!isPlainObject(specRaw)) {
      errors.push(`extractors.${name} must be an object`);
      continue;
    }
    const spec = specRaw;
    if (typeof spec.pattern !== "string" || !spec.pattern) {
      errors.push(`extractors.${name}.pattern must be a non-empty string`);
    } else {
      // A JS RegExp is a proxy for Python's `re`, which the pattern
      // actually runs under — most patterns are valid in both.
      try {
        new RegExp(spec.pattern);
      } catch {
        errors.push(`extractors.${name}.pattern is not a valid regular expression`);
      }
    }
    if (typeof spec.type !== "string" || !EXTRACTOR_TYPES.includes(spec.type as ExtractorType))
      errors.push(`extractors.${name}.type must be one of ${EXTRACTOR_TYPES.join(", ")}`);
    if (spec.group !== undefined && (!Number.isInteger(spec.group) || (spec.group as number) < 0))
      errors.push(`extractors.${name}.group must be a non-negative integer`);
    if (spec.fields !== undefined && (!Array.isArray(spec.fields) || !spec.fields.every((f) => typeof f === "string")))
      errors.push(`extractors.${name}.fields must be an array of strings`);
    out[name] = spec as unknown as ExtractorSpec;
  }
  return out;
}

/** A stored or submitted product, loosely typed so this accepts both
 * ProductInput (freshly parsed) and db.ProductFull (read back from the
 * db) without coupling the two modules' types together. */
export interface ProductLike {
  slug?: string;
  name?: string;
  description?: string;
  queries?: string[];
  manual_checks?: string[];
  sites?: string[];
  max_price?: number | null;
  criteria?: unknown;
  extractors?: unknown;
}

/** Turns a stored (or partially-submitted) product into the string
 * values its form fields render: queries/manual_checks joined by
 * newline, criteria/extractors pretty-printed JSON. */
export function formDefaults(product?: ProductLike | null) {
  return {
    slug: product?.slug ?? "",
    name: product?.name ?? "",
    description: product?.description ?? "",
    queries: (product?.queries ?? []).join("\n"),
    manual_checks: (product?.manual_checks ?? []).join("\n"),
    sites: product?.sites ?? [],
    max_price: product?.max_price != null ? String(product.max_price) : "",
    criteria: JSON.stringify(product?.criteria ?? [], null, 2),
    extractors: JSON.stringify(product?.extractors ?? {}, null, 2),
  };
}

/** INSERT ... ON CONFLICT DO UPDATE, mirroring storage.upsert_product:
 * created_at is kept across an update, updated_at is stamped fresh. */
export function saveProduct(p: ProductInput): boolean {
  const file = dbPath();
  if (!file) return false;
  let db: Database.Database | null = null;
  try {
    db = new Database(file, { fileMustExist: true });
    db.pragma("busy_timeout = 10000");
    const now = new Date().toISOString().slice(0, 19) + "+00:00";
    db.prepare(
      `INSERT INTO products (slug, name, description, queries, criteria, extractors,
                             manual_checks, sites, max_price, created_at, updated_at)
       VALUES (@slug, @name, @description, @queries, @criteria, @extractors,
               @manual_checks, @sites, @max_price, @now, @now)
       ON CONFLICT(slug) DO UPDATE SET
         name=excluded.name, description=excluded.description, queries=excluded.queries,
         criteria=excluded.criteria, extractors=excluded.extractors,
         manual_checks=excluded.manual_checks, sites=excluded.sites,
         max_price=excluded.max_price,
         updated_at=excluded.updated_at`
    ).run({
      slug: p.slug,
      name: p.name,
      description: p.description,
      queries: JSON.stringify(p.queries),
      criteria: JSON.stringify(p.criteria),
      extractors: JSON.stringify(p.extractors),
      manual_checks: JSON.stringify(p.manual_checks),
      sites: JSON.stringify(p.sites),
      max_price: p.max_price,
      now,
    });
    return true;
  } catch {
    return false;
  } finally {
    db?.close();
  }
}

/** Deletes a product and its listings in one transaction (never
 * price_history — mirrors storage.delete_product). Returns true iff a
 * products row was actually deleted. */
export function deleteProduct(slug: string): boolean {
  const file = dbPath();
  if (!file) return false;
  let db: Database.Database | null = null;
  try {
    db = new Database(file, { fileMustExist: true });
    db.pragma("busy_timeout = 10000");
    const run = db.transaction((s: string) => {
      db!.prepare("DELETE FROM listings WHERE product_slug=?").run(s);
      return db!.prepare("DELETE FROM products WHERE slug=?").run(s).changes > 0;
    });
    return run(slug);
  } catch {
    return false;
  } finally {
    db?.close();
  }
}
