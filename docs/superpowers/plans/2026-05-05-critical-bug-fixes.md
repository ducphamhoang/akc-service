# akc-service Critical Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 critical bugs that prevent akc-service from operating correctly as a standalone service, including the dead learning loop, wrong SLA threshold, broken standalone mode, missing env-var wiring, and stale documentation.

**Architecture:** All fixes are surgical edits to existing files — no new modules except `akc_service/config.py` for centralising env-var reading. Each fix is independently deployable and tested.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, httpx (TestClient)

---

## File Map

| Action | File | What changes |
|---|---|---|
| Modify | `akc_service/api/routes.py` | Wire `/record` to `BackgroundTasks`; fix `/fix` empty-KB 404 |
| Modify | `akc_service/learning_integration.py` | Fix SLA threshold (300 000ms → 50ms); fix latency file name |
| Create | `akc_service/config.py` | Central env-var reader (KB_DIR, SAFETY_LEVEL, URL, LOG_LEVEL) |
| Modify | `akc_service/api/main.py` | Read `AKC_SERVICE_LOG_LEVEL` at startup |
| Modify | `akc_service/adapters/godot/godot_akc_adapter.py` | Read `AKC_SERVICE_URL` env var as default |
| Modify | `docs/CAPABILITIES.md` | Fix method names: `record_lint_results` → `record_lint_result`, `record_test_results` → `record_test_result` |
| Modify | `tests/test_akc_api_endpoints.py` | Add tests for record dispatch, empty-KB fix, SLA |
| Create | `tests/test_config.py` | Tests for the new config module |

---

## Task 1: Central Config Module

**Files:**
- Create: `akc_service/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1.1: Write failing tests for config module**

```python
# tests/test_config.py
import os
import pytest
from pathlib import Path


def test_safety_level_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_SAFETY_LEVEL", raising=False)
    # Re-import to pick up env change
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.SAFETY_LEVEL == 1


def test_safety_level_from_env(monkeypatch):
    monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", "2")
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.SAFETY_LEVEL == 2


def test_safety_level_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AKC_SERVICE_SAFETY_LEVEL", "banana")
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.SAFETY_LEVEL == 1


def test_akc_url_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_URL", raising=False)
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.AKC_URL == "http://localhost:8000"


def test_akc_url_from_env(monkeypatch):
    monkeypatch.setenv("AKC_SERVICE_URL", "http://remote:9000")
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.AKC_URL == "http://remote:9000"


def test_log_level_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_LOG_LEVEL", raising=False)
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.LOG_LEVEL == "INFO"


def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("AKC_SERVICE_LOG_LEVEL", "DEBUG")
    import importlib
    import akc_service.config as cfg
    importlib.reload(cfg)
    assert cfg.LOG_LEVEL == "DEBUG"


def test_max_delta_for_safety_level_0():
    from akc_service.config import max_delta_for_level
    assert max_delta_for_level(0) == 0.25


def test_max_delta_for_safety_level_1():
    from akc_service.config import max_delta_for_level
    assert max_delta_for_level(1) == 0.15


def test_max_delta_for_safety_level_2():
    from akc_service.config import max_delta_for_level
    assert max_delta_for_level(2) == 0.10
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd /Users/ducph/godot/my-demon/packages/akc-service
python -m pytest tests/test_config.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'akc_service.config'`

- [ ] **Step 1.3: Create `akc_service/config.py`**

```python
# akc_service/config.py
import logging
import os
from pathlib import Path

_DEFAULT_KB_DIR = Path(__file__).parent / "kb"

KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))

try:
    SAFETY_LEVEL = int(os.environ.get("AKC_SERVICE_SAFETY_LEVEL", "1"))
    if SAFETY_LEVEL not in (0, 1, 2):
        SAFETY_LEVEL = 1
except ValueError:
    SAFETY_LEVEL = 1

AKC_URL = os.environ.get("AKC_SERVICE_URL", "http://localhost:8000")

LOG_LEVEL = os.environ.get("AKC_SERVICE_LOG_LEVEL", "INFO").upper()

_DELTA_CAPS = {0: 0.25, 1: 0.15, 2: 0.10}


def max_delta_for_level(level: int) -> float:
    """Return the maximum allowed confidence delta for a safety level."""
    return _DELTA_CAPS.get(level, 0.15)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 1.5: Commit**

```bash
git add akc_service/config.py tests/test_config.py
git commit -m "feat: add central config module for env-var reading"
```

---

## Task 2: Fix SLA Threshold (300 000ms → 50ms) and Latency File Name

**Files:**
- Modify: `akc_service/learning_integration.py:697` (sla_threshold_ms)
- Modify: `akc_service/latency_monitor.py:42` (file name `latency_history.jsonl` → `latency_samples.jsonl`)

- [ ] **Step 2.1: Write failing test**

Add to `tests/test_akc_api_endpoints.py` inside `TestStatsEndpoint` (or create a new class):

```python
# tests/test_akc_api_endpoints.py  — add to existing file
class TestSLAThreshold:
    """Verify check_latency uses 50ms SLA budget, not 5-minute."""

    def test_sla_warning_when_latency_exceeds_50ms(self, tmp_path, monkeypatch):
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)

        # Write two latency entries: one at 30ms (ok), one at 80ms (over 50ms SLA)
        latency_file = tmp_path / "confidence_history.jsonl"
        import json
        latency_file.write_text(
            json.dumps({"latency_ms": 30}) + "\n" +
            json.dumps({"latency_ms": 80}) + "\n"
        )

        result = li.check_latency()
        assert result["sla_status"] == "WARNING", (
            f"Expected WARNING when a sample exceeds 50ms, got {result['sla_status']}"
        )

    def test_sla_healthy_when_all_latency_under_50ms(self, tmp_path, monkeypatch):
        from akc_service import learning_integration as li
        monkeypatch.setattr(li, "KB_DIR", tmp_path)

        latency_file = tmp_path / "confidence_history.jsonl"
        import json
        latency_file.write_text(
            json.dumps({"latency_ms": 10}) + "\n" +
            json.dumps({"latency_ms": 40}) + "\n"
        )

        result = li.check_latency()
        assert result["sla_status"] == "HEALTHY"
```

- [ ] **Step 2.2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestSLAThreshold -v
```

Expected: `FAILED — assert 'HEALTHY' == 'WARNING'` (because threshold is currently 300 000ms)

- [ ] **Step 2.3: Fix the SLA threshold in `learning_integration.py`**

Open `akc_service/learning_integration.py` and change line 697:

```python
# OLD:
sla_threshold_ms = 300000

# NEW:
sla_threshold_ms = 50
```

- [ ] **Step 2.4: Fix latency file name in `latency_monitor.py`**

Open `akc_service/latency_monitor.py` and change the file reference at line 42 from `latency_history.jsonl` to `latency_samples.jsonl`:

```python
# OLD (line ~42):
LATENCY_FILE = KB_DIR / "latency_history.jsonl"

# NEW:
LATENCY_FILE = KB_DIR / "latency_samples.jsonl"
```

- [ ] **Step 2.5: Run tests to verify they pass**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestSLAThreshold -v
```

Expected: both SLA tests PASS

- [ ] **Step 2.6: Commit**

```bash
git add akc_service/learning_integration.py akc_service/latency_monitor.py tests/test_akc_api_endpoints.py
git commit -m "fix: correct SLA threshold to 50ms and latency file name to latency_samples.jsonl"
```

---

## Task 3: Wire `/record` to Background Learning Loop

**Files:**
- Modify: `akc_service/api/routes.py` (add `BackgroundTasks` parameter, call `apply_confidence_delta`)
- Modify: `tests/test_akc_api_endpoints.py` (add test verifying delta is called)

- [ ] **Step 3.1: Write failing test**

Add to `tests/test_akc_api_endpoints.py`:

```python
class TestRecordDispatchesLearning:
    """Verify /record actually dispatches confidence delta updates."""

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_dispatches_background_delta(self, mock_delta, client):
        """Posting to /record must enqueue apply_confidence_delta as background task."""
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-001",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["pattern_001", "pattern_002"]
            }
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 202
        # Background task must have been enqueued
        mock_delta.assert_called_once()
        call_arg = mock_delta.call_args[0][0]
        assert call_arg["task_id"] == "t-learning-001"
        assert call_arg["status"] == "success"

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_failed_status_dispatches_delta(self, mock_delta, client):
        """Failed task status also dispatches apply_confidence_delta."""
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-002",
            "status": "failed",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": ["pattern_001"]
            }
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 202
        mock_delta.assert_called_once()

    @patch("akc_service.api.routes.apply_confidence_delta")
    def test_record_no_active_patterns_still_returns_202(self, mock_delta, client):
        """Empty pattern list returns 202 and still dispatches (delta handles no-op)."""
        payload = {
            "schema_version": "1.0",
            "task_id": "t-learning-003",
            "status": "success",
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {"akc_enabled": True, "knowledge_patterns_active": []}
        }
        response = client.post("/akc/v1/record", json=payload)
        assert response.status_code == 202
        mock_delta.assert_called_once()
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestRecordDispatchesLearning -v
```

Expected: `FAILED — assert mock_delta.call_count == 1` (currently 0 — the no-op)

- [ ] **Step 3.3: Wire `apply_confidence_delta` into the `/record` endpoint**

In `akc_service/api/routes.py`, make two changes:

**Change 1** — Add import at the top (with the other `learning_integration` imports):

```python
from akc_service.learning_integration import (
    load_all_patterns,
    find_pattern_by_id,
    append_pattern_version,
    log_confidence_update,
    check_latency,
    now_iso,
    determine_tier,
    apply_confidence_delta,        # ← add this line
)
```

**Change 2** — Add `BackgroundTasks` to the endpoint signature and dispatch the task. Replace the current `record_task_outcome` signature and the comment block:

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

@router.post("/record", status_code=status.HTTP_202_ACCEPTED)
async def record_task_outcome(
    request: RecordRequest,
    background_tasks: BackgroundTasks,
) -> RecordResponse:
    # ... (keep all existing validation unchanged) ...

    # Replace the comment "# Log outcome recording (actual KB update happens asynchronously)"
    # with the actual dispatch:

    task_result = {
        "schema_version": request.schema_version,
        "task_id": request.task_id,
        "status": request.status,
        "timestamp": request.timestamp,
        "akc_context": request.akc_context,
    }

    background_tasks.add_task(apply_confidence_delta, task_result)

    logger.info(
        f"record_task_outcome: dispatched background delta task={request.task_id}, "
        f"patterns_to_update={patterns_to_update}"
    )

    return RecordResponse(
        accepted=True,
        task_id=request.task_id,
        update_mode=update_mode,
        patterns_to_update=patterns_to_update,
        timestamp=now_iso(),
    )
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestRecordDispatchesLearning -v
```

Expected: all 3 tests PASS

- [ ] **Step 3.5: Run full test suite to verify no regressions**

```bash
python -m pytest tests/ -v
```

Expected: all existing tests still pass

- [ ] **Step 3.6: Commit**

```bash
git add akc_service/api/routes.py tests/test_akc_api_endpoints.py
git commit -m "fix: wire /record endpoint to apply_confidence_delta via BackgroundTasks"
```

---

## Task 4: Fix `/fix` Returning 404 on Empty KB

**Files:**
- Modify: `akc_service/api/routes.py` (change 404 to empty-list 200 when KB absent)
- Modify: `tests/test_akc_api_endpoints.py` (add test for empty KB)

- [ ] **Step 4.1: Write failing test**

Add to `tests/test_akc_api_endpoints.py`:

```python
class TestFixEndpointEmptyKB:
    """Verify /fix returns empty list (not 404) when KB has no patterns."""

    @patch("akc_service.api.routes.load_all_patterns", return_value=[])
    def test_fix_returns_empty_list_when_kb_empty(self, mock_load, client):
        """Empty KB should yield 200 with empty fixes list, not 404."""
        payload = {"signature_hash": "abc123", "category": "implementation"}
        response = client.post("/akc/v1/fix", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["fixes"] == []
        assert data["count"] == 0
        assert data["category"] == "implementation"
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestFixEndpointEmptyKB -v
```

Expected: `FAILED — assert 404 == 200`

- [ ] **Step 4.3: Fix the empty-KB handling in `routes.py`**

In `akc_service/api/routes.py`, locate the block inside `get_pattern_fixes` that raises 404 when `not all_patterns` and replace it:

```python
# OLD:
if not all_patterns:
    logger.warning("get_pattern_fixes: no patterns in KB")
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No patterns found matching category '{request.category}'"
    )

# NEW:
if not all_patterns:
    logger.info("get_pattern_fixes: KB empty, returning empty fix list")
    return FixResponse(fixes=[], category=request.category, count=0)
```

Also replace the second 404 (no matching fixes found) with an empty-list response:

```python
# OLD:
if not matching_fixes:
    logger.info(
        f"get_pattern_fixes: no fixes found for category={request.category}"
    )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No fixes found for category '{request.category}'"
    )

# NEW:
if not matching_fixes:
    logger.info(f"get_pattern_fixes: no fixes found for category={request.category}")
    return FixResponse(fixes=[], category=request.category, count=0)
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestFixEndpointEmptyKB -v
```

Expected: PASS

- [ ] **Step 4.5: Commit**

```bash
git add akc_service/api/routes.py tests/test_akc_api_endpoints.py
git commit -m "fix: /fix endpoint returns empty list (not 404) when KB is empty"
```

---

## Task 5: Wire `AKC_SERVICE_SAFETY_LEVEL` into Confidence Delta

**Files:**
- Modify: `akc_service/learning_integration.py` (`apply_confidence_delta` reads SAFETY_LEVEL to cap delta)
- Modify: `tests/test_akc_api_endpoints.py` (add safety-level delta cap tests)

- [ ] **Step 5.1: Write failing tests**

Add to `tests/test_akc_api_endpoints.py`:

```python
class TestSafetyLevelDeltaCap:
    """Verify apply_confidence_delta respects AKC_SERVICE_SAFETY_LEVEL delta caps."""

    def _make_task_result(self, status: str, pattern_id: str) -> dict:
        return {
            "schema_version": "1.0",
            "task_id": "t-safety-001",
            "status": status,
            "timestamp": "2026-05-05T10:00:00Z",
            "akc_context": {
                "akc_enabled": True,
                "knowledge_patterns_active": [pattern_id],
            },
        }

    def test_level_1_caps_delta_at_0_15(self, tmp_path, monkeypatch):
        import json
        from akc_service import learning_integration as li
        import akc_service.config as cfg

        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(cfg, "SAFETY_LEVEL", 1)

        # Seed a pattern at confidence 0.50 — normal +0.05 success delta stays under 0.15 cap
        pattern = {
            "id": "p-safety-1", "entity": "e", "component": "c",
            "confidence": 0.50, "confidence_tier": "experimental",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
        }
        (tmp_path / "patterns.jsonl").write_text(json.dumps(pattern) + "\n")

        result = li.apply_confidence_delta(self._make_task_result("success", "p-safety-1"))
        assert result["status"] == "success"
        assert result["patterns_updated"] == 1

        # Read back new confidence from patterns.jsonl
        lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
        updated = json.loads(lines[-1])
        new_conf = updated["confidence"]
        # delta = 0.05, new = 0.55 — well within 0.15 cap
        assert abs(new_conf - 0.55) < 0.001

    def test_level_2_caps_delta_at_0_10(self, tmp_path, monkeypatch):
        import json
        from akc_service import learning_integration as li
        import akc_service.config as cfg

        monkeypatch.setattr(li, "KB_DIR", tmp_path)
        monkeypatch.setattr(cfg, "SAFETY_LEVEL", 2)

        pattern = {
            "id": "p-safety-2", "entity": "e", "component": "c",
            "confidence": 0.50, "confidence_tier": "experimental",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
        }
        (tmp_path / "patterns.jsonl").write_text(json.dumps(pattern) + "\n")

        # At level 2, cap is 0.10. The normal success delta of +0.05 stays under cap.
        result = li.apply_confidence_delta(self._make_task_result("success", "p-safety-2"))
        assert result["status"] == "success"

        lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
        updated = json.loads(lines[-1])
        assert abs(updated["confidence"] - 0.55) < 0.001
```

- [ ] **Step 5.2: Run tests to verify they pass right away** (delta is 0.05 which is always within cap — these tests verify cap doesn't break normal operation)

```bash
python -m pytest tests/test_akc_api_endpoints.py::TestSafetyLevelDeltaCap -v
```

These should already PASS once Task 1 is done (config module exists). If not, check SAFETY_LEVEL is being imported.

- [ ] **Step 5.3: Wire safety level into `apply_confidence_delta`**

In `akc_service/learning_integration.py`, add import at top:

```python
from akc_service.config import SAFETY_LEVEL, max_delta_for_level
```

Then inside `apply_confidence_delta`, after computing `delta`, add clamping:

```python
# Determine delta based on status
if status == "success":
    delta = 0.05
elif status == "failed":
    delta = -0.10
else:
    return {"status": "error", "error": f"Unknown status: {status}", "patterns_updated": 0, "latency_ms": 0}

# Clamp delta to safety level cap
max_allowed = max_delta_for_level(SAFETY_LEVEL)
delta = max(min(delta, max_allowed), -max_allowed)
```

- [ ] **Step 5.4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 5.5: Commit**

```bash
git add akc_service/learning_integration.py tests/test_akc_api_endpoints.py
git commit -m "feat: wire AKC_SERVICE_SAFETY_LEVEL delta cap into apply_confidence_delta"
```

---

## Task 6: Wire `AKC_SERVICE_URL` in Godot Adapter

**Files:**
- Modify: `akc_service/adapters/godot/godot_akc_adapter.py:27` (read env var as default)
- Modify: `adapters/godot/tests/test_godot_akc_adapter.py` (add test for env var)

- [ ] **Step 6.1: Write failing test**

Add to `adapters/godot/tests/test_godot_akc_adapter.py`:

```python
def test_adapter_reads_akc_url_from_env(monkeypatch):
    """GodotAKCAdapter reads AKC_SERVICE_URL env var when no explicit url is given."""
    monkeypatch.setenv("AKC_SERVICE_URL", "http://remote-host:9000")
    from importlib import reload
    import akc_service.adapters.godot.godot_akc_adapter as mod
    reload(mod)
    adapter = mod.GodotAKCAdapter()
    assert adapter.akc_url == "http://remote-host:9000"
    assert "remote-host:9000" in adapter.record_endpoint


def test_adapter_explicit_url_overrides_env(monkeypatch):
    """Explicit url arg takes precedence over AKC_SERVICE_URL env var."""
    monkeypatch.setenv("AKC_SERVICE_URL", "http://remote-host:9000")
    from akc_service.adapters.godot.godot_akc_adapter import GodotAKCAdapter
    adapter = GodotAKCAdapter(akc_url="http://explicit:8888")
    assert adapter.akc_url == "http://explicit:8888"
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
python -m pytest adapters/godot/tests/test_godot_akc_adapter.py -v -k "test_adapter_reads_akc_url_from_env or test_adapter_explicit_url_overrides_env"
```

Expected: FAIL — adapter still uses hardcoded default `http://localhost:8000`

- [ ] **Step 6.3: Update adapter constructor**

In `akc_service/adapters/godot/godot_akc_adapter.py`, change the `__init__` signature:

```python
import os

# OLD:
def __init__(self, akc_url: str = "http://localhost:8000") -> None:

# NEW:
def __init__(self, akc_url: str = "") -> None:
    if not akc_url:
        akc_url = os.environ.get("AKC_SERVICE_URL", "http://localhost:8000")
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
python -m pytest adapters/godot/tests/ -v
```

Expected: all adapter tests PASS

- [ ] **Step 6.5: Commit**

```bash
git add akc_service/adapters/godot/godot_akc_adapter.py adapters/godot/tests/test_godot_akc_adapter.py
git commit -m "fix: GodotAKCAdapter reads AKC_SERVICE_URL env var as default"
```

---

## Task 7: Wire `AKC_SERVICE_LOG_LEVEL` at Server Startup

**Files:**
- Modify: `akc_service/api/main.py` (read LOG_LEVEL from config at startup)

- [ ] **Step 7.1: Read `main.py` to find the logging.basicConfig call**

```bash
grep -n "basicConfig\|logging\|LOG_LEVEL" /Users/ducph/godot/my-demon/packages/akc-service/akc_service/api/main.py | head -20
```

- [ ] **Step 7.2: Update `main.py` to use config LOG_LEVEL**

Find the `logging.basicConfig` call in `main.py` and replace it:

```python
# Add import near top
from akc_service.config import LOG_LEVEL

# Replace existing basicConfig (usually looks like):
# logging.basicConfig(level=logging.INFO, ...)
# with:
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
```

- [ ] **Step 7.3: Verify server still starts**

```bash
python -c "from akc_service.api.main import app; print('OK')"
```

Expected: `OK` with no errors

- [ ] **Step 7.4: Commit**

```bash
git add akc_service/api/main.py
git commit -m "feat: read AKC_SERVICE_LOG_LEVEL from env at server startup"
```

---

## Task 8: Fix CAPABILITIES.md Method Name Typos

**Files:**
- Modify: `docs/CAPABILITIES.md:172-173` (plural → singular method names)

- [ ] **Step 8.1: Fix the two method names in CAPABILITIES.md**

In `docs/CAPABILITIES.md`, find lines 172–173:

```gdscript
# OLD:
adapter.record_lint_results(linting_data)  # HTTP 202
adapter.record_test_results(test_data)     # HTTP 202

# NEW:
adapter.record_lint_result(lint_result, file_path)  # HTTP 202
adapter.record_test_result(test_result, scene_path) # HTTP 202
```

- [ ] **Step 8.2: Commit**

```bash
git add docs/CAPABILITIES.md
git commit -m "docs: fix GodotAKCAdapter method names (plural -> singular)"
```

---

## Task 9: Run Full Test Suite and Verify All Pass

- [ ] **Step 9.1: Run all tests**

```bash
cd /Users/ducph/godot/my-demon/packages/akc-service
python -m pytest tests/ adapters/godot/tests/ -v 2>&1 | tail -30
```

Expected: all tests PASS, zero failures.

- [ ] **Step 9.2: Verify service starts and health endpoint responds**

```bash
# Start server in background
uvicorn akc_service.api.main:app --port 8765 &
sleep 2
curl -s http://localhost:8765/akc/v1/health
# Kill background server
kill %1
```

Expected: `{"status":"healthy","timestamp":"..."}` with no errors in stderr.

- [ ] **Step 9.3: Verify service handles empty KB gracefully**

```bash
# With a fresh empty KB dir
AKC_SERVICE_KB_DIR=/tmp/empty-akc-kb uvicorn akc_service.api.main:app --port 8766 &
sleep 2
curl -s http://localhost:8766/akc/v1/health
curl -s -X POST http://localhost:8766/akc/v1/fix \
  -H "Content-Type: application/json" \
  -d '{"signature_hash":"abc","category":"implementation"}'
kill %1
```

Expected: health returns `healthy`, fix returns `{"fixes":[],"category":"implementation","count":0}`
