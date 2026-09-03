// SSR everywhere: pages read the SQLite db per request via src/lib/db.ts.
import node from "@astrojs/node";
import { defineConfig } from "astro/config";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  server: { port: 4321, host: true },
  // Astro's built-in origin check compares against a URL it rebuilds as
  // "http://localhost" unless every public hostname is enumerated, so
  // it rejected same-origin posts both locally and behind the tunnel.
  // The one POST route (/api/hide) does its own Origin-vs-Host check.
  security: { checkOrigin: false },
  vite: { ssr: { external: ["better-sqlite3"] } },
});
