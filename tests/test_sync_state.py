import os
import pytest


class TestSyncConfig:
    def test_remote_url_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("AKC_SERVICE_REMOTE_URL", raising=False)
        import importlib
        import akc_service.sync.config as c
        importlib.reload(c)
        assert c.REMOTE_URL == ""

    def test_sync_disabled_when_no_remote_url(self, monkeypatch):
        monkeypatch.delenv("AKC_SERVICE_REMOTE_URL", raising=False)
        import importlib
        import akc_service.sync.config as c
        importlib.reload(c)
        assert c.sync_enabled() is False

    def test_sync_enabled_when_remote_url_set(self, monkeypatch):
        monkeypatch.setenv("AKC_SERVICE_REMOTE_URL", "http://remote:8000")
        import importlib
        import akc_service.sync.config as c
        importlib.reload(c)
        assert c.sync_enabled() is True

    def test_min_confidence_default(self, monkeypatch):
        monkeypatch.delenv("AKC_SERVICE_SYNC_MIN_CONFIDENCE", raising=False)
        import importlib
        import akc_service.sync.config as c
        importlib.reload(c)
        assert c.MIN_CONFIDENCE == 0.70

    def test_push_batch_default(self, monkeypatch):
        monkeypatch.delenv("AKC_SERVICE_SYNC_PUSH_BATCH", raising=False)
        import importlib
        import akc_service.sync.config as c
        importlib.reload(c)
        assert c.PUSH_BATCH == 50


class TestSyncState:
    def test_load_state_returns_defaults_when_no_file(self, tmp_path):
        from akc_service.sync.state import load_state
        state = load_state(tmp_path)
        assert state["schema_version"] == "1.0"
        assert state["remote_url"] == ""
        assert state["last_push_cursor"] is None
        assert state["last_pull_cursor"] is None
        assert state["pending_pattern_ids"] == []
        assert state["sync_errors"] == []

    def test_save_and_reload_state(self, tmp_path):
        from akc_service.sync.state import load_state, save_state
        state = load_state(tmp_path)
        state["remote_url"] = "http://remote:9000"
        state["last_push_cursor"] = "2026-05-05T12:00:00Z"
        save_state(state, tmp_path)
        reloaded = load_state(tmp_path)
        assert reloaded["remote_url"] == "http://remote:9000"
        assert reloaded["last_push_cursor"] == "2026-05-05T12:00:00Z"

    def test_add_pending_pattern_id(self, tmp_path):
        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-001", tmp_path)
        reloaded = load_state(tmp_path)
        assert "pat-001" in reloaded["pending_pattern_ids"]

    def test_add_pending_id_is_idempotent(self, tmp_path):
        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-001", tmp_path)
        add_pending_id(state, "pat-001", tmp_path)
        reloaded = load_state(tmp_path)
        assert reloaded["pending_pattern_ids"].count("pat-001") == 1

    def test_clear_pending_ids(self, tmp_path):
        from akc_service.sync.state import load_state, add_pending_id, clear_pending_ids
        state = load_state(tmp_path)
        add_pending_id(state, "pat-001", tmp_path)
        add_pending_id(state, "pat-002", tmp_path)
        state = load_state(tmp_path)
        clear_pending_ids(state, ["pat-001"], tmp_path)
        reloaded = load_state(tmp_path)
        assert "pat-001" not in reloaded["pending_pattern_ids"]
        assert "pat-002" in reloaded["pending_pattern_ids"]
