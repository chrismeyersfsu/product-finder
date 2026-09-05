/**
 * View model for the Monitor page: hourly-sync run progress and the
 * on-demand scrape-now queue. Owns reading state/hourly.json and
 * state/requested.json — see scrape.py's module docstring for the
 * shape, this module mirrors it exactly — plus the queue/running/done
 * dirs via scrapeQueue.ts's baseDir/listDir; never writes anything of
 * its own. A missing or corrupt state file reads as null rather than
 * throwing, same as scrapeQueue's "nothing going on" convention; the
 * page renders that as "never run". Every helper below is pure given
 * an explicit `now`, so it's deterministic and unit-testable.
 * Server-side only (uses node:fs via scrapeQueue.ts).
 */
import fs from "node:fs";
import path from "node:path";
import { baseDir, listDir, RUNNING_STALE_MS, scrapeStatus } from "./scrapeQueue";

export interface RunResult {
  line: string;
  stored: number;
  errors: number;
  seconds: number;
  finished_at: string;
}

export interface RunState {
  mode: "hourly" | "requested";
  started_at: string;
  finished_at: string | null;
  products: string[];
  current: string | null;
  current_started_at: string | null;
  results: Record<string, RunResult>;
  exit: 0 | 1 | null;
}

const DIED_AFTER_MS = 60 * 60 * 1000;
const DEFAULT_PRODUCT_SECONDS = 80;
const DONE_CAP = 20;

/** Reads state/<mode>.json. Null for a missing file, an unreadable
 * one, or JSON that doesn't look like a run state (never throws). */
export function readState(mode: "hourly" | "requested"): RunState | null {
  const base = baseDir();
  if (!base) return null;
  try {
    const raw = fs.readFileSync(path.join(base, "state", `${mode}.json`), "utf8");
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object" || !Array.isArray(data.products)) return null;
    return data as RunState;
  } catch {
    return null;
  }
}

export type RunStatus = "running" | "finished" | "died" | "never";

/** "never" with no state file at all; "finished" once finished_at is
 * set; "died" when started over an hour ago and never finished (the
 * scrape process crashed mid-run, leaving `current` stuck); otherwise
 * "running". */
export function runStatus(state: RunState | null, now: Date = new Date()): RunStatus {
  if (!state) return "never";
  if (state.finished_at) return "finished";
  const startedMs = Date.parse(state.started_at);
  if (!Number.isNaN(startedMs) && now.getTime() - startedMs > DIED_AFTER_MS) return "died";
  return "running";
}

/** Seconds from an ISO timestamp to `now` (or null if it doesn't
 * parse); never negative. */
export function secondsSince(iso: string | null, now: Date = new Date()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now.getTime() - t) / 1000);
}

/** Next top-of-hour at or after `now` — the timer is OnCalendar=hourly. */
export function nextHourlyRun(now: Date = new Date()): Date {
  const next = new Date(now);
  next.setMinutes(0, 0, 0);
  if (next.getTime() <= now.getTime()) next.setHours(next.getHours() + 1);
  return next;
}

/** Median seconds-per-product among this run's finished results, or
 * the 80s default before anything has finished. */
export function medianSeconds(state: RunState | null): number {
  const values = state ? Object.values(state.results).map((r) => r.seconds) : [];
  if (!values.length) return DEFAULT_PRODUCT_SECONDS;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

export type ProductPhase = "done" | "current" | "pending" | "not-planned";

export interface ProductProgress {
  phase: ProductPhase;
  position: number; // 1-based index in the run's planned order
  total: number;
  etaSeconds: number | null; // "pending" with an active current only
}

/** Where one product sits in a run's planned order: already done,
 * being scraped right now, or pending — with an ETA (remaining planned
 * products up to and including the one in flight, times
 * medianSeconds()) when the run is actively working through the list.
 * Null when the slug isn't in this run's planned order at all. */
export function productProgress(state: RunState | null, slug: string): ProductProgress | null {
  if (!state) return null;
  const idx = state.products.indexOf(slug);
  if (idx === -1) return null;
  const total = state.products.length;
  if (state.results[slug]) return { phase: "done", position: idx + 1, total, etaSeconds: null };
  if (state.current === slug) return { phase: "current", position: idx + 1, total, etaSeconds: null };
  const currentIdx = state.current ? state.products.indexOf(state.current) : -1;
  const etaSeconds = currentIdx === -1 ? null : Math.max(0, idx - currentIdx) * medianSeconds(state);
  return { phase: "pending", position: idx + 1, total, etaSeconds };
}

export type QueueItemKind = "running" | "queued" | "done";

export interface QueueItem {
  slug: string;
  kind: QueueItemKind;
  since: string; // ISO — queued/started/finished time, per kind
  position: number | null; // 1..n for queued items only
  doneLine: string | null; // first line of the summary, for done items
}

/** The on-demand queue as one ordered list: the running item first (if
 * any, and not stale — same 50-minute rule scrapeQueue.ts uses), then
 * queued items oldest-first with their 1..n position, then recently
 * finished ones newest-first (capped at 20). */
export function onDemandQueue(now: Date = new Date()): QueueItem[] {
  const items: QueueItem[] = [];

  const running = listDir("running").filter((e) => now.getTime() - e.mtimeMs <= RUNNING_STALE_MS);
  const runningEntry = running.sort((a, b) => a.mtimeMs - b.mtimeMs)[0];
  if (runningEntry) {
    items.push({
      slug: runningEntry.slug,
      kind: "running",
      since: new Date(runningEntry.mtimeMs).toISOString(),
      position: null,
      doneLine: null,
    });
  }

  const queued = listDir("queue").sort((a, b) => a.mtimeMs - b.mtimeMs);
  queued.forEach((q, i) => {
    items.push({
      slug: q.slug,
      kind: "queued",
      since: new Date(q.mtimeMs).toISOString(),
      position: i + 1,
      doneLine: null,
    });
  });

  const done = listDir("done")
    .sort((a, b) => b.mtimeMs - a.mtimeMs)
    .slice(0, DONE_CAP);
  for (const d of done) {
    items.push({
      slug: d.slug,
      kind: "done",
      since: new Date(d.mtimeMs).toISOString(),
      position: null,
      doneLine: scrapeStatus(d.slug).last?.text ?? null,
    });
  }

  return items;
}

/** True when the dashboard should keep auto-refreshing: the hourly run
 * is actively going, or there's on-demand activity (running or
 * queued — a recently-done item alone doesn't warrant it). */
export function isActive(hourly: RunState | null, queue: QueueItem[], now: Date = new Date()): boolean {
  if (runStatus(hourly, now) === "running") return true;
  return queue.some((q) => q.kind === "running" || q.kind === "queued");
}
