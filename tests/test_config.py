#!/usr/bin/env python3
"""
Configuration Module Tests

Tests for akc_service.config module that handles centralized environment variable reading
and defaults.
"""

import os
import importlib
import pytest


@pytest.fixture
def clean_config():
    """Fixture that cleans up config module state."""
    yield
    # After test, reload to reset state
    import akc_service.config as cfg
    importlib.reload(cfg)


class TestKBDir:
    """Tests for KB_DIR configuration."""

    def test_kb_dir_default(self, monkeypatch, clean_config):
        """KB_DIR defaults to akc_service/kb when env var absent."""
        monkeypatch.delenv("AKC_SERVICE_KB_DIR", raising=False)
        import akc_service.config as cfg
        importlib.reload(cfg)

        from pathlib import Path
        expected = Path(__file__).parent.parent / "akc_service" / "kb"
        assert cfg.KB_DIR == expected

    def test_kb_dir_from_env(self, monkeypatch, clean_config, tmp_path):
        """KB_DIR reads from AKC_SERVICE_KB_DIR env var."""
        monkeypatch.setenv("AKC_SERVICE_KB_DIR", str(tmp_path))
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.KB_DIR == tmp_path


class TestSafetyLevel:
    """Tests for SAFETY_LEVEL configuration."""

    def test_safety_level_defaults_to_1(self, monkeypatch, clean_config):
        """SAFETY_LEVEL defaults to 1 when env var absent."""
        monkeypatch.delenv("AKC_SERVICE_SAFETY_LEVEL", raising=False)
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.SAFETY_LEVEL == 1

    def test_safety_level_reads_from_env(self, monkeypatch, clean_config):
        """SAFETY_LEVEL reads from AKC_SERVICE_SAFETY_LEVEL env var."""
        monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", "2")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.SAFETY_LEVEL == 2

    def test_safety_level_clamping(self, monkeypatch, clean_config):
        """SAFETY_LEVEL is clamped to valid values 0, 1, 2."""
        # Test that valid values are preserved
        for level in [0, 1, 2]:
            monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", str(level))
            import akc_service.config as cfg
            importlib.reload(cfg)
            assert cfg.SAFETY_LEVEL == level

    def test_safety_level_invalid_falls_back_to_1(self, monkeypatch, clean_config):
        """SAFETY_LEVEL falls back to 1 on invalid input."""
        monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", "banana")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.SAFETY_LEVEL == 1

    def test_safety_level_invalid_non_integer_falls_back(self, monkeypatch, clean_config):
        """SAFETY_LEVEL falls back to 1 on non-integer input."""
        monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", "3.14")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.SAFETY_LEVEL == 1


class TestAKCUrl:
    """Tests for AKC_URL configuration."""

    def test_akc_url_default(self, monkeypatch, clean_config):
        """AKC_URL defaults to http://localhost:8000."""
        monkeypatch.delenv("AKC_SERVICE_URL", raising=False)
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.AKC_URL == "http://localhost:8000"

    def test_akc_url_reads_from_env(self, monkeypatch, clean_config):
        """AKC_URL reads from AKC_SERVICE_URL env var."""
        monkeypatch.setenv("AKC_SERVICE_URL", "http://remote-host:9000")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.AKC_URL == "http://remote-host:9000"


class TestLogLevel:
    """Tests for LOG_LEVEL configuration."""

    def test_log_level_default(self, monkeypatch, clean_config):
        """LOG_LEVEL defaults to INFO."""
        monkeypatch.delenv("AKC_SERVICE_LOG_LEVEL", raising=False)
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.LOG_LEVEL == "INFO"

    def test_log_level_reads_from_env(self, monkeypatch, clean_config):
        """LOG_LEVEL reads from AKC_SERVICE_LOG_LEVEL env var."""
        monkeypatch.setenv("AKC_SERVICE_LOG_LEVEL", "debug")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.LOG_LEVEL == "DEBUG"

    def test_log_level_uppercase(self, monkeypatch, clean_config):
        """LOG_LEVEL is uppercased."""
        monkeypatch.setenv("AKC_SERVICE_LOG_LEVEL", "warning")
        import akc_service.config as cfg
        importlib.reload(cfg)

        assert cfg.LOG_LEVEL == "WARNING"


class TestMaxDeltaForLevel:
    """Tests for max_delta_for_level() function."""

    def test_max_delta_for_level_0(self, clean_config):
        """max_delta_for_level(0) returns 0.25."""
        import akc_service.config as cfg
        assert cfg.max_delta_for_level(0) == 0.25

    def test_max_delta_for_level_1(self, clean_config):
        """max_delta_for_level(1) returns 0.15."""
        import akc_service.config as cfg
        assert cfg.max_delta_for_level(1) == 0.15

    def test_max_delta_for_level_2(self, clean_config):
        """max_delta_for_level(2) returns 0.10."""
        import akc_service.config as cfg
        assert cfg.max_delta_for_level(2) == 0.10

    def test_max_delta_for_level_invalid_defaults_to_1(self, clean_config):
        """max_delta_for_level(invalid) defaults to 1's value (0.15)."""
        import akc_service.config as cfg
        assert cfg.max_delta_for_level(999) == 0.15
