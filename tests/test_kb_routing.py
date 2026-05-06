"""
KB Routing Test Suite

Tests for akc_service.config KB registry, entity mapping, resolve_kb_dir(),
validate_kb_config(), and startup logging. Covers all 10 Phase 1 success criteria
from ROADMAP.md (Phase 1: Config & Resolution).
"""

import dataclasses
import importlib
import json
import logging
import time
from pathlib import Path

import pytest

import akc_service.config as cfg_module


# ─── Shared Fixture ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def clean_config():
    """Reload config module after each test to reset module-level state."""
    yield
    import os
    for var in ("AKC_SERVICE_KB_REGISTRY", "AKC_SERVICE_ENTITY_KB_MAPPING"):
        os.environ.pop(var, None)
    importlib.reload(cfg_module)


# ─── TestKBRegistryParsing ────────────────────────────────────────────────────

class TestKBRegistryParsing:
    """CONFIG-01, CONFIG-05: KB_REGISTRY parsed from env var or defaults."""

    def test_valid_json_registry_is_parsed(self, monkeypatch, clean_config):
        """CONFIG-01: AKC_SERVICE_KB_REGISTRY with valid JSON is parsed into KB_REGISTRY dict."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        importlib.reload(cfg_module)
        assert cfg_module.KB_REGISTRY == {"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}

    def test_default_registry_when_no_env_var(self, monkeypatch, clean_config):
        """CONFIG-05: No env var → KB_REGISTRY defaults to package-internal kb/ directory."""
        monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
        importlib.reload(cfg_module)
        expected_default = Path(cfg_module.__file__).parent / "kb"
        assert cfg_module.KB_REGISTRY == {"default": str(expected_default)}

    def test_invalid_json_registry_raises_value_error(self, monkeypatch, clean_config):
        """CONFIG-02: Invalid JSON in AKC_SERVICE_KB_REGISTRY raises ValueError at startup."""
        monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", "not-valid-json{{{")
        with pytest.raises(ValueError) as exc_info:
            importlib.reload(cfg_module)
        assert "Invalid JSON in AKC_SERVICE_KB_REGISTRY" in str(exc_info.value)

    def test_invalid_json_registry_error_includes_example(self, monkeypatch, clean_config):
        """CONFIG-02: ValueError for non-dict JSON includes AKC_SERVICE_KB_REGISTRY in message."""
        monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", "[1, 2, 3]")
        with pytest.raises(ValueError) as exc_info:
            importlib.reload(cfg_module)
        assert "AKC_SERVICE_KB_REGISTRY" in str(exc_info.value)


# ─── TestEntityMappingParsing ─────────────────────────────────────────────────

class TestEntityMappingParsing:
    """CONFIG-04: ENTITY_KB_MAPPING validation."""

    def test_valid_json_mapping_is_parsed(self, monkeypatch, clean_config):
        """AKC_SERVICE_ENTITY_KB_MAPPING with valid JSON is parsed into ENTITY_KB_MAPPING dict."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        assert cfg_module.ENTITY_KB_MAPPING["entity:physics"] == "physics"
        assert cfg_module.ENTITY_KB_MAPPING["entity:*"] == "default"

    def test_invalid_json_mapping_raises_value_error(self, monkeypatch, clean_config):
        """CONFIG-02: Invalid JSON in AKC_SERVICE_ENTITY_KB_MAPPING raises ValueError."""
        monkeypatch.setenv("AKC_SERVICE_ENTITY_KB_MAPPING", "bad{{json")
        with pytest.raises(ValueError) as exc_info:
            importlib.reload(cfg_module)
        assert "Invalid JSON in AKC_SERVICE_ENTITY_KB_MAPPING" in str(exc_info.value)

    def test_mapping_referencing_unknown_kb_raises_value_error(self, monkeypatch, clean_config):
        """CONFIG-04: ENTITY_KB_MAPPING referencing KB not in KB_REGISTRY raises ValueError."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        with pytest.raises(ValueError) as exc_info:
            importlib.reload(cfg_module)
        error_msg = str(exc_info.value)
        assert "ENTITY_KB_MAPPING references unknown KB" in error_msg
        assert "physics" in error_msg
        assert "Available KBs" in error_msg

    def test_default_mapping_when_no_env_var(self, monkeypatch, clean_config):
        """Default ENTITY_KB_MAPPING is {'entity:*': 'default'} when env var absent."""
        monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
        monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
        importlib.reload(cfg_module)
        assert cfg_module.ENTITY_KB_MAPPING == {"entity:*": "default"}


# ─── TestKBContext ────────────────────────────────────────────────────────────

class TestKBContext:
    """ROUTE-01: KBContext dataclass fields."""

    def test_kbcontext_has_required_fields(self):
        """ROUTE-01: KBContext dataclass has path: Path, name: str, safety_level: int."""
        fields = {f.name: f for f in dataclasses.fields(cfg_module.KBContext)}
        assert "path" in fields
        assert "name" in fields
        assert "safety_level" in fields

    def test_kbcontext_has_routing_tier_field(self):
        """API-05: KBContext dataclass has routing_tier: str field (Wave 2 callers need it)."""
        fields = {f.name: f for f in dataclasses.fields(cfg_module.KBContext)}
        assert "routing_tier" in fields, (
            "KBContext is missing routing_tier field. "
            "Add `routing_tier: str` to the KBContext dataclass."
        )

    def test_kbcontext_instantiation(self, tmp_path):
        """KBContext can be instantiated with correct field types."""
        kbc = cfg_module.KBContext(path=tmp_path, name="default", safety_level=1, routing_tier="fallback")
        assert isinstance(kbc.path, Path)
        assert isinstance(kbc.name, str)
        assert isinstance(kbc.safety_level, int)
        assert kbc.name == "default"
        assert kbc.safety_level == 1

    def test_kbcontext_instantiation_with_routing_tier(self, tmp_path):
        """KBContext can be instantiated with routing_tier field."""
        kbc = cfg_module.KBContext(
            path=tmp_path, name="physics", safety_level=1, routing_tier="explicit"
        )
        assert isinstance(kbc.routing_tier, str)
        assert kbc.routing_tier == "explicit"

    def test_kbcontext_routing_tier_fallback_value(self, tmp_path):
        """KBContext accepts routing_tier='fallback' as a valid value."""
        kbc = cfg_module.KBContext(
            path=tmp_path, name="default", safety_level=1, routing_tier="fallback"
        )
        assert kbc.routing_tier == "fallback"


# ─── TestResolveKBDir ─────────────────────────────────────────────────────────

class TestResolveKBDir:
    """ROUTE-01, ROUTE-02, ROUTE-05, ROUTE-06: resolve_kb_dir() resolution tiers."""

    def test_explicit_override_tier1(self, monkeypatch, clean_config):
        """ROUTE-02: kb_override matching registry → KBContext with that KB name (Tier 1)."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(kb_override="physics")
        assert kbc.name == "physics"
        assert kbc.path == Path("/tmp/kb/physics")
        assert isinstance(kbc.safety_level, int)

    def test_fallback_tier3_no_args(self, monkeypatch, clean_config):
        """ROUTE-05: resolve_kb_dir() with no args → KBContext(name='default') (Tier 3)."""
        monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
        monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir()
        assert kbc.name == "default"
        assert isinstance(kbc.path, Path)
        assert isinstance(kbc.safety_level, int)

    def test_default_config_routes_to_package_kb(self, monkeypatch, clean_config):
        """CONFIG-05: No env var → resolve_kb_dir() path matches package-internal kb/ dir."""
        monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
        monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir()
        expected_path = Path(cfg_module.__file__).parent / "kb"
        assert kbc.path == expected_path

    def test_entity_mapping_tier2(self, monkeypatch, clean_config):
        """ROUTE-01: entity-based routing uses ENTITY_KB_MAPPING when kb_override absent (Tier 2)."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(entity="physics")
        assert kbc.name == "physics"
        assert kbc.path == Path("/tmp/kb/physics")

    def test_entity_wildcard_fallback(self, monkeypatch, clean_config):
        """ROUTE-01: entity not in mapping → uses entity:* wildcard KB (Tier 2 wildcard)."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(entity="rendering")
        assert kbc.name == "default"

    def test_explicit_override_takes_priority_over_entity(self, monkeypatch, clean_config):
        """ROUTE-02: Tier 1 (explicit) takes priority over Tier 2 (entity)."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({
                "default": "/tmp/kb/default",
                "physics": "/tmp/kb/physics",
                "animation": "/tmp/kb/animation",
            }),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(kb_override="animation", entity="physics")
        assert kbc.name == "animation"

    def test_explicit_override_routing_tier(self, monkeypatch, clean_config):
        """API-05/OBS-02: resolve_kb_dir with valid kb_override returns routing_tier='explicit'."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(kb_override="physics")
        assert hasattr(kbc, "routing_tier"), "KBContext missing routing_tier field"
        assert kbc.routing_tier == "explicit", (
            f"Expected routing_tier='explicit', got {kbc.routing_tier!r}"
        )

    def test_fallback_routing_tier_no_args(self, monkeypatch, clean_config):
        """API-05/OBS-02: resolve_kb_dir() with no args returns routing_tier='fallback'."""
        monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
        monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir()
        assert hasattr(kbc, "routing_tier"), "KBContext missing routing_tier field"
        assert kbc.routing_tier == "fallback", (
            f"Expected routing_tier='fallback', got {kbc.routing_tier!r}"
        )

    def test_unknown_kb_override_routing_tier_is_fallback(self, monkeypatch, clean_config):
        """D-02: kb_override not in registry → silent fallthrough → routing_tier='fallback'."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(kb_override="unknown_kb")
        assert hasattr(kbc, "routing_tier"), "KBContext missing routing_tier field"
        assert kbc.routing_tier == "fallback", (
            f"Expected routing_tier='fallback' for unknown kb_override, got {kbc.routing_tier!r}"
        )

    def test_entity_mapping_routing_tier(self, monkeypatch, clean_config):
        """API-05: entity matching explicit key in ENTITY_KB_MAPPING → routing_tier='entity_mapping'."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(entity="physics")
        assert hasattr(kbc, "routing_tier"), "KBContext missing routing_tier field"
        assert kbc.routing_tier == "entity_mapping", (
            f"Expected routing_tier='entity_mapping', got {kbc.routing_tier!r}"
        )

    def test_entity_wildcard_routing_tier(self, monkeypatch, clean_config):
        """API-05: entity not in explicit key → wildcard match → routing_tier='entity_wildcard'."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        monkeypatch.setenv(
            "AKC_SERVICE_ENTITY_KB_MAPPING",
            json.dumps({"entity:physics": "physics", "entity:*": "default"}),
        )
        importlib.reload(cfg_module)
        kbc = cfg_module.resolve_kb_dir(entity="rendering")
        assert hasattr(kbc, "routing_tier"), "KBContext missing routing_tier field"
        assert kbc.routing_tier == "entity_wildcard", (
            f"Expected routing_tier='entity_wildcard', got {kbc.routing_tier!r}"
        )


# ─── TestPerformance ─────────────────────────────────────────────────────────

class TestPerformance:
    """ROUTE-06: KB resolution completes in < 1ms (dict lookup only, no I/O)."""

    def test_resolution_under_1ms_average(self, monkeypatch, clean_config):
        """ROUTE-06: 1000 calls to resolve_kb_dir() complete in under 1 second total (< 1ms each)."""
        monkeypatch.setenv(
            "AKC_SERVICE_KB_REGISTRY",
            json.dumps({"default": "/tmp/kb/default", "physics": "/tmp/kb/physics"}),
        )
        importlib.reload(cfg_module)

        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            cfg_module.resolve_kb_dir(kb_override="physics")
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, (
            f"resolve_kb_dir() too slow: {iterations} calls took {elapsed:.3f}s "
            f"({elapsed / iterations * 1000:.3f}ms/call, expected < 1ms/call)"
        )


# ─── TestValidateKBConfig ────────────────────────────────────────────────────

class TestValidateKBConfig:
    """CONFIG-03, CONFIG-04: validate_kb_config() behavior."""

    def test_missing_directory_logs_warning_not_exception(self, tmp_path, caplog):
        """CONFIG-03: Missing KB directory → WARNING log, no exception raised."""
        nonexistent = str(tmp_path / "does_not_exist")
        registry = {"default": nonexistent}
        mapping = {"entity:*": "default"}

        with caplog.at_level(logging.WARNING, logger="akc_service.config"):
            cfg_module.validate_kb_config(registry=registry, mapping=mapping)

        assert any("KB directory does not exist" in r.message for r in caplog.records), (
            f"Expected WARNING about missing directory. Got: {[r.message for r in caplog.records]}"
        )

    def test_unknown_kb_in_mapping_raises_value_error(self):
        """CONFIG-04: ENTITY_KB_MAPPING references unknown KB → ValueError."""
        registry = {"default": "/tmp/kb/default"}
        mapping = {"entity:unknown_domain": "nonexistent_kb", "entity:*": "default"}

        with pytest.raises(ValueError) as exc_info:
            cfg_module.validate_kb_config(registry=registry, mapping=mapping)

        error_msg = str(exc_info.value)
        assert "ENTITY_KB_MAPPING references unknown KB" in error_msg
        assert "nonexistent_kb" in error_msg
        assert "Available KBs" in error_msg

    def test_valid_config_does_not_raise(self, tmp_path):
        """validate_kb_config() with all-valid config raises no exception."""
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        registry = {"default": str(kb_path)}
        mapping = {"entity:*": "default"}
        cfg_module.validate_kb_config(registry=registry, mapping=mapping)


# ─── TestStartupLogging ───────────────────────────────────────────────────────

class TestStartupLogging:
    """OBS-03: Startup validation logs registered KB names and entity mapping count."""

    def test_startup_logs_registry_names(self, tmp_path, caplog):
        """OBS-03: validate_kb_config() logs 'KB Registry loaded:' with KB names."""
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        registry = {"default": str(kb_path), "physics": str(kb_path)}
        mapping = {"entity:*": "default"}

        with caplog.at_level(logging.INFO, logger="akc_service.config"):
            cfg_module.validate_kb_config(registry=registry, mapping=mapping)

        registry_log = [r.message for r in caplog.records if "KB Registry loaded" in r.message]
        assert len(registry_log) >= 1, (
            f"Expected 'KB Registry loaded' log. Got: {[r.message for r in caplog.records]}"
        )
        assert "default" in registry_log[0]

    def test_startup_logs_entity_mapping_count(self, tmp_path, caplog):
        """OBS-03: validate_kb_config() logs 'Entity mappings loaded: N entries'."""
        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        registry = {"default": str(kb_path)}
        mapping = {"entity:physics": "default", "entity:*": "default"}

        with caplog.at_level(logging.INFO, logger="akc_service.config"):
            cfg_module.validate_kb_config(registry=registry, mapping=mapping)

        mapping_log = [r.message for r in caplog.records if "Entity mappings loaded" in r.message]
        assert len(mapping_log) >= 1, (
            f"Expected 'Entity mappings loaded' log. Got: {[r.message for r in caplog.records]}"
        )
        assert "2" in mapping_log[0]
