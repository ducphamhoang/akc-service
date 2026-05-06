# KB Routing Guide

Multi-KB Routing lets you route different requests to different knowledge base directories
based on explicit request fields or entity name inference.

See [CONFIGURATION.md](CONFIGURATION.md) for environment variable reference.

## Overview

akc-service supports multiple named knowledge bases. Each KB is an isolated directory
containing its own `patterns.jsonl` and `confidence_history.jsonl`. The routing tier
determines which KB handles a given request.

All responses include `kb_used` (KB name) and `routing_tier` (how it was selected).

## Configuration

Two environment variables control KB routing:

### AKC_SERVICE_KB_REGISTRY

Defines named KB directories. Value is a JSON object mapping KB names to filesystem paths.

```bash
export AKC_SERVICE_KB_REGISTRY='{"default": "/var/kb/default", "physics": "/var/kb/physics", "ui": "/var/kb/ui"}'
```

When not set, only the `default` KB is available (package-internal `kb/` directory).

### AKC_SERVICE_ENTITY_KB_MAPPING

Maps entity keys to KB names. Keys use `entity:<name>` prefix. The `entity:*` wildcard
matches any entity not covered by an exact key.

```bash
export AKC_SERVICE_ENTITY_KB_MAPPING='{"entity:physics": "physics", "entity:ui": "ui", "entity:*": "default"}'
```

When not set, all entities fall back to the `default` KB.

## Routing Tiers

Routing resolves in priority order. The first matching tier wins.

### Tier 1 — Explicit KB (routing_tier: "explicit")

The request body includes a `kb` field naming a registered KB. Highest priority.

```bash
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "t1",
    "entity": "player",
    "component": "PhysicsComponent",
    "kb": "physics"
  }'
# Response: {"kb_used": "physics", "routing_tier": "explicit", ...}
```

### Tier 2a — Entity Mapping (routing_tier: "entity_mapping")

No `kb` field. Entity name matches an exact key in `ENTITY_KB_MAPPING`.

```bash
# With ENTITY_KB_MAPPING = {"entity:physics": "physics", "entity:*": "default"}
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "t1",
    "entity": "physics",
    "component": "PhysicsComponent"
  }'
# Response: {"kb_used": "physics", "routing_tier": "entity_mapping", ...}
```

### Tier 2b — Entity Wildcard (routing_tier: "entity_wildcard")

No `kb` field. Entity name does not match any exact key but `entity:*` is defined.

```bash
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "t1",
    "entity": "player",
    "component": "HealthComponent"
  }'
# Response: {"kb_used": "default", "routing_tier": "entity_wildcard", ...}
```

### Tier 3 — Fallback (routing_tier: "fallback")

No `kb` field and no entity provided (or entity is null). Routes to the `default` KB.

```bash
curl -X POST http://localhost:8000/akc/v1/fix \
  -H "Content-Type: application/json" \
  -d '{"category": "detection"}'
# Response: {"kb_used": "default", "routing_tier": "fallback", ...}
```

## Request Examples

### /record to a named KB

```bash
curl -X POST http://localhost:8000/akc/v1/record \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "task_id": "task-101",
    "status": "success",
    "timestamp": "2026-05-06T10:00:00Z",
    "akc_context": {
      "knowledge_patterns_active": [{"id": "pattern_001", "confidence": 0.85}]
    },
    "kb": "physics"
  }'
```

### /fix to a named KB

```bash
curl -X POST http://localhost:8000/akc/v1/fix \
  -H "Content-Type: application/json" \
  -d '{
    "category": "implementation",
    "kb": "ui"
  }'
```

## Stats Per-KB

Use the `?kb=` query parameter to retrieve stats for a specific KB.

```bash
# Stats for the physics KB
curl "http://localhost:8000/akc/v1/stats?kb=physics"

# Stats for the default KB
curl "http://localhost:8000/akc/v1/stats?kb=default"
```

When multiple KBs are registered and `?kb=` is omitted, the service returns **HTTP 400**
with an error message instructing you to add `?kb=<name>`.

## Troubleshooting

### "Unknown KB name" (HTTP 400)

The `kb` field in your request names a KB not in `AKC_SERVICE_KB_REGISTRY`.

Check registered names:
```bash
echo $AKC_SERVICE_KB_REGISTRY | python3 -c "import json,sys; print(list(json.load(sys.stdin).keys()))"
```

### "?kb= parameter required" (HTTP 400 on /stats)

Multiple KBs are registered. Add `?kb=<name>` to your /stats request.

### Patterns written to wrong KB

Verify `AKC_SERVICE_ENTITY_KB_MAPPING` is set correctly and that the `entity` field
in your request body matches an exact key (e.g. `entity:physics`) or the wildcard
`entity:*`. Check the `routing_tier` field in responses to see which tier resolved.

### KB directory does not exist

The service logs a WARNING at startup for missing KB directories:
```
WARNING: KB directory does not exist (will be created on first write): kb_name='physics' path=/var/kb/physics
```
The directory is created automatically on the first write. No manual setup required.
