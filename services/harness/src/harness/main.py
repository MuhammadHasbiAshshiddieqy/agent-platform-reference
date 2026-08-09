from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from harness.api.routes import router
from harness.authz.agent_profile import load_agent_profiles
from harness.authz.killswitch import KillswitchChecker
from harness.authz.manifest import load_tool_manifests
from harness.authz.policy_resolver import YamlPolicyResolver
from harness.cache.store import SemanticCacheStore
from harness.clients.business_api import BusinessApiClient
from harness.clients.embedding import EmbeddingClient
from harness.clients.model_router import ModelRouterClient
from harness.clients.retrieval import RetrievalClient
from harness.clients.token_exchange import TokenExchangeClient
from harness.config import settings
from harness.graph.build import build_graph
from harness.observability.tracing import get_langfuse_client
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    model_router = ModelRouterClient(
        settings.model_router_url, settings.model_router_key, settings.model_router_timeout_seconds
    )
    retrieval = RetrievalClient(settings.retrieval_url, settings.retrieval_timeout_seconds)
    business_api = BusinessApiClient(
        settings.business_api_url, settings.business_api_timeout_seconds
    )
    token_exchange = TokenExchangeClient(settings.mock_idp_url, settings.mock_idp_timeout_seconds)
    redis = Redis.from_url(settings.redis_url)
    embedding_client = EmbeddingClient(
        settings.model_router_url, settings.model_router_key, settings.embedding_model_alias
    )
    # §10/§5.7 — a SEPARATE connection pinned to db 0; see config.py's
    # `semantic_cache_redis_url` docstring for why it can't share
    # `redis`/db 2 above (RediSearch's own db-0-only limitation).
    cache_redis = Redis.from_url(settings.semantic_cache_redis_url)
    cache_store = SemanticCacheStore(
        cache_redis,
        vector_dim=settings.semantic_cache_vector_dim,
        similarity_threshold=settings.semantic_cache_similarity_threshold,
    )
    await cache_store.ensure_index()

    # §21/ADR-011 — raises (fails boot) if HARNESS_AUDIENCE has zero
    # matching tools under tools_dir; see authz/manifest.py's docstring.
    tool_manifests = load_tool_manifests(settings.tools_dir, audience=settings.harness_audience)
    agent_profiles = load_agent_profiles(settings.agents_dir, audience=settings.harness_audience)
    killswitch = KillswitchChecker(redis)
    policy_resolver = YamlPolicyResolver(
        tool_manifests=tool_manifests, agent_profiles=agent_profiles, killswitch=killswitch
    )

    app.state.business_api = business_api
    app.state.tool_manifests = tool_manifests
    app.state.audience = settings.harness_audience
    app.state.redis = redis
    app.state.killswitch = killswitch
    app.state.langfuse = get_langfuse_client()
    app.state.cache_store = cache_store

    # §23.1 invariant #1: PostgresSaver, never MemorySaver. The #1 mistake
    # in LangGraph deployments — works fine with one instance, breaks
    # silently the moment there's more than one.
    async with AsyncPostgresSaver.from_conn_string(settings.database_url_psycopg) as checkpointer:
        await checkpointer.setup()
        app.state.graph = build_graph(
            model_router,
            retrieval,
            business_api,
            policy_resolver,
            tool_manifests,
            token_exchange,
            settings.harness_audience,
            embedding_client,
            cache_store,
            agent_profiles,
        ).compile(checkpointer=checkpointer)
        yield

    await model_router.aclose()
    await retrieval.aclose()
    await business_api.aclose()
    await token_exchange.aclose()
    await embedding_client.aclose()
    await redis.aclose()
    await cache_redis.aclose()


app = FastAPI(title="agent-harness", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
