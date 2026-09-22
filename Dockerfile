# GlamBought server image (AWS Lightsail container service). Secrets come in as environment variables.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY web ./web
COPY optimizer ./optimizer
COPY seed ./seed
COPY cache/*.json ./cache/
RUN uv sync --frozen --no-dev && mkdir -p logs tokens
ENV PATH="/app/.venv/bin:$PATH" PORT=8787
EXPOSE 8787
CMD ["sh", "-c", "uvicorn glam_bought.server:app --host 0.0.0.0 --port ${PORT}"]
