#!/usr/bin/env python3
"""
AKC Learning Engine
Phase 1, Wave 4 - Tasks 1.17-1.22

Manages confidence updates, pattern versioning, auto-promotion/demotion,
dependency tracking, and knowledge base analysis.

Usage:
    python learning_engine.py --update-confidence --pattern-id <id> --delta <float> --reason <str>
    python learning_engine.py --version-pattern --pattern-id <id> --fix-id <id>
    python learning_engine.py --analyze-kb
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"
TAXONOMY_PATH = KB_DIR / "ENTITY_COMPONENT_TAXONOMY.md"
_DEFAULT_ANALYSIS = Path(__file__).parent.parent / "KB_ANALYSIS.md"
KB_ANALYSIS_PATH = Path(os.environ.get("AKC_SERVICE_KB_ANALYSIS", str(_DEFAULT_ANALYSIS)))

# ─── Tier Definitions (from SCHEMA_v2) ────────────────────────────────────────

TIERS = {
    "gold":        (0.85, 1.0,  "Guardrail-protected. Highest trust, not auto-modified."),
    "production":  (0.70, 0.85, "Normal use. Trusted, actively recommended."),
    "experimental": (0.50, 0.70, "In development. Some use, under improvement."),
    "demoted":     (0.0,  0.50, "Unreliable. Excluded from agent recommendations."),
}

# Total entity:component combinations (from taxonomy)
ENTITY_COMPONENTS = [
    ("player", "HealthComponent"), ("player", "MovementComponent"),
    ("player", "CombatComponent"), ("player", "PhysicsComponent"),
    ("player", "AnimationComponent"), ("player", "SignalComponent"),
    ("enemy_knight", "HealthComponent"), ("enemy_knight", "PhysicsComponent"),
    ("enemy_knight", "AnimationComponent"), ("enemy_knight", "CombatComponent"),
    ("enemy_knight", "MovementComponent"),
    ("enemy_mage", "HealthComponent"), ("enemy_mage", "CombatComponent"),
    ("enemy_mage", "AnimationComponent"),
    ("minion", "HealthComponent"), ("minion", "PhysicsComponent"),
    ("minion", "MovementComponent"), ("minion", "CombatComponent"),
    ("boss", "HealthComponent"), ("boss", "CombatComponent"),
    ("boss", "AnimationComponent"),
    ("global", "PhysicsComponent"), ("global", "EventSystem"),
    ("global", "autoload"), ("global", "cross_component"),
    ("ui", "EventSystem"), ("ui", "SignalComponent"),
    ("camera", "PhysicsComponent"),
    ("audio", "SignalComponent"),
]

TOTAL_ENTITY_COMPONENTS = len(ENTITY_COMPONENTS)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_history_id() -> str:
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


def load_pattern(pattern_id: str) -> dict | None:
    for p in load_all_patterns():
        if p.get("id") == pattern_id:
            return p
    return None


def save_all_patterns(patterns: list) -> None:
    """Atomically save all patterns to patterns.jsonl (overwrite)."""
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATTERNS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for p in patterns:
            f.write(json.dumps(p) + "\n")
    tmp.replace(PATTERNS_PATH)


def append_confidence_history(entry: dict) -> None:
    CONFIDENCE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIDENCE_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_confidence_history() -> list:
    history = []
    if not CONFIDENCE_HISTORY_PATH.exists():
        return history
    with open(CONFIDENCE_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return history


def load_fix_history() -> list:
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


# ─── Task 1.17: Confidence Updater ────────────────────────────────────────────

def update_confidence(
    pattern_id: str,
    delta: float,
    reason: str,
    task_id: str = None,
    fix_id: str = None,
) -> dict:
    """
    Task 1.17: Update pattern confidence and log to confidence_history.jsonl.

    Deltas:
      +0.05 on task success
      -0.10 on task failure/rollback
      Custom delta for manual override

    Returns:
        dict with old_confidence, new_confidence, tier transition info
    """
    patterns = load_all_patterns()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)

    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    old_confidence = pattern.get("confidence", 0.5)
    old_tier = _confidence_tier(old_confidence)

    # Apply delta with clamping
    new_confidence = round(max(0.0, min(1.0, old_confidence + delta)), 4)
    new_tier = _confidence_tier(new_confidence)

    # Update pattern
    pattern["confidence"] = new_confidence
    pattern["confidence_tier"] = new_tier
    pattern["updated_at"] = now_iso()

    # Guardrail protection: gold tier patterns get protected flag
    if new_confidence > 0.85:
        pattern["guardrail_protected"] = True
    elif new_confidence <= 0.85 and pattern.get("guardrail_protected"):
        # Only remove if explicitly demoted below gold threshold
        if old_confidence > 0.85 and new_confidence <= 0.85:
            pattern["guardrail_protected"] = False

    # Task 1.19/1.20: Auto-promotion / auto-demotion
    tier_changed = old_tier != new_tier
    promotion_type = None
    if tier_changed:
        if TIERS[new_tier][0] > TIERS[old_tier][0]:
            promotion_type = "promotion"
        else:
            promotion_type = "demotion"

    # Alert on demotion to 'demoted' tier
    if new_tier == "demoted" and old_tier != "demoted":
        _emit_demotion_alert(pattern_id, new_confidence, reason)

    # Save updated pattern
    save_all_patterns(patterns)

    # Log to confidence history
    history_entry = {
        "history_id": make_history_id(),
        "pattern_id": pattern_id,
        "timestamp": now_iso(),
        "delta": delta,
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "old_tier": old_tier,
        "new_tier": new_tier,
        "tier_changed": tier_changed,
        "promotion_type": promotion_type,
        "trigger": reason,
        "task_id": task_id,
        "fix_id": fix_id,
        "changed_by": "learning_engine",
    }
    append_confidence_history(history_entry)

    return {
        "pattern_id": pattern_id,
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "old_tier": old_tier,
        "new_tier": new_tier,
        "tier_changed": tier_changed,
        "promotion_type": promotion_type,
        "guardrail_protected": pattern.get("guardrail_protected", False),
        "success": True,
    }


def _emit_demotion_alert(pattern_id: str, confidence: float, reason: str) -> None:
    """Emit alert when pattern is auto-demoted to 'demoted' tier."""
    alert = {
        "alert_type": "pattern_demoted",
        "pattern_id": pattern_id,
        "confidence": confidence,
        "reason": reason,
        "timestamp": now_iso(),
        "message": (
            f"ALERT: Pattern '{pattern_id}' demoted to 'unreliable' tier "
            f"(confidence={confidence}). Excluded from agent recommendations. "
            f"Human review required."
        ),
    }
    # Log to stderr as alert channel (in production: Slack/email via monitoring_engine)
    print(json.dumps(alert), file=sys.stderr)


# ─── Task 1.18: Pattern Versioner ─────────────────────────────────────────────

def version_pattern(pattern_id: str, fix_id: str, change_reason: str = None) -> dict:
    """
    Task 1.18: Create immutable new version of a pattern after successful fix.

    Versioning scheme: v1 → v2 → v3 (each is a snapshot, never modified)
    Version history is append-only in patterns.jsonl.
    """
    patterns = load_all_patterns()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)

    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    version_info = pattern.get("version", {"current": "v1", "history": []})
    current_version = version_info.get("current", "v1")

    # Determine next version
    version_nums = {"v1": 1, "v2": 2, "v3": 3, "v4": 4, "v5": 5}
    current_num = version_nums.get(current_version, 1)

    if current_num >= 5:
        return {
            "error": "Maximum versions (v5) reached — manual intervention required",
            "success": False,
        }

    next_version = f"v{current_num + 1}"

    # Create version snapshot (immutable record of current state)
    snapshot = {
        "version_id": next_version,
        "confidence_snapshot": pattern.get("confidence", 0.5),
        "timestamp": now_iso(),
        "change_reason": change_reason or f"Fix applied: {fix_id}",
        "changed_by": "learning_engine",
    }

    # Append snapshot to history (immutable — never modify existing entries)
    version_info["history"].append(snapshot)
    version_info["current"] = next_version

    # Update pattern with new version
    pattern["version"] = version_info
    pattern["updated_at"] = now_iso()

    save_all_patterns(patterns)

    return {
        "pattern_id": pattern_id,
        "old_version": current_version,
        "new_version": next_version,
        "fix_id": fix_id,
        "snapshot": snapshot,
        "total_versions": len(version_info["history"]),
        "success": True,
    }


def rollback_to_version(pattern_id: str, target_version: str) -> dict:
    """Roll back pattern to a prior version by restoring the confidence snapshot."""
    patterns = load_all_patterns()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)

    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    version_info = pattern.get("version", {"current": "v1", "history": []})
    history = version_info.get("history", [])

    target_snapshot = next((h for h in history if h.get("version_id") == target_version), None)
    if not target_snapshot:
        return {
            "error": f"Version {target_version} not found in history for {pattern_id}",
            "success": False,
        }

    old_confidence = pattern.get("confidence", 0.5)
    restored_confidence = target_snapshot.get("confidence_snapshot", 0.5)

    # Restore confidence from snapshot (creates a new version with restored values)
    pattern["confidence"] = restored_confidence
    pattern["confidence_tier"] = _confidence_tier(restored_confidence)
    pattern["updated_at"] = now_iso()

    # Log the rollback as a new version entry
    new_snapshot = {
        "version_id": f"rollback-to-{target_version}",
        "confidence_snapshot": restored_confidence,
        "timestamp": now_iso(),
        "change_reason": f"Rollback to {target_version}",
        "changed_by": "learning_engine_rollback",
    }
    version_info["history"].append(new_snapshot)
    pattern["version"] = version_info

    save_all_patterns(patterns)

    # Log to confidence history
    append_confidence_history({
        "history_id": make_history_id(),
        "pattern_id": pattern_id,
        "timestamp": now_iso(),
        "delta": restored_confidence - old_confidence,
        "old_confidence": old_confidence,
        "new_confidence": restored_confidence,
        "trigger": f"rollback_to_{target_version}",
        "changed_by": "learning_engine",
    })

    return {
        "pattern_id": pattern_id,
        "rolled_back_to": target_version,
        "old_confidence": old_confidence,
        "restored_confidence": restored_confidence,
        "success": True,
    }


# ─── Task 1.19: Auto-Promotion ─────────────────────────────────────────────────

def auto_promote(pattern_id: str) -> dict:
    """
    Task 1.19: Re-evaluate pattern tier and apply promotion if threshold crossed.
    Called after every confidence update.
    """
    pattern = load_pattern(pattern_id)
    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    confidence = pattern.get("confidence", 0.5)
    current_tier = pattern.get("confidence_tier", _confidence_tier(confidence))
    expected_tier = _confidence_tier(confidence)

    if current_tier == expected_tier:
        return {
            "pattern_id": pattern_id,
            "tier_unchanged": True,
            "current_tier": current_tier,
            "success": True,
        }

    # Tier mismatch — apply correction
    return update_confidence(
        pattern_id=pattern_id,
        delta=0.0,  # no delta, just tier correction
        reason="auto_tier_correction",
    )


# ─── Task 1.20: Auto-Demotion ──────────────────────────────────────────────────

def auto_demote(pattern_id: str) -> dict:
    """
    Task 1.20: Automatically demote pattern if confidence < 0.50.
    Called after every confidence update that brings confidence near threshold.
    """
    pattern = load_pattern(pattern_id)
    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    confidence = pattern.get("confidence", 0.5)
    if confidence >= 0.50:
        return {
            "pattern_id": pattern_id,
            "demotion_needed": False,
            "confidence": confidence,
            "message": "Confidence above demotion threshold",
            "success": True,
        }

    current_tier = pattern.get("confidence_tier", "experimental")
    if current_tier == "demoted":
        return {
            "pattern_id": pattern_id,
            "already_demoted": True,
            "confidence": confidence,
            "success": True,
        }

    # Apply demotion
    patterns = load_all_patterns()
    p = next((x for x in patterns if x.get("id") == pattern_id), None)
    if p:
        p["confidence_tier"] = "demoted"
        p["updated_at"] = now_iso()
        save_all_patterns(patterns)

    _emit_demotion_alert(pattern_id, confidence, "auto_demotion_threshold_crossed")

    append_confidence_history({
        "history_id": make_history_id(),
        "pattern_id": pattern_id,
        "timestamp": now_iso(),
        "delta": 0.0,
        "old_confidence": confidence,
        "new_confidence": confidence,
        "old_tier": current_tier,
        "new_tier": "demoted",
        "trigger": "auto_demotion",
        "changed_by": "learning_engine",
        "demotion_reason": f"Confidence {confidence} below 0.50 threshold",
    })

    return {
        "pattern_id": pattern_id,
        "demoted": True,
        "confidence": confidence,
        "previous_tier": current_tier,
        "new_tier": "demoted",
        "excluded_from_recommendations": True,
        "success": True,
    }


# ─── Task 1.21: Pattern Dependency Tracking ───────────────────────────────────

def add_dependency(
    pattern_id: str,
    depends_on: list = None,
    conflicts_with: list = None,
    related_to: list = None,
) -> dict:
    """
    Task 1.21: Add dependency metadata to a pattern.

    Dependency types:
    - prerequisite (depends_on): Pattern A must be resolved before Pattern B
    - conflicts_with: Pattern A and B cannot both be active
    - related: Patterns that often appear together (correlation)
    """
    patterns = load_all_patterns()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)

    if not pattern:
        return {"error": f"Pattern {pattern_id} not found", "success": False}

    # Get existing dependencies
    current_deps = pattern.get("dependencies", [])
    current_conflicts = pattern.get("conflicts_with", [])

    # Extended dependency metadata (stored alongside SCHEMA_v2 fields)
    dependency_meta = pattern.get("dependency_metadata", {
        "prerequisite": [],
        "conflicts": [],
        "related": [],
    })

    if depends_on:
        for dep_id in depends_on:
            if dep_id not in current_deps:
                current_deps.append(dep_id)
            if dep_id not in dependency_meta["prerequisite"]:
                dependency_meta["prerequisite"].append(dep_id)

    if conflicts_with:
        for conf_id in conflicts_with:
            if conf_id not in current_conflicts:
                current_conflicts.append(conf_id)
            if conf_id not in dependency_meta["conflicts"]:
                dependency_meta["conflicts"].append(conf_id)

    if related_to:
        for rel_id in related_to:
            if rel_id not in dependency_meta["related"]:
                dependency_meta["related"].append(rel_id)

    pattern["dependencies"] = current_deps
    pattern["conflicts_with"] = current_conflicts
    pattern["dependency_metadata"] = dependency_meta
    pattern["updated_at"] = now_iso()

    save_all_patterns(patterns)

    return {
        "pattern_id": pattern_id,
        "dependencies": current_deps,
        "conflicts_with": current_conflicts,
        "dependency_metadata": dependency_meta,
        "success": True,
    }


def check_dependencies_for_fix(pattern_id: str, proposed_fix_patterns: list) -> dict:
    """
    Before proposing a fix, check if dependency constraints are satisfied.

    Returns:
        dict with can_proceed, blocking_reasons
    """
    pattern = load_pattern(pattern_id)
    if not pattern:
        return {"error": f"Pattern {pattern_id} not found"}

    all_patterns = load_all_patterns()
    blocking_reasons = []

    # Check prerequisites
    for prereq_id in pattern.get("dependencies", []):
        prereq = next((p for p in all_patterns if p.get("id") == prereq_id), None)
        if prereq:
            if prereq.get("confidence_tier") == "demoted":
                blocking_reasons.append(
                    f"Prerequisite '{prereq_id}' is demoted — resolve first"
                )

    # Check conflicts
    for conflict_id in pattern.get("conflicts_with", []):
        if conflict_id in proposed_fix_patterns:
            blocking_reasons.append(
                f"Conflict: cannot enable '{pattern_id}' and '{conflict_id}' simultaneously"
            )

    return {
        "pattern_id": pattern_id,
        "can_proceed": len(blocking_reasons) == 0,
        "blocking_reasons": blocking_reasons,
        "success": True,
    }


# ─── Task 1.22: Knowledge Base Analyzer ───────────────────────────────────────

def analyze_kb() -> dict:
    """
    Task 1.22: Analyze and validate knowledge base maturity.

    Metrics computed:
    1. Total patterns
    2. Patterns by tier (gold, production, experimental, demoted)
    3. Average confidence
    4. Coverage (% entity:component combos with ≥1 pattern)
    5. Learning speed (avg days to reach gold from creation)
    6. Dependency health (patterns with dependencies vs. without)

    Validation checks:
    - ≥50 patterns
    - ≥30% gold/production
    - >60% entity:component coverage
    """
    patterns = load_all_patterns()
    confidence_history = load_confidence_history()

    total = len(patterns)

    # Tier breakdown
    tier_counts = {"gold": 0, "production": 0, "experimental": 0, "demoted": 0}
    for p in patterns:
        tier = p.get("confidence_tier") or _confidence_tier(p.get("confidence", 0.5))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Average confidence
    avg_confidence = (
        round(sum(p.get("confidence", 0.5) for p in patterns) / total, 4)
        if total > 0 else 0.0
    )

    # Coverage
    covered_combos = set()
    for p in patterns:
        entity = p.get("entity")
        component = p.get("component")
        if entity and component:
            covered_combos.add((entity, component))

    coverage_pct = round(len(covered_combos) / TOTAL_ENTITY_COMPONENTS, 4) if TOTAL_ENTITY_COMPONENTS > 0 else 0.0

    # Learning speed: compute avg days from v1 to gold (from confidence history)
    learning_speed_days = _compute_learning_speed(patterns, confidence_history)

    # Dependency health
    with_deps = sum(1 for p in patterns if p.get("dependencies"))
    with_conflicts = sum(1 for p in patterns if p.get("conflicts_with"))

    # Validation checks
    checks = {
        "has_50_patterns": total >= 50,
        "has_30pct_gold_production": (
            (tier_counts["gold"] + tier_counts["production"]) / max(total, 1) >= 0.30
        ),
        "has_60pct_coverage": coverage_pct >= 0.60,
        "no_single_point_dependency": _check_no_single_point_dependency(patterns),
    }
    all_checks_pass = all(checks.values())

    metrics = {
        "total_patterns": total,
        "patterns_by_tier": tier_counts,
        "average_confidence": avg_confidence,
        "entity_component_coverage": {
            "covered": len(covered_combos),
            "total": TOTAL_ENTITY_COMPONENTS,
            "coverage_pct": coverage_pct,
        },
        "learning_speed_days_avg": learning_speed_days,
        "dependency_health": {
            "patterns_with_dependencies": with_deps,
            "patterns_with_conflicts": with_conflicts,
        },
        "validation_checks": checks,
        "all_checks_pass": all_checks_pass,
        "analyzed_at": now_iso(),
    }

    # Write KB_ANALYSIS.md
    _write_kb_analysis_report(metrics)

    return metrics


def _compute_learning_speed(patterns: list, history: list) -> float:
    """Avg days from pattern creation to gold confidence."""
    gold_times = []
    for p in patterns:
        if p.get("confidence_tier") != "gold" and p.get("confidence", 0) <= 0.85:
            continue
        created = p.get("created_at", "")
        updated = p.get("updated_at", "")
        if created and updated:
            try:
                t_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                t_updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                days = (t_updated - t_created).total_seconds() / 86400
                gold_times.append(days)
            except (ValueError, TypeError):
                pass
    return round(sum(gold_times) / len(gold_times), 2) if gold_times else 0.0


def _check_no_single_point_dependency(patterns: list) -> bool:
    """Check no single pattern is depended on by >80% of other patterns."""
    if not patterns:
        return True
    dep_counts: dict = {}
    for p in patterns:
        for dep in p.get("dependencies", []):
            dep_counts[dep] = dep_counts.get(dep, 0) + 1
    max_dep = max(dep_counts.values()) if dep_counts else 0
    return max_dep < len(patterns) * 0.8


def _write_kb_analysis_report(metrics: dict) -> None:
    """Write KB_ANALYSIS.md with metrics table and findings."""
    total = metrics["total_patterns"]
    tiers = metrics["patterns_by_tier"]
    coverage = metrics["entity_component_coverage"]
    checks = metrics["validation_checks"]
    avg_conf = metrics["average_confidence"]
    learning = metrics["learning_speed_days_avg"]
    dep_health = metrics["dependency_health"]
    all_pass = metrics["all_checks_pass"]
    analyzed = metrics["analyzed_at"]

    # Compute pass/fail symbols
    def sym(b): return "PASS" if b else "FAIL"

    content = f"""# Knowledge Base Health Analysis
**Generated:** {analyzed}
**Phase:** 1 (Task 1.22 deliverable)
**Requirements:** LEARN-06

---

## Metrics Summary

| Metric | Value | Status |
|--------|-------|--------|
| Total Patterns | {total} | {sym(total >= 50)} (target: >=50) |
| Gold Patterns | {tiers['gold']} | - |
| Production Patterns | {tiers['production']} | - |
| Experimental Patterns | {tiers['experimental']} | - |
| Demoted Patterns | {tiers['demoted']} | - |
| Gold+Production % | {(tiers['gold']+tiers['production'])/max(total,1):.0%} | {sym(checks['has_30pct_gold_production'])} (target: >=30%) |
| Average Confidence | {avg_conf:.4f} | - |
| Entity:Component Coverage | {coverage['coverage_pct']:.0%} ({coverage['covered']}/{coverage['total']}) | {sym(checks['has_60pct_coverage'])} (target: >60%) |
| Avg Days to Gold | {learning} days | - |
| Patterns w/ Dependencies | {dep_health['patterns_with_dependencies']} | - |
| Patterns w/ Conflicts | {dep_health['patterns_with_conflicts']} | - |

---

## Validation Checks

| Check | Result |
|-------|--------|
| >=50 patterns in KB | {sym(checks['has_50_patterns'])} |
| >=30% gold/production tier | {sym(checks['has_30pct_gold_production'])} |
| >60% entity:component coverage | {sym(checks['has_60pct_coverage'])} |
| No single-point dependency | {sym(checks['no_single_point_dependency'])} |

**Overall:** {"ALL CHECKS PASS" if all_pass else "SOME CHECKS FAILED — action required"}

---

## Tier Distribution

```
Gold:         {tiers['gold']:4d} patterns  [{tiers['gold']/max(total,1):.0%}]  🟡
Production:   {tiers['production']:4d} patterns  [{tiers['production']/max(total,1):.0%}]  🟢
Experimental: {tiers['experimental']:4d} patterns  [{tiers['experimental']/max(total,1):.0%}]  🟠
Demoted:      {tiers['demoted']:4d} patterns  [{tiers['demoted']/max(total,1):.0%}]  🔴
```

---

## Coverage Map

Entity:component combinations with at least 1 pattern: **{coverage['covered']}/{coverage['total']}** ({coverage['coverage_pct']:.0%})

---

## Findings & Recommendations

{"No issues found. KB is healthy." if all_pass else _findings_text(checks, total)}

---

*Run with: `python .claude/scripts/learning_engine.py --analyze-kb`*
*Schedule: Weekly or on-demand after major KB updates*
"""

    KB_ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(KB_ANALYSIS_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def _findings_text(checks: dict, total: int) -> str:
    findings = []
    if not checks["has_50_patterns"]:
        needed = 50 - total
        findings.append(f"- **KB size**: Only {total} patterns (need {needed} more to reach >=50 target)")
    if not checks["has_30pct_gold_production"]:
        findings.append("- **Tier quality**: Less than 30% of patterns are gold/production tier. Increase usage and fix rate.")
    if not checks["has_60pct_coverage"]:
        findings.append("- **Coverage gap**: Less than 60% of entity:component combinations have patterns. Create baseline patterns for uncovered combos.")
    if not checks["no_single_point_dependency"]:
        findings.append("- **Dependency risk**: A pattern is depended on by >80% of other patterns. Decouple or duplicate the dependency.")
    return "\n".join(findings)


# ─── Task 3: Tier Routing & Monitoring ─────────────────────────────────────────

def get_tier_for_confidence(confidence: float) -> dict:
    """
    Task 3: Get tier classification for a given confidence value.

    Used for monitoring and verification of tier assignments.
    """
    tier = _confidence_tier(confidence)

    # Tier descriptions
    tier_descriptions = {
        "gold": "Highest trust, protected. Used preferentially in agent recommendations.",
        "production": "Normal use. Trusted, actively recommended.",
        "experimental": "In development. Some use, under improvement.",
        "demoted": "Unreliable. Excluded from agent recommendations."
    }

    return {
        "confidence": confidence,
        "tier": tier,
        "description": tier_descriptions.get(tier, "Unknown"),
        "success": True
    }


def list_patterns_by_tier() -> dict:
    """
    Task 3: List all patterns grouped by tier.

    Used for audit and routing decisions.
    """
    patterns = load_all_patterns()
    patterns_by_tier = {
        "gold": [],
        "production": [],
        "experimental": [],
        "demoted": []
    }

    for pattern in patterns:
        tier = pattern.get("confidence_tier", _confidence_tier(pattern.get("confidence", 0.5)))
        if tier in patterns_by_tier:
            patterns_by_tier[tier].append(pattern.get("id", "unknown"))

    return {
        "patterns_by_tier": patterns_by_tier,
        "total_patterns": len(patterns),
        "total_gold": len(patterns_by_tier["gold"]),
        "total_production": len(patterns_by_tier["production"]),
        "total_experimental": len(patterns_by_tier["experimental"]),
        "total_demoted": len(patterns_by_tier["demoted"]),
        "success": True
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Learning Engine — confidence updates, versioning, auto-promotion, KB analysis"
    )
    parser.add_argument("--update-confidence", action="store_true")
    parser.add_argument("--version-pattern", action="store_true")
    parser.add_argument("--add-dependency", action="store_true")
    parser.add_argument("--analyze-kb", action="store_true")
    parser.add_argument("--auto-demote", action="store_true")
    parser.add_argument("--rollback-version", action="store_true")
    parser.add_argument("--get-tier-for-confidence", action="store_true")
    parser.add_argument("--list-patterns-by-tier", action="store_true")

    parser.add_argument("--pattern-id", help="Pattern ID")
    parser.add_argument("--delta", type=float, help="Confidence delta (+0.05 or -0.10)")
    parser.add_argument("--reason", help="Reason for confidence update")
    parser.add_argument("--task-id", help="Associated task ID")
    parser.add_argument("--fix-id", help="Associated fix ID")
    parser.add_argument("--depends-on", nargs="*", help="Prerequisite pattern IDs")
    parser.add_argument("--conflicts-with", nargs="*", help="Conflicting pattern IDs")
    parser.add_argument("--related-to", nargs="*", help="Related pattern IDs")
    parser.add_argument("--target-version", help="Version to roll back to (e.g., v1)")
    parser.add_argument("--confidence", type=float, help="Confidence value for tier lookup")

    args = parser.parse_args()

    if args.update_confidence:
        if not args.pattern_id or args.delta is None or not args.reason:
            print("ERROR: --update-confidence requires --pattern-id, --delta, and --reason", file=sys.stderr)
            sys.exit(1)
        result = update_confidence(
            pattern_id=args.pattern_id,
            delta=args.delta,
            reason=args.reason,
            task_id=args.task_id,
            fix_id=args.fix_id,
        )
        print(json.dumps(result, indent=2))
        return

    if args.version_pattern:
        if not args.pattern_id or not args.fix_id:
            print("ERROR: --version-pattern requires --pattern-id and --fix-id", file=sys.stderr)
            sys.exit(1)
        result = version_pattern(args.pattern_id, args.fix_id, args.reason)
        print(json.dumps(result, indent=2))
        return

    if args.add_dependency:
        if not args.pattern_id:
            print("ERROR: --add-dependency requires --pattern-id", file=sys.stderr)
            sys.exit(1)
        result = add_dependency(
            pattern_id=args.pattern_id,
            depends_on=args.depends_on,
            conflicts_with=args.conflicts_with,
            related_to=args.related_to,
        )
        print(json.dumps(result, indent=2))
        return

    if args.auto_demote:
        if not args.pattern_id:
            print("ERROR: --auto-demote requires --pattern-id", file=sys.stderr)
            sys.exit(1)
        result = auto_demote(args.pattern_id)
        print(json.dumps(result, indent=2))
        return

    if args.rollback_version:
        if not args.pattern_id or not args.target_version:
            print("ERROR: --rollback-version requires --pattern-id and --target-version", file=sys.stderr)
            sys.exit(1)
        result = rollback_to_version(args.pattern_id, args.target_version)
        print(json.dumps(result, indent=2))
        return

    if args.analyze_kb:
        result = analyze_kb()
        print(json.dumps(result, indent=2))
        return

    if args.get_tier_for_confidence:
        if args.confidence is None:
            print("ERROR: --get-tier-for-confidence requires --confidence <float>", file=sys.stderr)
            sys.exit(1)
        result = get_tier_for_confidence(args.confidence)
        print(json.dumps(result, indent=2))
        return

    if args.list_patterns_by_tier:
        result = list_patterns_by_tier()
        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
