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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
