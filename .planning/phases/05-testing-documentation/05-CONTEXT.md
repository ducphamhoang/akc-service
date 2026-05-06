# Phase 5: Testing & Documentation — Context

**Gathered:** 2026-05-06
**Status:** Ready for planning
**Source:** MULTI_KB_ROUTING_SPEC.md + Phase 5 gap analysis

<domain>
## Phase Boundary

Phase 5 completes the Multi-KB Routing v0.5 milestone with two deliverables:
1. **Concurrent write test** — verify two simultaneous requests to different KBs do not corrupt each other's patterns.jsonl
2. **Documentation updates** — API_REFERENCE.md (kb_used/routing_tier examples), KB_ROUTING.md guide (new file), CONFIGURATION.md (new env vars)

This phase does NOT add new routing features. It closes test and doc gaps identified after Phase 4 completion.

</domain>

<decisions>
## Implementation Decisions

### Test: Concurrent Write Isolation
- Test two simultaneous /record requests targeting different KB directories
- Use threading.Thread or asyncio to fire requests concurrently
- Verify patterns.jsonl for KB-A contains only KB-A writes, KB-B only KB-B writes
- Test lives in tests/test_concurrent_kb_writes.py
- Use the TestClient from fastapi.testclient (synchronous) with threads, or httpx async client
- Two temp KB dirs (tmp_path fixture), each pre-populated with empty patterns.jsonl and confidence_history.jsonl
- Assert no cross-contamination: KB-A's patterns.jsonl line count matches KB-A write count, same for KB-B

### Documentation: API_REFERENCE.md
- Add `kb` request field to /query, /record, /fix request body examples
- Add `kb_used` and `routing_tier` to response examples for /query, /record, /fix, /stats
- Add routing_tier value table: "explicit_kb", "entity_mapping", "entity_wildcard", "fallback"
- Add /stats endpoint ?kb= query param documentation
- Location: docs/API_REFERENCE.md (update existing file)

### Documentation: KB_ROUTING.md (new file)
- New file at docs/KB_ROUTING.md
- Sections: Overview, Configuration (env vars), Routing Tiers (Tier 1/2/3 with examples), Request Examples, Stats Per-KB, Troubleshooting
- Include concrete curl examples for each tier
- Include JSON env var format for AKC_SERVICE_KB_REGISTRY and AKC_SERVICE_ENTITY_KB_MAPPING
- Cross-reference CONFIGURATION.md for env var details

### Documentation: CONFIGURATION.md
- Add AKC_SERVICE_KB_REGISTRY section (JSON env var, format, example)
- Add AKC_SERVICE_ENTITY_KB_MAPPING section (JSON env var, format, wildcard syntax, example)
- Add AKC_SERVICE_SAFETY_LEVEL note that it is global (not per-KB)
- Location: docs/CONFIGURATION.md (update existing file)

### Claude's Discretion
- Test file structure (class-based vs function-based — follow existing test patterns: function-based with pytest fixtures)
- Number of concurrent threads/tasks in the concurrent write test (2 is sufficient)
- How many patterns per write (1-2 patterns per request is sufficient to verify isolation)
- Whether to add a smoke test for all 3 routing tiers in a single e2e test (optional, existing coverage may be sufficient)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Routing Spec
- `MULTI_KB_ROUTING_SPEC.md` — Full multi-KB routing spec: tiers, env vars, response fields

### Existing Test Patterns
- `tests/test_api_kb_routing.py` — Phase 3 routing tests (pattern for KB-aware API tests)
- `tests/test_entity_inference.py` — Phase 4 entity inference tests (pattern for entity routing)
- `tests/test_kb_file_io.py` — File I/O tests (pattern for tmp_path fixture usage)

### Docs to Update
- `docs/API_REFERENCE.md` — Add kb/kb_used/routing_tier to all endpoint examples
- `docs/CONFIGURATION.md` — Add AKC_SERVICE_KB_REGISTRY and AKC_SERVICE_ENTITY_KB_MAPPING sections

### Source Files (for understanding response shapes)
- `akc_service/api/routes.py` — Route handlers: kb_used/routing_tier in responses
- `akc_service/config.py` — KBContext, resolve_kb_dir, env var names

</canonical_refs>

<specifics>
## Specific Items

### Concurrent Write Test Requirements (from MULTI_KB_ROUTING_SPEC.md Phase 5)
- Verify patterns.jsonl for KB-A and KB-B remain isolated after concurrent writes
- This closes the only remaining technical gap from Phase 5 spec

### Routing Tier Values (for docs)
- `"explicit_kb"` — kb field present in request (Tier 1)
- `"entity_mapping"` — exact match in ENTITY_KB_MAPPING (Tier 2)
- `"entity_wildcard"` — wildcard `entity:*` match in ENTITY_KB_MAPPING (Tier 2)
- `"fallback"` — no kb field, no entity match (Tier 3, uses AKC_SERVICE_KB_DIR default)

### Env Var Formats (for docs)
```bash
# Registry: named KBs with paths
export AKC_SERVICE_KB_REGISTRY='{"physics": "/var/kb/physics", "ui": "/var/kb/ui"}'

# Entity mapping: entity prefix → KB name
export AKC_SERVICE_ENTITY_KB_MAPPING='{"entity:physics": "physics", "entity:ui": "ui", "entity:*": "default"}'
```

</specifics>

<deferred>
## Deferred Ideas

- `?kb=all` stats aggregation (explicitly deferred in MULTI_KB_ROUTING_SPEC.md)
- Per-KB confidence decay (out of scope for v0.5)
- KB migration tooling

</deferred>

---

*Phase: 05-testing-documentation*
*Context gathered: 2026-05-06*
