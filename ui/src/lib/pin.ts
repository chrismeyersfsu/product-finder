/**
 * The UI's pin write path, mirroring hide.ts: pin or unpin a listing
 * (the opposite of Hide — a pinned listing floats to the top of a
 * results page, in its own bucket, instead of leaving the list).
 * Opens the db read-write for a single UPDATE of listings.pinned_at
 * and closes it; never inserts, deletes, or touches any other column,
 * and never creates the db. Tolerant of a db that hasn't been
 * migrated with the pinned_at column yet: the UPDATE then fails and
 * this returns false, same as an unknown listing id (see also
 * db.ts's pinningAvailable(), which the results page uses to hide the
 * Pin button entirely on such a db instead of offering a button that
 * always fails). Server-side only — called from the /api/pin route.
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

export function setPinned(listingId: number, pinned: boolean): boolean {
  const p = dbPath();
  if (!p || !Number.isInteger(listingId)) return false;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { fileMustExist: true });
    db.pragma("busy_timeout = 10000");
    // Re-pinning keeps the original stamp, mirroring hide.ts's own
    // COALESCE-on-set / NULL-on-clear shape for hidden_at.
    const now = new Date().toISOString().slice(0, 19) + "+00:00";
    const res = pinned
      ? db.prepare("UPDATE listings SET pinned_at = COALESCE(pinned_at, ?) WHERE id = ?").run(now, listingId)
      : db.prepare("UPDATE listings SET pinned_at = NULL WHERE id = ?").run(listingId);
    return res.changes > 0;
  } catch {
    return false; // also covers pinned_at not existing yet on an unmigrated db
  } finally {
    db?.close();
  }
}
