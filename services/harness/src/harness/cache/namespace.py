"""§10's cache namespace — `acl_hash` is the security-critical piece: a
cached answer built from ACL-gated retrieval must never surface to a user
whose ACL groups don't match, even within the same tenant (§26.2 step
6d's "the most important assertion in the whole demo" — a leak here is
invisible to every tenant-isolation test, since both users are in the
same tenant). Keyed only on `acl_group_ids` (not `user_id`), so two
different users with an identical ACL set correctly share one cache entry
(step 6c) while a user with even one differing group correctly misses
(step 6d).
"""

from __future__ import annotations

import hashlib


def acl_hash(acl_group_ids: list[str]) -> str:
    joined = ",".join(sorted(acl_group_ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]
