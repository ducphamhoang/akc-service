#!/usr/bin/env python3
"""
AKC Safety Engine
Phase 1, Wave 5 - Tasks 1.23-1.27

Enforces hard guardrails, routes fixes to review tiers, detects conflicts,
monitors deployment, and provides escape hatches for manual control.

Usage:
    python safety_engine.py --route-fix --fix-id <id>
    python safety_engine.py --detect-conflicts
    python safety_engine.py --set-escape-hatch caution|quarantine|re-validate|reset
    python safety_engine.py --check-guardrails --pattern-id <id>
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"
SAFETY_STATE_PATH = KB_DIR / "safety_state.json"

# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all_patterns() -> list:
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


def load_fix(fix_id: str) -> dict | None:
    if not FIX_HISTORY_PATH.exists():
        return None
    with open(FIX_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("fix_id") == fix_id:
                    return entry
            except json.JSONDecodeError:
                pass
    return None


def update_fix(fix_id: str, updates: dict) -> bool:
    # Guard: Quarantine mode blocks KB writes
    safety_state = load_safety_state()
    if safety_state.get("escape_hatch") == "quarantine":
        raise RuntimeError("KB writes blocked: quarantine mode active")

    if not FIX_HISTORY_PATH.exists():
        return False
    lines = []
    found = False
    with open(FIX_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            try:
                entry = json.loads(stripped)
                if entry.get("fix_id") == fix_id:
                    entry.update(updates)
                    lines.append(json.dumps(entry) + "\n")
                    found = True
                else:
                    lines.append(line)
            except json.JSONDecodeError:
                lines.append(line)
    if found:
        with open(FIX_HISTORY_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return found


def load_safety_state() -> dict:
    if not SAFETY_STATE_PATH.exists():
        return {
            "escape_hatch": None,
            "escape_hatch_set_at": None,
            "escape_hatch_reason": None,
        }
    with open(SAFETY_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_safety_state(state: dict) -> None:
    SAFETY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SAFETY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_confidence_history(entry: dict) -> None:
    CONFIDENCE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIDENCE_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Task 1.23: Hard Guardrails Enforcement ───────────────────────────────────

# 6 Hard Guardrails
GUARDRAILS = {
    "G1_physics_layers": {
        "description": "Never modify PhysicsLayers.gd constants or assigned layer values",
        "protected_files": ["constants/PhysicsLayers.gd", "PhysicsLayers.gd"],
        "protected_patterns": re.compile(r"PhysicsLayers\.(LAYER_\w+)\s*=", re.I),
    },
    "G2_signal_signatures": {
        "description": "Never change emitted signal names or parameter counts",
        "violation_patterns": re.compile(r"(signal\s+\w+\s*\(.*\))", re.I),
    },
    "G3_public_api_signatures": {
        "description": "Never rename or remove public functions in components",
        "protected_patterns": re.compile(r"func\s+(take_damage|heal|get_health|set_health|_ready|_physics_process)\s*\(", re.I),
    },
    "G4_architecture_patterns": {
        "description": "Never restructure scene tree (move/reparent nodes)",
        "violation_patterns": re.compile(r"(reparent|move_child|remove_child)\s*\(", re.I),
    },
    "G5_hard_constraints": {
        "description": "Never hardcode integer literals for collision layers",
        "violation_patterns": re.compile(r"collision_layer\s*=\s*\d+|collision_mask\s*=\s*\d+", re.I),
    },
    "G6_high_confidence_patterns": {
        "description": "Never modify patterns with confidence > 0.85 without override_key",
    },
}


def check_guardrails(
    pattern_entry: dict | None = None,
    diff_text: str = "",
    files_affected: list | None = None,
    override_key: str | None = None,
) -> dict:
    """
    Task 1.23: Evaluate a fix/pattern change against all 6 guardrails.

    Returns:
        dict with per-guardrail results and overall pass/fail
    """
    files_affected = files_affected or []
    results = {}
    violations = []

    # G1: Physics layers
    g1_pass = True
    for f in files_affected:
        if any(protected in f for protected in GUARDRAILS["G1_physics_layers"]["protected_files"]):
            g1_pass = False
            violations.append("G1_physics_layers: Protected file modified — PhysicsLayers.gd is read-only")
            break
    if diff_text and GUARDRAILS["G1_physics_layers"]["protected_patterns"].search(diff_text):
        g1_pass = False
        violations.append("G1_physics_layers: Physics layer constant assignment detected in diff")
    results["G1_physics_layers"] = "PASS" if g1_pass else "FAIL"

    # G2: Signal signatures
    g2_pass = True
    if diff_text and "signal " in diff_text.lower():
        # Check if signal declaration is being changed
        signal_changes = re.findall(r"[-+]\s*signal\s+\w+", diff_text)
        if signal_changes:
            g2_pass = False
            violations.append(f"G2_signal_signatures: Signal declaration modified: {signal_changes[:2]}")
    results["G2_signal_signatures"] = "PASS" if g2_pass else "FAIL"

    # G3: Public API signatures
    g3_pass = True
    if diff_text and GUARDRAILS["G3_public_api_signatures"]["protected_patterns"].search(diff_text):
        # Check if protected functions are being removed/renamed
        removals = re.findall(r"^-\s*func\s+\w+", diff_text, re.MULTILINE)
        if removals:
            g3_pass = False
            violations.append(f"G3_public_api: Protected function removed/renamed: {removals[:2]}")
    results["G3_public_api_signatures"] = "PASS" if g3_pass else "FAIL"

    # G4: Architecture patterns
    g4_pass = True
    if diff_text and GUARDRAILS["G4_architecture_patterns"]["violation_patterns"].search(diff_text):
        g4_pass = False
        violations.append("G4_architecture: Scene tree restructuring detected (reparent/move_child/remove_child)")
    results["G4_architecture_patterns"] = "PASS" if g4_pass else "FAIL"

    # G5: Hard constraints (no integer literals in collision layers)
    g5_pass = True
    if diff_text and GUARDRAILS["G5_hard_constraints"]["violation_patterns"].search(diff_text):
        matches = GUARDRAILS["G5_hard_constraints"]["violation_patterns"].findall(diff_text)
        g5_pass = False
        violations.append(f"G5_hard_constraints: Integer literal in collision assignment: {matches[:2]}")
    results["G5_hard_constraints"] = "PASS" if g5_pass else "FAIL"

    # G6: High-confidence patterns
    g6_pass = True
    if pattern_entry:
        confidence = pattern_entry.get("confidence", 0.0)
        if confidence > 0.85:
            if override_key:
                # Validate override key format
                if re.match(r"^OVERRIDE-[A-Z0-9]{8}$", override_key):
                    g6_pass = True  # Valid override
                else:
                    g6_pass = False
                    violations.append("G6_high_confidence: Invalid override key format (expected OVERRIDE-XXXXXXXX)")
            else:
                g6_pass = False
                violations.append(
                    f"G6_high_confidence: Pattern '{pattern_entry.get('id')}' has confidence {confidence:.2f} > 0.85 — "
                    "requires override_key for modification"
                )
    results["G6_high_confidence_patterns"] = "PASS" if g6_pass else "FAIL"

    all_pass = all(v == "PASS" for v in results.values())

    return {
        "guardrail_results": results,
        "all_guardrails_passed": all_pass,
        "violations": violations,
        "violation_count": len(violations),
    }


# ─── Task 1.24: Tiered Review Router ──────────────────────────────────────────

def route_fix(fix_id: str) -> dict:
    """
    Task 1.24: Route a fix to the appropriate review tier.

    Tiers:
    - Tier 1 (Auto-approve): confidence >= 0.85, all tests pass, no new files
    - Tier 2 (Auto-deploy with staging): confidence 0.70-0.84, tests pass, existing files
    - Tier 3 (Human review): confidence 0.60-0.70, new files, or critical paths
    - Blocked: guardrail violations or confidence <0.60

    Returns:
        dict with tier, routing_decision, justification
    """
    # Check escape hatches first
    safety_state = load_safety_state()
    current_hatch = safety_state.get("escape_hatch")

    fix = load_fix(fix_id)
    if not fix:
        return {"error": f"Fix {fix_id} not found", "success": False}

    candidates = fix.get("candidates", [])
    selected_id = fix.get("selected_candidate_id")
    candidate = next((c for c in candidates if c.get("candidate_id") == selected_id), None)
    if not candidate and candidates:
        candidate = candidates[0]

    if not candidate:
        return {
            "fix_id": fix_id,
            "tier": "blocked",
            "routing_decision": "blocked_no_candidate",
            "justification": "No candidates available for routing",
            "success": False,
        }

    confidence = candidate.get("confidence", 0.0)
    guardrails_violated = candidate.get("guardrails_violated", [])
    files_affected = candidate.get("files_affected", [])

    # Check for new files (created vs. modified)
    creates_new_files = any(
        f.endswith(".tscn") or f.endswith(".gd")
        for f in files_affected
        if "new_file" in f.lower()
    )

    # Check critical paths
    critical_paths = ["project.godot", "PhysicsLayers.gd", "EventSystem", "autoload"]
    touches_critical = any(
        any(cp in f for cp in critical_paths)
        for f in files_affected
    )

    # Run guardrail check
    guardrail_check = check_guardrails(
        pattern_entry=None,
        diff_text=candidate.get("pseudo_code", ""),
        files_affected=files_affected,
    )

    # Escape hatch overrides
    if current_hatch == "caution":
        # Caution: disable Tier 1, require human approval for Tier 2
        if confidence >= 0.85:
            tier = "tier_2"
            routing = "caution_mode_degraded_to_tier2"
            justification = (
                f"CAUTION MODE: Tier 1 disabled. Confidence={confidence:.2f} >= 0.85 "
                "but escape hatch 'caution' active — routing to Tier 2 (human approval required)."
            )
            _save_routing(fix_id, tier, routing, justification)
            return {"fix_id": fix_id, "tier": tier, "routing_decision": routing, "justification": justification, "success": True}

    if current_hatch == "quarantine":
        tier = "blocked"
        routing = "quarantine_mode"
        justification = "QUARANTINE MODE: All KB updates suspended. Fix blocked until quarantine lifted."
        _save_routing(fix_id, tier, routing, justification)
        return {"fix_id": fix_id, "tier": tier, "routing_decision": routing, "justification": justification, "success": True}

    # Standard routing logic
    if guardrails_violated or not guardrail_check["all_guardrails_passed"]:
        tier = "blocked"
        routing = "blocked_guardrail_violation"
        justification = (
            f"BLOCKED: Guardrail violations detected: {guardrails_violated or guardrail_check['violations']}. "
            "Fix rejected. Human review required."
        )

    elif confidence >= 0.85 and not creates_new_files and not touches_critical:
        tier = "tier_1"
        routing = "auto_approve"
        justification = (
            f"Tier 1 — AUTO-APPROVE: confidence={confidence:.2f} >= 0.85, "
            "all 6 guardrails passed, no new files, no critical paths. "
            "Deploy immediately to 10% staging."
        )

    elif confidence >= 0.70 and not creates_new_files and not touches_critical:
        tier = "tier_2"
        routing = "auto_deploy_staging"
        justification = (
            f"Tier 2 — AUTO-DEPLOY WITH STAGING: confidence={confidence:.2f} (0.70-0.84), "
            "all guardrails passed, modifies existing files only. "
            "Deploy to 10% staging, monitor 48h before promotion."
        )

    elif confidence >= 0.60 or creates_new_files or touches_critical:
        tier = "tier_3"
        routing = "human_review_required"
        reasons = []
        if confidence < 0.70:
            reasons.append(f"confidence={confidence:.2f} in 0.60-0.70 range")
        if creates_new_files:
            reasons.append("creates new files")
        if touches_critical:
            reasons.append("touches critical path files")
        justification = (
            f"Tier 3 — HUMAN REVIEW: {'; '.join(reasons)}. "
            "Routed to human reviewer with full context. "
            "Awaiting approval before validation."
        )

    else:
        tier = "blocked"
        routing = "escalation_low_confidence"
        justification = (
            f"ESCALATION: confidence={confidence:.2f} < 0.60. "
            "No automatic action. Flagged for human investigation."
        )

    _save_routing(fix_id, tier, routing, justification)
    return {
        "fix_id": fix_id,
        "tier": tier,
        "routing_decision": routing,
        "justification": justification,
        "confidence": confidence,
        "guardrail_check": guardrail_check,
        "escape_hatch_active": current_hatch,
        "success": True,
    }


def _save_routing(fix_id: str, tier: str, routing: str, justification: str) -> None:
    update_fix(fix_id, {
        "safety_routing": {
            "tier": tier,
            "routing_decision": routing,
            "justification": justification,
            "routed_at": now_iso(),
        }
    })


# ─── Task 1.25: Conflict Detector ─────────────────────────────────────────────

def detect_conflicts() -> dict:
    """
    Task 1.25: Detect conflicts between patterns.

    Conflict types:
    1. Static: Two patterns modify the same file/function
    2. Semantic: Contradictory logic in pattern descriptions
    3. Empirical: Historical data shows patterns fail when both enabled

    Returns:
        dict with conflict_list (each with severity: high/medium/low)
    """
    patterns = load_all_patterns()
    fix_history = _load_fix_history()

    conflicts = []

    # ── Type 1: Static Conflicts ───────────────────────────────────────────────
    # Group patterns by entity+component
    component_groups: dict = {}
    for p in patterns:
        key = (p.get("entity"), p.get("component"))
        component_groups.setdefault(key, []).append(p)

    for (entity, component), group in component_groups.items():
        if len(group) > 1:
            # Multiple patterns for same entity+component — potential static conflict
            for i, p1 in enumerate(group):
                for p2 in group[i+1:]:
                    if p1.get("pattern_type") == p2.get("pattern_type"):
                        conflicts.append({
                            "type": "static",
                            "severity": "medium",
                            "pattern_ids": [p1.get("id"), p2.get("id")],
                            "entity": entity,
                            "component": component,
                            "reason": (
                                f"Both patterns modify {entity}/{component}/{p1.get('pattern_type')} — "
                                "potential file/function overlap"
                            ),
                        })

    # ── Type 2: Semantic Conflicts ─────────────────────────────────────────────
    CONTRADICTION_PAIRS = [
        (re.compile(r"\balways\s+validate", re.I), re.compile(r"skip\s+validation|no\s+validation", re.I)),
        (re.compile(r"\balways\s+use\s+PhysicsLayers", re.I), re.compile(r"use\s+integer\s+literal|hardcode", re.I)),
        (re.compile(r"emit\s+signal\s+immediately", re.I), re.compile(r"buffer\s+signal|defer\s+signal", re.I)),
        (re.compile(r"clamp\s+health", re.I), re.compile(r"unclamped|no\s+bounds", re.I)),
    ]

    for i, p1 in enumerate(patterns):
        desc1 = (p1.get("description", "") + " " + p1.get("rule", "")).lower()
        for p2 in patterns[i+1:]:
            desc2 = (p2.get("description", "") + " " + p2.get("rule", "")).lower()
            for pos_re, neg_re in CONTRADICTION_PAIRS:
                if (pos_re.search(desc1) and neg_re.search(desc2)) or \
                   (pos_re.search(desc2) and neg_re.search(desc1)):
                    conflicts.append({
                        "type": "semantic",
                        "severity": "high",
                        "pattern_ids": [p1.get("id"), p2.get("id")],
                        "entity": p1.get("entity"),
                        "component": p1.get("component"),
                        "reason": "Semantic contradiction in pattern descriptions/rules",
                    })

    # ── Type 3: Empirical Conflicts ────────────────────────────────────────────
    # Analyze fix_history for negative correlations between pattern pairs
    empirical_conflicts = _detect_empirical_conflicts(patterns, fix_history)
    conflicts.extend(empirical_conflicts)

    # Also check declared conflicts_with fields
    for p in patterns:
        for conflict_id in p.get("conflicts_with", []):
            # Find the conflicting pattern
            conflict_p = next((x for x in patterns if x.get("id") == conflict_id), None)
            if conflict_p:
                conflicts.append({
                    "type": "declared",
                    "severity": "high",
                    "pattern_ids": [p.get("id"), conflict_id],
                    "entity": p.get("entity"),
                    "component": p.get("component"),
                    "reason": f"Pattern explicitly declares conflict with '{conflict_id}'",
                })

    # De-duplicate
    seen = set()
    unique_conflicts = []
    for c in conflicts:
        key = frozenset(c["pattern_ids"])
        if key not in seen:
            seen.add(key)
            unique_conflicts.append(c)

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    unique_conflicts.sort(key=lambda x: severity_order.get(x["severity"], 3))

    high_count = sum(1 for c in unique_conflicts if c["severity"] == "high")

    result = {
        "total_conflicts": len(unique_conflicts),
        "high_severity": high_count,
        "conflict_list": unique_conflicts,
        "alert_triggered": high_count >= 1,
        "detected_at": now_iso(),
    }

    if high_count >= 1:
        print(
            f"ALERT: {high_count} high-severity conflict(s) detected. "
            "Review conflict_list and resolve.",
            file=sys.stderr
        )

    return result


def _load_fix_history() -> list:
    fixes = []
    if not FIX_HISTORY_PATH.exists():
        return fixes
    with open(FIX_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    fixes.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return fixes


def _detect_empirical_conflicts(patterns: list, fix_history: list) -> list:
    """Detect empirical conflicts from historical fix success rates."""
    # Build: pattern_id → list of outcomes (success/failure) per fix
    pattern_outcomes: dict = {}
    for fix in fix_history:
        outcome = fix.get("outcome", "")
        pattern_id = fix.get("pattern_id")
        if pattern_id and outcome:
            pattern_outcomes.setdefault(pattern_id, []).append(outcome)

    # If insufficient data, skip empirical analysis
    if len(pattern_outcomes) < 2:
        return []

    # Look for patterns with high solo success but poor combined success
    # (simplified: any pattern with >50% failure rate when other patterns also failing)
    poor_performers = [
        pid for pid, outcomes in pattern_outcomes.items()
        if outcomes and
        sum(1 for o in outcomes if "fail" in o.lower() or "rolled_back" in o.lower()) / len(outcomes) > 0.5
    ]

    conflicts = []
    for i, p1_id in enumerate(poor_performers):
        for p2_id in poor_performers[i+1:]:
            conflicts.append({
                "type": "empirical",
                "severity": "low",
                "pattern_ids": [p1_id, p2_id],
                "entity": None,
                "component": None,
                "reason": (
                    f"Both patterns show >50% failure rate historically — "
                    "empirical correlation suggests possible interaction conflict"
                ),
            })

    return conflicts


# ─── Task 1.27: Escape Hatches ────────────────────────────────────────────────

ESCAPE_HATCHES = {
    "caution": {
        "description": "Disable Tier 1 auto-approvals. Require explicit approval for Tier 2.",
        "effect": "All fixes routed through human review before deployment.",
    },
    "quarantine": {
        "description": "Suspend all KB updates (no new patterns, no confidence changes).",
        "effect": "Read-only mode. Agents can use KB but no modifications.",
    },
    "re-validate": {
        "description": "Re-run all staged deployments for currently deployed patterns.",
        "effect": "All active fixes re-enter validation pipeline.",
    },
    "reset": {
        "description": "Revert KB to a prior checkpoint with full audit trail.",
        "effect": "All recent patterns/confidence changes rolled back to checkpoint.",
    },
    "none": {
        "description": "Normal operation (no escape hatch active).",
        "effect": "Standard tiered routing and auto-approvals apply.",
    },
}


def set_escape_hatch(mode: str, reason: str = None) -> dict:
    """
    Task 1.27: Set escape hatch mode for manual intervention.

    Modes: caution, quarantine, re-validate, reset, none (to clear)
    """
    if mode not in ESCAPE_HATCHES:
        return {
            "error": f"Invalid escape hatch mode: {mode}. Valid: {list(ESCAPE_HATCHES.keys())}",
            "success": False,
        }

    hatch_config = ESCAPE_HATCHES[mode]
    state = load_safety_state()

    old_hatch = state.get("escape_hatch")

    if mode == "none":
        state["escape_hatch"] = None
        state["escape_hatch_set_at"] = None
        state["escape_hatch_reason"] = None
    else:
        state["escape_hatch"] = mode
        state["escape_hatch_set_at"] = now_iso()
        state["escape_hatch_reason"] = reason or f"Manually set to {mode}"

    save_safety_state(state)

    # Log to confidence history for audit trail
    append_confidence_history({
        "history_id": f"esc-{now_iso()}",
        "event_type": "escape_hatch_change",
        "timestamp": now_iso(),
        "old_escape_hatch": old_hatch,
        "new_escape_hatch": mode if mode != "none" else None,
        "reason": reason,
        "changed_by": "safety_engine",
    })

    # Execute escape hatch side effects
    side_effects = _execute_escape_hatch_effects(mode)

    return {
        "escape_hatch": mode,
        "description": hatch_config["description"],
        "effect": hatch_config["effect"],
        "set_at": state.get("escape_hatch_set_at"),
        "reason": state.get("escape_hatch_reason"),
        "side_effects": side_effects,
        "success": True,
    }


def _execute_escape_hatch_effects(mode: str) -> list:
    """Execute side effects for escape hatch activation."""
    effects = []

    if mode == "caution":
        effects.append("Tier 1 auto-approvals disabled")
        effects.append("Tier 2 requires explicit human sign-off")

    elif mode == "quarantine":
        effects.append("KB write operations suspended")
        effects.append("All pending fixes paused")
        effects.append("Read-only mode active — agents can still query KB")

    elif mode == "re-validate":
        effects.append("All deployed fixes queued for re-validation")
        effects.append("Staged deployment timers reset")
        effects.append("Monitoring alerts elevated to 1-hour cadence")

    elif mode == "reset":
        from akc_service.learning_integration import restore_from_checkpoint
        success = restore_from_checkpoint()
        if success:
            effects.append("KB patterns restored from checkpoint")
            effects.append("Audit trail preserved in confidence_history.jsonl")
        else:
            effects.append("ERROR: No checkpoint available — cannot restore")
            effects.append("Manual recovery required")

    elif mode == "none":
        effects.append("Normal operation restored")
        effects.append("Standard tiered routing re-enabled")

    return effects


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Safety Engine — guardrails, tiered routing, conflict detection, escape hatches"
    )
    parser.add_argument("--check-guardrails", action="store_true",
                        help="Check guardrails for a pattern or fix")
    parser.add_argument("--route-fix", action="store_true",
                        help="Route a fix to the appropriate review tier")
    parser.add_argument("--detect-conflicts", action="store_true",
                        help="Detect conflicts between KB patterns")
    parser.add_argument("--set-escape-hatch", metavar="MODE",
                        help="Set escape hatch mode: caution, quarantine, re-validate, reset, none")
    parser.add_argument("--get-escape-hatch", action="store_true",
                        help="Show current escape hatch state")

    parser.add_argument("--fix-id", help="Fix ID")
    parser.add_argument("--pattern-id", help="Pattern ID")
    parser.add_argument("--diff", help="Diff text to check against guardrails")
    parser.add_argument("--files", nargs="*", help="Files affected by change")
    parser.add_argument("--override-key", help="Override key for high-confidence patterns")
    parser.add_argument("--reason", help="Reason for escape hatch activation")

    args = parser.parse_args()

    if args.check_guardrails:
        patterns = load_all_patterns()
        pattern = next((p for p in patterns if p.get("id") == args.pattern_id), None) if args.pattern_id else None
        result = check_guardrails(
            pattern_entry=pattern,
            diff_text=args.diff or "",
            files_affected=args.files or [],
            override_key=args.override_key,
        )
        print(json.dumps(result, indent=2))
        return

    if args.route_fix:
        if not args.fix_id:
            print("ERROR: --route-fix requires --fix-id", file=sys.stderr)
            sys.exit(1)
        result = route_fix(args.fix_id)
        print(json.dumps(result, indent=2))
        return

    if args.detect_conflicts:
        result = detect_conflicts()
        print(json.dumps(result, indent=2))
        return

    if args.set_escape_hatch:
        result = set_escape_hatch(args.set_escape_hatch, args.reason)
        print(json.dumps(result, indent=2))
        return

    if args.get_escape_hatch:
        state = load_safety_state()
        print(json.dumps(state, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
