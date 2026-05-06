# Multi-KB Routing Specification

**Status:** Draft  
**Date:** 2026-05-06  
**Owner:** AKC Service Team  
**Priority:** Medium (post-MVP)

---

## 1. Overview

This specification describes the design and implementation of **multi-knowledge-base (multi-KB) routing** for akc-service. Currently, akc-service supports a single monolithic knowledge base. This feature enables akc-service to simultaneously manage multiple isolated knowledge bases, routing each request to the correct KB based on explicit KB override (`kb` field) or automatic entity-based inference.

### Motivation

- **Domain Isolation:** Physics patterns should never be mixed with animation patterns
- **Concurrent Learning:** Multiple game systems (physics, animation, AI, rendering) can learn independently
- **Operational Flexibility:** Teams can add new domains without code changes
- **Backward Compatibility:** Existing single-KB deployments continue to work

---

## 2. Current State

### Architecture
```
Environment Variable
├─ AKC_SERVICE_KB_DIR
    └─ Single hardcoded path (e.g., ./kb/)

Module Config (config.py)
├─ KB_DIR = Path(env var or default)
├─ PATTERNS_JSONL = KB_DIR / "patterns.jsonl"
└─ All other paths derived from KB_DIR

Routes (routes.py, learning_integration.py, etc.)
├─ Import KB_DIR at module load time
├─ All I/O uses global KB_DIR
└─ No per-request KB selection
```

### Limitations
1. Single KB per process — cannot serve multiple domains
2. No routing logic — all inputs go to one place
3. Configuration is global and static
4. Adding KB support requires code changes, not just config

---

## 3. Problem Statement

### Use Case 1: Multi-Domain Game Engine
A Godot game has multiple specialist systems:
- **Physics Engine** — handles collision, dynamics, constraints
- **Animation System** — handles skeletal animation, blending, state machines
- **AI System** — handles pathfinding, behavior trees, decision making
- **Rendering System** — handles lighting, shadows, post-processing

Each system should:
- Learn independently (physics patterns don't bias animation learning)
- Query independently (avoid pattern pollution)
- Scale independently (reuse successful patterns across projects)

**Today:** Impossible. All patterns mixed in one KB.

### Use Case 2: Multi-Agent Learning
Multiple specialist agents (orchestrator, physics agent, animation agent) post task outcomes concurrently. Each outcome should:
- Be routed to the correct domain KB
- Update confidence without interfering with other domains
- Be queryable by other agents in the same domain

**Today:** Agents must coordinate to avoid KB contention; no domain isolation.

### Use Case 3: KB Segmentation for Compliance
A studio has:
- **Public KB** — patterns that can be shared with external partners
- **Private KB** — patterns subject to IP restrictions
- **Experimental KB** — patterns still being validated

**Today:** No way to separate KBs by access level without running separate services.

---

## 4. Requirements

### Functional Requirements

#### 4.1 KB Registry
- **FR-4.1.1:** Support a configurable registry mapping KB names to filesystem paths
- **FR-4.1.2:** Registry should be specified via environment variable as JSON
- **FR-4.1.3:** Registry must include a "default" entry as fallback
- **FR-4.1.4:** Registry paths must be validated on service startup (warn if nonexistent)

#### 4.2 Routing Logic
- **FR-4.2.1:** Implement 3-tier fallback routing:
  1. Explicit KB from `kb` field in request (if present)
  2. Entity-based inference from pattern entity (if present)
  3. Default KB
- **FR-4.2.2:** Support entity→KB mapping via environment variable
- **FR-4.2.3:** Entity mapping must support wildcard patterns (e.g., `entity:*` → `default`)
- **FR-4.2.4:** Route resolution must be deterministic (same input → same KB)

#### 4.3 API Changes
- **FR-4.3.1:** Add optional `kb` field to `/record`, `/query`, `/fix` endpoints (explicit KB override)
- **FR-4.3.2:** Add optional `kb` query parameter to `/stats` endpoint (single KB or aggregate)
- **FR-4.3.3:** Maintain backward compatibility (requests without `kb` field must work)
- **FR-4.3.4:** Document `kb` field as optional in Pydantic models
- **FR-4.3.5:** Return `kb_used: "{name}"` in all response bodies (e.g., `kb_used: "physics"`)

#### 4.4 Data Isolation
- **FR-4.4.1:** Each KB has its own `patterns.jsonl` file (separate files, not mixed records)
- **FR-4.4.2:** Each KB has its own `confidence_history.jsonl` (audit trail per KB)
- **FR-4.4.3:** Each KB has its own `fix_history.jsonl`
- **FR-4.4.4:** Each KB has its own `latency_samples.jsonl`
- **FR-4.4.5:** Each KB has its own `patterns.checkpoint` (for rollback)
- **FR-4.4.6:** Queries on KB-A never return patterns from KB-B

#### 4.5 Concurrent Safety
- **FR-4.5.1:** Multiple agents can write to different KBs concurrently without blocking
- **FR-4.5.2:** If multiple agents write to the same KB, fcntl locks ensure write-ordering
- **FR-4.5.3:** No additional database locks needed (file-level locking sufficient)

#### 4.6 Module Refactoring
- **FR-4.6.1:** `config.py` must expose `resolve_kb_dir(kb_override: Optional[str], entity: Optional[str]) → KBContext`
- **FR-4.6.2:** `learning_integration.py` functions must accept `kb_dir` parameter
- **FR-4.6.3:** `safety_engine.py` functions must accept `kb_dir` parameter
- **FR-4.6.4:** `monitoring_engine.py` functions must accept `kb_dir` parameter
- **FR-4.6.5:** `failure_detection.py` functions must accept `kb_dir` parameter
- **FR-4.6.6:** All I/O calls (read/write patterns, history files) must use passed `kb_dir`

### Non-Functional Requirements

#### 4.7 Performance
- **NFR-4.7.1:** KB resolution latency must be < 1ms (dict lookup only)
- **NFR-4.7.2:** Per-request I/O must not change (still append-only JSONL)
- **NFR-4.7.3:** No additional memory overhead (routing is stateless)

#### 4.8 Backward Compatibility
- **NFR-4.8.1:** Existing single-KB deployments must work without config changes
- **NFR-4.8.2:** Requests without explicit `kb` field must be routed correctly (fall through to entity inference or default)
- **NFR-4.8.3:** Default config must match current behavior

#### 4.9 Observability
- **NFR-4.9.1:** Logs must include KB name/tenant on every request (e.g., `KB=physics`)
- **NFR-4.9.2:** Routing decisions must be logged at INFO level
- **NFR-4.9.3:** Metrics must be tagged by KB (Prometheus: `kb_name` label)

#### 4.10 Documentation
- **NFR-4.10.1:** Update API_REFERENCE.md with tenant field examples
- **NFR-4.10.2:** Add KB_ROUTING.md guide (configuration, examples, troubleshooting)
- **NFR-4.10.3:** Update CONFIGURATION.md with env var definitions

---

## 5. Design

### 5.1 Data Model

#### KB Registry (Environment Variable)
```json
{
  "default": "/path/to/kb/default",
  "physics": "/path/to/kb/physics",
  "animation": "/path/to/kb/animation",
  "ai_agents": "/path/to/kb/ai_agents"
}
```
- Stored in: `AKC_SERVICE_KB_REGISTRY`
- Format: JSON
- Default: `{"default": "<package>/kb"}`

#### Entity→KB Mapping (Environment Variable)
```json
{
  "entity:physics": "physics",
  "entity:animation": "animation",
  "entity:ai_agent": "ai_agents",
  "entity:*": "default"
}
```
- Stored in: `AKC_SERVICE_ENTITY_KB_MAPPING`
- Format: JSON
- Default: `{"entity:*": "default"}`
- Wildcard `entity:*` is fallback for unmapped entities

#### KB Directory Structure
Each KB directory contains:
```
kb/
├─ physics/
│  ├─ patterns.jsonl
│  ├─ confidence_history.jsonl
│  ├─ fix_history.jsonl
│  ├─ latency_samples.jsonl
│  └─ patterns.checkpoint
├─ animation/
│  ├─ patterns.jsonl
│  ├─ confidence_history.jsonl
│  └─ ...
└─ default/
   └─ ...
```

### 5.2 Routing Resolution Algorithm

```python
from dataclasses import dataclass

@dataclass
class KBContext:
    """Encapsulates resolved KB information.
    
    This design allows per-KB configuration (safety levels, policies) to be
    added in future phases without refactoring all call sites. Today,
    safety_level is global; in Phase 2+, it can be populated per-KB from config.
    """
    path: Path
    name: str  # "physics", "default", etc.
    safety_level: int  # Currently global; per-KB override in future


def resolve_kb_dir(
    kb_override: Optional[str] = None,
    entity: Optional[str] = None,
    global_safety_level: int = 1  # Default safety level for now
) -> KBContext:
    """
    Resolve knowledge base directory using 3-tier fallback.
    
    Args:
        kb_override: Explicit KB name from request (e.g., "physics")
                    Priority: Tier 1 (highest)
        entity: Pattern entity for automatic routing (e.g., "physics")
               Priority: Tier 2
        global_safety_level: Global safety level (applies to all KBs in MVP)
    
    Returns:
        KBContext with path, name, and safety level
    
    Fallback order:
        1. If kb_override is provided and exists in KB_REGISTRY → use it
        2. Else if entity is provided:
           - Lookup f"entity:{entity}" in ENTITY_KB_MAPPING
           - Resolve the mapped KB name via KB_REGISTRY
        3. Else use "default" from KB_REGISTRY
    """
    kb_name = None
    routing_tier = None
    
    # Tier 1: Explicit KB override
    if kb_override and kb_override in KB_REGISTRY:
        kb_name = kb_override
        routing_tier = "explicit"
    
    # Tier 2: Entity-based inference
    elif entity:
        pattern_key = f"entity:{entity}"
        if pattern_key in ENTITY_KB_MAPPING:
            kb_name = ENTITY_KB_MAPPING[pattern_key]
            routing_tier = "entity_mapping"
        else:
            # Wildcard fallback
            kb_name = ENTITY_KB_MAPPING.get("entity:*", "default")
            routing_tier = "entity_wildcard"
    
    # Tier 3: Default fallback
    if not kb_name:
        kb_name = "default"
        routing_tier = "fallback"
    
    path = Path(KB_REGISTRY[kb_name])
    logger.info(f"KB routing: {routing_tier}={kb_override or entity} → kb_name={kb_name} → {path}")
    
    return KBContext(
        path=path,
        name=kb_name,
        safety_level=global_safety_level  # Per-KB override point for future
    )
```

**Startup Validation (in `config.py`):**

```python
def validate_kb_config():
    """Validate KB_REGISTRY and ENTITY_KB_MAPPING at startup."""
    
    # Check 1: Parse JSON, fail hard on syntax errors
    try:
        registry = json.loads(os.getenv("AKC_SERVICE_KB_REGISTRY", "{}"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in AKC_SERVICE_KB_REGISTRY: {e}\n"
            f"Example: {{'default': './kb/default', 'physics': './kb/physics'}}"
        )
    
    try:
        mapping = json.loads(os.getenv("AKC_SERVICE_ENTITY_KB_MAPPING", "{}"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in AKC_SERVICE_ENTITY_KB_MAPPING: {e}\n"
            f"Example: {{'entity:physics': 'physics', 'entity:*': 'default'}}"
        )
    
    # Check 2: Validate all KB names in mapping exist in registry
    for mapping_key, kb_name in mapping.items():
        if kb_name not in registry:
            raise ValueError(
                f"ENTITY_KB_MAPPING references unknown KB: '{kb_name}' "
                f"(mapping key: {mapping_key}). "
                f"Available KBs: {list(registry.keys())}"
            )
    
    # Check 3: Warn if KB directories don't exist (but don't fail startup)
    for kb_name, kb_path in registry.items():
        path = Path(kb_path)
        if not path.exists():
            logger.warning(
                f"KB directory does not exist: {kb_name}={kb_path}. "
                f"Will be created on first write."
            )
    
    # Log final config for operator verification
    logger.info(f"KB Registry loaded: {list(registry.keys())}")
    logger.info(f"Entity mappings loaded: {len(mapping)} entries")
    
    return registry, mapping
```

### 5.3 API Changes

#### POST /query
**Request:**
```python
class QueryRequest(BaseModel):
    task_id: str
    entity: str
    component: str
    kb: Optional[str] = None  # NEW: explicit KB override (optional)
    context: Optional[Dict] = None
```

**Response:**
```python
class QueryResponse(BaseModel):
    task_id: str
    patterns: List[Dict]
    kb_used: str  # NEW: resolved KB name (e.g., "physics", "default")
    routing_tier: str  # NEW: which tier resolved the KB (explicit/entity_mapping/fallback)
```

#### POST /record
**Request:**
```python
class RecordRequest(BaseModel):
    schema_version: str
    task_id: str
    status: str  # "success" | "failed"
    timestamp: str
    kb: Optional[str] = None  # NEW: explicit KB override (optional)
    akc_context: Dict
```

**Response:**
```python
class RecordResponse(BaseModel):
    status: str
    kb_used: str  # NEW: resolved KB name
    patterns_written: int
```

#### POST /fix
**Request:**
```python
class FixRequest(BaseModel):
    category: str
    kb: Optional[str] = None  # NEW: explicit KB override (optional)
```

**Response:**
```python
class FixResponse(BaseModel):
    status: str
    kb_used: str  # NEW: resolved KB name
```

#### GET /stats
```
GET /stats?time_window=1h&kb=physics
GET /stats?time_window=1h&kb=default
```
- Query parameter: `kb` (optional)
  - `?kb=physics` → stats for physics KB only
  - `?kb=default` → stats for default KB only
  - No `?kb=` param when single KB registered → returns default KB (backward compat)
  - No `?kb=` param when multiple KBs registered → **error 400: require explicit KB**
  - `?kb=all` → **deferred to Phase 2** (after collecting customer feedback on aggregation semantics)

**Response:**
```python
class StatsResponse(BaseModel):
    kb_name: str  # NEW: which KB these stats are for (enables future aggregation)
    pattern_count: int
    avg_confidence: float
    latency_p95_ms: float
    # ... other stats fields
```

### 5.4 Implementation Plan (Two-Slice Strategy)

This plan ships **Slice 1** first (explicit KB only) for immediate value, then **Slice 2** (entity inference) in the next sprint after collecting feedback.

#### Phase 1: Config & Resolution (1 week, Slice 1 foundation)
- [ ] Add `KB_REGISTRY` parsing to `config.py` with fail-fast JSON validation
- [ ] Add startup validation: check all KB dirs exist, cross-validate mappings
- [ ] Implement `resolve_kb_dir(kb_override, entity) → KBContext`
- [ ] Add tests for routing resolution logic
- [ ] Design `KBContext` dataclass (path, name, safety_level) for future per-KB extension

#### Phase 2: Module Refactoring (1 week, internal only, backward compatible)
- [ ] Update `learning_integration.py` to accept `kb_dir` parameter
  - `apply_confidence_delta(task_result, kb_dir=None)`
  - `load_all_patterns(kb_dir=None)`
  - `append_pattern_version(kb_dir=None)`
- [ ] Update `safety_engine.py`, `monitoring_engine.py`, `failure_detection.py` similarly
- [ ] Add tests for each module with alternate KB paths
- [ ] Backward compatibility: if `kb_dir=None`, use global `KB_DIR` (current behavior)

#### Phase 3A: API Integration — Explicit KB Only (Slice 1, 1 week, user-facing)
- [ ] Add optional `kb` field to `/query`, `/record`, `/fix` request models
- [ ] Update route handlers to:
  - Extract `kb` from request
  - Call `resolve_kb_dir(kb_override=kb, entity=None)` (entity tier disabled for now)
  - Pass resolved `kb_dir` to engine functions
- [ ] **Add `kb_used` and `routing_tier` to all response bodies**
- [ ] Update `/stats?kb=physics` to query specific KB only
- [ ] Add logging for routing decisions (INFO level)
- [ ] **DO NOT** implement entity-based routing yet (defer to Slice 2)
- [ ] Launch Phase 3A in production (feature: explicit KB selection)

#### Phase 3B: Add Entity Inference (Slice 2, 1 week after Phase 3A feedback)
- [ ] Enable `ENTITY_KB_MAPPING` tier in routing (Tier 2, only if Tier 1 not provided)
- [ ] Update route handlers to extract `entity` from `akc_context`
- [ ] Call `resolve_kb_dir(kb_override=kb, entity=entity)` to use all 3 tiers
- [ ] Add tests for entity-based routing and fallback
- [ ] Monitor logs for silent fallbacks; add `routing_tier=fallback` counter

#### Phase 4: Testing & Documentation (1 week)
- [ ] End-to-end tests for multi-KB scenarios
- [ ] Concurrent write tests (different KBs)
- [ ] Routing fallback tests (all 3 tiers)
- [ ] Update API_REFERENCE.md with tenant field examples and `kb_used` in responses
- [ ] Write KB_ROUTING.md guide (configuration, examples, troubleshooting)
- [ ] Update CONFIGURATION.md with `AKC_SERVICE_KB_REGISTRY` and `AKC_SERVICE_ENTITY_KB_MAPPING` docs

---

## 6. Examples

### Example 1: Explicit KB Selection (Slice 1 — Available Week 2)

**Configuration:**
```bash
export AKC_SERVICE_KB_REGISTRY='{
  "default": "./kb/default",
  "physics": "./kb/physics",
  "animation": "./kb/animation"
}'
# ENTITY_KB_MAPPING not needed yet (Slice 1 doesn't use it)
```

**Query with explicit KB override:**
```json
POST /akc/v1/query
{
  "task_id": "phys_001",
  "entity": "physics",
  "component": "collision_detection",
  "kb": "physics"  ← Explicit (Tier 1)
}
```

**Response:**
```json
{
  "task_id": "phys_001",
  "patterns": [ /* ... */ ],
  "kb_used": "physics",
  "routing_tier": "explicit"
}
```
→ Routes to: `./kb/physics/patterns.jsonl`

**Default (no kb field):**
```json
POST /akc/v1/query
{
  "task_id": "anim_001",
  "entity": "animation",
  "component": "skeletal_mesh"
  /* no "kb" field */
}
```

**Response:**
```json
{
  "task_id": "anim_001",
  "patterns": [ /* ... */ ],
  "kb_used": "default",
  "routing_tier": "fallback"  ← No kb field, uses default in Slice 1
}
```
→ Routes to: `./kb/default/patterns.jsonl` (falls back to default since no `kb` field)

---

### Example 2: Entity-Based Auto-Routing (Slice 2 — Available Week 3)

**Configuration:**
```bash
export AKC_SERVICE_KB_REGISTRY='{
  "default": "./kb/default",
  "physics": "./kb/physics",
  "animation": "./kb/animation"
}'

export AKC_SERVICE_ENTITY_KB_MAPPING='{
  "entity:physics": "physics",
  "entity:animation": "animation",
  "entity:*": "default"
}'
```

**Physics Agent Query (no explicit kb field):**
```json
POST /akc/v1/query
{
  "task_id": "phys_001",
  "entity": "physics",
  "component": "collision_detection"
  /* no "kb" field — entity inference enabled */
}
```

**Response:**
```json
{
  "task_id": "phys_001",
  "patterns": [ /* ... */ ],
  "kb_used": "physics",
  "routing_tier": "entity_mapping"  ← Inferred from entity:physics mapping
}
```
→ Routes to: `./kb/physics/patterns.jsonl` (Tier 2: entity-based inference)

**Animation Agent Query:**
```json
POST /akc/v1/query
{
  "task_id": "anim_001",
  "entity": "animation",
  "component": "skeletal_mesh"
}
```
→ Routes to: `./kb/animation/patterns.jsonl` (Tier 2: matched `entity:animation`)

**Unknown Entity (wildcard fallback):**
```json
POST /akc/v1/query
{
  "task_id": "unknown_001",
  "entity": "rendering",
  "component": "shadow_mapping"
}
```

**Response:**
```json
{
  "task_id": "unknown_001",
  "patterns": [ /* ... */ ],
  "kb_used": "default",
  "routing_tier": "entity_wildcard"  ← Matched entity:* fallback
}
```
→ Routes to: `./kb/default/patterns.jsonl` (Tier 3: wildcard fallback)

### Example 3: Explicit KB Override (Power Users, Slice 1+)

**Configuration:** (same as Example 2)

**Request with explicit KB override (overrides any inference):**
```json
POST /akc/v1/query
{
  "task_id": "test_001",
  "entity": "physics",
  "component": "forces",
  "kb": "animation"  ← Explicit override (Tier 1, always wins)
}
```

**Response:**
```json
{
  "task_id": "test_001",
  "patterns": [ /* ... */ ],
  "kb_used": "animation",
  "routing_tier": "explicit"  ← Explicit override always takes priority
}
```
→ Routes to: `./kb/animation/patterns.jsonl` (not physics!)

**Use case:** Testing, debugging, or cross-domain learning experiments (power users only)

---

### Example 4: Backward Compatible (Single KB, No Multi-KB Config)

**Configuration:** (default, no `AKC_SERVICE_KB_REGISTRY` set)
```python
# Defaults:
KB_REGISTRY = {"default": "<package>/kb"}
ENTITY_KB_MAPPING = {"entity:*": "default"}
```

**Request (no kb field, entity inference disabled without mapping):**
```json
POST /akc/v1/query
{
  "task_id": "test_001",
  "entity": "physics",
  "component": "collision"
}
```

**Response:**
```json
{
  "task_id": "test_001",
  "patterns": [ /* ... */ ],
  "kb_used": "default",
  "routing_tier": "fallback"  ← Single KB: all requests route to default
}
```
→ Routes to: `<package>/kb/patterns.jsonl` (same as today)

**Existing code works unchanged. `kb_used` and `routing_tier` fields are new but backward compatible.**

---

## 7. Success Criteria

### Slice 1: Explicit KB Selection

**Functional**
- [ ] `kb` field optional in `/query`, `/record`, `/fix`
- [ ] Explicit KB override works (Tier 1 routing)
- [ ] Backward compatibility maintained (requests without `kb` field work)
- [ ] All modules accept `kb_dir` parameter and use it
- [ ] `KBContext` dataclass designed and returned (not yet used for per-KB config)

**Observability (Critical)**
- [ ] All responses include `kb_used: "{name}"` (highest-impact mitigation)
- [ ] All responses include `routing_tier: "explicit"` (for Slice 1)
- [ ] All logs include `KB=physics` tag on every request
- [ ] Startup validation logs all registered KBs and entity mappings

**Performance**
- [ ] KB resolution latency < 1ms
- [ ] No change to per-request I/O latency (still <50ms for /query)

**Documentation (Slice 1)**
- [ ] API_REFERENCE.md updated with `kb` field and `kb_used` response field
- [ ] CONFIGURATION.md updated with `AKC_SERVICE_KB_REGISTRY` docs
- [ ] Basic KB_ROUTING.md guide (explicit KB usage only)

### Slice 2: Entity-Based Inference (Next Sprint)

**Functional**
- [ ] Entity-based routing works (Tier 2)
- [ ] Default/wildcard fallback works (Tier 3)
- [ ] All 3 routing tiers tested
- [ ] Data isolation verified (physics patterns don't appear in animation queries)
- [ ] Concurrent writes to different KBs work independently

**Observability**
- [ ] Routing decisions logged at INFO level
- [ ] `routing_tier` includes `entity_mapping`, `entity_wildcard`, `fallback` values
- [ ] Prometheus metrics tagged by `kb_name`
- [ ] `/stats?kb=physics` endpoint works for per-KB queries

**Documentation (Slice 2)**
- [ ] KB_ROUTING.md guide expanded (entity-based routing, fallback behavior, debugging)
- [ ] Troubleshooting section: how to debug routing decisions
- [ ] Examples: multi-domain setup, entity mapping edge cases

---

## 8. Design Decisions

### Resolved Questions

1. **Field Naming:** `kb` instead of `tenant`
   - **Rationale:** `kb` matches the query parameter `?kb=physics`, improving API consistency. Also signals "override" more clearly to users than `tenant`. (UX feedback)

2. **Rollout Strategy:** Two-slice approach
   - **Slice 1 (Week 1-2):** Explicit `kb` field only. Ship immediately with full test coverage.
   - **Slice 2 (Week 3-4):** Add entity-based inference. Collect feedback on Slice 1 before adding complexity.
   - **Rationale:** Reduces risk, allows faster MVP, keeps feedback loop tight. (PO + Architect consensus)

3. **Field Requirement:** Make `kb` optional
   - **Rationale:** Support backward compatibility and gradual adoption. Explicit override for power users, implicit routing for common case.

4. **KB Auto-Creation:** Startup behavior
   - **Decision:** Startup should warn but not fail if KB directories don't exist. Auto-create on first write.
   - **Rationale:** Less disruptive; safety is maintained via fcntl locks.

5. **Per-KB Safety Levels:** Global for MVP
   - **Decision:** Global `SAFETY_LEVEL` applies to all KBs in MVP. Per-KB override in Phase 3+ after customer feedback.
   - **Rationale:** Simplifies MVP, reduces support burden, design is extensible via `KBContext`.

6. **Config Validation:** Fail-fast on errors
   - **Decision:** Raise `ValueError` on JSON parse errors or mapping inconsistencies. Do not silently fall back.
   - **Rationale:** Prevents silent data loss (biggest risk identified by devil's advocate). Startup should be noisy about config problems.

7. **Response Format:** Include routing context
   - **Decision:** All responses include `kb_used: "{name}"` and `routing_tier: "{explicit|entity|fallback}"`.
   - **Rationale:** Eliminates the #1 debugging question ("which KB got my data?"). Single highest-impact mitigation across all design decisions.

### Deferred Questions (Post-MVP)

8. **Per-KB Metrics Aggregation:** `?kb=all` semantics
   - **Decision:** Defer `?kb=all` entirely from Phase 1. Ship `?kb=physics` only.
   - **Rationale:** No real customer requirements yet; speculative design leads to wrong semantics. When customers ask, implement to their spec.
   - **Future:** Phase 2 or later, after collecting feedback on what aggregation means in practice.

9. **Cross-KB Queries:** Multi-KB in single request
   - **Decision:** Not in MVP. Each request routes to exactly one KB.
   - **Future:** Could support `?kb=physics,animation` as comma-separated list in Phase 3+ (once single KB routing is battle-tested).

10. **Config File as Primary Source:**
    - **Decision:** MVP uses JSON env vars only (consistent with existing config pattern, zero deployment friction).
    - **Future:** Offer `AKC_SERVICE_CONFIG_FILE` path override in Phase 2 for teams with 15+ KBs who want file-based config.
    - **Rationale:** Architectural: plan the abstraction now (config source is pluggable in `config.py`), implement the file path override later when needed.

---

## 9. Rollout Timeline (Two-Slice Strategy)

### Week 1: Phase 1 + Phase 2 (Config & Refactoring)
- Implement config parsing with validation
- Implement `KBContext` design pattern
- Refactor `learning_integration.py`, `safety_engine.py`, etc. to accept `kb_dir`
- Add comprehensive tests for routing and module behavior
- **Status:** Internal only, no API changes yet

### Week 2: Phase 3A (Explicit KB, Slice 1)
- Add optional `kb` field to `/query`, `/record`, `/fix`
- Add `kb_used` and `routing_tier` to all responses
- Update `/stats?kb=physics` to query by KB name
- **Do not implement** entity-based inference yet
- Deploy to staging/production with feature enabled
- Monitor logs: watch for `kb_used` field appearing in all responses
- **Status:** **LAUNCH** — Slice 1 is live. Explicit KB selection available.

### Week 3: Phase 3B (Entity Inference, Slice 2)
- Enable `ENTITY_KB_MAPPING` tier in routing
- Add entity-based routing tests
- Monitor logs for `routing_tier=entity_mapping` and `routing_tier=fallback` events
- **Status:** **LAUNCH** — Slice 2 is live. Auto-routing via entity available.

### Week 4: Phase 4 (Docs & Stabilization)
- Publish KB_ROUTING.md guide (configuration, examples, troubleshooting)
- Update API_REFERENCE.md with `kb` field and `kb_used` response field
- Update CONFIGURATION.md with env var docs
- Collect customer feedback on aggregation semantics (input for future `?kb=all`)
- **Status:** **GA** — Multi-KB routing is stable and documented

### Future (Phase 2+, when customer demand justifies)
- Implement `?kb=all` after collecting real aggregation requirements
- Add per-KB safety levels (if compliance use case demands it)
- Add config file as primary source (when 15+ KB deployments arrive)

---

## 10. Appendix: Code Skeleton

### config.py
```python
import json
from pathlib import Path
from typing import Dict, Optional

KB_REGISTRY: Dict[str, str] = json.loads(
    os.environ.get(
        "AKC_SERVICE_KB_REGISTRY",
        json.dumps({"default": str(_DEFAULT_KB_DIR)})
    )
)

ENTITY_KB_MAPPING: Dict[str, str] = json.loads(
    os.environ.get(
        "AKC_SERVICE_ENTITY_KB_MAPPING",
        json.dumps({"entity:*": "default"})
    )
)

def resolve_kb_dir(
    kb_override: Optional[str] = None,
    entity: Optional[str] = None,
    global_safety_level: int = 1
) -> KBContext:
    """Resolve KB directory using 3-tier fallback. (Full implementation in 5.2)"""
    pass
```

### routes.py (example, Phase 3A — Explicit KB Only)
```python
from akc_service.config import resolve_kb_dir

@router.post("/record")
async def record_task_outcome(request: RecordRequest) -> RecordResponse:
    # Extract routing hints
    kb_override = getattr(request, 'kb', None)  # Explicit KB field
    entity = None  # Phase 3A: entity inference disabled, use Tier 3 (default)
    
    # Resolve KB
    kb_context = resolve_kb_dir(
        kb_override=kb_override,
        entity=entity,
        global_safety_level=SAFETY_LEVEL
    )
    
    # Pass to learning engine
    delta_result = apply_confidence_delta(
        task_result=build_task_result(request),
        kb_dir=kb_context.path
    )
    
    return RecordResponse(
        status="success",
        kb_used=kb_context.name,
        patterns_written=len(delta_result.patterns)
    )
```

### routes.py (example, Phase 3B — With Entity Inference)
```python
# Phase 3B: Add entity extraction to routing
@router.post("/record")
async def record_task_outcome(request: RecordRequest) -> RecordResponse:
    # Extract routing hints
    kb_override = getattr(request, 'kb', None)
    entity = extract_entity_from_context(request.akc_context)  # Phase 3B: enabled
    
    # Resolve KB (now uses all 3 tiers)
    kb_context = resolve_kb_dir(
        kb_override=kb_override,
        entity=entity,
        global_safety_level=SAFETY_LEVEL
    )
    
    # Rest is same as Phase 3A...
    delta_result = apply_confidence_delta(
        task_result=build_task_result(request),
        kb_dir=kb_context.path
    )
    
    logger.info(f"KB routing: {kb_context.name} ({kb_context.safety_level})")
    
    return RecordResponse(
        status="success",
        kb_used=kb_context.name,
        patterns_written=len(delta_result.patterns)
    )
```

---

## 11. References

- [API_REFERENCE.md](docs/API_REFERENCE.md) — Current REST API spec
- [CONFIGURATION.md](docs/CONFIGURATION.md) — Current configuration guide
- [CAPABILITIES.md](docs/CAPABILITIES.md) — System architecture overview
- Graph analysis: `graphify-out/GRAPH_REPORT.md` — Dependency analysis
