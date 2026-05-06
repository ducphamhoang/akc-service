#!/usr/bin/env python3
"""
Entity Inference Tests — INF-08, INF-09, INF-10
Covers Tier 2 routing via ENTITY_KB_MAPPING.
"""

import importlib
import json
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import akc_service.config as _cfg
from akc_service.api.main import app


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def entity_kb_client(tmp_path, monkeypatch):
    """TestClient with two KBs and an ENTITY_KB_MAPPING configured.

    Sets:
      AKC_SERVICE_KB_REGISTRY   = {"default": <tmp>, "physics": <tmp>}
      AKC_SERVICE_ENTITY_KB_MAPPING = {"entity:physics": "physics", "entity:*": "default"}

    Reloads akc_service.config and akc_service.api.routes after setting env vars
    so KB_REGISTRY and ENTITY_KB_MAPPING are parsed with the new values.

    Yields (TestClient(app), {"default": Path, "physics": Path}).
    """
    kb_default = tmp_path / "kb" / "default"
    kb_physics = tmp_path / "kb" / "physics"
    kb_default.mkdir(parents=True)
    kb_physics.mkdir(parents=True)

    registry = json.dumps({
        "default": str(kb_default),
        "physics": str(kb_physics),
    })
    mapping = json.dumps({
        "entity:physics": "physics",
        "entity:*": "default",
    })

    monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", registry)
    monkeypatch.setenv("AKC_SERVICE_ENTITY_KB_MAPPING", mapping)

    importlib.reload(_cfg)
    assert len(_cfg.KB_REGISTRY) == 2, (
        f"Fixture setup failed: KB_REGISTRY has {len(_cfg.KB_REGISTRY)} entries "
        f"after env var change — expected 2."
    )

    import akc_service.api.routes as _routes
    importlib.reload(_routes)

    yield TestClient(app), {"default": kb_default, "physics": kb_physics}

    monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
    monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
    importlib.reload(_cfg)
    importlib.reload(_routes)


@pytest.fixture
def no_mapping_client(tmp_path, monkeypatch):
    """TestClient with only the default KB and no ENTITY_KB_MAPPING configured.

    Sets:
      AKC_SERVICE_KB_REGISTRY = {"default": <tmp>}
      AKC_SERVICE_ENTITY_KB_MAPPING is unset

    Yields TestClient(app).
    """
    kb_default = tmp_path / "kb" / "default"
    kb_default.mkdir(parents=True)

    registry = json.dumps({"default": str(kb_default)})
    monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", registry)
    monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)

    importlib.reload(_cfg)

    import akc_service.api.routes as _routes
    importlib.reload(_routes)

    yield TestClient(app)

    monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
    importlib.reload(_cfg)
    importlib.reload(_routes)


# ─── INF-08: TestEntityMappingTier ───────────────────────────────────────────


class TestEntityMappingTier:
    """INF-08: Exact entity key match in ENTITY_KB_MAPPING → routing_tier='entity_mapping'."""

    @patch("akc_service.api.routes.get_active_patterns")
    def test_query_entity_physics_routes_to_physics_kb(
        self, mock_patterns, entity_kb_client
    ):
        """POST /query with entity='physics' → routing_tier='entity_mapping', kb_used='physics'."""
        mock_patterns.return_value = []
        client, _ = entity_kb_client
        response = client.post("/akc/v1/query", json={
            "task_id": "inf08-q1",
            "entity": "physics",
            "component": "CollisionBody",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "entity_mapping", (
            f"Expected entity_mapping, got: {data['routing_tier']}"
        )
        assert data["kb_used"] == "physics", (
            f"Expected physics KB, got: {data['kb_used']}"
        )

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_entity_in_akc_context_routes_to_physics_kb(
        self, mock_delta, entity_kb_client
    ):
        """POST /record with akc_context.entity='physics' → routing_tier='entity_mapping', kb_used='physics'."""
        mock_delta.return_value = {"status": "ok", "patterns_updated": 0}
        client, _ = entity_kb_client
        response = client.post("/akc/v1/record", json={
            "schema_version": "1.0",
            "task_id": "inf08-r1",
            "status": "success",
            "timestamp": "2026-05-06T00:00:00Z",
            "akc_context": {
                "entity": "physics",
                "knowledge_patterns_active": [],
            },
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "entity_mapping", (
            f"Expected entity_mapping, got: {data['routing_tier']}"
        )
        assert data["kb_used"] == "physics", (
            f"Expected physics KB, got: {data['kb_used']}"
        )

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_entity_from_pattern_routes_to_physics_kb(
        self, mock_delta, entity_kb_client
    ):
        """POST /record with entity extracted from first pattern → routing_tier='entity_mapping', kb_used='physics'."""
        mock_delta.return_value = {"status": "ok", "patterns_updated": 1}
        client, _ = entity_kb_client
        response = client.post("/akc/v1/record", json={
            "schema_version": "1.0",
            "task_id": "inf08-r2",
            "status": "success",
            "timestamp": "2026-05-06T00:00:00Z",
            "akc_context": {
                "knowledge_patterns_active": [
                    {"entity": "physics", "component": "collision", "id": "p1"},
                ],
            },
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "entity_mapping", (
            f"Expected entity_mapping, got: {data['routing_tier']}"
        )
        assert data["kb_used"] == "physics", (
            f"Expected physics KB, got: {data['kb_used']}"
        )

    @patch("akc_service.api.routes.load_all_patterns")
    def test_fix_entity_field_routes_to_physics_kb(
        self, mock_patterns, entity_kb_client
    ):
        """POST /fix with entity='physics' → routing_tier='entity_mapping', kb_used='physics'."""
        mock_patterns.return_value = []
        client, _ = entity_kb_client
        response = client.post("/akc/v1/fix", json={
            "category": "detection",
            "entity": "physics",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "entity_mapping", (
            f"Expected entity_mapping, got: {data['routing_tier']}"
        )
        assert data["kb_used"] == "physics", (
            f"Expected physics KB, got: {data['kb_used']}"
        )


# ─── INF-09: TestEntityWildcardTier ──────────────────────────────────────────


class TestEntityWildcardTier:
    """INF-09: No exact entity match, wildcard entity:* exists → routing_tier='entity_wildcard'."""

    @patch("akc_service.api.routes.get_active_patterns")
    def test_query_unknown_entity_uses_wildcard(
        self, mock_patterns, entity_kb_client
    ):
        """POST /query with entity='animation' (no exact mapping) → routing_tier='entity_wildcard', kb_used='default'."""
        mock_patterns.return_value = []
        client, _ = entity_kb_client
        response = client.post("/akc/v1/query", json={
            "task_id": "inf09-q1",
            "entity": "animation",
            "component": "AnimationPlayer",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "entity_wildcard", (
            f"Expected entity_wildcard for unknown entity, got: {data['routing_tier']}"
        )
        assert data["kb_used"] == "default", (
            f"Expected default KB via wildcard, got: {data['kb_used']}"
        )


# ─── INF-10: TestFallbackTierNoMapping ───────────────────────────────────────


class TestFallbackTierNoMapping:
    """INF-10: No ENTITY_KB_MAPPING configured → routing_tier='fallback' when no entity resolves."""

    @patch("akc_service.api.routes.get_active_patterns")
    def test_query_with_entity_no_mapping_uses_fallback(
        self, mock_patterns, no_mapping_client
    ):
        """POST /query with entity='physics' but no explicit ENTITY_KB_MAPPING → routing_tier differs.

        When AKC_SERVICE_ENTITY_KB_MAPPING is unset, config returns the default
        {"entity:*": "default"} wildcard, so entity='physics' hits entity_wildcard
        (not fallback). This test verifies we still reach the default KB.

        Note: True 'fallback' tier only triggers when entity is absent entirely.
        """
        mock_patterns.return_value = []
        client = no_mapping_client
        response = client.post("/akc/v1/query", json={
            "task_id": "inf10-q1",
            "entity": "physics",
            "component": "CollisionBody",
        })
        assert response.status_code == 200
        data = response.json()
        # entity='physics' has no exact match → falls to entity:* wildcard → entity_wildcard
        assert data["routing_tier"] == "entity_wildcard"
        assert data["kb_used"] == "default"

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_no_entity_no_mapping_uses_fallback(
        self, mock_delta, no_mapping_client
    ):
        """POST /record with empty akc_context={} → routing_tier='fallback' (no entity at all)."""
        mock_delta.return_value = {"status": "ok", "patterns_updated": 0}
        client = no_mapping_client
        response = client.post("/akc/v1/record", json={
            "schema_version": "1.0",
            "task_id": "inf10-r1",
            "status": "success",
            "timestamp": "2026-05-06T00:00:00Z",
            "akc_context": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["routing_tier"] == "fallback", (
            f"Expected fallback when no entity provided, got: {data['routing_tier']}"
        )


# ─── Unit Tests: _extract_entity_from_context ────────────────────────────────


class TestExtractEntityFromContext:
    """Unit tests for the _extract_entity_from_context helper."""

    def setup_method(self):
        from akc_service.api.routes import _extract_entity_from_context
        self._fn = _extract_entity_from_context

    def test_returns_entity_from_direct_key(self):
        """Returns 'physics' for context with direct entity key."""
        assert self._fn({"entity": "physics"}) == "physics"

    def test_returns_entity_from_first_pattern(self):
        """Returns entity from first item in knowledge_patterns_active."""
        ctx = {"knowledge_patterns_active": [{"entity": "animation", "id": "p1"}]}
        assert self._fn(ctx) == "animation"

    def test_direct_entity_wins_over_pattern(self):
        """Direct 'entity' key takes priority over entity in patterns."""
        ctx = {
            "entity": "physics",
            "knowledge_patterns_active": [{"entity": "animation"}],
        }
        assert self._fn(ctx) == "physics"

    def test_returns_none_for_empty_dict(self):
        """Returns None for empty context dict."""
        assert self._fn({}) is None

    def test_returns_none_for_none_input(self):
        """Returns None when None is passed as context."""
        assert self._fn(None) is None

    def test_returns_none_when_patterns_have_no_entity(self):
        """Returns None when patterns exist but none have 'entity' field."""
        ctx = {"knowledge_patterns_active": [{"id": "p1", "component": "col"}]}
        assert self._fn(ctx) is None
