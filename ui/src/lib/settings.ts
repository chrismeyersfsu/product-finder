/**
 * The UI's settings write path, alongside hide.ts and products.ts:
 * upserts one JSON-typed row in the `settings` table (key/value),
 * mirroring storage.py's set_setting exactly (INSERT ... ON CONFLICT
 * DO UPDATE SET value). Opens the db read-write for a single
 * statement and closes it; never creates the db, never touches any
 * other table. The read side is db.ts's getSetting. Server-side only
 * — called from page POST handlers.
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

export function setSetting(key: string, value: unknown): boolean {
  const p = dbPath();
  if (!p) return false;
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { fileMustExist: true });
    db.pragma("busy_timeout = 10000");
    db.prepare(
      "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    ).run(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  } finally {
    db?.close();
  }
}
