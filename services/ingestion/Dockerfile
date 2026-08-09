# Multi-stage, non-root, slim (§18). Build context is repo root — see
# services/harness/Dockerfile for why the final stage COPYs
# packages/contracts/src too (§4.1 rule 2, CLAUDE.md boundary #6).

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/ingestion services/ingestion
RUN uv sync --frozen --no-dev --package ingestion

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/ingestion/src /app/services/ingestion/src
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/ingestion/src
USER appuser
EXPOSE 8083
CMD ["uvicorn", "ingestion.main:app", "--host", "0.0.0.0", "--port", "8083"]
