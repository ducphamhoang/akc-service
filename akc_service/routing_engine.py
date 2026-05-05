#!/usr/bin/env python3
"""
AKC Routing Engine
Phase 3, Plan 02 - Task 1

Tiered routing decision tree for fix candidates.
Routes candidates to Tier 1 (autonomous), Tier 2 (semi-autonomous),
Tier 3 (human review), or Escalation based on confidence scores
and risk assessment.

Usage:
    python routing_engine.py --route-candidate --candidate-id '<id>'
    python routing_engine.py --batch-route --candidate-list '<json>'
    python routing_engine.py --get-tier-stats
    python routing_engine.py --test-routing
"""

import argparse
import json
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))
from threading import Lock

ROUTING_DIR = KB_DIR / "routing"

# Queue file paths
TIER_1_QUEUE = ROUTING_DIR / "tier_1_queue.jsonl"
TIER_2_QUEUE = ROUTING_DIR / "tier_2_queue.jsonl"
TIER_3_QUEUE = ROUTING_DIR / "tier_3_queue.jsonl"
ESCALATION_QUEUE = ROUTING_DIR / "escalation_queue.jsonl"
ROUTING_STATS_PATH = ROUTING_DIR / "routing_stats.jsonl"

# KB paths
PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"

# Thread-safety lock for batch operations
_routing_lock = Lock()

# ─── Tier Thresholds ────────────────────────────────────────────────────────────

TIER_1_MIN_CONFIDENCE = 0.75       # Autonomous deployment
TIER_2_MIN_CONFIDENCE = 0.60       # Semi-autonomous, human gate
TIER_1_MIN_PATTERN_CONFIDENCE = 0.80  # KB pattern confidence requirement for Tier 1

# Batch routing limit (DoS protection, T-FIX-08)
MAX_BATCH_SIZE = 50

# ─── Risk Penalty/Bonus Constants ───────────────────────────────────────────────

RISK_ARCHITECTURE_PENALTY = 0.10   # Adding new component type
RISK_PHYSICS_LAYER_PENALTY = 0.05  # Modifying collision layers
RISK_SIGNAL_CHANGE_PENALTY = 0.05  # Modifying existing signal emitters
PROVEN_PATTERN_BONUS = 0.05        # Pattern used >5 times in KB
PROVEN_PATTERN_USAGE_THRESHOLD = 5


# ─── Helpers ────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_routing_dirs() -> None:
    ROUTING_DIR.mkdir(parents=True, exist_ok=True)


def append_to_queue(queue_path: Path, entry: dict) -> None:
    """Immutable append to a queue file."""
    ensure_routing_dirs()
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def load_patterns() -> list:
    return load_jsonl(PATTERNS_PATH)


def get_pattern_by_id(pattern_id: str) -> dict | None:
    for p in load_patterns():
        if p.get("id") == pattern_id:
            return p
    return None


def get_pattern_usage_count(pattern_id: str) -> int:
    """Count how many times a pattern has been used (from fix history)."""
    fixes = load_jsonl(FIX_HISTORY_PATH)
    count = 0
    for fix in fixes:
        if fix.get("pattern_id") == pattern_id:
            count += 1
        # Also check in candidates
        for cand in fix.get("candidates", []):
            if cand.get("pattern_id") == pattern_id:
                count += 1
    return count


# ─── Risk Assessment ────────────────────────────────────────────────────────────

def assess_risk(candidate: dict, pattern: dict | None) -> dict:
    """
    Assess risk for a routing candidate.

    Returns:
        {
            risk_level: "low" | "medium" | "high",
            risk_factors: [str, ...],
            risk_penalty: float,
            proven_pattern_bonus: float,
            architecture_change: bool
        }
    """
    risk_factors = []
    total_penalty = 0.0
    proven_bonus = 0.0

    # Check for architecture change (new component type)
    modification_type = candidate.get("modification_type", "")
    description = candidate.get("description", "").lower()

    is_architecture_change = (
        modification_type in ("add_component_type", "add_autoload", "add_scene_type")
        or candidate.get("architecture_change", False)
        or "new component type" in description
        or "add autoload" in description
        or "restructure" in description
    )
    if is_architecture_change:
        risk_factors.append("architecture_change")
        total_penalty += RISK_ARCHITECTURE_PENALTY

    # Check for physics layer change
    is_physics_change = (
        modification_type in ("physics_layer_change", "collision_layer_change")
        or candidate.get("physics_layer_change", False)
        or "physics layer" in description
        or "collision layer" in description
    )
    if is_physics_change:
        risk_factors.append("physics_layer_change")
        total_penalty += RISK_PHYSICS_LAYER_PENALTY

    # Check for signal change
    is_signal_change = (
        modification_type in ("signal_change", "modify_signal")
        or candidate.get("signal_change", False)
        or "signal" in description and "modify" in description
    )
    if is_signal_change:
        risk_factors.append("signal_change")
        total_penalty += RISK_SIGNAL_CHANGE_PENALTY

    # Proven pattern bonus
    pattern_id = candidate.get("pattern_id") or (pattern.get("id") if pattern else None)
    if pattern_id:
        usage_count = get_pattern_usage_count(pattern_id)
        if usage_count > PROVEN_PATTERN_USAGE_THRESHOLD:
            proven_bonus = PROVEN_PATTERN_BONUS
            risk_factors.append(f"proven_pattern_bonus(+{PROVEN_PATTERN_BONUS})")

    # Classify risk level
    penalty_count = len([f for f in risk_factors if "bonus" not in f])
    if penalty_count == 0:
        risk_level = "low"
    elif penalty_count == 1:
        risk_level = "medium"
    else:
        risk_level = "high"

    return {
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "risk_penalty": round(total_penalty, 4),
        "proven_pattern_bonus": round(proven_bonus, 4),
        "architecture_change": is_architecture_change,
        "physics_change": is_physics_change,
        "signal_change": is_signal_change,
    }


# ─── Routing Confidence Formula ─────────────────────────────────────────────────

def compute_routing_confidence(
    candidate_confidence: float,
    pattern_confidence: float,
    risk_penalty: float,
    proven_bonus: float,
) -> float:
    """
    routing_confidence = (candidate_confidence * 0.6) + (pattern_confidence * 0.4)
                         - risk_penalty + proven_bonus
    Clamped to [0.0, 1.0].
    """
    raw = (
        (candidate_confidence * 0.6)
        + (pattern_confidence * 0.4)
        - risk_penalty
        + proven_bonus
    )
    return round(max(0.0, min(1.0, raw)), 4)


# ─── Tier Assignment ────────────────────────────────────────────────────────────

def assign_tier(
    routing_confidence: float,
    architecture_change: bool,
    pattern_confidence: float,
) -> str:
    """
    Assign candidate to a routing tier.

    Tier 1: routing_confidence >= 0.75 AND pattern_confidence >= 0.80
            AND no architecture risk
    Tier 2: 0.60 <= routing_confidence < 0.75
    Tier 3: routing_confidence < 0.60 OR architecture_change=True
    Escalation: invalid/negative confidence

    Returns: "tier_1" | "tier_2" | "tier_3" | "escalation"
    """
    if routing_confidence < 0.0 or routing_confidence > 1.0:
        return "escalation"

    # Tier 3 override: architecture risk forces human review
    if architecture_change:
        return "tier_3"

    if (
        routing_confidence >= TIER_1_MIN_CONFIDENCE
        and pattern_confidence >= TIER_1_MIN_PATTERN_CONFIDENCE
    ):
        return "tier_1"

    if routing_confidence >= TIER_2_MIN_CONFIDENCE:
        return "tier_2"

    if routing_confidence < TIER_2_MIN_CONFIDENCE:
        return "tier_3"

    return "escalation"


# ─── Routing Decision ────────────────────────────────────────────────────────────

def route_candidate(candidate_data: dict) -> dict:
    """
    Route a single candidate through the tiered decision tree.

    Input candidate_data fields:
        - candidate_id (str)
        - confidence_score (float) — from fix_generation_engine
        - pattern_id (str, optional) — for KB lookup
        - pattern_confidence (float, optional) — override KB lookup
        - modification_type (str, optional)
        - architecture_change (bool, optional)
        - physics_layer_change (bool, optional)
        - signal_change (bool, optional)

    Returns routing decision dict.
    """
    ensure_routing_dirs()

    candidate_id = candidate_data.get("candidate_id", "unknown")
    candidate_confidence = float(candidate_data.get("confidence_score", 0.0))

    # Validate confidence range
    if not (0.0 <= candidate_confidence <= 1.0):
        tier = "escalation"
        routing_decision_reason = (
            f"Invalid confidence_score={candidate_confidence}. "
            "Must be in [0.0, 1.0]. Escalating for investigation."
        )
        entry = {
            "candidate_id": candidate_id,
            "routing_timestamp": now_iso(),
            "tier": tier,
            "confidence_score": candidate_confidence,
            "pattern_confidence": 0.0,
            "risk_level": "high",
            "risk_factors": ["invalid_confidence"],
            "routing_confidence": 0.0,
            "routing_decision_reason": routing_decision_reason,
        }
        append_to_queue(ESCALATION_QUEUE, entry)
        _update_routing_stats(tier)
        return entry

    # Get pattern confidence
    pattern_id = candidate_data.get("pattern_id")
    pattern = get_pattern_by_id(pattern_id) if pattern_id else None

    if candidate_data.get("pattern_confidence") is not None:
        pattern_confidence = float(candidate_data["pattern_confidence"])
    elif pattern:
        pattern_confidence = float(pattern.get("confidence", 0.70))
    else:
        # Pattern ID was provided but not found — log warning (WR-02 mitigation)
        if pattern_id:
            print(f"WARNING: Pattern {pattern_id} not found in KB for candidate {candidate_data.get('candidate_id')}", file=sys.stderr)
        pattern_confidence = 0.70  # neutral default

    # Risk assessment
    risk = assess_risk(candidate_data, pattern)

    # Compute routing confidence
    routing_confidence = compute_routing_confidence(
        candidate_confidence=candidate_confidence,
        pattern_confidence=pattern_confidence,
        risk_penalty=risk["risk_penalty"],
        proven_bonus=risk["proven_pattern_bonus"],
    )

    # Assign tier
    tier = assign_tier(
        routing_confidence=routing_confidence,
        architecture_change=risk["architecture_change"],
        pattern_confidence=pattern_confidence,
    )

    # Build routing decision reason
    routing_decision_reason = _build_routing_reason(
        tier=tier,
        candidate_confidence=candidate_confidence,
        pattern_confidence=pattern_confidence,
        routing_confidence=routing_confidence,
        risk=risk,
    )

    # Build queue entry
    entry = {
        "candidate_id": candidate_id,
        "routing_timestamp": now_iso(),
        "tier": tier,
        "confidence_score": candidate_confidence,
        "pattern_confidence": pattern_confidence,
        "risk_level": risk["risk_level"],
        "risk_factors": risk["risk_factors"],
        "routing_confidence": routing_confidence,
        "routing_decision_reason": routing_decision_reason,
    }

    # Route to appropriate queue
    queue_map = {
        "tier_1": TIER_1_QUEUE,
        "tier_2": TIER_2_QUEUE,
        "tier_3": TIER_3_QUEUE,
        "escalation": ESCALATION_QUEUE,
    }
    append_to_queue(queue_map[tier], entry)
    _update_routing_stats(tier)

    return entry


def _build_routing_reason(
    tier: str,
    candidate_confidence: float,
    pattern_confidence: float,
    routing_confidence: float,
    risk: dict,
) -> str:
    formula = (
        f"routing_confidence = ({candidate_confidence}×0.6) + ({pattern_confidence}×0.4) "
        f"- {risk['risk_penalty']} + {risk['proven_pattern_bonus']} = {routing_confidence}"
    )
    tier_descriptions = {
        "tier_1": "Tier 1 (Autonomous): routing_confidence >= 0.75, pattern_confidence >= 0.80, no architecture risk → auto-approve and auto-deploy",
        "tier_2": "Tier 2 (Semi-Autonomous): 0.60 <= routing_confidence < 0.75 → stage candidate, human gate before production",
        "tier_3": "Tier 3 (Human Review): routing_confidence < 0.60 OR architecture_change=True → manual review required",
        "escalation": "Escalation (Out of Scope): invalid candidate → reject or defer for investigation",
    }
    reason = tier_descriptions.get(tier, f"Unknown tier: {tier}")
    return f"{reason}. {formula}. Risk: {risk['risk_level']} {risk['risk_factors']}."


# ─── Routing Statistics ─────────────────────────────────────────────────────────

def _update_routing_stats(tier: str) -> None:
    """Append a stats entry for this routing decision."""
    entry = {
        "timestamp": now_iso(),
        "tier": tier,
    }
    ensure_routing_dirs()
    with open(ROUTING_STATS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_tier_stats() -> dict:
    """Return counts and distribution of candidates in each tier."""
    entries = load_jsonl(ROUTING_STATS_PATH)

    counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0, "escalation": 0}
    for e in entries:
        tier = e.get("tier")
        if tier in counts:
            counts[tier] += 1

    total = sum(counts.values())
    distribution = {}
    if total > 0:
        for tier, count in counts.items():
            distribution[tier] = round((count / total) * 100, 1)
    else:
        distribution = {k: 0.0 for k in counts}

    # Compute confidence averages per tier from actual queue files
    queue_files = {
        "tier_1": TIER_1_QUEUE,
        "tier_2": TIER_2_QUEUE,
        "tier_3": TIER_3_QUEUE,
        "escalation": ESCALATION_QUEUE,
    }
    avg_confidence = {}
    for tier, path in queue_files.items():
        queue_entries = load_jsonl(path)
        if queue_entries:
            confidences = [e.get("routing_confidence", 0.0) for e in queue_entries]
            avg_confidence[tier] = round(sum(confidences) / len(confidences), 4)
        else:
            avg_confidence[tier] = None

    return {
        "counts": counts,
        "total": total,
        "distribution_pct": distribution,
        "avg_routing_confidence_by_tier": avg_confidence,
        "description": {
            "tier_1": "Autonomous (≥0.75): auto-approve and auto-deploy",
            "tier_2": "Semi-Autonomous (0.60–0.75): stage + human gate",
            "tier_3": "Human Review (<0.60): manual review required",
            "escalation": "Escalation: out of scope or invalid",
        },
    }


# ─── Batch Routing ───────────────────────────────────────────────────────────────

def batch_route(candidate_list: list) -> dict:
    """
    Route multiple candidates. Capped at MAX_BATCH_SIZE (DoS protection).

    Input: list of candidate dicts (same format as route_candidate input).
    Returns: routing manifest with results per candidate.
    """
    if len(candidate_list) > MAX_BATCH_SIZE:
        return {
            "error": f"Batch size {len(candidate_list)} exceeds limit of {MAX_BATCH_SIZE}",
            "success": False,
        }

    results = []
    with _routing_lock:
        for candidate_data in candidate_list:
            result = route_candidate(candidate_data)
            results.append(result)

    # Summary counts
    tier_counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0, "escalation": 0}
    for r in results:
        tier = r.get("tier", "escalation")
        if tier in tier_counts:
            tier_counts[tier] += 1

    return {
        "success": True,
        "total_routed": len(results),
        "tier_summary": tier_counts,
        "routing_manifest": results,
    }


# ─── Self-Test ────────────────────────────────────────────────────────────────────

def run_self_test() -> bool:
    """
    Run self-test verifying tier routing correctness.
    Returns True if all tests pass.
    """
    print("Running routing engine self-test...")
    passed = 0
    failed = 0

    def check(name: str, result: dict, expected_tier: str) -> None:
        nonlocal passed, failed
        actual = result.get("tier")
        if actual == expected_tier:
            print(f"  PASS: {name} → {actual}")
            passed += 1
        else:
            print(f"  FAIL: {name} → expected {expected_tier}, got {actual}")
            failed += 1

    # Test 1: High confidence, no risk → tier_1
    c1 = {
        "candidate_id": "test-tier1",
        "confidence_score": 0.88,
        "pattern_confidence": 0.92,
        "modification_type": "add_component",
    }
    check("Tier 1 (high confidence, no risk)", route_candidate(c1), "tier_1")

    # Test 2: Mid confidence → tier_2
    c2 = {
        "candidate_id": "test-tier2",
        "confidence_score": 0.67,
        "pattern_confidence": 0.72,
        "modification_type": "update_property",
    }
    check("Tier 2 (mid confidence)", route_candidate(c2), "tier_2")

    # Test 3: Low confidence → tier_3
    c3 = {
        "candidate_id": "test-tier3",
        "confidence_score": 0.45,
        "pattern_confidence": 0.60,
        "modification_type": "update_script",
    }
    check("Tier 3 (low confidence)", route_candidate(c3), "tier_3")

    # Test 4: Architecture change → tier_3 (override)
    c4 = {
        "candidate_id": "test-arch-risk",
        "confidence_score": 0.80,
        "pattern_confidence": 0.85,
        "modification_type": "update_property",
        "architecture_change": True,
    }
    check("Tier 3 (architecture risk override)", route_candidate(c4), "tier_3")

    # Test 5: Invalid confidence → escalation
    c5 = {
        "candidate_id": "test-invalid",
        "confidence_score": -0.5,
        "pattern_confidence": 0.70,
    }
    check("Escalation (invalid confidence)", route_candidate(c5), "escalation")

    # Test 6: Physics layer change (penalty -0.05 applied)
    c6 = {
        "candidate_id": "test-physics-risk",
        "confidence_score": 0.76,
        "pattern_confidence": 0.83,
        "physics_layer_change": True,
    }
    # routing_confidence = (0.76*0.6) + (0.83*0.4) - 0.05 = 0.456 + 0.332 - 0.05 = 0.738
    # >= 0.75? 0.738 < 0.75 → tier_2
    check("Tier 2 (physics penalty reduces from tier_1)", route_candidate(c6), "tier_2")

    # Test 7: Batch routing
    batch_result = batch_route([
        {"candidate_id": "batch-1", "confidence_score": 0.80, "pattern_confidence": 0.85},
        {"candidate_id": "batch-2", "confidence_score": 0.65, "pattern_confidence": 0.70},
    ])
    if batch_result.get("success") and batch_result.get("total_routed") == 2:
        print("  PASS: Batch routing (2 candidates)")
        passed += 1
    else:
        print("  FAIL: Batch routing")
        failed += 1

    # Test 8: Batch size limit enforcement
    oversized = [{"candidate_id": f"x{i}", "confidence_score": 0.5} for i in range(51)]
    batch_fail = batch_route(oversized)
    if not batch_fail.get("success"):
        print("  PASS: Batch size limit enforced (51 > 50)")
        passed += 1
    else:
        print("  FAIL: Batch size limit not enforced")
        failed += 1

    # Test 9: Tier stats available
    stats = get_tier_stats()
    if "tier_1" in stats.get("counts", {}):
        print("  PASS: tier_1 stats available")
        passed += 1
    else:
        print("  FAIL: Tier stats missing tier_1")
        failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


# ─── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Routing Engine — tiered routing decision tree"
    )
    parser.add_argument(
        "--route-candidate", action="store_true",
        help="Route a single candidate to a tier"
    )
    parser.add_argument(
        "--candidate-id", help="Candidate ID to route"
    )
    parser.add_argument(
        "--confidence-score", type=float, help="Candidate confidence score (0.0–1.0)"
    )
    parser.add_argument(
        "--pattern-confidence", type=float, help="Pattern confidence from KB (0.0–1.0)"
    )
    parser.add_argument(
        "--pattern-id", help="Pattern ID for KB lookup"
    )
    parser.add_argument(
        "--modification-type", help="Type of code modification"
    )
    parser.add_argument(
        "--architecture-change", action="store_true",
        help="Flag if this is an architecture-level change"
    )
    parser.add_argument(
        "--batch-route", action="store_true",
        help="Route multiple candidates from JSON list"
    )
    parser.add_argument(
        "--candidate-list", help="JSON array of candidate dicts for batch routing"
    )
    parser.add_argument(
        "--get-tier-stats", action="store_true",
        help="Return tier distribution statistics"
    )
    parser.add_argument(
        "--test-routing", action="store_true",
        help="Run self-test verifying routing correctness"
    )

    args = parser.parse_args()

    if args.route_candidate:
        if not args.candidate_id:
            print("ERROR: --route-candidate requires --candidate-id", file=sys.stderr)
            sys.exit(1)
        candidate_data = {
            "candidate_id": args.candidate_id,
            "confidence_score": args.confidence_score or 0.0,
        }
        if args.pattern_confidence is not None:
            candidate_data["pattern_confidence"] = args.pattern_confidence
        if args.pattern_id:
            candidate_data["pattern_id"] = args.pattern_id
        if args.modification_type:
            candidate_data["modification_type"] = args.modification_type
        if args.architecture_change:
            candidate_data["architecture_change"] = True
        result = route_candidate(candidate_data)
        print(json.dumps(result, indent=2))
        return

    if args.batch_route:
        if not args.candidate_list:
            print("ERROR: --batch-route requires --candidate-list '<json>'", file=sys.stderr)
            sys.exit(1)
        try:
            candidate_list = json.loads(args.candidate_list)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON for --candidate-list: {e}", file=sys.stderr)
            sys.exit(1)
        result = batch_route(candidate_list)
        print(json.dumps(result, indent=2))
        return

    if args.get_tier_stats:
        stats = get_tier_stats()
        print(json.dumps(stats, indent=2))
        return

    if args.test_routing:
        ok = run_self_test()
        sys.exit(0 if ok else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
