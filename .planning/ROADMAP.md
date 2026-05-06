# Multi-KB Routing v0.5 — Roadmap

**Milestone:** v0.5 Multi-KB Routing

## Phase 1: Config & Resolution ✓
**Goal:** KBContext, resolve_kb_dir, validate_kb_config, ENTITY_KB_MAPPING parsing
**Status:** Complete

## Phase 2: Module Refactoring ✓
**Goal:** kb_dir isolation in all 5 engine modules (safety_engine, learning_integration, failure_detection, latency_monitor, monitoring_engine)
**Status:** Complete

## Phase 3: API Integration ✓
**Goal:** Wire resolve_kb_dir into all route handlers (query, record, fix, stats); add kb_used/routing_tier to all responses; explicit-kb Slice 1
**Status:** Complete

## Phase 4: Entity Inference
**Goal:** Enable ENTITY_KB_MAPPING Tier 2 routing — requests without explicit kb field auto-route based on entity name extracted from request context

**Requirements:**
- INF-01: /query handler passes request.entity to resolve_kb_dir (not None)
- INF-02: /record handler extracts entity from akc_context and passes to resolve_kb_dir
- INF-03: /fix handler accepts optional entity field and passes to resolve_kb_dir
- INF-04: routing_tier returns "entity_mapping" when ENTITY_KB_MAPPING has exact match
- INF-05: routing_tier returns "entity_wildcard" when only wildcard entry matches
- INF-06: routing_tier returns "fallback" when entity is None or no mapping found
- INF-07: All 4 existing entity=None Slice 1 comments are replaced
- INF-08: Tests cover entity→KB routing via ENTITY_KB_MAPPING env var
- INF-09: Tests cover wildcard fallback (entity:* mapping)
- INF-10: Tests cover fallback tier (no ENTITY_KB_MAPPING set)

## Phase 5: Testing & Documentation
**Goal:** End-to-end tests, concurrent write tests, routing docs
**Status:** Not started

**Requirements:**
- TST-01: Concurrent write test covering two simultaneous /record requests to different KB dirs, verifying no cross-contamination of patterns.jsonl
- DOC-01: API_REFERENCE.md updated with kb request field, kb_used/routing_tier response fields, routing_tier value table, /stats ?kb= param
- DOC-02: New docs/KB_ROUTING.md guide with Overview, Configuration, Routing Tiers, Request Examples, Stats Per-KB, Troubleshooting sections
- DOC-03: CONFIGURATION.md updated with AKC_SERVICE_KB_REGISTRY and AKC_SERVICE_ENTITY_KB_MAPPING sections

**Plans:** 2 plans

Plans:
- [x] 05-01-PLAN.md — Concurrent write isolation test (TST-01)
- [x] 05-02-PLAN.md — Documentation updates: API_REFERENCE.md, KB_ROUTING.md, CONFIGURATION.md (DOC-01, DOC-02, DOC-03)
