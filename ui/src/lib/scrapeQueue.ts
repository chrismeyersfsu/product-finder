/**
 * "Scrape now" file queue: the UI's third write path, but it never
 * touches the sqlite db. A separate systemd job on the host watches
 * `<dirname(db file)>/scrape-now/queue/` for files dropped by this
 * module, drops `running/<slug>` (mtime = start) while it works, and
 * writes `done/<slug>` (mtime = finish, first line = summary) when
 * done. This module only ever creates/reads files in those three
 * subdirectories — it never deletes or otherwise mutates them, since
 * that's the scraper job's job. Callers rely on: enqueueScrape being
 * idempotent (an already-queued slug is still a successful enqueue)
 * and returning false (never throwing) on a bad slug, missing db, or
 * any fs error; scrapeStatus/scrapeStatuses never throwing either,
 * yielding the "nothing going on" shape instead; and a `running`
 * marker older than 50 minutes being treated as stale and ignored
 * (the host job died without cleaning up). baseDir, RUNNING_STALE_MS,
 * and listDir are exported so monitor.ts can build the Monitor page's
 * ordered on-demand queue from the same three directories without
 * duplicating the path/slug/staleness rules. Server-side only.
 */
import fs from "node:fs";
import path from "node:path";
import { dbPath, SLUG_RE } from "./products";

export const RUNNING_STALE_MS = 50 * 60 * 1000;

const ENTRY_RE = /^[a-z0-9-]+$/;

export function baseDir(): string | null {
  const file = dbPath();
  return file ? path.join(path.dirname(file), "scrape-now") : null;
}

export interface DirEntry {
  slug: string;
  mtimeMs: number;
}

/** Every valid-looking slug in one scrape-now subdir (queue/running/
 * done), with its mtime — the moment it was queued, started running,
 * or finished, respectively. Dotfiles and anything not matching the
 * slug shape scrape.py uses are skipped (same junk-filename rule as
 * the Python side); an empty list when the dir or the db don't exist.
 * Unsorted — callers order by mtime for their own purpose (oldest
 * first for the queue, newest first for done). */
export function listDir(sub: "queue" | "running" | "done"): DirEntry[] {
  const base = baseDir();
  if (!base) return [];
  try {
    return fs
      .readdirSync(path.join(base, sub))
      .filter((name) => ENTRY_RE.test(name))
      .map((slug) => ({ slug, mtimeMs: fs.statSync(path.join(base, sub, slug)).mtimeMs }));
  } catch {
    return [];
  }
}

/** Drops an empty file at queue/<slug>, requesting an out-of-band
 * scrape. Idempotent — an existing file is still a success. Returns
 * false for an invalid slug, when there's no db, or on any fs error. */
export function enqueueScrape(slug: string): boolean {
  if (!SLUG_RE.test(slug)) return false;
  const base = baseDir();
  if (!base) return false;
  try {
    const dir = path.join(base, "queue");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, slug), "");
    return true;
  } catch {
    return false;
  }
}

export interface ScrapeStatus {
  queued: boolean;
  runningSince: string | null;
  last: { at: string; text: string } | null;
}

const EMPTY_STATUS: ScrapeStatus = { queued: false, runningSince: null, last: null };

function runningSince(base: string, slug: string): string | null {
  try {
    const st = fs.statSync(path.join(base, "running", slug));
    if (Date.now() - st.mtimeMs > RUNNING_STALE_MS) return null;
    return new Date(st.mtimeMs).toISOString();
  } catch {
    return null;
  }
}

function lastDone(base: string, slug: string): { at: string; text: string } | null {
  try {
    const file = path.join(base, "done", slug);
    const st = fs.statSync(file);
    const text = fs.readFileSync(file, "utf8").split("\n", 1)[0]!.trim();
    return { at: new Date(st.mtimeMs).toISOString(), text };
  } catch {
    return null;
  }
}

/** One slug's status: whether it's queued, since when it's been
 * running (null if not / stale), and its last finished on-demand
 * scrape (null if none). */
export function scrapeStatus(slug: string): ScrapeStatus {
  const base = baseDir();
  if (!base) return EMPTY_STATUS;
  return {
    queued: fs.existsSync(path.join(base, "queue", slug)),
    runningSince: runningSince(base, slug),
    last: lastDone(base, slug),
  };
}

/** Every slug with any scrape-now activity, in one pass over the three
 * directories — cheap enough for the products list page to call on
 * every render. */
export function scrapeStatuses(): Map<string, ScrapeStatus> {
  const out = new Map<string, ScrapeStatus>();
  const base = baseDir();
  if (!base) return out;
  const slugs = new Set<string>();
  for (const sub of ["queue", "running", "done"]) {
    try {
      for (const name of fs.readdirSync(path.join(base, sub))) slugs.add(name);
    } catch {
      // subdir doesn't exist yet — nothing queued/running/done there
    }
  }
  for (const slug of slugs) out.set(slug, scrapeStatus(slug));
  return out;
}
