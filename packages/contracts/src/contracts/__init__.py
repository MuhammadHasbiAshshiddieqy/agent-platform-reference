"""Shared Pydantic v2 contracts for the Duta agent platform.

§4.1: this is the only package another service may import. Nothing in here
may import from `gateway`, `harness`, `retrieval`, `ingestion`, `worker`,
or `eval` — enforced by `lint-imports` (root pyproject.toml).
"""
