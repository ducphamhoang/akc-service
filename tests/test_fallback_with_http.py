#!/usr/bin/env python3
"""
AKC Phase 1 Fallback Validation Tests
Tests network failure scenarios and fallback behavior using mocked requests.

Covers:
- Connection errors (server down)
- Timeout scenarios
- HTTP 5xx errors
- Full task flow without AKC
- Timeout compliance (< 150ms)
"""

import pytest
import time
from unittest.mock import patch, MagicMock
import requests

# akc_http_client is part of the agent-system package, not akc-service
try:
    from akc_http_client import AKCClient
    _AKC_HTTP_CLIENT_AVAILABLE = True
except ImportError:
    AKCClient = None
    _AKC_HTTP_CLIENT_AVAILABLE = False

http_client_required = pytest.mark.skipif(
    not _AKC_HTTP_CLIENT_AVAILABLE,
    reason="akc_http_client not available — install agent-system package"
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def akc_client():
    """AKC client with default timeout."""
    return AKCClient(timeout_sec=0.15)


@pytest.fixture
def akc_client_custom_timeout():
    """AKC client with custom timeout."""
    return AKCClient(timeout_sec=0.10)


# ─── Query Patterns Fallback Tests ──────────────────────────────────────────

@http_client_required
class TestQueryPatternsFallback:
    """Tests for query_patterns fallback behavior."""

    @patch("requests.Session.post")
    def test_query_patterns_server_down(self, mock_post, akc_client):
        """Query returns empty list when server is down."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        assert result == []
        assert isinstance(result, list)

    @patch("requests.Session.post")
    def test_query_patterns_timeout(self, mock_post, akc_client):
        """Query returns empty list on timeout."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timeout")

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        assert result == []

    @patch("requests.Session.post")
    def test_query_patterns_http_500(self, mock_post, akc_client):
        """Query returns empty list on HTTP 500."""
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_post.return_value = response

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        assert result == []

    @patch("requests.Session.post")
    def test_query_patterns_http_503(self, mock_post, akc_client):
        """Query returns empty list on HTTP 503 (service unavailable)."""
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("503 Service Unavailable")
        mock_post.return_value = response

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        assert result == []

    @patch("requests.Session.post")
    def test_query_patterns_invalid_json(self, mock_post, akc_client):
        """Query returns empty list on invalid JSON response."""
        response = MagicMock()
        response.json.side_effect = ValueError("Invalid JSON")
        mock_post.return_value = response

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        assert result == []


# ─── Is Available Fallback Tests ────────────────────────────────────────────

@http_client_required
class TestIsAvailableFallback:
    """Tests for is_available fallback behavior."""

    @patch("requests.Session.head")
    def test_is_available_returns_false_on_timeout(self, mock_head, akc_client):
        """is_available returns False when health check times out."""
        mock_head.side_effect = requests.exceptions.Timeout()

        available = akc_client.is_available()

        assert available is False

    @patch("requests.Session.head")
    def test_is_available_returns_false_on_connection_error(self, mock_head, akc_client):
        """is_available returns False on connection error."""
        mock_head.side_effect = requests.exceptions.ConnectionError()

        available = akc_client.is_available()

        assert available is False

    @patch("requests.Session.head")
    def test_is_available_returns_false_on_http_error(self, mock_head, akc_client):
        """is_available returns False on HTTP error."""
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_head.return_value = response

        available = akc_client.is_available()

        assert available is False

    @patch("requests.Session.head")
    def test_is_available_returns_true_on_success(self, mock_head, akc_client):
        """is_available returns True on successful health check."""
        response = MagicMock()
        response.status_code = 200
        mock_head.return_value = response

        available = akc_client.is_available()

        assert available is True


# ─── Record Outcome Fallback Tests ──────────────────────────────────────────

@http_client_required
class TestRecordOutcomeFallback:
    """Tests for record_outcome fallback behavior."""

    @patch("requests.Session.post")
    def test_record_outcome_server_down(self, mock_post, akc_client):
        """Record returns empty dict when server is down."""
        mock_post.side_effect = requests.exceptions.ConnectionError()

        result = akc_client.record_outcome({"task_id": "t1", "status": "success"})

        assert result == {}
        assert isinstance(result, dict)

    @patch("requests.Session.post")
    def test_record_outcome_timeout(self, mock_post, akc_client):
        """Record returns empty dict on timeout."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timeout")

        result = akc_client.record_outcome({"task_id": "t1", "status": "success"})

        assert result == {}

    @patch("requests.Session.post")
    def test_record_outcome_http_500(self, mock_post, akc_client):
        """Record returns empty dict on HTTP 500."""
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_post.return_value = response

        result = akc_client.record_outcome({"task_id": "t1", "status": "success"})

        assert result == {}

    @patch("requests.Session.post")
    def test_record_outcome_invalid_json(self, mock_post, akc_client):
        """Record returns empty dict on invalid JSON response."""
        response = MagicMock()
        response.json.side_effect = ValueError("Invalid JSON")
        mock_post.return_value = response

        result = akc_client.record_outcome({"task_id": "t1", "status": "success"})

        assert result == {}

    @patch("requests.Session.post")
    def test_record_outcome_non_dict_response(self, mock_post, akc_client):
        """Record returns empty dict when response is not a dict."""
        response = MagicMock()
        response.json.return_value = ["not", "a", "dict"]
        response.headers = {}
        mock_post.return_value = response

        result = akc_client.record_outcome({"task_id": "t1", "status": "success"})

        assert result == {}


# ─── Get Stats Fallback Tests ────────────────────────────────────────────────

@http_client_required
class TestGetStatsFallback:
    """Tests for get_stats fallback behavior."""

    @patch("requests.Session.get")
    def test_get_stats_server_down(self, mock_get, akc_client):
        """Get stats returns empty dict when server is down."""
        mock_get.side_effect = requests.exceptions.ConnectionError()

        result = akc_client.get_stats()

        assert result == {}
        assert isinstance(result, dict)

    @patch("requests.Session.get")
    def test_get_stats_timeout(self, mock_get, akc_client):
        """Get stats returns empty dict on timeout."""
        mock_get.side_effect = requests.exceptions.Timeout()

        result = akc_client.get_stats()

        assert result == {}

    @patch("requests.Session.get")
    def test_get_stats_http_500(self, mock_get, akc_client):
        """Get stats returns empty dict on HTTP 500."""
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_get.return_value = response

        result = akc_client.get_stats()

        assert result == {}

    @patch("requests.Session.get")
    def test_get_stats_invalid_json(self, mock_get, akc_client):
        """Get stats returns empty dict on invalid JSON."""
        response = MagicMock()
        response.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = response

        result = akc_client.get_stats()

        assert result == {}


# ─── Timeout Compliance Tests ──────────────────────────────────────────────

@http_client_required
class TestTimeoutCompliance:
    """Tests for timeout configuration and compliance."""

    @patch("requests.Session.post")
    def test_timeout_is_respected_in_query(self, mock_post, akc_client_custom_timeout):
        """Query uses configured timeout value."""
        response = MagicMock()
        response.json.return_value = {"patterns": []}
        response.headers = {}
        mock_post.return_value = response

        akc_client_custom_timeout.query_patterns("t1", "player", "HealthComponent")

        # Verify timeout was passed
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 0.10

    @patch("requests.Session.post")
    def test_timeout_is_respected_in_record(self, mock_post, akc_client_custom_timeout):
        """Record uses configured timeout value."""
        response = MagicMock()
        response.json.return_value = {}
        response.headers = {}
        mock_post.return_value = response

        akc_client_custom_timeout.record_outcome({"task_id": "t1"})

        # Verify timeout was passed
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 0.10

    @patch("requests.Session.get")
    def test_timeout_is_respected_in_stats(self, mock_get, akc_client_custom_timeout):
        """Stats uses configured timeout value."""
        response = MagicMock()
        response.json.return_value = {}
        response.headers = {}
        mock_get.return_value = response

        akc_client_custom_timeout.get_stats()

        # Verify timeout was passed
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["timeout"] == 0.10

    @patch("requests.Session.head")
    def test_health_check_uses_50ms_timeout(self, mock_head, akc_client):
        """Health check uses 50ms timeout (stricter than default)."""
        response = MagicMock()
        response.status_code = 200
        mock_head.return_value = response

        akc_client.is_available()

        # Verify 50ms timeout was used (0.05 seconds)
        call_kwargs = mock_head.call_args[1]
        assert call_kwargs["timeout"] == 0.05


# ─── Full Task Flow Without AKC Tests ────────────────────────────────────────

@http_client_required
class TestFullTaskFlowWithoutAKC:
    """Tests for completing tasks without AKC service."""

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_agent_proceeds_without_akc_patterns(self, mock_get, mock_post, akc_client):
        """Agent can proceed when query fails but record also fails."""
        # Query fails
        mock_post.side_effect = requests.exceptions.ConnectionError()
        # Stats also fails
        mock_get.side_effect = requests.exceptions.ConnectionError()

        # Step 1: Try to query patterns (fails gracefully)
        patterns = akc_client.query_patterns("t1", "player", "HealthComponent")
        assert patterns == []

        # Step 2: Try to record outcome (fails gracefully)
        record_result = akc_client.record_outcome({
            "task_id": "t1",
            "status": "success"
        })
        assert record_result == {}

        # Step 3: Try to get stats (fails gracefully)
        stats = akc_client.get_stats()
        assert stats == {}

        # Agent can still complete the task despite all AKC calls failing
        assert True

    @patch("requests.Session.post")
    def test_fallback_query_with_timeout(self, mock_post, akc_client):
        """Query timeout doesn't block task execution."""
        start = time.time()
        mock_post.side_effect = requests.exceptions.Timeout()

        result = akc_client.query_patterns("t1", "player", "HealthComponent")

        elapsed = time.time() - start
        assert result == []
        # Should complete quickly (timeout exception raised, then caught)
        assert elapsed < 1.0  # Generous bound; actual should be < 200ms


# ─── Multiple Failure Scenarios Tests ──────────────────────────────────────

@http_client_required
class TestMultipleFailureScenarios:
    """Tests for cascading failure scenarios."""

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    @patch("requests.Session.head")
    def test_all_endpoints_down(self, mock_head, mock_get, mock_post, akc_client):
        """All endpoints down simultaneously."""
        mock_head.side_effect = requests.exceptions.ConnectionError()
        mock_post.side_effect = requests.exceptions.ConnectionError()
        mock_get.side_effect = requests.exceptions.ConnectionError()

        # All calls should fail gracefully
        assert akc_client.is_available() is False
        assert akc_client.query_patterns("t1", "player", "Health") == []
        assert akc_client.record_outcome({"task_id": "t1"}) == {}
        assert akc_client.get_stats() == {}

    @patch("requests.Session.post")
    @patch("requests.Session.head")
    def test_health_check_down_but_query_works(self, mock_head, mock_post, akc_client):
        """Health check fails but query succeeds (server partially available)."""
        mock_head.side_effect = requests.exceptions.Timeout()

        response = MagicMock()
        response.json.return_value = {"patterns": [{"id": "p1"}]}
        response.headers = {}
        mock_post.return_value = response

        # Health check fails
        assert akc_client.is_available() is False

        # But query works anyway
        patterns = akc_client.query_patterns("t1", "player", "Health")
        assert len(patterns) == 1

    @patch("requests.Session.post")
    def test_transient_timeout_then_recovery(self, mock_post, akc_client):
        """Simulates transient timeout (first call fails, second succeeds)."""
        # First call fails
        mock_post.side_effect = [
            requests.exceptions.Timeout(),
            MagicMock(json=lambda: {"patterns": [{"id": "p1"}]}, headers={})
        ]

        # First call fails gracefully
        result1 = akc_client.query_patterns("t1", "player", "Health")
        assert result1 == []

        # Reset mock to succeed on next call
        response = MagicMock()
        response.json.return_value = {"patterns": [{"id": "p1"}]}
        response.headers = {}
        mock_post.side_effect = None
        mock_post.return_value = response

        # Second call succeeds
        result2 = akc_client.query_patterns("t2", "enemy", "Health")
        assert len(result2) == 1


# ─── Edge Case Tests ────────────────────────────────────────────────────────

@http_client_required
class TestEdgeCases:
    """Tests for edge cases and unusual scenarios."""

    @patch("requests.Session.post")
    def test_query_with_empty_task_id(self, mock_post, akc_client):
        """Query handles empty task_id."""
        response = MagicMock()
        response.json.return_value = {"patterns": []}
        response.headers = {}
        mock_post.return_value = response

        result = akc_client.query_patterns("", "player", "Health")

        assert result == []

    @patch("requests.Session.post")
    def test_record_with_empty_outcome(self, mock_post, akc_client):
        """Record handles empty outcome dictionary."""
        mock_post.side_effect = requests.exceptions.ConnectionError()

        result = akc_client.record_outcome({})

        assert result == {}

    @patch("requests.Session.get")
    def test_get_stats_with_network_delay(self, mock_get, akc_client):
        """Get stats handles delayed but successful response."""
        response = MagicMock()
        response.json.return_value = {"sample_count": 100}
        response.headers = {}
        mock_get.return_value = response

        result = akc_client.get_stats()

        assert result["sample_count"] == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
