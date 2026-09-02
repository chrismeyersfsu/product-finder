#!/usr/bin/env bash
# Install/refresh the Product Finder systemd user units and podman
# quadlets. Idempotent — rerun after changing units or app code (rebuilds
# both images; mcp/ui restart onto them). The gate, the image builds, and
# the restarts each skip themselves when their inputs are unchanged, so a
# no-op rerun takes seconds. Rootless; the same .container files move to
# /etc/containers/systemd/ on a future dedicated host.
# Pattern: caseworkflow/docs/patterns/deployment.md.
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(cd ../.. && pwd)"

TUNNEL_ID=388883e5-bcfc-48cd-9e12-c08da5d20835
TUNNEL_HOST=product-finder.judicialschedule.com

QUADLET_DIR="$HOME/.config/containers/systemd"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$QUADLET_DIR" "$UNIT_DIR"

git -C "$REPO" config core.hooksPath .githooks

echo "== gate =="
(cd "$REPO" && uv run ruff check packages/ && uv run ruff format --check packages/)

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/product-finder"
mkdir -p "$CACHE_DIR"

echo "== building images =="
# Build from a staging mirror, not the repo: node_modules/, the db, and
# caches would otherwise ride into the context on every build (rsync
# makes the no-change case free).
CTX="$CACHE_DIR/build-ctx"
mkdir -p "$CTX"
rsync -a --delete \
    --exclude=/.git --exclude=/.venv --exclude=/data \
    --exclude=/ui/node_modules --exclude=/ui/dist \
    --exclude="*.db" --exclude="*.db-wal" --exclude="*.db-shm" \
    --exclude=/.pytest_cache --exclude=/.ruff_cache \
    --exclude="__pycache__/" --exclude="*.pyc" \
    "$REPO/" "$CTX/"
old_mcp=$(podman image inspect -f '{{.Id}}' localhost/product-finder:latest 2>/dev/null || echo none)
old_ui=$(podman image inspect -f '{{.Id}}' localhost/product-finder-ui:latest 2>/dev/null || echo none)
podman build -q -t product-finder --target browser -f "$CTX/Dockerfile" "$CTX"
podman build -q -t product-finder-ui -f "$CTX/Dockerfile.ui" "$CTX"
new_mcp=$(podman image inspect -f '{{.Id}}' localhost/product-finder:latest)
new_ui=$(podman image inspect -f '{{.Id}}' localhost/product-finder-ui:latest)

echo "== data dir =="
# One-time migration: adopt a repo-root db from the pre-quadlet era so
# existing listings/backtests survive. WAL sidecars move with it.
mkdir -p "$REPO/data"
if [[ ! -f "$REPO/data/product_finder.db" && -f "$REPO/product_finder.db" ]]; then
    for f in product_finder.db product_finder.db-wal product_finder.db-shm; do
        [[ -f "$REPO/$f" ]] && cp "$REPO/$f" "$REPO/data/$f"
    done
    echo "migrated repo-root db into data/"
fi

echo "== secrets =="
# Site API keys are read by the scrape and mcp containers from this
# EnvironmentFile; never committed. Created empty with a template so the
# operator only has to fill in values.
SECRETS="$HOME/.config/product-finder/secrets.env"
if [[ ! -f "$SECRETS" ]]; then
    mkdir -p "$(dirname "$SECRETS")"
    (umask 177 && cat > "$SECRETS" <<'TPL'
# product-finder site API keys — KEY=value, one per line, no quotes.
# EBAY_CLIENT_ID=
# EBAY_CLIENT_SECRET=
# KROGER_CLIENT_ID=
# KROGER_CLIENT_SECRET=
# BESTBUY_API_KEY=
# WALMART_API_KEY=
# FB_COOKIES=
TPL
    )
    echo "created $SECRETS (fill in API keys, then rerun)"
fi
chmod 600 "$SECRETS"

echo "== cloudflare tunnel =="
if [[ ! -f "$HOME/.cloudflared/product-finder.yml" ]]; then
    cat > "$HOME/.cloudflared/product-finder.yml" <<CFG
tunnel: $TUNNEL_ID
credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $TUNNEL_HOST
    service: http://127.0.0.1:4321
  - service: http_status:404
CFG
fi
# Route is create-once; tolerate the record already existing — but only
# when it targets THIS tunnel. Without --config and the UUID, cloudflared
# reads the default config.yml and binds the record to that tunnel instead.
out=$(cloudflared --config "$HOME/.cloudflared/product-finder.yml" \
    tunnel route dns "$TUNNEL_ID" "$TUNNEL_HOST" 2>&1) || true
if grep -qi "already exists\|already configured" <<<"$out" && ! grep -q "$TUNNEL_ID" <<<"$out"; then
    echo "$out"
    echo "DNS record for $TUNNEL_HOST targets a DIFFERENT tunnel; fix with:"
    echo "    cloudflared --config ~/.cloudflared/product-finder.yml tunnel route dns --overwrite-dns $TUNNEL_ID $TUNNEL_HOST"
    exit 1
elif ! grep -qiE "Added CNAME|already" <<<"$out"; then
    echo "$out"; exit 1
fi
echo "route: $TUNNEL_HOST -> tunnel $TUNNEL_ID"

echo "== installing units =="
# Ad-hoc dev processes from before the quadlet era hold :4321; the
# script never kills anything itself — it asks and stops instead.
if pgrep -f 'node dist/server/entry.mjs' >/dev/null 2>&1; then
    echo "a dev UI server is holding :4321 — stop it first:"
    echo "    pkill -f 'node dist/server/entry.mjs'"
    exit 1
fi
# Stale host-process units would shadow the quadlet generator.
rm -f "$UNIT_DIR"/product-finder-ui.service "$UNIT_DIR"/product-finder-mcp.service
cp ./*.container "$QUADLET_DIR/"
cp ./*.service ./*.timer "$UNIT_DIR/"
systemctl --user daemon-reload
loginctl enable-linger "$USER" || true

echo "== starting services =="
systemctl --user enable --now product-finder-tunnel.service product-finder-scrape.timer
# Skip the restarts when nothing a running service consumes has changed:
# the images or the unit files.
INFRA_HASH=$( (cd "$REPO" && find infra/systemd -type f -print0 \
    | sort -z | xargs -0 sha256sum) | sha256sum | cut -d' ' -f1)
if [[ "$new_mcp" != "$old_mcp" || "$new_ui" != "$old_ui" \
      || ! -f "$CACHE_DIR/infra.hash" \
      || $(cat "$CACHE_DIR/infra.hash") != "$INFRA_HASH" ]]; then
    systemctl --user restart product-finder-mcp product-finder-ui
    echo "$INFRA_HASH" > "$CACHE_DIR/infra.hash"
else
    echo "images and units unchanged — services left running"
fi

systemctl --user --no-pager --plain list-units 'product-finder-*'
echo "Timer: systemctl --user list-timers product-finder-scrape.timer"
echo "Done. Dashboard: https://$TUNNEL_HOST"
