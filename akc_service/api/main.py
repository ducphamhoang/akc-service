#!/usr/bin/env python3
"""
AKC Service REST API
Phase 1 — FastAPI foundation endpoints

Provides the boundary layer for AKC queries and health checks.
Integrates with orchestrator_hooks for pattern retrieval.
"""

import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .routes import router
from akc_service.config import LOG_LEVEL

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)


# ─── Request/Response Logging Middleware ───────────────────────────────────

class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all incoming requests and outgoing responses."""

    async def dispatch(self, request: Request, call_next):
        """Process request, log metadata, and response."""
        method = request.method
        path = request.url.path

        logger.info(f"→ {method} {path}")

        try:
            response = await call_next(request)
            logger.info(f"← {method} {path} {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"✗ {method} {path} error: {e}")
            raise


# ─── Lifespan Context Manager ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown context manager for the FastAPI app."""
    # Startup
    logger.info("AKC Service starting up...")
    logger.info(f"API version: {app.version}")
    logger.info(f"Title: {app.title}")

    yield

    # Shutdown
    logger.info("AKC Service shutting down...")


# ─── FastAPI Application ───────────────────────────────────────────────────

app = FastAPI(
    title="AKC Service",
    version="1.0",
    description="Agent Knowledge Collective REST API",
    lifespan=lifespan
)

# Add CORS middleware to allow localhost requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:*", "http://127.0.0.1", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request/response logging middleware
app.add_middleware(LoggingMiddleware)

# Register router with prefix
app.include_router(router)

from akc_service.api.sync_routes import router as sync_router
app.include_router(sync_router)


# ─── Health Check Endpoint ────────────────────────────────────────────────

@app.get("/akc/v1/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    Returns:
        dict with status and ISO8601 timestamp.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }


# ─── Global Exception Handlers ───────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    logger.error(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# ─── Startup Log ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting AKC Service on http://0.0.0.0:8000")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
