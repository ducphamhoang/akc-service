"""Fallback pipeline coverage for AKC learning integration (Phase 4, Wave 4).

Tests critical fallback paths:
1. test_pipeline_without_orchestrator_hooks — orchestrator_hooks module absent
2. test_pipeline_akc_disabled — AKC disabled in task_result
3. test_pipeline_patterns_file_missing — patterns.jsonl does not exist
4. test_pipeline_timeout_fallback — trigger_learning_delta raises TimeoutError
5. test_pipeline_error_fallback — trigger_learning_delta raises RuntimeError
6. test_full_chain_success_with_empty_kb — successful pattern update with empty KB

Run:
    pytest .claude/scripts/test_fallback_full_pipeline.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

# agent_learning_utils is part of the agent-system package (not akc-service)
# These tests require agent-system to be installed alongside akc-service
try:
    import agent_learning_utils as _agent_learning_utils_check
    _AGENT_SYSTEM_AVAILABLE = True
except ImportError:
    _AGENT_SYSTEM_AVAILABLE = False

agent_system_required = pytest.mark.skipif(
    not _AGENT_SYSTEM_AVAILABLE,
    reason="agent_learning_utils not available — install agent-system package"
)

import akc_service.learning_integration as learning_integration  # noqa: E402


def _make_task_result(
    task_id: str,
    status: str = "success",
    akc_enabled: bool = True,
    active_patterns: list[str] | None = None,
) -> dict:
    """Helper: build a valid task_result dict."""
    if not _AGENT_SYSTEM_AVAILABLE:
        # Fallback: build task_result directly
        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "akc_context": {
                "akc_enabled": akc_enabled,
                "knowledge_patterns_active": active_patterns or ["pat-test-01"],
            },
        }
    from agent_learning_utils import build_task_result

    return build_task_result(
        task_id=task_id,
        status=status,
        active_patterns=active_patterns or ["pat-test-01"],
        confidence_scores={"pat-test-01": 0.75} if active_patterns is None else {},
        pattern_outcomes={
            "pat-test-01": {"used": True, "success": True, "applied": True}
        } if active_patterns is None else {},
        akc_enabled=akc_enabled,
    )


# ---------------------------------------------------------------------------
# Test 1 — Pipeline without orchestrator_hooks module
# ---------------------------------------------------------------------------

@agent_system_required
class TestPipelineWithoutOrchestratorHooks:
    """Simulate orchestrator_hooks module entirely absent (ImportError)."""

    def test_pipeline_without_orchestrator_hooks(self):
        """
        Mock orchestrator_hooks module absent.
        call_learning_with_timeout() should return status "error_fallback_async"
        without raising.
        """
        import agent_learning_utils
        import builtins

        task_result = _make_task_result("no-hooks-001")

        # Mock __import__ to raise ImportError when trying to import orchestrator_hooks
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "orchestrator_hooks":
                raise ImportError("No module named 'orchestrator_hooks'")
            return original_import(name, *args, **kwargs)

        mock_proc = MagicMock()
        mock_proc.pid = 777
        mock_proc.stdin = MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = agent_learning_utils.call_learning_with_timeout(task_result)

        # Must return error_fallback_async status without raising
        assert result["status"] == "error_fallback_async", (
            f"Expected error_fallback_async, got {result['status']!r}"
        )
        assert "error" in result, "result must have an 'error' field"
        assert "pid" in result, "result must have a 'pid' field"
        assert isinstance(result["pid"], int), (
            f"pid should be int, got {type(result['pid'])}"
        )


# ---------------------------------------------------------------------------
# Test 2 — Pipeline with AKC disabled
# ---------------------------------------------------------------------------

@agent_system_required
class TestPipelineAkcDisabled:
    """Call with akc_context.akc_enabled = False."""

    def test_pipeline_akc_disabled(self):
        """
        call_learning_with_timeout with task_result where akc_enabled=False
        should return status "skipped".
        """
        import agent_learning_utils

        task_result = _make_task_result("akc-disabled-001", akc_enabled=False)

        # Mock trigger_learning_delta to return skipped for AKC-disabled task
        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            return_value={"status": "skipped", "reason": "AKC disabled"}
        )

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            result = agent_learning_utils.call_learning_with_timeout(task_result)

        assert result["status"] == "skipped", (
            f"Expected 'skipped', got {result['status']!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Pipeline with patterns.jsonl missing
# ---------------------------------------------------------------------------

class TestPipelinePatternsFileMissing:
    """patterns.jsonl does not exist; apply_confidence_delta should gracefully degrade."""

    def test_pipeline_patterns_file_missing(self, tmp_path: Path):
        """
        Patterns file missing. apply_confidence_delta should return
        {"status": "success", "patterns_updated": 0} without raising.
        """
        # learning_integration imported at module level
        # Temporarily patch KB_DIR to use tmp_path
        with patch.object(learning_integration, "KB_DIR", tmp_path):
            with patch.object(
                learning_integration,
                "PATTERNS_PATH",
                tmp_path / "patterns.jsonl"
            ):
                task_result = _make_task_result(
                    "patterns-missing-001",
                    status="success",
                    active_patterns=["pat-missing-01"],
                )

                result = learning_integration.apply_confidence_delta(task_result)

        # Must return success with 0 patterns updated (graceful degradation)
        assert result["status"] == "success", (
            f"Expected 'success', got {result['status']!r}"
        )
        assert result["patterns_updated"] == 0, (
            f"Expected 0 patterns_updated, got {result['patterns_updated']}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Pipeline timeout fallback
# ---------------------------------------------------------------------------

@agent_system_required
class TestPipelineTimeoutFallback:
    """Patch trigger_learning_delta to raise TimeoutError."""

    def test_pipeline_timeout_fallback(self):
        """
        trigger_learning_delta raises TimeoutError.
        call_learning_with_timeout should return status "timeout_fallback_async"
        without raising.
        """
        import agent_learning_utils

        task_result = _make_task_result("timeout-fb-001")

        # Mock trigger_learning_delta to raise TimeoutError
        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=TimeoutError("Learning delta timeout after 30s")
        )

        mock_proc = MagicMock()
        mock_proc.pid = 999
        mock_proc.stdin = MagicMock()

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = agent_learning_utils.call_learning_with_timeout(task_result)

        # Must return timeout_fallback_async without raising
        assert result["status"] == "timeout_fallback_async", (
            f"Expected timeout_fallback_async, got {result['status']!r}"
        )
        assert isinstance(result["pid"], int), (
            f"pid should be int, got {type(result['pid'])}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Pipeline error fallback
# ---------------------------------------------------------------------------

@agent_system_required
class TestPipelineErrorFallback:
    """Patch trigger_learning_delta to raise RuntimeError."""

    def test_pipeline_error_fallback(self):
        """
        trigger_learning_delta raises RuntimeError("test").
        call_learning_with_timeout should return status "error_fallback_async"
        without raising.
        """
        import agent_learning_utils

        task_result = _make_task_result("error-fb-001")

        # Mock trigger_learning_delta to raise RuntimeError
        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=RuntimeError("test error")
        )

        mock_proc = MagicMock()
        mock_proc.pid = 888
        mock_proc.stdin = MagicMock()

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = agent_learning_utils.call_learning_with_timeout(task_result)

        # Must return error_fallback_async without raising
        assert result["status"] == "error_fallback_async", (
            f"Expected error_fallback_async, got {result['status']!r}"
        )
        assert "error" in result, "result must have an 'error' field"
        assert "test error" in result["error"], (
            f"Expected 'test error' in error field, got {result['error']!r}"
        )
        assert isinstance(result["pid"], int), (
            f"pid should be int, got {type(result['pid'])}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Full chain success with empty KB
# ---------------------------------------------------------------------------

@agent_system_required
class TestFullChainSuccessWithEmptyKb:
    """
    Write one pattern to KB, call apply_confidence_delta with that pattern
    active and status="success", verify patterns_updated == 1 and file is updated.
    """

    def test_full_chain_success_with_empty_kb(self, tmp_path: Path):
        """
        1. Initialize KB with one pattern at confidence 0.70
        2. Call apply_confidence_delta with status="success"
        3. Assert patterns_updated == 1
        4. Assert patterns.jsonl has updated confidence (0.70 + 0.05 = 0.75)
        """
        # learning_integration imported at module level
        # Create KB directory and initialize patterns.jsonl
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        patterns_path = kb_dir / "patterns.jsonl"
        history_path = kb_dir / "confidence_history.jsonl"

        # Write one initial pattern
        initial_pattern = {
            "id": "pat-kb-001",
            "name": "Test Pattern 1",
            "confidence": 0.70,
            "confidence_tier": "production",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "version": {
                "current": "v1",
                "history": []
            }
        }

        with open(patterns_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_pattern) + "\n")

        # Patch KB_DIR and PATTERNS_PATH and CONFIDENCE_HISTORY_PATH
        with patch.object(learning_integration, "KB_DIR", kb_dir):
            with patch.object(learning_integration, "PATTERNS_PATH", patterns_path):
                with patch.object(learning_integration, "CONFIDENCE_HISTORY_PATH", history_path):
                    # Build task result with pat-kb-001 as active pattern
                    task_result = _make_task_result(
                        "success-chain-001",
                        status="success",
                        active_patterns=["pat-kb-001"],
                    )
                    # Update confidence_scores to match active patterns
                    task_result["akc_context"]["confidence_scores"] = {
                        "pat-kb-001": 0.70
                    }
                    task_result["akc_context"]["pattern_outcomes"] = {
                        "pat-kb-001": {"used": True, "success": True, "applied": True}
                    }

                    # Call apply_confidence_delta
                    result = learning_integration.apply_confidence_delta(task_result)

        # Assert success and patterns_updated == 1
        assert result["status"] == "success", (
            f"Expected 'success', got {result['status']!r}"
        )
        assert result["patterns_updated"] == 1, (
            f"Expected 1 pattern updated, got {result['patterns_updated']}"
        )

        # Read patterns.jsonl and verify confidence was updated
        with open(patterns_path, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()

        # Should have at least one line (the updated pattern)
        assert len(lines) > 0, "patterns.jsonl should have at least one line"

        # Parse the last line (most recent version)
        updated_pattern = json.loads(lines[-1])

        # Verify confidence increased by 0.05 (success delta)
        expected_confidence = 0.75  # 0.70 + 0.05
        assert updated_pattern["confidence"] == expected_confidence, (
            f"Expected confidence {expected_confidence}, got {updated_pattern['confidence']}"
        )

        # Verify tier is still production (>= 0.70)
        assert updated_pattern["confidence_tier"] == "production", (
            f"Expected tier 'production', got {updated_pattern['confidence_tier']}"
        )

        # Verify pattern ID is correct
        assert updated_pattern["id"] == "pat-kb-001", (
            f"Expected pattern id 'pat-kb-001', got {updated_pattern['id']}"
        )


# ---------------------------------------------------------------------------
# Additional integration test: All fallbacks together
# ---------------------------------------------------------------------------

@agent_system_required
class TestFallbackIntegration:
    """Verify fallback behavior across multiple error scenarios."""

    def test_async_fallback_does_not_raise(self, tmp_path: Path):
        """Verify _spawn_async_fallback never raises, returns a PID."""
        import agent_learning_utils

        task_result = _make_task_result("fallback-spawn-001")

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin = MagicMock()

        with patch("subprocess.Popen", return_value=mock_proc):
            pid = agent_learning_utils._spawn_async_fallback(task_result)

        assert isinstance(pid, int), f"Expected int PID, got {type(pid)}"
        assert pid == 12345, f"Expected PID 12345, got {pid}"

    def test_validate_task_result_missing_schema_version(self):
        """Task result missing schema_version should fail validation."""
        # learning_integration imported at module level
        bad_result = {
            "task_id": "bad-001",
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            # Missing schema_version
        }

        is_valid, error_msg = learning_integration.validate_task_result(bad_result)

        assert not is_valid, "Expected validation to fail"
        assert "schema_version" in error_msg, (
            f"Expected 'schema_version' in error message, got {error_msg}"
        )

    def test_validate_task_result_missing_required_fields(self):
        """Task result missing required fields should fail validation."""
        # learning_integration imported at module level
        bad_result = {
            "schema_version": "1.0",
            "task_id": "bad-002",
            # Missing status and timestamp
        }

        is_valid, error_msg = learning_integration.validate_task_result(bad_result)

        assert not is_valid, "Expected validation to fail"

    def test_determine_tier_boundaries(self):
        """Test confidence tier classification at boundaries."""
        # learning_integration imported at module level
        # Test all boundaries
        assert learning_integration.determine_tier(0.95) == "gold"
        assert learning_integration.determine_tier(0.85) == "gold"
        assert learning_integration.determine_tier(0.84) == "production"
        assert learning_integration.determine_tier(0.70) == "production"
        assert learning_integration.determine_tier(0.69) == "experimental"
        assert learning_integration.determine_tier(0.50) == "experimental"
        assert learning_integration.determine_tier(0.49) == "demoted"
        assert learning_integration.determine_tier(0.0) == "demoted"


# ---------------------------------------------------------------------------
# Test task_result schema compatibility
# ---------------------------------------------------------------------------

@agent_system_required
class TestTaskResultSchemaCompatibility:
    """Verify task_result schema compatibility with apply_confidence_delta."""

    def test_task_result_has_schema_version_1_0(self):
        """Task result should always have schema_version=1.0."""
        task_result = _make_task_result("schema-check-001")

        assert "schema_version" in task_result, (
            "task_result missing schema_version"
        )
        assert task_result["schema_version"] == "1.0", (
            f"Expected schema_version 1.0, got {task_result['schema_version']}"
        )

    def test_task_result_has_akc_context(self):
        """Task result should always have akc_context."""
        task_result = _make_task_result("akc-context-check-001")

        assert "akc_context" in task_result, (
            "task_result missing akc_context"
        )
        assert "akc_enabled" in task_result["akc_context"], (
            "akc_context missing akc_enabled"
        )
        assert "knowledge_patterns_active" in task_result["akc_context"], (
            "akc_context missing knowledge_patterns_active"
        )

    def test_apply_confidence_delta_with_no_active_patterns(self, tmp_path: Path):
        """apply_confidence_delta should gracefully handle empty active patterns."""
        # learning_integration imported at module level
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        patterns_path = kb_dir / "patterns.jsonl"

        # Create one pattern in KB
        initial_pattern = {
            "id": "pat-unused-01",
            "name": "Unused Pattern",
            "confidence": 0.75,
            "confidence_tier": "production",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "version": {"current": "v1", "history": []}
        }

        with open(patterns_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_pattern) + "\n")

        with patch.object(learning_integration, "KB_DIR", kb_dir):
            with patch.object(learning_integration, "PATTERNS_PATH", patterns_path):
                # Task result with NO active patterns
                task_result = _make_task_result(
                    "no-active-patterns-001",
                    status="success",
                    active_patterns=[],  # Empty!
                )

                result = learning_integration.apply_confidence_delta(task_result)

        # Should return success with 0 patterns updated
        assert result["status"] == "success", (
            f"Expected 'success', got {result['status']!r}"
        )
        assert result["patterns_updated"] == 0, (
            f"Expected 0 patterns_updated, got {result['patterns_updated']}"
        )
