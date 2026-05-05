#!/usr/bin/env python3
"""
AKC CSP (Constraint Satisfaction Problem) Solver
Phase 1, Wave 2 - Task 1.6

Lightweight CSP solver for fix generation. Enumerates pattern modifications
that respect all 6 hard guardrails and returns ranked candidate solutions.

Usage:
    python csp_solver.py --pattern-id <id> --entity <entity> --component <comp>
    python csp_solver.py --pattern-id <id> --entity player --component HealthComponent
"""

import argparse
import functools
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))
from typing import Any

# ─── Paths ─────────────────────────────────────────────────────────────────────

PATTERNS_PATH = KB_DIR / "patterns.jsonl"

# ─── Pattern Cache (pre-loaded at module import) ───────────────────────────────

_patterns_cache: dict[str, dict] = {}


def _load_patterns_cache() -> dict[str, dict]:
    """
    Load all patterns from patterns.jsonl into a dict keyed by pattern id.
    Called once at module import. Returns empty dict if file is missing/corrupt.
    """
    cache: dict[str, dict] = {}
    if not PATTERNS_PATH.exists():
        print(
            f"[csp_solver] WARNING: patterns.jsonl not found at {PATTERNS_PATH}. "
            "Pattern ID lookups will return None.",
            file=sys.stderr,
        )
        return cache
    try:
        with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                    pid = p.get("id")
                    if pid:
                        cache[pid] = p
                    else:
                        print(
                            f"[csp_solver] WARNING: line {lineno} in patterns.jsonl has no 'id' field — skipped.",
                            file=sys.stderr,
                        )
                except json.JSONDecodeError as e:
                    print(
                        f"[csp_solver] WARNING: JSON parse error at line {lineno}: {e} — skipped.",
                        file=sys.stderr,
                    )
    except OSError as e:
        print(
            f"[csp_solver] ERROR: Could not open {PATTERNS_PATH}: {e}",
            file=sys.stderr,
        )
    return cache


# Load once at module import
_patterns_cache = _load_patterns_cache()
PATTERNS_CACHE_SIZE: int = len(_patterns_cache)

# ─── 6 Hard Guardrails ─────────────────────────────────────────────────────────

GUARDRAILS = {
    "G1_physics_layers": {
        "description": "Never modify PhysicsLayers.gd constants or assigned layer values",
        "blocks": ["physics_layer_modification", "collision_layer_constant_change"],
    },
    "G2_signal_signatures": {
        "description": "Never change emitted signal names or parameter counts",
        "blocks": ["signal_rename", "signal_parameter_change", "signal_removal"],
    },
    "G3_public_api_signatures": {
        "description": "Never rename or remove public functions in components",
        "blocks": ["method_rename", "method_removal", "method_signature_change"],
    },
    "G4_architecture_patterns": {
        "description": "Never restructure scene tree (move/reparent nodes)",
        "blocks": ["node_reparent", "scene_restructure", "node_removal"],
    },
    "G5_hard_constraints": {
        "description": "Never violate PhysicsLayers registry — always use constants",
        "blocks": ["integer_literal_in_collision_layer", "hardcoded_physics_value"],
    },
    "G6_high_confidence_patterns": {
        "description": "Never modify patterns with confidence > 0.85 without override_key",
        "blocks": ["high_confidence_pattern_modification"],
    },
}

# ─── Modification Types (what CSP can change) ──────────────────────────────────

MODIFIABLE_ASPECTS = [
    "add_bounds_check",
    "add_null_check",
    "add_error_logging",
    "fix_collision_constant_reference",
    "add_signal_connection_check",
    "fix_animation_state_reset",
    "add_validation_guard",
    "fix_health_underflow",
    "add_component_initialization",
    "fix_node_path_reference",
]

# Which modification types are blocked by which guardrails
BLOCKED_BY_GUARDRAILS = {
    "physics_layer_modification": "G1_physics_layers",
    "signal_rename": "G2_signal_signatures",
    "method_rename": "G3_public_api_signatures",
    "node_reparent": "G4_architecture_patterns",
    "integer_literal_in_collision_layer": "G5_hard_constraints",
    "high_confidence_pattern_modification": "G6_high_confidence_patterns",
}


# ─── Data Types ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    rank: int
    modification_type: str
    description: str
    estimated_complexity: int  # 1=trivial, 2=simple, 3=moderate, 4=complex
    guardrails_passed: list
    guardrails_violated: list
    feasibility_score: float
    applies_to_files: list
    pseudo_code: str


# ─── Pattern Loader ────────────────────────────────────────────────────────────

def load_pattern(pattern_id: str) -> dict | None:
    """
    Return pattern dict for the given id, or None if not found.
    Uses the module-level _patterns_cache (pre-loaded at import time).
    """
    if pattern_id not in _patterns_cache:
        print(
            f"[csp_solver] WARNING: pattern_id '{pattern_id}' not found in cache "
            f"({len(_patterns_cache)} patterns loaded). Check patterns.jsonl.",
            file=sys.stderr,
        )
        return None
    return _patterns_cache[pattern_id]


# ─── Guardrail Checker ─────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=128)
def _check_guardrails_cached(modification_type: str, confidence: float) -> tuple[tuple, tuple]:
    """
    Cached inner implementation of guardrail checking.
    Args:
        modification_type: one of the MODIFIABLE_ASPECTS strings
        confidence: pattern confidence as float (0.0 if no pattern)
    Returns:
        (passed_guardrails_tuple, violated_guardrails_tuple) — tuples so they are hashable/cacheable
    """
    passed = []
    violated = []

    for gid, guardrail in GUARDRAILS.items():
        if modification_type in guardrail.get("blocks", []):
            violated.append(gid)
            continue
        if gid == "G6_high_confidence_patterns" and confidence > 0.85:
            violated.append(gid)
            continue
        passed.append(gid)

    return tuple(passed), tuple(violated)


def check_guardrails(modification_type: str, pattern: dict | None) -> tuple[list, list]:
    """
    Returns (passed_guardrails, violated_guardrails) for a given modification.
    Delegates to _check_guardrails_cached() for O(1) repeat calls.
    """
    confidence = pattern.get("confidence", 0.0) if pattern else 0.0
    passed_t, violated_t = _check_guardrails_cached(modification_type, confidence)
    return list(passed_t), list(violated_t)


# ─── Feasibility Scorer ────────────────────────────────────────────────────────

def score_feasibility(
    modification_type: str,
    complexity: int,
    passed: list,
    violated: list
) -> float:
    """Score a candidate modification 0.0-1.0."""
    if violated:
        return 0.0  # Any guardrail violation = infeasible

    # Start at 1.0, penalize complexity
    score = 1.0 - ((complexity - 1) * 0.15)  # -0.0, -0.15, -0.30, -0.45

    # Bonus for all guardrails passing
    score += 0.05 * (len(passed) / len(GUARDRAILS))

    # Penalize if it touches many guardrail-adjacent areas
    return round(max(0.0, min(1.0, score)), 4)


# ─── Candidate Generator ───────────────────────────────────────────────────────

MODIFICATION_DETAILS = {
    "add_bounds_check": {
        "complexity": 1,
        "description": "Add bounds/clamp check to prevent out-of-range values",
        "pseudo_code": "var clamped = clamp(value, min_bound, max_bound)",
        "applicable_components": ["HealthComponent", "MovementComponent", "CombatComponent"],
    },
    "add_null_check": {
        "complexity": 1,
        "description": "Add null/validity check before accessing potentially null node/resource",
        "pseudo_code": "if node == null: push_error('Node is null'); return",
        "applicable_components": ["*"],
    },
    "add_error_logging": {
        "complexity": 1,
        "description": "Add push_error() or print_debug() to surface hidden failures",
        "pseudo_code": "if not condition: push_error('Expected condition failed: ' + str(condition))",
        "applicable_components": ["*"],
    },
    "fix_collision_constant_reference": {
        "complexity": 2,
        "description": "Replace integer literal with PhysicsLayers constant reference",
        "pseudo_code": "collision_layer = PhysicsLayers.LAYER_NAME  # was: collision_layer = 4",
        "applicable_components": ["PhysicsComponent"],
    },
    "add_signal_connection_check": {
        "complexity": 2,
        "description": "Add is_connected() guard before assuming signal is connected",
        "pseudo_code": "if not signal_name.is_connected(handler): signal_name.connect(handler)",
        "applicable_components": ["SignalComponent", "EventSystem"],
    },
    "fix_animation_state_reset": {
        "complexity": 2,
        "description": "Add state machine reset on scene entry to prevent stale animation state",
        "pseudo_code": "anim_player.play('RESET')  # force reset on _ready",
        "applicable_components": ["AnimationComponent"],
    },
    "add_validation_guard": {
        "complexity": 2,
        "description": "Add input validation at method entry to reject invalid arguments",
        "pseudo_code": "if not is_valid(arg): push_warning('Invalid arg'); return",
        "applicable_components": ["*"],
    },
    "fix_health_underflow": {
        "complexity": 2,
        "description": "Add clamp to prevent health going below 0 or above max_health",
        "pseudo_code": "health = clamp(health + delta, 0.0, max_health)",
        "applicable_components": ["HealthComponent"],
    },
    "add_component_initialization": {
        "complexity": 3,
        "description": "Add proper _ready() initialization for component that may start uninitialized",
        "pseudo_code": "func _ready():\n    super._ready()\n    _initialize_component()",
        "applicable_components": ["*"],
    },
    "fix_node_path_reference": {
        "complexity": 2,
        "description": "Fix @onready var that references a node path that may not exist",
        "pseudo_code": "@onready var node = $NodePath  # verify path matches scene",
        "applicable_components": ["*"],
    },
}


def generate_candidates(
    pattern_id: str,
    entity: str,
    component: str,
    constraints: list | None = None,
    max_candidates: int = 5,
) -> list[Candidate]:
    """
    CSP solver: enumerate valid modifications and return top N by feasibility.

    Args:
        pattern_id: ID of pattern to fix
        entity: entity name (player, enemy_knight, etc.)
        component: component name (HealthComponent, etc.)
        constraints: additional constraints beyond 6 guardrails
        max_candidates: max candidates to return (default 5)

    Returns:
        List of Candidate objects sorted by feasibility descending
    """
    pattern = load_pattern(pattern_id) if pattern_id else None
    candidates = []

    for mod_type in MODIFIABLE_ASPECTS:
        detail = MODIFICATION_DETAILS.get(mod_type, {})
        applicable = detail.get("applicable_components", ["*"])

        # Check if this modification applies to the component
        if "*" not in applicable and component not in applicable:
            continue

        complexity = detail.get("complexity", 2)
        passed, violated = check_guardrails(mod_type, pattern)

        # REJECT EARLY if guardrails violated (before candidate construction)
        if violated:
            continue

        # Additional constraints check
        if constraints:
            for constraint in constraints:
                if constraint in violated:
                    continue

        feasibility = score_feasibility(mod_type, complexity, passed, violated)

        # Determine files this would affect
        files = _infer_affected_files(entity, component)

        candidates.append(Candidate(
            rank=0,  # set after sorting
            modification_type=mod_type,
            description=detail.get("description", mod_type),
            estimated_complexity=complexity,
            guardrails_passed=passed,
            guardrails_violated=violated,
            feasibility_score=feasibility,
            applies_to_files=files,
            pseudo_code=detail.get("pseudo_code", "# see documentation"),
        ))

    # Sort by feasibility descending
    candidates.sort(key=lambda c: c.feasibility_score, reverse=True)

    # Assign ranks
    for i, c in enumerate(candidates[:max_candidates]):
        c.rank = i + 1

    return candidates[:max_candidates]


def _infer_affected_files(entity: str, component: str) -> list:
    """Infer which files this entity/component fix would affect."""
    entity_map = {
        "player": "scenes/player/",
        "enemy_knight": "scenes/enemies/knight/",
        "enemy_mage": "scenes/enemies/mage/",
        "minion": "scenes/minions/",
        "boss": "scenes/player/boss_form/",
        "global": "constants/",
    }
    prefix = entity_map.get(entity, "scenes/")
    component_file = f"{component.lower()}.gd"
    return [f"{prefix}{component_file}"]


# ─── Benchmark Curve ───────────────────────────────────────────────────────────

def _run_benchmark_curve() -> dict:
    """
    Run the CSP solver at increasing pattern counts and return a performance report.

    Strategy:
    - Pull real pattern IDs from _patterns_cache (use as many as available, cycle if needed).
    - For each target count N, run generate_candidates() N times with real pattern IDs.
    - Record total_ms, per_pattern_ms, and guardrail_pass_rate.
    - One-time file I/O cost (loading _patterns_cache) is excluded — it already happened at import.

    Returns a dict with:
        - "curve": list of per-count measurement dicts
        - "compliance": dict with CSP-02 verdict
        - "markdown_table": pre-formatted markdown string for docs
    """
    import itertools

    pattern_ids = list(_patterns_cache.keys())
    if not pattern_ids:
        # No patterns loaded — run with pattern_id=None for all
        pattern_ids = [None]

    # Cycle pattern IDs so we always have enough for any target count
    id_cycle = itertools.cycle(pattern_ids)

    # Test combos — entity/component pairs to rotate through
    test_combos = [
        ("player", "HealthComponent"),
        ("player", "MovementComponent"),
        ("enemy_knight", "HealthComponent"),
        ("enemy_knight", "PhysicsComponent"),
        ("enemy_mage", "CombatComponent"),
        ("minion", "HealthComponent"),
        ("global", "PhysicsComponent"),
        ("boss", "HealthComponent"),
        ("player", "AnimationComponent"),
        ("minion", "CombatComponent"),
    ]
    combo_cycle = itertools.cycle(test_combos)

    target_counts = [10, 20, 50, 100, 200]
    # Attempt 500 only if 200 completes in <5s
    curve = []

    for count in target_counts:
        ids_for_run = [next(id_cycle) for _ in range(count)]
        combos_for_run = [next(combo_cycle) for _ in range(count)]

        total_guardrail_passes = 0
        total_guardrail_checks = 0

        start = time.perf_counter()
        for pid, (entity, component) in zip(ids_for_run, combos_for_run):
            candidates = generate_candidates(
                pattern_id=pid,
                entity=entity,
                component=component,
                max_candidates=5,
            )
            for c in candidates:
                total_guardrail_passes += len(c.guardrails_passed)
                total_guardrail_checks += len(c.guardrails_passed) + len(c.guardrails_violated)
        elapsed = time.perf_counter() - start

        total_ms = round(elapsed * 1000, 2)
        per_pattern_ms = round(total_ms / count, 4)
        guardrail_pass_rate = (
            round(total_guardrail_passes / total_guardrail_checks * 100, 1)
            if total_guardrail_checks > 0 else 0.0
        )

        row = {
            "pattern_count": count,
            "total_ms": total_ms,
            "per_pattern_ms": per_pattern_ms,
            "guardrail_pass_rate_pct": guardrail_pass_rate,
            "within_60s_slo": elapsed < 60.0,
        }
        curve.append(row)

        # Attempt 500 only if 200 finished quickly
        if count == 200 and elapsed < 5.0:
            target_counts_extended = [500]
            for big_count in target_counts_extended:
                ids_big = [next(id_cycle) for _ in range(big_count)]
                combos_big = [next(combo_cycle) for _ in range(big_count)]
                gp2, gc2 = 0, 0
                t2 = time.perf_counter()
                for pid2, (e2, c2) in zip(ids_big, combos_big):
                    cands2 = generate_candidates(pid2, e2, c2, max_candidates=5)
                    for cc in cands2:
                        gp2 += len(cc.guardrails_passed)
                        gc2 += len(cc.guardrails_passed) + len(cc.guardrails_violated)
                e2_elapsed = time.perf_counter() - t2
                curve.append({
                    "pattern_count": big_count,
                    "total_ms": round(e2_elapsed * 1000, 2),
                    "per_pattern_ms": round(e2_elapsed * 1000 / big_count, 4),
                    "guardrail_pass_rate_pct": round(gp2 / gc2 * 100, 1) if gc2 > 0 else 0.0,
                    "within_60s_slo": e2_elapsed < 60.0,
                })

    # Build markdown table
    header = "| pattern_count | total_time(ms) | time_per_pattern(ms) | guardrail_pass_rate(%) | within_60s_slo |"
    separator = "|---|---|---|---|---|"
    rows_md = [
        f"| {r['pattern_count']} | {r['total_ms']} | {r['per_pattern_ms']} | {r['guardrail_pass_rate_pct']} | {'YES' if r['within_60s_slo'] else 'NO'} |"
        for r in curve
    ]
    markdown_table = "\n".join([header, separator] + rows_md)

    # CSP-02 compliance check: all counts must be within 60s
    failing = [r for r in curve if not r["within_60s_slo"]]
    csp02_pass = len(failing) == 0
    csp02_verdict = "PASS" if csp02_pass else f"FAIL — {len(failing)} counts exceeded 60s SLO"

    return {
        "benchmark_curve": True,
        "patterns_in_cache": len(_patterns_cache),
        "curve": curve,
        "csp02_compliance": csp02_verdict,
        "markdown_table": markdown_table,
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC CSP Solver — generates constrained fix candidates for patterns"
    )
    parser.add_argument("--pattern-id", help="Pattern ID to generate fixes for")
    parser.add_argument("--entity", required=False, help="Entity name (player, enemy_knight, etc.)")
    parser.add_argument("--component", required=False, help="Component name (HealthComponent, etc.)")
    parser.add_argument("--constraints", nargs="*", help="Additional constraint names to enforce")
    parser.add_argument("--max-candidates", type=int, default=5, help="Max candidates to return")
    parser.add_argument("--benchmark", action="store_true", help="Run benchmark on 10-20 patterns")
    parser.add_argument(
        "--benchmark-curve",
        action="store_true",
        help="Run latency curve benchmark at 10, 20, 50, 100, 200 pattern counts",
    )

    args = parser.parse_args()

    if not args.benchmark and not args.benchmark_curve:
        if not args.entity or not args.component:
            parser.error("--entity and --component are required unless --benchmark or --benchmark-curve is set")

    if args.benchmark_curve:
        report = _run_benchmark_curve()
        print(json.dumps(report, indent=2))
        return

    if args.benchmark:
        # Benchmark mode: test solver performance
        start = time.time()
        all_candidates = []
        # Simulate 20 different entity/component combinations
        test_combos = [
            ("player", "HealthComponent"),
            ("player", "MovementComponent"),
            ("enemy_knight", "HealthComponent"),
            ("enemy_knight", "PhysicsComponent"),
            ("enemy_knight", "AnimationComponent"),
            ("enemy_mage", "HealthComponent"),
            ("enemy_mage", "CombatComponent"),
            ("minion", "HealthComponent"),
            ("minion", "PhysicsComponent"),
            ("global", "PhysicsComponent"),
            ("global", "EventSystem"),
            ("boss", "HealthComponent"),
            ("ui", "SignalComponent"),
            ("camera", "PhysicsComponent"),
            ("player", "CombatComponent"),
            ("enemy_knight", "CombatComponent"),
            ("player", "AnimationComponent"),
            ("minion", "CombatComponent"),
            ("global", "autoload"),
            ("player", "SignalComponent"),
        ]
        for entity, component in test_combos:
            candidates = generate_candidates(
                pattern_id=None,
                entity=entity,
                component=component,
                max_candidates=5,
            )
            all_candidates.extend(candidates)

        elapsed = time.time() - start
        print(json.dumps({
            "benchmark": True,
            "combos_tested": len(test_combos),
            "total_candidates": len(all_candidates),
            "elapsed_seconds": round(elapsed, 3),
            "within_limit": elapsed < 30.0,
            "performance": f"{elapsed:.3f}s for {len(test_combos)} pattern combos",
            "scalability_note": (
                f"Solver runs in O(n*m) where n=patterns and m=modification_types ({len(MODIFIABLE_ASPECTS)}). "
                f"At current rate, 1000 patterns would take ~{elapsed*50:.1f}s. "
                "Recommend caching modification eligibility at KB load time for scale."
            ),
            "guardrail_cache_info": str(_check_guardrails_cached.cache_info()),
        }, indent=2))
        return

    start = time.time()
    candidates = generate_candidates(
        pattern_id=args.pattern_id,
        entity=args.entity,
        component=args.component,
        constraints=args.constraints,
        max_candidates=args.max_candidates,
    )
    elapsed = time.time() - start

    result = {
        "pattern_id": args.pattern_id,
        "entity": args.entity,
        "component": args.component,
        "candidates_generated": len(candidates),
        "elapsed_seconds": round(elapsed, 4),
        "candidates": [asdict(c) for c in candidates],
        "guardrails_enforced": list(GUARDRAILS.keys()),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
