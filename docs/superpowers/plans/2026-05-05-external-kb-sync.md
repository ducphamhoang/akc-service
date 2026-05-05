# akc-service External KB Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow akc-service to run standalone with a local write-ahead KB, while optionally syncing patterns to/from a remote akc-service instance via both CLI (`akc-sync`) and REST API (`POST /akc/v1/sync/push`, `POST /akc/v1/sync/pull`, `GET /akc/v1/sync/status`, `GET /akc/v1/sync/export`).

**Architecture:** A new `akc_service/sync/` package handles all remote-KB logic. The local KB (append-only JSONL) is the source of truth; `sync_state.json` tracks cursors and a pending-push queue. Syncing is always explicit — no background threads — triggered by CLI command or API call. The remote side exposes `GET /akc/v1/sync/export` for pull and `POST /akc/v1/sync/receive` for push. Both sides can be the same akc-service binary.

**Tech Stack:** Python 3.11, FastAPI, httpx (async HTTP client for outbound sync calls), pytest, pydantic v2

---

## New Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `AKC_SERVICE_REMOTE_URL` | `""` (disabled) | URL of remote akc-service (sync disabled when empty) |
| `AKC_SERVICE_REMOTE_API_KEY` | `""` | Bearer token for remote KB auth |
| `AKC_SERVICE_REMOTE_TIMEOUT` | `10` | HTTP timeout in seconds for remote calls |
| `AKC_SERVICE_SYNC_ON_STARTUP` | `false` | Pull remote patterns on server startup |
| `AKC_SERVICE_SYNC_PUSH_BATCH` | `50` | Max patterns per push batch |
| `AKC_SERVICE_SYNC_MIN_CONFIDENCE` | `0.70` | Min confidence for patterns eligible to push |

Add all to `docs/CONFIGURATION.md` under a new "Sync Configuration" section.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `akc_service/sync/__init__.py` | Package marker |
| Create | `akc_service/sync/config.py` | Read 6 new sync env vars |
| Create | `akc_service/sync/state.py` | Read/write `sync_state.json` |
| Create | `akc_service/sync/push.py` | Push local patterns to remote |
| Create | `akc_service/sync/pull.py` | Pull remote patterns into local KB |
| Create | `akc_service/sync/cli.py` | `akc-sync` CLI entry point |
| Create | `akc_service/api/sync_routes.py` | 4 new sync API endpoints |
| Modify | `akc_service/api/main.py` | Include `sync_routes.router`; optional startup pull |
| Modify | `akc_service/learning_integration.py` | `append_pattern_version` adds to pending queue |
| Modify | `pyproject.toml` | Register `akc-sync` console script |
| Modify | `docs/CONFIGURATION.md` | Add sync env vars section |
| Create | `tests/test_sync_state.py` | Tests for sync_state read/write |
| Create | `tests/test_sync_push.py` | Tests for push logic |
| Create | `tests/test_sync_pull.py` | Tests for pull logic + conflict resolution |
| Create | `tests/test_sync_routes.py` | Tests for sync API endpoints |
| Create | `tests/test_sync_cli.py` | Tests for CLI commands |

---

## Task 1: Sync Config Module

**Files:**
- Create: `akc_service/sync/__init__.py`
- Create: `akc_service/sync/config.py`
- Create: `tests/test_sync_state.py` (config section)

- [ ] **Step 1.1: Write failing tests**

```python
# tests/test_sync_state.py  (partial — config section)
import os
import pytest


def test_remote_url_empty_by_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_REMOTE_URL", raising=False)
    import importlib
    import akc_service.sync.config as c
    importlib.reload(c)
    assert c.REMOTE_URL == ""


def test_sync_disabled_when_no_remote_url(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_REMOTE_URL", raising=False)
    import importlib
    import akc_service.sync.config as c
    importlib.reload(c)
    assert c.sync_enabled() is False


def test_sync_enabled_when_remote_url_set(monkeypatch):
    monkeypatch.setenv("AKC_SERVICE_REMOTE_URL", "http://remote:8000")
    import importlib
    import akc_service.sync.config as c
    importlib.reload(c)
    assert c.sync_enabled() is True


def test_min_confidence_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_SYNC_MIN_CONFIDENCE", raising=False)
    import importlib
    import akc_service.sync.config as c
    importlib.reload(c)
    assert c.MIN_CONFIDENCE == 0.70


def test_push_batch_default(monkeypatch):
    monkeypatch.delenv("AKC_SERVICE_SYNC_PUSH_BATCH", raising=False)
    import importlib
    import akc_service.sync.config as c
    importlib.reload(c)
    assert c.PUSH_BATCH == 50
```

- [ ] **Step 1.2: Run to verify they fail**

```bash
cd /Users/ducph/godot/my-demon/packages/akc-service
python -m pytest tests/test_sync_state.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'akc_service.sync'`

- [ ] **Step 1.3: Create the sync package and config**

```python
# akc_service/sync/__init__.py
# (empty)
```

```python
# akc_service/sync/config.py
import os


REMOTE_URL: str = os.environ.get("AKC_SERVICE_REMOTE_URL", "").rstrip("/")
REMOTE_API_KEY: str = os.environ.get("AKC_SERVICE_REMOTE_API_KEY", "")
REMOTE_TIMEOUT: int = int(os.environ.get("AKC_SERVICE_REMOTE_TIMEOUT", "10"))
SYNC_ON_STARTUP: bool = os.environ.get("AKC_SERVICE_SYNC_ON_STARTUP", "false").lower() == "true"
PUSH_BATCH: int = int(os.environ.get("AKC_SERVICE_SYNC_PUSH_BATCH", "50"))

try:
    MIN_CONFIDENCE: float = float(os.environ.get("AKC_SERVICE_SYNC_MIN_CONFIDENCE", "0.70"))
except ValueError:
    MIN_CONFIDENCE = 0.70


def sync_enabled() -> bool:
    """Return True only when a remote URL is configured."""
    return bool(REMOTE_URL)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_state.py -v -k "config or remote_url or sync_enabled or min_confidence or push_batch"
```

Expected: all PASS

- [ ] **Step 1.5: Commit**

```bash
git add akc_service/sync/__init__.py akc_service/sync/config.py tests/test_sync_state.py
git commit -m "feat: add sync config module with 6 new env vars"
```

---

## Task 2: Sync State File (`sync_state.json`)

**Files:**
- Create: `akc_service/sync/state.py`
- Modify: `tests/test_sync_state.py` (add state tests)

- [ ] **Step 2.1: Write failing tests**

Append to `tests/test_sync_state.py`:

```python
import json
from pathlib import Path


def test_load_state_returns_defaults_when_no_file(tmp_path):
    from akc_service.sync.state import load_state
    state = load_state(tmp_path)
    assert state["schema_version"] == "1.0"
    assert state["remote_url"] == ""
    assert state["last_push_cursor"] is None
    assert state["last_pull_cursor"] is None
    assert state["pending_pattern_ids"] == []
    assert state["sync_errors"] == []


def test_save_and_reload_state(tmp_path):
    from akc_service.sync.state import load_state, save_state
    state = load_state(tmp_path)
    state["remote_url"] = "http://remote:9000"
    state["last_push_cursor"] = "2026-05-05T12:00:00Z"
    save_state(state, tmp_path)

    reloaded = load_state(tmp_path)
    assert reloaded["remote_url"] == "http://remote:9000"
    assert reloaded["last_push_cursor"] == "2026-05-05T12:00:00Z"


def test_add_pending_pattern_id(tmp_path):
    from akc_service.sync.state import load_state, save_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-001", tmp_path)
    reloaded = load_state(tmp_path)
    assert "pat-001" in reloaded["pending_pattern_ids"]


def test_add_pending_id_is_idempotent(tmp_path):
    from akc_service.sync.state import load_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-001", tmp_path)
    add_pending_id(state, "pat-001", tmp_path)
    reloaded_state = load_state(tmp_path)
    assert reloaded_state["pending_pattern_ids"].count("pat-001") == 1


def test_clear_pending_ids(tmp_path):
    from akc_service.sync.state import load_state, add_pending_id, clear_pending_ids
    state = load_state(tmp_path)
    add_pending_id(state, "pat-001", tmp_path)
    add_pending_id(state, "pat-002", tmp_path)
    state = load_state(tmp_path)
    clear_pending_ids(state, ["pat-001"], tmp_path)
    reloaded = load_state(tmp_path)
    assert "pat-001" not in reloaded["pending_pattern_ids"]
    assert "pat-002" in reloaded["pending_pattern_ids"]
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_state.py -v -k "load_state or save_state or add_pending or clear_pending"
```

Expected: `ImportError` — `state.py` not yet created

- [ ] **Step 2.3: Create `akc_service/sync/state.py`**

```python
# akc_service/sync/state.py
import json
from pathlib import Path
from typing import Optional


_STATE_FILE = "sync_state.json"

_DEFAULT_STATE = {
    "schema_version": "1.0",
    "remote_url": "",
    "last_push_at": None,
    "last_pull_at": None,
    "last_push_cursor": None,
    "last_pull_cursor": None,
    "push_queue_size": 0,
    "pending_pattern_ids": [],
    "sync_errors": [],
}


def load_state(kb_dir: Path) -> dict:
    """Load sync_state.json; return defaults if absent."""
    path = kb_dir / _STATE_FILE
    if not path.exists():
        return dict(_DEFAULT_STATE)
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT_STATE)


def save_state(state: dict, kb_dir: Path) -> None:
    """Atomically write sync_state.json."""
    kb_dir.mkdir(parents=True, exist_ok=True)
    path = kb_dir / _STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def add_pending_id(state: dict, pattern_id: str, kb_dir: Path) -> None:
    """Add pattern_id to pending queue (idempotent) and persist."""
    if pattern_id not in state["pending_pattern_ids"]:
        state["pending_pattern_ids"].append(pattern_id)
        state["push_queue_size"] = len(state["pending_pattern_ids"])
        save_state(state, kb_dir)


def clear_pending_ids(state: dict, cleared_ids: list, kb_dir: Path) -> None:
    """Remove successfully-pushed IDs from the pending queue and persist."""
    state["pending_pattern_ids"] = [
        p for p in state["pending_pattern_ids"] if p not in cleared_ids
    ]
    state["push_queue_size"] = len(state["pending_pattern_ids"])
    save_state(state, kb_dir)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_state.py -v
```

Expected: all PASS

- [ ] **Step 2.5: Commit**

```bash
git add akc_service/sync/state.py tests/test_sync_state.py
git commit -m "feat: add sync state file reader/writer (sync_state.json)"
```

---

## Task 3: Wire Pending Queue into `append_pattern_version`

**Files:**
- Modify: `akc_service/learning_integration.py` (`append_pattern_version` queues ID when sync is enabled)
- Create: `tests/test_sync_pending_queue.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_sync_pending_queue.py
import json
import pytest
from pathlib import Path


def _make_pattern(pid: str, confidence: float = 0.80) -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


def test_append_adds_to_pending_when_sync_enabled(tmp_path, monkeypatch):
    import akc_service.sync.config as sync_cfg
    from akc_service import learning_integration as li

    monkeypatch.setattr(li, "KB_DIR", tmp_path)
    monkeypatch.setattr(sync_cfg, "REMOTE_URL", "http://remote:8000")
    monkeypatch.setattr(sync_cfg, "MIN_CONFIDENCE", 0.70)

    pattern = _make_pattern("pat-queue-001", confidence=0.85)
    li.append_pattern_version(pattern)

    from akc_service.sync.state import load_state
    state = load_state(tmp_path)
    assert "pat-queue-001" in state["pending_pattern_ids"]


def test_append_skips_pending_when_sync_disabled(tmp_path, monkeypatch):
    import akc_service.sync.config as sync_cfg
    from akc_service import learning_integration as li

    monkeypatch.setattr(li, "KB_DIR", tmp_path)
    monkeypatch.setattr(sync_cfg, "REMOTE_URL", "")  # sync disabled

    pattern = _make_pattern("pat-queue-002", confidence=0.85)
    li.append_pattern_version(pattern)

    from akc_service.sync.state import load_state
    state = load_state(tmp_path)
    assert "pat-queue-002" not in state["pending_pattern_ids"]


def test_append_skips_low_confidence_patterns(tmp_path, monkeypatch):
    import akc_service.sync.config as sync_cfg
    from akc_service import learning_integration as li

    monkeypatch.setattr(li, "KB_DIR", tmp_path)
    monkeypatch.setattr(sync_cfg, "REMOTE_URL", "http://remote:8000")
    monkeypatch.setattr(sync_cfg, "MIN_CONFIDENCE", 0.70)

    # confidence 0.50 is below the 0.70 threshold
    pattern = _make_pattern("pat-queue-003", confidence=0.50)
    li.append_pattern_version(pattern)

    from akc_service.sync.state import load_state
    state = load_state(tmp_path)
    assert "pat-queue-003" not in state["pending_pattern_ids"]
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_pending_queue.py -v
```

Expected: first test FAILS — pending queue is never updated

- [ ] **Step 3.3: Add queue logic to `append_pattern_version` in `learning_integration.py`**

Find `append_pattern_version` in `learning_integration.py` and add at the end of the function, after the file write:

```python
def append_pattern_version(pattern: dict) -> None:
    # ... existing file-append logic unchanged ...

    # Queue for remote sync if enabled and confidence meets threshold
    try:
        from akc_service.sync import config as sync_cfg
        from akc_service.sync.state import load_state, add_pending_id
        if sync_cfg.sync_enabled():
            confidence = pattern.get("confidence", 0.0)
            if confidence >= sync_cfg.MIN_CONFIDENCE:
                state = load_state(KB_DIR)
                add_pending_id(state, pattern["id"], KB_DIR)
    except Exception:
        pass  # sync queue failure must never break the core write path
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_pending_queue.py -v
```

Expected: all 3 PASS

- [ ] **Step 3.5: Commit**

```bash
git add akc_service/learning_integration.py tests/test_sync_pending_queue.py
git commit -m "feat: append_pattern_version queues pattern ID for remote sync when enabled"
```

---

## Task 4: Push Logic

**Files:**
- Create: `akc_service/sync/push.py`
- Create: `tests/test_sync_push.py`

- [ ] **Step 4.1: Write failing tests**

```python
# tests/test_sync_push.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _seed_kb(kb_dir: Path, patterns: list[dict]) -> None:
    pf = kb_dir / "patterns.jsonl"
    pf.write_text("\n".join(json.dumps(p) for p in patterns) + "\n")


def _make_pattern(pid: str, confidence: float, updated_at: str = "2026-05-05T10:00:00Z") -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": updated_at,
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


def test_push_sends_eligible_patterns(tmp_path):
    """Push sends patterns with confidence >= MIN_CONFIDENCE."""
    patterns = [
        _make_pattern("pat-push-001", confidence=0.85),
        _make_pattern("pat-push-002", confidence=0.50),  # below threshold, excluded
    ]
    _seed_kb(tmp_path, patterns)

    # Pre-populate pending queue
    from akc_service.sync.state import load_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-push-001", tmp_path)

    with patch("akc_service.sync.push.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"accepted": 1}
        mock_httpx.post.return_value = mock_response

        from akc_service.sync import push
        result = push.push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

    assert result["pushed"] == 1
    assert result["skipped"] == 0
    call_body = mock_httpx.post.call_args[1]["json"]
    assert len(call_body["patterns"]) == 1
    assert call_body["patterns"][0]["id"] == "pat-push-001"


def test_push_clears_pending_on_success(tmp_path):
    """Successful push removes IDs from pending queue."""
    patterns = [_make_pattern("pat-push-003", confidence=0.80)]
    _seed_kb(tmp_path, patterns)

    from akc_service.sync.state import load_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-push-003", tmp_path)

    with patch("akc_service.sync.push.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"accepted": 1}
        mock_httpx.post.return_value = mock_response

        from akc_service.sync import push
        push.push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

    state = load_state(tmp_path)
    assert "pat-push-003" not in state["pending_pattern_ids"]


def test_push_dry_run_does_not_send(tmp_path):
    """Dry-run returns eligible count without making HTTP calls."""
    patterns = [_make_pattern("pat-dry-001", confidence=0.80)]
    _seed_kb(tmp_path, patterns)

    from akc_service.sync.state import load_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-dry-001", tmp_path)

    with patch("akc_service.sync.push.httpx") as mock_httpx:
        from akc_service.sync import push
        result = push.push_to_remote(
            kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key", dry_run=True
        )

    mock_httpx.post.assert_not_called()
    assert result["pushed"] == 0
    assert result["would_push"] == 1


def test_push_records_error_on_network_failure(tmp_path):
    """Network failure is recorded in sync_state.sync_errors, not raised."""
    patterns = [_make_pattern("pat-err-001", confidence=0.80)]
    _seed_kb(tmp_path, patterns)

    from akc_service.sync.state import load_state, add_pending_id
    state = load_state(tmp_path)
    add_pending_id(state, "pat-err-001", tmp_path)

    with patch("akc_service.sync.push.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("connection refused")

        from akc_service.sync import push
        result = push.push_to_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="key")

    assert result["errors"] == 1
    state = load_state(tmp_path)
    assert len(state["sync_errors"]) == 1
    assert "connection refused" in state["sync_errors"][0]["error"]
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_push.py -v
```

Expected: `ImportError: cannot import name 'push'`

- [ ] **Step 4.3: Create `akc_service/sync/push.py`**

```python
# akc_service/sync/push.py
import httpx
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from akc_service.learning_integration import load_all_patterns
from akc_service.sync.state import load_state, save_state, clear_pending_ids

logger = logging.getLogger(__name__)


def push_to_remote(
    kb_dir: Path,
    remote_url: str,
    api_key: str,
    min_confidence: float = 0.70,
    batch_size: int = 50,
    dry_run: bool = False,
    timeout: int = 10,
) -> dict:
    """
    Push locally-queued patterns to remote akc-service.

    Returns:
        dict with keys: pushed, skipped, errors, would_push (dry_run only), cursor
    """
    state = load_state(kb_dir)
    pending_ids = set(state.get("pending_pattern_ids", []))

    if not pending_ids:
        return {"pushed": 0, "skipped": 0, "errors": 0, "cursor": None}

    # Load patterns and filter eligible ones
    all_patterns = load_all_patterns()
    eligible = [
        p for p in all_patterns
        if p.get("id") in pending_ids and p.get("confidence", 0.0) >= min_confidence
    ]
    skipped = len(pending_ids) - len(eligible)

    if dry_run:
        return {"pushed": 0, "skipped": skipped, "errors": 0, "would_push": len(eligible), "cursor": None}

    if not eligible:
        return {"pushed": 0, "skipped": skipped, "errors": 0, "cursor": None}

    pushed_total = 0
    error_count = 0
    pushed_ids = []

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # Send in batches
    for i in range(0, len(eligible), batch_size):
        batch = eligible[i : i + batch_size]
        try:
            resp = httpx.post(
                f"{remote_url}/akc/v1/sync/receive",
                json={"patterns": batch, "pushed_at": _now_iso()},
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code == 200:
                pushed_total += len(batch)
                pushed_ids.extend(p["id"] for p in batch)
            else:
                error_count += 1
                _record_error(state, "push", f"HTTP {resp.status_code}", kb_dir)
        except Exception as e:
            error_count += 1
            _record_error(state, "push", str(e), kb_dir)

    if pushed_ids:
        state = load_state(kb_dir)
        clear_pending_ids(state, pushed_ids, kb_dir)
        state["last_push_at"] = _now_iso()
        if pushed_ids:
            # cursor = latest updated_at among pushed patterns
            pushed_patterns = [p for p in eligible if p["id"] in pushed_ids]
            state["last_push_cursor"] = max(p.get("updated_at", "") for p in pushed_patterns)
        save_state(state, kb_dir)

    return {
        "pushed": pushed_total,
        "skipped": skipped,
        "errors": error_count,
        "cursor": state.get("last_push_cursor"),
    }


def _record_error(state: dict, direction: str, error: str, kb_dir: Path) -> None:
    state.setdefault("sync_errors", []).append({
        "timestamp": _now_iso(),
        "direction": direction,
        "error": error,
        "retry_count": 0,
    })
    save_state(state, kb_dir)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_push.py -v
```

Expected: all 4 PASS

- [ ] **Step 4.5: Commit**

```bash
git add akc_service/sync/push.py tests/test_sync_push.py
git commit -m "feat: add sync push logic with dry-run and error recording"
```

---

## Task 5: Pull Logic and Conflict Resolution

**Files:**
- Create: `akc_service/sync/pull.py`
- Create: `tests/test_sync_pull.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/test_sync_pull.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_pattern(pid: str, confidence: float, updated_at: str = "2026-05-05T10:00:00Z") -> dict:
    return {
        "id": pid, "entity": "e", "component": "c",
        "confidence": confidence, "confidence_tier": "production",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": updated_at,
        "version": {"current": "v1", "history": []}, "fixes": [], "category": "other",
    }


def test_pull_writes_new_remote_patterns(tmp_path):
    """Patterns from remote not in local KB are appended."""
    remote_pattern = _make_pattern("pat-remote-001", confidence=0.80)

    with patch("akc_service.sync.pull.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
        }
        mock_httpx.get.return_value = mock_resp

        from akc_service.sync import pull
        result = pull.pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

    assert result["pulled"] == 1
    lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
    written = json.loads(lines[-1])
    assert written["id"] == "pat-remote-001"


def test_pull_keeps_local_when_local_confidence_higher(tmp_path):
    """If local pattern has higher confidence, local wins (default conflict resolution)."""
    local_pattern = _make_pattern("pat-conflict-001", confidence=0.90)
    (tmp_path / "patterns.jsonl").write_text(json.dumps(local_pattern) + "\n")

    remote_pattern = _make_pattern("pat-conflict-001", confidence=0.60)

    with patch("akc_service.sync.pull.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
        }
        mock_httpx.get.return_value = mock_resp

        from akc_service.sync import pull
        result = pull.pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

    assert result["conflicts"] == 1
    # Local should still be at 0.90, not overwritten to 0.60
    lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
    last = json.loads(lines[-1])
    assert last["confidence"] == 0.90


def test_pull_overwrite_local_flag_forces_remote_version(tmp_path):
    """overwrite_local=True forces remote version regardless of confidence."""
    local_pattern = _make_pattern("pat-overwrite-001", confidence=0.90)
    (tmp_path / "patterns.jsonl").write_text(json.dumps(local_pattern) + "\n")

    remote_pattern = _make_pattern("pat-overwrite-001", confidence=0.60)

    with patch("akc_service.sync.pull.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "patterns": [remote_pattern], "count": 1, "as_of": "2026-05-05T12:00:00Z"
        }
        mock_httpx.get.return_value = mock_resp

        from akc_service.sync import pull
        result = pull.pull_from_remote(
            kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k", overwrite_local=True
        )

    lines = (tmp_path / "patterns.jsonl").read_text().strip().split("\n")
    last = json.loads(lines[-1])
    assert last["confidence"] == 0.60


def test_pull_updates_last_pull_cursor(tmp_path):
    """Successful pull advances last_pull_cursor in sync_state.json."""
    with patch("akc_service.sync.pull.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "patterns": [_make_pattern("pat-cursor-001", 0.80)],
            "count": 1,
            "as_of": "2026-05-05T14:00:00Z",
        }
        mock_httpx.get.return_value = mock_resp

        from akc_service.sync import pull
        pull.pull_from_remote(kb_dir=tmp_path, remote_url="http://remote:8000", api_key="k")

    from akc_service.sync.state import load_state
    state = load_state(tmp_path)
    assert state["last_pull_cursor"] == "2026-05-05T14:00:00Z"
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_pull.py -v
```

Expected: `ImportError` — `pull.py` not yet created

- [ ] **Step 5.3: Create `akc_service/sync/pull.py`**

```python
# akc_service/sync/pull.py
import httpx
import logging
from datetime import datetime, timezone
from pathlib import Path

from akc_service.learning_integration import load_all_patterns, append_pattern_version, log_confidence_update
from akc_service.sync.state import load_state, save_state

logger = logging.getLogger(__name__)


def pull_from_remote(
    kb_dir: Path,
    remote_url: str,
    api_key: str,
    since: str = None,
    overwrite_local: bool = False,
    dry_run: bool = False,
    timeout: int = 10,
) -> dict:
    """
    Pull patterns from remote akc-service into local KB.

    Conflict resolution (default): remote wins only if remote confidence > local confidence.
    With overwrite_local=True: remote always wins.

    Returns:
        dict with keys: pulled, conflicts, errors
    """
    state = load_state(kb_dir)
    cursor = since or state.get("last_pull_cursor")

    url = f"{remote_url}/akc/v1/sync/export"
    params = {}
    if cursor:
        params["since"] = cursor

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {"pulled": 0, "conflicts": 0, "errors": 1}
        data = resp.json()
    except Exception as e:
        logger.warning(f"pull_from_remote: HTTP error — {e}")
        return {"pulled": 0, "conflicts": 0, "errors": 1}

    remote_patterns = data.get("patterns", [])
    as_of = data.get("as_of")

    if dry_run:
        return {"pulled": 0, "conflicts": 0, "errors": 0, "would_pull": len(remote_patterns)}

    local_patterns = load_all_patterns()
    local_index = {p["id"]: p for p in local_patterns}

    pulled = 0
    conflicts = 0

    for remote in remote_patterns:
        pid = remote.get("id")
        if not pid:
            continue

        local = local_index.get(pid)

        if local:
            r_conf = remote.get("confidence", 0.0)
            l_conf = local.get("confidence", 0.0)
            if not overwrite_local and l_conf >= r_conf:
                # Keep local — log conflict decision
                conflicts += 1
                log_confidence_update({
                    "history_id": f"ch-sync-conflict-{_now_iso()}",
                    "timestamp": _now_iso(),
                    "pattern_id": pid,
                    "old_confidence": l_conf,
                    "new_confidence": l_conf,
                    "confidence_delta": 0,
                    "task_id": "sync_pull",
                    "task_status": "conflict_kept_local",
                    "tier_change": "none",
                    "update_type": "sync_conflict",
                    "reason": f"local {l_conf} >= remote {r_conf}; kept local",
                })
                continue

        append_pattern_version(remote)
        pulled += 1

    if as_of:
        state["last_pull_cursor"] = as_of
        state["last_pull_at"] = _now_iso()
        save_state(state, kb_dir)

    return {"pulled": pulled, "conflicts": conflicts, "errors": 0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_pull.py -v
```

Expected: all 4 PASS

- [ ] **Step 5.5: Commit**

```bash
git add akc_service/sync/pull.py tests/test_sync_pull.py
git commit -m "feat: add sync pull logic with conflict resolution and cursor tracking"
```

---

## Task 6: Sync API Endpoints

**Files:**
- Create: `akc_service/api/sync_routes.py`
- Modify: `akc_service/api/main.py` (include sync_routes.router)
- Create: `tests/test_sync_routes.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_sync_routes.py
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from akc_service.api.main import app
    return TestClient(app)


def test_sync_status_returns_200(client):
    """GET /akc/v1/sync/status returns 200 with sync metadata."""
    response = client.get("/akc/v1/sync/status")
    assert response.status_code == 200
    data = response.json()
    assert "remote_url" in data
    assert "connected" in data
    assert "push_queue_size" in data


def test_sync_export_returns_empty_list_when_no_kb(client, tmp_path, monkeypatch):
    """GET /akc/v1/sync/export returns empty patterns list when KB is empty."""
    import akc_service.api.sync_routes as sr
    monkeypatch.setattr(sr, "KB_DIR", tmp_path)
    response = client.get("/akc/v1/sync/export")
    assert response.status_code == 200
    data = response.json()
    assert data["patterns"] == []
    assert data["count"] == 0


@patch("akc_service.api.sync_routes.push_to_remote")
def test_sync_push_endpoint_calls_push_logic(mock_push, client):
    """POST /akc/v1/sync/push calls push_to_remote and returns result."""
    mock_push.return_value = {"pushed": 3, "skipped": 1, "errors": 0, "cursor": None}
    response = client.post("/akc/v1/sync/push", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["pushed"] == 3
    mock_push.assert_called_once()


@patch("akc_service.api.sync_routes.pull_from_remote")
def test_sync_pull_endpoint_calls_pull_logic(mock_pull, client):
    """POST /akc/v1/sync/pull calls pull_from_remote and returns result."""
    mock_pull.return_value = {"pulled": 5, "conflicts": 1, "errors": 0}
    response = client.post("/akc/v1/sync/pull", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["pulled"] == 5
    mock_pull.assert_called_once()


def test_sync_push_returns_503_when_sync_disabled(client, monkeypatch):
    """POST /akc/v1/sync/push returns 503 when no REMOTE_URL is configured."""
    import akc_service.sync.config as sc
    monkeypatch.setattr(sc, "REMOTE_URL", "")
    response = client.post("/akc/v1/sync/push", json={})
    assert response.status_code == 503
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_routes.py -v
```

Expected: 404 on all `/akc/v1/sync/*` routes

- [ ] **Step 6.3: Create `akc_service/api/sync_routes.py`**

```python
# akc_service/api/sync_routes.py
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from akc_service.learning_integration import load_all_patterns, append_pattern_version
from akc_service.sync.config import REMOTE_URL, REMOTE_API_KEY, REMOTE_TIMEOUT, MIN_CONFIDENCE, PUSH_BATCH, sync_enabled
from akc_service.sync.state import load_state
from akc_service.sync.push import push_to_remote
from akc_service.sync.pull import pull_from_remote

logger = logging.getLogger(__name__)

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))

router = APIRouter(prefix="/akc/v1/sync", tags=["sync"])


class SyncPushRequest(BaseModel):
    min_confidence: float = MIN_CONFIDENCE
    batch_size: int = PUSH_BATCH
    dry_run: bool = False


class SyncPullRequest(BaseModel):
    since: Optional[str] = None
    overwrite_local: bool = False
    dry_run: bool = False


class SyncReceiveRequest(BaseModel):
    patterns: list
    pushed_at: str


@router.get("/status")
async def sync_status():
    state = load_state(KB_DIR)
    reachable = False
    if sync_enabled():
        try:
            import httpx
            r = httpx.get(f"{REMOTE_URL}/akc/v1/health", timeout=3)
            reachable = r.status_code == 200
        except Exception:
            pass
    return {
        "remote_url": REMOTE_URL,
        "connected": sync_enabled(),
        "remote_reachable": reachable,
        "last_push_at": state.get("last_push_at"),
        "last_pull_at": state.get("last_pull_at"),
        "push_queue_size": state.get("push_queue_size", 0),
    }


@router.get("/export")
async def export_patterns(since: Optional[str] = None):
    all_patterns = load_all_patterns()
    if since:
        all_patterns = [p for p in all_patterns if p.get("updated_at", "") >= since]
    return {
        "patterns": all_patterns,
        "count": len(all_patterns),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.post("/push")
async def sync_push(request: SyncPushRequest):
    if not sync_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Sync disabled: AKC_SERVICE_REMOTE_URL not set")
    result = push_to_remote(
        kb_dir=KB_DIR, remote_url=REMOTE_URL, api_key=REMOTE_API_KEY,
        min_confidence=request.min_confidence, batch_size=request.batch_size,
        dry_run=request.dry_run, timeout=REMOTE_TIMEOUT,
    )
    return result


@router.post("/pull")
async def sync_pull(request: SyncPullRequest):
    if not sync_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Sync disabled: AKC_SERVICE_REMOTE_URL not set")
    result = pull_from_remote(
        kb_dir=KB_DIR, remote_url=REMOTE_URL, api_key=REMOTE_API_KEY,
        since=request.since, overwrite_local=request.overwrite_local,
        dry_run=request.dry_run, timeout=REMOTE_TIMEOUT,
    )
    return result


@router.post("/receive")
async def sync_receive(request: SyncReceiveRequest):
    """Inbound endpoint — accept patterns pushed from a remote node."""
    accepted = 0
    for pattern in request.patterns:
        try:
            append_pattern_version(pattern)
            accepted += 1
        except Exception as e:
            logger.warning(f"sync_receive: failed to write pattern {pattern.get('id')}: {e}")
    return {"accepted": accepted, "total": len(request.patterns)}
```

- [ ] **Step 6.4: Register sync router in `main.py`**

In `akc_service/api/main.py`, add after the existing router include:

```python
from akc_service.api.sync_routes import router as sync_router
app.include_router(sync_router)
```

- [ ] **Step 6.5: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_routes.py -v
```

Expected: all 5 PASS

- [ ] **Step 6.6: Commit**

```bash
git add akc_service/api/sync_routes.py akc_service/api/main.py tests/test_sync_routes.py
git commit -m "feat: add sync API endpoints (push, pull, export, receive, status)"
```

---

## Task 7: `akc-sync` CLI

**Files:**
- Create: `akc_service/sync/cli.py`
- Modify: `pyproject.toml` (register `akc-sync` entry point)
- Create: `tests/test_sync_cli.py`

- [ ] **Step 7.1: Write failing tests**

```python
# tests/test_sync_cli.py
import sys
import pytest
from unittest.mock import patch


def _run_cli(args: list[str]) -> int:
    """Run akc-sync CLI and return exit code."""
    from akc_service.sync.cli import main
    with patch("sys.argv", ["akc-sync"] + args):
        try:
            main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 0


def test_cli_status_subcommand_exits_0(tmp_path, monkeypatch):
    import akc_service.sync.cli as cli_mod
    monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
    code = _run_cli(["status"])
    assert code == 0


def test_cli_push_disabled_exits_1(tmp_path, monkeypatch):
    import akc_service.sync.config as sc
    import akc_service.sync.cli as cli_mod
    monkeypatch.setattr(sc, "REMOTE_URL", "")
    monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
    code = _run_cli(["push"])
    assert code == 1


@patch("akc_service.sync.cli.push_to_remote")
def test_cli_push_calls_push_logic(mock_push, tmp_path, monkeypatch):
    import akc_service.sync.config as sc
    import akc_service.sync.cli as cli_mod
    monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
    monkeypatch.setattr(sc, "REMOTE_API_KEY", "key")
    monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
    mock_push.return_value = {"pushed": 2, "skipped": 0, "errors": 0, "cursor": None}
    code = _run_cli(["push"])
    assert code == 0
    mock_push.assert_called_once()


@patch("akc_service.sync.cli.pull_from_remote")
def test_cli_pull_calls_pull_logic(mock_pull, tmp_path, monkeypatch):
    import akc_service.sync.config as sc
    import akc_service.sync.cli as cli_mod
    monkeypatch.setattr(sc, "REMOTE_URL", "http://remote:8000")
    monkeypatch.setattr(sc, "REMOTE_API_KEY", "key")
    monkeypatch.setattr(cli_mod, "KB_DIR", tmp_path)
    mock_pull.return_value = {"pulled": 3, "conflicts": 0, "errors": 0}
    code = _run_cli(["pull"])
    assert code == 0
    mock_pull.assert_called_once()
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_sync_cli.py -v
```

Expected: `ImportError: cannot import name 'cli'`

- [ ] **Step 7.3: Create `akc_service/sync/cli.py`**

```python
# akc_service/sync/cli.py
import argparse
import json
import os
import sys
from pathlib import Path

from akc_service.sync import config as sync_cfg
from akc_service.sync.push import push_to_remote
from akc_service.sync.pull import pull_from_remote
from akc_service.sync.state import load_state, save_state

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))


def _cmd_status(args) -> int:
    state = load_state(KB_DIR)
    print(json.dumps({
        "remote_url": sync_cfg.REMOTE_URL or "(not configured)",
        "sync_enabled": sync_cfg.sync_enabled(),
        "push_queue_size": state.get("push_queue_size", 0),
        "last_push_at": state.get("last_push_at"),
        "last_pull_at": state.get("last_pull_at"),
        "last_push_cursor": state.get("last_push_cursor"),
        "last_pull_cursor": state.get("last_pull_cursor"),
        "sync_errors": len(state.get("sync_errors", [])),
    }, indent=2))
    return 0


def _cmd_push(args) -> int:
    if not sync_cfg.sync_enabled():
        print("ERROR: AKC_SERVICE_REMOTE_URL is not set — sync is disabled.", file=sys.stderr)
        return 1
    result = push_to_remote(
        kb_dir=KB_DIR,
        remote_url=sync_cfg.REMOTE_URL,
        api_key=sync_cfg.REMOTE_API_KEY,
        min_confidence=args.min_confidence,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        timeout=sync_cfg.REMOTE_TIMEOUT,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["errors"] == 0 else 1


def _cmd_pull(args) -> int:
    if not sync_cfg.sync_enabled():
        print("ERROR: AKC_SERVICE_REMOTE_URL is not set — sync is disabled.", file=sys.stderr)
        return 1
    result = pull_from_remote(
        kb_dir=KB_DIR,
        remote_url=sync_cfg.REMOTE_URL,
        api_key=sync_cfg.REMOTE_API_KEY,
        since=getattr(args, "since", None),
        overwrite_local=getattr(args, "overwrite_local", False),
        dry_run=getattr(args, "dry_run", False),
        timeout=sync_cfg.REMOTE_TIMEOUT,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["errors"] == 0 else 1


def _cmd_connect(args) -> int:
    state = load_state(KB_DIR)
    state["remote_url"] = args.url
    save_state(state, KB_DIR)
    print(f"Connected to {args.url} (API key stored in environment — not persisted to disk)")
    return 0


def _cmd_reset_queue(args) -> int:
    state = load_state(KB_DIR)
    count = len(state.get("pending_pattern_ids", []))
    state["pending_pattern_ids"] = []
    state["push_queue_size"] = 0
    save_state(state, KB_DIR)
    print(f"Cleared {count} pending pattern IDs from push queue.")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="akc-sync", description="akc-service sync CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show sync queue and cursor state")

    push_p = sub.add_parser("push", help="Push locally-learned patterns to remote KB")
    push_p.add_argument("--dry-run", action="store_true")
    push_p.add_argument("--min-confidence", type=float, default=sync_cfg.MIN_CONFIDENCE)
    push_p.add_argument("--batch-size", type=int, default=sync_cfg.PUSH_BATCH)

    pull_p = sub.add_parser("pull", help="Pull remote patterns into local KB")
    pull_p.add_argument("--dry-run", action="store_true")
    pull_p.add_argument("--since", default=None)
    pull_p.add_argument("--overwrite-local", action="store_true")

    connect_p = sub.add_parser("connect", help="Configure remote KB URL")
    connect_p.add_argument("--url", required=True)
    connect_p.add_argument("--api-key", default="")

    sub.add_parser("reset-queue", help="Clear the push queue without pushing")

    args = parser.parse_args()

    dispatch = {
        "status": _cmd_status,
        "push": _cmd_push,
        "pull": _cmd_pull,
        "connect": _cmd_connect,
        "reset-queue": _cmd_reset_queue,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    sys.exit(dispatch[args.command](args))
```

- [ ] **Step 7.4: Register `akc-sync` in `pyproject.toml`**

In `pyproject.toml`, add to `[project.scripts]`:

```toml
[project.scripts]
akc-service = "akc_service.api.main:main"
akc-sync    = "akc_service.sync.cli:main"
```

- [ ] **Step 7.5: Run tests to verify they pass**

```bash
python -m pytest tests/test_sync_cli.py -v
```

Expected: all 4 PASS

- [ ] **Step 7.6: Commit**

```bash
git add akc_service/sync/cli.py pyproject.toml tests/test_sync_cli.py
git commit -m "feat: add akc-sync CLI (push/pull/status/connect/reset-queue)"
```

---

## Task 8: Update CONFIGURATION.md with Sync Variables

**Files:**
- Modify: `docs/CONFIGURATION.md` (add "Sync Configuration" section)

- [ ] **Step 8.1: Add sync vars section to `docs/CONFIGURATION.md`**

Append after the existing env-var table under "Core Configuration":

```markdown
---

## Sync Configuration

These variables control optional synchronisation with a remote akc-service instance.
All sync is disabled (and has zero overhead) when `AKC_SERVICE_REMOTE_URL` is not set.

### AKC_SERVICE_REMOTE_URL

**Type:** URL  
**Default:** `""` (sync disabled)  
**Purpose:** Base URL of the remote akc-service instance to sync with.

```bash
export AKC_SERVICE_REMOTE_URL=https://remote.example.com/akc
```

### AKC_SERVICE_REMOTE_API_KEY

**Type:** String  
**Default:** `""` (no auth)  
**Purpose:** Bearer token sent in `Authorization` header for remote calls.

### AKC_SERVICE_REMOTE_TIMEOUT

**Type:** Integer (seconds)  
**Default:** `10`  
**Purpose:** HTTP timeout for all outbound sync calls.

### AKC_SERVICE_SYNC_ON_STARTUP

**Type:** Boolean (`true`/`false`)  
**Default:** `false`  
**Purpose:** When `true`, the service pulls from the remote KB before accepting requests.

### AKC_SERVICE_SYNC_PUSH_BATCH

**Type:** Integer  
**Default:** `50`  
**Purpose:** Maximum number of patterns sent per push HTTP request.

### AKC_SERVICE_SYNC_MIN_CONFIDENCE

**Type:** Float  
**Default:** `0.70`  
**Purpose:** Patterns below this confidence threshold are excluded from push.

### CLI Usage

```bash
# Check sync state
akc-sync status

# Pull latest patterns from remote
akc-sync pull

# Push locally-learned patterns to remote
akc-sync push

# Preview what would be pushed
akc-sync push --dry-run

# Configure remote URL
akc-sync connect --url https://remote.example.com/akc --api-key <token>

# Clear push queue (e.g., after manual reconciliation)
akc-sync reset-queue
```
```

- [ ] **Step 8.2: Commit**

```bash
git add docs/CONFIGURATION.md
git commit -m "docs: add sync configuration section to CONFIGURATION.md"
```

---

## Task 9: Full Test Suite and Smoke Test

- [ ] **Step 9.1: Run all tests**

```bash
cd /Users/ducph/godot/my-demon/packages/akc-service
python -m pytest tests/ adapters/godot/tests/ -v 2>&1 | tail -40
```

Expected: all tests PASS, zero failures.

- [ ] **Step 9.2: Verify sync status with no remote configured**

```bash
python -m akc_service.sync.cli status
# Or after pip install -e .:
akc-sync status
```

Expected: JSON output showing `"sync_enabled": false` with no errors.

- [ ] **Step 9.3: Verify sync push returns 503 from API when unconfigured**

```bash
uvicorn akc_service.api.main:app --port 8765 &
sleep 2
curl -s -X POST http://localhost:8765/akc/v1/sync/status
curl -s -X POST http://localhost:8765/akc/v1/sync/push -H "Content-Type: application/json" -d '{}'
kill %1
```

Expected: `/sync/status` returns 200, `/sync/push` returns 503 with `"Sync disabled"` message.
