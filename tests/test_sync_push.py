import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _seed_kb(kb_dir: Path, patterns: list) -> None:
    pf = kb_dir / "patterns.jsonl"
    pf.write_text("\n".join(json.dumps(p) for p in patterns) + "\n")


def _make_pattern(pid: str, confidence: float, updated_at: str = "2026-05-05T10:00:00Z") -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": updated_at,
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


class TestSyncPush:
    def test_push_sends_eligible_patterns(self, tmp_path, monkeypatch):
        patterns = [
            _make_pattern("pat-push-001", confidence=0.85),
            _make_pattern("pat-push-002", confidence=0.50),
        ]
        _seed_kb(tmp_path, patterns)

        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-push-001", tmp_path)

        import akc_service.sync.push as push_mod
        monkeypatch.setattr(push_mod, "load_all_patterns", lambda: patterns)

        with patch("akc_service.sync.push.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"accepted": 1}
            mock_httpx.post.return_value = mock_resp

            from akc_service.sync.push import push_to_remote
            result = push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

        assert result["pushed"] == 1
        assert result["skipped"] == 0
        call_body = mock_httpx.post.call_args[1]["json"]
        assert len(call_body["patterns"]) == 1
        assert call_body["patterns"][0]["id"] == "pat-push-001"

    def test_push_clears_pending_on_success(self, tmp_path, monkeypatch):
        patterns = [_make_pattern("pat-push-003", confidence=0.80)]
        _seed_kb(tmp_path, patterns)

        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-push-003", tmp_path)

        import akc_service.sync.push as push_mod
        monkeypatch.setattr(push_mod, "load_all_patterns", lambda: patterns)

        with patch("akc_service.sync.push.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"accepted": 1}
            mock_httpx.post.return_value = mock_resp

            from akc_service.sync.push import push_to_remote
            push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

        state = load_state(tmp_path)
        assert "pat-push-003" not in state["pending_pattern_ids"]

    def test_push_dry_run_does_not_send(self, tmp_path, monkeypatch):
        patterns = [_make_pattern("pat-dry-001", confidence=0.80)]
        _seed_kb(tmp_path, patterns)

        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-dry-001", tmp_path)

        import akc_service.sync.push as push_mod
        monkeypatch.setattr(push_mod, "load_all_patterns", lambda: patterns)

        with patch("akc_service.sync.push.httpx") as mock_httpx:
            from akc_service.sync.push import push_to_remote
            result = push_to_remote(
                kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key", dry_run=True
            )

        mock_httpx.post.assert_not_called()
        assert result["pushed"] == 0
        assert result["would_push"] == 1

    def test_push_records_error_on_network_failure(self, tmp_path, monkeypatch):
        patterns = [_make_pattern("pat-err-001", confidence=0.80)]
        _seed_kb(tmp_path, patterns)

        from akc_service.sync.state import load_state, add_pending_id
        state = load_state(tmp_path)
        add_pending_id(state, "pat-err-001", tmp_path)

        import akc_service.sync.push as push_mod
        monkeypatch.setattr(push_mod, "load_all_patterns", lambda: patterns)

        with patch("akc_service.sync.push.httpx") as mock_httpx:
            mock_httpx.post.side_effect = Exception("connection refused")

            from akc_service.sync.push import push_to_remote
            result = push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

        assert result["errors"] == 1
        state = load_state(tmp_path)
        assert len(state["sync_errors"]) == 1
        assert "connection refused" in state["sync_errors"][0]["error"]
