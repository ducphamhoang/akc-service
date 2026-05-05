import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_pattern(pid: str, confidence: float, updated_at: str = "2026-05-05T10:00:00Z") -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": updated_at,
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


class TestSyncPull:
    def test_pull_writes_new_remote_patterns(self, tmp_path, monkeypatch):
        remote_pattern = _make_pattern("pat-remote-001", confidence=0.80)
        written_patterns = []

        import akc_service.sync.pull as pull_mod
        monkeypatch.setattr(pull_mod, "load_all_patterns", lambda: [])
        monkeypatch.setattr(pull_mod, "append_pattern_version", lambda p: written_patterns.append(p))

        with patch("akc_service.sync.pull.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
            }
            mock_httpx.get.return_value = mock_resp

            from akc_service.sync.pull import pull_from_remote
            result = pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

        assert result["pulled"] == 1
        assert result["errors"] == 0
        assert len(written_patterns) == 1
        assert written_patterns[0]["id"] == "pat-remote-001"

    def test_pull_keeps_local_when_local_confidence_higher(self, tmp_path, monkeypatch):
        local_pattern = _make_pattern("pat-conflict-001", confidence=0.90)
        remote_pattern = _make_pattern("pat-conflict-001", confidence=0.60)
        written_patterns = []

        import akc_service.sync.pull as pull_mod
        monkeypatch.setattr(pull_mod, "load_all_patterns", lambda: [local_pattern])
        monkeypatch.setattr(pull_mod, "append_pattern_version", lambda p: written_patterns.append(p))

        with patch("akc_service.sync.pull.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
            }
            mock_httpx.get.return_value = mock_resp

            from akc_service.sync.pull import pull_from_remote
            result = pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

        assert result["conflicts"] == 1
        # remote pattern was NOT written — local was kept
        assert len(written_patterns) == 0

    def test_pull_overwrite_local_forces_remote_version(self, tmp_path, monkeypatch):
        local_pattern = _make_pattern("pat-overwrite-001", confidence=0.90)
        remote_pattern = _make_pattern("pat-overwrite-001", confidence=0.60)
        written_patterns = []

        import akc_service.sync.pull as pull_mod
        monkeypatch.setattr(pull_mod, "load_all_patterns", lambda: [local_pattern])
        monkeypatch.setattr(pull_mod, "append_pattern_version", lambda p: written_patterns.append(p))

        with patch("akc_service.sync.pull.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
            }
            mock_httpx.get.return_value = mock_resp

            from akc_service.sync.pull import pull_from_remote
            result = pull_from_remote(
                kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k", overwrite_local=True
            )

        assert len(written_patterns) == 1
        assert written_patterns[0]["confidence"] == 0.60

    def test_pull_updates_last_pull_cursor(self, tmp_path, monkeypatch):
        import akc_service.sync.pull as pull_mod
        monkeypatch.setattr(pull_mod, "load_all_patterns", lambda: [])
        monkeypatch.setattr(pull_mod, "append_pattern_version", lambda p: None)

        with patch("akc_service.sync.pull.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "patterns": [_make_pattern("pat-cursor-001", 0.80)],
                "count": 1,
                "as_of": "2026-05-05T14:00:00Z",
            }
            mock_httpx.get.return_value = mock_resp

            from akc_service.sync.pull import pull_from_remote
            pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

        from akc_service.sync.state import load_state
        state = load_state(tmp_path)
        assert state["last_pull_cursor"] == "2026-05-05T14:00:00Z"
