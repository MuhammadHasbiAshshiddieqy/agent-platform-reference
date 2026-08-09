# Multi-stage, non-root, slim (§18). Build context is the repo root — see
# services/harness/Dockerfile for why (§4.1 rule 2 — never leak another
# service's code into this image).

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/gateway services/gateway
RUN uv sync --frozen --no-dev --package gateway

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/gateway/src /app/services/gateway/src
# `contracts` is installed editable, pointing at this exact absolute path
# from the builder stage — must exist here too, or the import resolves to
# nothing at runtime even though `uv sync` succeeded at build time.
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/gateway/src
USER appuser
EXPOSE 8080
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
