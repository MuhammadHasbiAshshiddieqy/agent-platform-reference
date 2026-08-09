# Thin image: config + LiteLLM, not an application (§5.4). Build context is
# the repo root (see deploy/docker-compose.yml) so this COPY path works.
FROM ghcr.io/berriai/litellm:main-stable

COPY config/model-router/config.yaml /app/config.yaml

EXPOSE 4000

ENTRYPOINT ["litellm"]
CMD ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
