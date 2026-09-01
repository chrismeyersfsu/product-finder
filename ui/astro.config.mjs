// SSR everywhere: pages read the SQLite db per request via src/lib/db.ts.
import node from "@astrojs/node";
import { defineConfig } from "astro/config";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  server: { port: 4321, host: true },
  vite: { ssr: { external: ["better-sqlite3"] } },
});
