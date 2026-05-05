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

    Conflict resolution (default): keep local if local_conf >= remote_conf.
    With overwrite_local=True: remote always wins.

    Returns dict with keys: pulled, conflicts, errors
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
