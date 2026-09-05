/** POST /api/hide — form fields `id`, `hidden` ("1" hides, "0"
 * unhides) and optional `back` (same-origin path to return to). With a
 * fetch caller (Accept: application/json) answers JSON; a plain form
 * post is redirected back so the page works without JavaScript.
 * Cross-site posts are refused: the Origin header's host must equal
 * the Host header (Astro's own check is off — see astro.config.mjs). */
import type { APIRoute } from "astro";
import { setHidden } from "../../lib/hide";
import { sameOrigin } from "../../lib/request";

export const prerender = false;

export const POST: APIRoute = async ({ request }) => {
  if (!sameOrigin(request)) return new Response("Cross-site POST refused", { status: 403 });
  const form = await request.formData();
  const id = Number(form.get("id"));
  const hidden = form.get("hidden") !== "0";
  const ok = setHidden(id, hidden);
  if (request.headers.get("accept")?.includes("application/json"))
    return new Response(JSON.stringify({ ok, id, hidden }), {
      status: ok ? 200 : 404,
      headers: { "content-type": "application/json" },
    });
  const back = String(form.get("back") ?? "/");
  const location = back.startsWith("/") && !back.startsWith("//") ? back : "/";
  return new Response(null, { status: 303, headers: { location } });
};
