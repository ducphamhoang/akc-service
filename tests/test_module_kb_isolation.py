"""
tests/test_module_kb_isolation.py

Phase 2 isolation tests: verifies data isolation when kb_dir params are used.

Success criteria tested:
  SC-01: learning_integration.append_pattern_version + load_all_patterns use kb_dir
  SC-02: safety_engine functions use kb_dir
  SC-03: monitoring_engine functions use kb_dir
  SC-04: failure_detection functions use kb_dir
  SC-05: latency_monitor writes latency_samples.jsonl to kb_dir (ISOLATE-04)
  SC-06: Write to kb_a does NOT create files in kb_b (ISOLATE-01)
  SC-07: Querying kb_b returns 0 results when only kb_a written (ISOLATE-02)
  SC-08: confidence_history.jsonl written to correct KB subdirectory (ISOLATE-03)
  SC-09: fix_history / safety_state land in correct KB subdirectory (ISOLATE-05)
  SC-10: patterns.checkpoint created in correct KB subdirectory (ISOLATE-06)
  SC-11: Backward compat — all modules fall back to KB_DIR when kb_dir=None
"""

import json
from pathlib import Path
import pytest


# ─── Shared fixtures ─────────────────────────────────────────────────────────────

MINIMAL_PATTERN = {
    "id": "pat-001",
    "confidence": 0.75,
    "entity": "player",
    "component": "HealthComponent",
    "confidence_tier": "production",
    "version": {"current": "v1", "history": []},
    "description": "test pattern",
    "rule": "use health properly",
    "example_incorrect": "",
    "updated_at": "2026-01-01T00:00:00Z",
}


def _write_safety_state(kb_dir: Path) -> None:
    (kb_dir / "safety_state.json").write_text('{"escape_hatch": null}', encoding="utf-8")


# ─── SC-01: learning_integration.py — kb_dir isolation ──────────────────────────

def test_learning_integration_append_pattern_to_kb_dir(tmp_path):
    """ISOLATE-01 / MODULE-01: append_pattern_version writes only to kb_dir, not global KB_DIR."""
    from akc_service import learning_integration as li

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()
    _write_safety_state(kb_a)

    pattern = dict(MINIMAL_PATTERN)
    li.append_pattern_version(pattern, kb_dir=kb_a)

    assert (kb_a / "patterns.jsonl").exists(), "patterns.jsonl must be created in kb_a"
    assert not (kb_b / "patterns.jsonl").exists(), "patterns.jsonl must NOT be created in kb_b"


# ─── SC-02: safety_engine.py — kb_dir isolation ──────────────────────────────────

def test_safety_engine_save_load_safety_state(tmp_path):
    """MODULE-02: save_safety_state writes to kb_dir; load_safety_state reads from same."""
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

    assert (kb_a / "safety_state.json").exists(), "safety_state.json must be created in kb_a"
    assert not (kb_b / "safety_state.json").exists(), "safety_state.json must NOT exist in kb_b"

    loaded_a = se.load_safety_state(kb_dir=kb_a)
    loaded_b = se.load_safety_state(kb_dir=kb_b)

    assert loaded_a.get("escape_hatch") == "caution", f"Expected caution, got {loaded_a}"
    assert loaded_b.get("escape_hatch") is None, f"Expected None from kb_b, got {loaded_b}"


# ─── SC-03: monitoring_engine.py — kb_dir isolation ─────────────────────────────

def test_monitoring_engine_load_patterns_isolation(tmp_path):
    """MODULE-03: monitoring_engine.load_all_patterns reads from kb_dir."""
    from akc_service import monitoring_engine as me

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    (kb_a / "patterns.jsonl").write_text(json.dumps(MINIMAL_PATTERN) + "\n", encoding="utf-8")

    loaded_a = me.load_all_patterns(kb_dir=kb_a)
    loaded_b = me.load_all_patterns(kb_dir=kb_b)

    assert len(loaded_a) == 1, f"Expected 1 pattern from kb_a, got {len(loaded_a)}"
    assert len(loaded_b) == 0, f"Expected 0 patterns from kb_b, got {len(loaded_b)}"


# ─── SC-04: failure_detection.py — kb_dir isolation ─────────────────────────────

def test_failure_detection_load_patterns_isolation(tmp_path):
    """MODULE-04: failure_detection.load_patterns reads from kb_dir."""
    from akc_service import failure_detection as fd

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    (kb_a / "patterns.jsonl").write_text(json.dumps(MINIMAL_PATTERN) + "\n", encoding="utf-8")

    loaded_a = fd.load_patterns(kb_dir=kb_a)
    loaded_b = fd.load_patterns(kb_dir=kb_b)

    assert len(loaded_a) == 1, f"Expected 1 pattern from kb_a, got {len(loaded_a)}"
    assert len(loaded_b) == 0, f"Expected 0 patterns from kb_b, got {len(loaded_b)}"


# ─── SC-05: latency_monitor.py — latency_samples.jsonl isolation (ISOLATE-04) ───

def test_latency_monitor_writes_to_kb_dir(tmp_path):
    """ISOLATE-04 / MODULE-05: track_candidate_latency writes latency_samples.jsonl to kb_dir."""
    from akc_service import latency_monitor as lm

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    ts = {
        "T0": "2026-01-01T10:00:00Z",
        "T1": "2026-01-01T10:01:30Z",
        "T3": "2026-01-01T10:03:30Z",
        "T4": "2026-01-01T10:04:00Z",
        "T5": "2026-01-01T10:04:10Z",
        "T6": "2026-01-01T10:07:00Z",
    }
    lm.track_candidate_latency("cand-test-001", ts, kb_dir=kb_a)

    assert (kb_a / "latency_samples.jsonl").exists(), "latency_samples.jsonl must be created in kb_a"
    assert not (kb_b / "latency_samples.jsonl").exists(), "latency_samples.jsonl must NOT exist in kb_b"

    stats_a = lm.get_latency_stats(kb_dir=kb_a)
    stats_b = lm.get_latency_stats(kb_dir=kb_b)

    assert stats_a["sample_count"] == 1, f"Expected 1 sample in kb_a, got {stats_a['sample_count']}"
    assert stats_b["sample_count"] == 0, f"Expected 0 samples in kb_b, got {stats_b['sample_count']}"


# ─── SC-06: ISOLATE-01 — write to kb_a does NOT create files in kb_b ────────────

def test_learning_integration_no_cross_kb_leakage(tmp_path):
    """ISOLATE-01 / ISOLATE-02: Write to kb_a; querying kb_b returns 0 results."""
    from akc_service import learning_integration as li

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()
    _write_safety_state(kb_a)

    pattern = dict(MINIMAL_PATTERN)
    li.append_pattern_version(pattern, kb_dir=kb_a)

    results = li.load_all_patterns(kb_dir=kb_b)
    assert results == [], f"Expected 0 results from kb_b, got {len(results)}: {results}"


# ─── SC-07: ISOLATE-02 — failure_detection: no cross-KB leakage ─────────────────

def test_failure_detection_factor_uses_kb_dir(tmp_path):
    """MODULE-04 / ISOLATE-02: factor_pattern_matching queries patterns only from specified kb_dir."""
    from akc_service import failure_detection as fd

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    pattern = {**MINIMAL_PATTERN, "entity": "player", "component": "HealthComponent"}
    (kb_a / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")

    result_a = fd.factor_pattern_matching("player", "HealthComponent", "health null", kb_dir=kb_a)
    result_b = fd.factor_pattern_matching("player", "HealthComponent", "health null", kb_dir=kb_b)

    assert result_a["score"] > result_b["score"], (
        f"kb_a score ({result_a['score']}) should exceed kb_b score ({result_b['score']}) "
        "because kb_a has matching pattern"
    )


# ─── SC-08: ISOLATE-03 — confidence_history.jsonl in correct KB subdirectory ────

def test_monitoring_engine_confidence_history_isolation(tmp_path):
    """ISOLATE-03 / MODULE-03: confidence_history.jsonl read from correct KB subdirectory."""
    from akc_service import monitoring_engine as me

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    entry = {
        "history_id": "ch-001",
        "timestamp": "2026-01-01T10:00:00Z",
        "pattern_id": "pat-001",
        "old_confidence": 0.7,
        "new_confidence": 0.75,
        "delta": 0.05,
    }
    (kb_a / "confidence_history.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    history_a = me.load_confidence_history(kb_dir=kb_a)
    history_b = me.load_confidence_history(kb_dir=kb_b)

    assert len(history_a) == 1, f"Expected 1 entry from kb_a, got {len(history_a)}"
    assert len(history_b) == 0, f"Expected 0 entries from kb_b, got {len(history_b)}"


# ─── SC-09: ISOLATE-05 — fix_history / safety_state in correct KB subdirectory ──

def test_safety_engine_fix_history_isolation(tmp_path):
    """ISOLATE-05 / MODULE-02: safety_state.json and confidence_history.jsonl land in correct KB."""
    from akc_service import safety_engine as se

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    # append_confidence_history writes to kb_dir/confidence_history.jsonl
    entry = {
        "history_id": "ch-fix-001",
        "timestamp": "2026-01-01T00:00:00Z",
        "event_type": "escape_hatch_change",
    }
    se.append_confidence_history(entry, kb_dir=kb_a)

    assert (kb_a / "confidence_history.jsonl").exists(), "confidence_history.jsonl must exist in kb_a"
    assert not (kb_b / "confidence_history.jsonl").exists(), "confidence_history.jsonl must NOT exist in kb_b"

    # save_safety_state also goes to kb_a only
    state = {
        "escape_hatch": "caution",
        "escape_hatch_set_at": "2026-01-01T00:00:00Z",
        "escape_hatch_reason": "test",
    }
    se.save_safety_state(state, kb_dir=kb_a)
    assert (kb_a / "safety_state.json").exists()
    assert not (kb_b / "safety_state.json").exists()


# ─── SC-10: ISOLATE-06 — patterns.checkpoint in correct KB subdirectory ─────────

def test_checkpoint_isolation(tmp_path):
    """ISOLATE-06: save_checkpoint creates patterns.checkpoint in kb_dir only."""
    from akc_service import learning_integration as li

    kb_a = tmp_path / "kb_a"
    kb_b = tmp_path / "kb_b"
    kb_a.mkdir()
    kb_b.mkdir()

    (kb_a / "patterns.jsonl").write_text(json.dumps(MINIMAL_PATTERN) + "\n", encoding="utf-8")

    li.save_checkpoint(kb_dir=kb_a)

    assert (kb_a / "patterns.checkpoint").exists(), "patterns.checkpoint must be created in kb_a"
    assert not (kb_b / "patterns.checkpoint").exists(), "patterns.checkpoint must NOT exist in kb_b"


# ─── SC-11: Backward compat — fall back to KB_DIR when kb_dir=None ──────────────

def test_backward_compat_learning_integration_no_kb_dir(tmp_path, monkeypatch):
    """MODULE-05: learning_integration.append_pattern_version() with no kb_dir uses KB_DIR."""
    import akc_service.learning_integration as li

    fake_kb = tmp_path / "fake_global_kb"
    fake_kb.mkdir()
    monkeypatch.setattr(li, "KB_DIR", fake_kb)

    pattern = dict(MINIMAL_PATTERN)
    li.append_pattern_version(pattern)

    assert (fake_kb / "patterns.jsonl").exists(), (
        "append_pattern_version() with no kb_dir must write to module KB_DIR"
    )


def test_backward_compat_safety_engine_no_kb_dir(tmp_path, monkeypatch):
    """MODULE-05: safety_engine.save_safety_state() with no kb_dir uses KB_DIR."""
    import akc_service.safety_engine as se

    fake_kb = tmp_path / "fake_global_kb"
    fake_kb.mkdir()
    monkeypatch.setattr(se, "KB_DIR", fake_kb)

    state = {"escape_hatch": None, "escape_hatch_set_at": None, "escape_hatch_reason": None}
    se.save_safety_state(state)

    assert (fake_kb / "safety_state.json").exists(), (
        "save_safety_state() with no kb_dir must write to module KB_DIR"
    )
