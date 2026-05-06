#!/usr/bin/env python3
"""
Test checkpoint save/restore and reset escape hatch functionality.

Tests:
1. save_checkpoint() creates checkpoint file
2. restore_from_checkpoint() restores patterns from checkpoint
3. set_escape_hatch("reset") calls restore and returns success
4. Checkpoint restoration is atomic (tmp file then rename)
5. restore_from_checkpoint() returns False when no checkpoint exists
"""

import json
import tempfile
from pathlib import Path
import pytest

# Import functions to test
from akc_service.learning_integration import (
    save_checkpoint,
    restore_from_checkpoint,
    PATTERNS_PATH,
    CHECKPOINT_PATH,
    KB_DIR,
)
from akc_service.safety_engine import set_escape_hatch


@pytest.fixture
def temp_kb_dir(monkeypatch):
    """Create temporary KB directory and set environment variable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        kb_path = Path(tmpdir)
        monkeypatch.setenv("AKC_SERVICE_KB_DIR", str(kb_path))

        # Reimport to pick up new KB_DIR
        import importlib
        import akc_service.learning_integration as li
        import akc_service.safety_engine as se
        importlib.reload(li)
        importlib.reload(se)

        yield kb_path

        # Cleanup: reimport again to restore original paths
        importlib.reload(li)
        importlib.reload(se)


def test_save_checkpoint_creates_file(temp_kb_dir):
    """Test that save_checkpoint creates checkpoint file."""
    # Reload modules with temp KB dir
    import importlib
    import akc_service.learning_integration as li
    importlib.reload(li)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    # Create test patterns file
    test_patterns = [
        {"id": "pat-1", "confidence": 0.85, "name": "Pattern 1"},
        {"id": "pat-2", "confidence": 0.70, "name": "Pattern 2"},
    ]
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    with open(patterns_path, "w") as f:
        for p in test_patterns:
            f.write(json.dumps(p) + "\n")

    # Save checkpoint
    li.save_checkpoint()

    # Verify checkpoint exists and has same content
    assert checkpoint_path.exists()
    checkpoint_content = checkpoint_path.read_text()
    patterns_content = patterns_path.read_text()
    assert checkpoint_content == patterns_content


def test_save_checkpoint_does_nothing_if_no_patterns(temp_kb_dir):
    """Test that save_checkpoint does nothing if patterns.jsonl doesn't exist."""
    import importlib
    import akc_service.learning_integration as li
    importlib.reload(li)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    # patterns.jsonl doesn't exist
    assert not patterns_path.exists()

    # Save checkpoint should not fail
    li.save_checkpoint()

    # Checkpoint should not be created
    assert not checkpoint_path.exists()


def test_restore_from_checkpoint(temp_kb_dir):
    """Test that restore_from_checkpoint restores patterns."""
    import importlib
    import akc_service.learning_integration as li
    importlib.reload(li)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    # Create checkpoint with test patterns
    original_patterns = [
        {"id": "pat-1", "confidence": 0.85, "name": "Pattern 1"},
        {"id": "pat-2", "confidence": 0.70, "name": "Pattern 2"},
    ]
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_content = "\n".join(json.dumps(p) for p in original_patterns) + "\n"
    checkpoint_path.write_text(checkpoint_content)

    # Modify patterns file (simulate corruption or changes)
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    corrupted_patterns = [
        {"id": "pat-1", "confidence": 0.10, "name": "Pattern 1 CORRUPTED"},
        {"id": "pat-99", "confidence": 0.50, "name": "Spam Pattern"},
    ]
    patterns_path.write_text("\n".join(json.dumps(p) for p in corrupted_patterns) + "\n")

    # Restore from checkpoint
    success = li.restore_from_checkpoint()

    assert success

    # Verify patterns were restored
    restored = patterns_path.read_text()
    assert restored == checkpoint_content

    # Verify content is correct
    lines = restored.strip().split("\n")
    restored_patterns = [json.loads(line) for line in lines]
    assert len(restored_patterns) == 2
    assert restored_patterns[0]["id"] == "pat-1"
    assert restored_patterns[0]["confidence"] == 0.85
    assert restored_patterns[1]["id"] == "pat-2"
    assert restored_patterns[1]["confidence"] == 0.70


def test_restore_from_checkpoint_returns_false_if_no_checkpoint(temp_kb_dir):
    """Test that restore_from_checkpoint returns False if checkpoint doesn't exist."""
    import importlib
    import akc_service.learning_integration as li
    importlib.reload(li)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "pat-1"}\n')

    # No checkpoint exists
    assert not checkpoint_path.exists()

    # Restore should fail
    success = li.restore_from_checkpoint()

    assert not success


def test_restore_from_checkpoint_is_atomic(temp_kb_dir):
    """Test that restore_from_checkpoint uses atomic write (tmp then rename)."""
    import importlib
    import akc_service.learning_integration as li
    importlib.reload(li)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    # Create checkpoint
    checkpoint_content = '{"id": "pat-1", "confidence": 0.85}\n'
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(checkpoint_content)

    # Create corrupted patterns
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "pat-1", "confidence": 0.10}\n')

    # Restore
    success = li.restore_from_checkpoint()

    assert success

    # Verify patterns were restored and no tmp file exists
    assert patterns_path.exists()
    assert not patterns_path.with_suffix(".tmp").exists()
    assert patterns_path.read_text() == checkpoint_content


def test_reset_escape_hatch_calls_restore(temp_kb_dir, monkeypatch):
    """Test that set_escape_hatch('reset') calls restore_from_checkpoint."""
    import importlib
    import akc_service.learning_integration as li
    import akc_service.safety_engine as se
    importlib.reload(li)
    importlib.reload(se)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"
    safety_state_path = temp_kb_dir / "safety_state.json"

    # Create checkpoint
    checkpoint_content = '{"id": "pat-1", "confidence": 0.85}\n'
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(checkpoint_content)

    # Create corrupted patterns
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "pat-1", "confidence": 0.10}\n')

    # Trigger reset escape hatch
    result = se.set_escape_hatch("reset", reason="Test recovery")

    assert result["success"]
    assert result["escape_hatch"] == "reset"

    # Verify patterns were restored
    assert patterns_path.read_text() == checkpoint_content

    # Verify effects indicate successful restore
    side_effects = result.get("side_effects", [])
    assert any("restored from checkpoint" in e for e in side_effects)


def test_reset_escape_hatch_reports_error_if_no_checkpoint(temp_kb_dir):
    """Test that reset escape hatch reports error if no checkpoint available."""
    import importlib
    import akc_service.safety_engine as se
    importlib.reload(se)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    safety_state_path = temp_kb_dir / "safety_state.json"

    # Create corrupted patterns but no checkpoint
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "pat-1", "confidence": 0.10}\n')

    # Trigger reset escape hatch
    result = se.set_escape_hatch("reset", reason="Test recovery")

    assert result["success"]  # Escape hatch still sets successfully

    # But side_effects should indicate failure
    side_effects = result.get("side_effects", [])
    assert any("ERROR" in e and "No checkpoint" in e for e in side_effects)


def test_reset_blocked_when_quarantine_active(temp_kb_dir):
    """Test that reset is blocked when quarantine mode is active."""
    import importlib
    import json
    import akc_service.safety_engine as se
    importlib.reload(se)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"
    safety_state_path = temp_kb_dir / "safety_state.json"

    # Create checkpoint and patterns
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text('{"id": "pat-1", "confidence": 0.85}\n')
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "pat-1", "confidence": 0.10}\n')

    # Activate quarantine mode
    quarantine_state = {"escape_hatch": "quarantine", "escape_hatch_set_at": "2026-01-01T00:00:00Z"}
    safety_state_path.write_text(json.dumps(quarantine_state))

    # Trigger reset escape hatch — should be blocked by quarantine guard
    result = se.set_escape_hatch("reset", reason="Test recovery while quarantined")

    assert result["success"]
    side_effects = result.get("side_effects", [])

    # Verify the quarantine guard blocked the restore
    assert any("blocked" in e.lower() or "quarantine" in e.lower() for e in side_effects), \
        f"Expected quarantine block message in side_effects, got: {side_effects}"

    # Verify patterns.jsonl was NOT restored (quarantine blocked it)
    current_content = patterns_path.read_text()
    assert '"confidence": 0.10' in current_content, \
        "patterns.jsonl should NOT have been restored when quarantine is active"


def test_reset_reports_pattern_count_after_restore(temp_kb_dir):
    """Test that reset reports the correct number of restored patterns."""
    import importlib
    import json
    import akc_service.safety_engine as se
    importlib.reload(se)

    patterns_path = temp_kb_dir / "patterns.jsonl"
    checkpoint_path = temp_kb_dir / "patterns.checkpoint"

    # Create checkpoint with 3 patterns
    checkpoint_patterns = [
        {"id": "pat-1", "confidence": 0.90, "name": "Gold Pattern"},
        {"id": "pat-2", "confidence": 0.75, "name": "Production Pattern"},
        {"id": "pat-3", "confidence": 0.60, "name": "Experimental Pattern"},
    ]
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("\n".join(json.dumps(p) for p in checkpoint_patterns) + "\n")

    # Corrupt the patterns file
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text('{"id": "bad-pat", "confidence": 0.10}\n')

    # Trigger reset
    result = se.set_escape_hatch("reset", reason="Test pattern count")

    assert result["success"]
    side_effects = result.get("side_effects", [])

    # Verify restore happened
    assert any("restored" in e.lower() for e in side_effects), \
        f"Expected restore confirmation in side_effects, got: {side_effects}"

    # Verify pattern count is reported
    assert any("3 patterns" in e for e in side_effects), \
        f"Expected '3 patterns' in side_effects, got: {side_effects}"

    # Verify patterns.jsonl was actually restored
    restored_content = patterns_path.read_text()
    assert "pat-1" in restored_content
    assert "pat-2" in restored_content
    assert "pat-3" in restored_content
    assert "bad-pat" not in restored_content


# ─── /reset REST API Endpoint Tests ─────────────────────────────────────────

def _reload_all_for_kb(tmp_path, monkeypatch):
    """Helper: set AKC_SERVICE_KB_DIR + AKC_SERVICE_KB_REGISTRY to tmp_path and reload all modules."""
    import json as _json
    import importlib
    monkeypatch.setenv("AKC_SERVICE_KB_DIR", str(tmp_path))
    monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", _json.dumps({"default": str(tmp_path)}))
    import akc_service.config as cfg
    import akc_service.learning_integration as li
    import akc_service.safety_engine as se
    import akc_service.api.routes as r
    importlib.reload(cfg)
    importlib.reload(li)
    importlib.reload(se)
    importlib.reload(r)
    return li, se, r


def _restore_all():
    """Helper: restore all modules after test."""
    import importlib
    import akc_service.config as cfg
    import akc_service.learning_integration as li
    import akc_service.safety_engine as se
    import akc_service.api.routes as r
    importlib.reload(cfg)
    importlib.reload(li)
    importlib.reload(se)
    importlib.reload(r)


class TestResetEndpoint:
    """Tests for the /akc/v1/reset REST endpoint."""

    @pytest.fixture
    def api_client(self):
        from fastapi.testclient import TestClient
        from akc_service.api.main import app
        return TestClient(app)

    def test_reset_returns_restored_when_checkpoint_exists(self, api_client, tmp_path, monkeypatch):
        """POST /reset restores KB from checkpoint and returns status='restored'."""
        checkpoint_content = '{"id": "pat-1", "confidence": 0.90}\n'
        (tmp_path / "patterns.checkpoint").write_text(checkpoint_content)
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-bad", "confidence": 0.05}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test_reset"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()
        assert data["status"] == "restored"
        assert data["checkpoint_used"] is True
        assert data["patterns_restored"] >= 1
        assert data["reason"] == "test_reset"
        assert "timestamp" in data
        assert len(data["effects"]) > 0

        _restore_all()

    def test_reset_blocked_by_quarantine(self, api_client, tmp_path, monkeypatch):
        """POST /reset returns 409 when quarantine mode is active."""
        import json
        # Write checkpoint so we don't fail on missing checkpoint first
        (tmp_path / "patterns.checkpoint").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        # Set quarantine in safety state
        quarantine_state = {"escape_hatch": "quarantine", "escape_hatch_set_at": "2026-01-01T00:00:00Z"}
        (tmp_path / "safety_state.json").write_text(json.dumps(quarantine_state))

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test"})
        assert response.status_code == 409, f"Got {response.status_code}: {response.text}"
        data = response.json()
        # The global exception handler wraps the message under "error" key
        error_msg = data.get("error") or data.get("detail") or ""
        assert "quarantine" in error_msg.lower(), f"Expected 'quarantine' in error, got: {data}"

        _restore_all()

    def test_reset_returns_503_when_no_checkpoint(self, api_client, tmp_path, monkeypatch):
        """POST /reset returns 503 when no checkpoint file exists."""
        # Only create patterns.jsonl, no checkpoint
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-1", "confidence": 0.90}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test"})
        assert response.status_code == 503, f"Got {response.status_code}: {response.text}"
        data = response.json()
        # The global exception handler wraps the message under "error" key
        error_msg = data.get("error") or data.get("detail") or ""
        assert "checkpoint" in error_msg.lower(), f"Expected 'checkpoint' in error, got: {data}"

        _restore_all()

    def test_reset_response_has_required_fields(self, api_client, tmp_path, monkeypatch):
        """POST /reset response includes all required fields."""
        (tmp_path / "patterns.checkpoint").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-bad", "confidence": 0.05}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "verify_fields"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()
        required_fields = ["status", "reason", "patterns_restored", "checkpoint_used", "effects", "timestamp"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

        _restore_all()

    # ─── New Tests: kb param routing, telemetry fields, audit failure ─────────

    def test_reset_response_includes_new_fields(self, api_client, tmp_path, monkeypatch):
        """POST /reset response includes checkpoint_created_at (ISO 8601) and patterns_before_reset (int)."""
        (tmp_path / "patterns.checkpoint").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-bad", "confidence": 0.05}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test_new_fields"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()

        # checkpoint_created_at must be a non-empty ISO 8601 string
        assert "checkpoint_created_at" in data, "Missing checkpoint_created_at"
        assert isinstance(data["checkpoint_created_at"], str)
        assert len(data["checkpoint_created_at"]) > 0
        assert "T" in data["checkpoint_created_at"], "checkpoint_created_at must be ISO 8601 (contains T)"

        # patterns_before_reset must be a non-negative integer
        assert "patterns_before_reset" in data, "Missing patterns_before_reset"
        assert isinstance(data["patterns_before_reset"], int)
        assert data["patterns_before_reset"] >= 0

        _restore_all()

    def test_reset_kb_patterns_before_reset_count(self, api_client, tmp_path, monkeypatch):
        """patterns_before_reset reflects count before restore; patterns_restored reflects checkpoint count."""
        import json

        # Checkpoint has 2 patterns
        checkpoint_patterns = [
            {"id": "pat-1", "confidence": 0.90, "name": "Checkpoint Pattern 1"},
            {"id": "pat-2", "confidence": 0.75, "name": "Checkpoint Pattern 2"},
        ]
        (tmp_path / "patterns.checkpoint").write_text(
            "\n".join(json.dumps(p) for p in checkpoint_patterns) + "\n"
        )

        # Current patterns.jsonl has 3 patterns (different ids)
        current_patterns = [
            {"id": "cur-1", "confidence": 0.50, "name": "Current Pattern 1"},
            {"id": "cur-2", "confidence": 0.60, "name": "Current Pattern 2"},
            {"id": "cur-3", "confidence": 0.70, "name": "Current Pattern 3"},
        ]
        (tmp_path / "patterns.jsonl").write_text(
            "\n".join(json.dumps(p) for p in current_patterns) + "\n"
        )

        _reload_all_for_kb(tmp_path, monkeypatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test_counts"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()

        assert data["patterns_before_reset"] == 3, f"Expected 3, got {data['patterns_before_reset']}"
        assert data["patterns_restored"] == 2, f"Expected 2, got {data['patterns_restored']}"

        _restore_all()

    def test_reset_kb_audit_failure_warns_in_effects(self, api_client, tmp_path, monkeypatch):
        """When set_escape_hatch raises, effects list contains audit failure warning."""
        (tmp_path / "patterns.checkpoint").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-bad", "confidence": 0.05}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        # Patch set_escape_hatch in safety_engine module to raise — reset_kb() imports it via
        # `from akc_service.safety_engine import set_escape_hatch as _set_escape_hatch` on each call
        import akc_service.safety_engine as se

        def _raise_escape_hatch(*args, **kwargs):
            raise RuntimeError("Simulated audit write failure")

        monkeypatch.setattr(se, "set_escape_hatch", _raise_escape_hatch)

        response = api_client.post("/akc/v1/reset", json={"reason": "test_audit_fail"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()

        effects = data.get("effects", [])
        assert any("WARNING" in e and "audit trail write failed" in e for e in effects), \
            f"Expected audit failure warning in effects, got: {effects}"

        _restore_all()

    def test_reset_kb_default_kb_backward_compat(self, api_client, tmp_path, monkeypatch):
        """POST /reset without kb param succeeds — backward compatible with pre-multi-KB deployments."""
        (tmp_path / "patterns.checkpoint").write_text('{"id": "pat-1", "confidence": 0.90}\n')
        (tmp_path / "patterns.jsonl").write_text('{"id": "pat-bad", "confidence": 0.05}\n')

        _reload_all_for_kb(tmp_path, monkeypatch)

        # Send request WITHOUT kb param — backward compatible
        response = api_client.post("/akc/v1/reset", json={"reason": "backward_compat_test"})
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()
        assert data["status"] == "restored"
        # No KeyError, no 500, no exception
        assert "patterns_restored" in data
        assert data["checkpoint_used"] is True

        _restore_all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
