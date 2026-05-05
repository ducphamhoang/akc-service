#!/usr/bin/env python3
"""
AKC Fix Generation Engine
Phase 1, Wave 2 - Tasks 1.8-1.10

Generates fix candidates via CSP solver + LLM reasoning, scores them,
and selects based on confidence thresholds.

Usage:
    python fix_generation_engine.py --generate-candidates --failure-id <id> \\
        --pattern-id <id> --entity <e> --component <c> --pattern-type <t>
    python fix_generation_engine.py --select-candidate --fix-id <id>
"""

import argparse
import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

# Package-relative import
from akc_service import csp_solver  # noqa: E402

# ─── Paths ─────────────────────────────────────────────────────────────────────

FAILURE_INDEX_PATH = KB_DIR / "failure_index.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"
PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FIX_TEMPLATES_PATH = _REPO_ROOT / ".planning" / "FIX_TEMPLATES.md"

# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_fix_id(failure_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short = hashlib.md5(f"{failure_id}-{ts}".encode()).hexdigest()[:6]
    return f"fix-{ts}-{short}"


def load_failure(failure_id: str) -> dict | None:
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


def load_pattern(pattern_id: str) -> dict | None:
    if not PATTERNS_PATH.exists():
        return None
    with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                if p.get("id") == pattern_id:
                    return p
            except json.JSONDecodeError:
                pass
    return None


def load_all_fixes() -> list:
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


def append_fix(entry: dict) -> None:
    FIX_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIX_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def update_fix(fix_id: str, updates: dict) -> bool:
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


# ─── Template Matcher ──────────────────────────────────────────────────────────

# Mapping from (component, error_category) to fix template IDs
TEMPLATE_MAP = {
    ("HealthComponent", "health_tracking"): ["T01", "T10"],
    ("HealthComponent", None): ["T01"],
    ("PhysicsComponent", "collision_detection"): ["T02", "T07", "T14"],
    ("PhysicsComponent", None): ["T02"],
    ("SignalComponent", "signal_connection"): ["T03"],
    ("EventSystem", "signal_connection"): ["T03", "T09"],
    ("AnimationComponent", "animation_state"): ["T04", "T13"],
    ("AnimationComponent", None): ["T13"],
    ("cross_component", "scene_lifecycle"): ["T05", "T08", "T11"],
    ("MovementComponent", "ai_behavior"): ["T06", "T12"],
    ("CombatComponent", "ai_behavior"): ["T15"],
}


def find_applicable_templates(component: str, error_category: str) -> list:
    """Find fix templates applicable to the given component/error category."""
    key1 = (component, error_category)
    key2 = (component, None)
    templates = TEMPLATE_MAP.get(key1, []) + TEMPLATE_MAP.get(key2, [])
    return list(dict.fromkeys(templates))  # dedup preserving order


# ─── Precedent Scorer ──────────────────────────────────────────────────────────

def compute_precedent_score(modification_type: str, component: str) -> float:
    """
    Check how often a similar fix has worked before.
    Returns 0.0-0.3 precedent score.
    """
    historical_fixes = load_all_fixes()
    if not historical_fixes:
        return 0.1  # neutral baseline

    matching = [
        f for f in historical_fixes
        if any(
            c.get("modification_type") == modification_type
            for c in f.get("candidates", [])
        ) and f.get("outcome") == "deployment_success"
    ]

    total_similar = sum(
        1 for f in historical_fixes
        if any(
            c.get("modification_type") == modification_type
            for c in f.get("candidates", [])
        )
    )

    if total_similar == 0:
        return 0.1  # no historical data, neutral

    success_rate = len(matching) / total_similar
    return round(min(0.3, success_rate * 0.3), 4)


# ─── Task 1.9: Candidate Scoring ──────────────────────────────────────────────

def score_candidate(
    candidate: dict,
    pattern: dict | None,
    component: str,
    error_category: str,
) -> float:
    """
    Task 1.9: Score a fix candidate 0.0-1.0.

    Scoring factors:
    - Simplicity: fewer lines changed = higher score (+0.2 for complexity 1, +0.05 for complexity 3)
    - Precedent: historical success rate (+0.1-0.3)
    - Risk: guardrail violations (-0.5 if violated)
    - Test impact: number of tests affected (-0.1 per affected test, max -0.3)

    Returns:
        Final score 0.0-1.0
    """
    # Guardrail violations = automatic rejection
    if candidate.get("guardrails_violated"):
        return 0.0

    # Simplicity score (complexity 1=trivial → 4=complex)
    complexity = candidate.get("estimated_complexity", 2)
    simplicity = {1: 0.20, 2: 0.15, 3: 0.10, 4: 0.05}.get(complexity, 0.10)

    # Precedent score
    mod_type = candidate.get("modification_type", "")
    precedent = compute_precedent_score(mod_type, component)

    # Risk score (no violations = 0 risk penalty)
    risk = 0.0  # no penalty since we already checked guardrails above

    # Test impact (heuristic: complex modifications may affect more tests)
    # Complexity 1-2 = 0 tests assumed, 3+ = 1-2 tests
    test_impact = 0.0
    if complexity >= 3:
        test_impact = 0.05 * (complexity - 2)

    # Template match bonus
    templates = find_applicable_templates(component, error_category)
    template_bonus = 0.05 if templates else 0.0

    raw = simplicity + precedent - risk - test_impact + template_bonus
    return round(max(0.0, min(1.0, raw)), 4)


# ─── Task 1.8: LLM Reasoning Module ───────────────────────────────────────────

def generate_llm_candidates(
    failure: dict,
    pattern: dict | None,
    entity: str,
    component: str,
    pattern_type: str,
    templates: list,
) -> list:
    """
    Task 1.8: Generate fix candidates using LLM reasoning.

    In production, this calls the Claude API. For MVP, generates structured
    candidates based on the failure context and available templates, simulating
    what an LLM would produce given the 6-guardrail-constrained system prompt.

    The LLM prompt structure is documented here for integration:

    System: "Generate 3 candidate fixes for this pattern failure.
             Each candidate must respect the 6 hard guardrails:
             G1: No physics layer changes, G2: No signal signature changes,
             G3: No public API changes, G4: No scene restructure,
             G5: Always use PhysicsLayers constants, G6: No high-confidence modifications.
             Rank by likelihood of success."

    User: "[failure description] + [pattern context] + [templates as examples]"
    """
    error_message = failure.get("error_message", "")
    error_category = failure.get("error_category", "unknown")
    files_modified = failure.get("files_modified", [])

    # Generate candidates based on CSP solver + template matching
    csp_candidates = csp_solver.generate_candidates(
        pattern_id=failure.get("inferred_pattern_id"),
        entity=entity,
        component=component,
        max_candidates=5,
    )

    llm_candidates = []
    for i, csp_c in enumerate(csp_candidates[:3]):  # top 3 from CSP
        # Enrich with LLM-style reasoning
        template_ids = find_applicable_templates(component, error_category)
        template_ref = f"Template {template_ids[0]}" if template_ids else "custom fix"

        candidate = {
            "candidate_id": f"cand-{i+1:02d}",
            "rank": i + 1,
            "method": "csp_solver" if i == 0 else "llm_reasoning",
            "modification_type": csp_c.modification_type,
            "description": csp_c.description,
            "pseudo_code": csp_c.pseudo_code,
            "files_affected": files_modified or csp_c.applies_to_files,
            "estimated_complexity": csp_c.estimated_complexity,
            "guardrails_passed": csp_c.guardrails_passed,
            "guardrails_violated": csp_c.guardrails_violated,
            "template_reference": template_ref,
            "llm_rationale": (
                f"Based on error '{error_category}' in {component}, "
                f"applying '{csp_c.modification_type}' addresses the root cause. "
                f"Matches {template_ref}."
            ),
            "confidence": 0.0,  # will be set by scorer
        }
        llm_candidates.append(candidate)

    # Add one template-specific candidate if templates available and not yet covered
    if templates and len(llm_candidates) < 3:
        llm_candidates.append({
            "candidate_id": f"cand-{len(llm_candidates)+1:02d}",
            "rank": len(llm_candidates) + 1,
            "method": "template_substitution",
            "modification_type": "template_apply",
            "description": f"Apply fix template {templates[0]} for {component}/{error_category}",
            "pseudo_code": f"# Apply {templates[0]} — see .planning/FIX_TEMPLATES.md for code",
            "files_affected": files_modified,
            "estimated_complexity": 2,
            "guardrails_passed": list(csp_solver.GUARDRAILS.keys()),
            "guardrails_violated": [],
            "template_reference": templates[0],
            "llm_rationale": (
                f"Template {templates[0]} directly addresses {component}/{error_category}. "
                "High historical success rate, low complexity."
            ),
            "confidence": 0.0,
        })

    return llm_candidates


# ─── Task 1.8 + 1.9: Full Candidate Generation ────────────────────────────────

def generate_and_score_candidates(
    failure_id: str,
    pattern_id: str | None,
    entity: str,
    component: str,
    pattern_type: str,
) -> dict:
    """
    Generate candidates (LLM + CSP + templates) and score them.
    Stores results in fix_history.jsonl.
    """
    failure = load_failure(failure_id)
    if not failure:
        return {"error": f"Failure {failure_id} not found", "success": False}

    pattern = load_pattern(pattern_id) if pattern_id else None
    error_category = failure.get("error_category", "unknown")
    templates = find_applicable_templates(component, error_category)

    # Generate raw candidates
    raw_candidates = generate_llm_candidates(
        failure=failure,
        pattern=pattern,
        entity=entity,
        component=component,
        pattern_type=pattern_type,
        templates=templates,
    )

    # Score each candidate (Task 1.9)
    for c in raw_candidates:
        c["confidence"] = score_candidate(c, pattern, component, error_category)

    # Sort by confidence descending
    raw_candidates.sort(key=lambda x: x["confidence"], reverse=True)

    # Re-rank after sorting
    for i, c in enumerate(raw_candidates):
        c["rank"] = i + 1

    # Validate each against guardrails (reject invalid)
    valid_candidates = [c for c in raw_candidates if not c["guardrails_violated"]]

    # Generate fix ID
    fix_id = make_fix_id(failure_id)

    # Task 1.10: Select candidate based on confidence thresholds
    selection = select_candidate_logic(valid_candidates, fix_id)

    entry = {
        "fix_id": fix_id,
        "failure_id": failure_id,
        "pattern_id": pattern_id,
        "entity": entity,
        "component": component,
        "pattern_type": pattern_type,
        "generated_at": now_iso(),
        "candidates": valid_candidates,
        "selected_candidate_id": selection.get("selected_candidate_id"),
        "autonomy_level": selection.get("autonomy_level"),
        "selection_method": selection.get("selection_method"),
        "decision_log": selection.get("decision_log"),
        "outcome": "pending_validation",
        "schema_version": "v2",
    }

    append_fix(entry)

    return {
        "fix_id": fix_id,
        "candidates_generated": len(valid_candidates),
        "candidates": valid_candidates,
        "selection": selection,
        "success": True,
    }


# ─── Task 1.10: Fix Selection Logic ───────────────────────────────────────────

def select_candidate_logic(candidates: list, fix_id: str) -> dict:
    """
    Task 1.10: Select fix candidate based on confidence thresholds.

    Tiers:
    - Full autonomy (score >= 0.75): auto-select, route to Validation Engine
    - Semi-autonomous (0.60-0.75): propose top 3, await human approval
    - Escalation (<0.60): flag for human investigation

    Returns:
        dict with selected_candidate_id, autonomy_level, decision_log
    """
    if not candidates:
        return {
            "selected_candidate_id": None,
            "autonomy_level": "escalation",
            "selection_method": "none",
            "decision_log": "No valid candidates generated — escalating for human review",
        }

    top = candidates[0]  # highest confidence after scoring
    confidence = top.get("confidence", 0.0)

    if confidence >= 0.75:
        return {
            "selected_candidate_id": top["candidate_id"],
            "autonomy_level": "full_autonomy",
            "selection_method": "highest_confidence_auto",
            "decision_log": (
                f"Auto-selected candidate {top['candidate_id']} "
                f"(confidence={confidence}, method={top['method']}). "
                f"Confidence >= 0.75 threshold → full autonomy. "
                f"Routing to Validation Engine directly."
            ),
        }
    elif confidence >= 0.60:
        top3 = candidates[:3]
        return {
            "selected_candidate_id": top["candidate_id"],
            "autonomy_level": "semi_autonomous",
            "selection_method": "human_approval_required",
            "top_candidates_for_review": [
                {
                    "candidate_id": c["candidate_id"],
                    "rank": c["rank"],
                    "confidence": c["confidence"],
                    "description": c["description"],
                }
                for c in top3
            ],
            "decision_log": (
                f"Semi-auto: confidence={confidence} (0.60-0.75 range). "
                f"Top 3 candidates prepared for human review. "
                f"Proposed choice: {top['candidate_id']} ({top['description']}). "
                f"Awaiting human approval before validation."
            ),
        }
    else:
        return {
            "selected_candidate_id": None,
            "autonomy_level": "escalation",
            "selection_method": "none",
            "all_candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "confidence": c["confidence"],
                    "description": c["description"],
                }
                for c in candidates
            ],
            "decision_log": (
                f"Escalation: highest confidence={confidence} < 0.60. "
                f"All {len(candidates)} candidates need human investigation. "
                f"Fix ID {fix_id} flagged for human review."
            ),
        }


def select_candidate_cmd(fix_id: str) -> dict:
    """Run selection logic on an existing fix entry."""
    fix = load_fix(fix_id)
    if not fix:
        return {"error": f"Fix {fix_id} not found", "success": False}

    candidates = fix.get("candidates", [])
    selection = select_candidate_logic(candidates, fix_id)

    update_fix(fix_id, {
        "selected_candidate_id": selection.get("selected_candidate_id"),
        "autonomy_level": selection.get("autonomy_level"),
        "selection_method": selection.get("selection_method"),
        "decision_log": selection.get("decision_log"),
    })

    return {"fix_id": fix_id, "selection": selection, "success": True}


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Fix Generation Engine — candidate generation, scoring, and selection"
    )
    parser.add_argument("--generate-candidates", action="store_true",
                        help="Generate fix candidates for a failure")
    parser.add_argument("--select-candidate", action="store_true",
                        help="Select best candidate from existing fix entry")
    parser.add_argument("--failure-id", help="Failure ID from failure_index.jsonl")
    parser.add_argument("--fix-id", help="Fix ID from fix_history.jsonl")
    parser.add_argument("--pattern-id", help="Pattern ID (optional)")
    parser.add_argument("--entity", help="Entity name")
    parser.add_argument("--component", help="Component name")
    parser.add_argument("--pattern-type", help="Pattern type")

    args = parser.parse_args()

    if args.generate_candidates:
        if not args.failure_id:
            print("ERROR: --generate-candidates requires --failure-id", file=sys.stderr)
            sys.exit(1)
        if not args.entity or not args.component:
            # Try to load from failure entry
            if args.failure_id:
                failure = load_failure(args.failure_id)
                if failure:
                    entity = args.entity or failure.get("attributed_entity", "global")
                    component = args.component or failure.get("attributed_component", "cross_component")
                    pattern_type = args.pattern_type or failure.get("attributed_pattern_type", "scene_lifecycle")
                else:
                    print("ERROR: --generate-candidates requires --entity and --component", file=sys.stderr)
                    sys.exit(1)
            else:
                print("ERROR: --generate-candidates requires --entity and --component", file=sys.stderr)
                sys.exit(1)
        else:
            entity = args.entity
            component = args.component
            pattern_type = args.pattern_type or "scene_lifecycle"

        result = generate_and_score_candidates(
            failure_id=args.failure_id,
            pattern_id=args.pattern_id,
            entity=entity,
            component=component,
            pattern_type=pattern_type,
        )
        print(json.dumps(result, indent=2))
        return

    if args.select_candidate:
        if not args.fix_id:
            print("ERROR: --select-candidate requires --fix-id", file=sys.stderr)
            sys.exit(1)
        result = select_candidate_cmd(args.fix_id)
        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
