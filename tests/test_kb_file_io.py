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
