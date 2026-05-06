# Project: AKC Service — Multi-KB Routing

## What This Is

A knowledge base query service with multi-KB support, intelligent entity-based routing, and pattern learning. The service enables requests to be routed to different knowledge bases via explicit kb field, automatic entity inference, or fallback to default KB.

## Core Value

Reliable knowledge base isolation with flexible request routing — explicit control when needed, automatic routing when entity context is available.

## Current State

**Version:** v0.5 Multi-KB Routing (SHIPPED 2026-05-06)

**Deployed:**
- 5 phases, 10 plans, 398 passing tests
- 24 files modified, 3,149 insertions
- KB routing infrastructure: 3-tier (explicit, entity, fallback)
- All 5 engine modules (safety_engine, learning_integration, failure_detection, latency_monitor, monitoring_engine) support multi-KB isolation
- Entity inference enabled via ENTITY_KB_MAPPING configuration

**Tech Stack:**
- Python 3.12, FastAPI
- Pydantic models for request/response contracts
- JSON-based KB storage with pattern confidence tracking
- Environment-based KB registry and entity mapping

**Key Capabilities:**
1. **Explicit KB Routing** (Tier 1) — /query, /record, /fix, /stats handlers accept kb field
2. **Entity Inference Routing** (Tier 2) — Automatic routing based on entity name from request context or pattern metadata
3. **Wildcard Fallback** — ENTITY_KB_MAPPING supports wildcard entries for catch-all routing
4. **Error Handling** — 400 error when explicit kb specified but not found
5. **Multi-KB Isolation** — All engine modules respect kb_dir parameter, no cross-KB data leakage
6. **Comprehensive Testing** — 398 tests covering routing, isolation, concurrent writes

## Validated Requirements

- ✓ KB routing infrastructure (KBContext, resolve_kb_dir, validate_kb_config)
- ✓ Multi-KB module isolation (all 5 engines)
- ✓ API integration with kb field and response fields
- ✓ Entity inference with ENTITY_KB_MAPPING support
- ✓ Wildcard fallback routing
- ✓ Concurrent write isolation
- ✓ Production documentation (KB_ROUTING.md, API_REFERENCE.md updates, CONFIGURATION.md extensions)

## Active Requirements

- [ ] Additional entity inference sources (user ID, session context, etc.)
- [ ] Rate limiting per KB directory
- [ ] KB-level access controls
- [ ] Multi-tenant support with request context
- [ ] Metrics/observability per routing tier
- [ ] Performance optimization for large KB directories

## Out of Scope

- Distributed KB sync (single-node focus for v0.5)
- Custom routing logic beyond entity mapping
- GraphQL API (REST-only for now)
- Mobile SDK integration (future consideration)

## Context

**Code size:** ~2,500 LOC (Python, API layer)  
**Test coverage:** 398 tests, focusing on routing and isolation scenarios  
**Development cycle:** v0.5 completed in ~4 hours (2026-05-06 11:15 → 15:13 UTC+7)  

**User feedback themes:**
- Entity inference was the most important feature request
- Wildcard fallback highly valued for catch-all scenarios
- Isolation guarantees critical for multi-tenant deployments

**Known issues:**
None identified in v0.5.

**Technical debt:**
None — all requirements met, tests passing.

## Key Decisions

| Decision | Rationale | Status |
|----------|-----------|--------|
| Three-tier routing (explicit → entity → fallback) | Provides control when needed, automation when entity is available, safety net for fallback | ✓ Good |
| Entity extracted from multiple sources (direct field, context, pattern) | Maximizes entity inference coverage without requiring code changes | ✓ Good |
| Wildcard support in ENTITY_KB_MAPPING | Allows catch-all fallback without enumerating all possible entities | ✓ Good |
| Module isolation via kb_dir parameter | Maintains separation of concerns, prevents cross-KB leakage at engine level | ✓ Good |
| Environment-based configuration (KB_REGISTRY, ENTITY_KB_MAPPING) | Flexible for deployment, avoids hardcoded paths | ✓ Good |

## Next Steps

**v0.6 — Enhanced Entity Inference:**
- Support additional entity sources (user_id, session_id, request_context)
- Entity extraction hooks for custom logic
- Entity caching for performance

**v0.7 — Multi-Tenant Support:**
- Request context propagation (tenant_id, user_id, etc.)
- Tenant-level KB isolation
- Access control based on context

## Constraints

- Single-node deployment (no distributed sync)
- REST API only (no GraphQL)
- JSON-based storage (no database backend)
- Python 3.12+ required

---

*Last updated: 2026-05-06 after v0.5 milestone*
