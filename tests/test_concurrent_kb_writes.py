#!/usr/bin/env python3
"""
TST-01: Concurrent write test for Multi-KB Routing v0.5.
Verifies two simultaneous /record requests to different KB dirs
produce no cross-contamination of patterns.jsonl.
Run: pytest tests/test_concurrent_kb_writes.py -v
"""

import importlib
import json
import threading
from fastapi.testclient import TestClient
from unittest.mock import patch
from pathlib import Path

import pytest

import akc_service.config as _cfg
from akc_service.api.main import app


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def two_kb_client(tmp_path, monkeypatch):
    """TestClient with two KBs registered: 'kb_a' and 'kb_b'.

    Mirrors multi_kb_client from test_api_kb_routing.py but uses kb_a/kb_b naming.
    Reloads akc_service.config and akc_service.api.routes after setting
    AKC_SERVICE_KB_REGISTRY and AKC_SERVICE_ENTITY_KB_MAPPING so that both are
    parsed with the new values.
    """
    kb_a = tmp_path / "kb" / "kb_a"
    kb_b = tmp_path / "kb" / "kb_b"
    kb_a.mkdir(parents=True)
    kb_b.mkdir(parents=True)

    registry = json.dumps({
        "kb_a": str(kb_a),
        "kb_b": str(kb_b),
    })
    # Set entity mapping to point to kb_a for the wildcard
    entity_mapping = json.dumps({
        "entity:*": "kb_a",
    })
    monkeypatch.setenv("AKC_SERVICE_KB_REGISTRY", registry)
    monkeypatch.setenv("AKC_SERVICE_ENTITY_KB_MAPPING", entity_mapping)
    importlib.reload(_cfg)
    assert len(_cfg.KB_REGISTRY) == 2, (
        f"Fixture setup failed: KB_REGISTRY has {len(_cfg.KB_REGISTRY)} entries "
        f"after env var change — expected 2."
    )
    import akc_service.api.routes as _routes
    importlib.reload(_routes)

    yield TestClient(app), {"kb_a": kb_a, "kb_b": kb_b}

    monkeypatch.delenv("AKC_SERVICE_KB_REGISTRY", raising=False)
    monkeypatch.delenv("AKC_SERVICE_ENTITY_KB_MAPPING", raising=False)
    importlib.reload(_cfg)
    importlib.reload(_routes)


# ─── Helper Functions ──────────────────────────────────────────────────────


def _post_record(client, kb_name: str, task_id: str):
    """Post one /record request to a specific KB.

    Args:
        client: TestClient instance
        kb_name: KB name ("kb_a" or "kb_b")
        task_id: Task ID to use in request

    Returns:
        Response object from client.post()
    """
    return client.post("/akc/v1/record", json={
        "schema_version": "1.0",
        "task_id": task_id,
        "status": "success",
        "timestamp": "2026-05-06T10:00:00Z",
        "akc_context": {
            "knowledge_patterns_active": [
                {"id": f"pattern-{task_id}", "confidence": 0.80}
            ]
        },
        "kb": kb_name,
    })


# ─── Tests ─────────────────────────────────────────────────────────────────


class TestConcurrentWrites:
    """Concurrent write isolation tests for TST-01."""

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_concurrent_writes_no_cross_contamination(
        self, mock_delta, two_kb_client
    ):
        """TST-01: Two simultaneous /record requests to different KB dirs
        produce zero cross-contamination in patterns.jsonl.

        Uses threading.Barrier(2) to ensure both threads start simultaneously.
        Each thread writes 3 records to its KB.
        Verifies no KB-A path appears in KB-B file and vice versa.
        """
        client, paths = two_kb_client
        kb_a_path = paths["kb_a"]
        kb_b_path = paths["kb_b"]

        # Mock apply_confidence_delta to write actual sentinel data to KB dirs
        def fake_delta(result, kb_dir=None, **kwargs):
            """Write a sentinel pattern to the KB dir's patterns.jsonl."""
            if kb_dir is not None:
                task_id = result.get("task_id", "unknown")
                sentinel = {
                    "id": f"pattern-{task_id}",
                    "kb_written": str(kb_dir),
                    "status": "active",
                    "confidence": 0.80,
                }
                patterns_file = Path(kb_dir) / "patterns.jsonl"
                with open(patterns_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(sentinel) + "\n")
            return {"status": "ok", "patterns_updated": 1}

        mock_delta.side_effect = fake_delta

        # Barrier to synchronize thread starts
        barrier = threading.Barrier(2)
        errors = []

        def thread_a_worker():
            """Post 3 records to kb_a."""
            try:
                barrier.wait()  # Wait for both threads to be ready
                for i in range(3):
                    task_id = f"task-a-{i}"
                    response = _post_record(client, "kb_a", task_id)
                    assert response.status_code == 200, (
                        f"KB-A post failed: {response.status_code} - {response.text}"
                    )
            except Exception as e:
                errors.append(("thread_a", e))

        def thread_b_worker():
            """Post 3 records to kb_b."""
            try:
                barrier.wait()  # Wait for both threads to be ready
                for i in range(3):
                    task_id = f"task-b-{i}"
                    response = _post_record(client, "kb_b", task_id)
                    assert response.status_code == 200, (
                        f"KB-B post failed: {response.status_code} - {response.text}"
                    )
            except Exception as e:
                errors.append(("thread_b", e))

        # Launch both threads
        t_a = threading.Thread(target=thread_a_worker, daemon=False)
        t_b = threading.Thread(target=thread_b_worker, daemon=False)
        t_a.start()
        t_b.start()

        # Wait for both to complete
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        # Check for errors in worker threads
        assert not errors, f"Worker thread errors: {errors}"

        # Verify both threads completed
        assert not t_a.is_alive(), "Thread A did not complete"
        assert not t_b.is_alive(), "Thread B did not complete"

        # Read KB-A patterns.jsonl
        patterns_a_file = kb_a_path / "patterns.jsonl"
        assert patterns_a_file.exists(), "KB-A patterns.jsonl should exist after writes"

        lines_a = patterns_a_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines_a) == 3, f"KB-A should have 3 lines, got {len(lines_a)}"

        # Read KB-B patterns.jsonl
        patterns_b_file = kb_b_path / "patterns.jsonl"
        assert patterns_b_file.exists(), "KB-B patterns.jsonl should exist after writes"

        lines_b = patterns_b_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines_b) == 3, f"KB-B should have 3 lines, got {len(lines_b)}"

        # Parse and verify KB-A: all lines should contain kb_a path
        kb_a_path_str = str(kb_a_path)
        kb_b_path_str = str(kb_b_path)

        for i, line in enumerate(lines_a):
            entry = json.loads(line)
            kb_written = entry.get("kb_written", "")
            assert kb_a_path_str in kb_written, (
                f"KB-A line {i}: expected path containing '{kb_a_path_str}', "
                f"got '{kb_written}'"
            )
            assert kb_b_path_str not in kb_written, (
                f"KB-A line {i}: should NOT contain KB-B path '{kb_b_path_str}', "
                f"but got '{kb_written}'"
            )

        # Parse and verify KB-B: all lines should contain kb_b path
        for i, line in enumerate(lines_b):
            entry = json.loads(line)
            kb_written = entry.get("kb_written", "")
            assert kb_b_path_str in kb_written, (
                f"KB-B line {i}: expected path containing '{kb_b_path_str}', "
                f"got '{kb_written}'"
            )
            assert kb_a_path_str not in kb_written, (
                f"KB-B line {i}: should NOT contain KB-A path '{kb_a_path_str}', "
                f"but got '{kb_written}'"
            )

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_concurrent_writes_response_codes(self, mock_delta, two_kb_client):
        """Supporting test: Verify both concurrent /record requests return 200.

        Tests that response codes are consistent even under concurrent write load.
        """
        client, _ = two_kb_client

        # Mock apply_confidence_delta with minimal return
        mock_delta.return_value = {"status": "ok", "patterns_updated": 0}

        # Barrier to synchronize thread starts
        barrier = threading.Barrier(2)
        responses = []
        errors = []

        def thread_a_worker():
            """Post one record to kb_a and capture response."""
            try:
                barrier.wait()
                response = _post_record(client, "kb_a", "response-test-a")
                responses.append(("kb_a", response.status_code))
            except Exception as e:
                errors.append(("thread_a", e))

        def thread_b_worker():
            """Post one record to kb_b and capture response."""
            try:
                barrier.wait()
                response = _post_record(client, "kb_b", "response-test-b")
                responses.append(("kb_b", response.status_code))
            except Exception as e:
                errors.append(("thread_b", e))

        # Launch both threads
        t_a = threading.Thread(target=thread_a_worker, daemon=False)
        t_b = threading.Thread(target=thread_b_worker, daemon=False)
        t_a.start()
        t_b.start()

        # Wait for completion
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        # Check for errors
        assert not errors, f"Worker thread errors: {errors}"
        assert len(responses) == 2, f"Expected 2 responses, got {len(responses)}"

        # Both should return 200 OK
        for kb_name, status_code in responses:
            assert status_code == 200, (
                f"KB {kb_name} returned {status_code}, expected 200"
            )
