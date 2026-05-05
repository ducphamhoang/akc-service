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

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

# Package-relative imports — no sys.path manipulation needed
import os
from pathlib import Path

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))

from akc_service.learning_integration import (
    load_all_patterns,
    find_pattern_by_id,
    append_pattern_version,
    log_confidence_update,
    check_latency,
    now_iso,
    determine_tier,
    apply_confidence_delta,
)


def get_active_patterns(entity: str, component: str) -> list:
    """
    Query patterns for entity:component.

    Replaces the former orchestrator_hooks.get_active_patterns() to avoid
    circular dependency. Uses load_all_patterns() from learning_integration.

    Returns list of dicts with pattern id, confidence, and tier.
    """
    if not entity or not component:
        logger.warning(f"get_active_patterns: missing entity or component ({entity}, {component})")
        return []

    patterns = load_all_patterns()
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
    matched.sort(key=lambda x: x["confidence"], reverse=True)
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
    signature_hash: str = Field(..., description="Pattern signature hash")
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
    sample_count: int = Field(..., description="Number of latency samples")
    latency_stats: Dict[str, Any] = Field(..., description="Min/max/avg/p95 latency in ms")
    sla_status: str = Field(..., description="'HEALTHY' or 'WARNING'")
    gold_tier_count: int = Field(..., description="Number of gold-tier patterns")
    avg_confidence: float = Field(..., description="Average confidence across KB")


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

    task_id: str = Field(..., description="Unique task identifier")
    entity: str = Field(..., description="Entity name (e.g., 'player', 'enemy_knight')")
    component: str = Field(..., description="Component name (e.g., 'HealthComponent')")
    context: Optional[dict] = Field(
        default=None,
        description="Additional context for the query (optional)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "task-001",
                "entity": "player",
                "component": "HealthComponent",
                "context": {"difficulty": "hard"}
            }
        }


class PatternResponse(BaseModel):
    """Pattern metadata in response."""

    id: str = Field(..., description="Pattern unique identifier")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    tier: str = Field(..., description="Confidence tier (gold, production, experimental, demoted)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "pattern_001",
                "confidence": 0.85,
                "tier": "gold"
            }
        }


class QueryResponse(BaseModel):
    """Response model for pattern query."""

    patterns: List[PatternResponse] = Field(..., description="List of matching patterns")
    query_latency_ms: float = Field(..., description="Query execution time in milliseconds")
    source: str = Field(default="kb", description="Source of patterns (kb, cache, etc.)")

    class Config:
        json_schema_extra = {
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

@router.post("/record", status_code=status.HTTP_202_ACCEPTED)
async def record_task_outcome(request: RecordRequest, background_tasks: BackgroundTasks) -> RecordResponse:
    """
    Record a task outcome and trigger learning delta updates (fire-and-forget).

    Validates schema_version == "1.0", then spawns async or sync KB update based on
    whether any active pattern has confidence < 0.50 (critical threshold).

    Args:
        request: RecordRequest with task_id, status, timestamp, and akc_context.

    Returns:
        RecordResponse 202 Accepted (fire-and-forget; update happens in background).

    Raises:
        HTTPException 400: If schema_version != "1.0" or status invalid.
        HTTPException 500: If serialization or subprocess spawn fails.
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

        # Dispatch learning delta update in background
        background_tasks.add_task(apply_confidence_delta, task_result)

        # Determine update mode (async vs sync) based on active patterns
        # For now, default to async; trigger_learning_delta will handle routing
        update_mode = "async"
        active_patterns = request.akc_context.get("knowledge_patterns_active", [])
        patterns_to_update = len(active_patterns)

        # Log outcome recording (actual KB update happens asynchronously)
        logger.info(
            f"record_task_outcome: accepted task={request.task_id}, "
            f"patterns_to_update={patterns_to_update}"
        )

        response = RecordResponse(
            accepted=True,
            task_id=request.task_id,
            update_mode=update_mode,
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
    Retrieve fix recommendations for a pattern by signature hash and category.

    Loads all patterns from KB, filters by category, and returns matching fixes.

    Args:
        request: FixRequest with signature_hash and category (enum of 5 categories).

    Returns:
        FixResponse with list of fixes matching the category.

    Raises:
        HTTPException 404: If no patterns match the category.
        HTTPException 400: If category is invalid.
        HTTPException 500: If pattern loading fails.
    """
    try:
        valid_categories = {"detection", "implementation", "testing", "documentation", "other"}
        if request.category not in valid_categories:
            logger.warning(
                f"get_pattern_fixes: invalid category={request.category}, "
                f"hash={request.signature_hash}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"category must be one of {valid_categories}, got '{request.category}'"
            )

        logger.info(
            f"get_pattern_fixes: hash={request.signature_hash}, category={request.category}"
        )

        # Load all patterns
        all_patterns = load_all_patterns()
        if not all_patterns:
            logger.warning("get_pattern_fixes: no patterns in KB")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No patterns found matching category '{request.category}'"
            )

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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No fixes found for category '{request.category}'"
            )

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
            f"get_pattern_fixes: hash={request.signature_hash} failed - {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pattern fixes: {str(e)}"
        )


# ─── KB Statistics Endpoint ────────────────────────────────────────────────

@router.get("/stats")
async def get_kb_stats(time_window: str = "all") -> StatsResponse:
    """
    Retrieve KB statistics: latency compliance, gold-tier count, average confidence.

    Calls check_latency() from learning_integration and loads patterns for tier analysis.

    Args:
        time_window: Optional query param "all", "24h", "7d", or "30d" (not yet filtered).

    Returns:
        StatsResponse with latency stats, SLA status, and tier metrics.

    Raises:
        HTTPException 500: If stats collection fails.
    """
    try:
        valid_windows = {"all", "24h", "7d", "30d"}
        if time_window not in valid_windows:
            logger.warning(f"get_kb_stats: invalid time_window={time_window}")
            # Silently default to "all" rather than fail
            time_window = "all"

        logger.info(f"get_kb_stats: time_window={time_window}")

        # Get latency stats from learning_integration
        latency_data = check_latency()
        latency_stats = latency_data.get("latency_stats", {})
        sla_status = latency_data.get("sla_status", "UNKNOWN")

        # Load patterns and compute tier statistics
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
            f"sample_count={latency_data.get('sample_count', 0)}"
        )

        response = StatsResponse(
            sample_count=latency_data.get("sample_count", 0),
            latency_stats=latency_stats,
            sla_status=sla_status,
            gold_tier_count=gold_tier_count,
            avg_confidence=round(avg_confidence, 4)
        )

        return response

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
