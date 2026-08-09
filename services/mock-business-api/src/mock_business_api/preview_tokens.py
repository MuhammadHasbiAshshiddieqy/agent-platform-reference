"""§8.4: "execute hanya menerima preview_token, bukan parameter mentah" —
this is what stops an agent (or a compromised harness) from previewing a
low-risk change and executing a different, larger one. TTL 5 minutes,
single-use, tenant-scoped.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status


@dataclass
class PreviewEntry:
    action: str
    params: dict[str, Any]
    tenant_id: str
    requires_approval: bool
    expires_at: float
    consumed: bool = False


class PreviewTokenStore:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._tokens: dict[str, PreviewEntry] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self, *, action: str, params: dict[str, Any], tenant_id: str, requires_approval: bool
    ) -> str:
        token = f"prv_{uuid.uuid4().hex[:20]}"
        async with self._lock:
            self._tokens[token] = PreviewEntry(
                action=action,
                params=params,
                tenant_id=tenant_id,
                requires_approval=requires_approval,
                expires_at=time.time() + self._ttl,
            )
        return token

    async def consume(self, token: str, *, tenant_id: str, action: str) -> PreviewEntry:
        async with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid preview_token")
            if entry.tenant_id != tenant_id:
                # Same status as "not found" — never reveal that a token
                # exists for a different tenant (§7.3's isolation spirit
                # applied to this one extra piece of cross-tenant state).
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid preview_token")
            if entry.action != action:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="preview_token was issued for a different action",
                )
            if entry.consumed:
                raise HTTPException(status.HTTP_409_CONFLICT, detail="preview_token already used")
            if time.time() > entry.expires_at:
                raise HTTPException(status.HTTP_410_GONE, detail="preview_token expired")
            entry.consumed = True
            return entry
