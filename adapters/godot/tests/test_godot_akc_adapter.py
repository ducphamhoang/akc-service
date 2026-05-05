"""
Unit tests for GodotAKCAdapter.

Covers:
  - test_record_lint_fail_posts_per_error: each lint error → one POST call
  - test_record_test_pass: passing test run → one POST with outcome=pass
  - test_fallback_on_connection_error: connection refused → no exception raised
"""

import sys
from pathlib import Path

# packages/akc-service/adapters/godot/tests → parents[3] = packages/akc-service
# This makes akc_service.adapters.godot importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
import requests
from unittest.mock import patch, MagicMock

from akc_service.adapters.godot.godot_akc_adapter import GodotAKCAdapter


# ── Fixtures ──────────────────────────────────────────────────────────────────

LINT_FAIL = {
    "passed": False,
    "errors": [
        {"file": "player.gd", "line": 10, "message": "Unused variable: hp"},
        {"file": "player.gd", "line": 22, "message": "Missing return type"},
    ],
    "raw_output": (
        "player.gd:10: Error: Unused variable: hp\n"
        "player.gd:22: Error: Missing return type"
    ),
}

LINT_PASS = {"passed": True, "errors": [], "raw_output": ""}


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_record_lint_fail_posts_per_error():
    """record_lint_result with 2 errors → exactly 2 POSTs with correct payloads."""
    adapter = GodotAKCAdapter(akc_url="http://localhost:9999")
    with patch.object(adapter.session, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=202)
        adapter.record_lint_result(LINT_FAIL, "player.gd")

    assert mock_post.call_count == 2, (
        f"Expected 2 POST calls (one per error), got {mock_post.call_count}"
    )

    # Validate first call payload
    first_call_kwargs = mock_post.call_args_list[0][1]
    payload = first_call_kwargs["json"]
    assert payload["akc_context"]["entity"] == "gdscript"
    assert payload["akc_context"]["outcome"] == "lint_fail"
    assert "Unused variable" in payload["akc_context"]["error_signature"]
    assert payload["status"] == "failed"
    assert payload["schema_version"] == "1.0"

    # Validate second call payload
    second_call_kwargs = mock_post.call_args_list[1][1]
    payload2 = second_call_kwargs["json"]
    assert "Missing return type" in payload2["akc_context"]["error_signature"]


def test_record_test_pass():
    """record_test_result with passed=True → 1 POST with outcome=pass."""
    adapter = GodotAKCAdapter(akc_url="http://localhost:9999")
    with patch.object(adapter.session, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=202)
        adapter.record_test_result("1 passed", passed=True, test_file="test_player.py")

    assert mock_post.call_count == 1, (
        f"Expected 1 POST call, got {mock_post.call_count}"
    )

    payload = mock_post.call_args[1]["json"]
    assert payload["akc_context"]["outcome"] == "pass"
    assert payload["akc_context"]["entity"] == "godot_test"
    assert payload["status"] == "success"
    assert payload["schema_version"] == "1.0"


def test_fallback_on_connection_error():
    """record_lint_result must NOT raise when akc-service is unreachable."""
    adapter = GodotAKCAdapter(akc_url="http://localhost:9999")
    with patch.object(
        adapter.session, "post", side_effect=requests.exceptions.ConnectionError("refused")
    ):
        # Must NOT raise — adapter swallows connection errors
        try:
            adapter.record_lint_result(LINT_FAIL, "player.gd")
        except Exception as exc:
            pytest.fail(
                f"record_lint_result raised {type(exc).__name__} on connection error: {exc}"
            )
    # If we reach here, fallback worked correctly


def test_adapter_reads_akc_url_from_env(monkeypatch):
    """Adapter reads AKC_SERVICE_URL from environment variable."""
    monkeypatch.setenv("AKC_SERVICE_URL", "http://remote-host:9000")
    adapter = GodotAKCAdapter()
    assert adapter.akc_url == "http://remote-host:9000"
    assert "remote-host:9000" in adapter.record_endpoint


def test_adapter_explicit_url_overrides_env(monkeypatch):
    """Explicit akc_url argument overrides AKC_SERVICE_URL env var."""
    monkeypatch.setenv("AKC_SERVICE_URL", "http://remote-host:9000")
    adapter = GodotAKCAdapter(akc_url="http://explicit:8888")
    assert adapter.akc_url == "http://explicit:8888"
