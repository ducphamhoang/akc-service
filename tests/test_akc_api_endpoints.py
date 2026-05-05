#!/usr/bin/env python3
"""
AKC Phase 1 Endpoint Integration Tests
Tests all 5 REST endpoints against FastAPI TestClient without starting uvicorn.

Covers:
- /akc/v1/health (health check)
- /akc/v1/query (pattern retrieval)
- /akc/v1/record (task outcome recording)
- /akc/v1/fix (pattern fix retrieval)
- /akc/v1/stats (knowledge base statistics)
- /akc/v1/update (confidence override)
"""

import pytest
from fastapi.testclient import TestClient
from akc_service.api.main import app
from unittest.mock import patch, MagicMock


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


# ─── Health Endpoint Tests ───────────────────────────────────────────────────

class TestHealthEndpoint:
    """Tests for /akc/v1/health endpoint."""

    def test_health_endpoint_returns_200(self, client):
        """Health check returns 200 with status field."""
        response = client.get("/akc/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_endpoint_includes_timestamp(self, client):
        """Health check includes ISO8601 timestamp."""
        response = client.get("/akc/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        # Verify ISO8601 format (contains 'T' and 'Z')
        assert "T" in data["timestamp"]
        assert "Z" in data["timestamp"]


# ─── Query Endpoint Tests ────────────────────────────────────────────────────

class TestQueryEndpoint:
    """Tests for /akc/v1/query endpoint."""

    @patch("akc_service.api.routes.get_active_patterns")
    def test_query_endpoint_success(self, mock_get_patterns, client):
        """Query endpoint returns 200 with patterns."""
        mock_get_patterns.return_value = [
            {"id": "p1", "confidence": 0.85, "tier": "gold"},
            {"id": "p2", "confidence": 0.72, "tier": "production"}
        ]

        payload = {
            "task_id": "t1",
            "entity": "player",
            "component": "HealthComponent"
        }
        response = client.post("/akc/v1/query", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "patterns" in data
        assert len(data["patterns"]) == 2
        assert data["patterns"][0]["id"] == "p1"
        assert "query_latency_ms" in data
        assert "source" in data

    @patch("akc_service.api.routes.get_active_patterns")
    def test_query_endpoint_empty_patterns(self, mock_get_patterns, client):
        """Query endpoint handles empty pattern list."""
        mock_get_patterns.return_value = []

        payload = {
            "task_id": "t1",
            "entity": "player",
            "component": "HealthComponent"
        }
        response = client.post("/akc/v1/query", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["patterns"] == []

    def test_query_endpoint_missing_entity(self, client):
        """Query endpoint returns 422 when entity is missing."""
        payload = {
            "task_id": "t1",
            "component": "HealthComponent"
        }
        response = client.post("/akc/v1/query", json=payload)
        assert response.status_code == 422  # Pydantic validation error

    def test_query_endpoint_missing_component(self, client):
        """Query endpoint returns 422 when component is missing."""
        payload = {
            "task_id": "t1",
            "entity": "player"
        }
        response = client.post("/akc/v1/query", json=payload)
        assert response.status_code == 422

    def test_query_endpoint_missing_task_id(self, client):
        """Query endpoint returns 422 when task_id is missing."""
        payload = {
            "entity": "player",
            "component": "HealthComponent"
        }
        response = client.post("/akc/v1/query", json=payload)
        assert response.status_code == 422


# ─── Record Endpoint Tests ───────────────────────────────────────────────────

class TestRecordEndpoint:
    """Tests for /akc/v1/record endpoint."""

    @patch("akc_service.api.routes.now_iso")
    def test_record_endpoint_returns_202(self, mock_now_iso, client):
        """Record endpoint returns 202 Accepted."""
        mock_now_iso.return_value = "2026-05-04T10:00:00Z"

        payload = {
            "schema_version": "1.0",
            "task_id": "t1",
            "status": "success",
            "timestamp": "2026-05-04T10:00:00Z",
            "akc_context": {
                "knowledge_patterns_active": [{"id": "p1", "confidence": 0.85}]
            }
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 202

        data = response.json()
        assert data["accepted"] is True
        assert data["task_id"] == "t1"
        assert "update_mode" in data
        assert "patterns_to_update" in data

    def test_record_endpoint_invalid_schema_version(self, client):
        """Record endpoint returns 400 for invalid schema_version."""
        payload = {
            "schema_version": "2.0",
            "task_id": "t1",
            "status": "success",
            "timestamp": "2026-05-04T10:00:00Z",
            "akc_context": {}
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 400
        assert "schema_version" in response.text.lower()

    def test_record_endpoint_invalid_status(self, client):
        """Record endpoint returns 400 for invalid status."""
        payload = {
            "schema_version": "1.0",
            "task_id": "t1",
            "status": "pending",
            "timestamp": "2026-05-04T10:00:00Z",
            "akc_context": {}
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 400

    def test_record_endpoint_missing_required_fields(self, client):
        """Record endpoint returns 422 when required fields missing."""
        payload = {
            "schema_version": "1.0",
            "task_id": "t1"
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 422


# ─── Fix Endpoint Tests ──────────────────────────────────────────────────────

class TestFixEndpoint:
    """Tests for /akc/v1/fix endpoint."""

    @patch("akc_service.api.routes.load_all_patterns")
    def test_fix_endpoint_success(self, mock_load_patterns, client):
        """Fix endpoint returns 200 with fixes."""
        mock_load_patterns.return_value = [
            {
                "id": "p1",
                "category": "detection",
                "fixes": [
                    {"fix_id": "f1", "description": "adjust signal handler"},
                    {"fix_id": "f2", "description": "validate args"}
                ]
            }
        ]

        payload = {
            "signature_hash": "hash123",
            "category": "detection"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "fixes" in data
        assert "category" in data
        assert "count" in data
        assert data["category"] == "detection"
        assert data["count"] == 2

    @patch("akc_service.api.routes.load_all_patterns")
    def test_fix_endpoint_no_patterns_found(self, mock_load_patterns, client):
        """Fix endpoint returns 404 when no patterns match."""
        mock_load_patterns.return_value = []

        payload = {
            "signature_hash": "hash123",
            "category": "detection"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 404

    def test_fix_endpoint_invalid_category(self, client):
        """Fix endpoint returns 400 for invalid category."""
        payload = {
            "signature_hash": "hash123",
            "category": "invalid_category"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 400
        assert "category" in response.text.lower()

    @patch("akc_service.api.routes.load_all_patterns")
    def test_fix_endpoint_no_fixes_in_category(self, mock_load_patterns, client):
        """Fix endpoint returns 404 when category exists but has no fixes."""
        mock_load_patterns.return_value = [
            {
                "id": "p1",
                "category": "detection",
                "fixes": []
            }
        ]

        payload = {
            "signature_hash": "hash123",
            "category": "detection"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 404

    def test_fix_endpoint_missing_fields(self, client):
        """Fix endpoint returns 422 when required fields missing."""
        payload = {
            "signature_hash": "hash123"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 422


# ─── Stats Endpoint Tests ────────────────────────────────────────────────────

class TestStatsEndpoint:
    """Tests for /akc/v1/stats endpoint."""

    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_success(self, mock_check_latency, mock_load_patterns, client):
        """Stats endpoint returns 200 with statistics."""
        mock_check_latency.return_value = {
            "sample_count": 100,
            "latency_stats": {
                "min": 5.0,
                "max": 50.0,
                "avg": 15.5,
                "p95": 40.0
            },
            "sla_status": "HEALTHY"
        }
        mock_load_patterns.return_value = [
            {"id": "p1", "confidence": 0.85, "confidence_tier": "gold"},
            {"id": "p2", "confidence": 0.72, "confidence_tier": "production"}
        ]

        response = client.get("/akc/v1/stats")
        assert response.status_code == 200

        data = response.json()
        assert "sample_count" in data
        assert "latency_stats" in data
        assert "sla_status" in data
        assert data["sla_status"] == "HEALTHY"
        assert "gold_tier_count" in data
        assert data["gold_tier_count"] == 1
        assert "avg_confidence" in data
        assert data["avg_confidence"] == 0.785

    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_with_time_window(self, mock_check_latency, mock_load_patterns, client):
        """Stats endpoint accepts time_window query parameter."""
        mock_check_latency.return_value = {
            "sample_count": 50,
            "latency_stats": {"min": 5.0, "max": 30.0, "avg": 12.0, "p95": 25.0},
            "sla_status": "HEALTHY"
        }
        mock_load_patterns.return_value = []

        response = client.get("/akc/v1/stats?time_window=24h")
        assert response.status_code == 200

        data = response.json()
        assert data["sample_count"] == 50

    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_empty_kb(self, mock_check_latency, mock_load_patterns, client):
        """Stats endpoint handles empty KB gracefully."""
        mock_check_latency.return_value = {
            "sample_count": 0,
            "latency_stats": {},
            "sla_status": "UNKNOWN"
        }
        mock_load_patterns.return_value = []

        response = client.get("/akc/v1/stats")
        assert response.status_code == 200

        data = response.json()
        assert data["sample_count"] == 0
        assert data["avg_confidence"] == 0.0


# ─── Update Endpoint Tests ───────────────────────────────────────────────────

class TestUpdateEndpoint:
    """Tests for /akc/v1/update endpoint."""

    @patch("akc_service.api.routes.log_confidence_update")
    @patch("akc_service.api.routes.append_pattern_version")
    @patch("akc_service.api.routes.now_iso")
    @patch("akc_service.api.routes.find_pattern_by_id")
    @patch("akc_service.api.routes.load_all_patterns")
    def test_update_endpoint_success(
        self,
        mock_load_all,
        mock_find_pattern,
        mock_now_iso,
        mock_append,
        mock_log,
        client
    ):
        """Update endpoint returns 200 with old/new scores."""
        mock_load_all.return_value = [{"id": "p1", "confidence": 0.75}]
        mock_find_pattern.return_value = {
            "id": "p1",
            "confidence": 0.75,
            "version": {"current": "v1", "history": []}
        }
        mock_now_iso.return_value = "2026-05-04T10:00:00Z"

        payload = {
            "pattern_id": "p1",
            "new_score": 0.85,
            "reason": "improved after testing"
        }
        response = client.post("/akc/v1/update", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["pattern_id"] == "p1"
        assert data["old_score"] == 0.75
        assert data["new_score"] == 0.85
        assert "updated_at" in data

    def test_update_endpoint_score_out_of_range_high(self, client):
        """Update endpoint returns 422 for score > 0.95 (Pydantic validation)."""
        payload = {
            "pattern_id": "p1",
            "new_score": 1.5,
            "reason": "test"
        }
        response = client.post("/akc/v1/update", json=payload)
        assert response.status_code == 422  # Pydantic validation error

    def test_update_endpoint_score_out_of_range_negative(self, client):
        """Update endpoint returns 422 for negative score (Pydantic validation)."""
        payload = {
            "pattern_id": "p1",
            "new_score": -0.1,
            "reason": "test"
        }
        response = client.post("/akc/v1/update", json=payload)
        assert response.status_code == 422  # Pydantic validation error

    @patch("akc_service.api.routes.find_pattern_by_id")
    @patch("akc_service.api.routes.load_all_patterns")
    def test_update_endpoint_pattern_not_found(self, mock_load_all, mock_find, client):
        """Update endpoint returns 404 when pattern not found."""
        mock_load_all.return_value = []
        mock_find.return_value = None

        payload = {
            "pattern_id": "nonexistent",
            "new_score": 0.80,
            "reason": "test"
        }
        response = client.post("/akc/v1/update", json=payload)
        assert response.status_code == 404

    def test_update_endpoint_missing_fields(self, client):
        """Update endpoint returns 422 when required fields missing."""
        payload = {
            "pattern_id": "p1"
        }
        response = client.post("/akc/v1/update", json=payload)
        assert response.status_code == 422


# ─── Integration Tests ───────────────────────────────────────────────────────

class TestEndpointIntegration:
    """Integration tests across multiple endpoints."""

    def test_health_check_is_always_available(self, client):
        """Health check works regardless of KB state."""
        # Health check should not depend on any KB data
        for _ in range(3):
            response = client.get("/akc/v1/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    @patch("akc_service.api.routes.get_active_patterns")
    @patch("akc_service.api.routes.now_iso")
    def test_query_then_record_flow(self, mock_now_iso, mock_get_patterns, client):
        """Typical flow: query for patterns, then record outcome."""
        mock_get_patterns.return_value = [
            {"id": "p1", "confidence": 0.85, "tier": "gold"}
        ]
        mock_now_iso.return_value = "2026-05-04T10:00:00Z"

        # Step 1: Query for patterns
        query_payload = {
            "task_id": "t1",
            "entity": "player",
            "component": "HealthComponent"
        }
        query_response = client.post("/akc/v1/query", json=query_payload)
        assert query_response.status_code == 200

        patterns = query_response.json()["patterns"]
        assert len(patterns) == 1

        # Step 2: Record outcome using pattern info
        record_payload = {
            "schema_version": "1.0",
            "task_id": "t1",
            "status": "success",
            "timestamp": "2026-05-04T10:00:00Z",
            "akc_context": {
                "knowledge_patterns_active": patterns
            }
        }
        record_response = client.post("/akc/v1/record", json=record_payload)
        assert record_response.status_code == 202
        assert record_response.json()["patterns_to_update"] == 1


class TestSLAThreshold:
    """Tests for SLA threshold correctness (50ms, not 5 minutes)."""

    def test_sla_warning_when_latency_exceeds_50ms(self, tmp_path, monkeypatch):
        """SLA status should be WARNING when latency exceeds 50ms."""
        from akc_service import learning_integration as li
        import json
        from pathlib import Path

        # Create confidence history file
        latency_file = tmp_path / "confidence_history.jsonl"
        latency_file.write_text(
            json.dumps({"latency_ms": 30}) + "\n" +
            json.dumps({"latency_ms": 80}) + "\n"
        )

        # Patch the path constant
        monkeypatch.setattr(li, "CONFIDENCE_HISTORY_PATH", latency_file)

        result = li.check_latency()
        assert result["sla_status"] == "WARNING"

    def test_sla_healthy_when_all_latency_under_50ms(self, tmp_path, monkeypatch):
        """SLA status should be HEALTHY when all latency under 50ms."""
        from akc_service import learning_integration as li
        import json
        from pathlib import Path

        # Create confidence history file
        latency_file = tmp_path / "confidence_history.jsonl"
        latency_file.write_text(
            json.dumps({"latency_ms": 10}) + "\n" +
            json.dumps({"latency_ms": 40}) + "\n"
        )

        # Patch the path constant
        monkeypatch.setattr(li, "CONFIDENCE_HISTORY_PATH", latency_file)

        result = li.check_latency()
        assert result["sla_status"] == "HEALTHY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
