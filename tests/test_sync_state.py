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
