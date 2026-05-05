"""
Knowledge base file I/O coverage tests.

Tests the core append-only file operations and recovery mechanisms for:
- patterns.jsonl (append_pattern_version, load_all_patterns)
- confidence_history.jsonl (log_confidence_update)

Requirements:
1. test_write_pattern_creates_file — file created with valid JSON
2. test_read_all_patterns_returns_written — write 3 patterns, read 3 back
3. test_append_is_idempotent_with_same_id — same pattern ID written twice → 2 entries
4. test_concurrent_append_no_corruption — two threads append 50 times → 100 valid lines
5. test_delete_pattern_file_recovery — file deleted, load returns [], then append recreates
6. test_partial_write_does_not_corrupt_existing — malformed line is skipped

Run:
    pytest test_kb_file_io.py -v
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import akc_service.learning_integration as learning_integration


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _monkeypatch_kb_paths(tmp_path: Path) -> None:
    """Patch learning_integration module to use temp paths."""
    patterns_path = tmp_path / "patterns.jsonl"
    confidence_history_path = tmp_path / "confidence_history.jsonl"

    # Patch the module-level constants
    learning_integration.PATTERNS_PATH = patterns_path
    learning_integration.CONFIDENCE_HISTORY_PATH = confidence_history_path


@pytest.fixture
def tmp_kb_paths(tmp_path: Path):
    """Fixture: monkeypatch KB paths to temp directory."""
    _monkeypatch_kb_paths(tmp_path)
    yield tmp_path
    # Cleanup: restore original paths (optional, but good practice)
    # Restore to package defaults
    _default_kb = Path(__file__).parent.parent / "kb"
    learning_integration.PATTERNS_PATH = _default_kb / "patterns.jsonl"
    learning_integration.CONFIDENCE_HISTORY_PATH = _default_kb / "confidence_history.jsonl"


# ─── Test Utilities ───────────────────────────────────────────────────────


def _make_pattern(pattern_id: str, confidence: float = 0.75) -> dict:
    """Create a minimal valid pattern dict matching learning_integration schema."""
    return {
        "id": pattern_id,
        "status": "active",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "confidence": confidence,
        "confidence_tier": "production",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": {
            "current": "v1",
            "history": []
        }
    }


def _load_jsonl_lines(file_path: Path) -> list[dict]:
    """Load all valid JSON lines from a .jsonl file, skipping malformed ones."""
    lines = []
    if not file_path.exists():
        return lines
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines
    return lines


# ─── Test 1: Write Pattern Creates File ───────────────────────────────────


class TestWritePatternCreatesFile:
    """Test that append_pattern_version creates file and writes valid JSON."""

    def test_write_pattern_creates_file(self, tmp_kb_paths: Path):
        """Call append_pattern_version, assert file exists and last line is valid JSON."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"
        pattern = _make_pattern("p1", confidence=0.80)

        learning_integration.append_pattern_version(pattern)

        assert patterns_file.exists(), "patterns.jsonl should exist after append_pattern_version"

        # Read file and verify last line is valid JSON matching pattern
        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

        last_entry = json.loads(lines[-1])
        assert last_entry["id"] == "p1", f"Expected id='p1', got {last_entry['id']!r}"
        assert last_entry["confidence"] == 0.80, f"Expected confidence=0.80, got {last_entry['confidence']}"

    def test_write_pattern_with_all_schema_fields(self, tmp_kb_paths: Path):
        """Written pattern preserves all schema fields."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"
        pattern = _make_pattern("p-full", confidence=0.75)

        learning_integration.append_pattern_version(pattern)

        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[0])

        # Verify required fields exist
        required = {"id", "status", "timestamp", "confidence", "confidence_tier", "updated_at", "version"}
        missing = required - entry.keys()
        assert not missing, f"Missing fields: {missing}"


# ─── Test 2: Read All Patterns Returns Written ─────────────────────────────


class TestReadAllPatternsReturnsWritten:
    """Test that load_all_patterns returns all previously written patterns."""

    def test_read_all_patterns_returns_written(self, tmp_kb_paths: Path):
        """Write 3 patterns, load_all_patterns returns exactly 3."""
        patterns = [
            _make_pattern("p1", confidence=0.70),
            _make_pattern("p2", confidence=0.80),
            _make_pattern("p3", confidence=0.60),
        ]

        for p in patterns:
            learning_integration.append_pattern_version(p)

        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 3, f"Expected 3 patterns, got {len(loaded)}"

        # Verify all IDs are present
        loaded_ids = {p["id"] for p in loaded}
        expected_ids = {"p1", "p2", "p3"}
        assert loaded_ids == expected_ids, f"Expected {expected_ids}, got {loaded_ids}"

    def test_load_preserves_field_integrity(self, tmp_kb_paths: Path):
        """load_all_patterns preserves all fields from written patterns."""
        original = _make_pattern("p-integrity", confidence=0.85)
        learning_integration.append_pattern_version(original)

        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 1

        loaded_pattern = loaded[0]
        assert loaded_pattern["id"] == original["id"]
        assert loaded_pattern["confidence"] == original["confidence"]
        assert loaded_pattern["status"] == original["status"]

    def test_load_all_patterns_empty_file(self, tmp_kb_paths: Path):
        """load_all_patterns returns [] when patterns.jsonl doesn't exist."""
        loaded = learning_integration.load_all_patterns()
        assert loaded == [], "Empty KB should return empty list"


# ─── Test 3: Append is Idempotent with Same ID ─────────────────────────────


class TestAppendIdempotent:
    """Test append-only semantics: same ID written twice → 2 entries."""

    def test_append_is_idempotent_with_same_id(self, tmp_kb_paths: Path):
        """Write pattern id=p1 twice; load_all_patterns returns 2 entries (last version wins)."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Write same pattern ID twice with different confidence
        p1_v1 = _make_pattern("p1", confidence=0.70)
        p1_v2 = _make_pattern("p1", confidence=0.85)

        learning_integration.append_pattern_version(p1_v1)
        learning_integration.append_pattern_version(p1_v2)

        # File should have exactly 2 lines
        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, f"Expected 2 lines (append-only), got {len(lines)}"

        # Load all patterns (including duplicates)
        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 2, f"Expected 2 entries in load_all_patterns, got {len(loaded)}"

        # Verify both entries are valid JSON
        for i, entry in enumerate(loaded):
            assert entry["id"] == "p1", f"Entry {i}: expected id='p1', got {entry['id']!r}"

        # Verify first has old confidence, second has new
        assert loaded[0]["confidence"] == 0.70, f"First entry: expected 0.70, got {loaded[0]['confidence']}"
        assert loaded[1]["confidence"] == 0.85, f"Second entry: expected 0.85, got {loaded[1]['confidence']}"

    def test_append_idempotent_deduplication_logic(self, tmp_kb_paths: Path):
        """Helper find_pattern_by_id returns last occurrence when duplicates exist."""
        p1_v1 = _make_pattern("p1", confidence=0.70)
        p1_v2 = _make_pattern("p1", confidence=0.85)
        p2 = _make_pattern("p2", confidence=0.60)

        learning_integration.append_pattern_version(p1_v1)
        learning_integration.append_pattern_version(p1_v2)
        learning_integration.append_pattern_version(p2)

        all_patterns = learning_integration.load_all_patterns()

        # find_pattern_by_id should return the last version (v2)
        found = learning_integration.find_pattern_by_id("p1", all_patterns)
        assert found is not None, "Pattern p1 should be found"
        assert found["confidence"] == 0.85, f"Expected last version (0.85), got {found['confidence']}"


# ─── Test 4: Concurrent Append No Corruption ──────────────────────────────


class TestConcurrentAppend:
    """Test fcntl.LOCK_EX prevents concurrent write corruption."""

    def test_concurrent_append_no_corruption(self, tmp_kb_paths: Path):
        """Two threads append 50 times each; file has exactly 100 valid JSON lines."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"
        appends_per_thread = 50
        num_threads = 2

        def append_worker(thread_id: int) -> None:
            """Append appends_per_thread patterns from this thread."""
            for i in range(appends_per_thread):
                pattern = _make_pattern(f"p-t{thread_id}-{i}", confidence=0.50 + (i * 0.01))
                learning_integration.append_pattern_version(pattern)

        # Spawn two threads
        threads = []
        for tid in range(num_threads):
            t = threading.Thread(target=append_worker, args=(tid,))
            threads.append(t)
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # Verify file has exactly 100 valid JSON lines
        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 100, f"Expected exactly 100 lines, got {len(lines)}"

        # Verify all lines are valid JSON
        valid_count = 0
        for line in lines:
            try:
                json.loads(line)
                valid_count += 1
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON on line: {line!r}. Error: {e}")

        assert valid_count == 100, f"Expected 100 valid JSON lines, got {valid_count}"

    def test_concurrent_append_with_executor(self, tmp_kb_paths: Path):
        """Concurrent append using ThreadPoolExecutor."""
        appends_per_thread = 25
        num_threads = 3

        def append_patterns(thread_id: int) -> int:
            """Append appends_per_thread patterns and return count."""
            for i in range(appends_per_thread):
                pattern = _make_pattern(f"p-tpe{thread_id}-{i}", confidence=0.50)
                learning_integration.append_pattern_version(pattern)
            return appends_per_thread

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(append_patterns, tid) for tid in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        total_appended = sum(results)
        assert total_appended == appends_per_thread * num_threads, \
            f"Expected {appends_per_thread * num_threads} appends, got {total_appended}"

        # Verify file integrity
        patterns_file = tmp_kb_paths / "patterns.jsonl"
        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == total_appended, \
            f"File should have {total_appended} lines, got {len(lines)}"


# ─── Test 5: Delete Pattern File Recovery ─────────────────────────────────


class TestDeletePatternFileRecovery:
    """Test recovery when patterns.jsonl is deleted."""

    def test_delete_pattern_file_recovery(self, tmp_kb_paths: Path):
        """Delete patterns.jsonl; load_all_patterns returns [] without error."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # First, create and write a pattern
        p1 = _make_pattern("p1", confidence=0.75)
        learning_integration.append_pattern_version(p1)
        assert patterns_file.exists(), "File should exist after first write"

        # Delete the file
        patterns_file.unlink()
        assert not patterns_file.exists(), "File should be deleted"

        # Load should return empty list without error
        loaded = learning_integration.load_all_patterns()
        assert loaded == [], f"Expected empty list after deletion, got {loaded}"

    def test_delete_and_recreate_file(self, tmp_kb_paths: Path):
        """After deletion, append should recreate file."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Write, delete, then append again
        learning_integration.append_pattern_version(_make_pattern("p1", confidence=0.70))
        patterns_file.unlink()
        assert not patterns_file.exists()

        # Now append again — file should be recreated
        learning_integration.append_pattern_version(_make_pattern("p2", confidence=0.80))
        assert patterns_file.exists(), "File should be recreated by append_pattern_version"

        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 1, f"Expected 1 pattern (p2), got {len(loaded)}"
        assert loaded[0]["id"] == "p2"

    def test_recovery_maintains_append_only_semantics(self, tmp_kb_paths: Path):
        """After recovery, file is still append-only."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Initial write
        learning_integration.append_pattern_version(_make_pattern("p1", confidence=0.70))

        # Delete
        patterns_file.unlink()

        # Recover
        learning_integration.append_pattern_version(_make_pattern("p2", confidence=0.80))
        learning_integration.append_pattern_version(_make_pattern("p3", confidence=0.90))

        lines = patterns_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, f"Expected 2 lines (p2, p3), got {len(lines)}"

        loaded = learning_integration.load_all_patterns()
        loaded_ids = [p["id"] for p in loaded]
        assert loaded_ids == ["p2", "p3"], f"Expected ['p2', 'p3'], got {loaded_ids}"


# ─── Test 6: Partial Write Does Not Corrupt Existing ──────────────────────


class TestPartialWriteRecovery:
    """Test that malformed lines are skipped, valid ones are retained."""

    def test_partial_write_does_not_corrupt_existing(self, tmp_kb_paths: Path):
        """Pre-populate file; truncate last line; load_all_patterns skips malformed, returns valid."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Pre-populate with 3 valid patterns
        p1 = _make_pattern("p1", confidence=0.70)
        p2 = _make_pattern("p2", confidence=0.75)
        p3 = _make_pattern("p3", confidence=0.80)

        learning_integration.append_pattern_version(p1)
        learning_integration.append_pattern_version(p2)
        learning_integration.append_pattern_version(p3)

        # Simulate interrupt: truncate last line to make it invalid JSON
        content = patterns_file.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        # Keep first 2 lines, truncate the last to be incomplete JSON
        truncated = "".join(lines[:-1]) + "{"
        patterns_file.write_text(truncated, encoding="utf-8")

        # Load should skip malformed line, return 2 valid entries
        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 2, f"Expected 2 valid patterns (skipped malformed), got {len(loaded)}"

        loaded_ids = [p["id"] for p in loaded]
        assert loaded_ids == ["p1", "p2"], f"Expected ['p1', 'p2'], got {loaded_ids}"

    def test_multiple_malformed_lines_skipped(self, tmp_kb_paths: Path):
        """Multiple malformed lines are all skipped."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Write mixed valid and malformed lines manually
        patterns_file.write_text(
            json.dumps(_make_pattern("p1", confidence=0.70)) + "\n" +
            "this is not json\n" +
            json.dumps(_make_pattern("p2", confidence=0.75)) + "\n" +
            "{ incomplete json\n" +
            json.dumps(_make_pattern("p3", confidence=0.80)) + "\n",
            encoding="utf-8"
        )

        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 3, f"Expected 3 valid entries, got {len(loaded)}"

        loaded_ids = [p["id"] for p in loaded]
        assert loaded_ids == ["p1", "p2", "p3"], f"Expected ['p1', 'p2', 'p3'], got {loaded_ids}"

    def test_empty_lines_ignored(self, tmp_kb_paths: Path):
        """Empty lines in file are ignored."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"

        # Write with blank lines
        patterns_file.write_text(
            json.dumps(_make_pattern("p1", confidence=0.70)) + "\n" +
            "\n" +
            json.dumps(_make_pattern("p2", confidence=0.75)) + "\n" +
            "\n\n" +
            json.dumps(_make_pattern("p3", confidence=0.80)) + "\n",
            encoding="utf-8"
        )

        loaded = learning_integration.load_all_patterns()
        assert len(loaded) == 3, f"Expected 3 patterns (blanks ignored), got {len(loaded)}"


# ─── Test 7: Confidence History File I/O ──────────────────────────────────


class TestConfidenceHistoryFileIo:
    """Test log_confidence_update file I/O."""

    def test_log_confidence_update_creates_file(self, tmp_kb_paths: Path):
        """Call log_confidence_update, assert file is created with valid JSON."""
        confidence_history_file = tmp_kb_paths / "confidence_history.jsonl"

        entry = {
            "history_id": "ch-2026-05-04T120000",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pattern_id": "p1",
            "old_confidence": 0.70,
            "new_confidence": 0.75,
            "confidence_delta": 0.05,
            "task_id": "task-001",
            "task_status": "success",
            "tier_change": "experimental → experimental"
        }

        learning_integration.log_confidence_update(entry)

        assert confidence_history_file.exists(), "confidence_history.jsonl should exist"

        lines = confidence_history_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        logged = json.loads(lines[0])
        assert logged["pattern_id"] == "p1"
        assert logged["confidence_delta"] == 0.05

    def test_log_confidence_update_append_only(self, tmp_kb_paths: Path):
        """Multiple calls to log_confidence_update append, don't overwrite."""
        confidence_history_file = tmp_kb_paths / "confidence_history.jsonl"

        for i in range(3):
            entry = {
                "history_id": f"ch-test-{i}",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pattern_id": f"p{i}",
                "old_confidence": 0.50,
                "new_confidence": 0.55,
                "confidence_delta": 0.05,
                "task_id": f"task-{i}",
                "task_status": "success",
                "tier_change": "none"
            }
            learning_integration.log_confidence_update(entry)

        lines = confidence_history_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3, f"Expected 3 history entries, got {len(lines)}"


# ─── Integration Tests ────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests combining multiple file I/O operations."""

    def test_full_workflow_write_read_append(self, tmp_kb_paths: Path):
        """Full workflow: write patterns, read, append more, read again."""
        # Initial write
        p1 = _make_pattern("p1", confidence=0.70)
        p2 = _make_pattern("p2", confidence=0.75)
        learning_integration.append_pattern_version(p1)
        learning_integration.append_pattern_version(p2)

        # First read
        loaded1 = learning_integration.load_all_patterns()
        assert len(loaded1) == 2

        # Append more
        p3 = _make_pattern("p3", confidence=0.80)
        learning_integration.append_pattern_version(p3)

        # Second read
        loaded2 = learning_integration.load_all_patterns()
        assert len(loaded2) == 3

        ids = [p["id"] for p in loaded2]
        assert ids == ["p1", "p2", "p3"]

    def test_patterns_and_history_independent(self, tmp_kb_paths: Path):
        """patterns.jsonl and confidence_history.jsonl are independent."""
        patterns_file = tmp_kb_paths / "patterns.jsonl"
        history_file = tmp_kb_paths / "confidence_history.jsonl"

        # Write a pattern
        learning_integration.append_pattern_version(_make_pattern("p1", confidence=0.70))
        assert patterns_file.exists()
        assert not history_file.exists()

        # Log a history entry
        entry = {
            "history_id": "ch-001",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pattern_id": "p1",
            "old_confidence": 0.70,
            "new_confidence": 0.75,
            "confidence_delta": 0.05,
            "task_id": "task-001",
            "task_status": "success",
            "tier_change": "none"
        }
        learning_integration.log_confidence_update(entry)

        assert patterns_file.exists()
        assert history_file.exists()

        # Both files are independent
        patterns = learning_integration.load_all_patterns()
        history_lines = history_file.read_text(encoding="utf-8").strip().splitlines()

        assert len(patterns) == 1
        assert len(history_lines) == 1


# ─── Test 8: Pattern Index & Determinism (Tech-M7) ────────────────────────


class TestPatternIndexDeterminism:
    """
    Tests for build_pattern_index(), get_deduped_patterns(), and
    invalidate_pattern_index() — the fix for TIER 2 issue 10.

    Verifies that get_deduped_patterns() and get_active_patterns() always
    return the same result regardless of how many times they are called,
    even when the same pattern ID has been written multiple times
    (append-only versioning means duplicates accumulate over time).
    """

    @pytest.fixture(autouse=True)
    def no_quarantine(self):
        """Ensure the safety engine never blocks writes during these tests."""
        with patch(
            "akc_service.safety_engine.load_safety_state",
            return_value={"escape_hatch": "none"}
        ):
            yield

    def _reset_index(self):
        """Force index rebuild so tests start with a clean slate."""
        learning_integration.invalidate_pattern_index()

    def test_build_pattern_index_deduplicates_last_occurrence_wins(self, tmp_kb_paths: Path):
        """build_pattern_index keeps the LAST entry for each ID."""
        self._reset_index()

        p_v1 = _make_pattern("dup-001", confidence=0.60)
        p_v2 = _make_pattern("dup-001", confidence=0.80)
        p_v3 = _make_pattern("dup-001", confidence=0.75)

        for p in (p_v1, p_v2, p_v3):
            learning_integration.append_pattern_version(p)

        index = learning_integration.build_pattern_index()

        assert len(index) == 1, f"Expected 1 unique ID, got {len(index)}"
        assert "dup-001" in index
        # Last written version (v3, confidence=0.75) must win
        assert index["dup-001"]["confidence"] == 0.75, (
            f"Expected 0.75 (last-occurrence-wins), got {index['dup-001']['confidence']}"
        )

    def test_build_pattern_index_multiple_ids(self, tmp_kb_paths: Path):
        """build_pattern_index returns one entry per unique ID."""
        self._reset_index()

        learning_integration.append_pattern_version(_make_pattern("a-001", confidence=0.70))
        learning_integration.append_pattern_version(_make_pattern("b-002", confidence=0.80))
        learning_integration.append_pattern_version(_make_pattern("a-001", confidence=0.85))  # v2
        learning_integration.append_pattern_version(_make_pattern("c-003", confidence=0.60))

        index = learning_integration.build_pattern_index()

        assert set(index.keys()) == {"a-001", "b-002", "c-003"}
        assert index["a-001"]["confidence"] == 0.85, "a-001 should use its v2 confidence"

    def test_get_deduped_patterns_sorted_by_id(self, tmp_kb_paths: Path):
        """get_deduped_patterns returns patterns sorted alphabetically by ID."""
        self._reset_index()

        learning_integration.append_pattern_version(_make_pattern("z-last", confidence=0.70))
        learning_integration.append_pattern_version(_make_pattern("a-first", confidence=0.80))
        learning_integration.append_pattern_version(_make_pattern("m-middle", confidence=0.75))

        patterns = learning_integration.get_deduped_patterns()
        ids = [p["id"] for p in patterns]

        assert ids == sorted(ids), f"Expected sorted IDs, got {ids}"
        assert ids == ["a-first", "m-middle", "z-last"]

    def test_get_deduped_patterns_deterministic_across_5_calls(self, tmp_kb_paths: Path):
        """
        Core determinism test: same query returns identical list on all 5 calls.

        Simulates the scenario described in TIER 2 issue 10 — a pattern ID that
        appears multiple times in patterns.jsonl due to append-only versioning.
        """
        self._reset_index()

        # Write pattern "det-001" five times (5 versions accumulate)
        for i in range(1, 6):
            p = _make_pattern("det-001", confidence=0.50 + i * 0.05)
            learning_integration.append_pattern_version(p)

        # Also write two other patterns
        learning_integration.append_pattern_version(_make_pattern("det-002", confidence=0.72))
        learning_integration.append_pattern_version(_make_pattern("det-003", confidence=0.88))

        # Call get_deduped_patterns 5 times and collect results
        results = [learning_integration.get_deduped_patterns() for _ in range(5)]

        # All 5 results must be identical
        assert all(r == results[0] for r in results), (
            "get_deduped_patterns() returned different results across calls — not deterministic"
        )

        # Only 3 unique patterns (not 7)
        assert len(results[0]) == 3, f"Expected 3 deduplicated patterns, got {len(results[0])}"

        # det-001 must carry the LAST confidence (v5 = 0.50 + 5*0.05 = 0.75)
        det001 = next(p for p in results[0] if p["id"] == "det-001")
        assert det001["confidence"] == 0.75, (
            f"Expected last version confidence 0.75, got {det001['confidence']}"
        )

    def test_invalidate_pattern_index_forces_rebuild(self, tmp_kb_paths: Path):
        """After invalidation, the next call rebuilds from disk and sees new data."""
        self._reset_index()

        learning_integration.append_pattern_version(_make_pattern("inv-001", confidence=0.60))

        # Warm the cache
        _ = learning_integration.get_deduped_patterns()

        # Manually write a second version directly (bypassing append_pattern_version
        # to avoid auto-invalidation — tests the staleness-check path)
        import json as _json
        new_version = _make_pattern("inv-001", confidence=0.90)
        with open(learning_integration.PATTERNS_PATH, "a", encoding="utf-8") as f:
            f.write(_json.dumps(new_version) + "\n")

        # Without invalidation, index may be stale (mtime check covers this, but
        # explicit invalidation guarantees a rebuild)
        learning_integration.invalidate_pattern_index()

        patterns = learning_integration.get_deduped_patterns()
        inv001 = next(p for p in patterns if p["id"] == "inv-001")
        assert inv001["confidence"] == 0.90, (
            f"After invalidation, expected refreshed confidence 0.90, got {inv001['confidence']}"
        )

    def test_append_pattern_version_invalidates_cache(self, tmp_kb_paths: Path):
        """append_pattern_version automatically invalidates the index cache."""
        self._reset_index()

        learning_integration.append_pattern_version(_make_pattern("auto-001", confidence=0.60))

        # Warm cache
        patterns_before = learning_integration.get_deduped_patterns()
        assert any(p["id"] == "auto-001" and p["confidence"] == 0.60 for p in patterns_before)

        # Write a new version — must auto-invalidate
        learning_integration.append_pattern_version(_make_pattern("auto-001", confidence=0.90))

        patterns_after = learning_integration.get_deduped_patterns()
        auto001 = next(p for p in patterns_after if p["id"] == "auto-001")
        assert auto001["confidence"] == 0.90, (
            f"Cache should be invalidated after append; expected 0.90, got {auto001['confidence']}"
        )

    def test_get_active_patterns_deterministic_with_duplicates(self, tmp_kb_paths: Path):
        """
        get_active_patterns() returns identical ordered list across 5 calls,
        even with 5 versions of the same pattern ID in patterns.jsonl.
        """
        from akc_service.api.routes import get_active_patterns

        self._reset_index()

        # Add the same entity/component pattern 5 times with varying confidence
        for i in range(1, 6):
            p = _make_pattern("route-001", confidence=0.50 + i * 0.05)
            p["entity"] = "player"
            p["component"] = "HealthComponent"
            learning_integration.append_pattern_version(p)

        # Add a second pattern for the same entity/component
        p2 = _make_pattern("route-002", confidence=0.90)
        p2["entity"] = "player"
        p2["component"] = "HealthComponent"
        learning_integration.append_pattern_version(p2)

        # Call 5 times
        results = [get_active_patterns("player", "HealthComponent") for _ in range(5)]

        assert all(r == results[0] for r in results), (
            "get_active_patterns() is non-deterministic across repeated calls"
        )

        # Exactly 2 patterns (one per unique ID, demoted ones excluded)
        assert len(results[0]) == 2, f"Expected 2 patterns, got {len(results[0])}"

        # Sorted by confidence descending; ties broken by ID ascending
        confidences = [p["confidence"] for p in results[0]]
        assert confidences == sorted(confidences, reverse=True), (
            "Results must be sorted by confidence descending"
        )

        # route-001 last version: 0.50 + 5*0.05 = 0.75
        # route-002: 0.90
        # Expected order: route-002 first, route-001 second
        assert results[0][0]["id"] == "route-002"
        assert results[0][1]["id"] == "route-001"

    def test_get_deduped_patterns_empty_kb(self, tmp_kb_paths: Path):
        """get_deduped_patterns returns [] when patterns.jsonl is missing."""
        self._reset_index()
        # tmp_kb_paths has no patterns.jsonl yet
        patterns = learning_integration.get_deduped_patterns()
        assert patterns == [], f"Expected [] for empty KB, got {patterns}"

    def test_build_pattern_index_skips_malformed_lines(self, tmp_kb_paths: Path):
        """build_pattern_index skips lines that are not valid JSON."""
        self._reset_index()

        learning_integration.PATTERNS_PATH.write_text(
            '{"id": "ok-001", "confidence": 0.80}\n'
            'this is not json\n'
            '{"id": "ok-002", "confidence": 0.70}\n',
            encoding="utf-8"
        )

        index = learning_integration.build_pattern_index()
        assert set(index.keys()) == {"ok-001", "ok-002"}, (
            f"Should contain ok-001 and ok-002, got {set(index.keys())}"
        )


# ─── Test 9: Advisory Read Lock (TIER 3 Data-M6) ──────────────────────────


class TestAdvisoryReadLock:
    """
    Tests for the LOCK_SH | LOCK_NB advisory lock added to load_all_patterns()
    and build_pattern_index() (TIER 3, Data-M6).

    Coverage:
    1. Normal read succeeds with no contention.
    2. While file is exclusively write-locked, load_all_patterns raises BlockingIOError.
    3. While file is exclusively write-locked, build_pattern_index raises BlockingIOError.
    4. Multiple concurrent readers acquire shared locks without blocking each other.
    5. After the write lock is released, a subsequent read succeeds.
    """

    @pytest.fixture(autouse=True)
    def no_quarantine(self):
        """Ensure the safety engine never blocks writes during these tests."""
        with patch(
            "akc_service.safety_engine.load_safety_state",
            return_value={"escape_hatch": "none"}
        ):
            yield

    def test_read_succeeds_when_no_contention(self, tmp_kb_paths: Path):
        """load_all_patterns and build_pattern_index work normally with no lock held."""
        p1 = _make_pattern("lock-001", confidence=0.75)
        learning_integration.append_pattern_version(p1)

        patterns = learning_integration.load_all_patterns()
        assert len(patterns) == 1
        assert patterns[0]["id"] == "lock-001"

        index = learning_integration.build_pattern_index()
        assert "lock-001" in index

    def test_load_all_patterns_raises_when_write_locked(self, tmp_kb_paths: Path):
        """
        load_all_patterns raises BlockingIOError when patterns.jsonl is write-locked.

        Simulates a writer holding LOCK_EX by acquiring it in the main thread,
        then verifying that load_all_patterns() exhausts its retries and raises.
        """
        import fcntl as _fcntl

        # Seed the file so it exists
        p = _make_pattern("lock-read-001", confidence=0.70)
        learning_integration.append_pattern_version(p)

        # Hold an exclusive write lock from another thread
        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_write_lock():
            with open(learning_integration.PATTERNS_PATH, "a", encoding="utf-8") as lf:
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
                lock_acquired.set()
                # Hold the lock until the main thread signals release
                release_lock.wait(timeout=5.0)
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)

        writer = threading.Thread(target=hold_write_lock, daemon=True)
        writer.start()
        lock_acquired.wait(timeout=2.0)

        try:
            with pytest.raises(BlockingIOError, match="write-locked"):
                learning_integration.load_all_patterns()
        finally:
            release_lock.set()
            writer.join(timeout=2.0)

    def test_build_pattern_index_raises_when_write_locked(self, tmp_kb_paths: Path):
        """
        build_pattern_index raises BlockingIOError when patterns.jsonl is write-locked.
        """
        import fcntl as _fcntl

        p = _make_pattern("lock-index-001", confidence=0.80)
        learning_integration.append_pattern_version(p)

        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_write_lock():
            with open(learning_integration.PATTERNS_PATH, "a", encoding="utf-8") as lf:
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
                lock_acquired.set()
                release_lock.wait(timeout=5.0)
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)

        writer = threading.Thread(target=hold_write_lock, daemon=True)
        writer.start()
        lock_acquired.wait(timeout=2.0)

        try:
            with pytest.raises(BlockingIOError, match="write-locked"):
                learning_integration.build_pattern_index()
        finally:
            release_lock.set()
            writer.join(timeout=2.0)

    def test_concurrent_readers_do_not_block_each_other(self, tmp_kb_paths: Path):
        """
        Multiple threads reading concurrently all succeed — shared locks are compatible.
        """
        # Seed with some patterns
        for i in range(5):
            learning_integration.append_pattern_version(
                _make_pattern(f"cr-{i:03d}", confidence=0.50 + i * 0.05)
            )

        results: list[list] = []
        errors: list[Exception] = []

        def reader():
            try:
                patterns = learning_integration.load_all_patterns()
                results.append(patterns)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"Concurrent readers raised: {errors}"
        assert len(results) == 8, f"Expected 8 successful reads, got {len(results)}"
        assert all(len(r) == 5 for r in results), "Each reader should see all 5 patterns"

    def test_read_succeeds_after_write_lock_released(self, tmp_kb_paths: Path):
        """
        After a write lock is released, load_all_patterns returns the file contents.
        """
        import fcntl as _fcntl

        # Pre-seed file
        learning_integration.append_pattern_version(
            _make_pattern("post-lock-001", confidence=0.72)
        )

        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_write_lock():
            with open(learning_integration.PATTERNS_PATH, "a", encoding="utf-8") as lf:
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_EX)
                lock_acquired.set()
                release_lock.wait(timeout=5.0)
                _fcntl.flock(lf.fileno(), _fcntl.LOCK_UN)

        writer = threading.Thread(target=hold_write_lock, daemon=True)
        writer.start()
        lock_acquired.wait(timeout=2.0)

        # While locked, reads should fail
        with pytest.raises(BlockingIOError):
            learning_integration.load_all_patterns()

        # Release the lock
        release_lock.set()
        writer.join(timeout=2.0)

        # After release, reads must succeed
        patterns = learning_integration.load_all_patterns()
        assert len(patterns) == 1
        assert patterns[0]["id"] == "post-lock-001"
