# akc-service: Capabilities & Components

## Overview

**akc-service** (Agent Knowledge Collective) is a learning, safety, and metrics engine that powers intelligent pattern-based decision-making for Godot game agents. It maintains a knowledge base of proven solutions, validates new fixes, and continuously improves confidence scores based on real task outcomes.

## Core Capabilities

### 1. Learning Engine

The learning engine manages pattern storage, retrieval, and confidence scoring.

**What it does:**
- Stores proven patterns (entity:component solutions) in an append-only knowledge base
- Loads patterns into memory for fast lookup (millisecond-scale queries)
- Tracks confidence scores: how reliable is each pattern? (0.0 = unreliable, 1.0 = guaranteed)
- Updates confidence based on task outcomes (success/failure delta application)
- Classifies patterns into tiers:
  - **Gold** (0.85-1.0): Guardrail-protected, highest trust, never auto-modified
  - **Production** (0.70-0.85): Normal use, trusted, actively recommended
  - **Experimental** (0.50-0.70): In development, under improvement
  - **Demoted** (0.0-0.50): Unreliable, excluded from recommendations

**Files:**
- `patterns.jsonl` — append-only KB with pattern versions
- `confidence_history.jsonl` — immutable audit trail of confidence updates

**Key operations:**
```python
load_all_patterns()              # Load KB into memory
find_pattern_by_id(id, patterns) # Find pattern by unique ID
determine_tier(confidence)       # Map confidence → tier
append_pattern_version(pattern)  # Atomically append new version
```

### 2. Safety Engine

The safety engine enforces hard guardrails and prevents unsafe changes.

**What it does:**
- Validates all fix proposals against 6 hard guardrails before deployment
- Routes fixes to review tiers (staged deployment: test → canary → production)
- Detects pattern conflicts (contradictory fixes for same entity:component)
- Monitors deployment stages and auto-rolls back on failures
- Provides escape hatches for manual control (caution, quarantine, re-validate, reset)

**Guardrails (enforced):**
1. No pattern confidence > 0.95 (cap to prevent over-confidence)
2. No confidence jump > 0.15 in single update (prevent wild swings)
3. No demoted pattern auto-promotion (manual review required)
4. No fix that contradicts existing guardrail (safety rules locked)
5. No concurrent modification of same pattern (conflict detection)
6. No unsafe staging transitions (validation required between stages)

**Files:**
- `safety_state.json` — deployment status and escape hatch configuration
- `fix_history.jsonl` — all proposed and applied fixes

### 3. CSP Solver

The Constraint Satisfaction Problem (CSP) solver generates candidate fixes for patterns.

**What it does:**
- Enumerates possible pattern modifications that respect all 6 guardrails
- Ranks candidates by feasibility and expected confidence gain
- Returns top-N solutions to the fix-generation system
- Executes in < 50ms per query (latency budget)

**Input:** Pattern ID, entity, component, optional context
**Output:** Ranked list of candidate modifications with confidence projections

### 4. Validation Engine

The validation engine ensures fixes work before deployment.

**What it does:**
- Generates unit tests from fix descriptions (template-based)
- Runs generated tests against Godot headless (game simulation)
- Lints GDScript before execution (early syntax error detection)
- Executes integration tests
- Orchestrates 3-stage pipeline: unit tests → integration → QC review
- Auto-rolls back on test failure
- Tracks deployment stages

**Stages:**
1. **Unit Tests**: Isolated test of fix logic (< 5s)
2. **Integration Tests**: Full game scenario simulation (< 15s)
3. **QC Review**: Manual verification gate (staged deployment)

**Files:**
- `fix_history.jsonl` — test results and rollback logs
- Generated tests in `res://tests/generated/`

### 5. Monitoring Engine

The monitoring engine provides observability and alerts.

**What it does:**
- Collects latency metrics for every query (response time tracking)
- Monitors SLA compliance: 50ms budget per request
- Detects alert conditions:
  - Error spikes (success rate drop > 2%)
  - Confidence drops (> 15% drop in 24h)
  - Pattern conflicts (2+ conflicting patterns)
  - Rollback cascades (3+ rollbacks in 1 day)
- Sends alerts via email/webhook
- Provides health dashboard

**Metrics:**
- Query latency (min/max/avg/p95 in milliseconds)
- Pattern confidence distribution
- Gold-tier pattern count
- Success rate per entity:component
- Deployment failure rate

**Files:**
- `latency_samples.jsonl` — timestamped query latency records
- Alert thresholds configurable via environment

### 6. Validation Engine (Test & Linting)

Extended validation for code quality and safety.

**What it does:**
- Generates unit tests from fix templates
- Lints GDScript code (syntax, style, guardrail compliance)
- Validates test execution against Godot headless
- Records test results with coverage metrics
- Generates linting gate (fail-fast before Godot invocation)

**Linting checks:**
- Syntax errors
- Type mismatches
- Guardrail violations (e.g., forbidden class usage)
- Performance warnings

### 7. REST API

Five endpoints expose akc-service to agents and external systems.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/akc/v1/health` | Service health check |
| POST | `/akc/v1/query` | Retrieve patterns for entity:component |
| POST | `/akc/v1/record` | Record task outcome (fire-and-forget) |
| POST | `/akc/v1/fix` | Get fix recommendations by category |
| GET | `/akc/v1/stats` | KB statistics and SLA status |
| POST | `/akc/v1/update` | Manual confidence override |

**Features:**
- JSON request/response with Pydantic validation
- CORS enabled for localhost development
- Request logging (all requests and status codes)
- Global exception handling (500 → JSON error)
- HTTPS-ready (supports reverse proxy)

### 8. Godot Adapter

Integration bridge for Godot game engine.

**What it does:**
- Records GDScript linting results to akc-service
- Records test execution results (pass/fail)
- Queries patterns from orchestrator agent
- Handles service unavailability gracefully (continues without patterns)
- Fire-and-forget HTTP 202 acceptance for recording

**Usage:**
```gdscript
# In GDScript
var adapter = GodotAKCAdapter.new()
adapter.record_lint_result(lint_result, file_path)  # HTTP 202
adapter.record_test_result(test_result, scene_path)     # HTTP 202
```

See `adapters/godot/README.md` for setup and examples.

## System Architecture

### Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent System                             │
│  (Orchestrator, MCP Agent, Script Agent, QC Agent)             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ├─→ HTTP POST /akc/v1/query
                             ├─→ HTTP POST /akc/v1/record
                             ├─→ HTTP GET /akc/v1/stats
                             └─→ HTTP POST /akc/v1/update
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     AKC Service                                 │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI Application (main.py, routes.py)                      │
│  - Request routing                                              │
│  - Pydantic validation                                          │
│  - Exception handling                                           │
│  - CORS middleware                                              │
├─────────────────────────────────────────────────────────────────┤
│  Learning Engine              Safety Engine                     │
│  - Pattern I/O                - Guardrail validation            │
│  - Confidence updates         - Fix routing                     │
│  - Tier classification        - Conflict detection              │
│  - Version history            - Escape hatches                  │
├─────────────────────────────────────────────────────────────────┤
│  CSP Solver                   Validation Engine                 │
│  - Constraint enumeration     - Test generation                 │
│  - Candidate ranking          - Linting (GDScript)              │
│  - < 50ms execution           - Pipeline orchestration          │
├─────────────────────────────────────────────────────────────────┤
│  Monitoring Engine            Metrics Collector                 │
│  - Latency tracking           - Time-series metrics             │
│  - Alert detection            - SLA compliance                  │
│  - Health dashboard           - Performance analysis            │
├─────────────────────────────────────────────────────────────────┤
│  Knowledge Base (append-only)                                   │
│  - patterns.jsonl (primary)                                     │
│  - confidence_history.jsonl                                     │
│  - fix_history.jsonl                                            │
│  - failure_index.jsonl                                          │
│  - latency_samples.jsonl                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Data Dependencies

```
Agent Task Execution
         │
         ├─→ Query Patterns
         │   └─→ Learning Engine
         │       └─→ patterns.jsonl (read)
         │
         └─→ Record Outcome
             └─→ Record Endpoint
                 └─→ Learning Engine (async update)
                     ├─→ Apply confidence delta
                     │   └─→ patterns.jsonl (append new version)
                     ├─→ Log confidence change
                     │   └─→ confidence_history.jsonl (append)
                     └─→ Monitoring Engine
                         └─→ latency_samples.jsonl (append)
```

## Data Flow

### Pattern Query Flow (Synchronous)

1. **Agent** sends `POST /akc/v1/query` with (entity, component, context)
2. **Router** validates request (Pydantic schema)
3. **Learning Engine** loads patterns.jsonl
4. **Matcher** filters by (entity, component) and confidence tier
5. **Sorter** ranks by confidence (descending)
6. **Response** returns patterns + query latency in < 50ms

### Task Outcome Recording Flow (Async)

1. **Agent** sends `POST /akc/v1/record` with (task_id, status, akc_context)
2. **Router** validates schema_version == "1.0" → returns 202 Accepted immediately
3. **Background worker** (async) applies delta:
   - For each active pattern: delta = (success=+0.05, failure=-0.10)
   - New confidence = old confidence + delta (capped at [0.0, 0.95])
   - Determine new tier from confidence threshold
   - If tier changes: append new version to patterns.jsonl
   - Log to confidence_history.jsonl
4. **Monitoring Engine** records query latency
5. Client receives 202 before DB write completes (fire-and-forget)

### Fix Recommendation Flow

1. **Validation Engine** detects pattern failure
2. **CSP Solver** enumerates candidates (< 50ms)
3. **Candidate Ranker** sorts by feasibility
4. **Test Generator** creates unit tests (template-based)
5. **Godot Headless** executes tests (< 5s)
6. **Linter** validates GDScript syntax (early fail-fast)
7. **Pipeline Orchestrator** gates staged deployment

## Knowledge Base Structure

### patterns.jsonl

Append-only file of pattern records.

```json
{
  "id": "pattern_001",
  "entity": "player",
  "component": "HealthComponent",
  "confidence": 0.85,
  "confidence_tier": "gold",
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-05-05T14:22:15Z",
  "version": {
    "current": "v3",
    "history": [
      {
        "version_id": "v1",
        "confidence_snapshot": 0.50,
        "timestamp": "2026-01-15T10:30:00Z",
        "change_reason": "initial_seed",
        "tier": "experimental"
      }
    ]
  },
  "description": "Health regeneration logic for player",
  "category": "implementation",
  "fixes": [
    {
      "fix_id": "fix_001",
      "description": "Increase regen rate by 10%",
      "status": "validated"
    }
  ]
}
```

### confidence_history.jsonl

Immutable audit trail of all confidence updates.

```json
{
  "history_id": "ch-20260505T142215",
  "timestamp": "2026-05-05T14:22:15Z",
  "pattern_id": "pattern_001",
  "old_confidence": 0.80,
  "new_confidence": 0.85,
  "confidence_delta": 0.05,
  "task_id": "task-042",
  "task_status": "success",
  "tier_change": "production → gold",
  "update_type": "outcome_delta",
  "reason": "task success outcome"
}
```

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AKC_SERVICE_KB_DIR` | `<package>/kb/` | Knowledge base directory |
| `AKC_SERVICE_REPO_ROOT` | cwd | Project root for Godot tests |
| `AKC_SERVICE_SAFETY_LEVEL` | 1 | Safety strictness: 0=permissive, 1=standard, 2=strict |
| `AKC_SERVICE_URL` | `http://localhost:8000` | Service URL (for HTTP clients) |

See `CONFIGURATION.md` for detailed setup and tuning.

## Reliability & Safety

### Guardrails

6 hard guardrails prevent unsafe changes:

1. Confidence never exceeds 0.95 (prevent over-confidence)
2. Single update never > 0.15 delta (prevent wild swings)
3. Demoted patterns cannot auto-promote (manual review required)
4. Guardrails cannot be violated by fixes
5. Concurrent modifications detected and blocked
6. Unsafe stage transitions are blocked

### Recovery Mechanisms

- **Append-only KB**: No data loss on crash (can recover by re-reading)
- **File locking**: Prevents concurrent corruption on KB files
- **Rollback support**: 3-stage pipeline with auto-rollback on test failure
- **Manual escape hatches**: Override safety in emergencies (with audit trail)

### SLA

- **Query latency budget**: 50ms per request (p95 < 40ms typical)
- **Record acceptance**: < 10ms (202 before background update)
- **Health check**: < 5ms

## Next Steps

- **Operators**: See [CONFIGURATION.md](CONFIGURATION.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Integrators**: See [INTEGRATION.md](INTEGRATION.md) for Godot setup
- **API users**: See [API_REFERENCE.md](API_REFERENCE.md) for endpoint specs
- **Developers**: See [ERROR_HANDLING.md](ERROR_HANDLING.md) for failure modes
