# Multi-stage, non-root, slim (§18). Build context is the repo root (see
# deploy/docker-compose.yml) — this Dockerfile only ever COPYs its own
# service dir plus packages/contracts, never another service's code
# (§4.1 rule 2: dependencies must not leak between service images).
#
# Also COPYs config/tools and config/agents (§22.2/§22.7's manifest/
# profile YAML) — docker-compose.yml additionally bind-mounts both at
# runtime so editing a manifest doesn't require a rebuild; this COPY is
# the fallback baseline the image can run with standalone.

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/harness services/harness
RUN uv sync --frozen --no-dev --package harness

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/harness/src /app/services/harness/src
# `contracts` is installed editable, pointing at this exact absolute path
# from the builder stage — must exist here too, or the import resolves to
# nothing at runtime even though `uv sync` succeeded at build time.
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
COPY config/tools /app/config/tools
COPY config/agents /app/config/agents
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/harness/src
USER appuser
EXPOSE 8081
CMD ["uvicorn", "harness.main:app", "--host", "0.0.0.0", "--port", "8081"]
