# akc-service REST API Reference

## Base URL

```
http://localhost:8000/akc/v1
```

All examples assume akc-service is running locally on port 8000.

## Endpoints Summary

| Method | Endpoint | Purpose | Latency Budget |
|--------|----------|---------|-----------------|
| GET | `/health` | Service health check | < 5ms |
| POST | `/query` | Retrieve patterns for entity:component | < 50ms |
| POST | `/record` | Record task outcome (202 fire-and-forget) | < 10ms |
| POST | `/fix` | Get fix recommendations by category | < 100ms |
| GET | `/stats` | KB statistics and SLA status | < 100ms |
| POST | `/update` | Manual confidence override | < 100ms |
| POST | `/reset` | Restore KB to startup checkpoint | < 500ms |
| POST | `/kb/export-markdown` | Export patterns to markdown files | < 2s |
| GET  | `/sync/status`  | Sync queue state and remote reachability | < 50ms |
| GET  | `/sync/export`  | Export local patterns (used by remote pull) | < 100ms |
| POST | `/sync/push`    | Push queued patterns to remote KB | < 30s |
| POST | `/sync/pull`    | Pull patterns from remote KB | < 30s |
| POST | `/sync/receive` | Inbound: accept patterns from remote node | < 100ms |

---

## GET /health

Service health check endpoint.

### Request

```bash
curl -X GET http://localhost:8000/akc/v1/health
```

### Response (200 OK)

```json
{
  "status": "healthy",
  "timestamp": "2026-05-05T14:22:15Z"
}
```

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Service is healthy |
| 500 | Internal error (service degraded) |

---

## POST /query

Retrieve patterns matching an entity:component pair from the knowledge base.

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "task_id": "task-042",
  "entity": "player",
  "component": "HealthComponent",
  "context": {
    "difficulty": "hard",
    "phase": "late_game"
  },
  "kb": "physics"
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Unique identifier for this task (for logging) |
| `entity` | string | Yes | Entity name (e.g., 'player', 'enemy_knight', 'boss') |
| `component` | string | Yes | Component name (e.g., 'HealthComponent', 'MovementComponent') |
| `context` | object | No | Additional context for the query (optional metadata) |
| `kb` | string | No | Knowledge base name from AKC_SERVICE_KB_REGISTRY (e.g., "physics"). Omit to use entity-inferred or default KB. |

**Valid entity:component pairs:**
```
player: HealthComponent, MovementComponent, CombatComponent, PhysicsComponent,
        AnimationComponent, SignalComponent
enemy_knight: HealthComponent, PhysicsComponent, AnimationComponent, CombatComponent,
              MovementComponent
enemy_mage: HealthComponent, CombatComponent, AnimationComponent
minion: HealthComponent, PhysicsComponent, MovementComponent, CombatComponent
boss: HealthComponent, CombatComponent, AnimationComponent
global: PhysicsComponent, EventSystem, autoload, cross_component
ui: EventSystem, SignalComponent
camera: PhysicsComponent
audio: SignalComponent
```

### Example Request (curl)

```bash
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "task-042",
    "entity": "player",
    "component": "HealthComponent",
    "context": {"difficulty": "hard"}
  }'
```

### Example Request (Python)

```python
import requests

response = requests.post(
    "http://localhost:8000/akc/v1/query",
    json={
        "task_id": "task-042",
        "entity": "player",
        "component": "HealthComponent",
        "context": {"difficulty": "hard"}
    }
)
patterns = response.json()
```

### Response (200 OK)

```json
{
  "patterns": [
    {
      "id": "pattern_001",
      "confidence": 0.85,
      "tier": "gold"
    },
    {
      "id": "pattern_002",
      "confidence": 0.72,
      "tier": "production"
    },
    {
      "id": "pattern_003",
      "confidence": 0.60,
      "tier": "experimental"
    }
  ],
  "query_latency_ms": 12.5,
  "source": "kb",
  "kb_used": "physics",
  "routing_tier": "explicit"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `patterns` | array | List of matching patterns, sorted by confidence (descending) |
| `patterns[].id` | string | Unique pattern identifier |
| `patterns[].confidence` | number | Confidence score [0.0, 1.0] |
| `patterns[].tier` | string | Confidence tier: "gold", "production", "experimental", "demoted" |
| `query_latency_ms` | number | Query execution time in milliseconds |
| `source` | string | Source of patterns: "kb" (knowledge base) or "cache" |
| `kb_used` | string | Name of the KB directory used to serve this request |
| `routing_tier` | string | How the KB was selected: see Routing Tier Values table below |

### Routing Tier Values

| Value | Description |
|-------|-------------|
| `"explicit"` | `kb` field was present in the request body (Tier 1 — highest priority) |
| `"entity_mapping"` | Entity name matched an exact entry in `ENTITY_KB_MAPPING` (Tier 2) |
| `"entity_wildcard"` | Entity name matched the `entity:*` wildcard in `ENTITY_KB_MAPPING` (Tier 2) |
| `"fallback"` | No `kb` field and no entity match — default KB used (Tier 3) |

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success — patterns returned (may be empty list) |
| 400 | Missing or invalid entity/component |
| 500 | Server error — KB loading failed |

### Error Response (400 Bad Request)

```json
{
  "error": "entity and component are required"
}
```

### Error Response (500 Internal Server Error)

```json
{
  "error": "Pattern query failed: <details>"
}
```

---

## POST /record

Record a task outcome (success/failure) and trigger asynchronous confidence delta updates.

**Important:** This endpoint returns **202 Accepted** immediately (fire-and-forget). The actual KB update happens asynchronously in the background.

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "schema_version": "1.0",
  "task_id": "task-042",
  "status": "success",
  "timestamp": "2026-05-05T14:22:15Z",
  "akc_context": {
    "knowledge_patterns_active": [
      {
        "id": "pattern_001",
        "confidence": 0.85
      },
      {
        "id": "pattern_002",
        "confidence": 0.72
      }
    ],
    "fixes_applied": [
      {
        "fix_id": "fix_001",
        "applied_at": "2026-05-05T14:20:00Z"
      }
    ]
  },
  "kb": "physics"
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | Yes | Must be exactly "1.0" |
| `task_id` | string | Yes | Unique task identifier |
| `status` | string | Yes | Task outcome: "success" or "failed" |
| `timestamp` | string | Yes | ISO 8601 completion timestamp |
| `akc_context` | object | Yes | Context about which patterns were active during task |
| `kb` | string | No | Knowledge base name from AKC_SERVICE_KB_REGISTRY (e.g., "physics"). Omit to use entity-inferred or default KB. |

**akc_context structure:**

```json
{
  "knowledge_patterns_active": [
    {
      "id": "pattern_001",
      "confidence": 0.85,
      "used": true
    }
  ],
  "fixes_applied": [
    {
      "fix_id": "fix_001",
      "applied_at": "2026-05-05T14:20:00Z",
      "category": "implementation"
    }
  ]
}
```

### Example Request (curl)

```bash
curl -X POST http://localhost:8000/akc/v1/record \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "task_id": "task-042",
    "status": "success",
    "timestamp": "2026-05-05T14:22:15Z",
    "akc_context": {
      "knowledge_patterns_active": [
        {"id": "pattern_001", "confidence": 0.85}
      ]
    }
  }'
```

### Example Request (Python)

```python
import requests
from datetime import datetime, timezone

response = requests.post(
    "http://localhost:8000/akc/v1/record",
    json={
        "schema_version": "1.0",
        "task_id": "task-042",
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        "akc_context": {
            "knowledge_patterns_active": [
                {"id": "pattern_001", "confidence": 0.85}
            ]
        }
    }
)
assert response.status_code == 202  # Accepted, not 200 OK
result = response.json()
```

### Response (202 Accepted)

```json
{
  "accepted": true,
  "task_id": "task-042",
  "update_mode": "async",
  "patterns_to_update": 1,
  "timestamp": "2026-05-05T14:22:16Z",
  "kb_used": "physics",
  "routing_tier": "explicit"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `accepted` | boolean | Always true on success |
| `task_id` | string | Echoed task ID |
| `update_mode` | string | "async" (KB update in background) or "sync" (critical patterns) |
| `patterns_to_update` | integer | Number of patterns that will be updated |
| `timestamp` | string | Server timestamp (ISO 8601) |
| `kb_used` | string | Name of the KB directory used to serve this request |
| `routing_tier` | string | How the KB was selected. See [Routing Tier Values](#routing-tier-values) in the /query section above. |

### Status Codes

| Code | Meaning |
|------|---------|
| 202 | Accepted — KB update queued for background processing |
| 400 | Invalid schema_version or status |
| 500 | Server error — could not queue update |

### Error Response (400 Bad Request)

```json
{
  "error": "schema_version must be '1.0', got '2.0'"
}
```

### Fire-and-Forget Semantics

- Client receives **202** immediately (< 10ms typical)
- KB write happens **asynchronously** in background
- No guarantee on completion time
- If service crashes before background update, outcome is lost (acceptable for learning)
- For critical tasks, caller should implement timeout + retry if needed

---

## POST /fix

Retrieve fix recommendations for patterns matching a category.

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "signature_hash": "sha256_abc123def456",
  "category": "implementation",
  "kb": "physics"
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `signature_hash` | string | Yes | Pattern signature hash (for matching KB entries) |
| `category` | string | Yes | Fix category (enum: "detection", "implementation", "testing", "documentation", "other") |
| `kb` | string | No | Knowledge base name from AKC_SERVICE_KB_REGISTRY (e.g., "physics"). Omit to use entity-inferred or default KB. |

**Valid categories:**
- `detection` — Logic/algorithm fixes
- `implementation` — Code/integration fixes
- `testing` — Test coverage improvements
- `documentation` — Doc/comment improvements
- `other` — Miscellaneous fixes

### Example Request (curl)

```bash
curl -X POST http://localhost:8000/akc/v1/fix \
  -H "Content-Type: application/json" \
  -d '{
    "signature_hash": "sha256_abc123def456",
    "category": "implementation"
  }'
```

### Response (200 OK)

```json
{
  "fixes": [
    {
      "fix_id": "fix_001",
      "description": "Increase health regeneration rate by 10%",
      "status": "validated",
      "category": "implementation"
    },
    {
      "fix_id": "fix_002",
      "description": "Add cooldown between regen ticks",
      "status": "validated",
      "category": "implementation"
    }
  ],
  "category": "implementation",
  "count": 2,
  "kb_used": "physics",
  "routing_tier": "explicit"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `fixes` | array | List of fix recommendations matching the category |
| `category` | string | The requested fix category |
| `count` | integer | Number of fixes returned |
| `kb_used` | string | Name of the KB directory used to serve this request |
| `routing_tier` | string | How the KB was selected. See [Routing Tier Values](#routing-tier-values) in the /query section above. |

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success — fixes returned |
| 400 | Invalid category |
| 404 | No patterns found matching category |
| 500 | Server error — KB loading failed |

---

## GET /stats

Retrieve knowledge base statistics: latency compliance, tier distribution, average confidence.

### Request

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `time_window` | string | "all" | Time window: "all", "24h", "7d", or "30d" |
| `kb` | string | — | Knowledge base name from AKC_SERVICE_KB_REGISTRY. Required when multiple KBs are registered; omit when only one KB is registered. |

### Example Request (curl)

```bash
# Single-KB deployment (kb param optional)
curl -X GET "http://localhost:8000/akc/v1/stats?time_window=24h"

# Multi-KB deployment (kb param required)
curl -X GET "http://localhost:8000/akc/v1/stats?kb=physics&time_window=24h"
```

### Example Request (Python)

```python
import requests

response = requests.get(
    "http://localhost:8000/akc/v1/stats",
    params={"time_window": "24h"}
)
stats = response.json()
print(f"SLA Status: {stats['sla_status']}")
print(f"Gold-tier patterns: {stats['gold_tier_count']}")
print(f"Avg confidence: {stats['avg_confidence']}")
```

### Response (200 OK)

```json
{
  "sample_count": 427,
  "latency_stats": {
    "min_ms": 2.5,
    "max_ms": 48.7,
    "avg_ms": 15.2,
    "p95_ms": 38.4
  },
  "sla_status": "HEALTHY",
  "gold_tier_count": 12,
  "avg_confidence": 0.76,
  "kb_used": "physics",
  "routing_tier": "explicit"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `sample_count` | integer | Number of query samples in the window |
| `latency_stats.min_ms` | number | Minimum query latency (ms) |
| `latency_stats.max_ms` | number | Maximum query latency (ms) |
| `latency_stats.avg_ms` | number | Average query latency (ms) |
| `latency_stats.p95_ms` | number | 95th percentile latency (ms) |
| `sla_status` | string | "HEALTHY" (p95 < 50ms) or "WARNING" (p95 >= 50ms) |
| `gold_tier_count` | integer | Count of patterns in gold tier |
| `avg_confidence` | number | Average confidence across all patterns [0.0, 1.0] |
| `kb_used` | string | Name of the KB directory used to serve this request |
| `routing_tier` | string | How the KB was selected. See [Routing Tier Values](#routing-tier-values) in the /query section above. |

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success — stats returned |
| 400 | Multiple KBs registered and no ?kb= param provided |
| 500 | Server error — stats collection failed |

---

## POST /update

Manually override confidence score for a pattern.

**Use case:** Manual guardrail updates (e.g., marking a gold-tier pattern as demoted due to discovered bug).

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "pattern_id": "pattern_001",
  "new_score": 0.55,
  "reason": "Discovered critical bug in health regeneration"
}
```

**Field Descriptions:**

| Field | Type | Required | Range | Description |
|-------|------|----------|-------|-------------|
| `pattern_id` | string | Yes | — | Pattern ID to update |
| `new_score` | number | Yes | [0.0, 0.95] | New confidence score (capped at 0.95) |
| `reason` | string | Yes | — | Reason for manual override (audit trail) |

### Example Request (curl)

```bash
curl -X POST http://localhost:8000/akc/v1/update \
  -H "Content-Type: application/json" \
  -d '{
    "pattern_id": "pattern_001",
    "new_score": 0.55,
    "reason": "Manual demotion: discovered critical bug"
  }'
```

### Response (200 OK)

```json
{
  "pattern_id": "pattern_001",
  "old_score": 0.85,
  "new_score": 0.55,
  "updated_at": "2026-05-05T14:22:15Z"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `pattern_id` | string | Updated pattern ID |
| `old_score` | number | Previous confidence score |
| `new_score` | number | New confidence score |
| `updated_at` | string | Update timestamp (ISO 8601) |

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success — pattern updated |
| 400 | new_score out of range [0.0, 0.95] |
| 404 | Pattern not found |
| 500 | Server error — file write failed |

### Error Response (404 Not Found)

```json
{
  "error": "Pattern 'pattern_001' not found"
}
```

---

## POST /reset

Restore knowledge base to its startup checkpoint state.

**Escape hatch:** Manual recovery from divergent KB or critical safety issues.

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "reason": "manual_reset",
  "kb": "default"
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reason` | string | No | Reason for reset (logged to audit trail). Defaults to "manual_reset". |
| `kb` | string | No | KB name to reset (e.g., 'default', 'physics'). Omit to use default KB. |

### Response (200 OK)

```json
{
  "status": "restored",
  "reason": "manual_reset",
  "patterns_restored": 157,
  "checkpoint_used": true,
  "checkpoint_created_at": "2026-05-05T10:00:00Z",
  "patterns_before_reset": 203,
  "effects": [
    "Confidence history NOT rolled back (append-only, immutable)",
    "All patterns reverted to startup snapshot",
    "Audit logged to confidence_history.jsonl"
  ],
  "timestamp": "2026-05-05T14:22:15Z"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | 'restored' \| 'failed' \| 'blocked' |
| `reason` | string | Echoed reason for reset |
| `patterns_restored` | integer | Number of unique patterns in restored KB |
| `checkpoint_used` | boolean | True if checkpoint existed and was used |
| `checkpoint_created_at` | string | ISO 8601 timestamp when checkpoint was created |
| `patterns_before_reset` | integer | Pattern count before reset was applied |
| `effects` | array | Side-effect descriptions (e.g., rollback scope) |
| `timestamp` | string | ISO 8601 reset operation timestamp |

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Reset successful |
| 503 | No checkpoint file exists |
| 409 | Reset blocked (quarantine mode active) |
| 500 | Internal error (e.g., file write failed) |

### Examples

**Successful reset:**
```bash
curl -X POST http://localhost:8000/akc/v1/reset \
  -H "Content-Type: application/json" \
  -d '{"reason": "corrupted_patterns"}'
```

**Reset specific KB (multi-KB):**
```bash
curl -X POST http://localhost:8000/akc/v1/reset \
  -H "Content-Type: application/json" \
  -d '{"reason": "rollback", "kb": "physics"}'
```

---

## POST /kb/export-markdown

Export patterns to markdown files organized by entity, tier, or pattern type.

**Use case:** Human-readable audit, GraphRAG integration, external system import.

### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "export_path": "./kb_export",
  "organization": "by-entity",
  "min_confidence": 0.7,
  "include_demoted": false,
  "dry_run": false
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `export_path` | string | No | Output folder path. Defaults to AKC_SERVICE_KB_EXPORT_DIR or `./kb_export`. |
| `organization` | string | No | Organization strategy: 'by-entity', 'by-tier', or 'by-pattern-type'. Default: 'by-entity'. |
| `min_confidence` | float | No | Minimum confidence threshold [0.0, 1.0]. Default: 0.0 (export all). |
| `include_demoted` | boolean | No | Include demoted patterns (confidence < 0.50). Default: false. |
| `dry_run` | boolean | No | Validate without writing files. Default: false. |

### Response (200 OK)

```json
{
  "success": true,
  "patterns_exported": 157,
  "folder": "/home/user/kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z",
  "error": null
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Export operation success |
| `patterns_exported` | integer | Number of patterns exported |
| `folder` | string | Absolute export folder path |
| `organization` | string | Organization strategy used |
| `exported_at` | string | ISO 8601 export timestamp |
| `error` | string | Error message if export failed |

### Organization Strategies

**by-entity** (default):
```
kb_export/
  by-entity/
    player/
      HealthComponent.md
      MovementComponent.md
    enemy_knight/
      HealthComponent.md
    index.md
```

**by-tier:**
```
kb_export/
  by-tier/
    gold/
      player_HealthComponent.md
    production/
      player_MovementComponent.md
    experimental/
      enemy_knight_HealthComponent.md
    index.md
```

**by-pattern-type:**
```
kb_export/
  by-pattern-type/
    implementation/
      player_HealthComponent.md
    design/
      enemy_knight_HealthComponent.md
    index.md
```

### Status Codes

| Code | Meaning |
|------|---------|
| 200 | Export successful |
| 400 | Invalid request (e.g., bad confidence threshold) |
| 500 | Internal error (e.g., write permission denied) |

### Examples

**Export all patterns by entity:**
```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{"organization": "by-entity"}'
```

**Export high-confidence patterns by tier:**
```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-tier",
    "min_confidence": 0.75,
    "export_path": "./production_export"
  }'
```

**Dry run validation:**
```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

---

## Request Validation Rules

All endpoints validate input using Pydantic schemas.

### Common Validation Rules

1. **String fields**: Must be non-empty, UTF-8 encoded
2. **Confidence scores**: Must be in [0.0, 1.0]
3. **ISO 8601 timestamps**: Required format `YYYY-MM-DDTHH:MM:SSZ`
4. **JSON structure**: Must parse as valid JSON

### Example: Invalid Request

```bash
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "task-042"
    # Missing required fields: entity, component
  }'
```

**Response (400 Bad Request):**
```json
{
  "error": "entity and component are required"
}
```

---

## Error Handling

All endpoints use consistent error response format:

```json
{
  "error": "<error message>"
}
```

### HTTP Status Codes

| Code | When | Recovery |
|------|------|----------|
| 200 | Success | None needed |
| 202 | Accepted (async) | None needed |
| 400 | Bad request (validation) | Fix request and retry |
| 404 | Resource not found | Check IDs and retry |
| 500 | Server error | Retry with exponential backoff |

### Retry Strategy

- **4xx errors**: Do not retry (fix request first)
- **5xx errors**: Retry with exponential backoff (50ms → 100ms → 200ms → 400ms)
- **Network timeouts**: Treat as 5xx and retry

---

## Rate Limiting

**Current:** No rate limiting (Phase 2 feature).

Recommended defaults for production:
- 1000 req/s per client IP
- 100 req/s per pattern ID
- 10 req/s per entity:component pair

---

## Timeout Behavior

All endpoints have a 50ms latency budget (p95 target).

- Query: < 50ms (typical: 10-20ms)
- Record: < 10ms (returns 202 before DB write)
- Stats: < 100ms
- Update: < 100ms
- Health: < 5ms

**If timeout occurs:**
1. Check service logs: `tail -f /tmp/akc-service.log`
2. Check disk I/O: `iostat 1 5`
3. Check memory: `ps aux | grep uvicorn`
4. Restart service if degraded

---

## Security

### CORS Configuration

Enabled for localhost development:
```
allow_origins=["http://localhost", "http://127.0.0.1", ...]
allow_credentials=True
allow_methods=["*"]
allow_headers=["*"]
```

For production, restrict origins:
```python
allow_origins=["https://api.example.com"]
```

### Authentication

**Current:** None (Phase 2 feature).

Recommended for production:
- API key header: `X-API-Key: <token>`
- JWT bearer token
- mTLS for service-to-service

---

## Examples

### Python Client (with akc_http_client)

```python
from agent_system.akc_http_client import AKCClient

client = AKCClient(base_url="http://localhost:8000", timeout_sec=0.15)

# Query patterns
patterns = client.query_patterns(
    task_id="task-042",
    entity="player",
    component="HealthComponent",
    context={"difficulty": "hard"}
)
print(f"Found {len(patterns)} patterns")
for p in patterns:
    print(f"  {p['id']}: confidence={p['confidence']:.2f}, tier={p['tier']}")

# Record outcome
result = client.record_outcome(
    task_id="task-042",
    status="success",
    patterns_active=[
        {"id": p["id"], "confidence": p["confidence"]}
        for p in patterns
    ]
)
print(f"Record accepted: {result['accepted']}")

# Get stats
stats = client.get_stats(time_window="24h")
print(f"Avg latency: {stats['latency_stats']['avg_ms']:.1f}ms")
print(f"SLA status: {stats['sla_status']}")
```

### Bash Script

```bash
#!/bin/bash
# Query patterns and record outcome

BASE_URL="http://localhost:8000/akc/v1"
ENTITY="player"
COMPONENT="HealthComponent"

# Query patterns
PATTERNS=$(curl -s -X POST "$BASE_URL/query" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "task-042",
    "entity": "'$ENTITY'",
    "component": "'$COMPONENT'"
  }')

echo "Patterns: $PATTERNS"

# Record success
curl -s -X POST "$BASE_URL/record" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "task_id": "task-042",
    "status": "success",
    "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'",
    "akc_context": {
      "knowledge_patterns_active": []
    }
  }'

echo "Recorded"
```

---

## Sync Endpoints

These endpoints are only meaningful when `AKC_SERVICE_REMOTE_URL` is configured. Push and pull return HTTP 503 when sync is disabled.

---

### GET /sync/status

Returns current sync state: remote URL, reachability, push queue size, and last sync timestamps.

#### Response

```json
{
  "remote_url": "https://remote.example.com/akc",
  "connected": true,
  "remote_reachable": true,
  "last_push_at": "2026-05-05T12:00:00Z",
  "last_pull_at": "2026-05-05T11:00:00Z",
  "push_queue_size": 3
}
```

---

### GET /sync/export

Export local patterns for consumption by a remote pull. Optionally filter by `since` (ISO 8601 timestamp).

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `since` | string | No | ISO 8601 timestamp — only return patterns updated after this time |

#### Response

```json
{
  "patterns": [...],
  "count": 42,
  "as_of": "2026-05-05T14:00:00Z"
}
```

---

### POST /sync/push

Push locally-queued patterns (confidence ≥ `min_confidence`) to the remote KB in batches.

Returns HTTP 503 when `AKC_SERVICE_REMOTE_URL` is not set.

#### Request Body

```json
{
  "min_confidence": 0.70,
  "batch_size": 50,
  "dry_run": false
}
```

All fields are optional (defaults shown).

#### Response

```json
{
  "pushed": 12,
  "skipped": 3,
  "errors": 0,
  "cursor": "2026-05-05T12:00:00Z"
}
```

| Field | Description |
|-------|-------------|
| `pushed` | Patterns successfully sent |
| `skipped` | Patterns below `min_confidence` threshold |
| `errors` | Batches that failed (network errors, non-200 responses) |
| `cursor` | `updated_at` of the latest pushed pattern |
| `would_push` | (dry_run only) Count of patterns that would be sent |

---

### POST /sync/pull

Pull patterns from the remote KB into the local KB.

Returns HTTP 503 when `AKC_SERVICE_REMOTE_URL` is not set.

#### Request Body

```json
{
  "since": null,
  "overwrite_local": false,
  "dry_run": false
}
```

#### Response

```json
{
  "pulled": 8,
  "conflicts": 2,
  "errors": 0
}
```

| Field | Description |
|-------|-------------|
| `pulled` | Patterns written to local KB |
| `conflicts` | Patterns where local was kept (local confidence ≥ remote) |
| `errors` | HTTP errors during the pull |

---

### POST /sync/receive

Inbound endpoint — accepts a batch of patterns pushed from a remote node. Called automatically by the remote's `/sync/push`; not normally called directly.

#### Request Body

```json
{
  "patterns": [...],
  "pushed_at": "2026-05-05T12:00:00Z"
}
```

#### Response

```json
{
  "accepted": 12,
  "total": 12
}
```

---

## Related Documentation

- [CAPABILITIES.md](CAPABILITIES.md) — Component overview and architecture
- [CONFIGURATION.md](CONFIGURATION.md) — Environment setup and tuning
- [INTEGRATION.md](INTEGRATION.md) — Godot adapter and usage
- [ERROR_HANDLING.md](ERROR_HANDLING.md) — Failure modes and recovery
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues and solutions
