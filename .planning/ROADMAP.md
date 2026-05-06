# Roadmap: AKC Service

## Milestones

- ✅ **v0.5 Multi-KB Routing** — Phases 1-5 (shipped 2026-05-06)

## Phases

### Phase 6: reset_kb Hardening

**Goal:** Harden the `reset_kb()` escape hatch endpoint based on multi-agent code review findings. Fix one CRITICAL correctness bug (no kb param in multi-KB deployments), three IMPORTANT UX/safety gaps (lying effects array, missing checkpoint age, stale safety state), and one response quality improvement (pre-reset pattern count).

**Milestone:** v0.6  
**Depends on:** Phase 5

**Requirements:**
- REQ-06-01: Add `kb: Optional[str]` to `ResetRequest`; wire `resolve_kb_dir()` and pass `kb_dir` to all downstream calls (restore, load_patterns, safety state)
- REQ-06-02: Track `audit_ok` flag; make `effects[1]` conditional — warn if audit write fails
- REQ-06-03: Add `checkpoint_created_at` (ISO 8601) to `ResetResponse` using `CHECKPOINT_PATH.stat().st_mtime`
- REQ-06-04: Move `_set_escape_hatch("reset")` call to immediately after `restore_from_checkpoint()` returns `True`, before pattern count
- REQ-06-05: Capture pre-reset pattern count before restore; add `patterns_before_reset: int` to `ResetResponse`

**Plans:** 3 plans

Plans:
- [ ] 06-01-PLAN.md — Wave 1 (haiku): audit_ok flag (REQ-06-02) + _set_escape_hatch reorder (REQ-06-04)
- [ ] 06-02-PLAN.md — Wave 1 (haiku): ResetResponse new fields checkpoint_created_at + patterns_before_reset (REQ-06-03, REQ-06-05)
- [ ] 06-03-PLAN.md — Wave 2 (sonnet): kb param structural change + set_escape_hatch kb_dir + tests (REQ-06-01)

---

<details>
<summary>✅ v0.5 Multi-KB Routing (Phases 1-5) — SHIPPED 2026-05-06</summary>

- [x] Phase 1: Config & Resolution (2/2 plans) — completed 2026-05-06
- [x] Phase 2: Module Refactoring (2/2 plans) — completed 2026-05-06
- [x] Phase 3: API Integration (2/2 plans) — completed 2026-05-06
- [x] Phase 4: Entity Inference (2/2 plans) — completed 2026-05-06
- [x] Phase 5: Testing & Documentation (2/2 plans) — completed 2026-05-06

See `.planning/milestones/v0.5-ROADMAP.md` for full details.

</details>

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|---|--------|-----------|
| 1. Config & Resolution | v0.5 | 2/2 | Complete | 2026-05-06 |
| 2. Module Refactoring | v0.5 | 2/2 | Complete | 2026-05-06 |
| 3. API Integration | v0.5 | 2/2 | Complete | 2026-05-06 |
| 4. Entity Inference | v0.5 | 2/2 | Complete | 2026-05-06 |
| 5. Testing & Documentation | v0.5 | 2/2 | Complete | 2026-05-06 |
| 6. reset_kb Hardening | v0.6 | 0/3 | Planned | — |

---

*Last updated: 2026-05-06 after v0.6 phase 6 planning*
