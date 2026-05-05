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
    print(f"Configured remote URL: {args.url}")
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
    push_p.add_argument("--min-confidence", type=float, default=sync_cfg.MIN_CONFIDENCE, dest="min_confidence")
    push_p.add_argument("--batch-size", type=int, default=sync_cfg.PUSH_BATCH, dest="batch_size")

    pull_p = sub.add_parser("pull", help="Pull remote patterns into local KB")
    pull_p.add_argument("--dry-run", action="store_true")
    pull_p.add_argument("--since", default=None)
    pull_p.add_argument("--overwrite-local", action="store_true", dest="overwrite_local")

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
