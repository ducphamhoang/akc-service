import json
import pytest
from pathlib import Path


def _make_pattern(pid: str, confidence: float = 0.80) -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


class TestSyncPendingQueue:
    def test_append_adds_to_pending_when_sync_enabled(self, tmp_path, monkeypatch):
        import akc_service.sync.config as sync_cfg
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(sync_cfg, "REMOTE_URL", "http://remote:8000")
        monkeypatch.setattr(sync_cfg, "MIN_CONFIDENCE", 0.70)

        pattern = _make_pattern("pat-queue-001", confidence=0.85)
        li.append_pattern_version(pattern)

        from akc_service.sync.state import load_state
        state = load_state(tmp_path)
        assert "pat-queue-001" in state["pending_pattern_ids"]

    def test_append_skips_pending_when_sync_disabled(self, tmp_path, monkeypatch):
        import akc_service.sync.config as sync_cfg
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(sync_cfg, "REMOTE_URL", "")

        pattern = _make_pattern("pat-queue-002", confidence=0.85)
        li.append_pattern_version(pattern)

        from akc_service.sync.state import load_state
        state = load_state(tmp_path)
        assert "pat-queue-002" not in state["pending_pattern_ids"]

    def test_append_skips_low_confidence_patterns(self, tmp_path, monkeypatch):
        import akc_service.sync.config as sync_cfg
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(sync_cfg, "REMOTE_URL", "http://remote:8000")
        monkeypatch.setattr(sync_cfg, "MIN_CONFIDENCE", 0.70)

        pattern = _make_pattern("pat-queue-003", confidence=0.50)
        li.append_pattern_version(pattern)

        from akc_service.sync.state import load_state
        state = load_state(tmp_path)
        assert "pat-queue-003" not in state["pending_pattern_ids"]
