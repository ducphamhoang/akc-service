#!/usr/bin/env python3
"""
AKC Learning Integration Module
Phase 2, Task 1 + Phase 4, Wave 4 - Orchestrator → Learning Engine Bridge

Handles asynchronous application of confidence deltas from agent task outcomes.
Called from orchestrator after each task completion (success or failure).

Phase 4 additions (Wave 4):
- async_update_confidence: per-pattern outcome-based confidence update with tier transitions
- sync_update_critical_patterns: synchronous update for patterns below 0.50 threshold
- determine_tier: public tier classification API
- log_confidence_update: immutable audit trail appender
- Helper functions: append_pattern_version, find_pattern_by_id, generate_version_id

Usage:
    python learning_integration.py --apply-delta --task-result '<json>'
    python learning_integration.py --check-latency
    python learning_integration.py --async-update  (stdin: JSON with task_result + pattern_outcomes)
    python learning_integration.py --sync-update   (stdin: JSON with task_result + critical_patterns + pattern_outcomes)
"""

import argparse
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"


# ─── Helper Functions ───────────────────────────────────────────────────────────

def now_iso() -> str:
    """Return current time in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_history_id() -> str:
    """Generate a unique history ID from current timestamp."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    return f"ch-{ts}"


def _confidence_tier(confidence: float) -> str:
    """Classify confidence into a tier with explicit boundary handling."""
    if confidence >= 0.85:
        return "gold"
    elif confidence >= 0.70:
        return "production"
    elif confidence >= 0.50:
        return "experimental"
    else:
        return "demoted"


def determine_tier(confidence: float) -> str:
    """
    Public API for confidence tier classification (Phase 4).

    Maps confidence score to tier:
    - gold       : >= 0.85
    - production : >= 0.70
    - experimental: >= 0.50
    - demoted    : < 0.50

    Args:
        confidence: Float confidence score in [0.0, 1.0].

    Returns:
        Tier string: "gold" | "production" | "experimental" | "demoted"
    """
    return _confidence_tier(confidence)


def generate_version_id(pattern_id: str) -> str:
    """Generate a unique version ID for a pattern update (Phase 4)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return f"{pattern_id}_v{ts}"


def find_pattern_by_id(pattern_id: str, patterns: list) -> dict | None:
    """Find the last occurrence of a pattern by ID in a loaded pattern list (Phase 4)."""
    result = None
    for p in patterns:
        if p.get("id") == pattern_id:
            result = p  # Keep last occurrence (most recent version)
    return result


def append_pattern_version(pattern: dict) -> None:
    """
    Append a new pattern version to patterns.jsonl (immutable append-only, Phase 4).

    Updates the confidence, confidence_tier, updated_at, and version fields
    of the pattern before appending. Uses advisory file lock to prevent concurrent append corruption.
    """
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PATTERNS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(pattern) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def log_confidence_update(entry: dict) -> None:
    """
    Append a confidence update entry to confidence_history.jsonl audit trail (Phase 4).

    Entry format:
        timestamp, pattern_id, old_confidence, new_confidence, confidence_delta,
        task_id, task_status, tier_change (if any)

    Uses advisory file lock to prevent concurrent append corruption.
    """
    CONFIDENCE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIDENCE_HISTORY_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(entry) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_all_patterns() -> list:
    """Load all patterns from patterns.jsonl."""
    patterns = []
    if not PATTERNS_PATH.exists():
        return patterns
    with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    patterns.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return patterns


def save_all_patterns(patterns: list) -> None:
    """Save all patterns to patterns.jsonl (overwrite)."""
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PATTERNS_PATH, "w", encoding="utf-8") as f:
        for p in patterns:
            f.write(json.dumps(p) + "\n")


def append_confidence_history(entry: dict) -> None:
    """Append a single entry to confidence_history.jsonl (immutable append)."""
    CONFIDENCE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIDENCE_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Phase 4 Wave 4: Async/Sync Confidence Updates ──────────────────────────────

def async_update_confidence(task_result: dict, pattern_outcomes: dict) -> dict:
    """
    Asynchronous confidence update for non-critical patterns (Phase 4, Wave 4).

    Called from background subprocess (spawn_async_kb_update) after agent task completion.

    Logic:
    - Success: +0.05 to pattern confidence (reward signal)
    - Failure: -0.10 to pattern confidence (penalty signal)
    - Auto-promote on 0.70+ (production tier); auto-demote on <0.50 (demoted tier)
    - Creates immutable version snapshot per pattern update
    - Logs all updates to confidence_history.jsonl audit trail

    Args:
        task_result: Complete task result from agent (task_id, status, timestamp, etc.)
        pattern_outcomes: {"pattern-id": {"used": bool, "success": bool, ...}}

    Returns:
        {
            "status": "complete",
            "patterns_updated": int,
            "promotions": [{"pattern_id": "...", "from_tier": "...", "to_tier": "..."}],
            "demotions": [{"pattern_id": "...", "from_tier": "...", "to_tier": "..."}]
        }
    """
    start_time = time.time()
    task_id = task_result.get("task_id", "unknown")
    task_status = task_result.get("status", "unknown")

    print(f"[ASYNC] Starting async confidence update for task={task_id}", file=sys.stderr)

    # Load current patterns (deduplicated to latest version per ID)
    all_patterns = load_all_patterns()

    promotions = []
    demotions = []
    updated_count = 0

    for pattern_id, outcome in pattern_outcomes.items():
        pattern = find_pattern_by_id(pattern_id, all_patterns)
        if not pattern:
            print(f"[ASYNC] WARNING: Pattern {pattern_id} not found; skipping", file=sys.stderr)
            continue

        success = outcome.get("success", False)
        confidence_delta = 0.05 if success else -0.10

        old_confidence = pattern.get("confidence", 0.5)
        new_confidence = round(max(0.0, min(0.95, old_confidence + confidence_delta)), 4)

        old_tier = determine_tier(old_confidence)
        new_tier = determine_tier(new_confidence)

        # Track tier transitions
        if old_tier != new_tier:
            if new_confidence >= old_confidence:
                promotions.append({
                    "pattern_id": pattern_id,
                    "from_tier": old_tier,
                    "to_tier": new_tier,
                    "confidence": new_confidence
                })
                print(
                    f"[ASYNC] Pattern {pattern_id} PROMOTED: {old_tier} → {new_tier} "
                    f"(conf {new_confidence:.4f})",
                    file=sys.stderr
                )
            else:
                demotions.append({
                    "pattern_id": pattern_id,
                    "from_tier": old_tier,
                    "to_tier": new_tier,
                    "confidence": new_confidence
                })
                print(
                    f"[ASYNC] Pattern {pattern_id} DEMOTED: {old_tier} → {new_tier} "
                    f"(conf {new_confidence:.4f})",
                    file=sys.stderr
                )

        # Build updated pattern with new version entry
        version_info = pattern.get("version", {"current": "v1", "history": []})
        current_version = version_info.get("current", "v1")
        try:
            version_num = int(current_version[1:])
            next_version = f"v{version_num + 1}"
        except (ValueError, IndexError):
            next_version = "v2"

        snapshot = {
            "version_id": next_version,
            "confidence_snapshot": new_confidence,
            "timestamp": now_iso(),
            "change_reason": f"Task {task_id}: outcome={'success' if success else 'failed'} (async)",
            "changed_by": "learning_loop_async",
            "tier": new_tier
        }

        history = version_info.get("history", [])
        history.append(snapshot)
        version_info = {**version_info, "current": next_version, "history": history}

        updated_pattern = {
            **pattern,
            "confidence": new_confidence,
            "confidence_tier": new_tier,
            "updated_at": now_iso(),
            "version": version_info
        }

        # Append immutable version to patterns.jsonl
        append_pattern_version(updated_pattern)

        # Log to audit trail
        log_confidence_update({
            "history_id": make_history_id(),
            "timestamp": now_iso(),
            "pattern_id": pattern_id,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "confidence_delta": confidence_delta,
            "task_id": task_id,
            "task_status": task_status,
            "tier_change": f"{old_tier} → {new_tier}" if old_tier != new_tier else "none",
            "update_type": "async"
        })

        updated_count += 1

    elapsed_ms = int((time.time() - start_time) * 1000)
    print(
        f"[ASYNC] Confidence update complete: {updated_count} patterns updated in {elapsed_ms}ms",
        file=sys.stderr
    )

    return {
        "status": "complete",
        "patterns_updated": updated_count,
        "promotions": promotions,
        "demotions": demotions,
        "latency_ms": elapsed_ms
    }


def sync_update_critical_patterns(task_result: dict, critical_patterns: list, pattern_outcomes: dict) -> dict:
    """
    Synchronous confidence update for critical patterns (confidence < 0.50, Phase 4, Wave 4).

    Called synchronously (blocking) when patterns fall below critical threshold.
    Must complete within 30 seconds (enforced by caller timeout in trigger_sync_kb_update).

    Produces the same immutable pattern version entries and audit trail as async_update_confidence.

    Args:
        task_result: Complete task result from agent (task_id, status, etc.)
        critical_patterns: List of pattern IDs that have confidence < 0.50.
        pattern_outcomes: {"pattern-id": {"used": bool, "success": bool, ...}}

    Returns:
        {
            "status": "complete",
            "patterns_updated": int,
            "critical_demotions": [{"pattern_id": "...", "confidence": float, "reason": "..."}]
        }
    """
    start_time = time.time()
    task_id = task_result.get("task_id", "unknown")
    task_status = task_result.get("status", "unknown")

    print(
        f"[SYNC] Starting SYNC update for {len(critical_patterns)} critical patterns (task={task_id})",
        file=sys.stderr
    )

    all_patterns = load_all_patterns()
    critical_demotions = []
    updated_count = 0

    for pattern_id in critical_patterns:
        outcome = pattern_outcomes.get(pattern_id, {})
        pattern = find_pattern_by_id(pattern_id, all_patterns)
        if not pattern:
            print(f"[SYNC] WARNING: Critical pattern {pattern_id} not found; skipping", file=sys.stderr)
            continue

        success = outcome.get("success", False)
        confidence_delta = 0.05 if success else -0.10

        old_confidence = pattern.get("confidence", 0.5)
        new_confidence = round(max(0.0, min(0.95, old_confidence + confidence_delta)), 4)
        old_tier = determine_tier(old_confidence)
        new_tier = determine_tier(new_confidence)

        # Track severe demotions (still below or dropping further below 0.50)
        if new_confidence < 0.50:
            critical_demotions.append({
                "pattern_id": pattern_id,
                "old_confidence": old_confidence,
                "confidence": new_confidence,
                "reason": "critical_threshold_violated",
                "tier": new_tier
            })
            print(
                f"[SYNC] CRITICAL DEMOTION: {pattern_id} confidence {old_confidence:.4f} → {new_confidence:.4f} "
                f"(below 0.50 threshold)",
                file=sys.stderr
            )

        # Build updated pattern
        version_info = pattern.get("version", {"current": "v1", "history": []})
        current_version = version_info.get("current", "v1")
        try:
            version_num = int(current_version[1:])
            next_version = f"v{version_num + 1}"
        except (ValueError, IndexError):
            next_version = "v2"

        snapshot = {
            "version_id": next_version,
            "confidence_snapshot": new_confidence,
            "timestamp": now_iso(),
            "change_reason": f"SYNC UPDATE: critical pattern — Task {task_id}",
            "changed_by": "learning_loop_sync",
            "tier": new_tier
        }

        history = version_info.get("history", [])
        history.append(snapshot)
        version_info = {**version_info, "current": next_version, "history": history}

        updated_pattern = {
            **pattern,
            "confidence": new_confidence,
            "confidence_tier": new_tier,
            "updated_at": now_iso(),
            "version": version_info
        }

        append_pattern_version(updated_pattern)

        log_confidence_update({
            "history_id": make_history_id(),
            "timestamp": now_iso(),
            "pattern_id": pattern_id,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "confidence_delta": confidence_delta,
            "task_id": task_id,
            "task_status": task_status,
            "tier_change": f"{old_tier} → {new_tier}" if old_tier != new_tier else "none",
            "update_type": "sync_critical"
        })

        updated_count += 1

    elapsed_ms = int((time.time() - start_time) * 1000)
    print(
        f"[SYNC] Critical update complete: {updated_count} patterns updated in {elapsed_ms}ms",
        file=sys.stderr
    )

    return {
        "status": "complete",
        "patterns_updated": updated_count,
        "critical_demotions": critical_demotions,
        "latency_ms": elapsed_ms
    }


# ─── Task Result Validation ─────────────────────────────────────────────────────

def validate_task_result(task_result: dict) -> tuple[bool, str]:
    """Validate task result JSON has required fields."""
    required_fields = ["schema_version", "task_id", "status", "timestamp"]
    for field in required_fields:
        if field not in task_result:
            return False, f"Missing required field: {field}"

    if task_result.get("schema_version") != "1.0":
        return False, f"Invalid schema_version: {task_result.get('schema_version')}"

    if task_result.get("status") not in ["success", "failed"]:
        return False, f"Invalid status: {task_result.get('status')}"

    return True, ""


# ─── Confidence Delta Application ────────────────────────────────────────────────

def apply_confidence_delta(task_result: dict) -> dict:
    """
    Apply confidence delta to patterns based on task outcome.

    Args:
        task_result: Task result JSON from orchestrator with:
            - status: "success" or "failed"
            - akc_context.knowledge_patterns_active: list of pattern IDs
            - timestamp: ISO 8601 time of task completion

    Returns:
        dict with status, patterns_updated, latency_ms
    """
    start_time = time.time()

    # Validate task result
    is_valid, error_msg = validate_task_result(task_result)
    if not is_valid:
        print(f"ERROR: {error_msg}", file=sys.stderr)
        return {
            "status": "error",
            "error": error_msg,
            "patterns_updated": 0,
            "latency_ms": 0
        }

    status = task_result.get("status")
    timestamp = task_result.get("timestamp")

    # Determine delta based on status
    if status == "success":
        delta = 0.05
    elif status == "failed":
        delta = -0.10
    else:
        return {
            "status": "error",
            "error": f"Unknown status: {status}",
            "patterns_updated": 0,
            "latency_ms": 0
        }

    # Extract patterns to update from akc_context
    akc_context = task_result.get("akc_context", {})
    if not akc_context.get("akc_enabled", False):
        print("WARNING: AKC disabled in task result; skipping confidence updates", file=sys.stderr)
        end_time = time.time()
        return {
            "status": "success",
            "patterns_updated": 0,
            "latency_ms": int((end_time - start_time) * 1000),
            "message": "AKC disabled"
        }

    active_patterns = akc_context.get("knowledge_patterns_active", [])
    if not active_patterns:
        print(f"WARNING: No active patterns in akc_context; skipping updates", file=sys.stderr)
        end_time = time.time()
        return {
            "status": "success",
            "patterns_updated": 0,
            "latency_ms": int((end_time - start_time) * 1000),
            "message": "No patterns to update"
        }

    # Load all patterns
    patterns = load_all_patterns()
    patterns_updated = 0

    # For each active pattern, apply delta and create version snapshot
    for pattern_id in active_patterns:
        pattern = next((p for p in patterns if p.get("id") == pattern_id), None)

        if not pattern:
            print(f"WARNING: Pattern {pattern_id} not found in KB; skipping", file=sys.stderr)
            continue

        # Get old values
        old_confidence = pattern.get("confidence", 0.5)
        old_tier = _confidence_tier(old_confidence)

        # Apply delta with clamping to [0.0, 0.95]
        new_confidence = round(max(0.0, min(0.95, old_confidence + delta)), 4)
        new_tier = _confidence_tier(new_confidence)

        # Check if tier changed
        tier_changed = old_tier != new_tier

        # Update pattern confidence and tier
        pattern["confidence"] = new_confidence
        pattern["confidence_tier"] = new_tier
        pattern["updated_at"] = now_iso()

        # Create version snapshot (increment version)
        version_info = pattern.get("version", {"current": "v1", "history": []})
        current_version = version_info.get("current", "v1")

        # Parse version number
        try:
            version_num = int(current_version[1:])  # Extract number from "v1"
            next_version = f"v{version_num + 1}"
        except (ValueError, IndexError):
            next_version = "v2"  # Default fallback

        # Create snapshot
        snapshot = {
            "version_id": next_version,
            "confidence_snapshot": new_confidence,
            "timestamp": now_iso(),
            "change_reason": f"Task outcome feedback: {status}",
            "changed_by": "learning_loop",
            "tier": new_tier
        }

        # Append to history (immutable)
        version_info["history"].append(snapshot)
        version_info["current"] = next_version
        pattern["version"] = version_info

        # Record delta application time for latency measurement
        delta_applied_time = now_iso()

        # Calculate latency from task completion to delta application
        latency_ms = 0
        try:
            task_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            delta_time = datetime.fromisoformat(delta_applied_time.replace("Z", "+00:00"))
            latency_ms = int((delta_time - task_time).total_seconds() * 1000)
        except (ValueError, AttributeError):
            pass  # If timestamp parse fails, latency_ms stays 0

        # Append to confidence history
        history_entry = {
            "history_id": make_history_id(),
            "timestamp": now_iso(),
            "pattern_id": pattern_id,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "delta": delta,
            "task_status": status,
            "reason": f"Task outcome feedback: {status}",
            "new_tier": new_tier,
            "tier_changed": tier_changed,
            "old_tier": old_tier,
            "task_completion_time": timestamp,
            "delta_applied_time": delta_applied_time,
            "latency_ms": latency_ms
        }

        append_confidence_history(history_entry)

        # Log update
        print(
            f"Applied delta {delta:+.2f} to pattern {pattern_id}: "
            f"{old_confidence:.4f}→{new_confidence:.4f} ({old_tier}→{new_tier})",
            file=sys.stderr
        )

        patterns_updated += 1

    # Save all updated patterns
    if patterns_updated > 0:
        save_all_patterns(patterns)

    # Calculate execution time
    end_time = time.time()
    latency_ms = int((end_time - start_time) * 1000)

    # Log summary
    print(
        f"Applied delta {delta:+.2f} to {patterns_updated} patterns",
        file=sys.stderr
    )

    return {
        "status": "success",
        "patterns_updated": patterns_updated,
        "latency_ms": latency_ms
    }


# ─── Latency Monitoring ─────────────────────────────────────────────────────────

def check_latency() -> dict:
    """
    Check learning latency compliance (<5 minutes SLA).

    Returns:
        dict with latency statistics and SLA status
    """
    if not CONFIDENCE_HISTORY_PATH.exists():
        return {
            "measurement_time": now_iso(),
            "sample_count": 0,
            "latency_stats": {
                "min_ms": 0,
                "max_ms": 0,
                "avg_ms": 0,
                "p95_ms": 0,
                "over_sla_count": 0
            },
            "sla_status": "UNKNOWN"
        }

    # Read confidence history
    entries = []
    with open(CONFIDENCE_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    entry = json.loads(line)
                    latency = entry.get("latency_ms", 0)
                    entries.append(latency)
                except json.JSONDecodeError:
                    pass

    if not entries:
        return {
            "measurement_time": now_iso(),
            "sample_count": 0,
            "latency_stats": {
                "min_ms": 0,
                "max_ms": 0,
                "avg_ms": 0,
                "p95_ms": 0,
                "over_sla_count": 0
            },
            "sla_status": "UNKNOWN"
        }

    # Calculate statistics
    entries_sorted = sorted(entries)
    min_ms = min(entries)
    max_ms = max(entries)
    avg_ms = int(sum(entries) / len(entries))

    # Calculate p95
    p95_index = int(len(entries_sorted) * 0.95)
    p95_ms = entries_sorted[p95_index] if p95_index < len(entries_sorted) else max_ms

    # Count over SLA (5 minutes = 300,000 ms)
    sla_threshold_ms = 300000
    over_sla_count = sum(1 for e in entries if e > sla_threshold_ms)

    # Determine SLA status
    sla_status = "HEALTHY" if over_sla_count == 0 else "WARNING"

    return {
        "measurement_time": now_iso(),
        "sample_count": len(entries),
        "latency_stats": {
            "min_ms": min_ms,
            "max_ms": max_ms,
            "avg_ms": avg_ms,
            "p95_ms": p95_ms,
            "over_sla_count": over_sla_count
        },
        "sla_status": sla_status
    }


# ─── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Learning Integration — Orchestrator → Learning Engine bridge"
    )
    parser.add_argument(
        "--apply-delta",
        action="store_true",
        help="Apply confidence delta from task result"
    )
    parser.add_argument(
        "--task-result",
        help="Task result JSON string with status and akc_context"
    )
    parser.add_argument(
        "--check-latency",
        action="store_true",
        help="Check learning latency compliance (<5 min SLA)"
    )
    # Phase 4 flags
    parser.add_argument(
        "--async-update",
        action="store_true",
        help="(Phase 4) Read task_result + pattern_outcomes from stdin; run async_update_confidence"
    )
    parser.add_argument(
        "--sync-update",
        action="store_true",
        help="(Phase 4) Read task_result + critical_patterns + pattern_outcomes from stdin; run sync_update_critical_patterns"
    )

    args = parser.parse_args()

    # Phase 4 entry points
    if args.async_update:
        run_async_update_from_stdin()
        return  # run_async_update_from_stdin calls sys.exit

    if args.sync_update:
        run_sync_update_from_stdin()
        return  # run_sync_update_from_stdin calls sys.exit

    if args.apply_delta:
        if not args.task_result:
            print("ERROR: --apply-delta requires --task-result '<json>'", file=sys.stderr)
            sys.exit(1)

        try:
            task_result = json.loads(args.task_result)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in --task-result: {e}", file=sys.stderr)
            sys.exit(1)

        result = apply_confidence_delta(task_result)
        print(json.dumps(result))
        sys.exit(0 if result.get("status") == "success" else 1)

    if args.check_latency:
        result = check_latency()
        print(json.dumps(result))
        sys.exit(0)

    parser.print_help()
    sys.exit(0)


# ─── Phase 4 CLI Entry Points ────────────────────────────────────────────────────

def run_async_update_from_stdin() -> None:
    """
    Phase 4: Read JSON from stdin and run async_update_confidence.
    Called by spawn_async_kb_update subprocess via --async-update flag.

    Stdin JSON format:
        {
            "task_result": {...},
            "pattern_outcomes": {"pat-id": {"used": bool, "success": bool}}
        }
    """
    import json as _json
    try:
        payload = _json.loads(sys.stdin.read())
    except _json.JSONDecodeError as e:
        print(f"ERROR: Invalid stdin JSON: {e}", file=sys.stderr)
        sys.exit(1)

    task_result = payload.get("task_result", {})
    pattern_outcomes = payload.get("pattern_outcomes", {})

    if not pattern_outcomes:
        # Fall back to full task-result-based delta (legacy path)
        result = apply_confidence_delta(task_result)
    else:
        result = async_update_confidence(task_result, pattern_outcomes)

    print(_json.dumps(result))
    sys.exit(0 if result.get("status") == "complete" else 1)


def run_sync_update_from_stdin() -> None:
    """
    Phase 4: Read JSON from stdin and run sync_update_critical_patterns.
    Called by trigger_sync_kb_update subprocess via --sync-update flag.

    Stdin JSON format:
        {
            "task_result": {...},
            "critical_patterns": ["pat-id-1", "pat-id-2"],
            "pattern_outcomes": {"pat-id": {"used": bool, "success": bool}}
        }
    """
    import json as _json
    try:
        payload = _json.loads(sys.stdin.read())
    except _json.JSONDecodeError as e:
        print(f"ERROR: Invalid stdin JSON: {e}", file=sys.stderr)
        sys.exit(1)

    task_result = payload.get("task_result", {})
    critical_patterns = payload.get("critical_patterns", [])
    pattern_outcomes = payload.get("pattern_outcomes", {})

    result = sync_update_critical_patterns(task_result, critical_patterns, pattern_outcomes)
    print(_json.dumps(result))
    sys.exit(0 if result.get("status") == "complete" else 1)


if __name__ == "__main__":
    main()
