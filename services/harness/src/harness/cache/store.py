"""§10/§5.7 semantic cache store — Redis (`redis/redis-stack-server`) via
RediSearch vector KNN.

Deliberately a SEPARATE Redis logical-DB connection from harness's other
Redis usage (killswitch, `settings.redis_url`'s db=2): RediSearch's
FT.CREATE/FT.SEARCH only operate on logical DB 0 — confirmed live
against the running `redis-stack-server` container
(`redis.exceptions.ResponseError: Cannot create index on db != 0`), not
a design choice. `settings.semantic_cache_redis_url` points at db 0.

TAG-field query values need RediSearch's own escaping (a bare `-` or `@`
inside a `{...}` tag filter is otherwise parsed as query syntax) —
`agent_id` values like `hr-assistant` and `prompt_version` values like
`system_prompt@v1` both hit this in practice, verified live before
writing this module (a naive unescaped filter silently matched nothing).
"""

from __future__ import annotations

import re
import struct
import uuid

from contracts.agent import Citation
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.commands.search.field import Field, TagField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

INDEX_NAME = "semcache:idx"
KEY_PREFIX = "semcache:"
DOCUMENT_ID_SEPARATOR = "|"

_TAG_SPECIAL = re.compile(r"([,.<>{}\[\]\"':;!@#$%^&*()\-+=~\s])")


def _escape_tag(value: str) -> str:
    return _TAG_SPECIAL.sub(r"\\\1", value)


def _vector_bytes(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


class CachedPayload(BaseModel):
    output_text: str
    citations: list[Citation]
    document_ids: list[str]


class SemanticCacheStore:
    def __init__(
        self,
        redis: Redis,
        *,
        vector_dim: int = 1024,
        similarity_threshold: float = 0.95,
    ) -> None:
        self._redis = redis
        self._vector_dim = vector_dim
        # §10's own text: "ambang batas 0.95 itu konservatif dan
        # disengaja" — deliberately not tunable via a low-friction path.
        self._threshold = similarity_threshold

    async def ensure_index(self) -> None:
        try:
            await self._redis.ft(INDEX_NAME).info()  # type: ignore[no-untyped-call]
            return
        except ResponseError:
            pass
        schema: list[Field] = [
            TagField("tenant_id"),
            TagField("agent_id"),
            TagField("acl_hash"),
            TagField("prompt_version"),
            TagField("document_ids", separator=DOCUMENT_ID_SEPARATOR),
            VectorField(
                "embedding",
                "HNSW",
                {"TYPE": "FLOAT32", "DIM": self._vector_dim, "DISTANCE_METRIC": "COSINE"},
            ),
        ]
        await self._redis.ft(INDEX_NAME).create_index(
            schema,
            definition=IndexDefinition(  # type: ignore[no-untyped-call]
                prefix=[KEY_PREFIX], index_type=IndexType.HASH
            ),
        )

    async def lookup(
        self,
        embedding: list[float],
        *,
        tenant_id: str,
        agent_id: str,
        acl_hash: str,
        prompt_version: str,
    ) -> CachedPayload | None:
        filter_expr = (
            f"(@tenant_id:{{{_escape_tag(tenant_id)}}} "
            f"@agent_id:{{{_escape_tag(agent_id)}}} "
            f"@acl_hash:{{{_escape_tag(acl_hash)}}} "
            f"@prompt_version:{{{_escape_tag(prompt_version)}}})"
        )
        query = (
            Query(f"{filter_expr}=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("score", "body")
            .dialect(2)
        )
        result = await self._redis.ft(INDEX_NAME).search(
            query, query_params={"vec": _vector_bytes(embedding)}
        )
        if not result.docs:
            return None
        doc = result.docs[0]
        # RediSearch's COSINE metric returns *distance* (0 = identical,
        # up to 2 = opposite), not similarity — confirmed live (an
        # orthogonal probe vector scored exactly 1.0, a near-identical
        # one scored ~0.0013). similarity = 1 - distance.
        similarity = 1 - float(doc.score)
        if similarity < self._threshold:
            return None
        return CachedPayload.model_validate_json(doc.body)

    async def write(
        self,
        embedding: list[float],
        *,
        tenant_id: str,
        agent_id: str,
        acl_hash: str,
        prompt_version: str,
        ttl_seconds: int,
        payload: CachedPayload,
    ) -> None:
        key = f"{KEY_PREFIX}{tenant_id}:{uuid.uuid4().hex[:20]}"
        document_ids_value = (
            DOCUMENT_ID_SEPARATOR.join(payload.document_ids) if payload.document_ids else "none"
        )
        await self._redis.hset(
            key,
            mapping={
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "acl_hash": acl_hash,
                "prompt_version": prompt_version,
                "document_ids": document_ids_value,
                "embedding": _vector_bytes(embedding),
                "body": payload.model_dump_json(),
            },
        )
        await self._redis.expire(key, ttl_seconds)

    async def invalidate_by_documents(self, *, tenant_id: str, document_ids: list[str]) -> int:
        """§10's invalidation rule, called from `POST /internal/v1/cache/
        invalidate` (ingestion-service's document-changed event). Deletes
        every entry whose citations included any of the given
        document_ids — a document that changed even once is reason
        enough to drop the whole cached answer rather than try to patch
        it in place."""
        if not document_ids:
            return 0
        tag_clause = " | ".join(_escape_tag(d) for d in document_ids)
        filter_expr = f"(@tenant_id:{{{_escape_tag(tenant_id)}}} @document_ids:{{{tag_clause}}})"
        query = Query(filter_expr).paging(0, 1000).no_content()
        result = await self._redis.ft(INDEX_NAME).search(query)
        keys = [doc.id for doc in result.docs]
        if keys:
            await self._redis.delete(*keys)
        return len(keys)
