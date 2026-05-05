# akc-service: Error Handling & Failure Modes

## Overview

This document describes how akc-service handles errors, failures, and edge cases—and what to do when things go wrong.

## Service Startup Failures

### Problem: "Port already in use"

**Error message:**
```
OSError: [Errno 48] Address already in use
```

**Diagnosis:**
```bash
# Find what's using port 8000
lsof -i :8000
# or
netstat -tulpn | grep :8000
```

**Solutions:**

1. **Kill existing process** (if safe):
   ```bash
   kill -9 <PID>
   ```

2. **Use different port:**
   ```bash
   uvicorn akc_service.api.main:app --port 8001
   ```

3. **Wait for socket cleanup:**
   ```bash
   # TIME_WAIT state can persist for 60s
   sleep 70 && uvicorn akc_service.api.main:app --port 8000
   ```

### Problem: "Could not load knowledge base"

**Error message:**
```
ERROR: Could not open patterns.jsonl: Permission denied
```

**Diagnosis:**
```bash
# Check KB directory exists
ls -la $AKC_SERVICE_KB_DIR

# Check permissions
stat $AKC_SERVICE_KB_DIR
```

**Solutions:**

1. **Create KB directory:**
   ```bash
   mkdir -p $AKC_SERVICE_KB_DIR
   chmod 755 $AKC_SERVICE_KB_DIR
   ```

2. **Fix ownership:**
   ```bash
   # If running as different user
   chown -R appuser:appgroup $AKC_SERVICE_KB_DIR
   ```

3. **Check disk space:**
   ```bash
   df -h $AKC_SERVICE_KB_DIR
   # Need at least 1GB free
   ```

### Problem: "ModuleNotFoundError"

**Error message:**
```
ModuleNotFoundError: No module named 'fastapi'
```

**Diagnosis:**
```bash
python -c "import fastapi; print(fastapi.__version__)"
```

**Solutions:**

1. **Install dependencies:**
   ```bash
   pip install -e ".[test]"
   ```

2. **Check virtualenv:**
   ```bash
   which python
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

---

## Database Corruption & Recovery

### Problem: "patterns.jsonl is corrupted"

**Symptoms:**
- Queries return no patterns
- "ValueError: JSON decode error at line X"
- Service crashes on startup

**Diagnosis:**

```bash
# Check if file is valid JSON
python -c "
import json
with open('kb/patterns.jsonl') as f:
    for i, line in enumerate(f, 1):
        try:
            json.loads(line.strip())
        except json.JSONDecodeError as e:
            print(f'Line {i}: {e}')
"
```

**Solutions:**

1. **Recover valid lines:**
   ```bash
   # Extract valid JSON lines only
   python -c "
   import json
   valid_count = 0
   with open('kb/patterns.jsonl') as f_in:
       with open('kb/patterns.jsonl.recovered') as f_out:
           for line in f_in:
               try:
                   json.loads(line.strip())
                   f_out.write(line)
                   valid_count += 1
               except:
                   pass
   print(f'Recovered {valid_count} valid patterns')
   "
   mv kb/patterns.jsonl kb/patterns.jsonl.corrupted
   mv kb/patterns.jsonl.recovered kb/patterns.jsonl
   ```

2. **Restore from backup:**
   ```bash
   if [ -f kb/patterns.jsonl.backup ]; then
       cp kb/patterns.jsonl.backup kb/patterns.jsonl
   fi
   ```

3. **Start fresh** (if no backup):
   ```bash
   # Delete corrupted file and let service recreate
   rm kb/patterns.jsonl
   # Next append will create a fresh file
   ```

### Problem: "confidence_history.jsonl partially written"

**Symptoms:**
- Last line is incomplete
- Service crashes on history update

**Diagnosis:**
```bash
# Check last line is complete JSON
tail -1 kb/confidence_history.jsonl | python -m json.tool
```

**Solutions:**

1. **Trim incomplete line:**
   ```bash
   # Remove last incomplete line
   head -n -1 kb/confidence_history.jsonl > kb/confidence_history.jsonl.tmp
   mv kb/confidence_history.jsonl.tmp kb/confidence_history.jsonl
   ```

2. **Rebuild from patterns:**
   ```bash
   # Extract confidence updates from pattern versions
   python akc_service/learning_engine.py --rebuild-history
   ```

---

## Concurrent Access Issues

### Problem: "File is locked (fcntl error)"

**Error message:**
```
OSError: [Errno 11] Resource temporarily unavailable
```

**Symptoms:**
- Random "lock not available" errors
- Only happens under high load

**Diagnosis:**
```bash
# Check for stale lock files
ls -la kb/patterns.jsonl.lock* kb/confidence_history.jsonl.lock*

# Check for hung processes
ps aux | grep -E 'uvicorn|python.*akc_service' | grep -v grep
```

**Solutions:**

1. **Remove stale locks** (if process is dead):
   ```bash
   rm -f kb/*.lock
   ```

2. **Increase lock timeout:**
   ```bash
   # In learning_integration.py
   LOCK_TIMEOUT_SEC = 5  # Default is 1, increase for slower storage
   ```

3. **Reduce concurrent writes:**
   - Batch outcome recording (Phase 2)
   - Use read replicas for queries (Phase 3)

### Problem: "Last-write-wins conflict"

**When it happens:**
- Two processes write to same pattern simultaneously
- One write is overwritten (lost update)

**Risk factors:**
- Multiple akc-service instances without coordination
- High outcome recording rate (> 100 req/s)
- Slow disk I/O

**Prevention:**

1. **Single instance:**
   ```bash
   # Run only one akc-service instance
   # OR use distributed locking (Phase 3)
   ```

2. **Read-before-write locking:**
   ```python
   # In learning_integration.py:
   with open(PATTERNS_PATH, 'r+') as f:
       fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
       patterns = json.loads(f.read())
       # ... modify ...
       f.seek(0)
       f.truncate()
       json.dump(patterns, f)
   ```

### Problem: "Knowledge base file too large"

**Symptoms:**
- Append operation slow (> 100ms)
- Memory usage high (> 1GB)
- Load_all_patterns() times out

**Diagnosis:**
```bash
# Check KB file size
ls -lh kb/patterns.jsonl

# Count patterns
wc -l kb/patterns.jsonl

# Check disk usage
du -sh kb/
```

**Solutions:**

1. **Archive old patterns:**
   ```bash
   # Keep only patterns updated in last 6 months
   python -c "
   import json
   from datetime import datetime, timedelta
   cutoff = (datetime.now() - timedelta(days=180)).isoformat()
   
   with open('kb/patterns.jsonl') as f_in:
       with open('kb/patterns.jsonl.new') as f_out:
           for line in f_in:
               p = json.loads(line)
               if p.get('updated_at', '') >= cutoff:
                   f_out.write(line)
   "
   mv kb/patterns.jsonl kb/patterns.jsonl.full
   mv kb/patterns.jsonl.new kb/patterns.jsonl
   ```

2. **Compact history:**
   ```bash
   # Remove old confidence updates (keep only recent)
   head -n 100000 kb/confidence_history.jsonl > kb/confidence_history.jsonl.new
   mv kb/confidence_history.jsonl.new kb/confidence_history.jsonl
   ```

3. **Migrate to distributed KB** (Phase 3)

---

## Request/Response Errors

### Problem: "400 Bad Request"

**Common causes:**
- Missing required fields
- Invalid field type (string instead of number)
- Invalid status (not "success" or "failed")
- schema_version mismatch

**Error message:**
```json
{"error": "entity and component are required"}
```

**Diagnosis:**
```bash
# Check request format
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"invalid": "request"}' -v
```

**Solutions:**
1. **Validate request** before sending:
   ```python
   # Check Pydantic models in api/routes.py
   from akc_service.api.routes import QueryRequest
   
   try:
       req = QueryRequest(
           task_id="task-001",
           entity="player",
           component="HealthComponent"
       )
   except ValidationError as e:
       print(f"Invalid request: {e}")
   ```

2. **Check schema_version in record requests:**
   ```bash
   # Must be exactly "1.0"
   "schema_version": "1.0"  # Correct
   "schema_version": "1"    # Wrong: not a string or wrong value
   ```

### Problem: "404 Not Found"

**Causes:**
- Pattern ID doesn't exist
- Category not in KB
- Endpoint typo

**Solutions:**
1. **Verify pattern ID:**
   ```bash
   grep '"id": "pattern_001"' kb/patterns.jsonl
   ```

2. **List valid categories:**
   ```bash
   grep '"category"' kb/patterns.jsonl | cut -d'"' -f4 | sort -u
   ```

### Problem: "500 Internal Server Error"

**Generic catch-all for server-side failures.**

**Diagnosis:**
```bash
# Check service logs
tail -50 /var/log/akc-service.log
# or via systemd
journalctl -u akc-service -n 50

# Check if service is responsive
curl http://localhost:8000/akc/v1/health
```

**Common root causes:**
1. **Disk full:**
   ```bash
   df -h
   # Need > 1GB free space
   ```

2. **Memory exhausted:**
   ```bash
   ps aux | grep uvicorn
   free -h
   ```

3. **Unhandled exception:**
   ```bash
   # Check logs for full stack trace
   # Look for "Traceback" or "Exception"
   ```

**Solutions:**
1. **Restart service:**
   ```bash
   systemctl restart akc-service
   ```

2. **Check system resources:**
   ```bash
   # CPU
   top -bn1 | head -20
   
   # Memory
   free -h
   
   # Disk
   df -h
   
   # Network
   netstat -tulpn | grep 8000
   ```

3. **Increase resource limits:**
   ```bash
   # In systemd service file
   [Service]
   MemoryLimit=2G
   TasksMax=100
   ```

---

## Latency & Timeout Issues

### Problem: "Query takes > 50ms"

**Symptoms:**
- Slow pattern retrieval
- SLA status = "WARNING"
- Client-side timeouts (default 150ms)

**Diagnosis:**
```bash
# Measure query latency
time curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"task_id":"test","entity":"player","component":"HealthComponent"}'

# Check service metrics
curl http://localhost:8000/akc/v1/stats | python -m json.tool
```

**Root causes & solutions:**

1. **Large KB file (> 100K patterns):**
   - Solution: Archive old patterns (see "File too large" section)

2. **Slow disk I/O:**
   ```bash
   # Check disk performance
   iostat -x 1 5
   # Look for high await time (> 10ms)
   
   # Solution: Upgrade to SSD, or use RAM disk for dev
   ```

3. **High CPU contention:**
   ```bash
   # Check CPU utilization
   top
   # Solution: Reduce other processes, increase CPU quota
   ```

4. **Network latency** (if remote service):
   ```bash
   ping -c 5 akc-service.example.com
   # Solution: Deploy closer, use CDN edge, increase timeout
   ```

### Problem: "Request timeout (client-side)"

**Error message:**
```
requests.exceptions.Timeout: <urlopen error timeout>
```

**Default client timeout: 150ms (5ms buffer above 50ms SLA)**

**Solutions:**

1. **Increase timeout:**
   ```python
   from agent_system.akc_http_client import AKCClient
   
   client = AKCClient(timeout_sec=0.5)  # 500ms instead of 150ms
   ```

2. **Reduce KB size** (fix root cause):
   ```bash
   # Archive patterns
   python akc_service/learning_engine.py --archive-old
   ```

3. **Enable query caching** (Phase 2):
   ```bash
   export AKC_SERVICE_CACHE_TTL=60  # Cache for 60s
   ```

---

## Data Loss Prevention

### Append-Only Design

The KB uses append-only files to prevent accidental data loss:

**Good:**
- Crashes don't corrupt data (can recover by re-reading)
- Easy to recover from failed writes (partial lines are skipped)
- Natural audit trail (all changes are logged)

**Tradeoff:**
- File grows indefinitely (archive old patterns)
- No random update (only append)

### Backup Strategy

**Recommended:**
```bash
# Daily backup to cloud storage
0 2 * * * cp -r /var/lib/akc-service/kb /backup/akc-$(date +%Y%m%d)
0 3 * * * aws s3 sync /backup/akc-$(date +%Y%m%d) s3://my-backups/akc/
```

**Restore from backup:**
```bash
# List available backups
ls /backup/

# Restore specific backup
cp -r /backup/akc-20260505/* /var/lib/akc-service/kb/

# Restart service
systemctl restart akc-service
```

---

## Memory Leaks

### Symptom: Memory usage grows over time

**Diagnosis:**
```bash
# Monitor memory every minute for 1 hour
watch -n 60 'ps aux | grep uvicorn | awk "{print \$6}"'

# Or plot with systemd
systemd-cgtop -n 60
```

**Root causes:**

1. **Pattern cache not cleared:**
   ```python
   # In learning_engine.py
   # Ensure _patterns_cache is cleared periodically
   @cache.ttl(300)  # 5-minute TTL
   def load_all_patterns():
       ...
   ```

2. **HTTP client connection pool leak:**
   ```python
   # Ensure requests session is closed
   session.close()
   ```

3. **Log file growing unbounded:**
   ```bash
   # Configure log rotation
   # In logrotate config:
   /var/log/akc-service.log {
       daily
       rotate 7
       compress
       delaycompress
   }
   ```

---

## Monitoring & Alerting

### Health Check Monitoring

```bash
#!/bin/bash
# Check service health every 30s
while true; do
    if curl -f http://localhost:8000/akc/v1/health > /dev/null 2>&1; then
        echo "[$(date)] OK"
    else
        echo "[$(date)] FAILED - restarting service"
        systemctl restart akc-service
    fi
    sleep 30
done
```

### Disk Space Alert

```bash
#!/bin/bash
USAGE=$(df -h $AKC_SERVICE_KB_DIR | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $USAGE -gt 80 ]; then
    echo "ALERT: KB directory $USAGE% full" | mail -s "AKC Disk Alert" ops@example.com
fi
```

### Latency Alert

```bash
#!/bin/bash
LATENCY=$(curl -w '%{time_total}' -o /dev/null -s \
    -X POST http://localhost:8000/akc/v1/stats)
if (( $(echo "$LATENCY > 0.1" | bc -l) )); then
    echo "ALERT: Stats query latency ${LATENCY}s (SLA: 100ms)" | mail -s "AKC Latency Alert" ops@example.com
fi
```

---

## Graceful Shutdown (SIGTERM)

The service handles SIGTERM cleanly:

```python
# In api/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown: close connections, flush buffers
    logger.info("AKC Service shutting down...")
```

**Graceful shutdown sequence:**
1. Stop accepting new requests
2. Wait for in-flight requests (up to 30s timeout)
3. Close database connections
4. Flush any pending writes
5. Exit

**Manual shutdown:**
```bash
kill -TERM <PID>
# Wait 30s for graceful shutdown
sleep 30
# If still running, force kill
kill -9 <PID>
```

---

## Performance Baseline

Expected performance under normal conditions:

| Operation | Latency | P95 | P99 |
|-----------|---------|-----|-----|
| Health check | 2-5ms | 5ms | 10ms |
| Query patterns (< 100 patterns) | 8-15ms | 20ms | 30ms |
| Query patterns (> 100 patterns) | 15-40ms | 45ms | 80ms |
| Record outcome (202 accept) | 3-8ms | 10ms | 15ms |
| Get stats | 30-50ms | 60ms | 100ms |
| Update confidence | 20-40ms | 50ms | 80ms |

**If worse than baseline:**
1. Check KB file size (`wc -l patterns.jsonl`)
2. Check disk I/O (`iostat -x 1 5`)
3. Check CPU usage (`top`)
4. Check available memory (`free -h`)

---

## Related Documentation

- [CAPABILITIES.md](CAPABILITIES.md) — Architecture and components
- [CONFIGURATION.md](CONFIGURATION.md) — Setup and tuning
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues
- [API_REFERENCE.md](API_REFERENCE.md) — REST endpoints
