#!/usr/bin/env python3
"""
Phase 3 API KB Routing Integration Tests
Covers all 10 Phase 3 success criteria.
Uses FastAPI TestClient with AKC_SERVICE_KB_REGISTRY env var for multi-KB scenarios.
"""

import importlib
import json
import logging
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import akc_service.config as _cfg
from akc_service.api.main import app


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """Single-KB TestClient (default registry only)."""
    return TestClient(app)


@pytest.fixture
def multi_kb_client(tmp_path, monkeypatch):
    """TestClient with two KBs registered: 'default' and 'physics'.

    Reloads akc_service.config and akc_service.api.routes after setting
    AKC_SERVICE_KB_REGISTRY so that KB_REGISTRY is parsed with the new value.

    KB_REGISTRY capture at module level:
    - routes.py does `from akc_service.config import KB_REGISTRY`, so reloading
      _routes re-executes that import and picks up the reloaded _cfg value.
    - _cfg reload re-parses the env var so KB_REGISTRY on the _cfg module is updated.
    Both modules are reloaded for correctness.

    The fixture assertion `assert len(_cfg.KB_REGISTRY) == 2` verifies that
    propagation occurred correctly before any test body runs.
    """
    kb_default = tmp_path / "kb" / "default"
    kb_physics = tmp_path / "kb" / "physics"
    kb_default.mkdir(parents=True)
    kb_physics.mkdir(parents=True)
    registry = json.dumps({
        "default": str(kb_default),
        "physics": str(kb_physics),
    })
    monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", registry)
    importlib.reload(_cfg)
    assert len(_cfg.KB_REGISTRY) == 2, (
        f"Fixture setup failed: KB_REGISTRY has {len(_cfg.KB_REGISTRY)} entries "
        f"after env var change — expected 2. "
        f"Check that config.py reads AKC_SERVICE_KB_REGISTRY at module load time."
    )
    import akc_service.api.routes as _routes
    importlib.reload(_routes)
    yield TestClient(app), {"default": kb_default, "physics": kb_physics}
    monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
    importlib.reload(_cfg)
    importlib.reload(_routes)


# ─── SC-1, SC-2, SC-8, SC-9, SC-10: Query KB Routing ─────────────────────────

class TestQueryKBRouting:
    """SC-1, SC-2, SC-8, SC-9, SC-10"""

    @patch("akc_service.api.routes.get_active_patterns")
    def test_sc1_explicit_kb_returns_kb_used_and_routing_tier(
        self, mock_patterns, multi_kb_client
    ):
        """SC-1: POST /query {"kb": "physics"} → kb_used="physics", routing_tier="explicit"."""
        mock_patterns.return_value = []
        client, _ = multi_kb_client
        response = client.post("/akc/v1/query", json={
            "task_id": "t1",
            "entity": "player",
            "component": "HealthComponent",
            "kb": "physics",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["kb_used"] == "physics"
        assert data["routing_tier"] == "explicit"

    @patch("akc_service.api.routes.get_active_patterns")
    def test_sc2_no_kb_field_returns_default_fallback(self, mock_patterns, client):
        """SC-2: POST /query without kb → kb_used="default".

        With Tier 2 entity routing enabled (04-01), entity="player" matches the
        default wildcard (entity:* → "default"), so routing_tier is "entity_wildcard".
        The KB destination is still "default" — same as before — just via a different tier.
        """
        mock_patterns.return_value = []
        response = client.post("/akc/v1/query", json={
            "task_id": "t1",
            "entity": "player",
            "component": "HealthComponent",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["kb_used"] == "default"
        # Tier 2 routing: entity="player" matches entity:* wildcard → entity_wildcard tier
        assert data["routing_tier"] == "entity_wildcard"

    @patch("akc_service.api.routes.get_active_patterns")
    def test_sc8_query_response_has_both_kb_fields(self, mock_patterns, client):
        """SC-8: All responses include both kb_used and routing_tier."""
        mock_patterns.return_value = []
        response = client.post("/akc/v1/query", json={
            "task_id": "t1",
            "entity": "e",
            "component": "c",
        })
        assert response.status_code == 200
        data = response.json()
        assert "kb_used" in data
        assert "routing_tier" in data

    @patch("akc_service.api.routes.get_active_patterns")
    def test_sc9_query_log_contains_kb_tag(self, mock_patterns, client, caplog):
        """SC-9: Every request log line contains KB=<name> tag."""
        mock_patterns.return_value = []
        with caplog.at_level(logging.INFO, logger="akc_service.api.routes"):
            client.post("/akc/v1/query", json={
                "task_id": "t1",
                "entity": "e",
                "component": "c",
            })
        assert any("KB=" in record.message for record in caplog.records)

    @patch("akc_service.api.routes.get_active_patterns")
    def test_sc10_routing_decision_logged_at_info(self, mock_patterns, client, caplog):
        """SC-10: Routing decisions logged at INFO with kb_name and path."""
        mock_patterns.return_value = []
        with caplog.at_level(logging.INFO, logger="akc_service.config"):
            client.post("/akc/v1/query", json={
                "task_id": "t1",
                "entity": "e",
                "component": "c",
            })
        routing_logs = [r for r in caplog.records if "KB routing:" in r.message]
        assert len(routing_logs) >= 1
        assert "kb_name=" in routing_logs[0].message


# ─── SC-3, SC-8: Record KB Routing ───────────────────────────────────────────

class TestRecordKBRouting:
    """SC-3, SC-8"""

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_sc3_explicit_kb_writes_to_correct_directory(
        self, mock_delta, multi_kb_client
    ):
        """SC-3: POST /record {"kb": "physics"} → apply_confidence_delta called with kb_dir pointing to physics."""
        mock_delta.return_value = {"status": "ok", "patterns_updated": 1}
        client, paths = multi_kb_client
        response = client.post("/akc/v1/record", json={
            "schema_version": "1.0",
            "task_id": "t2",
            "status": "success",
            "timestamp": "2026-05-06T10:00:00Z",
            "akc_context": {},
            "kb": "physics",
        })
        assert response.status_code == 200
        # Verify delta called with physics kb_dir
        call_kwargs = mock_delta.call_args
        passed_kb_dir = call_kwargs[1].get("kb_dir") or (
            call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        )
        assert passed_kb_dir == paths["physics"]

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_sc8_record_response_has_both_kb_fields(self, mock_delta, client):
        """SC-8: RecordResponse has kb_used and routing_tier."""
        mock_delta.return_value = {"status": "ok", "patterns_updated": 0}
        response = client.post("/akc/v1/record", json={
            "schema_version": "1.0",
            "task_id": "t2",
            "status": "success",
            "timestamp": "2026-05-06T10:00:00Z",
            "akc_context": {},
        })
        assert response.status_code == 200
        data = response.json()
        assert "kb_used" in data
        assert "routing_tier" in data


# ─── SC-4, SC-8: Fix KB Routing ───────────────────────────────────────────────

class TestFixKBRouting:
    """SC-4, SC-8"""

    @patch("akc_service.api.routes.load_all_patterns")
    def test_sc4_explicit_kb_returns_kb_used(self, mock_patterns, multi_kb_client):
        """SC-4: POST /fix {"kb": "physics"} → response has kb_used="physics"."""
        mock_patterns.return_value = []
        client, _ = multi_kb_client
        response = client.post("/akc/v1/fix", json={
            "category": "detection",
            "kb": "physics",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["kb_used"] == "physics"

    @patch("akc_service.api.routes.load_all_patterns")
    def test_sc8_fix_response_has_both_kb_fields(self, mock_patterns, client):
        """SC-8: FixResponse has kb_used and routing_tier."""
        mock_patterns.return_value = []
        response = client.post("/akc/v1/fix", json={"category": "detection"})
        assert response.status_code == 200
        data = response.json()
        assert "kb_used" in data
        assert "routing_tier" in data


# ─── SC-5, SC-6, SC-7, SC-8: Stats KB Routing ────────────────────────────────

class TestStatsKBRouting:
    """SC-5, SC-6, SC-7, SC-8"""

    @patch("akc_service.api.routes.check_latency")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.count_history_patterns_in_window")
    def test_sc5_stats_with_kb_param_returns_named_kb(
        self, mock_count, mock_patterns, mock_latency, multi_kb_client
    ):
        """SC-5: GET /stats?kb=physics → stats for physics KB only."""
        mock_latency.return_value = {"latency_stats": {}, "sla_status": "HEALTHY", "sample_count": 0}
        mock_patterns.return_value = []
        mock_count.return_value = {"patterns_updated": 0}
        client, _ = multi_kb_client
        response = client.get("/akc/v1/stats?kb=physics")
        assert response.status_code == 200
        data = response.json()
        assert data["kb_used"] == "physics"
        assert data["routing_tier"] == "explicit"

    def test_sc6_stats_no_kb_with_multi_kb_returns_400(self, multi_kb_client):
        """SC-6: GET /stats (no ?kb=) with 2 registered KBs → HTTP 400."""
        client, _ = multi_kb_client
        response = client.get("/akc/v1/stats")
        assert response.status_code == 400
        body = response.json()
        # Error message may be under "detail" (FastAPI default) or "error" (custom handler)
        error_msg = body.get("detail") or body.get("error") or ""
        assert "?kb=" in error_msg

    @patch("akc_service.api.routes.check_latency")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.count_history_patterns_in_window")
    def test_sc7_stats_no_kb_single_kb_returns_default(
        self, mock_count, mock_patterns, mock_latency, client
    ):
        """SC-7: GET /stats (no ?kb=) with 1 registered KB → default KB stats, 200."""
        mock_latency.return_value = {"latency_stats": {}, "sla_status": "HEALTHY", "sample_count": 0}
        mock_patterns.return_value = []
        mock_count.return_value = {"patterns_updated": 0}
        response = client.get("/akc/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["kb_used"] == "default"

    @patch("akc_service.api.routes.check_latency")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.count_history_patterns_in_window")
    def test_sc8_stats_response_has_both_kb_fields(
        self, mock_count, mock_patterns, mock_latency, client
    ):
        """SC-8: StatsResponse has kb_used and routing_tier."""
        mock_latency.return_value = {"latency_stats": {}, "sla_status": "HEALTHY", "sample_count": 0}
        mock_patterns.return_value = []
        mock_count.return_value = {"patterns_updated": 0}
        response = client.get("/akc/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert "kb_used" in data
        assert "routing_tier" in data
