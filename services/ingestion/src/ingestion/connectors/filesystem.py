"""§5.11 — "connector ke sumber" for the reference/seed corpus. Real
connectors (Confluence, Google Drive, S3) join this module at production
time; the shape (`RawDocument` in, nothing source-specific leaking past
this file) is what makes that swap-in possible without touching the
pipeline.

Metadata lives as YAML frontmatter directly in each `.md` file (`tenant_id`,
`acl_group_ids`, ...) rather than a side-channel manifest — one file to
keep in sync, not two. No `python-frontmatter` dependency: this is one
small, fully-owned format (`---\\n<yaml>\\n---\\n<body>`), not worth a
library.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SOURCE = "filesystem"


@dataclass
class RawDocument:
    source_uri: str
    tenant_id: str
    acl_group_ids: list[str]
    title: str | None
    lang: str | None
    content: str  # frontmatter stripped
    content_hash: str


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    _, raw_meta, body = parts
    meta: dict[str, Any] = yaml.safe_load(raw_meta) or {}
    return meta, body.strip()


def load_filesystem_documents(directory: str) -> list[RawDocument]:
    docs: list[RawDocument] = []
    for path in sorted(Path(directory).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        docs.append(
            RawDocument(
                source_uri=f"file://{path.name}",
                tenant_id=str(meta.get("tenant_id", "tnt_demo")),
                acl_group_ids=[str(g) for g in meta.get("acl_group_ids", [])],
                title=meta.get("title"),
                lang=meta.get("lang"),
                content=body,
                content_hash=content_hash,
            )
        )
    return docs
