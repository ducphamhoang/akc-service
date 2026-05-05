#!/usr/bin/env python3
"""
AKC Detection Engine
Phase 1, Wave 1 - Tasks 1.1-1.4

Captures failure signals, traces patterns, scores confidence, runs isolation reruns.

Usage:
    python detection_engine.py --capture --task-result '<json>'
    python detection_engine.py --score --failure-id <id>
    python detection_engine.py --isolate-rerun --failure-id <id> --task-id <id>
"""

import argparse
import json
import os
import re
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))


# ─── Paths ─────────────────────────────────────────────────────────────────────

FAILURE_INDEX_PATH = KB_DIR / "failure_index.jsonl"
PATTERNS_PATH = KB_DIR / "patterns.jsonl"


# ─── Failure Markers ───────────────────────────────────────────────────────────

FAILURE_MARKERS = [
    r"FAILED",
    r"ERROR",
    r"Traceback \(most recent call last\)",
    r"AssertionError",
    r"AttributeError",
    r"ValueError",
    r"TypeError",
    r"KeyError",
    r"NullPointerException",
    r"assertion failed",
    r"test.*fail",
    r"validation.*fail",
    r"SCRIPT ERROR",
    r"Parse Error",
    r"Invalid call",
    r"Node not found",
    r"Cannot connect",
    r"cannot connect",
    r"is null",
    r"is not",
    r"does not exist",
    r"could not",
    r"unexpected",
]

FAILURE_MARKER_PATTERN = re.compile(
    "|".join(FAILURE_MARKERS), re.IGNORECASE
)

# Error category heuristics: (regex, category)
ERROR_CATEGORIES = [
    (re.compile(r"physics|collision|layer|shape|body", re.I), "physics_configuration"),
    (re.compile(r"signal|emit|connect", re.I), "signal_connection"),
    (re.compile(r"health|damage|take_damage|hp", re.I), "health_tracking"),
    (re.compile(r"animation|state|anim_player", re.I), "animation_state"),
    (re.compile(r"navigation|pathfind|nav_agent", re.I), "ai_behavior"),
    (re.compile(r"scene|node|tscn|add_child|get_node", re.I), "scene_lifecycle"),
    (re.compile(r"resource|load|preload|import", re.I), "resource_loading"),
    (re.compile(r"ui|label|button|hud|control", re.I), "ui_update"),
]


# ─── Pattern Keyword Map ────────────────────────────────────────────────────────
# Maps log keywords -> (entity, component, pattern_type) for attribution

PATTERN_KEYWORDS = [
    # Player patterns
    (re.compile(r"player.*health|health.*player|HealthComponent.*player", re.I),
     "player", "HealthComponent", "health_tracking"),
    (re.compile(r"player.*move|move.*player|MovementComponent.*player", re.I),
     "player", "MovementComponent", "physics_configuration"),
    (re.compile(r"player.*signal|player.*emit", re.I),
     "player", "SignalComponent", "signal_emission"),

    # Enemy Knight
    (re.compile(r"knight.*health|enemy_knight.*health", re.I),
     "enemy_knight", "HealthComponent", "health_tracking"),
    (re.compile(r"knight.*collision|collision.*knight", re.I),
     "enemy_knight", "PhysicsComponent", "collision_detection"),
    (re.compile(r"knight.*anim|anim.*knight", re.I),
     "enemy_knight", "AnimationComponent", "animation_state"),

    # Enemy Mage
    (re.compile(r"mage.*health|enemy_mage.*health", re.I),
     "enemy_mage", "HealthComponent", "health_tracking"),
    (re.compile(r"mage.*spell|spell.*cast", re.I),
     "enemy_mage", "CombatComponent", "signal_emission"),

    # Minion
    (re.compile(r"minion.*health|minion.*damage", re.I),
     "minion", "HealthComponent", "health_tracking"),
    (re.compile(r"minion.*spawn|minion.*summon", re.I),
     "minion", "scene_structure", "scene_lifecycle"),

    # Global / cross-component
    (re.compile(r"PhysicsLayers|collision_layer|collision_mask", re.I),
     "global", "PhysicsComponent", "collision_detection"),
    (re.compile(r"EventSystem|event_bus|emit_event", re.I),
     "global", "EventSystem", "signal_emission"),
    (re.compile(r"autoload|singleton", re.I),
     "global", "autoload", "scene_lifecycle"),
]

# Known violations for Tier 2 (violation confidence)
KNOWN_VIOLATIONS = {
    "health_tracking": [
        re.compile(r"negative.*health|health.*negative|underflow|below.?0", re.I),
        re.compile(r"health.*not.*clamped|unclamped health", re.I),
    ],
    "collision_detection": [
        re.compile(r"integer.*literal.*collision|collision_layer\s*=\s*\d+", re.I),
        re.compile(r"hardcoded.*layer|layer.*hardcoded", re.I),
    ],
    "signal_emission": [
        re.compile(r"signal.*not.*connected|unconnected.*signal", re.I),
        re.compile(r"missing.*signal|signal.*missing", re.I),
    ],
    "animation_state": [
        re.compile(r"animation.*not.*found|missing.*animation", re.I),
        re.compile(r"state.*machine.*reset|invalid.*state", re.I),
    ],
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_failure_id(task_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_hash = hashlib.md5(f"{task_id}-{ts}".encode()).hexdigest()[:6]
    return f"fail-{ts}-{short_hash}"


def load_patterns() -> list:
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


def load_failure(failure_id: str) -> dict | None:
    """Load a failure entry by failure_id from failure_index.jsonl."""
    if not FAILURE_INDEX_PATH.exists():
        return None
    with open(FAILURE_INDEX_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("failure_id") == failure_id:
                    return entry
            except json.JSONDecodeError:
                pass
    return None


def update_failure(failure_id: str, updates: dict) -> bool:
    """Update an existing failure entry in failure_index.jsonl."""
    if not FAILURE_INDEX_PATH.exists():
        return False

    lines = []
    found = False
    with open(FAILURE_INDEX_PATH, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            try:
                entry = json.loads(stripped)
                if entry.get("failure_id") == failure_id:
                    entry.update(updates)
                    lines.append(json.dumps(entry) + "\n")
                    found = True
                else:
                    lines.append(line)
            except json.JSONDecodeError:
                lines.append(line)

    if found:
        with open(FAILURE_INDEX_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return found


def append_failure(entry: dict) -> None:
    """Append a failure entry to failure_index.jsonl."""
    FAILURE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FAILURE_INDEX_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Task 1.1: Failure Signal Capture ──────────────────────────────────────────

def extract_failure_markers(text: str) -> list:
    """Find failure markers in text."""
    matches = []
    for m in FAILURE_MARKER_PATTERN.finditer(text):
        matches.append(m.group())
    return list(set(matches))


def infer_error_category(error_message: str, logs: str) -> str:
    """Infer error category from message and logs."""
    combined = f"{error_message} {logs}"
    for pattern, category in ERROR_CATEGORIES:
        if pattern.search(combined):
            return category
    return "unknown"


def capture_failure(task_result_json: str) -> dict:
    """
    Task 1.1: Capture failure signals from a task result.

    Args:
        task_result_json: JSON string with task result {status, exit_code, logs, output, duration}
    Returns:
        dict with failure_id and entry details
    """
    try:
        task_result = json.loads(task_result_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid task result JSON: {e}", file=sys.stderr)
        sys.exit(1)

    task_id = task_result.get("task_id", "unknown-task")
    status = task_result.get("status", "failed")
    logs = task_result.get("logs", "")
    output = task_result.get("output", "")
    error_message = task_result.get("error", "")
    agent = task_result.get("agent", "unknown")
    files_modified = task_result.get("files_modified", [])

    # Combine all text for marker extraction
    combined_text = f"{error_message}\n{logs}\n{output}"
    failure_markers_found = extract_failure_markers(combined_text)

    # Error category
    error_category = infer_error_category(error_message, logs)

    # Generate failure ID
    failure_id = make_failure_id(task_id)

    # Task 1.2: Pattern usage tracing (initial attribution with low confidence)
    inferred_pattern_id, attributed_entity, attributed_component, attributed_type = \
        trace_pattern_usage(combined_text, logs)

    entry = {
        "failure_id": failure_id,
        "task_id": task_id,
        "timestamp": task_result.get("timestamp", now_iso()),
        "agent": agent,
        "status": status,
        "exit_code": task_result.get("exit_code", -1),
        "error_message": error_message,
        "error_category": error_category,
        "failure_markers": failure_markers_found[:5],  # cap at 5
        "files_modified": files_modified,
        "inferred_pattern_id": inferred_pattern_id,
        "attributed_entity": attributed_entity,
        "attributed_component": attributed_component,
        "attributed_pattern_type": attributed_type,
        "attribution_confidence": 0.0,  # will be set by scorer
        "attribution_method": "initial_capture",
        "isolation_rerun_required": False,
        "rerun_confidence": None,
        "final_confidence": None,
        "confidence_tier": None,
        "action_required": "pending_scoring",
        "schema_version": "v2",
    }

    append_failure(entry)
    return {"failure_id": failure_id, "entry": entry}


# ─── Task 1.2: Pattern Usage Tracer ────────────────────────────────────────────

def trace_pattern_usage(
    combined_text: str,
    logs: str
) -> tuple:
    """
    Task 1.2: Determine which pattern (if any) the agent used based on log text.

    Returns:
        (pattern_id, entity, component, pattern_type) or (None, None, None, None)
    """
    patterns = load_patterns()

    # First: check if any known pattern_id appears directly in logs
    if patterns:
        for p in patterns:
            pid = p.get("id", "")
            if pid and pid in combined_text:
                return (
                    pid,
                    p.get("entity"),
                    p.get("component"),
                    p.get("pattern_type"),
                )

    # Second: keyword matching against PATTERN_KEYWORDS
    for kw_pattern, entity, component, ptype in PATTERN_KEYWORDS:
        if kw_pattern.search(combined_text):
            # Try to find a matching pattern in KB
            matched_pid = None
            for p in patterns:
                if (p.get("entity") == entity and
                        p.get("component") == component and
                        p.get("pattern_type") == ptype):
                    matched_pid = p.get("id")
                    break

            return (matched_pid, entity, component, ptype)

    return (None, None, None, None)


# ─── Task 1.3: Confidence Scorer ───────────────────────────────────────────────

def score_confidence(failure: dict) -> dict:
    """
    Task 1.3: 3-tier confidence scoring.

    Tiers:
      Tier 1 (Usage confidence): pattern name in logs → +0.2, explicitly skipped → -0.1
      Tier 2 (Violation confidence): failure reason matches known violations → +0.3
      Tier 3 (Symptom matching): error matches historical failure signatures → +0.2/-0.2

    Returns:
        dict with tier scores and final_confidence
    """
    pattern_id = failure.get("inferred_pattern_id")
    pattern_type = failure.get("attributed_pattern_type", "")
    error_message = failure.get("error_message", "")
    error_category = failure.get("error_category", "")
    combined_text = f"{error_message} {failure.get('failure_markers', '')}"

    patterns = load_patterns()
    pattern = None
    if pattern_id:
        for p in patterns:
            if p.get("id") == pattern_id:
                pattern = p
                break

    tier1 = 0.0
    tier2 = 0.0
    tier3 = 0.0

    # ── Tier 1: Usage confidence ──────────────────────────────────────────────
    if pattern_id and pattern_id in combined_text:
        tier1 = 0.2  # pattern name explicitly appears in logs
    elif pattern_id is None:
        tier1 = 0.0  # no pattern identified
    elif failure.get("attributed_component") or failure.get("attributed_entity"):
        tier1 = 0.1  # partial match via keyword
    else:
        tier1 = 0.0

    # Check for explicit skipping
    if re.search(r"skip.*pattern|pattern.*skip|disable.*pattern", combined_text, re.I):
        tier1 -= 0.1

    # ── Tier 2: Violation confidence ──────────────────────────────────────────
    effective_type = pattern_type or error_category
    if effective_type in KNOWN_VIOLATIONS:
        for violation_re in KNOWN_VIOLATIONS[effective_type]:
            if violation_re.search(combined_text):
                tier2 = min(tier2 + 0.15, 0.3)  # Clamp per iteration, not after

    # ── Tier 3: Symptom matching ──────────────────────────────────────────────
    if pattern:
        # Does the error message match known incorrect examples for this pattern?
        example_incorrect = pattern.get("example_incorrect", "")
        rule = pattern.get("rule", "")

        # Simple heuristic: shared keywords between error and known bad code
        if example_incorrect:
            error_words = set(re.findall(r"\w+", error_message.lower()))
            example_words = set(re.findall(r"\w+", example_incorrect.lower()))
            overlap = error_words & example_words - {"the", "a", "and", "or", "in", "to"}
            if len(overlap) >= 3:
                tier3 = 0.2
            elif len(overlap) >= 1:
                tier3 = 0.05
    elif error_category != "unknown":
        # If error category matches attributed type, slight boost
        if error_category == pattern_type:
            tier3 = 0.1
        else:
            tier3 = -0.05

    # ── Final score ───────────────────────────────────────────────────────────
    # Clamp each tier independently before summing
    raw = min(tier1, 1.0) + min(tier2, 1.0) + min(tier3, 1.0)
    final_confidence = round(max(0.0, min(1.0, raw)), 4)

    # Determine action
    if final_confidence >= 0.85:
        action = "autonomous_fix"
    elif final_confidence >= 0.70:
        action = "isolation_rerun_required"
    else:
        action = "escalate_human_review"

    return {
        "tier1_usage": round(tier1, 4),
        "tier2_violation": round(tier2, 4),
        "tier3_symptom": round(tier3, 4),
        "final_confidence": final_confidence,
        "action_required": action,
        "confidence_tier": _confidence_tier_label(final_confidence),
    }


def _confidence_tier_label(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    elif confidence >= 0.70:
        return "medium"
    elif confidence >= 0.50:
        return "low"
    else:
        return "insufficient"


# ─── Task 1.4: Isolation Reruns ────────────────────────────────────────────────

def isolation_rerun(failure_id: str, task_id: str) -> dict:
    """
    Task 1.4: Isolation reruns for 0.70-0.84 confidence failures.

    Executes 3 simulated runs:
      Run 1: baseline (with suspected pattern)
      Run 2: pattern disabled
      Run 3: alternative pattern (fallback)

    In a real deployment this would call the task runner.
    For MVP: simulate by generating the rerun plan and recording methodology.

    Returns:
        dict with rerun plan, recommended confidence adjustment
    """
    failure = load_failure(failure_id)
    if not failure:
        return {"error": f"Failure {failure_id} not found", "success": False}

    pattern_id = failure.get("inferred_pattern_id")
    current_confidence = failure.get("attribution_confidence", 0.5)

    rerun_plan = {
        "failure_id": failure_id,
        "task_id": task_id,
        "pattern_id": pattern_id,
        "timestamp": now_iso(),
        "run_1": {
            "description": "Baseline — task with suspected pattern active",
            "pattern_state": "active",
            "expected": "failure (to confirm pattern is involved)",
            "simulated_outcome": "pending",
        },
        "run_2": {
            "description": "Pattern disabled — task without suspected pattern",
            "pattern_state": "disabled",
            "expected": "success (to confirm pattern causes failure)",
            "simulated_outcome": "pending",
        },
        "run_3": {
            "description": "Alternative pattern — use fallback if available",
            "pattern_state": "alternative",
            "expected": "success (confirms alternative works)",
            "simulated_outcome": "pending",
        },
        "analysis_rules": {
            "run1_fail_run2_pass": {"confidence_adjustment": +0.15, "conclusion": "pattern_is_cause"},
            "all_fail": {"confidence_adjustment": -0.10, "conclusion": "pattern_not_sole_cause"},
            "all_pass": {"confidence_adjustment": None, "conclusion": "non_deterministic_escalate"},
        },
        "recommended_action": "execute_reruns_in_isolation_environment",
        "note": (
            "This isolation rerun plan must be executed by the Orchestrator agent. "
            "Record actual outcomes in rerun_results field and call --score again."
        ),
    }

    # Update failure entry to note rerun is scheduled
    update_failure(failure_id, {
        "isolation_rerun_required": True,
        "rerun_plan": rerun_plan,
        "action_required": "isolation_rerun_scheduled",
    })

    return {
        "failure_id": failure_id,
        "rerun_plan": rerun_plan,
        "success": True,
    }


def process_rerun_results(
    failure_id: str,
    run1_outcome: str,
    run2_outcome: str,
    run3_outcome: str
) -> dict:
    """
    Process actual isolation rerun outcomes and adjust confidence.

    Args:
        run{N}_outcome: "pass" or "fail"
    """
    failure = load_failure(failure_id)
    if not failure:
        return {"error": f"Failure {failure_id} not found", "success": False}

    current_confidence = failure.get("attribution_confidence", 0.5)

    # Analysis
    if run1_outcome == "fail" and run2_outcome == "pass":
        adjustment = +0.15
        conclusion = "pattern_is_cause"
        action = "proceed_to_fix_generation"
    elif run1_outcome == "fail" and run2_outcome == "fail" and run3_outcome == "fail":
        adjustment = -0.10
        conclusion = "pattern_not_sole_cause"
        action = "escalate_human_review"
    elif run1_outcome == "pass" and run2_outcome == "pass" and run3_outcome == "pass":
        adjustment = None
        conclusion = "non_deterministic"
        action = "escalate_human_review"
    else:
        adjustment = 0.0
        conclusion = "inconclusive"
        action = "escalate_human_review"

    rerun_confidence = None
    if adjustment is not None:
        rerun_confidence = round(max(0.0, min(1.0, current_confidence + adjustment)), 4)

    updates = {
        "rerun_confidence": rerun_confidence,
        "rerun_conclusion": conclusion,
        "action_required": action,
        "rerun_results": {
            "run1": run1_outcome,
            "run2": run2_outcome,
            "run3": run3_outcome,
            "adjustment": adjustment,
            "conclusion": conclusion,
        },
    }
    if rerun_confidence is not None:
        updates["final_confidence"] = rerun_confidence
        updates["confidence_tier"] = _confidence_tier_label(rerun_confidence)

    update_failure(failure_id, updates)

    return {
        "failure_id": failure_id,
        "rerun_confidence": rerun_confidence,
        "conclusion": conclusion,
        "action_required": action,
        "success": True,
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Detection Engine — failure signal capture, pattern tracing, confidence scoring"
    )
    subparsers = parser.add_subparsers(dest="mode")

    # --capture mode
    cap = subparsers.add_parser("capture", help="Capture failure from task result JSON")
    cap.add_argument("--task-result", required=True, help="JSON string of task result")

    # Flat --capture flag (alternative CLI form expected by plan)
    parser.add_argument("--capture", action="store_true", help="Capture mode")
    parser.add_argument("--task-result", help="JSON string of task result")

    # --score mode
    parser.add_argument("--score", action="store_true", help="Score mode")
    parser.add_argument("--failure-id", help="failure_id to score or rerun")

    # --isolate-rerun mode
    parser.add_argument("--isolate-rerun", action="store_true", help="Isolation rerun mode")
    parser.add_argument("--task-id", help="task_id for rerun")
    parser.add_argument("--run1", help="Run 1 outcome: pass or fail")
    parser.add_argument("--run2", help="Run 2 outcome: pass or fail")
    parser.add_argument("--run3", help="Run 3 outcome: pass or fail")

    args = parser.parse_args()

    # ── --capture ──────────────────────────────────────────────────────────────
    if args.capture:
        if not args.task_result:
            print("ERROR: --capture requires --task-result", file=sys.stderr)
            sys.exit(1)
        result = capture_failure(args.task_result)
        # Auto-score after capture
        failure = load_failure(result["failure_id"])
        if failure:
            score = score_confidence(failure)
            update_failure(result["failure_id"], {
                "attribution_confidence": score["final_confidence"],
                "confidence_tier": score["confidence_tier"],
                "action_required": score["action_required"],
                "score_breakdown": {
                    "tier1_usage": score["tier1_usage"],
                    "tier2_violation": score["tier2_violation"],
                    "tier3_symptom": score["tier3_symptom"],
                },
            })
            result["score"] = score
        print(json.dumps(result, indent=2))
        return

    # ── --score ────────────────────────────────────────────────────────────────
    if args.score:
        if not args.failure_id:
            print("ERROR: --score requires --failure-id", file=sys.stderr)
            sys.exit(1)
        failure = load_failure(args.failure_id)
        if not failure:
            print(f"ERROR: Failure {args.failure_id} not found", file=sys.stderr)
            sys.exit(1)
        score = score_confidence(failure)
        update_failure(args.failure_id, {
            "attribution_confidence": score["final_confidence"],
            "confidence_tier": score["confidence_tier"],
            "action_required": score["action_required"],
            "score_breakdown": {
                "tier1_usage": score["tier1_usage"],
                "tier2_violation": score["tier2_violation"],
                "tier3_symptom": score["tier3_symptom"],
            },
        })
        print(json.dumps(score, indent=2))
        return

    # ── --isolate-rerun ────────────────────────────────────────────────────────
    if args.isolate_rerun:
        if not args.failure_id:
            print("ERROR: --isolate-rerun requires --failure-id", file=sys.stderr)
            sys.exit(1)

        # If rerun outcomes provided, process them
        if args.run1 and args.run2 and args.run3:
            result = process_rerun_results(
                args.failure_id, args.run1, args.run2, args.run3
            )
        else:
            # Generate the rerun plan
            task_id = args.task_id or "unknown-task"
            result = isolation_rerun(args.failure_id, task_id)

        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
