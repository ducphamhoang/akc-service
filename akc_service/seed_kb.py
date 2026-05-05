#!/usr/bin/env python3
"""
AKC Knowledge Base Seeder
Seeds the KB with >=10 baseline patterns covering all major entity:component combinations.
These are foundation patterns derived from Phase 0 Entity:Component Taxonomy.

Usage:
    python seed_kb.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

PATTERNS_PATH = KB_DIR / "patterns.jsonl"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

BASELINE_PATTERNS = [
    {
        "id": "global_physics_collision_detection_001",
        "schema_version": "v2",
        "entity": "global",
        "component": "PhysicsComponent",
        "pattern_type": "collision_detection",
        "description": "All collision layers and masks must use PhysicsLayers.gd constants, never integer literals. This ensures consistent layer assignment across all entities and prevents fragile hardcoded values.",
        "rule": "ALWAYS use PhysicsLayers constants (e.g., PhysicsLayers.LAYER_ENEMIES) for collision_layer and collision_mask assignments. NEVER use integer literals.",
        "example_correct": "collision_layer = PhysicsLayers.LAYER_ENEMIES\ncollision_mask = PhysicsLayers.LAYER_PLAYER | PhysicsLayers.LAYER_WORLD",
        "example_incorrect": "collision_layer = 4  # hardcoded integer literal\ncollision_mask = 2  # fragile, breaks on layer reorder",
        "confidence": 0.92,
        "confidence_tier": "gold",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.92, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern — Phase 0 taxonomy", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["safety_critical", "physics"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": True,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "player_health_health_tracking_001",
        "schema_version": "v2",
        "entity": "player",
        "component": "HealthComponent",
        "pattern_type": "health_tracking",
        "description": "Player HealthComponent must clamp health between 0.0 and max_health on every damage and heal operation. Emitting health_changed signal after every update ensures UI stays in sync.",
        "rule": "ALWAYS clamp health to [0.0, max_health] after take_damage() and heal(). ALWAYS emit health_changed signal after any health modification.",
        "example_correct": "func take_damage(amount: float) -> void:\n    health = clamp(health - amount, 0.0, max_health)\n    health_changed.emit(health)\n    if health <= 0.0: died.emit()",
        "example_incorrect": "func take_damage(amount: float) -> void:\n    health -= amount  # no clamp, can go negative",
        "confidence": 0.85,
        "confidence_tier": "gold",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.85, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["safety_critical", "combat"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": True,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "enemy_knight_physics_collision_detection_001",
        "schema_version": "v2",
        "entity": "enemy_knight",
        "component": "PhysicsComponent",
        "pattern_type": "collision_detection",
        "description": "Knight entity uses LAYER_ENEMIES for its layer and detects LAYER_PLAYER and LAYER_WORLD via mask. Hurtbox must be on LAYER_HURT_BOXES scanning LAYER_HIT_BOXES.",
        "rule": "Knight collision_layer = PhysicsLayers.LAYER_ENEMIES. Hurtbox collision_layer = PhysicsLayers.LAYER_HURT_BOXES, collision_mask = PhysicsLayers.LAYER_HIT_BOXES.",
        "example_correct": "collision_layer = PhysicsLayers.LAYER_ENEMIES\ncollision_mask = PhysicsLayers.LAYER_PLAYER | PhysicsLayers.LAYER_WORLD",
        "example_incorrect": "collision_layer = 2  # missing PhysicsLayers reference",
        "confidence": 0.88,
        "confidence_tier": "gold",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.88, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": ["global_physics_collision_detection_001"],
        "conflicts_with": [],
        "tags": ["physics", "safety_critical"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": True,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "global_event_system_signal_emission_001",
        "schema_version": "v2",
        "entity": "global",
        "component": "EventSystem",
        "pattern_type": "signal_emission",
        "description": "All entity death signals must be routed through EventSystem autoload, not direct node connections. Connecting via EventSystem prevents memory leaks when entities are freed.",
        "rule": "ALWAYS route entity lifecycle signals (died, spawned, despawned) through EventSystem autoload. ALWAYS disconnect signals in _exit_tree() to prevent stale connections.",
        "example_correct": "func _ready() -> void:\n    EventSystem.enemy_died.connect(_on_enemy_died)\nfunc _exit_tree() -> void:\n    if EventSystem.enemy_died.is_connected(_on_enemy_died):\n        EventSystem.enemy_died.disconnect(_on_enemy_died)",
        "example_incorrect": "func _ready() -> void:\n    enemy.died.connect(_on_enemy_died)  # direct connection, leaks on free",
        "confidence": 0.82,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.82, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["signal_bus", "lifecycle"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "minion_health_health_tracking_001",
        "schema_version": "v2",
        "entity": "minion",
        "component": "HealthComponent",
        "pattern_type": "health_tracking",
        "description": "Minion HealthComponent must handle death correctly: emit died signal through EventSystem, then queue_free() after animation. Do not free before death signal is processed.",
        "rule": "ALWAYS emit EventSystem.minion_died before queue_free(). ALWAYS wait for death animation to complete before queue_free(). Clamp health to [0.0, max_health].",
        "example_correct": "func _on_died() -> void:\n    EventSystem.minion_died.emit(self)\n    anim_player.play('death')\n    await anim_player.animation_finished\n    queue_free()",
        "example_incorrect": "func _on_died() -> void:\n    queue_free()  # immediately frees, signal never sent",
        "confidence": 0.79,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.79, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": ["global_event_system_signal_emission_001"],
        "conflicts_with": [],
        "tags": ["lifecycle", "combat"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "player_movement_physics_configuration_001",
        "schema_version": "v2",
        "entity": "player",
        "component": "MovementComponent",
        "pattern_type": "physics_configuration",
        "description": "Player movement uses CharacterBody2D.move_and_slide(). Velocity must be reset toward zero when not actively moving to prevent sliding. Floor detection uses is_on_floor().",
        "rule": "ALWAYS use move_and_slide() for player movement. ALWAYS apply velocity.move_toward(Vector2.ZERO, deceleration) when no input is detected. NEVER hardcode speed values.",
        "example_correct": "func _physics_process(delta: float) -> void:\n    var input = Input.get_vector('left', 'right', 'up', 'down')\n    if input != Vector2.ZERO:\n        velocity = input * speed\n    else:\n        velocity = velocity.move_toward(Vector2.ZERO, deceleration)\n    move_and_slide()",
        "example_incorrect": "velocity = Vector2(200, 0)  # hardcoded speed",
        "confidence": 0.80,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.80, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": ["global_physics_collision_detection_001"],
        "conflicts_with": [],
        "tags": ["physics", "performance"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "enemy_knight_animation_animation_state_001",
        "schema_version": "v2",
        "entity": "enemy_knight",
        "component": "AnimationComponent",
        "pattern_type": "animation_state",
        "description": "Knight AnimationPlayer must be reset on _ready() and use named animation states (idle, walk, attack, death). All state transitions must check is_valid() before playing.",
        "rule": "ALWAYS call anim_player.stop() then anim_player.seek(0, true) in _ready() to prevent stale state. ALWAYS check anim_player.has_animation(name) before play(). Use named states only.",
        "example_correct": "func _ready() -> void:\n    anim_player.stop()\n    anim_player.seek(0.0, true)\n    anim_player.play('idle')\n\nfunc play_anim(name: String) -> void:\n    if anim_player.has_animation(name):\n        anim_player.play(name)",
        "example_incorrect": "anim_player.play('idle')  # no reset, may start from wrong frame",
        "confidence": 0.75,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.75, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["animation"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "enemy_mage_combat_signal_emission_001",
        "schema_version": "v2",
        "entity": "enemy_mage",
        "component": "CombatComponent",
        "pattern_type": "signal_emission",
        "description": "Mage spell_cast signal must be emitted before instantiating projectile. Signal carries spell_type and target_position so VFX and audio systems can react before physics.",
        "rule": "ALWAYS emit spell_cast(spell_type, target_position) BEFORE instantiating projectile. ALWAYS validate target is not null before casting.",
        "example_correct": "func cast_spell(target: Node2D) -> void:\n    if not is_instance_valid(target): return\n    spell_cast.emit(current_spell, target.global_position)\n    var proj = PROJECTILE.instantiate()\n    add_child(proj)\n    proj.launch(target.global_position)",
        "example_incorrect": "var proj = PROJECTILE.instantiate()  # instantiates before signal",
        "confidence": 0.72,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.72, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["signal_bus", "combat"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "global_autoload_scene_lifecycle_001",
        "schema_version": "v2",
        "entity": "global",
        "component": "autoload",
        "pattern_type": "scene_lifecycle",
        "description": "All autoloads (PhysicsLayers, EventSystem) must be accessed via their registered name, never via get_node() or absolute path. Only the Orchestrator agent may modify project.godot autoloads.",
        "rule": "ALWAYS access autoloads via their registered singleton name (e.g., EventSystem.method()). NEVER use get_node('/root/EventSystem'). Only Orchestrator can add/remove autoloads.",
        "example_correct": "EventSystem.enemy_died.emit(self)  # autoload by registered name",
        "example_incorrect": "get_node('/root/EventSystem').enemy_died.emit(self)  # fragile path",
        "confidence": 0.90,
        "confidence_tier": "gold",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.90, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["autoload", "architecture", "safety_critical"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": True,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "minion_physics_collision_detection_001",
        "schema_version": "v2",
        "entity": "minion",
        "component": "PhysicsComponent",
        "pattern_type": "collision_detection",
        "description": "Minions use LAYER_MINIONS for their body layer. Their hurtbox scans LAYER_HIT_BOXES. Minions must not use LAYER_PLAYER or LAYER_ENEMIES to avoid friendly fire.",
        "rule": "Minion body: collision_layer = PhysicsLayers.LAYER_MINIONS. Minion hurtbox: collision_layer = PhysicsLayers.LAYER_HURT_BOXES, collision_mask = PhysicsLayers.LAYER_HIT_BOXES.",
        "example_correct": "collision_layer = PhysicsLayers.LAYER_MINIONS\ncollision_mask = PhysicsLayers.LAYER_WORLD",
        "example_incorrect": "collision_layer = PhysicsLayers.LAYER_PLAYER  # wrong layer for minion",
        "confidence": 0.83,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.83, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": ["global_physics_collision_detection_001"],
        "conflicts_with": [],
        "tags": ["physics"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "player_signal_signal_emission_001",
        "schema_version": "v2",
        "entity": "player",
        "component": "SignalComponent",
        "pattern_type": "signal_emission",
        "description": "Player emits orb_collected, level_up, and transform_to_boss signals. Each must be emitted exactly once per event and carry correct payload. UI subscribes to these for updates.",
        "rule": "ALWAYS emit orb_collected(orb_type, count) after each orb pickup. ALWAYS emit level_up(new_level) when XP threshold crossed. Signal payloads must match declared types.",
        "example_correct": "func collect_orb(orb: OrbScene) -> void:\n    orbs_collected += 1\n    orb_collected.emit(orb.orb_type, orbs_collected)\n    orb.queue_free()",
        "example_incorrect": "orb_collected.emit()  # missing payload arguments",
        "confidence": 0.77,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.77, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": [],
        "conflicts_with": [],
        "tags": ["signal_bus", "ui"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
    {
        "id": "boss_health_health_tracking_001",
        "schema_version": "v2",
        "entity": "boss",
        "component": "HealthComponent",
        "pattern_type": "health_tracking",
        "description": "Boss HealthComponent manages multiple phases. On phase transitions (health thresholds), emit phase_changed signal and update collision layers. Regeneration must be bounded.",
        "rule": "ALWAYS check phase transition thresholds in take_damage(). ALWAYS emit phase_changed(phase) when threshold crossed. ALWAYS clamp regen to max_health.",
        "example_correct": "func take_damage(amount: float) -> void:\n    health = clamp(health - amount, 0.0, max_health)\n    _check_phase_transition()\n    health_changed.emit(health)",
        "example_incorrect": "health -= amount  # no phase check, no clamp",
        "confidence": 0.73,
        "confidence_tier": "production",
        "version": {
            "current": "v1",
            "history": [{"version_id": "v1", "confidence_snapshot": 0.73, "timestamp": "2026-05-03T00:00:00Z", "change_reason": "Initial baseline pattern", "changed_by": "akc_system"}]
        },
        "dependencies": ["player_health_health_tracking_001"],
        "conflicts_with": [],
        "tags": ["combat", "safety_critical"],
        "confidence_delta": {"on_success": 0.05, "on_failure": -0.10, "min_bound": 0.0, "max_bound": 1.0},
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "created_at": "2026-05-03T00:00:00Z",
        "updated_at": now_iso(),
        "source": "manual_curation",
    },
]


def seed_kb():
    """Write baseline patterns to patterns.jsonl."""
    existing_ids = set()

    if PATTERNS_PATH.exists():
        with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        p = json.loads(line)
                        existing_ids.add(p.get("id"))
                    except json.JSONDecodeError:
                        pass

    added = 0
    skipped = 0
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(PATTERNS_PATH, "a", encoding="utf-8") as f:
        for pattern in BASELINE_PATTERNS:
            if pattern["id"] in existing_ids:
                skipped += 1
                continue
            f.write(json.dumps(pattern) + "\n")
            added += 1

    return {
        "added": added,
        "skipped": skipped,
        "total": len(BASELINE_PATTERNS),
        "patterns_path": str(PATTERNS_PATH),
    }


if __name__ == "__main__":
    import json
    result = seed_kb()
    print(json.dumps(result, indent=2))
