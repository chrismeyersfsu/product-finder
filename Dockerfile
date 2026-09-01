# MCP server container: streamable-http on 8848, SQLite under /data.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev
ENV PF_DB=/data/product_finder.db PF_PROJECT_ROOT=/app PATH="/app/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8848
CMD ["product-finder-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8848"]
