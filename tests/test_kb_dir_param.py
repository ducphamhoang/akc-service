"""
Tests for kb_dir parameter isolation in learning_integration.py and safety_engine.py.
Phase 02, Plan 01 — TDD RED tests.

These tests verify:
1. learning_integration I/O functions use kb_dir when provided
2. safety_engine I/O functions use kb_dir when provided
3. Backward compat: no kb_dir → module-level KB_DIR used
4. Isolation: writes to kb_a do NOT affect kb_b
"""
import json
import tempfile
import pytest
from pathlib import Path


# ─── Task 1: learning_integration.py kb_dir isolation ────────────────────────

class TestLearningIntegrationKbDir:
    """Verify kb_dir parameter isolation in learning_integration.py."""

    def _make_pattern(self, pat_id: str = "test-001") -> dict:
        return {
            "id": pat_id,
            "confidence": 0.8,
            "entity": "player",
            "component": "Health",
            "confidence_tier": "production",
            "version": {"current": "v1", "history": []},
        }

    def test_load_all_patterns_reads_from_kb_dir(self, tmp_path):
        """Test 1: load_all_patterns(kb_dir=X) reads from X/patterns.jsonl, not KB_DIR."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Write a pattern directly into kb_a
        pattern = self._make_pattern("test-001")
        (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        # load_all_patterns(kb_dir=kb_a) returns the pattern
        loaded_a = li.load_all_patterns(kb_dir=kb_a)
        assert len(loaded_a) == 1, f"Expected 1 pattern in kb_a, got {len(loaded_a)}"
        assert loaded_a[0]["id"] == "test-001"

        # load_all_patterns(kb_dir=kb_b) returns empty
        loaded_b = li.load_all_patterns(kb_dir=kb_b)
        assert len(loaded_b) == 0, f"Expected 0 patterns in kb_b, got {len(loaded_b)}"

    def test_append_pattern_version_writes_to_kb_dir(self, tmp_path):
        """Test 2: append_pattern_version(pattern, kb_dir=X) creates X/patterns.jsonl."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Ensure there's a safety_state.json in kb_a so quarantine check passes
        (kb_a / "safety_state.json").write_text('{"escape_hatch": null}', encoding="utf-8")

        pattern = self._make_pattern("test-002")
        li.append_pattern_version(pattern, kb_dir=kb_a)

        assert (kb_a / "patterns.jsonl").exists(), "kb_a/patterns.jsonl should exist"
        assert not (kb_b / "patterns.jsonl").exists(), "kb_b/patterns.jsonl should NOT exist"

    def test_log_confidence_update_writes_to_kb_dir(self, tmp_path):
        """Test 3: log_confidence_update(entry, kb_dir=X) writes to X/confidence_history.jsonl."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Ensure safety state allows writes
        (kb_a / "safety_state.json").write_text('{"escape_hatch": null}', encoding="utf-8")

        entry = {"history_id": "ch-001", "timestamp": "2026-01-01T00:00:00Z", "pattern_id": "test-003"}
        li.log_confidence_update(entry, kb_dir=kb_a)

        assert (kb_a / "confidence_history.jsonl").exists(), "kb_a/confidence_history.jsonl should exist"
        assert not (kb_b / "confidence_history.jsonl").exists(), "kb_b/confidence_history.jsonl should NOT exist"

    def test_save_checkpoint_uses_kb_dir(self, tmp_path):
        """Test 4: save_checkpoint(kb_dir=X) creates X/patterns.checkpoint."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Write patterns to kb_a first
        pattern = self._make_pattern("test-004")
        (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        li.save_checkpoint(kb_dir=kb_a)

        assert (kb_a / "patterns.checkpoint").exists(), "kb_a/patterns.checkpoint should exist"
        assert not (kb_b / "patterns.checkpoint").exists(), "kb_b/patterns.checkpoint should NOT exist"

    def test_apply_confidence_delta_isolation(self, tmp_path):
        """Test 5: apply_confidence_delta writes only to kb_a, not kb_b."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Set up safety state so quarantine check passes
        (kb_a / "safety_state.json").write_text('{"escape_hatch": null}', encoding="utf-8")

        # Write a pattern to kb_a
        pattern = self._make_pattern("test-005")
        (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        task_result = {
            "schema_version": "1.0",
            "task_id": "task-001",
            "status": "success",
            "timestamp": "2026-01-01T00:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["test-005"],
            },
        }

        result = li.apply_confidence_delta(task_result, kb_dir=kb_a)
        assert result["status"] == "success", f"Expected success, got: {result}"
        assert result["patterns_updated"] >= 1

        # kb_b should have NO files created by this call
        assert not (kb_b / "patterns.jsonl").exists(), "kb_b/patterns.jsonl should NOT exist"

    def test_backward_compat_no_kb_dir(self, tmp_path, monkeypatch):
        """Test 6: Calling load_all_patterns() with no kb_dir uses module-level KB_DIR."""
        from akc_service import learning_integration as li

        # Point KB_DIR to a temp dir with a known pattern
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(li, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(li, "CONFIDENCE_HISTORY_PATH", tmp_path / "confidence_history.jsonl")
        monkeypatch.setattr(li, "CHECKPOINT_PATH", tmp_path / "patterns.checkpoint")

        # Write a pattern to tmp_path
        pattern = {"id": "compat-001", "confidence": 0.75}
        (tmp_path / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        loaded = li.load_all_patterns()  # no kb_dir
        assert any(p["id"] == "compat-001" for p in loaded), "Backward compat: should read from KB_DIR"

    def test_append_confidence_history_uses_kb_dir(self, tmp_path):
        """append_confidence_history(entry, kb_dir=X) writes to X/confidence_history.jsonl."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        entry = {"history_id": "ch-002", "timestamp": "2026-01-01T00:00:00Z"}
        li.append_confidence_history(entry, kb_dir=kb_a)

        assert (kb_a / "confidence_history.jsonl").exists(), "kb_a/confidence_history.jsonl should exist"
        assert not (kb_b / "confidence_history.jsonl").exists(), "kb_b/confidence_history.jsonl should NOT exist"

    def test_save_all_patterns_uses_kb_dir(self, tmp_path):
        """save_all_patterns(patterns, kb_dir=X) writes to X/patterns.jsonl."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Set up safety state
        (kb_a / "safety_state.json").write_text('{"escape_hatch": null}', encoding="utf-8")

        patterns = [self._make_pattern("save-001"), self._make_pattern("save-002")]
        li.save_all_patterns(patterns, kb_dir=kb_a)

        assert (kb_a / "patterns.jsonl").exists(), "kb_a/patterns.jsonl should exist"
        assert not (kb_b / "patterns.jsonl").exists(), "kb_b/patterns.jsonl should NOT exist"

        loaded = li.load_all_patterns(kb_dir=kb_a)
        assert len(loaded) == 2, f"Expected 2 patterns in kb_a, got {len(loaded)}"

    def test_restore_from_checkpoint_uses_kb_dir(self, tmp_path):
        """restore_from_checkpoint(kb_dir=X) restores from X/patterns.checkpoint."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Create checkpoint in kb_a
        pattern = self._make_pattern("restore-001")
        (kb_a / "patterns.checkpoint").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        # Restore from checkpoint in kb_a
        result = li.restore_from_checkpoint(kb_dir=kb_a)
        assert result is True, "restore_from_checkpoint should return True on success"
        assert (kb_a / "patterns.jsonl").exists(), "kb_a/patterns.jsonl should be restored"

        # kb_b should have no files
        assert not (kb_b / "patterns.jsonl").exists(), "kb_b/patterns.jsonl should NOT exist"

    def test_build_pattern_index_uses_kb_dir(self, tmp_path):
        """build_pattern_index(kb_dir=X) reads from X/patterns.jsonl."""
        from akc_service import learning_integration as li

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        pattern = self._make_pattern("idx-001")
        (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        idx_a = li.build_pattern_index(kb_dir=kb_a)
        assert "idx-001" in idx_a, f"Expected idx-001 in index, got: {list(idx_a.keys())}"

        idx_b = li.build_pattern_index(kb_dir=kb_b)
        assert len(idx_b) == 0, f"Expected empty index for kb_b, got: {list(idx_b.keys())}"


# ─── Task 2: safety_engine.py kb_dir isolation ───────────────────────────────

class TestSafetyEngineKbDir:
    """Verify kb_dir parameter isolation in safety_engine.py."""

    def test_load_safety_state_from_kb_dir(self, tmp_path):
        """Test 1: load_safety_state(kb_dir=X) returns default when X/safety_state.json doesn't exist."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_a.mkdir()

        # No safety_state.json in kb_a — should return default
        default = se.load_safety_state(kb_dir=kb_a)
        assert default.get("escape_hatch") is None, f"Expected None, got: {default}"
        assert "escape_hatch_set_at" in default
        assert "escape_hatch_reason" in default

    def test_save_safety_state_writes_to_kb_dir(self, tmp_path):
        """Test 2: save_safety_state writes to X/safety_state.json; KB_DIR is unaffected."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        state = {
            "escape_hatch": "caution",
            "escape_hatch_set_at": "2026-01-01T00:00:00Z",
            "escape_hatch_reason": "test",
        }
        se.save_safety_state(state, kb_dir=kb_a)

        assert (kb_a / "safety_state.json").exists(), "kb_a/safety_state.json should exist"
        assert not (kb_b / "safety_state.json").exists(), "kb_b/safety_state.json should NOT exist"

        loaded = se.load_safety_state(kb_dir=kb_a)
        assert loaded.get("escape_hatch") == "caution"

    def test_load_all_patterns_uses_kb_dir(self, tmp_path):
        """Test 3: load_all_patterns(kb_dir=X) returns [] when X/patterns.jsonl is missing."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_a.mkdir()

        result = se.load_all_patterns(kb_dir=kb_a)
        assert result == [], f"Expected [], got: {result}"

    def test_detect_conflicts_uses_kb_dir(self, tmp_path):
        """Test 4: detect_conflicts(kb_dir=X) uses patterns from X/patterns.jsonl only."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Write a pattern to kb_a
        pattern = {
            "id": "conflict-001",
            "confidence": 0.8,
            "entity": "player",
            "component": "Health",
            "pattern_type": "script_logic",
        }
        (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

        # detect_conflicts on kb_b (no patterns) returns no conflicts
        result_b = se.detect_conflicts(kb_dir=kb_b)
        assert result_b["total_conflicts"] == 0, f"Expected 0 conflicts from kb_b, got: {result_b['total_conflicts']}"

    def test_backward_compat_no_kb_dir(self, tmp_path, monkeypatch):
        """Test 5: Calling any function with no kb_dir uses module-level KB_DIR."""
        from akc_service import safety_engine as se

        monkeypatch.setattr(se, "KB_DIR", tmp_path)
        monkeypatch.setattr(se, "PATTERNS_PATH", tmp_path / "patterns.jsonl")
        monkeypatch.setattr(se, "FIX_HISTORY_PATH", tmp_path / "fix_history.jsonl")
        monkeypatch.setattr(se, "CONFIDENCE_HISTORY_PATH", tmp_path / "confidence_history.jsonl")
        monkeypatch.setattr(se, "SAFETY_STATE_PATH", tmp_path / "safety_state.json")

        # save + load with no kb_dir
        state = {"escape_hatch": "caution", "escape_hatch_set_at": None, "escape_hatch_reason": None}
        se.save_safety_state(state)  # no kb_dir
        loaded = se.load_safety_state()  # no kb_dir
        assert loaded.get("escape_hatch") == "caution", "Backward compat: should read from KB_DIR"

    def test_append_confidence_history_uses_kb_dir_se(self, tmp_path):
        """append_confidence_history in safety_engine writes to X/confidence_history.jsonl."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        entry = {"history_id": "esc-001", "timestamp": "2026-01-01T00:00:00Z", "event_type": "escape_hatch_change"}
        se.append_confidence_history(entry, kb_dir=kb_a)

        assert (kb_a / "confidence_history.jsonl").exists(), "kb_a/confidence_history.jsonl should exist"
        assert not (kb_b / "confidence_history.jsonl").exists(), "kb_b/confidence_history.jsonl should NOT exist"

    def test_isolation_across_kbs_complete(self, tmp_path):
        """Full isolation test: save to kb_a, load from kb_b returns defaults."""
        from akc_service import safety_engine as se

        kb_a = tmp_path / "kb_a"
        kb_b = tmp_path / "kb_b"
        kb_a.mkdir()
        kb_b.mkdir()

        # Save caution state to kb_a
        state = {
            "escape_hatch": "caution",
            "escape_hatch_set_at": "2026-01-01T00:00:00Z",
            "escape_hatch_reason": "isolation test",
        }
        se.save_safety_state(state, kb_dir=kb_a)

        # Loading from kb_b should return default (no file there)
        kb_b_state = se.load_safety_state(kb_dir=kb_b)
        assert kb_b_state.get("escape_hatch") is None, f"kb_b should have no escape_hatch, got: {kb_b_state}"
