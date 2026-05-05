import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from akc_service.api.main import app
    return TestClient(app)


class TestSyncRoutes:
    def test_sync_status_returns_200(self, client):
        response = client.get("/akc/v1/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert "remote_url" in data
        assert "connected" in data
        assert "push_queue_size" in data

    def test_sync_export_returns_empty_when_no_kb(self, client, tmp_path, monkeypatch):
        import akc_service.api.sync_routes as sr
        monkeypatch.setattr(sr, "KB_DIR", tmp_path)
        with patch("akc_service.api.sync_routes.load_all_patterns", return_value=[]):
            response = client.get("/akc/v1/sync/export")
        assert response.status_code == 200
        data = response.json()
        assert data["patterns"] == []
        assert data["count"] == 0

    @patch("akc_service.api.sync_routes.push_to_remote")
    def test_sync_push_calls_push_logic(self, mock_push, client, monkeypatch):
        import akc_service.sync.config as sc
        monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
        mock_push.return_value = {"pushed": 3, "skipped": 1, "errors": 0, "cursor": None}
        response = client.post("/akc/v1/sync/push", json={})
        assert response.status_code == 200
        assert response.json()["pushed"] == 3
        mock_push.assert_called_once()

    @patch("akc_service.api.sync_routes.pull_from_remote")
    def test_sync_pull_calls_pull_logic(self, mock_pull, client, monkeypatch):
        import akc_service.sync.config as sc
        monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
        mock_pull.return_value = {"pulled": 5, "conflicts": 1, "errors": 0}
        response = client.post("/akc/v1/sync/pull", json={})
        assert response.status_code == 200
        assert response.json()["pulled"] == 5
        mock_pull.assert_called_once()

    def test_sync_push_returns_503_when_disabled(self, client, monkeypatch):
        import akc_service.sync.config as sc
        monkeypatch.setattr(sc, "REMOTE_URL", "")
        response = client.post("/akc/v1/sync/push", json={})
        assert response.status_code == 503
