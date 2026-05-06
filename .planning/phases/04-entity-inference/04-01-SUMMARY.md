---
plan_id: 04-01
phase: 04-entity-inference
status: complete
verification: all_must_haves_met
---

# Plan 04-01 Summary: Enable Entity Inference in Route Handlers

## One-liner
Replaced Slice 1 entity=None placeholders with actual entity extraction, enabling ENTITY_KB_MAPPING Tier 2 routing in /query, /record, /fix handlers.

## Objectives Met

✓ Added `entity: Optional[str]` field to FixRequest in models.py
✓ Extracted entity sources per handler:
  - /query → QueryRequest.entity (already existed)
  - /record → akc_context.get("entity") or first pattern entity
  - /fix → FixRequest.entity field
  - /stats → entity=None (no context)
✓ All four `entity=None  # Slice 1` comments removed from routes.py
✓ resolve_kb_dir receives actual entity values
✓ routing_tier returns "entity_mapping", "entity_wildcard", or "fallback" based on ENTITY_KB_MAPPING

## Requirements Delivered

- INF-01: /query handler passes request.entity to resolve_kb_dir ✓
- INF-02: /record handler extracts entity from akc_context ✓
- INF-03: /fix handler accepts optional entity field ✓
- INF-04: routing_tier returns "entity_mapping" for exact match ✓
- INF-05: routing_tier returns "entity_wildcard" for wildcard match ✓
- INF-06: routing_tier returns "fallback" for no mapping ✓
- INF-07: All 4 Slice 1 comments replaced ✓

## Files Modified

- akc_service/api/models.py (FixRequest + entity field)
- akc_service/api/routes.py (entity extraction logic, 4 handler updates)

## Key Commits

- ee25829: feat(04-01): enable entity inference in route handlers — replace Slice 1 entity=None with Tier 2 routing

## Testing Status

All 13 entity inference tests pass (verified via test_entity_inference.py).
