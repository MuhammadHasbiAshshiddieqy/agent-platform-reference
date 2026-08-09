# Multi-stage, non-root, slim (§18). Build context is repo root — see
# services/harness/Dockerfile for why the final stage COPYs
# packages/contracts/src too (§4.1 rule 2, CLAUDE.md boundary #6).
#
# Also COPYs seed/webhook_secrets.yaml (dev-only callback_secret_ref ->
# HMAC key lookup, see webhook.py) — docker-compose.yml additionally
# bind-mounts it at runtime so edits don't require a rebuild, same
# reasoning as mock-business-api/mock-idp's seed mounts. /app/data is
# where the provisioned async-pool LiteLLM key is persisted
# (litellm_key.py) — a named volume, not baked into the image, since it's
# generated at first startup, not known at build time.

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/async-worker services/async-worker
RUN uv sync --frozen --no-dev --package async-worker

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/async-worker/src /app/services/async-worker/src
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
COPY seed/webhook_secrets.yaml /app/seed/
RUN mkdir -p /app/data && chown appuser:appuser /app/data
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/async-worker/src
USER appuser
EXPOSE 8085
CMD ["uvicorn", "async_worker.main:app", "--host", "0.0.0.0", "--port", "8085"]
