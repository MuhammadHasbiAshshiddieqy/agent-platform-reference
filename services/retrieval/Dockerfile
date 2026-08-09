# Multi-stage, non-root, slim (§18). Build context is repo root — see
# services/harness/Dockerfile for why the final stage COPYs
# packages/contracts/src too (§4.1 rule 2, CLAUDE.md boundary #6).

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/retrieval services/retrieval
RUN uv sync --frozen --no-dev --package retrieval

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/retrieval/src /app/services/retrieval/src
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/retrieval/src
USER appuser
EXPOSE 8082
CMD ["uvicorn", "retrieval.main:app", "--host", "0.0.0.0", "--port", "8082"]
