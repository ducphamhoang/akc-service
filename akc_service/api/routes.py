#!/usr/bin/env python3
"""
AKC Service Routes
Phase 1 — Query endpoint for pattern retrieval

Implements the REST boundary for AKC pattern queries.
Integrates with orchestrator_hooks.get_active_patterns() for KB access.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

# Package-relative imports — no sys.path manipulation needed
import os
from pathlib import Path

from akc_service.kb_exporter import export_patterns_to_markdown
from akc_service.config import KB_EXPORT_DIR, KB_EXPORT_FORMAT, KB_EXPORT_MIN_CONFIDENCE

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))

from akc_service.learning_integration import (
    load_all_patterns,
    get_deduped_patterns,
    find_pattern_by_id,
    append_pattern_version,
    log_confidence_update,
    check_latency,
    count_history_patterns_in_window,
    now_iso,
    determine_tier,
    apply_confidence_delta,
    save_checkpoint,
    restore_from_checkpoint,
    CHECKPOINT_PATH,
)


def get_active_patterns(entity: str, component: str) -> list:
    """
    Query patterns for entity:component.

    Replaces the former orchestrator_hooks.get_active_patterns() to avoid
    circular dependency. Uses get_deduped_patterns() from learning_integration
    for deterministic, deduplicated results.

    Deduplication: last-occurrence-wins per pattern ID (most recent version).
    Ordering: primary sort by confidence descending; ties broken by pattern ID
    ascending so the result is identical across repeated calls.

    Returns list of dicts with pattern id, confidence, and tier.
    """
    if not entity or not component:
        logger.warning(f"get_active_patterns: missing entity or component ({entity}, {component})")
        return []

    # get_deduped_patterns() returns one entry per ID (last-occurrence-wins)
    # sorted by ID ascending — gives us a stable base to sort on.
    patterns = get_deduped_patterns()
    if not patterns:
        logger.warning("get_active_patterns: no patterns loaded from KB")
        return []

    matched = []
    for p in patterns:
        if p.get("entity") == entity and p.get("component") == component:
            tier = p.get("confidence_tier", "production")
            if tier != "demoted":
                matched.append({
                    "id": p.get("id"),
                    "confidence": p.get("confidence", 0.5),
                    "tier": tier,
                })

    # Sort by confidence descending; break ties deterministically by ID ascending
    matched.sort(key=lambda x: (-x["confidence"], x["id"]))
    return matched

logger = logging.getLogger(__name__)
PATTERNS_PATH = KB_DIR / "patterns.jsonl"

# ─── Pydantic Models ───────────────────────────────────────────────────────

class RecordRequest(BaseModel):
    """Request model for recording task outcomes."""
    schema_version: str = Field(..., description="Schema version (must be '1.0')")
    task_id: str = Field(..., description="Unique task identifier")
    status: str = Field(..., description="Task status: 'success' or 'failed'")
    timestamp: str = Field(..., description="ISO 8601 timestamp of task completion")
    akc_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="AKC context with active patterns and outcomes"
    )


class RecordResponse(BaseModel):
    """Response model for task outcome recording."""
    accepted: bool = Field(..., description="Whether the record was accepted")
    task_id: str = Field(..., description="Echoed task identifier")
    update_mode: str = Field(..., description="'async' or 'sync'")
    patterns_to_update: int = Field(..., description="Number of patterns to update")
    timestamp: str = Field(..., description="Server timestamp")


class FixRequest(BaseModel):
    """Request model for pattern fix retrieval."""
    category: str = Field(..., description="Fix category: detection|implementation|testing|documentation|other")


class FixResponse(BaseModel):
    """Response model for pattern fixes."""
    fixes: List[Dict[str, Any]] = Field(..., description="List of matching fixes")
    category: str = Field(..., description="Echoed fix category")
    count: int = Field(..., description="Number of fixes returned")


class StatsRequest(BaseModel):
    """Request model for KB statistics."""
    time_window: str = Field(
        default="all",
        description="Time window: 'all', '24h', '7d', '30d'"
    )


class StatsResponse(BaseModel):
    """Response model for KB statistics."""
    sample_count: int = Field(..., description="Number of latency samples in the time window")
    latency_stats: Dict[str, Any] = Field(..., description="Min/max/avg/p95 latency in ms")
    sla_status: str = Field(..., description="'HEALTHY' or 'WARNING'")
    gold_tier_count: int = Field(..., description="Number of gold-tier patterns")
    avg_confidence: float = Field(..., description="Average confidence across KB")
    patterns_updated: int = Field(
        default=0,
        description="Number of unique patterns updated in the time window"
    )
    time_window: str = Field(
        default="all",
        description="Time window applied to these stats"
    )


class UpdateRequest(BaseModel):
    """Request model for direct confidence update."""
    pattern_id: str = Field(..., description="Pattern ID to update")
    new_score: float = Field(..., ge=0.0, le=0.95, description="New confidence score [0.0-0.95]")
    reason: str = Field(..., description="Reason for update (e.g., 'manual override', 'flagged_issue')")


class UpdateResponse(BaseModel):
    """Response model for confidence update."""
    pattern_id: str = Field(..., description="Updated pattern ID")
    old_score: float = Field(..., description="Previous confidence score")
    new_score: float = Field(..., description="New confidence score")
    updated_at: str = Field(..., description="ISO 8601 update timestamp")


class QueryRequest(BaseModel):
    """Request model for pattern query."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_id": "task-001",
                "entity": "player",
                "component": "HealthComponent",
                "context": {"difficulty": "hard"}
            }
        }
    )

    task_id: str = Field(..., description="Unique task identifier")
    entity: str = Field(..., description="Entity name (e.g., 'player', 'enemy_knight')")
    component: str = Field(..., description="Component name (e.g., 'HealthComponent')")
    context: Optional[dict] = Field(
        default=None,
        description="Additional context for the query (optional)"
    )


class PatternResponse(BaseModel):
    """Pattern metadata in response."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "pattern_001",
                "confidence": 0.85,
                "tier": "gold"
            }
        }
    )

    id: str = Field(..., description="Pattern unique identifier")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    tier: str = Field(..., description="Confidence tier (gold, production, experimental, demoted)")


class QueryResponse(BaseModel):
    """Response model for pattern query."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
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
                    }
                ],
                "query_latency_ms": 12.5,
                "source": "kb"
            }
        }
    )

    patterns: List[PatternResponse] = Field(..., description="List of matching patterns")
    query_latency_ms: float = Field(..., description="Query execution time in milliseconds")
    source: str = Field(default="kb", description="Source of patterns (kb, cache, etc.)")


# ─── APIRouter ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/akc/v1", tags=["patterns"])


# ─── Pattern Query Endpoint ────────────────────────────────────────────────

@router.post("/query")
async def query_patterns(request: QueryRequest) -> QueryResponse:
    """
    Query the knowledge base for patterns matching entity:component.

    Retrieves active patterns from orchestrator_hooks and returns them
    with confidence scores and tier classifications.

    Args:
        request: QueryRequest with task_id, entity, component, and optional context.

    Returns:
        QueryResponse with list of matching patterns and query metadata.

    Raises:
        HTTPException 400: If entity or component is missing or invalid.
        HTTPException 500: If pattern retrieval fails.
    """

    # Validate input
    if not request.entity or not request.component:
        logger.warning(
            f"query_patterns: invalid input - "
            f"entity={request.entity}, component={request.component}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity and component are required"
        )

    # Record start time for latency calculation
    start_time = time.time()

    try:
        logger.info(
            f"query_patterns: task={request.task_id}, "
            f"entity={request.entity}, component={request.component}"
        )

        # Call orchestrator_hooks to retrieve patterns
        active_patterns = get_active_patterns(
            entity=request.entity,
            component=request.component
        )

        # Convert orchestrator response to PatternResponse models
        patterns = [
            PatternResponse(
                id=p.get("id"),
                confidence=float(p.get("confidence", 0.5)),
                tier=p.get("tier", "production")
            )
            for p in active_patterns
            if p.get("id")  # Filter out patterns without ID
        ]

        # Calculate query latency
        elapsed_ms = (time.time() - start_time) * 1000

        logger.info(
            f"query_patterns: task={request.task_id} returned {len(patterns)} patterns "
            f"(latency={elapsed_ms:.2f}ms)"
        )

        # Build response
        response = QueryResponse(
            patterns=patterns,
            query_latency_ms=elapsed_ms,
            source="kb"
        )

        return response

    except Exception as e:
        logger.error(
            f"query_patterns: task={request.task_id} failed - {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pattern query failed: {str(e)}"
        )


# ─── Task Outcome Recording Endpoint ───────────────────────────────────────

@router.post("/record", status_code=status.HTTP_200_OK)
async def record_task_outcome(request: RecordRequest) -> RecordResponse:
    """
    Record a task outcome and apply confidence delta updates synchronously.

    Validates schema_version == "1.0", then immediately applies confidence deltas
    to active patterns. KB write completes before response is returned — no
    fire-and-forget, no lost updates on process restart.

    Args:
        request: RecordRequest with task_id, status, timestamp, and akc_context.

    Returns:
        RecordResponse 200 OK when KB write has completed and been durably persisted.

    Raises:
        HTTPException 400: If schema_version != "1.0" or status invalid.
        HTTPException 500: If KB write fails.
    """
    try:
        # Validate schema version
        if request.schema_version != "1.0":
            logger.warning(
                f"record_task_outcome: invalid schema_version={request.schema_version} "
                f"(task={request.task_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"schema_version must be '1.0', got '{request.schema_version}'"
            )

        # Validate status
        if request.status not in ["success", "failed"]:
            logger.warning(
                f"record_task_outcome: invalid status={request.status} (task={request.task_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"status must be 'success' or 'failed', got '{request.status}'"
            )

        logger.info(
            f"record_task_outcome: task={request.task_id}, status={request.status}, "
            f"timestamp={request.timestamp}"
        )

        # Build task_result for learning integration
        task_result = {
            "schema_version": request.schema_version,
            "task_id": request.task_id,
            "status": request.status,
            "timestamp": request.timestamp,
            "akc_context": request.akc_context
        }

        # Apply learning delta update synchronously to ensure durability
        # Confidence updates must reach KB before 202 response is sent
        delta_result = apply_confidence_delta(task_result)

        # Extract results for response
        active_patterns = request.akc_context.get("knowledge_patterns_active", [])
        # Use actual count from delta result if available, else use active pattern count
        patterns_to_update = (
            delta_result.get("patterns_updated")
            if delta_result.get("patterns_updated") is not None
            else len(active_patterns)
        )

        # Log outcome recording with result
        logger.info(
            f"record_task_outcome: accepted task={request.task_id}, "
            f"patterns_to_update={patterns_to_update}, "
            f"delta_status={delta_result.get('status', 'unknown')}"
        )

        response = RecordResponse(
            accepted=True,
            task_id=request.task_id,
            update_mode="sync",
            patterns_to_update=patterns_to_update,
            timestamp=now_iso()
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"record_task_outcome: task={request.task_id} failed - {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record task outcome: {str(e)}"
        )


# ─── Pattern Fix Retrieval Endpoint ────────────────────────────────────────

@router.post("/fix")
async def get_pattern_fixes(request: FixRequest) -> FixResponse:
    """
    Retrieve fix recommendations for a pattern by category.

    Loads all patterns from KB, filters by category, and returns matching fixes.

    Args:
        request: FixRequest with category (enum of 5 categories).

    Returns:
        FixResponse with list of fixes matching the category.

    Raises:
        HTTPException 400: If category is invalid.
        HTTPException 500: If pattern loading fails.
    """
    try:
        valid_categories = {"detection", "implementation", "testing", "documentation", "other"}
        if request.category not in valid_categories:
            logger.warning(
                f"get_pattern_fixes: invalid category={request.category}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"category must be one of {valid_categories}, got '{request.category}'"
            )

        logger.info(
            f"get_pattern_fixes: category={request.category}"
        )

        # Load all patterns
        all_patterns = load_all_patterns()
        if not all_patterns:
            logger.warning("get_pattern_fixes: no patterns in KB")
            return FixResponse(fixes=[], category=request.category, count=0)

        # Filter by category and collect fixes
        matching_fixes = []
        for pattern in all_patterns:
            # Check if pattern has metadata matching category
            pattern_category = pattern.get("category", "other")
            if pattern_category == request.category:
                fixes = pattern.get("fixes", [])
                if fixes:
                    matching_fixes.extend(fixes)

        if not matching_fixes:
            logger.info(
                f"get_pattern_fixes: no fixes found for category={request.category}"
            )
            return FixResponse(fixes=[], category=request.category, count=0)

        logger.info(
            f"get_pattern_fixes: returned {len(matching_fixes)} fixes "
            f"for category={request.category}"
        )

        response = FixResponse(
            fixes=matching_fixes,
            category=request.category,
            count=len(matching_fixes)
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"get_pattern_fixes: category={request.category} failed - {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pattern fixes: {str(e)}"
        )


# ─── KB Statistics Endpoint ────────────────────────────────────────────────

_VALID_TIME_WINDOWS = {"all", "1h", "24h", "7d", "30d"}

_WINDOW_DELTAS = {
    "1h":  {"hours": 1},
    "24h": {"hours": 24},
    "7d":  {"days": 7},
    "30d": {"days": 30},
}


def _parse_time_window(window_str: str) -> Optional[datetime]:
    """
    Convert a time_window string to a UTC cutoff datetime.

    Supported values: "1h", "24h", "7d", "30d".
    Returns None for "all" (no cutoff) or unrecognised values.

    Args:
        window_str: One of "all", "1h", "24h", "7d", "30d".

    Returns:
        timezone-aware UTC datetime marking the start of the window, or None.
    """
    from datetime import timedelta
    delta_kwargs = _WINDOW_DELTAS.get(window_str)
    if delta_kwargs is None:
        return None  # "all" or unknown → no cutoff
    return datetime.now(timezone.utc) - timedelta(**delta_kwargs)


@router.get("/stats")
async def get_kb_stats(time_window: str = "all") -> StatsResponse:
    """
    Retrieve KB statistics: latency compliance, gold-tier count, average confidence.

    Calls check_latency() from learning_integration and loads patterns for tier analysis.
    When time_window is specified, latency samples and pattern-update counts are
    filtered to only include records whose timestamp falls within the window.

    Args:
        time_window: Optional query param — "all" (default), "1h", "24h", "7d", or "30d".
                     Invalid values are rejected with HTTP 400.

    Returns:
        StatsResponse with latency stats, SLA status, tier metrics, and window metadata.

    Raises:
        HTTPException 400: If time_window value is not one of the accepted options.
        HTTPException 500: If stats collection fails.
    """
    try:
        if time_window not in _VALID_TIME_WINDOWS:
            logger.warning(f"get_kb_stats: invalid time_window={time_window!r}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid time_window {time_window!r}. "
                    f"Must be one of: {sorted(_VALID_TIME_WINDOWS)}"
                )
            )

        # Resolve the window to a UTC cutoff (None means "all time")
        cutoff = _parse_time_window(time_window)

        logger.info(
            f"get_kb_stats: time_window={time_window}, "
            f"cutoff={cutoff.isoformat() if cutoff else 'none'}"
        )

        # Get latency stats filtered by the time window
        latency_data = check_latency(cutoff_time=cutoff)
        latency_stats = latency_data.get("latency_stats", {})
        sla_status = latency_data.get("sla_status", "UNKNOWN")

        # Count unique patterns updated in the window
        window_counts = count_history_patterns_in_window(cutoff_time=cutoff)
        patterns_updated = window_counts["patterns_updated"]

        # Pattern-level stats (current KB state — not time-windowed, as patterns
        # are point-in-time snapshots of the current KB, not historical aggregates)
        all_patterns = load_all_patterns()
        gold_tier_count = 0
        total_confidence = 0.0

        for pattern in all_patterns:
            tier = pattern.get("confidence_tier", "production")
            confidence = pattern.get("confidence", 0.5)

            if tier == "gold":
                gold_tier_count += 1

            total_confidence += confidence

        avg_confidence = (total_confidence / len(all_patterns)) if all_patterns else 0.0

        logger.info(
            f"get_kb_stats: gold_count={gold_tier_count}, avg_conf={avg_confidence:.4f}, "
            f"sample_count={latency_data.get('sample_count', 0)}, "
            f"patterns_updated={patterns_updated}, time_window={time_window}"
        )

        response = StatsResponse(
            sample_count=latency_data.get("sample_count", 0),
            latency_stats=latency_stats,
            sla_status=sla_status,
            gold_tier_count=gold_tier_count,
            avg_confidence=round(avg_confidence, 4),
            patterns_updated=patterns_updated,
            time_window=time_window,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_kb_stats: failed - {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve KB statistics: {str(e)}"
        )


# ─── Confidence Override Endpoint ──────────────────────────────────────────

@router.post("/update")
async def update_pattern_confidence(request: UpdateRequest) -> UpdateResponse:
    """
    Direct confidence override endpoint (manual or privileged updates only).

    Validates new_score in [0.0, 0.95], loads pattern by ID, updates confidence,
    appends new version to patterns.jsonl, and logs to confidence_history.jsonl.

    Args:
        request: UpdateRequest with pattern_id, new_score, and reason.

    Returns:
        UpdateResponse with old/new scores and update timestamp.

    Raises:
        HTTPException 404: If pattern not found.
        HTTPException 400: If new_score out of range or pattern_id invalid.
        HTTPException 500: If file write fails.
    """
    try:
        # Validate new_score bounds
        if not (0.0 <= request.new_score <= 0.95):
            logger.warning(
                f"update_pattern_confidence: new_score={request.new_score} out of range "
                f"for pattern={request.pattern_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"new_score must be in [0.0, 0.95], got {request.new_score}"
            )

        logger.info(
            f"update_pattern_confidence: pattern={request.pattern_id}, "
            f"new_score={request.new_score}, reason='{request.reason}'"
        )

        # Load all patterns and find by ID
        all_patterns = load_all_patterns()
        pattern = find_pattern_by_id(request.pattern_id, all_patterns)

        if not pattern:
            logger.warning(
                f"update_pattern_confidence: pattern not found - {request.pattern_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Pattern '{request.pattern_id}' not found"
            )

        # Save old values
        old_confidence = pattern.get("confidence", 0.5)
        old_tier = determine_tier(old_confidence)
        new_tier = determine_tier(request.new_score)

        # Update pattern with new confidence
        updated_pattern = {
            **pattern,
            "confidence": request.new_score,
            "confidence_tier": new_tier,
            "updated_at": now_iso()
        }

        # Update version history
        version_info = pattern.get("version", {"current": "v1", "history": []})
        current_version = version_info.get("current", "v1")
        try:
            version_num = int(current_version[1:])
            next_version = f"v{version_num + 1}"
        except (ValueError, IndexError):
            next_version = "v2"

        snapshot = {
            "version_id": next_version,
            "confidence_snapshot": request.new_score,
            "timestamp": now_iso(),
            "change_reason": request.reason,
            "changed_by": "api_manual_override",
            "tier": new_tier
        }

        history = version_info.get("history", [])
        history.append(snapshot)
        version_info = {**version_info, "current": next_version, "history": history}
        updated_pattern["version"] = version_info

        # Append to patterns.jsonl
        append_pattern_version(updated_pattern)

        # Log to confidence_history.jsonl
        log_confidence_update({
            "history_id": f"ch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            "timestamp": now_iso(),
            "pattern_id": request.pattern_id,
            "old_confidence": old_confidence,
            "new_confidence": request.new_score,
            "confidence_delta": request.new_score - old_confidence,
            "task_id": "manual_override",
            "task_status": "override",
            "tier_change": f"{old_tier} → {new_tier}" if old_tier != new_tier else "none",
            "update_type": "api_manual",
            "reason": request.reason
        })

        logger.info(
            f"update_pattern_confidence: pattern={request.pattern_id} updated "
            f"{old_confidence:.4f} → {request.new_score:.4f} ({old_tier} → {new_tier})"
        )

        response = UpdateResponse(
            pattern_id=request.pattern_id,
            old_score=old_confidence,
            new_score=request.new_score,
            updated_at=now_iso()
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"update_pattern_confidence: pattern={request.pattern_id} failed - {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update pattern confidence: {str(e)}"
        )


# ─── Reset Escape Hatch Endpoint ───────────────────────────────────────────

class ResetRequest(BaseModel):
    """Request model for KB reset."""
    reason: str = Field(
        default="manual_reset",
        description="Reason for initiating reset (logged to audit trail)"
    )


class ResetResponse(BaseModel):
    """Response model for KB reset."""
    status: str = Field(..., description="'restored' | 'failed' | 'blocked'")
    reason: str = Field(..., description="Echoed reason for reset")
    patterns_restored: int = Field(..., description="Number of unique patterns in restored KB")
    checkpoint_used: bool = Field(..., description="True if checkpoint existed and was used")
    effects: List[str] = Field(default_factory=list, description="Side-effect descriptions")
    timestamp: str = Field(..., description="ISO 8601 timestamp of reset operation")


class KBExportRequest(BaseModel):
    """Request model for KB markdown export."""
    export_path: Optional[str] = Field(None, description="Override default export path")
    organization: str = Field("by-entity", description="Organization strategy: by-entity, by-tier, or by-pattern-type")
    min_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Minimum confidence threshold")
    include_demoted: bool = Field(False, description="Include demoted patterns")
    dry_run: bool = Field(False, description="Validate without writing files")


class KBExportResponse(BaseModel):
    """Response model for KB markdown export."""
    success: bool = Field(..., description="Export operation success status")
    patterns_exported: int = Field(..., description="Number of patterns exported")
    folder: str = Field(..., description="Export folder path")
    organization: str = Field(..., description="Organization strategy used")
    exported_at: str = Field(..., description="ISO 8601 export timestamp")
    error: Optional[str] = Field(None, description="Error message if export failed")


@router.post("/reset")
async def reset_kb(request: ResetRequest) -> ResetResponse:
    """
    Reset escape hatch: restore KB to the startup checkpoint.

    Atomically replaces patterns.jsonl with the checkpoint saved at service
    startup.  Returns the number of unique patterns in the restored KB and
    a list of side-effect messages so operators can confirm recovery.

    Guards:
    - Blocked if quarantine mode is active (escape_hatch == 'quarantine').
    - Fails gracefully if no checkpoint exists (returns status='failed').

    Returns:
        ResetResponse with status, pattern count, and effects list.

    Raises:
        HTTPException 409: If quarantine mode is active.
        HTTPException 503: If no checkpoint is available.
        HTTPException 500: If restoration fails unexpectedly.
    """
    try:
        from akc_service.safety_engine import load_safety_state as _load_safety_state
        from akc_service.safety_engine import set_escape_hatch as _set_escape_hatch

        logger.warning(f"reset_kb: operator-initiated KB reset — reason='{request.reason}'")

        # Guard: block reset if quarantine is active
        safety_state = _load_safety_state()
        if safety_state.get("escape_hatch") == "quarantine":
            logger.error("reset_kb: blocked — quarantine mode is active")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Reset blocked: quarantine mode is active. "
                    "Lift quarantine first (POST /akc/v1/escape-hatch with mode='none'), "
                    "then retry reset."
                )
            )

        # Check checkpoint exists before attempting restore (uses module-level CHECKPOINT_PATH)
        if not CHECKPOINT_PATH.exists():
            logger.error("reset_kb: no checkpoint available")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "No checkpoint available. The service must have been started at least once "
                    "with patterns.jsonl present to create a checkpoint."
                )
            )

        # Perform atomic restore
        success = restore_from_checkpoint()
        if not success:
            logger.error("reset_kb: restore_from_checkpoint returned False")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Checkpoint restore failed — check server logs for details."
            )

        # Verify: count unique patterns in restored KB
        restored_patterns = load_all_patterns()
        unique_patterns: dict = {}
        for p in restored_patterns:
            pid = p.get("id")
            if pid:
                unique_patterns[pid] = p
        pattern_count = len(unique_patterns)

        # Record reset in safety state audit trail
        try:
            _set_escape_hatch("reset", reason=request.reason)
        except Exception as e:
            logger.warning(f"reset_kb: safety state audit failed (non-fatal): {e}")

        effects = [
            f"KB patterns restored from checkpoint ({pattern_count} patterns loaded)",
            "Audit trail preserved in confidence_history.jsonl",
            f"Verification passed: {pattern_count} unique patterns confirmed readable",
        ]

        logger.warning(
            f"reset_kb: restore complete — {pattern_count} patterns, reason='{request.reason}'"
        )

        return ResetResponse(
            status="restored",
            reason=request.reason,
            patterns_restored=pattern_count,
            checkpoint_used=True,
            effects=effects,
            timestamp=now_iso(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"reset_kb: unexpected failure — {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"KB reset failed: {str(e)}"
        )


# ─── KB Export Endpoint ────────────────────────────────────────────────────────

@router.post("/kb/export-markdown")
async def export_kb_to_markdown(request: KBExportRequest) -> KBExportResponse:
    """
    Export KB patterns to markdown files organized by entity, tier, or pattern type.

    Converts patterns.jsonl to a folder structure of markdown files compatible
    with graphRAG for indexing and knowledge graph construction.

    Args:
        request: KBExportRequest with organization strategy, confidence threshold,
                 and optional export path override.

    Returns:
        KBExportResponse with export metadata and results.

    Raises:
        HTTPException 400: If organization strategy invalid or min_confidence out of range.
        HTTPException 404: If patterns.jsonl not found.
        HTTPException 500: If export fails.
    """
    try:
        # Validate organization strategy
        valid_organizations = {"by-entity", "by-tier", "by-pattern-type"}
        if request.organization not in valid_organizations:
            logger.warning(
                f"export_kb_to_markdown: invalid organization={request.organization}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"organization must be one of {valid_organizations}, "
                    f"got '{request.organization}'"
                )
            )

        # Validate min_confidence (already bounded by Pydantic, but be explicit)
        if not (0.0 <= request.min_confidence <= 1.0):
            logger.warning(
                f"export_kb_to_markdown: min_confidence={request.min_confidence} out of range"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"min_confidence must be between 0.0 and 1.0, got {request.min_confidence}"
            )

        # Resolve export path (default to KB_EXPORT_DIR from config)
        export_path = request.export_path or str(KB_EXPORT_DIR)

        # Check if patterns.jsonl exists
        patterns_file = PATTERNS_PATH
        if not patterns_file.exists():
            logger.warning(
                f"export_kb_to_markdown: patterns file not found - {patterns_file}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"patterns.jsonl not found at {patterns_file}"
            )

        logger.info(
            f"export_kb_to_markdown: organization={request.organization}, "
            f"min_confidence={request.min_confidence}, "
            f"include_demoted={request.include_demoted}, "
            f"dry_run={request.dry_run}, "
            f"export_path={export_path}"
        )

        # Call export function
        result = export_patterns_to_markdown(
            export_path=export_path,
            jsonl_path=str(patterns_file),
            organization=request.organization,
            min_confidence=request.min_confidence,
            include_demoted=request.include_demoted,
            dry_run=request.dry_run
        )

        # Handle export result
        if result.get("success"):
            logger.info(
                f"export_kb_to_markdown: success — {result.get('patterns_exported')} patterns "
                f"exported to {result.get('folder')}"
            )
            response = KBExportResponse(
                success=True,
                patterns_exported=result.get("patterns_exported", 0),
                folder=result.get("folder", export_path),
                organization=request.organization,
                exported_at=result.get("exported_at", now_iso()),
                error=None
            )
        else:
            error_msg = result.get("error", "Export failed for unknown reason")
            logger.error(f"export_kb_to_markdown: failed — {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Export failed: {error_msg}"
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"export_kb_to_markdown: unexpected failure — {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"KB export failed: {str(e)}"
        )
