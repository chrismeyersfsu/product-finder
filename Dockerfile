# Two targets (see README "Container layout"):
#   mcp     — slim: API + plain-HTML scraping tiers only
#   browser — mcp + Playwright/Chromium so browser_css tiers run too
FROM python:3.12-slim AS mcp
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package product-finder-mcp
ENV PF_DB=/data/product_finder.db PF_PROJECT_ROOT=/app PATH="/app/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8848
CMD ["product-finder-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8848"]

FROM mcp AS browser
RUN uv sync --frozen --no-dev --package product-finder-mcp --extra browser \
    && playwright install --with-deps chromium
