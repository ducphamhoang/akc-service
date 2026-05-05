import httpx
import logging
from datetime import datetime, timezone
from pathlib import Path

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

    Returns dict with keys: pushed, skipped, errors, would_push (dry_run only), cursor
    """
    state = load_state(kb_dir)
    pending_ids = set(state.get("pending_pattern_ids", []))

    if not pending_ids:
        return {"pushed": 0, "skipped": 0, "errors": 0, "cursor": None}

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
