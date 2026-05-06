"""
Pydantic request and response models for AKC Service API.
Extracted from routes.py in Phase 3 (D-01).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ─── Task Outcome Recording ────────────────────────────────────────────────


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
    kb: Optional[str] = Field(None, description="Explicit KB name override (e.g., 'physics', 'animation'). Routes to default KB if absent or unknown.")


class RecordResponse(BaseModel):
    """Response model for task outcome recording."""
    accepted: bool = Field(..., description="Whether the record was accepted")
    task_id: str = Field(..., description="Echoed task identifier")
    update_mode: str = Field(..., description="'async' or 'sync'")
    patterns_to_update: int = Field(..., description="Number of patterns to update")
    timestamp: str = Field(..., description="Server timestamp")
    kb_used: str = Field(..., description="Resolved KB name used for this request")
    routing_tier: str = Field(..., description="Routing tier: explicit, entity_mapping, entity_wildcard, or fallback")


# ─── Pattern Fix Retrieval ─────────────────────────────────────────────────


class FixRequest(BaseModel):
    """Request model for pattern fix retrieval."""
    category: str = Field(..., description="Fix category: detection|implementation|testing|documentation|other")
    kb: Optional[str] = Field(None, description="Explicit KB name override")


class FixResponse(BaseModel):
    """Response model for pattern fixes."""
    fixes: List[Dict[str, Any]] = Field(..., description="List of matching fixes")
    category: str = Field(..., description="Echoed fix category")
    count: int = Field(..., description="Number of fixes returned")
    kb_used: str = Field(..., description="Resolved KB name used for this request")
    routing_tier: str = Field(..., description="Routing tier: explicit, entity_mapping, entity_wildcard, or fallback")


# ─── KB Statistics ─────────────────────────────────────────────────────────
# Note: StatsRequest is eliminated per D-03 — stats endpoint uses pure query params


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
    kb_used: str = Field(..., description="Resolved KB name used for this request")
    routing_tier: str = Field(..., description="Routing tier: explicit, entity_mapping, entity_wildcard, or fallback")
    kb_name: str = Field(..., description="KB name used for stats retrieval")


# ─── Confidence Override ───────────────────────────────────────────────────


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


# ─── Pattern Query ─────────────────────────────────────────────────────────


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
    kb: Optional[str] = Field(None, description="Explicit KB name override")


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
    kb_used: str = Field(..., description="Resolved KB name used for this request")
    routing_tier: str = Field(..., description="Routing tier: explicit, entity_mapping, entity_wildcard, or fallback")


# ─── KB Reset Escape Hatch ─────────────────────────────────────────────────


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


# ─── KB Markdown Export ────────────────────────────────────────────────────


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
