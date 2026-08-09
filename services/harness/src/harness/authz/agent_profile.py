"""§22.7's agent profile — `allowed_tools` is the design-time ceiling that
§22.1's intersection never exceeds no matter how high a user's real
permissions are. Loaded once at boot alongside the tool manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from contracts.common import Audience
from pydantic import BaseModel, ConfigDict, Field


class AgentModelConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sync: str
    async_: str = Field(alias="async")
    classifier: str


class RetrievalConfig(BaseModel):
    collections: list[str] = Field(default_factory=list)
    top_k: int = 8
    rerank: bool = True


class GuardrailsConfig(BaseModel):
    input: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)


class AgentProfile(BaseModel):
    """Parsed in full for fidelity to §22.7's YAML shape; only
    `allowed_tools` is actually consumed by the PolicyResolver today.
    `model`/`retrieval`/`guardrails`/`cacheable` describe a generalization
    (agent-profile-driven model/guardrail selection) this milestone
    doesn't wire up — every agent still runs M4's fixed guardrail set and
    `AgentState.model_alias`'s hardcoded default. That's a real scope cut,
    not an oversight: §22's DoD is tool authorization, not a config system
    for the whole graph.
    """

    agent_id: str
    audience: Audience
    version: int
    system_prompt_ref: str | None = None
    model: AgentModelConfig
    allowed_tools: list[str] = Field(default_factory=list)
    retrieval: RetrievalConfig | None = None
    guardrails: GuardrailsConfig | None = None
    cacheable: bool = False
    cache_ttl_seconds: int = 3600
    max_iterations: int = 8


def load_agent_profiles(profiles_dir: Path, *, audience: Audience) -> dict[str, AgentProfile]:
    """Unlike `load_tool_manifests`, an empty result here is not a boot
    failure — a deployment can legitimately have zero agent profiles for
    its audience yet (e.g. `harness-external` before `public-faq-bot` was
    added). §21.3's "gagal boot" language is about a *tool* manifest with
    zero matches, the harder failure mode this milestone's ADR-011 test
    targets."""
    profiles: dict[str, AgentProfile] = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        profile = AgentProfile.model_validate(raw)
        if profile.audience != audience:
            continue
        profiles[profile.agent_id] = profile
    return profiles
