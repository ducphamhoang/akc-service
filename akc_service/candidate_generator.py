#!/usr/bin/env python3
"""
AKC Fix Candidate Generator
Phase 3, Plan 01 - Task 2

Generates 3-5 ranked fix candidates per failure using three-tier strategy:
  - Tier 1: Template-based (0.80-0.95 confidence) — common failure patterns
  - Tier 2: CSP-based (0.70-0.85 confidence) — constraint satisfaction fixes
  - Tier 3: LLM-based (0.60-0.75 confidence) — complex failures (SDK required)

Scoring model: base_score * pattern_confidence * risk_adjustment, clamped [0.60, 0.95]
Ranking: primary=confidence, secondary=risk, tertiary=complexity

Usage:
    python candidate_generator.py --generate-candidates --failure-id '<id>'
    python candidate_generator.py --rank-candidates --candidate-list '<json>'
    python candidate_generator.py --score-candidate --candidate-id '<id>'
    python candidate_generator.py --test-generation
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))
SCRIPTS_DIR = Path(os.environ.get("AKC_SERVICE_SCRIPTS_DIR", str(Path(__file__).parent)))

# ─── Paths ─────────────────────────────────────────────────────────────────────

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FAILURE_SIGS_PATH = SCRIPTS_DIR / "failure_signatures.jsonl"
GENERATED_CANDIDATES_PATH = SCRIPTS_DIR / "generated_candidates.jsonl"
SDK_FALLBACK_PATH = SCRIPTS_DIR / "sdk_fallback.jsonl"

# ─── Scoring Constants ──────────────────────────────────────────────────────────

# Base scores by generation tier
BASE_SCORES = {
    "template": 0.90,   # Tier 1: highest confidence
    "csp": 0.75,        # Tier 2: medium confidence
    "llm": 0.65,        # Tier 3: lower confidence
    "csp_fallback": 0.68,  # CSP used as LLM fallback when SDK unavailable
}

# Risk factor adjustments
RISK_ADJUSTMENTS = {
    "architecture_change": -0.05,
    "physics_layer_change": -0.10,
    "proven_pattern": +0.05,
    "signal_change": -0.05,
    "standard_fix": 0.00,
}

# Confidence clamp bounds
SCORE_MIN = 0.60
SCORE_MAX = 0.95

# Risk level definitions
RISK_LEVELS = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


# ─── Tier 1: Template-Based Fixes ──────────────────────────────────────────────

# Templates for common failure patterns
FIX_TEMPLATES = {
    "missing_component": [
        {
            "template_id": "tmpl-missing-health",
            "name": "Add HealthComponent with default health",
            "description": "Attach HealthComponent node to entity and set max_health=100",
            "applies_to_component": "HealthComponent",
            "pseudo_code": (
                "# In scene .tscn: add HealthComponent child node\n"
                "# In entity script _ready():\n"
                "@onready var health_component = $HealthComponent\n"
                "func _ready():\n"
                "    if not health_component:\n"
                "        push_error('HealthComponent missing on ' + name)\n"
                "        return\n"
                "    health_component.max_health = 100\n"
                "    health_component.health = 100"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
        {
            "template_id": "tmpl-missing-movement",
            "name": "Add MovementComponent with default speed",
            "description": "Attach MovementComponent and set move_speed=150",
            "applies_to_component": "MovementComponent",
            "pseudo_code": (
                "@onready var movement_component = $MovementComponent\n"
                "func _ready():\n"
                "    if not movement_component:\n"
                "        push_error('MovementComponent missing on ' + name)\n"
                "        return\n"
                "    movement_component.move_speed = 150"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
        {
            "template_id": "tmpl-null-check-generic",
            "name": "Add null check for missing component reference",
            "description": "Add defensive null check before accessing any component",
            "applies_to_component": "*",
            "pseudo_code": (
                "func _ready():\n"
                "    if not component_node:\n"
                "        push_error(name + ': component_node is null — check scene setup')\n"
                "        return\n"
                "    component_node.initialize()"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ],
    "physics_configuration": [
        {
            "template_id": "tmpl-physics-constant",
            "name": "Replace integer literal with PhysicsLayers constant",
            "description": "Use PhysicsLayers registry constants instead of hardcoded integers",
            "applies_to_component": "PhysicsComponent",
            "pseudo_code": (
                "# Before: collision_layer = 4\n"
                "# After:\n"
                "collision_layer = PhysicsLayers.HEROES  # or appropriate constant\n"
                "collision_mask = PhysicsLayers.WORLD | PhysicsLayers.PLAYER"
            ),
            "risk_factor": "physics_layer_change",
            "risk_level": "medium",
            "complexity": 2,
        },
        {
            "template_id": "tmpl-physics-autoload-check",
            "name": "Add PhysicsLayers autoload existence check",
            "description": "Verify PhysicsLayers singleton is loaded before using constants",
            "applies_to_component": "PhysicsComponent",
            "pseudo_code": (
                "func _ready():\n"
                "    if not PhysicsLayers:\n"
                "        push_error('PhysicsLayers autoload not found')\n"
                "        return\n"
                "    collision_layer = PhysicsLayers.LAYER_NAME"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ],
    "signal_mismatch": [
        {
            "template_id": "tmpl-signal-connect-check",
            "name": "Add is_connected() guard before signal connect",
            "description": "Check connection state before connecting to prevent double-connect errors",
            "applies_to_component": "SignalComponent",
            "pseudo_code": (
                "func _ready():\n"
                "    if not signal_source.signal_name.is_connected(_on_signal_handler):\n"
                "        signal_source.signal_name.connect(_on_signal_handler)"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
        {
            "template_id": "tmpl-signal-emit-guard",
            "name": "Add null check before signal emission",
            "description": "Guard signal emit call against null emitter node",
            "applies_to_component": "SignalComponent",
            "pseudo_code": (
                "func emit_safe(signal_name: String, args: Array = []):\n"
                "    if not is_inside_tree():\n"
                "        return\n"
                "    emit_signal(signal_name, args)"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
        {
            "template_id": "tmpl-event-system-wire",
            "name": "Wire signal through EventSystem",
            "description": "Route signal through global EventSystem for decoupled communication",
            "applies_to_component": "EventSystem",
            "pseudo_code": (
                "# In sender:\n"
                "EventSystem.emit_event('signal_name', {data: value})\n"
                "# In receiver _ready():\n"
                "EventSystem.on_event('signal_name', _handle_signal)"
            ),
            "risk_factor": "signal_change",
            "risk_level": "medium",
            "complexity": 2,
        },
    ],
    "animation_state": [
        {
            "template_id": "tmpl-anim-reset",
            "name": "Add AnimationPlayer RESET call in _ready()",
            "description": "Force animation state reset to prevent stale state on scene load",
            "applies_to_component": "AnimationComponent",
            "pseudo_code": (
                "@onready var anim_player = $AnimationPlayer\n"
                "func _ready():\n"
                "    if anim_player.has_animation('RESET'):\n"
                "        anim_player.play('RESET')"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
        {
            "template_id": "tmpl-anim-has-check",
            "name": "Add has_animation() check before playing",
            "description": "Guard animation playback with has_animation() to prevent missing animation errors",
            "applies_to_component": "AnimationComponent",
            "pseudo_code": (
                "func play_animation(anim_name: String):\n"
                "    if not anim_player.has_animation(anim_name):\n"
                "        push_error(name + ': animation not found: ' + anim_name)\n"
                "        return\n"
                "    anim_player.play(anim_name)"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ],
    "scene_lifecycle": [
        {
            "template_id": "tmpl-scene-null-check",
            "name": "Add node path null check",
            "description": "Validate node paths exist before get_node() to prevent scene lifecycle errors",
            "applies_to_component": "SceneComponent",
            "pseudo_code": (
                "func get_node_safe(path: NodePath) -> Node:\n"
                "    if has_node(path):\n"
                "        return get_node(path)\n"
                "    push_error(name + ': Node not found at path: ' + str(path))\n"
                "    return null"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
        {
            "template_id": "tmpl-autoload-guard",
            "name": "Add autoload singleton existence check",
            "description": "Guard autoload access to handle missing singleton gracefully",
            "applies_to_component": "AutoloadComponent",
            "pseudo_code": (
                "func _ready():\n"
                "    if not Engine.has_singleton('AutoloadName'):\n"
                "        push_error('AutoloadName singleton not registered in project.godot')\n"
                "        return"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ],
    "unknown": [
        {
            "template_id": "tmpl-generic-null-guard",
            "name": "Add generic null check guard",
            "description": "Generic defensive null check applicable to any component",
            "applies_to_component": "*",
            "pseudo_code": (
                "func _ready():\n"
                "    assert(target_node != null, name + ': target_node must not be null')"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ],
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_candidate_id(failure_id: str, method: str, index: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_hash = hashlib.md5(f"{failure_id}-{method}-{index}-{ts}".encode()).hexdigest()[:6]
    return f"cand-{method[:4]}-{short_hash}"


def append_jsonl(path: Path, entry: dict) -> None:
    """Immutable append to JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_jsonl(path: Path) -> list:
    entries = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def load_failure_signature(failure_id: str) -> dict | None:
    """Load a failure signature by ID."""
    for entry in load_jsonl(FAILURE_SIGS_PATH):
        if entry.get("failure_id") == failure_id:
            return entry
    return None


def load_kb_patterns() -> list:
    return load_jsonl(PATTERNS_PATH)


def check_sdk_available() -> bool:
    """Check if Anthropic SDK is available."""
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def log_sdk_fallback(reason: str, failure_id: str) -> None:
    """Log SDK unavailable fallback event."""
    append_jsonl(SDK_FALLBACK_PATH, {
        "timestamp": now_iso(),
        "failure_id": failure_id,
        "reason": reason,
        "fallback_method": "csp_only_tier2_tier3",
        "note": "LLM Tier 3 unavailable; CSP fallback used",
    })


# ─── Candidate Scoring Model ───────────────────────────────────────────────────

def compute_candidate_score(
    generation_method: str,
    pattern_confidence: float,
    risk_factor: str,
    is_proven: bool = False,
) -> float:
    """
    Candidate scoring model:
      base_score (from generation method)
      * pattern_confidence_multiplier (from KB pattern)
      + risk_adjustment
      clamped to [0.60, 0.95]

    Args:
        generation_method: 'template', 'csp', 'llm', 'csp_fallback'
        pattern_confidence: KB pattern confidence (0.50-0.95)
        risk_factor: key from RISK_ADJUSTMENTS
        is_proven: whether this fix has a proven pattern match

    Returns:
        float in [0.60, 0.95]
    """
    # Validate pattern_confidence is in [0.0, 1.0] (CR-04 mitigation)
    if not (0.0 <= pattern_confidence <= 1.0):
        raise ValueError(
            f"pattern_confidence must be in [0.0, 1.0], got {pattern_confidence}"
        )

    base = BASE_SCORES.get(generation_method, 0.65)

    # Pattern confidence multiplier (0.50-0.95 range scales base by 0.9-1.0)
    conf_multiplier = 0.90 + (pattern_confidence * 0.10)  # 0.90 at conf=0, 1.0 at conf=1.0
    adjusted = base * conf_multiplier

    # Risk adjustment
    risk_adj = RISK_ADJUSTMENTS.get(risk_factor, 0.0)
    if is_proven:
        risk_adj += RISK_ADJUSTMENTS["proven_pattern"]

    final = adjusted + risk_adj
    return round(max(SCORE_MIN, min(SCORE_MAX, final)), 4)


# ─── Tier 1: Template-Based Generation ─────────────────────────────────────────

def generate_template_candidates(
    failure_id: str,
    entity: str,
    component: str,
    error_pattern: str,
    pattern_confidence: float,
    root_cause_pattern_id: str,
) -> list:
    """
    Tier 1: Generate template-based candidates (highest confidence: 0.80-0.95).

    Applies standard templates for common failure patterns.
    """
    candidates = []

    # Get templates for error pattern
    templates = FIX_TEMPLATES.get(error_pattern, FIX_TEMPLATES.get("unknown", []))

    # Also try component-specific templates if error pattern templates are sparse
    if len(templates) < 2:
        for pattern_key, tmpl_list in FIX_TEMPLATES.items():
            for tmpl in tmpl_list:
                applies_to = tmpl.get("applies_to_component", "*")
                if applies_to == component or applies_to == "*":
                    if tmpl not in templates:
                        templates.append(tmpl)
            if len(templates) >= 4:
                break

    for i, tmpl in enumerate(templates[:4]):  # max 4 template candidates
        score = compute_candidate_score(
            generation_method="template",
            pattern_confidence=pattern_confidence,
            risk_factor=tmpl.get("risk_factor", "standard_fix"),
            is_proven=root_cause_pattern_id is not None,
        )

        # Template scores should be in 0.80-0.95 range for common patterns
        # Boost if it's the primary template for this error pattern
        if i == 0:
            score = max(score, 0.82)  # Primary template floor

        candidate = {
            "candidate_id": make_candidate_id(failure_id, "template", i),
            "failure_id": failure_id,
            "generation_method": "template",
            "generation_tier": 1,
            "template_id": tmpl.get("template_id"),
            "name": tmpl.get("name"),
            "description": tmpl.get("description"),
            "pseudo_code": tmpl.get("pseudo_code"),
            "applies_to_entity": entity,
            "applies_to_component": component,
            "base_score": BASE_SCORES["template"],
            "pattern_confidence": pattern_confidence,
            "risk_factor": tmpl.get("risk_factor", "standard_fix"),
            "risk_level": tmpl.get("risk_level", "low"),
            "complexity": tmpl.get("complexity", 2),
            "final_score": score,
            "sdk_fallback": False,
        }
        candidates.append(candidate)

    return candidates


# ─── Tier 2: CSP-Based Generation ──────────────────────────────────────────────

# CSP fix templates for common patterns
CSP_FIX_MAP = {
    "missing_component": [
        {
            "name": "CSP: Initialize component with constraint validation",
            "description": (
                "Use CSP constraint checker to validate all required components "
                "are present before scene is considered ready"
            ),
            "pseudo_code": (
                "# CSP constraint: entity must have required components\n"
                "func validate_required_components(required: Array[String]) -> bool:\n"
                "    for comp_name in required:\n"
                "        if not has_node(comp_name):\n"
                "            push_error(name + ': Missing required component: ' + comp_name)\n"
                "            return false\n"
                "    return true"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
        {
            "name": "CSP: Component factory pattern",
            "description": "CSP-guided factory creates missing component with valid constraints",
            "pseudo_code": (
                "# Component factory with CSP constraints\n"
                "func get_or_create_component(comp_class: GDScript) -> Node:\n"
                "    var existing = find_child(comp_class.get_script_path().get_file())\n"
                "    if existing:\n"
                "        return existing\n"
                "    var comp = comp_class.new()\n"
                "    add_child(comp)\n"
                "    return comp"
            ),
            "risk_factor": "architecture_change",
            "risk_level": "medium",
            "complexity": 3,
        },
    ],
    "physics_configuration": [
        {
            "name": "CSP: Physics layer constraint enforcement",
            "description": "CSP validates physics layer assignments against PhysicsLayers registry",
            "pseudo_code": (
                "# CSP constraint: physics layers must use registry constants\n"
                "func validate_physics_layers() -> bool:\n"
                "    var valid_layers = PhysicsLayers.get_all_layers()\n"
                "    if not collision_layer in valid_layers:\n"
                "        push_error('Invalid collision_layer: ' + str(collision_layer))\n"
                "        collision_layer = PhysicsLayers.get_default_for(entity_type)\n"
                "        return false\n"
                "    return true"
            ),
            "risk_factor": "physics_layer_change",
            "risk_level": "medium",
            "complexity": 3,
        },
        {
            "name": "CSP: Collision mask reconciliation",
            "description": "CSP reconciles collision masks to match entity type requirements",
            "pseudo_code": (
                "# Apply CSP-computed collision mask for entity type\n"
                "func apply_csp_collision_config(entity_type: String):\n"
                "    var config = PhysicsLayers.get_csp_config(entity_type)\n"
                "    collision_layer = config.layer\n"
                "    collision_mask = config.mask"
            ),
            "risk_factor": "physics_layer_change",
            "risk_level": "medium",
            "complexity": 2,
        },
    ],
    "signal_mismatch": [
        {
            "name": "CSP: Signal connection graph reconciliation",
            "description": "CSP reconciles signal connection graph to restore expected connections",
            "pseudo_code": (
                "# CSP: ensure all expected signal connections are present\n"
                "func reconcile_signal_connections(expected_connections: Array) -> void:\n"
                "    for conn in expected_connections:\n"
                "        var src = get_node(conn.source)\n"
                "        if src and not src[conn.signal].is_connected(conn.target_method):\n"
                "            src[conn.signal].connect(conn.target_method)"
            ),
            "risk_factor": "signal_change",
            "risk_level": "medium",
            "complexity": 3,
        },
    ],
    "animation_state": [
        {
            "name": "CSP: Animation state machine validation",
            "description": "CSP validates all required animation states exist in AnimationPlayer",
            "pseudo_code": (
                "# CSP constraint: all required animations must exist\n"
                "const REQUIRED_ANIMATIONS = ['idle', 'walk', 'attack', 'RESET']\n"
                "func validate_animations() -> bool:\n"
                "    for anim in REQUIRED_ANIMATIONS:\n"
                "        if not anim_player.has_animation(anim):\n"
                "            push_error('Missing animation: ' + anim)\n"
                "            return false\n"
                "    return true"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
    ],
    "scene_lifecycle": [
        {
            "name": "CSP: Scene node existence constraint",
            "description": "CSP validates all required scene nodes exist before use",
            "pseudo_code": (
                "# CSP: validate required nodes before scene becomes active\n"
                "const REQUIRED_NODES = []\n"
                "func _ready():\n"
                "    for node_path in REQUIRED_NODES:\n"
                "        if not has_node(node_path):\n"
                "            push_error(name + ': required node missing: ' + str(node_path))"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
    ],
}


def generate_csp_candidates(
    failure_id: str,
    entity: str,
    component: str,
    error_pattern: str,
    pattern_confidence: float,
) -> list:
    """
    Tier 2: Generate CSP-based candidates (medium confidence: 0.70-0.85).

    Uses constraint satisfaction principles for complex scenarios.
    Calls csp_solver.py patterns when available.
    """
    candidates = []

    # Get CSP fixes for error pattern
    csp_fixes = CSP_FIX_MAP.get(error_pattern, [])
    if not csp_fixes:
        # Generic CSP fallback for unknown patterns
        csp_fixes = [
            {
                "name": "CSP: Generic constraint satisfaction fix",
                "description": "Apply CSP constraints to identify and resolve failure constraints",
                "pseudo_code": (
                    "# Generic CSP: validate all constraints before proceeding\n"
                    "func validate_csp_constraints() -> bool:\n"
                    "    # Entity-specific constraints injected by CSP solver\n"
                    "    return true"
                ),
                "risk_factor": "standard_fix",
                "risk_level": "low",
                "complexity": 2,
            }
        ]

    # Try to enrich with csp_solver.py patterns
    csp_solver_candidates = _call_csp_solver(entity, component)

    for i, fix in enumerate(csp_fixes[:3]):  # max 3 CSP candidates
        score = compute_candidate_score(
            generation_method="csp",
            pattern_confidence=pattern_confidence,
            risk_factor=fix.get("risk_factor", "standard_fix"),
        )

        # CSP scores should be in 0.70-0.85 range
        score = max(min(score, 0.85), 0.70)

        candidate = {
            "candidate_id": make_candidate_id(failure_id, "csp", i),
            "failure_id": failure_id,
            "generation_method": "csp",
            "generation_tier": 2,
            "name": fix.get("name"),
            "description": fix.get("description"),
            "pseudo_code": fix.get("pseudo_code"),
            "applies_to_entity": entity,
            "applies_to_component": component,
            "base_score": BASE_SCORES["csp"],
            "pattern_confidence": pattern_confidence,
            "risk_factor": fix.get("risk_factor", "standard_fix"),
            "risk_level": fix.get("risk_level", "medium"),
            "complexity": fix.get("complexity", 2),
            "final_score": score,
            "csp_solver_patterns": csp_solver_candidates[:2] if csp_solver_candidates else [],
            "sdk_fallback": False,
        }
        candidates.append(candidate)

    return candidates


def _call_csp_solver(entity: str, component: str) -> list:
    """
    Try to get additional constraint candidates from csp_solver.py.
    Returns list of modification types that pass guardrail checks.
    """
    try:
        from akc_service.csp_solver import MODIFIABLE_ASPECTS
        # Filter to relevant modification types for this component
        relevant = [m for m in MODIFIABLE_ASPECTS
                    if component.lower() in m.lower() or "null" in m or "check" in m]
        return relevant[:3]
    except (ImportError, Exception):
        return []


# ─── Tier 3: LLM-Based Generation ──────────────────────────────────────────────

def generate_llm_candidates(
    failure_id: str,
    entity: str,
    component: str,
    error_pattern: str,
    error_message: str,
    pattern_confidence: float,
) -> list:
    """
    Tier 3: Generate LLM-based candidates (lower confidence: 0.60-0.75).

    Uses LLM reasoning for complex failures.
    Falls back to CSP-only if SDK unavailable.
    """
    sdk_available = check_sdk_available()

    if not sdk_available:
        # SDK fallback: log and return CSP-based alternatives at reduced confidence
        log_sdk_fallback(
            reason="anthropic SDK not installed — using CSP fallback for Tier 3",
            failure_id=failure_id,
        )
        return _generate_csp_fallback_for_llm(failure_id, entity, component, error_pattern, pattern_confidence)

    # SDK available: generate LLM reasoning candidates
    try:
        import anthropic

        client = anthropic.Anthropic()
        context = (
            f"Entity: {entity}, Component: {component}\n"
            f"Error pattern: {error_pattern}\n"
            f"Error message: {error_message}\n"
            f"Pattern confidence: {pattern_confidence}\n"
        )
        prompt = (
            f"Given this Godot 4 game failure context and the pattern knowledge base, "
            f"generate 2 alternative fix candidates:\n\n{context}\n"
            f"Return JSON array of candidates with: name, description, pseudo_code, risk_level, complexity"
        )

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text

        # Try to extract JSON array more robustly (WR-06 mitigation)
        try:
            json_start = response_text.find('[')
            if json_start == -1:
                print(f"WARNING: No JSON array found in LLM response for {failure_id}", file=sys.stderr)
                return _generate_csp_fallback_for_llm(failure_id, entity, component, error_pattern, pattern_confidence)

            # Find matching closing bracket
            bracket_depth = 0
            json_end = json_start
            for i in range(json_start, len(response_text)):
                if response_text[i] == '[':
                    bracket_depth += 1
                elif response_text[i] == ']':
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        json_end = i + 1
                        break

            if bracket_depth != 0:
                print(f"WARNING: Unmatched brackets in JSON response for {failure_id}", file=sys.stderr)
                return _generate_csp_fallback_for_llm(failure_id, entity, component, error_pattern, pattern_confidence)

            llm_fixes = json.loads(response_text[json_start:json_end])

            # Validate candidate schema
            if not isinstance(llm_fixes, list) or not all(isinstance(c, dict) for c in llm_fixes):
                raise json.JSONDecodeError("Invalid candidate format", "", 0)

        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING: LLM response parse error: {str(e)[:100]} for {failure_id}", file=sys.stderr)
            return _generate_csp_fallback_for_llm(failure_id, entity, component, error_pattern, pattern_confidence)

        candidates = []
        for i, fix in enumerate(llm_fixes[:2]):
            score = compute_candidate_score(
                generation_method="llm",
                pattern_confidence=pattern_confidence,
                risk_factor=fix.get("risk_factor", "standard_fix"),
            )
            # LLM scores: 0.60-0.75
            score = max(min(score, 0.75), 0.60)

            candidate = {
                "candidate_id": make_candidate_id(failure_id, "llm", i),
                "failure_id": failure_id,
                "generation_method": "llm",
                "generation_tier": 3,
                "name": fix.get("name", f"LLM candidate {i+1}"),
                "description": fix.get("description", ""),
                "pseudo_code": fix.get("pseudo_code", ""),
                "reasoning": fix.get("reasoning", "LLM-generated alternative"),
                "applies_to_entity": entity,
                "applies_to_component": component,
                "base_score": BASE_SCORES["llm"],
                "pattern_confidence": pattern_confidence,
                "risk_factor": fix.get("risk_factor", "standard_fix"),
                "risk_level": fix.get("risk_level", "medium"),
                "complexity": fix.get("complexity", 3),
                "final_score": score,
                "sdk_fallback": False,
            }
            candidates.append(candidate)

        return candidates

    except Exception as e:
        # LLM call failed — fallback to CSP
        log_sdk_fallback(
            reason=f"LLM call failed: {str(e)[:100]} — falling back to CSP",
            failure_id=failure_id,
        )
        return _generate_csp_fallback_for_llm(failure_id, entity, component, error_pattern, pattern_confidence)


def _generate_csp_fallback_for_llm(
    failure_id: str,
    entity: str,
    component: str,
    error_pattern: str,
    pattern_confidence: float,
) -> list:
    """
    CSP fallback for Tier 3 when SDK is unavailable.
    Generates 2 additional CSP candidates at reduced confidence with sdk_fallback=True note.
    """
    candidates = []

    fallback_fixes = [
        {
            "name": f"CSP Fallback: Alternative {component} repair strategy",
            "description": (
                f"CSP-only fallback (LLM SDK unavailable): constraint-based repair "
                f"for {entity} {component} {error_pattern}"
            ),
            "pseudo_code": (
                f"# CSP fallback — no LLM reasoning available\n"
                f"# Manual repair template for {component}\n"
                f"func repair_{component.lower().replace('component', '')}() -> void:\n"
                f"    # Apply standard constraint fix for {error_pattern}\n"
                f"    pass"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 2,
        },
        {
            "name": f"CSP Fallback: Minimal {component} stub",
            "description": (
                f"Minimal stub implementation for {component} to unblock downstream (SDK unavailable)"
            ),
            "pseudo_code": (
                f"# Stub {component} to prevent null reference failures\n"
                f"# Replace with full implementation when SDK available\n"
                f"func stub_{component.lower().replace('component', '')}() -> void:\n"
                f"    push_warning('{component} stub active — replace with real impl')"
            ),
            "risk_factor": "standard_fix",
            "risk_level": "low",
            "complexity": 1,
        },
    ]

    for i, fix in enumerate(fallback_fixes):
        score = compute_candidate_score(
            generation_method="csp_fallback",
            pattern_confidence=pattern_confidence,
            risk_factor=fix.get("risk_factor", "standard_fix"),
        )
        # CSP fallback scores: 0.60-0.72 (reduced vs full LLM)
        score = max(min(score, 0.72), 0.60)

        candidate = {
            "candidate_id": make_candidate_id(failure_id, "csp_fallback", i),
            "failure_id": failure_id,
            "generation_method": "csp_fallback",
            "generation_tier": 3,
            "name": fix["name"],
            "description": fix["description"],
            "pseudo_code": fix["pseudo_code"],
            "applies_to_entity": entity,
            "applies_to_component": component,
            "base_score": BASE_SCORES["csp_fallback"],
            "pattern_confidence": pattern_confidence,
            "risk_factor": fix.get("risk_factor", "standard_fix"),
            "risk_level": fix.get("risk_level", "low"),
            "complexity": fix.get("complexity", 2),
            "final_score": score,
            "sdk_fallback": True,
            "sdk_fallback_reason": "anthropic SDK unavailable — CSP-only generation",
        }
        candidates.append(candidate)

    return candidates


# ─── Candidate Ranking ─────────────────────────────────────────────────────────

def rank_candidates(candidates: list) -> list:
    """
    Rank candidates by:
    1. Primary: confidence score (highest first)
    2. Secondary: risk level (lowest first)
    3. Tertiary: complexity (simplest first)
    4. Quaternary: candidate_id (for determinism, WR-05)

    Returns top 5 candidates (or fewer if fewer generated).
    """
    def sort_key(c):
        risk_order = RISK_LEVELS.get(c.get("risk_level", "medium"), 1)
        complexity = c.get("complexity", 2)
        score = c.get("final_score", 0.0)
        candidate_id = c.get("candidate_id", "")
        # Primary: score descending (-score), secondary: risk asc, tertiary: complexity asc, quaternary: id asc
        return (-score, risk_order, complexity, candidate_id)

    ranked = sorted(candidates, key=sort_key)

    # Add ranking position
    for i, c in enumerate(ranked):
        c["ranking_position"] = i + 1

    return ranked[:5]  # Top 5 max


# ─── Generate Candidates for a Failure (CLI: --generate-candidates) ────────────

def generate_candidates(failure_id: str) -> dict:
    """
    Generate 3-5 ranked fix candidates for a detected failure.

    Loads failure from failure_signatures.jsonl, runs all three tiers,
    ranks and trims to top 5, logs to generated_candidates.jsonl.

    Returns:
        dict with candidates_generated, top_candidates list
    """
    failure = load_failure_signature(failure_id)
    if not failure:
        return {
            "error": f"Failure {failure_id} not found in failure_signatures.jsonl",
            "success": False,
        }

    entity = failure.get("entity", "global")
    component = failure.get("component", "unknown")
    error_pattern = failure.get("error_pattern", "unknown")
    error_message = failure.get("failure_json", {}).get("error_message", "") if isinstance(failure.get("failure_json"), dict) else ""
    pattern_confidence = failure.get("root_cause_confidence", 0.65)
    root_cause_pattern_id = failure.get("root_cause_pattern_id")

    # Generate from all three tiers
    tier1_candidates = generate_template_candidates(
        failure_id, entity, component, error_pattern, pattern_confidence, root_cause_pattern_id
    )
    tier2_candidates = generate_csp_candidates(
        failure_id, entity, component, error_pattern, pattern_confidence
    )
    tier3_candidates = generate_llm_candidates(
        failure_id, entity, component, error_pattern, error_message, pattern_confidence
    )

    all_candidates = tier1_candidates + tier2_candidates + tier3_candidates

    # Rank and trim to top 5
    ranked_candidates = rank_candidates(all_candidates)

    # Ensure minimum 3 candidates
    if len(ranked_candidates) < 3:
        # Add generic fallback to reach minimum 3
        for i in range(3 - len(ranked_candidates)):
            generic = {
                "candidate_id": make_candidate_id(failure_id, "generic", i),
                "failure_id": failure_id,
                "generation_method": "template",
                "generation_tier": 1,
                "name": f"Generic fix {i+1}: Null check and initialization",
                "description": "Add defensive null check and re-initialization to resolve unknown failure",
                "pseudo_code": "if not node: node = create_default()\nassert(node != null)",
                "applies_to_entity": entity,
                "applies_to_component": component,
                "base_score": 0.80,
                "pattern_confidence": pattern_confidence,
                "risk_factor": "standard_fix",
                "risk_level": "low",
                "complexity": 1,
                "final_score": 0.72,
                "ranking_position": len(ranked_candidates) + i + 1,
                "sdk_fallback": False,
            }
            ranked_candidates.append(generic)

    # Log all generated candidates to generated_candidates.jsonl (immutable, T-FIX-04)
    for c in ranked_candidates:
        log_entry = {
            "candidate_id": c["candidate_id"],
            "failure_id": failure_id,
            "generation_method": c.get("generation_method"),
            "base_score": c.get("base_score"),
            "pattern_confidence": c.get("pattern_confidence"),
            "risk_factors": c.get("risk_factor"),
            "final_score": c.get("final_score"),
            "ranking_position": c.get("ranking_position"),
            "timestamp": now_iso(),
        }
        append_jsonl(GENERATED_CANDIDATES_PATH, log_entry)

    return {
        "failure_id": failure_id,
        "candidates_generated": len(ranked_candidates),
        "tier1_count": len(tier1_candidates),
        "tier2_count": len(tier2_candidates),
        "tier3_count": len(tier3_candidates),
        "top_candidates": ranked_candidates,
        "success": True,
    }


# ─── Rank Candidates (CLI: --rank-candidates) ──────────────────────────────────

def rank_candidates_cmd(candidate_list_json: str) -> dict:
    """
    Rank a provided list of candidates by confidence and risk.

    Args:
        candidate_list_json: JSON string with array of candidate objects

    Returns:
        dict with ranked_candidates list
    """
    try:
        candidates = json.loads(candidate_list_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid candidate list JSON: {e}", "success": False}

    if not isinstance(candidates, list):
        return {"error": "candidate-list must be a JSON array", "success": False}

    ranked = rank_candidates(candidates)

    return {
        "ranked_candidates": ranked,
        "total_input": len(candidates),
        "total_ranked": len(ranked),
        "success": True,
    }


# ─── Score Candidate (CLI: --score-candidate) ──────────────────────────────────

def score_candidate_cmd(candidate_id: str) -> dict:
    """
    Get confidence score for a single candidate from generated_candidates.jsonl.

    Returns:
        dict with candidate_id, final_score, score_breakdown
    """
    for entry in load_jsonl(GENERATED_CANDIDATES_PATH):
        if entry.get("candidate_id") == candidate_id:
            score = entry.get("final_score", 0.0)
            in_range = SCORE_MIN <= score <= SCORE_MAX

            return {
                "candidate_id": candidate_id,
                "final_score": score,
                "score_in_valid_range": in_range,
                "valid_range": f"[{SCORE_MIN}, {SCORE_MAX}]",
                "score_breakdown": {
                    "base_score": entry.get("base_score"),
                    "pattern_confidence": entry.get("pattern_confidence"),
                    "risk_factors": entry.get("risk_factors"),
                    "generation_method": entry.get("generation_method"),
                    "ranking_position": entry.get("ranking_position"),
                },
                "success": True,
            }

    return {"error": f"Candidate {candidate_id} not found", "success": False}


# ─── Test Generation (CLI: --test-generation) ─────────────────────────────────

def test_generation() -> dict:
    """
    Test candidate generation with a synthetic failure.

    Creates a test failure signature and generates candidates.

    Returns:
        dict with candidates_generated count and score range validation
    """
    # Create a synthetic test failure in failure_signatures.jsonl
    test_failure_id = "fail-test-gen-aabbcc"
    test_signature = {
        "failure_id": test_failure_id,
        "task_id": "task-test-gen-001",
        "timestamp": now_iso(),
        "entity": "player",
        "component": "HealthComponent",
        "error_pattern": "missing_component",
        "root_cause_pattern_id": "player:HealthComponent:missing_component",
        "root_cause_confidence": 0.85,
        "routing": "autonomous",
        "multi_factor_breakdown": {
            "pattern_matching_score": 0.90,
            "semantic_similarity_score": 0.80,
            "dependency_trace_score": 0.75,
            "final_confidence": 0.85,
        },
        "status": "failed",
        "schema_version": "v1",
        "source": "test_generation",
    }

    # Ensure test failure exists
    existing = load_failure_signature(test_failure_id)
    if not existing:
        append_jsonl(FAILURE_SIGS_PATH, test_signature)

    # Generate candidates
    result = generate_candidates(test_failure_id)

    if not result.get("success"):
        return result

    top_candidates = result.get("top_candidates", [])

    # Validate score range
    scores = [c.get("final_score", 0.0) for c in top_candidates]
    all_in_range = all(SCORE_MIN <= s <= SCORE_MAX for s in scores)

    # Check tier distribution
    methods = [c.get("generation_method") for c in top_candidates]
    has_template = "template" in methods
    has_csp = "csp" in methods or "csp_fallback" in methods

    return {
        "candidates_generated": len(top_candidates),
        "minimum_met": len(top_candidates) >= 3,
        "maximum_respected": len(top_candidates) <= 5,
        "all_scores_in_range": all_in_range,
        "score_range": f"[{SCORE_MIN}, {SCORE_MAX}]",
        "score_distribution": {
            "min": min(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "values": scores,
        },
        "tier_coverage": {
            "has_template": has_template,
            "has_csp": has_csp,
        },
        "sdk_available": check_sdk_available(),
        "test_failure_id": test_failure_id,
        "success": True,
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Fix Candidate Generator — 3-tier ranked fix generation"
    )
    parser.add_argument("--generate-candidates", action="store_true",
                        help="Generate candidates for a failure ID")
    parser.add_argument("--rank-candidates", action="store_true",
                        help="Rank a provided list of candidates")
    parser.add_argument("--score-candidate", action="store_true",
                        help="Get score for a single candidate ID")
    parser.add_argument("--test-generation", action="store_true",
                        help="Test generation with synthetic failure")
    parser.add_argument("--failure-id", help="Failure ID to generate candidates for")
    parser.add_argument("--candidate-list", help="JSON array of candidates to rank")
    parser.add_argument("--candidate-id", help="Candidate ID to score")

    args = parser.parse_args()

    if args.generate_candidates:
        if not args.failure_id:
            print("ERROR: --generate-candidates requires --failure-id", file=sys.stderr)
            sys.exit(1)
        result = generate_candidates(args.failure_id)
        print(json.dumps(result, indent=2))
        return

    if args.rank_candidates:
        if not args.candidate_list:
            print("ERROR: --rank-candidates requires --candidate-list", file=sys.stderr)
            sys.exit(1)
        result = rank_candidates_cmd(args.candidate_list)
        print(json.dumps(result, indent=2))
        return

    if args.score_candidate:
        if not args.candidate_id:
            print("ERROR: --score-candidate requires --candidate-id", file=sys.stderr)
            sys.exit(1)
        result = score_candidate_cmd(args.candidate_id)
        print(json.dumps(result, indent=2))
        return

    if args.test_generation:
        result = test_generation()
        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
