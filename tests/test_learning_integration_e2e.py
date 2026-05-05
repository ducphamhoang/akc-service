"""End-to-end integration tests for the 4-agent learning chain.

Proves LEARN-01..LEARN-06 across all agents (Orchestrator, MCP, Script, QC).

Tests:
    1. Full happy-path chain: all 4 agents fire call_learning_with_timeout(), each
       writes a jsonl entry to a temp confidence_history.jsonl (via mock).
    2. Timeout fallback: TimeoutExpired on trigger_learning_delta → status contains
       "timeout_fallback_async", pid is int, async Popen was called.
    3. Error fallback: RuntimeError on trigger_learning_delta → status contains
       "error_fallback_async", error field present, async Popen was called.
    4. AKC disabled no-op: akc_enabled=False → "skipped" status, no Popen.
    5. Meta test: all 4 agent prompt files contain agent_learning_utils AND
       call_learning_with_timeout (cross-cutting LEARN-01 invariant).

Run:
    pytest .claude/scripts/test_learning_integration_e2e.py -v
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# agent_learning_utils is part of the agent-system package (not akc-service)
try:
    import agent_learning_utils as _alu_check
    _AGENT_SYSTEM_AVAILABLE = True
except ImportError:
    _AGENT_SYSTEM_AVAILABLE = False

agent_system_required = pytest.mark.skipif(
    not _AGENT_SYSTEM_AVAILABLE,
    reason="agent_learning_utils not available — install agent-system package"
)

# Agent prompt files are part of the project repo, not the package
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # packages/akc-service/tests/ -> repo root
AGENT_PROMPTS = [
    _REPO_ROOT / "docs" / "agent-prompts" / "orchestrator.md",
    _REPO_ROOT / "docs" / "agent-prompts" / "mcp_agent.md",
    _REPO_ROOT / "docs" / "agent-prompts" / "script_agent.md",
    _REPO_ROOT / "docs" / "agent-prompts" / "qc_agent.md",
]


def _mock_proc() -> MagicMock:
    """Return a mock Popen process with pid=42 and writable stdin."""
    proc = MagicMock()
    proc.pid = 42
    proc.stdin = io.BytesIO()
    return proc


def _make_task_result(
    task_id: str,
    status: str = "success",
    akc_enabled: bool = True,
) -> dict:
    """Helper: build a valid task_result dict."""
    from datetime import datetime, timezone
    if _AGENT_SYSTEM_AVAILABLE:
        from agent_learning_utils import build_task_result
        return build_task_result(
            task_id=task_id,
            status=status,
            active_patterns=["pat-test-01"],
            confidence_scores={"pat-test-01": 0.75},
            pattern_outcomes={
                "pat-test-01": {"used": True, "success": True, "applied": True}
            },
            akc_enabled=akc_enabled,
        )
    # Fallback: build directly when agent-system not available
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "akc_context": {
            "akc_enabled": akc_enabled,
            "knowledge_patterns_active": ["pat-test-01"],
            "confidence_scores": {"pat-test-01": 0.75},
            "pattern_outcomes": {"pat-test-01": {"used": True, "success": True, "applied": True}},
        },
    }


# ---------------------------------------------------------------------------
# Test 1 — Full chain happy path
# ---------------------------------------------------------------------------

@agent_system_required
class TestFullChainHappyPath:
    """Simulate Orchestrator → MCP → Script → QC, each agent fires learning hook."""

    AGENT_IDS = ["orch-001", "mcp-001", "script-001", "qc-orch-001"]
    SYNC_RETURN = {"status": "sync_complete", "patterns_to_update": 1}

    def test_all_four_agents_write_jsonl_entries(self, tmp_path: Path):
        """Each agent's call_learning_with_timeout() writes one entry to confidence_history.jsonl."""
        import agent_learning_utils

        confidence_history = tmp_path / "confidence_history.jsonl"

        # Mock trigger_learning_delta to return sync_complete AND write a jsonl entry.
        def fake_trigger(task_result: dict) -> dict:
            entry = {
                "task_id": task_result["task_id"],
                "pattern_id": "pat-test-01",
                "old_confidence": 0.75,
                "new_confidence": 0.80,
                "delta": 0.05,
                "outcome": "success",
                "timestamp": task_result["timestamp"],
            }
            with confidence_history.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            return self.SYNC_RETURN

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(side_effect=fake_trigger)

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            for agent_id in self.AGENT_IDS:
                task_result = _make_task_result(task_id=agent_id)
                result = agent_learning_utils.call_learning_with_timeout(task_result)
                assert result["status"] == "sync_complete", (
                    f"Agent {agent_id}: expected sync_complete, got {result['status']!r}"
                )

        # Assert exactly 4 lines written, one per agent
        lines = confidence_history.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 4, f"Expected 4 jsonl entries, got {len(lines)}"

        written_ids = {json.loads(line)["task_id"] for line in lines}
        assert written_ids == set(self.AGENT_IDS), (
            f"task_ids mismatch: expected {self.AGENT_IDS}, got {written_ids}"
        )

    def test_jsonl_entry_schema(self, tmp_path: Path):
        """Each entry in confidence_history.jsonl has all required schema fields."""
        import agent_learning_utils

        confidence_history = tmp_path / "confidence_history.jsonl"
        required_fields = {
            "task_id", "pattern_id", "old_confidence", "new_confidence",
            "delta", "outcome", "timestamp",
        }

        def fake_trigger(task_result: dict) -> dict:
            entry = {
                "task_id": task_result["task_id"],
                "pattern_id": "pat-test-01",
                "old_confidence": 0.75,
                "new_confidence": 0.80,
                "delta": 0.05,
                "outcome": "success",
                "timestamp": task_result["timestamp"],
            }
            with confidence_history.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            return self.SYNC_RETURN

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(side_effect=fake_trigger)

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            result = agent_learning_utils.call_learning_with_timeout(
                _make_task_result("schema-check-001")
            )

        assert result["status"] == "sync_complete"
        line = confidence_history.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        missing = required_fields - entry.keys()
        assert not missing, f"Entry missing fields: {missing}"


# ---------------------------------------------------------------------------
# Test 2 — Timeout fallback
# ---------------------------------------------------------------------------

@agent_system_required
class TestTimeoutFallback:
    """Patching trigger_learning_delta to raise TimeoutExpired."""

    def test_timeout_returns_timeout_fallback_async(self):
        """TimeoutExpired → status == 'timeout_fallback_async', pid is int."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                result = agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("timeout-test-001")
                )

        assert result["status"] == "timeout_fallback_async", (
            f"Expected timeout_fallback_async, got {result['status']!r}"
        )
        assert isinstance(result["pid"], int), f"pid should be int, got {type(result['pid'])}"

    def test_timeout_spawns_async_popen(self):
        """TimeoutExpired → subprocess.Popen called with learning_integration.py --async-update."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("timeout-popen-001")
                )

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]  # argv list
        assert any("learning_integration.py" in str(a) for a in call_args), (
            f"Expected learning_integration.py in argv, got: {call_args}"
        )
        assert "--async-update" in call_args, (
            f"Expected --async-update in argv, got: {call_args}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Error fallback
# ---------------------------------------------------------------------------

@agent_system_required
class TestErrorFallback:
    """Patching trigger_learning_delta to raise RuntimeError."""

    def test_error_returns_error_fallback_async(self):
        """RuntimeError → status == 'error_fallback_async', error contains message."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=RuntimeError("KB locked")
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("error-test-001")
                )

        assert result["status"] == "error_fallback_async", (
            f"Expected error_fallback_async, got {result['status']!r}"
        )
        assert "error" in result, "result must have an 'error' field"
        assert "KB locked" in result["error"], (
            f"Expected 'KB locked' in error, got: {result['error']!r}"
        )

    def test_error_spawns_async_popen(self):
        """RuntimeError → async subprocess was spawned."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=RuntimeError("KB locked")
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("error-popen-001")
                )

        mock_popen.assert_called_once()

    def test_error_pid_is_int(self):
        """RuntimeError fallback → pid field is an int."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            side_effect=RuntimeError("KB locked")
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc):
                result = agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("error-pid-001")
                )

        assert isinstance(result["pid"], int), f"pid should be int, got {type(result['pid'])}"


# ---------------------------------------------------------------------------
# Test 4 — AKC disabled no-op
# ---------------------------------------------------------------------------

@agent_system_required
class TestAkcDisabledNoop:
    """When akc_enabled=False the call is a no-op — no async subprocess spawned."""

    def test_skipped_status_when_akc_disabled(self):
        """akc_enabled=False → trigger_learning_delta returns skipped, no Popen."""
        import agent_learning_utils

        # Use a real (non-mocked) trigger_learning_delta that returns skipped for
        # AKC-disabled task_results. We patch it to return the correct skipped dict.
        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            return_value={"status": "skipped", "reason": "AKC disabled"}
        )

        mock_proc = _mock_proc()
        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                result = agent_learning_utils.call_learning_with_timeout(
                    _make_task_result("noop-001", akc_enabled=False)
                )

        assert result["status"] == "skipped", (
            f"Expected 'skipped', got {result['status']!r}"
        )
        mock_popen.assert_not_called()

    def test_no_exception_when_akc_disabled(self):
        """akc_enabled=False → call_learning_with_timeout does not raise."""
        import agent_learning_utils

        mock_hooks = MagicMock()
        mock_hooks.trigger_learning_delta = MagicMock(
            return_value={"status": "skipped", "reason": "AKC disabled"}
        )

        with patch.dict(sys.modules, {"orchestrator_hooks": mock_hooks}):
            # Must not raise
            result = agent_learning_utils.call_learning_with_timeout(
                _make_task_result("noop-noexc-001", akc_enabled=False)
            )

        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 5 — Meta test: all 4 agent prompts reference the utility (LEARN-01)
# ---------------------------------------------------------------------------

@agent_system_required
class TestAllAgentPromptsReferenceUtility:
    """Cross-cutting invariant: all 4 agent prompts must import the shared utility."""

    @pytest.mark.parametrize("prompt_path", AGENT_PROMPTS)
    def test_agent_prompt_contains_agent_learning_utils(self, prompt_path: Path):
        """Each agent prompt file contains 'agent_learning_utils'."""
        text = prompt_path.read_text(encoding="utf-8")
        assert "agent_learning_utils" in text, (
            f"{prompt_path.name} is missing 'agent_learning_utils'"
        )

    @pytest.mark.parametrize("prompt_path", AGENT_PROMPTS)
    def test_agent_prompt_contains_call_learning_with_timeout(self, prompt_path: Path):
        """Each agent prompt file contains 'call_learning_with_timeout'."""
        text = prompt_path.read_text(encoding="utf-8")
        assert "call_learning_with_timeout" in text, (
            f"{prompt_path.name} is missing 'call_learning_with_timeout'"
        )
