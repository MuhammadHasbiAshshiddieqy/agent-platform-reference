# Multi-stage, non-root, slim (§18). Build context is repo root — see
# services/harness/Dockerfile for why the final stage COPYs
# packages/contracts/src too (§4.1 rule 2, CLAUDE.md boundary #6).
#
# Also COPYs seed/ — this service reads seed/business_state.json and
# seed/users.yaml directly (§27.1: "seed/users.yaml dinaikkan ke urutan
# awal karena dipakai bersama oleh mock-idp dan mock-business-api", ADR-009).
# docker-compose.yml additionally bind-mounts seed/ at runtime so `make
# reset` (state file edits) doesn't require a rebuild — this COPY is the
# fallback baseline the image can run with standalone.

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/mock-business-api services/mock-business-api
RUN uv sync --frozen --no-dev --package mock-business-api

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/mock-business-api/src /app/services/mock-business-api/src
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
COPY seed/business_state.json seed/users.yaml /app/seed/
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/mock-business-api/src
USER appuser
EXPOSE 8084
CMD ["uvicorn", "mock_business_api.main:app", "--host", "0.0.0.0", "--port", "8084"]
