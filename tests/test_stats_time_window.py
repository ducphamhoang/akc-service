#!/usr/bin/env python3
"""
Unit tests for /stats time_window filtering.

Covers:
- _load_history_entries: timestamp-based filtering
- check_latency: windowed vs all-time stats
- count_history_patterns_in_window: unique pattern counts per window
- _parse_time_window: window-string → cutoff datetime conversion
- /stats endpoint: end-to-end 400 for invalid window, 200 for all valid windows
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_entry(pattern_id: str, timestamp: datetime, latency_ms: int = 10) -> dict:
    """Create a minimal confidence_history entry dict."""
    return {
        "history_id": f"ch-test-{pattern_id}",
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pattern_id": pattern_id,
        "latency_ms": latency_ms,
        "old_confidence": 0.5,
        "new_confidence": 0.55,
    }


def _write_history(path: Path, entries: list) -> None:
    """Write entries as JSONL to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ─── Tests for _load_history_entries ────────────────────────────────────────

class TestLoadHistoryEntries:
    """Unit tests for the internal _load_history_entries() helper."""

    def test_returns_all_entries_when_no_cutoff(self, tmp_path):
        """With cutoff_time=None all entries are returned."""
        from akc_service.learning_integration import _load_history_entries, CONFIDENCE_HISTORY_PATH

        now = datetime.now(timezone.utc)
        entries = [
            _make_entry("p1", now - timedelta(days=10)),
            _make_entry("p2", now - timedelta(hours=1)),
            _make_entry("p3", now - timedelta(minutes=5)),
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = _load_history_entries(cutoff_time=None)

        assert len(result) == 3

    def test_filters_entries_before_cutoff(self, tmp_path):
        """Entries with timestamp < cutoff are excluded."""
        from akc_service.learning_integration import _load_history_entries

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)

        entries = [
            _make_entry("p-old-1", now - timedelta(days=7)),   # before cutoff → excluded
            _make_entry("p-old-2", now - timedelta(days=2)),   # before cutoff → excluded
            _make_entry("p-new-1", now - timedelta(hours=12)), # after cutoff → included
            _make_entry("p-new-2", now - timedelta(hours=1)),  # after cutoff → included
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = _load_history_entries(cutoff_time=cutoff)

        assert len(result) == 2
        pattern_ids = {e["pattern_id"] for e in result}
        assert pattern_ids == {"p-new-1", "p-new-2"}

    def test_empty_file_returns_empty_list(self, tmp_path):
        """Empty history file returns empty list regardless of cutoff."""
        from akc_service.learning_integration import _load_history_entries

        hist_path = tmp_path / "confidence_history.jsonl"
        hist_path.write_text("", encoding="utf-8")

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = _load_history_entries(cutoff_time=datetime.now(timezone.utc))

        assert result == []

    def test_missing_file_returns_empty_list(self, tmp_path):
        """Non-existent history file returns empty list."""
        from akc_service.learning_integration import _load_history_entries

        hist_path = tmp_path / "nonexistent.jsonl"
        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = _load_history_entries(cutoff_time=datetime.now(timezone.utc))

        assert result == []

    def test_entries_with_bad_timestamps_excluded_when_cutoff_set(self, tmp_path):
        """Entries with unparseable timestamps are skipped when cutoff is active."""
        from akc_service.learning_integration import _load_history_entries

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)

        good_entry = _make_entry("p-good", now - timedelta(minutes=30))
        bad_entry = {"history_id": "ch-bad", "timestamp": "not-a-date", "pattern_id": "p-bad", "latency_ms": 5}

        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, [good_entry, bad_entry])

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = _load_history_entries(cutoff_time=cutoff)

        # bad_entry skipped; good_entry included
        assert len(result) == 1
        assert result[0]["pattern_id"] == "p-good"


# ─── Tests for check_latency (windowed) ─────────────────────────────────────

class TestCheckLatencyWindowed:
    """Tests for check_latency() with cutoff_time argument."""

    def test_all_time_includes_all_entries(self, tmp_path):
        """check_latency(cutoff_time=None) counts all entries."""
        from akc_service.learning_integration import check_latency

        now = datetime.now(timezone.utc)
        entries = [
            _make_entry("p1", now - timedelta(days=30), latency_ms=10),
            _make_entry("p2", now - timedelta(days=1),  latency_ms=20),
            _make_entry("p3", now - timedelta(hours=1), latency_ms=30),
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = check_latency(cutoff_time=None)

        assert result["sample_count"] == 3
        assert result["latency_stats"]["min_ms"] == 10
        assert result["latency_stats"]["max_ms"] == 30

    def test_windowed_excludes_old_entries(self, tmp_path):
        """check_latency with 24h cutoff excludes entries older than 24h."""
        from akc_service.learning_integration import check_latency

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)

        entries = [
            _make_entry("p-old", now - timedelta(days=7),  latency_ms=100),  # excluded
            _make_entry("p-new", now - timedelta(hours=12), latency_ms=5),   # included
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = check_latency(cutoff_time=cutoff)

        assert result["sample_count"] == 1
        assert result["latency_stats"]["min_ms"] == 5
        assert result["latency_stats"]["max_ms"] == 5

    def test_no_entries_in_window_returns_unknown(self, tmp_path):
        """check_latency with a cutoff that excludes all entries returns UNKNOWN."""
        from akc_service.learning_integration import check_latency

        now = datetime.now(timezone.utc)
        # All entries are old
        entries = [_make_entry("p1", now - timedelta(days=10))]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        recent_cutoff = now - timedelta(hours=1)
        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = check_latency(cutoff_time=recent_cutoff)

        assert result["sample_count"] == 0
        assert result["sla_status"] == "UNKNOWN"


# ─── Tests for count_history_patterns_in_window ─────────────────────────────

class TestCountHistoryPatternsInWindow:
    """Tests for count_history_patterns_in_window()."""

    def test_counts_unique_patterns(self, tmp_path):
        """Unique pattern IDs are counted (not total update events)."""
        from akc_service.learning_integration import count_history_patterns_in_window

        now = datetime.now(timezone.utc)
        entries = [
            _make_entry("p1", now - timedelta(hours=1)),
            _make_entry("p1", now - timedelta(minutes=30)),  # same pattern, second update
            _make_entry("p2", now - timedelta(minutes=15)),
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = count_history_patterns_in_window(cutoff_time=None)

        assert result["patterns_updated"] == 2   # p1 and p2
        assert result["total_updates"] == 3       # 3 events

    def test_windowed_count_is_filtered(self, tmp_path):
        """Windowed count excludes old entries."""
        from akc_service.learning_integration import count_history_patterns_in_window

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)

        entries = [
            _make_entry("p-old", now - timedelta(days=5)),   # excluded
            _make_entry("p-new-1", now - timedelta(hours=12)), # included
            _make_entry("p-new-2", now - timedelta(hours=6)),  # included
        ]
        hist_path = tmp_path / "confidence_history.jsonl"
        _write_history(hist_path, entries)

        with patch("akc_service.learning_integration.CONFIDENCE_HISTORY_PATH", hist_path):
            result = count_history_patterns_in_window(cutoff_time=cutoff)

        assert result["patterns_updated"] == 2
        assert result["total_updates"] == 2


# ─── Tests for _parse_time_window ────────────────────────────────────────────

class TestParseTimeWindow:
    """Tests for _parse_time_window() route helper."""

    def test_all_returns_none(self):
        from akc_service.api.routes import _parse_time_window
        assert _parse_time_window("all") is None

    def test_1h_returns_approx_1_hour_ago(self):
        from akc_service.api.routes import _parse_time_window
        before = datetime.now(timezone.utc) - timedelta(hours=1, seconds=1)
        result = _parse_time_window("1h")
        after = datetime.now(timezone.utc) - timedelta(hours=1) + timedelta(seconds=1)
        assert before < result < after

    def test_24h_returns_approx_24_hours_ago(self):
        from akc_service.api.routes import _parse_time_window
        before = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
        result = _parse_time_window("24h")
        after = datetime.now(timezone.utc) - timedelta(hours=24) + timedelta(seconds=1)
        assert before < result < after

    def test_7d_returns_approx_7_days_ago(self):
        from akc_service.api.routes import _parse_time_window
        before = datetime.now(timezone.utc) - timedelta(days=7, seconds=1)
        result = _parse_time_window("7d")
        after = datetime.now(timezone.utc) - timedelta(days=7) + timedelta(seconds=1)
        assert before < result < after

    def test_30d_returns_approx_30_days_ago(self):
        from akc_service.api.routes import _parse_time_window
        before = datetime.now(timezone.utc) - timedelta(days=30, seconds=1)
        result = _parse_time_window("30d")
        after = datetime.now(timezone.utc) - timedelta(days=30) + timedelta(seconds=1)
        assert before < result < after

    def test_unknown_returns_none(self):
        from akc_service.api.routes import _parse_time_window
        assert _parse_time_window("bogus") is None


# ─── End-to-end /stats endpoint tests ────────────────────────────────────────

@pytest.fixture
def client():
    from akc_service.api.main import app
    return TestClient(app)


class TestStatsEndpointTimeWindowE2E:
    """End-to-end tests for /stats time_window handling via TestClient."""

    @pytest.mark.parametrize("window", ["all", "1h", "24h", "7d", "30d"])
    def test_all_valid_windows_return_200(self, client, window):
        """All supported time_window values return 200."""
        with patch("akc_service.api.routes.check_latency") as mock_lat, \
             patch("akc_service.api.routes.load_all_patterns") as mock_pat, \
             patch("akc_service.api.routes.count_history_patterns_in_window") as mock_cnt:
            mock_lat.return_value = {"sample_count": 0, "latency_stats": {}, "sla_status": "UNKNOWN"}
            mock_pat.return_value = []
            mock_cnt.return_value = {"patterns_updated": 0, "total_updates": 0}

            response = client.get(f"/akc/v1/stats?time_window={window}")

        assert response.status_code == 200
        assert response.json()["time_window"] == window

    def test_invalid_window_returns_400(self, client):
        """Unrecognised time_window returns 400."""
        response = client.get("/akc/v1/stats?time_window=2weeks")
        assert response.status_code == 400

    def test_default_window_is_all(self, client):
        """Omitting time_window defaults to 'all' in the response."""
        with patch("akc_service.api.routes.check_latency") as mock_lat, \
             patch("akc_service.api.routes.load_all_patterns") as mock_pat, \
             patch("akc_service.api.routes.count_history_patterns_in_window") as mock_cnt:
            mock_lat.return_value = {"sample_count": 0, "latency_stats": {}, "sla_status": "UNKNOWN"}
            mock_pat.return_value = []
            mock_cnt.return_value = {"patterns_updated": 0, "total_updates": 0}

            response = client.get("/akc/v1/stats")

        assert response.status_code == 200
        assert response.json()["time_window"] == "all"

    def test_windowed_check_latency_called_with_cutoff(self, client):
        """When a non-all window is used, check_latency receives a cutoff_time kwarg."""
        with patch("akc_service.api.routes.check_latency") as mock_lat, \
             patch("akc_service.api.routes.load_all_patterns") as mock_pat, \
             patch("akc_service.api.routes.count_history_patterns_in_window") as mock_cnt:
            mock_lat.return_value = {"sample_count": 0, "latency_stats": {}, "sla_status": "UNKNOWN"}
            mock_pat.return_value = []
            mock_cnt.return_value = {"patterns_updated": 0, "total_updates": 0}

            client.get("/akc/v1/stats?time_window=7d")

        call_args = mock_lat.call_args
        assert call_args is not None
        cutoff = call_args.kwargs.get("cutoff_time")
        assert cutoff is not None
        # cutoff should be approximately 7 days ago
        expected_approx = datetime.now(timezone.utc) - timedelta(days=7)
        delta = abs((cutoff - expected_approx).total_seconds())
        assert delta < 5, f"Cutoff {cutoff!r} differs from expected 7d by {delta:.1f}s"
