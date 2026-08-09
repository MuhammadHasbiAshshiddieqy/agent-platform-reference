"""§5.3's tool-schema generation. M5's own provisional `TOOLS` dict +
`available_tools(allow_mutations=...)` are gone as of M5b — tool
definitions now come from `config/tools/*.yaml` (`harness.authz.manifest.
ToolManifestEntry`), and which tools are available for a given run is
`PolicyResolver.resolve()`'s job (§22.1's five-set intersection), not a
plain `allow_mutations` filter. This module is left with exactly one
responsibility: turn a manifest entry into the JSON schema shape
model-router's tool-calling API expects.
"""

from __future__ import annotations

from typing import Any

from harness.authz.manifest import ToolManifestEntry


def to_openai_schema(tool: ToolManifestEntry) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description_for_model,
            "parameters": tool.parameters_model.model_json_schema(),
        },
    }
