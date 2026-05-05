# akc-service Configuration Guide

## Quick Start

### 1. Install

```bash
pip install -e ".[test]"
```

### 2. Set Environment Variables

```bash
export AKC_SERVICE_KB_DIR=./kb
export AKC_SERVICE_REPO_ROOT=$(pwd)
export AKC_SERVICE_SAFETY_LEVEL=1
export AKC_SERVICE_URL=http://localhost:8000
```

### 3. Run the Server

```bash
uvicorn akc_service.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Verify Health

```bash
curl http://localhost:8000/akc/v1/health
```

Expected response:
```json
{"status": "healthy", "timestamp": "2026-05-05T14:22:15Z"}
```

---

## Environment Variables

### Core Configuration

#### AKC_SERVICE_KB_DIR

**Type:** Path  
**Default:** `<package>/kb/`  
**Purpose:** Location of knowledge base files (patterns.jsonl, confidence_history.jsonl, etc.)

**Example:**
```bash
export AKC_SERVICE_KB_DIR=/var/lib/akc-service/kb
```

**Setup:**
```bash
mkdir -p /var/lib/akc-service/kb
chmod 755 /var/lib/akc-service/kb
```

**Validation:**
```bash
# Check if KB dir exists and is writable
test -d "$AKC_SERVICE_KB_DIR" && test -w "$AKC_SERVICE_KB_DIR" && echo "OK"
```

#### AKC_SERVICE_REPO_ROOT

**Type:** Path  
**Default:** Current working directory  
**Purpose:** Project root for Godot test generation (relative path resolution)

**Example:**
```bash
export AKC_SERVICE_REPO_ROOT=/home/dev/my-demon
```

**When to set:**
- Running akc-service from a different directory than the game project
- CI/CD pipelines with mounted filesystems

#### AKC_SERVICE_SAFETY_LEVEL

**Type:** Integer (0, 1, or 2)  
**Default:** 1  
**Purpose:** Safety strictness level for guardrail enforcement

**Values:**

| Level | Name | Behavior | Use Case |
|-------|------|----------|----------|
| 0 | Permissive | Allows confidence jumps, demoted auto-promotion | Development/testing |
| 1 | Standard | Enforces all 6 guardrails | Production |
| 2 | Strict | Guardrails + requires manual review for all updates | High-security environments |

**Example:**
```bash
export AKC_SERVICE_SAFETY_LEVEL=2  # Require manual review
```

#### AKC_SERVICE_URL

**Type:** URL  
**Default:** `http://localhost:8000`  
**Purpose:** Base URL for HTTP clients and external integrations

**Example:**
```bash
export AKC_SERVICE_URL=https://api.example.com/akc
```

**Used by:**
- Agent system HTTP client
- Godot adapter
- External integrations

---

## Performance Tuning

### Query Latency

The service targets **< 50ms per query** (p95).

#### Reducing Latency

1. **Increase KB size gracefully**
   ```python
   # Check pattern count
   wc -l $AKC_SERVICE_KB_DIR/patterns.jsonl
   
   # Archive old patterns
   mv patterns.jsonl patterns.jsonl.backup
   grep -v '"updated_at".*2025-' patterns.jsonl.backup > patterns.jsonl
   ```

2. **Use pattern caching** (Phase 2)
   ```bash
   export AKC_SERVICE_CACHE_TTL=60  # 1-minute pattern cache
   ```

3. **Run on faster storage**
   - Use SSD instead of network filesystem
   - Check I/O: `iostat -x 1 5`

4. **Profile bottlenecks**
   ```bash
   export AKC_SERVICE_DEBUG=1
   # Check logs for latency per component
   ```

### Memory Management

#### Knowledge Base Size

- **Small KB** (< 10K patterns): ~50MB RAM
- **Medium KB** (10K-100K patterns): ~500MB RAM
- **Large KB** (100K+ patterns): 1-5GB RAM

#### Monitoring Memory Usage

```bash
# Check resident memory
ps aux | grep uvicorn | awk '{print $6}'

# Monitor over time
watch -n 1 'ps aux | grep uvicorn | awk "{print \$6}"'
```

#### Memory Limits

Set resource limits for the service:

```bash
# Using systemd
[Service]
MemoryLimit=1G
MemoryAccounting=true

# Using ulimit (in shell)
ulimit -v 1000000  # 1GB virtual memory limit
```

#### Garbage Collection Tuning

```bash
# Enable Python GC optimization
export PYTHONGC=true
export PYTHONOPTIMIZE=2  # -OO: remove docstrings
```

### Database Tuning

#### KB File I/O

The append-only design avoids random I/O. Performance depends on sequential write speed.

```bash
# Benchmark KB disk performance
time cat $AKC_SERVICE_KB_DIR/patterns.jsonl > /dev/null

# Check disk utilization
iostat -x 1 5
```

#### File Locking Strategy

**Current:** fcntl locks (per-file locking)  
**Overhead:** < 1ms per operation

If experiencing lock contention:
1. Increase write batching
2. Use read replicas (Phase 3)
3. Migrate to distributed KB (Phase 4)

---

## Port Configuration

### Default Port (8000)

```bash
uvicorn akc_service.api.main:app --port 8000
```

### Custom Port

```bash
export AKC_SERVICE_PORT=8080
uvicorn akc_service.api.main:app --port $AKC_SERVICE_PORT
```

### Port Conflicts

If port 8000 is in use:

```bash
# Find what's using port 8000
lsof -i :8000

# Kill the process (if safe)
kill -9 <PID>

# Or use a different port
uvicorn akc_service.api.main:app --port 8001
```

### Firewall Configuration

**Development (localhost only):**
```bash
uvicorn akc_service.api.main:app --host 127.0.0.1 --port 8000
```

**Production (behind reverse proxy):**
```bash
uvicorn akc_service.api.main:app --host 127.0.0.1 --port 8000
# nginx/haproxy listens on :443 and forwards to :8000
```

**Kubernetes (all interfaces):**
```bash
uvicorn akc_service.api.main:app --host 0.0.0.0 --port 8000
```

---

## Logging Configuration

### Log Levels

**Environment variable:**
```bash
export AKC_SERVICE_LOG_LEVEL=INFO
# Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
```

**In Python:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Log Output

Default: stdout (stderr for errors)

Redirect to file:
```bash
uvicorn akc_service.api.main:app \
  --port 8000 \
  2>&1 | tee /var/log/akc-service.log
```

Or use systemd logging:
```bash
journalctl -u akc-service -f  # Follow logs
journalctl -u akc-service --since "1 hour ago"
```

### Structured Logging (Phase 2)

Currently: Text format  
Format: `%(asctime)s %(levelname)s [%(name)s] %(message)s`

Example log:
```
2026-05-05 14:22:15,123 INFO [akc_service.api.routes] → POST /akc/v1/query
2026-05-05 14:22:15,135 INFO [akc_service.api.routes] ← POST /akc/v1/query 200
```

---

## Safety Settings

### Guardrail Configuration

The 6 guardrails are hardcoded and cannot be disabled. However, safety_level affects interpretation:

#### Safety Level 0 (Permissive)

```python
# Allows:
- Confidence jumps up to 0.25
- Demoted patterns to auto-promote
- Concurrent modifications (with last-write-wins)

# Still prevents:
- Confidence > 1.0
- Negative confidence
- NaN/Inf values
```

#### Safety Level 1 (Standard, Default)

```python
# Enforces:
- Max confidence jump: 0.15
- Demoted patterns require manual review
- Concurrent modifications blocked
- All 6 guardrails strictly
```

#### Safety Level 2 (Strict)

```python
# Requires:
- Manual review for ANY confidence update
- All changes go to quarantine (safety_state.json)
- Ops team approval before KB modification
- Full audit trail for every change
```

### Escape Hatches

For emergency overrides, use the safety engine:

```bash
python -m akc_service.safety_engine --set-escape-hatch caution
# Options: caution, quarantine, re-validate, reset
```

**When to use:**
- `caution`: Lower safety checks temporarily
- `quarantine`: Hold all updates for manual review
- `re-validate`: Re-run validation on recent updates
- `reset`: Clear emergency state and resume normal operation

---

## Example Configurations

### Development (Local)

```bash
#!/bin/bash
# development.env
export AKC_SERVICE_KB_DIR=./kb
export AKC_SERVICE_REPO_ROOT=$(pwd)
export AKC_SERVICE_SAFETY_LEVEL=0    # Permissive
export AKC_SERVICE_LOG_LEVEL=DEBUG
export AKC_SERVICE_URL=http://localhost:8000

# Start server
uvicorn akc_service.api.main:app --port 8000 --reload
```

**Features:**
- Auto-reload on code changes
- Debug logging
- Permissive guardrails
- Local KB directory

### Testing (CI/CD)

```bash
#!/bin/bash
# testing.env
export AKC_SERVICE_KB_DIR=/tmp/akc-test-kb
export AKC_SERVICE_REPO_ROOT=$(pwd)
export AKC_SERVICE_SAFETY_LEVEL=1    # Standard
export AKC_SERVICE_LOG_LEVEL=WARNING
export AKC_SERVICE_URL=http://localhost:8000

# Create temp KB
mkdir -p $AKC_SERVICE_KB_DIR

# Run tests
pytest tests/ -v

# Cleanup
rm -rf $AKC_SERVICE_KB_DIR
```

**Features:**
- Isolated KB directory
- Standard guardrails
- Warnings only (faster output)
- Auto-cleanup

### Production (Server)

```bash
#!/bin/bash
# production.env
export AKC_SERVICE_KB_DIR=/var/lib/akc-service/kb
export AKC_SERVICE_REPO_ROOT=/opt/my-demon
export AKC_SERVICE_SAFETY_LEVEL=2    # Strict
export AKC_SERVICE_LOG_LEVEL=INFO
export AKC_SERVICE_URL=https://api.example.com/akc
export AKC_SERVICE_CACHE_TTL=300     # 5-minute cache

# Start with systemd
systemctl start akc-service

# Or with Docker
docker run -d \
  -e AKC_SERVICE_KB_DIR=/data/kb \
  -e AKC_SERVICE_SAFETY_LEVEL=2 \
  -v /var/lib/akc-service/kb:/data/kb \
  -p 8000:8000 \
  my-demon/akc-service:latest
```

**Features:**
- Strict guardrails (manual review required)
- Persistent KB directory
- Production safety level
- Systemd integration

---

## Docker Configuration

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml setup.py ./
RUN pip install --no-cache-dir -e .

# Copy source
COPY akc_service/ ./akc_service/
COPY adapters/ ./adapters/

# Create KB directory
RUN mkdir -p /data/kb

# Expose port
EXPOSE 8000

# Start server
CMD ["uvicorn", "akc_service.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  akc-service:
    build: .
    ports:
      - "8000:8000"
    environment:
      AKC_SERVICE_KB_DIR: /data/kb
      AKC_SERVICE_SAFETY_LEVEL: "1"
      AKC_SERVICE_LOG_LEVEL: INFO
    volumes:
      - akc-kb:/data/kb
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/akc/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  akc-kb:
```

Run:
```bash
docker-compose up -d
docker-compose logs -f akc-service
```

---

## Health Checks

### HTTP Health Check

```bash
curl -f http://localhost:8000/akc/v1/health && echo "OK" || echo "FAIL"
```

### Disk Space Check

```bash
# Alert if KB dir is > 90% full
USAGE=$(df -h $AKC_SERVICE_KB_DIR | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $USAGE -gt 90 ]; then
  echo "WARNING: KB directory $USAGE% full"
fi
```

### Memory Check

```bash
# Alert if service using > 1GB
MEMORY=$(ps aux | grep uvicorn | awk '{print $6}' | head -1)
if [ $MEMORY -gt 1000000 ]; then
  echo "WARNING: Service using $(( MEMORY / 1024 ))MB"
fi
```

### Response Time Check

```bash
# Measure query latency
TIME=$(curl -w '%{time_total}' -o /dev/null -s \
  -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"task_id":"test","entity":"player","component":"HealthComponent"}')
echo "Query latency: ${TIME}s"
```

---

## Sync Configuration

These variables control optional synchronisation with a remote akc-service instance.
All sync is disabled (and has zero overhead) when `AKC_SERVICE_REMOTE_URL` is not set.

### AKC_SERVICE_REMOTE_URL

**Type:** URL  
**Default:** `""` (sync disabled)  
**Purpose:** Base URL of the remote akc-service instance to sync with.

```bash
export AKC_SERVICE_REMOTE_URL=https://remote.example.com/akc
```

### AKC_SERVICE_REMOTE_API_KEY

**Type:** String  
**Default:** `""` (no auth)  
**Purpose:** Bearer token sent in `Authorization` header for remote calls.

### AKC_SERVICE_REMOTE_TIMEOUT

**Type:** Integer (seconds)  
**Default:** `10`  
**Purpose:** HTTP timeout for all outbound sync calls.

### AKC_SERVICE_SYNC_ON_STARTUP

**Type:** Boolean (`true`/`false`)  
**Default:** `false`  
**Purpose:** When `true`, the service pulls from the remote KB before accepting requests.

### AKC_SERVICE_SYNC_PUSH_BATCH

**Type:** Integer  
**Default:** `50`  
**Purpose:** Maximum number of patterns sent per push HTTP request.

### AKC_SERVICE_SYNC_MIN_CONFIDENCE

**Type:** Float  
**Default:** `0.70`  
**Purpose:** Patterns below this confidence threshold are excluded from push.

### CLI Usage

```bash
# Check sync state
akc-sync status

# Pull latest patterns from remote
akc-sync pull

# Push locally-learned patterns to remote
akc-sync push

# Preview what would be pushed
akc-sync push --dry-run

# Configure remote URL
akc-sync connect --url https://remote.example.com/akc --api-key <token>

# Clear push queue (e.g., after manual reconciliation)
akc-sync reset-queue
```

---

## Related Documentation

- [CAPABILITIES.md](CAPABILITIES.md) — Feature overview
- [API_REFERENCE.md](API_REFERENCE.md) — REST endpoints
- [INTEGRATION.md](INTEGRATION.md) — Godot setup
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues
- [ERROR_HANDLING.md](ERROR_HANDLING.md) — Failure modes
