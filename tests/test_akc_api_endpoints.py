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
    def test_record_endpoint_returns_200(self, mock_now_iso, client):
        """Record endpoint returns 200 OK (synchronous, durable write)."""
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
        assert response.status_code == 200

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
        """Fix endpoint returns 200 with empty list when no patterns found."""
        mock_load_patterns.return_value = []

        payload = {
            "signature_hash": "hash123",
            "category": "detection"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["fixes"] == []
        assert data["count"] == 0

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
        """Fix endpoint returns 200 with empty list when category has no fixes."""
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
        assert response.status_code == 200
        data = response.json()
        assert data["fixes"] == []
        assert data["count"] == 0

    def test_fix_endpoint_missing_fields(self, client):
        """Fix endpoint returns 422 when required fields missing."""
        payload = {
            "signature_hash": "hash123"
        }
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 422


class TestFixEndpointEmptyKB:
    """Tests for /akc/v1/fix endpoint when KB is empty or has no matching category."""

    @patch("akc_service.api.routes.load_all_patterns", return_value=[])
    def test_fix_returns_empty_list_when_kb_empty(self, mock_load, client):
        """Fix endpoint returns 200 with empty list when KB is empty."""
        payload = {"signature_hash": "abc123", "category": "implementation"}
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["fixes"] == []
        assert data["count"] == 0
        assert data["category"] == "implementation"

    @patch("akc_service.api.routes.load_all_patterns", return_value=[
        {"id": "p1", "category": "testing", "confidence": 0.8, "fixes": [{"fix_id": "f1"}]}
    ])
    def test_fix_returns_empty_list_when_no_category_match(self, mock_load, client):
        """Fix endpoint returns 200 with empty list when no patterns match category."""
        payload = {"signature_hash": "abc123", "category": "implementation"}
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["fixes"] == []
        assert data["count"] == 0
        assert data["category"] == "implementation"


# ─── Stats Endpoint Tests ────────────────────────────────────────────────────

class TestStatsEndpoint:
    """Tests for /akc/v1/stats endpoint."""

    @patch("akc_service.api.routes.count_history_patterns_in_window")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_success(self, mock_check_latency, mock_load_patterns,
                                    mock_count_window, client):
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
        mock_count_window.return_value = {"patterns_updated": 2, "total_updates": 10}

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
        # New fields
        assert "patterns_updated" in data
        assert "time_window" in data
        assert data["time_window"] == "all"
        assert data["patterns_updated"] == 2

    @patch("akc_service.api.routes.count_history_patterns_in_window")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_with_time_window(self, mock_check_latency, mock_load_patterns,
                                             mock_count_window, client):
        """Stats endpoint filters latency and update counts by time_window."""
        mock_check_latency.return_value = {
            "sample_count": 50,
            "latency_stats": {"min": 5.0, "max": 30.0, "avg": 12.0, "p95": 25.0},
            "sla_status": "HEALTHY"
        }
        mock_load_patterns.return_value = []
        mock_count_window.return_value = {"patterns_updated": 3, "total_updates": 7}

        response = client.get("/akc/v1/stats?time_window=24h")
        assert response.status_code == 200

        data = response.json()
        assert data["sample_count"] == 50
        assert data["time_window"] == "24h"
        assert data["patterns_updated"] == 3

        # Verify check_latency and count_history_patterns_in_window were called
        # with a non-None cutoff_time (i.e. actually filtered)
        call_kwargs_latency = mock_check_latency.call_args
        assert call_kwargs_latency is not None
        cutoff_arg = call_kwargs_latency.kwargs.get("cutoff_time")
        assert cutoff_arg is not None, "check_latency should receive a cutoff_time for 24h window"

        call_kwargs_count = mock_count_window.call_args
        assert call_kwargs_count is not None
        cutoff_arg2 = call_kwargs_count.kwargs.get("cutoff_time")
        assert cutoff_arg2 is not None, "count_history_patterns_in_window should receive a cutoff_time for 24h window"

    @patch("akc_service.api.routes.count_history_patterns_in_window")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_all_window_passes_none_cutoff(self, mock_check_latency,
                                                          mock_load_patterns,
                                                          mock_count_window, client):
        """With time_window=all, check_latency receives cutoff_time=None (no filter)."""
        mock_check_latency.return_value = {
            "sample_count": 200,
            "latency_stats": {},
            "sla_status": "HEALTHY"
        }
        mock_load_patterns.return_value = []
        mock_count_window.return_value = {"patterns_updated": 0, "total_updates": 0}

        response = client.get("/akc/v1/stats?time_window=all")
        assert response.status_code == 200

        call_kwargs = mock_check_latency.call_args
        cutoff_arg = call_kwargs.kwargs.get("cutoff_time")
        assert cutoff_arg is None, "check_latency should receive cutoff_time=None for 'all' window"

    def test_stats_endpoint_invalid_time_window_returns_400(self, client):
        """Stats endpoint returns 400 for unrecognised time_window values."""
        response = client.get("/akc/v1/stats?time_window=bogus")
        assert response.status_code == 400
        data = response.json()
        # The global exception handler returns {"error": ...}
        assert "error" in data
        assert "bogus" in data["error"]

    def test_stats_endpoint_accepts_1h_window(self, client):
        """Stats endpoint accepts the '1h' time_window value."""
        with patch("akc_service.api.routes.check_latency") as mock_lat, \
             patch("akc_service.api.routes.load_all_patterns") as mock_pat, \
             patch("akc_service.api.routes.count_history_patterns_in_window") as mock_cnt:
            mock_lat.return_value = {"sample_count": 5, "latency_stats": {}, "sla_status": "HEALTHY"}
            mock_pat.return_value = []
            mock_cnt.return_value = {"patterns_updated": 1, "total_updates": 2}

            response = client.get("/akc/v1/stats?time_window=1h")
            assert response.status_code == 200
            assert response.json()["time_window"] == "1h"

    @patch("akc_service.api.routes.count_history_patterns_in_window")
    @patch("akc_service.api.routes.load_all_patterns")
    @patch("akc_service.api.routes.check_latency")
    def test_stats_endpoint_empty_kb(self, mock_check_latency, mock_load_patterns,
                                     mock_count_window, client):
        """Stats endpoint handles empty KB gracefully."""
        mock_check_latency.return_value = {
            "sample_count": 0,
            "latency_stats": {},
            "sla_status": "UNKNOWN"
        }
        mock_load_patterns.return_value = []
        mock_count_window.return_value = {"patterns_updated": 0, "total_updates": 0}

        response = client.get("/akc/v1/stats")
        assert response.status_code == 200

        data = response.json()
        assert data["sample_count"] == 0
        assert data["avg_confidence"] == 0.0
        assert data["patterns_updated"] == 0


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

    @patch("akc_service.api.routes.apply_confidence_delta")
    @patch("akc_service.api.routes.get_active_patterns")
    @patch("akc_service.api.routes.now_iso")
    def test_query_then_record_flow(self, mock_now_iso, mock_get_patterns, mock_delta, client):
        """Typical flow: query for patterns, then record outcome."""
        mock_get_patterns.return_value = [
            {"id": "p1", "confidence": 0.85, "tier": "gold"}
        ]
        mock_now_iso.return_value = "2026-05-04T10:00:00Z"
        mock_delta.return_value = {
            "status": "success",
            "patterns_updated": 1,
            "latency_ms": 5
        }

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
                "akc_enabled": True,
                "knowledge_patterns_active": patterns
            }
        }
        record_response = client.post("/akc/v1/record", json=record_payload)
        assert record_response.status_code == 200
        assert record_response.json()["patterns_to_update"] == 1


class TestSLAThreshold:
    """Tests for SLA threshold correctness (50ms, not 5 minutes)."""

    def test_sla_warning_when_latency_exceeds_50ms(self, tmp_path, monkeypatch):
        """SLA status should be WARNING when latency exceeds 50ms."""
        from akc_service import learning_integration as li
        import json

        # Create confidence history file
        latency_file = tmp_path / "confidence_history.jsonl"
        latency_file.write_text(
            json.dumps({"latency_ms": 30}) + "\n" +
            json.dumps({"latency_ms": 80}) + "\n"
        )

        # Patch KB_DIR so check_latency resolves the correct history path
        monkeypatch.setattr(li, "KB_DIR", tmp_path)

        result = li.check_latency()
        assert result["sla_status"] == "WARNING"

    def test_sla_healthy_when_all_latency_under_50ms(self, tmp_path, monkeypatch):
        """SLA status should be HEALTHY when all latency under 50ms."""
        from akc_service import learning_integration as li
        import json

        # Create confidence history file
        latency_file = tmp_path / "confidence_history.jsonl"
        latency_file.write_text(
            json.dumps({"latency_ms": 10}) + "\n" +
            json.dumps({"latency_ms": 40}) + "\n"
        )

        # Patch KB_DIR so check_latency resolves the correct history path
        monkeypatch.setattr(li, "KB_DIR", tmp_path)

        result = li.check_latency()
        assert result["sla_status"] == "HEALTHY"


class TestRecordDispatchesLearning:
    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_dispatches_background_delta(self, mock_delta, client):
        mock_delta.return_value = {
            "status": "success",
            "patterns_updated": 2,
            "latency_ms": 10
        }
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-001",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["pattern_001", "pattern_002"]
            }
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 200
        mock_delta.assert_called_once()
        call_arg = mock_delta.call_args[0][0]
        assert call_arg["task_id"] == "t-learning-001"
        assert call_arg["status"] == "success"

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_failed_status_dispatches_delta(self, mock_delta, client):
        mock_delta.return_value = {
            "status": "success",
            "patterns_updated": 1,
            "latency_ms": 8
        }
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-002",
            "status": "failed",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {"akc_enabled": True, "knowledge_patterns_active": ["pattern_001"]}
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 200
        mock_delta.assert_called_once()

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_empty_patterns_still_returns_200(self, mock_delta, client):
        mock_delta.return_value = {
            "status": "success",
            "patterns_updated": 0,
            "latency_ms": 2
        }
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-003",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {"akc_enabled": True, "knowledge_patterns_active": []}
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 200
        mock_delta.assert_called_once()


class TestRecordDurability:
    """
    Verify that /record writes reach the KB before the 200 response is returned.

    The core guarantee: if the process were to restart immediately after receiving
    the 200, the confidence update would already be on disk.  We test this by
    calling apply_confidence_delta for real (no mock) on a tmp KB and asserting
    that patterns.jsonl contains the updated entry after the response.
    """

    def _seed_pattern(self, kb_dir, pid: str, confidence: float):
        import json
        pattern = {
            "id": pid, "entity": "e", "component": "c",
            "confidence": confidence, "confidence_tier": "production",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
        }
        (kb_dir / "patterns.jsonl").write_text(json.dumps(pattern) + "\n")

    def test_kb_written_before_200_returned(self, tmp_path, monkeypatch):
        """
        POST /record must write to KB before returning 200.

        Approach: patch PATTERNS_PATH, CONFIDENCE_HISTORY_PATH and KB_REGISTRY to
        tmp_path, seed one pattern, call the real endpoint, then read patterns.jsonl
        back and assert a second entry (the confidence update) exists.
        """
        import json as _json
        from akc_service import learning_integration as li
        import akc_service.config as cfg

        monkeypatch.setenv("AKC_SERVICE_KB_DIR", str(tmp_path))
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(li, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(li, "CONFIDENCE_HISTORY_PATH", tmp_path / "confidence_history.jsonl")
        # Patch KB_REGISTRY so resolve_kb_dir() returns tmp_path as the default KB
        monkeypatch.setattr(cfg, "KB_REGISTRY", {"default": str(tmp_path)})

        self._seed_pattern(tmp_path, "p-dur-1", 0.70)

        from fastapi.testclient import TestClient
        from akc_service.api.main import app
        client = TestClient(app)

        payload = {
            "schema_version": "1.0",
            "task_id": "t-durability-001",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["p-dur-1"],
            },
        }
        response = client.post("/akc/v1/record", json=payload)

        # 200 means write completed synchronously
        assert response.status_code == 200

        # Verify KB was actually updated on disk
        lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
        assert len(lines) >= 2, "Expected original + updated entry in patterns.jsonl"

        updated = _json.loads(lines[-1])
        assert updated["id"] == "p-dur-1"
        # success delta = +0.05 → 0.70 + 0.05 = 0.75
        assert abs(updated["confidence"] - 0.75) < 0.001, (
            f"Expected confidence 0.75, got {updated['confidence']}"
        )

    def test_kb_write_failure_returns_500_not_200(self, tmp_path, monkeypatch):
        """
        If the KB write fails, endpoint must return 500, not 200.

        A 200 with a failed write would be a silent data-loss bug.
        """
        from akc_service import learning_integration as li
        from unittest.mock import patch

        monkeypatch.setenv("AKC_SERVICE_KB_DIR", str(tmp_path))
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(li, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(li, "CONFIDENCE_HISTORY_PATH", tmp_path / "confidence_history.jsonl")

        from fastapi.testclient import TestClient
        from akc_service.api.main import app
        client = TestClient(app)

        payload = {
            "schema_version": "1.0",
            "task_id": "t-durability-002",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["p-dur-2"],
            },
        }

        # Force apply_confidence_delta to raise an IOError (simulates disk full / permissions)
        with patch("akc_service.api.routes.apply_confidence_delta", side_effect=IOError("disk full")):
            response = client.post("/akc/v1/record", json=payload)

        assert response.status_code == 500


class TestSafetyLevelDeltaCap:
    def _make_task_result(self, status: str, pattern_id: str) -> dict:
        return {
            "schema_version": "1.0",
            "task_id": "t-safety-001",
            "status": status,
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": [pattern_id],
            },
        }

    def _seed_pattern(self, kb_dir, pid: str, confidence: float):
        import json
        pattern = {
            "id": pid, "entity": "e", "component": "c",
            "confidence": confidence, "confidence_tier": "production",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
        }
        (kb_dir / "patterns.jsonl").write_text(json.dumps(pattern) + "\n")

    def test_level_1_success_delta_stays_within_cap(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path
        import akc_service.config as cfg
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(li, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(cfg, "SAFETY_LEVEL", 1)
        self._seed_pattern(tmp_path, "p-cap-1", 0.50)

        result = li.apply_confidence_delta(self._make_task_result("success", "p-cap-1"))
        assert result["status"] == "success"
        assert result["patterns_updated"] == 1

        lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
        updated = json.loads(lines[-1])
        # delta=+0.05, cap=0.15 → new confidence = 0.55
        assert abs(updated["confidence"] - 0.55) < 0.001

    def test_level_2_success_delta_stays_within_cap(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path
        import akc_service.config as cfg
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(li, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(cfg, "SAFETY_LEVEL", 2)
        self._seed_pattern(tmp_path, "p-cap-2", 0.50)

        result = li.apply_confidence_delta(self._make_task_result("success", "p-cap-2"))
        assert result["status"] == "success"

        lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
        updated = json.loads(lines[-1])
        # delta=+0.05, cap=0.10 → new confidence = 0.55
        assert abs(updated["confidence"] - 0.55) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
