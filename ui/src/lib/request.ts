/** Same-origin guard shared by every write route: the Origin header's
 * host must equal the Host header. Astro's own origin check is off
 * (see astro.config.mjs) so each POST route calls this itself. */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
