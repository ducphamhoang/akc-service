"""
Godot Plugin Example — AKC Service Integration
===============================================

This file demonstrates how a Godot plugin or external tool would integrate
with the AKC service REST API running locally.

USAGE PATTERN:
--------------

1. Start the AKC service (from packages/akc-service/):
       uvicorn akc_service.api.main:app --port 8000

2. From your Godot plugin / external client, import AKCClient:

       from akc_http_client import AKCClient

       client = AKCClient(base_url="http://127.0.0.1:8000")

3. Query patterns for an entity:component pair:

       result = client.query(
           task_id="task-42",
           entity="player",
           component="HealthComponent",
       )
       # Returns: {"patterns": [...], "query_latency_ms": 12.5, "source": "kb"}

4. Record a task outcome (fire-and-forget):

       client.record(
           task_id="task-42",
           status="success",
           timestamp="2026-05-04T10:00:00Z",
           akc_context={"knowledge_patterns_active": ["pat-001"]},
       )

5. Check health:

       health = client.health()
       # Returns: {"status": "healthy", "timestamp": "..."}

ENVIRONMENT:
------------
Set AKC_SERVICE_KB_DIR to point to your knowledge base directory:

    export AKC_SERVICE_KB_DIR=/path/to/your/kb

Set AKC_SERVICE_REPO_ROOT for Godot-specific paths (validation engine):

    export AKC_SERVICE_REPO_ROOT=/path/to/your/godot/project

NOTE: This file is documentation only. No HTTP calls are executed here.
"""
