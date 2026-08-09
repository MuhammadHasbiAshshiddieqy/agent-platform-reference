"""§9.2's hard rule: guardrails must not fail open. A guardrail *deciding*
allow/redact/flag/block is a normal outcome, expressed as a `GuardrailEvent`
return value. `GuardrailServiceError` is the other case — the check itself
couldn't run (Presidio raised, model-router was unreachable) — and it must
propagate as an exception, not get swallowed into a default "allow". See
`api/routes.py` for where this becomes an HTTP 503, deliberately not the
same "degrade and continue" pattern `graph/build.py`'s `retrieve` node uses
for retrieval-service being down — RAG has an explicit spec-sanctioned
degraded mode (§5.3), guardrails explicitly do not (§9.2).
"""

from __future__ import annotations


class GuardrailServiceError(Exception):
    pass
