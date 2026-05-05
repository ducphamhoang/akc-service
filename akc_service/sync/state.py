import json
from pathlib import Path

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
        # Deep copy to avoid mutating the default
        return json.loads(json.dumps(_DEFAULT_STATE))
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # Deep copy to avoid mutating the default
        return json.loads(json.dumps(_DEFAULT_STATE))


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
