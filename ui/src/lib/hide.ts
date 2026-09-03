/**
 * The UI's one write path: hide a listing from deals, or unhide it.
 * Opens the db read-write for a single UPDATE of listings.hidden_at
 * (the column storage.py owns and query_listings honours) and closes
 * it; never inserts, deletes, or touches any other column, and never
 * creates the db. Server-side only — called from the /api/hide route.
 * Returns false when there is no db or no such listing.
 */
import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

function dbPath(): string | null {
  const candidates = [
    process.env.PF_DB,
    path.resolve(process.cwd(), "product_finder.db"),
    path.resolve(process.cwd(), "..", "product_finder.db"),
  ].filter((p): p is string => !!p);
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return null;
}

export function setHidden(listingId: number, hidden: boolean): boolean {
  const p = dbPath();
  if (!p || !Number.isInteger(listingId)) return false;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { fileMustExist: true });
    db.pragma("busy_timeout = 10000");
    // Re-hiding keeps the original stamp, mirroring storage.set_listing_hidden.
    const now = new Date().toISOString().slice(0, 19) + "+00:00";
    const res = hidden
      ? db.prepare("UPDATE listings SET hidden_at = COALESCE(hidden_at, ?) WHERE id = ?").run(now, listingId)
      : db.prepare("UPDATE listings SET hidden_at = NULL WHERE id = ?").run(listingId);
    return res.changes > 0;
  } catch {
    return false;
  } finally {
    db?.close();
  }
}
