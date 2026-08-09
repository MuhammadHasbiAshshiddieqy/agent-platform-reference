# Multi-stage, non-root, slim (§18). Build context is repo root — see
# services/harness/Dockerfile for why the final stage COPYs
# packages/contracts/src too (§4.1 rule 2, CLAUDE.md boundary #6).
#
# Also COPYs seed/users.yaml — the same file mock-business-api reads
# (ADR-009, "never a copy"). docker-compose.yml additionally bind-mounts
# it at runtime so edits don't require a rebuild.

FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/mock-idp services/mock-idp
RUN uv sync --frozen --no-dev --package mock-idp

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/mock-idp/src /app/services/mock-idp/src
COPY --from=builder /app/packages/contracts/src /app/packages/contracts/src
COPY seed/users.yaml /app/seed/
ENV PATH="/app/.venv/bin:${PATH}" PYTHONPATH=/app/services/mock-idp/src
USER appuser
EXPOSE 8087
CMD ["uvicorn", "mock_idp.main:app", "--host", "0.0.0.0", "--port", "8087"]
