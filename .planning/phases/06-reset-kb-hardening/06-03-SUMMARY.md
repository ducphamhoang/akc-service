---
phase: 06-reset-kb-hardening
plan: 03
subsystem: api
tags: [fastapi, reset_kb, multi-kb-routing, resolve_kb_dir, safety_engine, tdd]

requires:
  - phase: 06-reset-kb-hardening
    plan: 01
    provides: routes.py with audit_ok flag and reordered _set_escape_hatch in reset_kb()
  - phase: 06-reset-kb-hardening
    plan: 02
    provides: ResetResponse with checkpoint_created_at and patterns_before_reset fields

provides:
  - ResetRequest.kb: Optional[str] field enabling explicit KB targeting in POST /reset
  - reset_kb() resolves kb_dir via resolve_kb_dir() at function entry
  - All downstream calls (load_all_patterns, restore_from_checkpoint, _load_safety_state, _set_escape_hatch) receive kb_dir
  - kb_checkpoint_path replaces module-level CHECKPOINT_PATH inside reset_kb()
  - set_escape_hatch() accepts optional kb_dir parameter passed to load/save/append calls
  - 4 new tests: telemetry fields, pattern count delta, audit failure path, backward compat

affects: [06-reset-kb-hardening]

tech-stack:
  added: []
  patterns:
    - "KB-scoped reset: resolve_kb_dir() called at top of reset_kb(), kb_dir threaded to all downstream calls"
    - "Multi-line function call pattern: resolve_kb_dir() result stored in kb_context, kb_dir extracted from kb_context.path"
    - "Deferred import patch strategy: patch akc_service.safety_engine.set_escape_hatch (not routes-level alias) to test audit failure"

key-files:
  created: []
  modified:
    - akc_service/api/models.py
    - akc_service/safety_engine.py
    - akc_service/api/routes.py
    - tests/test_checkpoint_reset.py

key-decisions:
  - "resolve_kb_dir() placed before the quarantine guard — KB resolution must happen before safety state is read, since safety state is now KB-scoped"
  - "kb_checkpoint_path derived from kb_dir, not KB_REGISTRY lookup — single source of truth at top of function"
  - "Existing TestResetEndpoint tests fixed to use AKC_SERVICE_KB_REGISTRY instead of AKC_SERVICE_KB_DIR — config.py must also be reloaded for resolve_kb_dir() to pick up the temp path"
  - "Audit failure test patches akc_service.safety_engine.set_escape_hatch directly — the deferred import in routes.py binds at call time"

patterns-established:
  - "KB-scoped function pattern: resolve at top, derive kb_dir and kb_checkpoint_path, thread to all downstream"
  - "Config reload pattern for tests: reload config before learning_integration and safety_engine to propagate env var changes through KB_REGISTRY"

requirements-completed:
  - REQ-06-01

duration: 20min
completed: 2026-05-06
---

# Phase 06 Plan 03: Reset KB Hardening — Multi-KB Routing for reset_kb() Summary

**ResetRequest.kb field wires resolve_kb_dir() through all downstream reset_kb() calls, fixing the critical bug where POST /reset always reset the default KB regardless of operator intent**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-06T10:00:00Z
- **Completed:** 2026-05-06T10:20:00Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Added `kb: Optional[str] = None` to `ResetRequest`, enabling explicit KB targeting in POST /reset
- Wired `resolve_kb_dir(kb_override=request.kb, ...)` at the top of `reset_kb()` — KB resolution now happens before any KB-specific operations
- Replaced module-level `CHECKPOINT_PATH` references inside `reset_kb()` with `kb_checkpoint_path = kb_dir / "patterns.checkpoint"` derived from the resolved KB
- Passed `kb_dir` to all four downstream calls: `_load_safety_state`, `load_all_patterns` (x2), `restore_from_checkpoint`, and `_set_escape_hatch`
- Extended `set_escape_hatch()` signature with `kb_dir: Optional[Path] = None`, passing it to `load_safety_state`, `save_safety_state`, and `append_confidence_history`
- Added 4 new tests covering telemetry fields, pattern count delta, audit failure warning, and backward compat
- Fixed existing `TestResetEndpoint` tests that broke because they set `AKC_SERVICE_KB_DIR` but not `AKC_SERVICE_KB_REGISTRY` — `resolve_kb_dir()` reads from KB_REGISTRY

## Task Commits

1. **Task 1: Add kb field to ResetRequest and extend set_escape_hatch with kb_dir** - `d2cfa9f` (feat)
2. **Task 2: Wire resolve_kb_dir() through reset_kb() and update all downstream calls** - `d46a40e` (feat)
3. **Task 3: Add tests for kb param routing and audit failure path** - `64f7dc7` (feat)

## Files Created/Modified

- `akc_service/api/models.py` — ResetRequest: added `kb: Optional[str] = None` field
- `akc_service/safety_engine.py` — set_escape_hatch(): added `kb_dir` parameter, passed to load/save/append calls
- `akc_service/api/routes.py` — reset_kb(): resolve_kb_dir() at top, kb_checkpoint_path replaces CHECKPOINT_PATH, kb_dir threaded to all downstream
- `tests/test_checkpoint_reset.py` — 4 new tests; existing TestResetEndpoint tests fixed to reload config module

## Decisions Made

- `resolve_kb_dir()` placed before the quarantine guard: safety state is KB-scoped, so KB must be resolved before reading safety state
- `kb_checkpoint_path` derived from `kb_dir / "patterns.checkpoint"` rather than another registry lookup — one resolution at the top, all usages derive from it
- Both `AKC_SERVICE_KB_DIR` and `AKC_SERVICE_KB_REGISTRY` must be set in tests, with `config.py` reloaded first — `KB_REGISTRY` is a module-level dict in config, not re-read on each call

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed existing TestResetEndpoint tests broken by resolve_kb_dir() change**
- **Found during:** Task 3 (TDD tests) — test_reset_returns_restored_when_checkpoint_exists failed with 503
- **Issue:** Existing tests set `AKC_SERVICE_KB_DIR` and reloaded li/se/r, but not config.py. After Task 2 changes, `resolve_kb_dir()` resolves through `KB_REGISTRY` in config.py which was not reloaded, so it still pointed to the package's default kb/ directory instead of tmp_path
- **Fix:** Added `_reload_all_for_kb()` helper that sets both `AKC_SERVICE_KB_DIR` and `AKC_SERVICE_KB_REGISTRY`, reloads config first, then li/se/r. Updated all 4 existing TestResetEndpoint tests to use the helper
- **Files modified:** tests/test_checkpoint_reset.py
- **Verification:** All 17 tests pass, 397 total passing (no regressions)
- **Committed in:** 64f7dc7 (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Necessary correctness fix — the existing tests were testing the wrong code path after the multi-KB routing change. No scope creep.

## Issues Encountered

- The plan's `grep -c "resolve_kb_dir.*kb_override=request.kb"` verification pattern returns 0 because the call spans multiple lines in the actual code (the pattern expects them on one line). The implementation is functionally correct — confirmed by import check and all tests passing.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - all fields are fully wired with live data sources.

## Threat Flags

None — T-06-03-01 (path traversal) mitigated by existing resolve_kb_dir() allowlist validation; T-06-03-02 (spoofing) mitigated by kb_context.name logging; T-06-03-03 and T-06-03-04 accepted per plan threat model.

## Self-Check: PASSED

- `d2cfa9f` — feat(06-03): add kb field to ResetRequest, extend set_escape_hatch with kb_dir (FOUND)
- `d46a40e` — feat(06-03): wire resolve_kb_dir() through reset_kb() with kb_dir to all downstream calls (FOUND)
- `64f7dc7` — feat(06-03): add tests for kb param routing, telemetry fields, and audit failure path (FOUND)
- All 4 new test functions present in tests/test_checkpoint_reset.py (FOUND)
- 397 tests passing, 0 failed (VERIFIED)

## Next Phase Readiness

- reset_kb() now fully KB-scoped: explicit kb param routes to correct KB directory
- set_escape_hatch() is KB-aware, safety state written to the correct KB
- All Wave 1 and Wave 2 plans for phase 06 complete
- Service ready for multi-KB reset operations in production deployments

---
*Phase: 06-reset-kb-hardening*
*Completed: 2026-05-06*
