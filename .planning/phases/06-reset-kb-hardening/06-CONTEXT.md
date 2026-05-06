# Phase 6: reset_kb Hardening — Context

**Gathered:** 2026-05-06
**Status:** Ready for planning
**Source:** Multi-agent code review (UX agent, Architecture agent, Devil's Advocate agent)

<domain>
## Phase Boundary

Harden the `reset_kb()` escape hatch endpoint in `akc_service/api/routes.py` (lines 711–810).
This endpoint is the operator recovery path — called during incidents to restore `patterns.jsonl`
from startup checkpoint. The fixes do not change the endpoint's behavior contract; they fix
a correctness bug, a misleading response, and missing operator signal.

**In scope:** `reset_kb()` function, `ResetRequest` model, `ResetResponse` model, associated tests.
**Out of scope:** `restore_from_checkpoint()` internals, `set_escape_hatch()` internals, circular import structure.

</domain>

<decisions>
## Implementation Decisions

### REQ-06-01 — kb param (CRITICAL, Wave 2, sonnet)
- Add `kb: Optional[str] = None` to `ResetRequest` (models.py)
- At top of `reset_kb()`, call `resolve_kb_dir(kb_override=request.kb, ...)` → `kb_context`
- Pass `kb_dir=kb_context.path` to: `restore_from_checkpoint()`, `load_all_patterns()`, `_load_safety_state()`, `_set_escape_hatch()`
- Use `kb_context.path / "patterns.checkpoint"` for checkpoint existence check (not module-level `CHECKPOINT_PATH`)
- Note: `set_escape_hatch` in `safety_engine.py` may not accept `kb_dir` — investigate and extend if needed

### REQ-06-02 — audit_ok flag (IMPORTANT, Wave 1, haiku)
- Add `audit_ok = True` before the `try: _set_escape_hatch(...)` block
- Set `audit_ok = False` in the `except` branch
- Change `effects[1]` from hardcoded `"Audit trail preserved in confidence_history.jsonl"` to:
  - `"Audit trail updated in safety_state.json"` if `audit_ok`
  - `"WARNING: audit trail write failed — check server logs"` if not `audit_ok`
- No other changes

### REQ-06-03 — checkpoint_created_at (IMPORTANT, Wave 1, haiku)
- Add `checkpoint_created_at: str` field to `ResetResponse` model
- Compute value just before returning: `datetime.fromtimestamp(CHECKPOINT_PATH.stat().st_mtime, tz=timezone.utc).isoformat()`
- Pass it in the `ResetResponse(...)` constructor
- For REQ-06-01 path: use `kb_checkpoint_path.stat().st_mtime`

### REQ-06-04 — audit write reorder (IMPORTANT, Wave 1, haiku)
- Move the `try: _set_escape_hatch("reset", reason=request.reason)` block to immediately after
  `if not success: raise HTTPException(...)` — before the `load_all_patterns()` call
- Pattern count verification is observational and should not gate the audit write
- The audit write records outcome, not intent — it still correctly goes after `restore_from_checkpoint()` returns True

### REQ-06-05 — patterns_before_reset (IMPORTANT, Wave 1, haiku)
- Before calling `restore_from_checkpoint()`, call `load_all_patterns()` (with kb_dir if REQ-06-01 is in play)
- Count unique pattern IDs: `before_count = len({p["id"]: p for p in load_all_patterns() if p.get("id")})`
- Add `patterns_before_reset: int` to `ResetResponse` model
- Pass `patterns_before_reset=before_count` in the constructor

### Claude's Discretion
- Test structure: add tests for the new response fields and the audit failure path; mirror existing test patterns in `tests/test_checkpoint_reset.py`
- Devil's advocate conceded the lying effects string (REQ-06-02) is a real bug; the rest of the "no rollback" and "deferred imports" criticisms are NOT being addressed in this phase
- The `checkpoint_used: bool` field (always True) is noted as redundant but NOT removed to avoid breaking changes

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core files being modified
- `akc_service/api/routes.py` — `reset_kb()` at line 711, `CHECKPOINT_PATH` import, `ResetRequest`/`ResetResponse` imports
- `akc_service/models.py` — `ResetRequest` model (lines ~177-182), `ResetResponse` model
- `akc_service/safety_engine.py` — `set_escape_hatch()`, `load_safety_state()` — check if they accept `kb_dir`

### Pattern references
- `akc_service/api/routes.py` — `resolve_kb_dir()` usage in `/query`, `/record`, `/fix` handlers — exact calling pattern to replicate for REQ-06-01
- `tests/test_checkpoint_reset.py` — existing reset tests; new tests must follow this structure
- `akc_service/learning_integration.py` — `restore_from_checkpoint()` signature — check if it accepts `kb_dir`

</canonical_refs>

<specifics>
## Specific Implementation Notes

**Wave 1 tasks are independent** — REQ-06-02, 03, 04, 05 touch different lines of the same
function and a shared model. They can be dispatched to parallel haiku agents as long as
each agent reads the current state of both `routes.py` and `models.py` before editing.

**Wave 2 (REQ-06-01) depends on Wave 1** — the kb param plumbing is the most structurally
invasive change. It's safer to apply it after the Wave 1 fixes have been committed, so the
diff is cleaner and conflicts are avoided.

**Existing test count:** 393 passing tests. No regressions acceptable.

**The audit write in REQ-06-04 reorder:** the new sequence is:
1. Quarantine guard
2. Checkpoint exists check
3. `restore_from_checkpoint()` ← KB restored
4. `_set_escape_hatch("reset", reason=...)` ← audit write (moved up)
5. `load_all_patterns()` ← count (moved down)
6. Return `ResetResponse`

</specifics>

<deferred>
## Deferred

- Circular import between `learning_integration` ↔ `safety_engine` — acknowledged, not fixing now
- Rollback semantics / two-phase commit — over-engineering for a manual escape hatch
- `checkpoint_used: bool` redundancy — not removing to avoid breaking API changes
- `set_escape_hatch` `kb_dir` gap in `safety_engine.py` — investigate as part of REQ-06-01; if non-trivial, defer to v0.7
</deferred>

---

*Phase: 06-reset-kb-hardening*
*Context gathered: 2026-05-06 via multi-agent review synthesis*
