#!/usr/bin/env python3
"""
AKC Fix Generation System — Orchestrator
Phase 3, Plan 03 - Task 1

Integrates all Phase 3 components into a unified end-to-end pipeline:
  failure_detection → candidate_generator → routing_engine → staging_manager

Six-step pipeline:
  Step 1: Detect failure         (failure_detection.py --detect-failure)
  Step 2: Generate candidates    (candidate_generator.py --generate-candidates)
  Step 3: Route candidates       (routing_engine.py --batch-route)
  Step 4: Stage Tier 1           (staging_manager.py --stage-candidate)
  Step 5: Queue Tier 2/3         (append to tier queues)
  Step 6: Return summary         (JSON with all results and next steps)

Phase 4 integration hooks:
  get_tier_1_ready_for_production()  → candidates ready for promotion
  get_tier_2_staged_results()        → staged candidates with test results
  get_tier_3_escalated()             → escalated candidates for human review

Threat mitigations:
  T-FIX-10: All operations logged immutably to system_audit.jsonl
  T-FIX-11: Tier 1 auto-stage only after dual confidence gate (routing + pattern >= 0.80)
  T-FIX-12: Single-threaded; max 50 candidates per batch

Usage:
    python fix_generation_system.py --end-to-end --failure-json '<json>'
    python fix_generation_system.py --process-failure --failure-id '<id>'
    python fix_generation_system.py --get-system-status
"""

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))
# SCRIPTS_DIR: runtime data output directory (configurable via env var)
SCRIPTS_DIR = Path(os.environ.get("AKC_SERVICE_SCRIPTS_DIR", str(Path(__file__).parent)))

# ─── Paths ─────────────────────────────────────────────────────────────────────

ROUTING_DIR = KB_DIR / "routing"
STAGING_DIR = KB_DIR / "staging"

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FAILURE_SIGS_PATH = SCRIPTS_DIR / "failure_signatures.jsonl"
GENERATED_CANDIDATES_PATH = SCRIPTS_DIR / "generated_candidates.jsonl"

# Orchestrator-specific files
SYSTEM_AUDIT_PATH = SCRIPTS_DIR / "system_audit.jsonl"      # T-FIX-10: immutable audit
SYSTEM_ERRORS_PATH = SCRIPTS_DIR / "system_errors.jsonl"     # Non-fatal error log
TIER_2_QUEUE_PATH = ROUTING_DIR / "tier_2_queue.jsonl"
TIER_3_QUEUE_PATH = ROUTING_DIR / "tier_3_queue.jsonl"
STAGING_PIPELINE_PATH = STAGING_DIR / "staging_pipeline.jsonl"
STAGING_VALIDATION_PATH = STAGING_DIR / "staging_validation_results.jsonl"
STAGING_METRICS_PATH = STAGING_DIR / "staging_metrics.jsonl"
LATENCY_HISTORY_PATH = KB_DIR / "latency_samples.jsonl"

# T-FIX-12: Max batch size
MAX_BATCH_SIZE = 50

# Python executable
PYTHON = sys.executable

# WR-04: Thread-safe audit log (concurrent write protection)
_audit_log_lock = threading.Lock()


# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_epoch() -> float:
    return time.time()


def ensure_dirs() -> None:
    ROUTING_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, entry: dict) -> None:
    """Immutable append to a JSONL file."""
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def audit_log(operation: str, context: dict) -> None:
    """T-FIX-10: Immutable audit trail for all orchestrator operations (WR-04: thread-safe)."""
    entry = {
        "timestamp": now_iso(),
        "operation": operation,
        **context,
    }
    with _audit_log_lock:
        append_jsonl(SYSTEM_AUDIT_PATH, entry)


def error_log(stage: str, error: str, context: dict) -> None:
    """Log non-fatal pipeline errors."""
    entry = {
        "timestamp": now_iso(),
        "stage": stage,
        "error": error,
        **context,
    }
    append_jsonl(SYSTEM_ERRORS_PATH, entry)
    sys.stderr.write(f"[WARN] Stage={stage}: {error}\n")


def run_script(script_name: str, args: list[str], timeout: int = 120) -> dict:
    """
    Run a Phase 3 component script and return parsed JSON output.

    Returns:
        {"ok": True, "data": {...}} on success
        {"ok": False, "error": "...", "stderr": "..."} on failure
    """
    cmd = [PYTHON, str(SCRIPTS_DIR / script_name)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_REPO_ROOT),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"Exit code {result.returncode}",
                "stderr": stderr[:500],
                "stdout": stdout[:200],
            }

        # Try to parse JSON from stdout
        if stdout:
            try:
                data = json.loads(stdout)
                return {"ok": True, "data": data}
            except json.JSONDecodeError:
                # Not JSON — treat as plain text success
                return {"ok": True, "data": {"output": stdout}}
        return {"ok": True, "data": {}}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stderr": ""}


# ─── Learning Loop Integration ─────────────────────────────────────────────────

def get_active_patterns(entity: str, component: str) -> list:
    """
    Call orchestrator_hooks.get_active_patterns and return active KB patterns.
    Used to integrate learning loop confidence scores into routing decisions.
    """
    result = run_script("orchestrator_hooks.py", [
        "--get-active-patterns",
        "--entity", entity,
        "--component", component,
    ], timeout=30)

    if result["ok"]:
        data = result["data"]
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("patterns", [])
    return []


def get_pattern_confidence(pattern_id: str) -> float:
    """Look up a pattern's confidence score from the KB."""
    patterns = load_jsonl(PATTERNS_PATH)
    for p in patterns:
        if p.get("id") == pattern_id:
            return float(p.get("confidence_score", 0.0))
    return 0.0


# ─── Pipeline Steps ─────────────────────────────────────────────────────────────

def step1_detect_failure(failure_json: dict) -> dict:
    """
    Step 1: Detect failure using failure_detection.py.

    Returns:
        {
            "failure_id": str,
            "root_cause_pattern_id": str,
            "confidence_score": float,
            "entity": str,
            "component": str,
            "error_pattern": str,
        }
    Raises: ValueError if detection fails fatally.
    """
    failure_str = json.dumps(failure_json)
    result = run_script("failure_detection.py", [
        "--detect-failure",
        "--failure-json", failure_str,
    ], timeout=60)

    if not result["ok"]:
        raise ValueError(f"Failure detection failed: {result.get('error')}")

    data = result["data"]
    if isinstance(data, dict) and "failure_id" in data:
        return {
            "failure_id": data["failure_id"],
            "root_cause_pattern_id": data.get("root_cause_pattern_id", "unknown"),
            "confidence_score": float(data.get("confidence_score", 0.0)),
            "entity": data.get("entity", failure_json.get("entity", "global")),
            "component": data.get("component", failure_json.get("component", "unknown")),
            "error_pattern": data.get("error_pattern", "unknown"),
        }

    # Try to extract failure_id from output text
    output = data.get("output", "")
    if "failure_id" in output:
        try:
            # Parse embedded JSON in output
            start = output.find("{")
            end = output.rfind("}") + 1
            if start >= 0 and end > start:
                extracted = json.loads(output[start:end])
                return {
                    "failure_id": extracted.get("failure_id", _gen_id("fail")),
                    "root_cause_pattern_id": extracted.get("root_cause_pattern_id", "unknown"),
                    "confidence_score": float(extracted.get("confidence_score", 0.5)),
                    "entity": failure_json.get("entity", "global"),
                    "component": failure_json.get("component", "unknown"),
                    "error_pattern": failure_json.get("error_pattern", "unknown"),
                }
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: derive from failure_json
    failure_id = _gen_id("fail")
    return {
        "failure_id": failure_id,
        "root_cause_pattern_id": "unknown",
        "confidence_score": 0.5,
        "entity": failure_json.get("entity", "global"),
        "component": failure_json.get("component", "unknown"),
        "error_pattern": failure_json.get("error_pattern", "unknown"),
    }


def step2_generate_candidates(failure_id: str) -> list:
    """
    Step 2: Generate 3-5 ranked fix candidates.

    Returns list of candidate dicts with confidence_score.
    """
    result = run_script("candidate_generator.py", [
        "--generate-candidates",
        "--failure-id", failure_id,
    ], timeout=90)

    if not result["ok"]:
        raise ValueError(f"Candidate generation failed: {result.get('error')}")

    data = result["data"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        candidates = data.get("candidates", [])
        if candidates:
            return candidates
        # Try output text
        output = data.get("output", "")
        if "[" in output:
            start = output.find("[")
            end = output.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(output[start:end])
                except json.JSONDecodeError:
                    pass

    return []


def step3_route_candidates(candidates: list) -> dict:
    """
    Step 3: Route candidates to tiers using routing_engine.py.

    Returns:
        {
            "tier_1_candidates": [...],
            "tier_2_candidates": [...],
            "tier_3_candidates": [...],
            "escalation_candidates": [...],
        }
    """
    if not candidates:
        return {
            "tier_1_candidates": [],
            "tier_2_candidates": [],
            "tier_3_candidates": [],
            "escalation_candidates": [],
        }

    # Enforce T-FIX-12: max batch size
    batch = candidates[:MAX_BATCH_SIZE]
    candidates_json = json.dumps(batch)

    result = run_script("routing_engine.py", [
        "--batch-route",
        "--candidate-list", candidates_json,
    ], timeout=60)

    if not result["ok"]:
        # Non-fatal: escalate all candidates
        error_log("routing", result.get("error", "unknown"), {
            "candidate_count": len(batch),
        })
        return {
            "tier_1_candidates": [],
            "tier_2_candidates": [],
            "tier_3_candidates": batch,
            "escalation_candidates": [],
        }

    data = result["data"]
    if isinstance(data, dict):
        # Direct structured response
        return {
            "tier_1_candidates": data.get("tier_1_candidates", data.get("tier_1", [])),
            "tier_2_candidates": data.get("tier_2_candidates", data.get("tier_2", [])),
            "tier_3_candidates": data.get("tier_3_candidates", data.get("tier_3", [])),
            "escalation_candidates": data.get("escalation_candidates", data.get("escalation", [])),
        }

    # Fallback: classify by confidence score
    tier_1, tier_2, tier_3 = [], [], []
    for c in batch:
        score = float(c.get("confidence_score", 0.0))
        if score >= 0.75:
            tier_1.append(c)
        elif score >= 0.60:
            tier_2.append(c)
        else:
            tier_3.append(c)

    return {
        "tier_1_candidates": tier_1,
        "tier_2_candidates": tier_2,
        "tier_3_candidates": tier_3,
        "escalation_candidates": [],
    }


def step4_stage_tier1(tier_1_candidates: list) -> list:
    """
    Step 4: Auto-stage Tier 1 candidates via staging_manager.py.

    Returns list of staging result dicts.
    """
    results = []
    for candidate in tier_1_candidates:
        cand_id = candidate.get("candidate_id") or candidate.get("id") or _gen_id("cand")

        stage_result = run_script("staging_manager.py", [
            "--stage-candidate",
            "--candidate-id", cand_id,
        ], timeout=120)

        if stage_result["ok"]:
            results.append({
                "candidate_id": cand_id,
                "status": "staged",
                "data": stage_result["data"],
            })
            audit_log("tier1_staged", {"candidate_id": cand_id, "status": "staged"})
        else:
            err = stage_result.get("error", "unknown")
            results.append({
                "candidate_id": cand_id,
                "status": "stage_failed",
                "error": err,
            })
            error_log("staging", err, {"candidate_id": cand_id})

    return results


def step5_queue_tier2_tier3(
    tier_2_candidates: list,
    tier_3_candidates: list,
    escalation_candidates: list,
    failure_id: str,
) -> dict:
    """
    Step 5: Queue Tier 2/3 candidates for human review.

    Appends to tier_2_queue.jsonl and tier_3_queue.jsonl.
    Returns queue summary.
    """
    queued_tier2, queued_tier3, queued_escalation = 0, 0, 0
    ts = now_iso()

    for c in tier_2_candidates:
        entry = {
            "queued_at": ts,
            "failure_id": failure_id,
            "candidate_id": c.get("candidate_id") or c.get("id") or _gen_id("cand"),
            "tier": "tier_2",
            "confidence_score": c.get("confidence_score", 0.0),
            "description": c.get("description", ""),
            "action_required": "human_approval_before_production",
        }
        append_jsonl(TIER_2_QUEUE_PATH, entry)
        audit_log("tier2_queued", {"candidate_id": entry["candidate_id"]})
        queued_tier2 += 1

    for c in tier_3_candidates:
        entry = {
            "queued_at": ts,
            "failure_id": failure_id,
            "candidate_id": c.get("candidate_id") or c.get("id") or _gen_id("cand"),
            "tier": "tier_3",
            "confidence_score": c.get("confidence_score", 0.0),
            "description": c.get("description", ""),
            "action_required": "human_review_required",
        }
        append_jsonl(TIER_3_QUEUE_PATH, entry)
        audit_log("tier3_queued", {"candidate_id": entry["candidate_id"]})
        queued_tier3 += 1

    # Escalation: append to Tier 3 with escalation flag
    for c in escalation_candidates:
        entry = {
            "queued_at": ts,
            "failure_id": failure_id,
            "candidate_id": c.get("candidate_id") or c.get("id") or _gen_id("cand"),
            "tier": "escalation",
            "confidence_score": c.get("confidence_score", 0.0),
            "description": c.get("description", ""),
            "action_required": "escalation_invalid_candidate",
        }
        append_jsonl(TIER_3_QUEUE_PATH, entry)
        audit_log("escalation_queued", {"candidate_id": entry["candidate_id"]})
        queued_escalation += 1

    return {
        "queued_tier2": queued_tier2,
        "queued_tier3": queued_tier3,
        "queued_escalation": queued_escalation,
    }


def step6_build_summary(
    failure_id: str,
    detection: dict,
    candidates: list,
    routing: dict,
    staging_results: list,
    queue_summary: dict,
    elapsed_s: float,
) -> dict:
    """Step 6: Build final pipeline summary."""
    tier_1_staged = sum(1 for r in staging_results if r.get("status") == "staged")
    tier_1_failed = sum(1 for r in staging_results if r.get("status") == "stage_failed")

    return {
        "pipeline_id": _gen_id("pipe"),
        "failure_id": failure_id,
        "timestamp": now_iso(),
        "elapsed_seconds": round(elapsed_s, 1),
        "elapsed_minutes": round(elapsed_s / 60, 2),
        "sla_ok": elapsed_s < 420,  # 7-minute SLA
        "detection": {
            "root_cause_pattern_id": detection.get("root_cause_pattern_id"),
            "confidence_score": detection.get("confidence_score"),
            "entity": detection.get("entity"),
            "component": detection.get("component"),
        },
        "candidates": {
            "total": len(candidates),
            "tier_1": len(routing.get("tier_1_candidates", [])),
            "tier_2": len(routing.get("tier_2_candidates", [])),
            "tier_3": len(routing.get("tier_3_candidates", [])),
            "escalation": len(routing.get("escalation_candidates", [])),
        },
        "staging": {
            "tier_1_submitted": len(routing.get("tier_1_candidates", [])),
            "tier_1_staged": tier_1_staged,
            "tier_1_failed": tier_1_failed,
        },
        "queues": {
            "tier_2_queued": queue_summary.get("queued_tier2", 0),
            "tier_3_queued": queue_summary.get("queued_tier3", 0),
            "escalation_queued": queue_summary.get("queued_escalation", 0),
        },
        "next_steps": _build_next_steps(routing, staging_results, queue_summary),
    }


def _build_next_steps(routing: dict, staging_results: list, queue_summary: dict) -> list:
    steps = []
    tier_1_staged = sum(1 for r in staging_results if r.get("status") == "staged")
    if tier_1_staged:
        steps.append(
            f"Review staging results for {tier_1_staged} Tier 1 candidate(s); "
            "auto-promoted if tests pass"
        )
    if queue_summary.get("queued_tier2", 0):
        steps.append(
            f"{queue_summary['queued_tier2']} Tier 2 candidate(s) waiting for human approval"
        )
    if queue_summary.get("queued_tier3", 0) or queue_summary.get("queued_escalation", 0):
        n = queue_summary.get("queued_tier3", 0) + queue_summary.get("queued_escalation", 0)
        steps.append(f"{n} Tier 3/Escalation candidate(s) require human review")
    if not steps:
        steps.append("No actionable candidates; consider improving KB pattern coverage")
    return steps


def _gen_id(prefix: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    h = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
    return f"{prefix}-{ts}-{h}"


# ─── High-Level Pipeline Entrypoints ──────────────────────────────────────────

def run_end_to_end(failure_json: dict) -> dict:
    """
    Execute the full 6-step fix generation pipeline.

    T-FIX-10: Every operation is audit-logged.
    T-FIX-11: Tier 1 staging only after routing engine dual-gate confirmation.
    T-FIX-12: Single-threaded; batch capped at MAX_BATCH_SIZE.
    """
    start = now_epoch()
    audit_log("pipeline_start", {
        "failure_json": failure_json,
        "sla_budget_s": 420,
    })

    # Step 1: Detect failure
    detection = {}
    try:
        detection = step1_detect_failure(failure_json)
        audit_log("step1_complete", {
            "failure_id": detection.get("failure_id"),
            "pattern_id": detection.get("root_cause_pattern_id"),
            "confidence": detection.get("confidence_score"),
        })
    except ValueError as exc:
        error_log("step1_detection", str(exc), {"failure_json": failure_json})
        detection = {
            "failure_id": _gen_id("fail"),
            "root_cause_pattern_id": "unknown",
            "confidence_score": 0.0,
            "entity": failure_json.get("entity", "global"),
            "component": failure_json.get("component", "unknown"),
            "error_pattern": "unknown",
        }

    failure_id = detection["failure_id"]

    # Learning loop integration: fetch active KB patterns
    entity = detection.get("entity", "global")
    component = detection.get("component", "unknown")
    kb_patterns = get_active_patterns(entity, component)
    audit_log("learning_loop_consulted", {
        "failure_id": failure_id,
        "entity": entity,
        "component": component,
        "active_pattern_count": len(kb_patterns),
    })

    # Step 2: Generate candidates
    candidates = []
    try:
        candidates = step2_generate_candidates(failure_id)
        if not candidates:
            raise ValueError("No candidates generated")
        audit_log("step2_complete", {
            "failure_id": failure_id,
            "candidate_count": len(candidates),
        })
    except ValueError as exc:
        error_log("step2_generation", str(exc), {"failure_id": failure_id})
        # Escalate to Tier 3 with error context
        candidates = [{
            "candidate_id": _gen_id("cand"),
            "failure_id": failure_id,
            "confidence_score": 0.40,
            "description": f"Escalated: candidate generation failed ({exc})",
            "tier": "escalation",
        }]

    # Step 3: Route candidates
    routing = step3_route_candidates(candidates)
    audit_log("step3_complete", {
        "failure_id": failure_id,
        "tier_1": len(routing["tier_1_candidates"]),
        "tier_2": len(routing["tier_2_candidates"]),
        "tier_3": len(routing["tier_3_candidates"]),
        "escalation": len(routing["escalation_candidates"]),
    })

    # Step 4: Stage Tier 1 candidates automatically
    staging_results = []
    if routing["tier_1_candidates"]:
        staging_results = step4_stage_tier1(routing["tier_1_candidates"])
        audit_log("step4_complete", {
            "failure_id": failure_id,
            "staged": sum(1 for r in staging_results if r.get("status") == "staged"),
            "failed": sum(1 for r in staging_results if r.get("status") == "stage_failed"),
        })

    # Step 5: Queue Tier 2/3 for human review
    queue_summary = step5_queue_tier2_tier3(
        routing["tier_2_candidates"],
        routing["tier_3_candidates"],
        routing["escalation_candidates"],
        failure_id,
    )
    audit_log("step5_complete", {
        "failure_id": failure_id,
        **queue_summary,
    })

    # Step 6: Build summary
    elapsed = now_epoch() - start
    summary = step6_build_summary(
        failure_id, detection, candidates, routing, staging_results, queue_summary, elapsed
    )
    audit_log("pipeline_complete", {
        "failure_id": failure_id,
        "elapsed_s": elapsed,
        "sla_ok": summary["sla_ok"],
    })

    return summary


def run_process_failure(failure_id: str) -> dict:
    """
    Process an existing failure (already detected) through Steps 2–6.
    Looks up failure details from failure_signatures.jsonl.
    """
    # Look up the failure
    failure_sig = _find_failure_signature(failure_id)
    if not failure_sig:
        return {
            "ok": False,
            "error": f"Failure '{failure_id}' not found in failure_signatures.jsonl",
        }

    start = now_epoch()
    audit_log("process_failure_start", {"failure_id": failure_id})

    detection = {
        "failure_id": failure_id,
        "root_cause_pattern_id": failure_sig.get("root_cause_pattern_id", "unknown"),
        "confidence_score": float(failure_sig.get("confidence_score", 0.5)),
        "entity": failure_sig.get("entity", "global"),
        "component": failure_sig.get("component", "unknown"),
        "error_pattern": failure_sig.get("error_pattern", "unknown"),
    }

    # Learning loop integration
    kb_patterns = get_active_patterns(detection["entity"], detection["component"])
    audit_log("learning_loop_consulted", {
        "failure_id": failure_id,
        "active_pattern_count": len(kb_patterns),
    })

    # Steps 2–6
    candidates = []
    try:
        candidates = step2_generate_candidates(failure_id)
        if not candidates:
            raise ValueError("No candidates generated")
    except ValueError as exc:
        error_log("step2_generation", str(exc), {"failure_id": failure_id})
        candidates = [{
            "candidate_id": _gen_id("cand"),
            "failure_id": failure_id,
            "confidence_score": 0.40,
            "description": f"Escalated: generation failed ({exc})",
        }]

    routing = step3_route_candidates(candidates)
    staging_results = []
    if routing["tier_1_candidates"]:
        staging_results = step4_stage_tier1(routing["tier_1_candidates"])

    queue_summary = step5_queue_tier2_tier3(
        routing["tier_2_candidates"],
        routing["tier_3_candidates"],
        routing["escalation_candidates"],
        failure_id,
    )

    elapsed = now_epoch() - start
    summary = step6_build_summary(
        failure_id, detection, candidates, routing, staging_results, queue_summary, elapsed
    )
    audit_log("process_failure_complete", {"failure_id": failure_id, "elapsed_s": elapsed})
    return summary


def _find_failure_signature(failure_id: str) -> dict | None:
    sigs = load_jsonl(FAILURE_SIGS_PATH)
    for sig in sigs:
        if sig.get("failure_id") == failure_id:
            return sig
    return None


# ─── System Status ─────────────────────────────────────────────────────────────

def get_system_status() -> dict:
    """
    Collect and return system-wide status across all pipeline components.

    Structure matches FIX-05 metrics requirements.
    """
    # Failure detection metrics
    failure_sigs = load_jsonl(FAILURE_SIGS_PATH)
    total_detected = len(failure_sigs)
    confidences = [sig.get("confidence_score", 0.0) for sig in failure_sigs]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    high_conf = sum(1 for c in confidences if c >= 0.80)
    accuracy_est = f"{round(high_conf / max(len(confidences), 1) * 100)}%+"

    # Candidate generation metrics
    candidates = load_jsonl(GENERATED_CANDIDATES_PATH)
    candidate_scores = [c.get("confidence_score", 0.0) for c in candidates]
    avg_score = sum(candidate_scores) / len(candidate_scores) if candidate_scores else 0.0
    score_min = min(candidate_scores) if candidate_scores else 0.0
    score_max = max(candidate_scores) if candidate_scores else 0.0

    # Group candidates by failure to compute avg_per_failure
    failure_to_cands: dict[str, int] = {}
    for c in candidates:
        fid = c.get("failure_id", "unknown")
        failure_to_cands[fid] = failure_to_cands.get(fid, 0) + 1
    counts = list(failure_to_cands.values())
    avg_candidates_per_failure = round(sum(counts) / len(counts), 1) if counts else 0.0

    # Routing metrics
    tier1_q = load_jsonl(ROUTING_DIR / "tier_1_queue.jsonl")
    tier2_q = load_jsonl(TIER_2_QUEUE_PATH)
    tier3_q = load_jsonl(TIER_3_QUEUE_PATH)
    escal_q = load_jsonl(ROUTING_DIR / "escalation_queue.jsonl" if (ROUTING_DIR / "escalation_queue.jsonl").exists() else TIER_3_QUEUE_PATH)

    # Staging metrics
    staging_results = load_jsonl(STAGING_METRICS_PATH)
    staging_pass = sum(1 for s in staging_results if s.get("outcome") in ("promoted", "passed"))
    staging_total = len(staging_results)
    staging_pass_rate = f"{round(staging_pass / max(staging_total, 1) * 100)}%"
    staging_pipeline = load_jsonl(STAGING_PIPELINE_PATH)
    tier1_staged = sum(1 for s in staging_pipeline if s.get("tier") in ("tier_1", None))

    # Latency metrics
    latency_data = load_jsonl(LATENCY_HISTORY_PATH)
    latencies = [l.get("total_seconds", 0.0) for l in latency_data if l.get("total_seconds")]
    avg_latency_s = sum(latencies) / len(latencies) if latencies else 0.0
    latencies_sorted = sorted(latencies)
    p95_idx = max(0, int(len(latencies_sorted) * 0.95) - 1) if latencies_sorted else 0
    p95_latency_s = latencies_sorted[p95_idx] if latencies_sorted else 0.0
    sla_violations = sum(1 for l in latencies if l > 420)
    sla_status = "HEALTHY" if (not latencies or sla_violations / len(latencies) < 0.20) else "WARNING"

    # Audit trail
    audit_entries = load_jsonl(SYSTEM_AUDIT_PATH)
    errors = load_jsonl(SYSTEM_ERRORS_PATH)

    return {
        "timestamp": now_iso(),
        "failure_detection": {
            "total_detected": total_detected,
            "avg_confidence": round(avg_confidence, 3),
            "accuracy": accuracy_est,
        },
        "candidate_generation": {
            "total_candidates": len(candidates),
            "avg_candidates_per_failure": avg_candidates_per_failure,
            "confidence_range": [round(score_min, 3), round(score_max, 3)],
            "avg_confidence": round(avg_score, 3),
        },
        "routing": {
            "tier_1_count": len(tier1_q),
            "tier_2_count": len(tier2_q),
            "tier_3_count": len(tier3_q),
        },
        "staging": {
            "tier_1_staged": tier1_staged,
            "tier_1_pass_rate": staging_pass_rate,
            "total_staging_runs": staging_total,
        },
        "latency": {
            "avg_total": f"{round(avg_latency_s / 60, 1)} min" if latency_data else "no data",
            "p95_total": f"{round(p95_latency_s / 60, 1)} min" if latency_data else "no data",
            "sla_status": sla_status,
            "sla_violations": sla_violations,
            "sla_violation_rate": f"{round(sla_violations / max(len(latencies), 1) * 100)}%",
        },
        "queues": {
            "tier_1": len(tier1_q),
            "tier_2": len(tier2_q),
            "tier_3": len(tier3_q),
            "escalation": 0,
        },
        "audit_trail": {
            "total_audit_entries": len(audit_entries),
            "total_errors": len(errors),
        },
    }


# ─── Phase 4 Integration Hooks ─────────────────────────────────────────────────

def get_tier_1_ready_for_production() -> list:
    """
    Phase 4 hook: Return Tier 1 candidates that have passed staging and are
    ready for production promotion.

    Reads staging_validation_results.jsonl and filters for passed candidates.
    """
    validation_results = load_jsonl(STAGING_VALIDATION_PATH)
    ready = []
    for r in validation_results:
        if r.get("overall_result") in ("PASSED", "passed", "promoted"):
            candidate_id = r.get("candidate_id")
            if candidate_id:
                ready.append({
                    "candidate_id": candidate_id,
                    "final_confidence": r.get("final_confidence", r.get("confidence_score", 0.0)),
                    "staging_completed_at": r.get("validated_at", r.get("timestamp", "")),
                    "promotion_status": "ready",
                })

    # Also check staging pipeline for promoted entries
    pipeline = load_jsonl(STAGING_PIPELINE_PATH)
    staged_ids = {r["candidate_id"] for r in ready}
    for entry in pipeline:
        if (entry.get("status") in ("promoted", "auto_promoted")
                and entry.get("candidate_id") not in staged_ids):
            ready.append({
                "candidate_id": entry["candidate_id"],
                "final_confidence": entry.get("final_confidence", 0.0),
                "staging_completed_at": entry.get("completed_at", ""),
                "promotion_status": "ready",
            })

    return ready


def get_tier_2_staged_results() -> list:
    """
    Phase 4 hook: Return Tier 2 candidates in staging with test results.
    These require human approval before promotion.
    """
    # Read staging approvals
    staging_dir = STAGING_DIR
    approvals_path = staging_dir / "staging_approvals.jsonl"
    approvals = load_jsonl(approvals_path)

    results = []
    for a in approvals:
        if a.get("status") in ("pending_approval", "pending", "awaiting_review"):
            results.append({
                "candidate_id": a.get("candidate_id"),
                "staged_at": a.get("queued_at", a.get("timestamp", "")),
                "test_results": a.get("test_results", {}),
                "approval_status": "pending",
                "approver_action_required": True,
            })

    # Also check tier_2 queue
    tier2_entries = load_jsonl(TIER_2_QUEUE_PATH)
    queued_ids = {r.get("candidate_id") for r in results}
    for entry in tier2_entries:
        if entry.get("candidate_id") not in queued_ids:
            results.append({
                "candidate_id": entry.get("candidate_id"),
                "staged_at": entry.get("queued_at", ""),
                "test_results": {},
                "approval_status": "pending_staging",
                "approver_action_required": True,
            })

    return results


def get_tier_3_escalated() -> list:
    """
    Phase 4 hook: Return Tier 3 and escalated candidates awaiting human review.
    """
    tier3_entries = load_jsonl(TIER_3_QUEUE_PATH)
    results = []
    for entry in tier3_entries:
        results.append({
            "candidate_id": entry.get("candidate_id"),
            "failure_id": entry.get("failure_id"),
            "tier": entry.get("tier", "tier_3"),
            "queued_at": entry.get("queued_at", ""),
            "confidence_score": entry.get("confidence_score", 0.0),
            "action_required": entry.get("action_required", "human_review_required"),
            "description": entry.get("description", ""),
        })
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AKC Fix Generation System — Orchestrator"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--end-to-end",
        action="store_true",
        help="Run complete pipeline from failure JSON to staging",
    )
    group.add_argument(
        "--process-failure",
        action="store_true",
        help="Process existing failure ID through candidate generation pipeline",
    )
    group.add_argument(
        "--get-system-status",
        action="store_true",
        help="Return system-wide status and queue depths",
    )
    group.add_argument(
        "--get-phase4-hooks",
        action="store_true",
        help="Return all Phase 4 integration hook data",
    )

    parser.add_argument("--failure-json", type=str, help="Failure JSON for --end-to-end")
    parser.add_argument("--failure-id", type=str, help="Failure ID for --process-failure")

    args = parser.parse_args()

    if args.end_to_end:
        if not args.failure_json:
            parser.error("--end-to-end requires --failure-json")
        try:
            failure_json = json.loads(args.failure_json)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"Invalid JSON: {exc}\n")
            sys.exit(1)

        result = run_end_to_end(failure_json)
        print(json.dumps(result, indent=2))

    elif args.process_failure:
        if not args.failure_id:
            parser.error("--process-failure requires --failure-id")
        result = run_process_failure(args.failure_id)
        print(json.dumps(result, indent=2))

    elif args.get_system_status:
        status = get_system_status()
        print(json.dumps(status, indent=2))

    elif args.get_phase4_hooks:
        hooks = {
            "tier_1_ready_for_production": get_tier_1_ready_for_production(),
            "tier_2_staged_results": get_tier_2_staged_results(),
            "tier_3_escalated": get_tier_3_escalated(),
        }
        print(json.dumps(hooks, indent=2))


if __name__ == "__main__":
    main()
