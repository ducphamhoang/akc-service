# akc-service

Agent Knowledge Collective: learning, safety, and metrics engine.
Exposes a REST API for agent pattern queries and outcome recording.

## Quick Start

### Installation

```bash
pip install -e ".[test]"
```

### Start the Server

```bash
uvicorn akc_service.api.main:app --port 8000
```

### Verify Health

```bash
curl http://localhost:8000/akc/v1/health
```

## Documentation

Comprehensive guides for every aspect of akc-service:

### Getting Started & Concepts

- **[CAPABILITIES.md](docs/CAPABILITIES.md)** — What akc-service does
  - Learning, safety, CSP, validation, monitoring engines
  - KB export to markdown & checkpoint reset features
  - REST API overview (12 endpoints)
  - System architecture & data flow
  - Knowledge base structure

### API Integration

- **[API_REFERENCE.md](docs/API_REFERENCE.md)** — Complete REST API documentation
  - All 12 endpoints with specs and examples
  - Request/response schemas (Pydantic models)
  - Status codes and error handling
  - curl and Python client examples
  - Rate limiting and timeout behavior

### Configuration & Operations

- **[CONFIGURATION.md](docs/CONFIGURATION.md)** — Setup and tuning guide
  - Environment variables (KB dir, safety level, port, etc.)
  - Performance tuning (latency, memory, disk I/O)
  - Docker/systemd deployment examples
  - Health checks and monitoring

- **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — Quick reference for common issues
  - "Service won't start", "High memory usage", "Slow queries"
  - Diagnosis steps and quick fixes (one-liners)
  - Performance baseline expectations

### Integration & Deployment

- **[INTEGRATION.md](docs/INTEGRATION.md)** — Godot engine adapter guide
  - GodotAKCAdapter setup and API
  - Pattern querying and result recording
  - Graceful degradation (fire-and-forget semantics)
  - Testing and concurrency handling

- **[ERROR_HANDLING.md](docs/ERROR_HANDLING.md)** — Failure modes and recovery
  - Startup failures, database corruption, concurrent access
  - Request/response errors and latency issues
  - Data loss prevention and backup strategies
  - Memory leaks and resource limits

## REST API Overview

| Method | Endpoint | Purpose | Latency |
|--------|----------|---------|---------|
| GET | `/akc/v1/health` | Service health check | < 5ms |
| POST | `/akc/v1/query` | Retrieve patterns for entity:component | < 50ms |
| POST | `/akc/v1/record` | Record task outcome (fire-and-forget, 202) | < 10ms |
| POST | `/akc/v1/fix` | Get fix recommendations by category | < 100ms |
| GET | `/akc/v1/stats` | KB statistics and SLA status | < 100ms |
| POST | `/akc/v1/update` | Manual confidence override | < 100ms |
| POST | `/akc/v1/reset` | Restore KB to startup checkpoint | < 500ms |
| POST | `/akc/v1/kb/export-markdown` | Export patterns to markdown files | < 2s |
| GET | `/akc/v1/sync/status` | Sync queue state and remote reachability | < 50ms |
| POST | `/akc/v1/sync/push` | Push patterns to remote KB | < 30s |
| POST | `/akc/v1/sync/pull` | Pull patterns from remote KB | < 30s |
| POST | `/akc/v1/sync/receive` | Accept patterns from remote node | < 100ms |

See [API_REFERENCE.md](docs/API_REFERENCE.md) for full endpoint documentation.

## Package Layout

```
akc_service/
  api/
    main.py               # FastAPI app, middleware, lifespan
    routes.py             # REST endpoints (12 routes)
    models.py             # Pydantic request/response schemas
  
  learning_engine.py      # Pattern storage, versioning, tier classification
  safety_engine.py        # Guardrail enforcement, escape hatches
  csp_solver.py           # Constraint satisfaction problem solver
  validation_engine.py    # Test generation, GDScript linting
  monitoring_engine.py    # Latency tracking, alerts
  kb_exporter.py          # KB export to markdown (by-entity, by-tier, by-pattern-type)
  
  learning_integration.py # Outcome delta application (async update)
  metrics_collector.py    # Time-series metrics
  failure_detection.py    # Anomaly detection
  
  kb/                     # Knowledge base (append-only files)
    patterns.jsonl        # Pattern records + versions
    confidence_history.jsonl
    fix_history.jsonl
    latency_samples.jsonl
    .akc_checkpoint/      # Startup snapshot for reset escape hatch

adapters/
  godot/                  # Godot engine integration
    README.md             # Godot adapter docs
    __init__.py
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AKC_SERVICE_KB_DIR` | `<package>/kb/` | Knowledge base directory |
| `AKC_SERVICE_REPO_ROOT` | cwd | Project root (for Godot tests) |
| `AKC_SERVICE_SAFETY_LEVEL` | 1 | Safety strictness (0=permissive, 1=standard, 2=strict) |
| `AKC_SERVICE_URL` | `http://localhost:8000` | Service URL for HTTP clients |

See [CONFIGURATION.md](docs/CONFIGURATION.md) for detailed environment setup.

## Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_akc_api_endpoints.py -v

# Run with coverage
pytest tests/ --cov=akc_service --cov-report=html
```

## Examples

### Python Client

```python
from agent_system.akc_http_client import AKCClient

client = AKCClient(base_url="http://localhost:8000", timeout_sec=0.15)

# Query patterns
patterns = client.query_patterns(
    task_id="task-042",
    entity="player",
    component="HealthComponent"
)
print(f"Found {len(patterns)} patterns")

# Record outcome
result = client.record_outcome(
    task_id="task-042",
    status="success",
    patterns_active=patterns
)
```

See [INTEGRATION.md](docs/INTEGRATION.md) for Godot adapter examples.

### curl Examples

```bash
# Query patterns
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"task_id":"task-001","entity":"player","component":"HealthComponent"}'

# Record outcome
curl -X POST http://localhost:8000/akc/v1/record \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "task_id": "task-001",
    "status": "success",
    "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'",
    "akc_context": {"knowledge_patterns_active": []}
  }'

# Get stats
curl http://localhost:8000/akc/v1/stats
```

See [API_REFERENCE.md](docs/API_REFERENCE.md) for complete endpoint specs.

## Getting Help

- **Common issues?** → [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- **Setup or tuning?** → [CONFIGURATION.md](docs/CONFIGURATION.md)
- **Integrating with Godot?** → [INTEGRATION.md](docs/INTEGRATION.md)
- **Strange errors?** → [ERROR_HANDLING.md](docs/ERROR_HANDLING.md)
- **API question?** → [API_REFERENCE.md](docs/API_REFERENCE.md)
