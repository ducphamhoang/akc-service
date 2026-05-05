import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from akc_service.learning_integration import load_all_patterns, append_pattern_version
from akc_service.sync.config import REMOTE_URL, REMOTE_API_KEY, REMOTE_TIMEOUT, MIN_CONFIDENCE, PUSH_BATCH, sync_enabled
from akc_service.sync.state import load_state
from akc_service.sync.push import push_to_remote
from akc_service.sync.pull import pull_from_remote

logger = logging.getLogger(__name__)

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))

router = APIRouter(prefix="/akc/v1/sync", tags=["sync"])


class SyncPushRequest(BaseModel):
    min_confidence: float = MIN_CONFIDENCE
    batch_size: int = PUSH_BATCH
    dry_run: bool = False


class SyncPullRequest(BaseModel):
    since: Optional[str] = None
    overwrite_local: bool = False
    dry_run: bool = False


class SyncReceiveRequest(BaseModel):
    patterns: list
    pushed_at: str


@router.get("/status")
async def sync_status():
    state = load_state(KB_DIR)
    reachable = False
    if sync_enabled():
        try:
            import httpx
            r = httpx.get(f"{REMOTE_URL}/akc/v1/health", timeout=3)
            reachable = r.status_code == 200
        except Exception:
            pass
    return {
        "remote_url": REMOTE_URL,
        "connected": sync_enabled(),
        "remote_reachable": reachable,
        "last_push_at": state.get("last_push_at"),
        "last_pull_at": state.get("last_pull_at"),
        "push_queue_size": state.get("push_queue_size", 0),
    }


@router.get("/export")
async def export_patterns(since: Optional[str] = None):
    all_patterns = load_all_patterns()
    if since:
        all_patterns = [p for p in all_patterns if p.get("updated_at", "") >= since]
    return {
        "patterns": all_patterns,
        "count": len(all_patterns),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.post("/push")
async def sync_push(request: SyncPushRequest):
    if not sync_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sync disabled: AKC_SERVICE_REMOTE_URL not set",
        )
    return push_to_remote(
        kb_dir=KB_DIR,
        remote_url=REMOTE_URL,
        api_key=REMOTE_API_KEY,
        min_confidence=request.min_confidence,
        batch_size=request.batch_size,
        dry_run=request.dry_run,
        timeout=REMOTE_TIMEOUT,
    )


@router.post("/pull")
async def sync_pull(request: SyncPullRequest):
    if not sync_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sync disabled: AKC_SERVICE_REMOTE_URL not set",
        )
    return pull_from_remote(
        kb_dir=KB_DIR,
        remote_url=REMOTE_URL,
        api_key=REMOTE_API_KEY,
        since=request.since,
        overwrite_local=request.overwrite_local,
        dry_run=request.dry_run,
        timeout=REMOTE_TIMEOUT,
    )


@router.post("/receive")
async def sync_receive(request: SyncReceiveRequest):
    accepted = 0
    for pattern in request.patterns:
        try:
            append_pattern_version(pattern)
            accepted += 1
        except Exception as e:
            logger.warning(f"sync_receive: failed to write pattern {pattern.get('id')}: {e}")
    return {"accepted": accepted, "total": len(request.patterns)}
