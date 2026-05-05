# akc-service: Troubleshooting Guide

Quick reference for common issues and their solutions.

## "Service won't start"

### Symptoms
- `uvicorn` command hangs or exits immediately
- Port error or permission denied
- Module import error

### Debug Steps

1. **Check if port is free:**
   ```bash
   lsof -i :8000
   # If output, kill the process or use different port
   kill -9 <PID>
   ```

2. **Verify dependencies installed:**
   ```bash
   python -c "import fastapi, uvicorn, pydantic; print('OK')"
   # If fails: pip install -e ".[test]"
   ```

3. **Check KB directory exists:**
   ```bash
   test -d $AKC_SERVICE_KB_DIR && echo "OK" || mkdir -p $AKC_SERVICE_KB_DIR
   ```

4. **Run with full output:**
   ```bash
   uvicorn akc_service.api.main:app --port 8000 --log-level debug
   # Look for detailed error messages
   ```

### Quick Fixes

| Issue | Fix |
|-------|-----|
| Port 8000 in use | `uvicorn ... --port 8001` |
| ModuleNotFoundError | `pip install -e .` |
| Permission denied | `chmod 755 $AKC_SERVICE_KB_DIR` |
| KB dir missing | `mkdir -p $AKC_SERVICE_KB_DIR` |

---

## "High memory usage" (> 500MB)

### Diagnosis

```bash
# Check current memory
ps aux | grep uvicorn | awk '{print "Memory: " $6 " KB"}'

# Check KB file size
ls -lh kb/patterns.jsonl
wc -l kb/patterns.jsonl

# Monitor over time
watch -n 5 'ps aux | grep uvicorn | awk "{print \$6}"'
```

### Root Causes & Solutions

1. **Large KB file (> 50K patterns):**
   ```bash
   # Archive old patterns
   python -c "
   import json
   from datetime import datetime, timedelta
   
   cutoff = (datetime.now() - timedelta(days=90)).isoformat()
   with open('kb/patterns.jsonl') as f_in:
       with open('kb/patterns.jsonl.new') as f_out:
           for line in f_in:
               p = json.loads(line.strip())
               if p.get('updated_at', '') >= cutoff:
                   f_out.write(line)
   "
   mv kb/patterns.jsonl kb/patterns.jsonl.archive
   mv kb/patterns.jsonl.new kb/patterns.jsonl
   
   # Restart service
   systemctl restart akc-service
   ```

2. **Memory leak in Python process:**
   ```bash
   # Restart service (temporary fix)
   systemctl restart akc-service
   
   # Monitor memory growth
   ps aux | grep uvicorn | awk '{print $2}' | xargs -I {} watch -n 5 'ps aux | grep {} | tail -1'
   ```

3. **Connection pool not releasing:**
   ```bash
   # Check open connections
   lsof -p $(pgrep -f uvicorn) | wc -l
   
   # Increase connection timeout
   export AKC_SERVICE_CONNECTION_TIMEOUT=30
   ```

---

## "Slow query responses" (p95 > 50ms)

### Diagnosis

```bash
# Measure single query
time curl -s -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"task_id":"test","entity":"player","component":"HealthComponent"}' | jq '.query_latency_ms'

# Check SLA status
curl -s http://localhost:8000/akc/v1/stats | jq '.latency_stats'

# Check disk I/O performance
iostat -x 1 5
# Look for await > 10ms
```

### Root Causes & Solutions

| Cause | Check | Solution |
|-------|-------|----------|
| Large KB file | `wc -l kb/patterns.jsonl` | Archive old patterns |
| Slow disk | `iostat -x` (await > 10ms) | Use SSD, or RAM disk for dev |
| High CPU | `top` (CPU% > 80%) | Reduce other processes |
| Network (remote) | `ping akc-server` | Deploy closer, increase timeout |
| Insufficient memory | `free -h` (< 500MB free) | Reduce KB size, increase RAM |

### Quick Optimization

```bash
# 1. Check KB file size
ls -lh kb/patterns.jsonl

# 2. If > 100MB, archive old patterns
tail -n 10000 kb/patterns.jsonl > kb/patterns.jsonl.recent
mv kb/patterns.jsonl.recent kb/patterns.jsonl

# 3. Restart service
systemctl restart akc-service

# 4. Verify latency improved
curl http://localhost:8000/akc/v1/stats | jq '.latency_stats'
```

---

## "400 Bad Request"

### Symptoms
```json
{"error": "entity and component are required"}
```

### Common Causes & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `entity and component are required` | Missing fields | Add `"entity"` and `"component"` |
| `schema_version must be '1.0'` | Wrong schema version | Use exactly `"schema_version": "1.0"` |
| `status must be 'success' or 'failed'` | Invalid status | Use lowercase: "success" or "failed" |
| `category must be one of {...}` | Invalid category | Use: detection, implementation, testing, documentation, other |

### Validation Checklist

```bash
# Test /query endpoint
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-001",
    "entity": "player",
    "component": "HealthComponent"
  }'

# Test /record endpoint
curl -X POST http://localhost:8000/akc/v1/record \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "task_id": "test-001",
    "status": "success",
    "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'",
    "akc_context": {
      "knowledge_patterns_active": []
    }
  }'
```

---

## "502 Service Unavailable"

### Symptoms
- `502 Bad Gateway` from nginx/reverse proxy
- Service is running but not responding

### Debug Steps

```bash
# 1. Check if service is running
ps aux | grep uvicorn

# 2. Try direct connection (not through proxy)
curl http://localhost:8000/akc/v1/health

# 3. Check logs
tail -50 /var/log/akc-service.log
# or via systemd
journalctl -u akc-service -n 50

# 4. Check system resources
free -h
df -h
top -b -n1
```

### Quick Recovery

```bash
# 1. Restart service
systemctl restart akc-service

# 2. Wait for startup
sleep 2

# 3. Verify health
curl http://localhost:8000/akc/v1/health

# 4. If still failing, check logs for startup errors
journalctl -u akc-service -n 20
```

### Common Causes

| Cause | Fix |
|-------|-----|
| Disk full | `df -h` and free space |
| Memory exhausted | `free -h`, kill other processes |
| KB file corrupted | See "Database Corruption" below |
| Port conflict | Change port or kill conflicting process |
| Permission denied | Check ownership: `ls -la kb/` |

---

## "Connection refused"

### Symptoms
```
requests.exceptions.ConnectionError: [Errno 111] Connection refused
```

### Debug Steps

```bash
# 1. Verify service is running
ps aux | grep uvicorn | grep -v grep

# 2. Verify correct port
netstat -tulpn | grep 8000

# 3. Check hostname/IP
ping localhost
ping 127.0.0.1

# 4. Try direct connection
nc -zv localhost 8000
```

### Quick Fixes

| Issue | Fix |
|-------|-----|
| Service not running | `uvicorn akc_service.api.main:app --port 8000` |
| Wrong port | Check `$AKC_SERVICE_URL` |
| Firewall blocking | Check iptables, allow port 8000 |
| Listening on wrong interface | Use `--host 0.0.0.0` |

---

## "Database Corruption"

### Symptoms
- Service crashes on startup
- "JSON decode error" in logs
- Queries return no results

### Recovery Steps

```bash
# 1. Identify corrupted file
python -c "
import json
with open('kb/patterns.jsonl') as f:
    for i, line in enumerate(f, 1):
        try:
            json.loads(line.strip())
        except json.JSONDecodeError as e:
            print(f'Line {i}: {e}')
            print(f'Content: {line[:100]}...')
"

# 2. Extract valid lines
python -c "
import json
valid = 0
with open('kb/patterns.jsonl') as f_in:
    with open('kb/patterns.jsonl.valid') as f_out:
        for line in f_in:
            try:
                json.loads(line.strip())
                f_out.write(line)
                valid += 1
            except:
                pass
print(f'Recovered {valid} valid patterns')
"

# 3. Replace corrupted file
mv kb/patterns.jsonl kb/patterns.jsonl.corrupted
mv kb/patterns.jsonl.valid kb/patterns.jsonl

# 4. Restart service
systemctl restart akc-service
```

---

## "No patterns returned"

### Symptoms
- `/query` returns empty array
- SLA status = "UNKNOWN"

### Debug Steps

```bash
# 1. Check KB file exists and has content
ls -lh kb/patterns.jsonl
wc -l kb/patterns.jsonl

# 2. Check file is readable
python -c "
import json
with open('kb/patterns.jsonl') as f:
    patterns = [json.loads(line) for line in f if line.strip()]
    print(f'Total patterns: {len(patterns)}')
    if patterns:
        print(f'First pattern: {patterns[0]}')
"

# 3. Test query manually
curl -X POST http://localhost:8000/akc/v1/query \
  -H "Content-Type: application/json" \
  -d '{"task_id":"test","entity":"player","component":"HealthComponent"}' | jq

# 4. Check if entity:component exists in KB
python -c "
import json
with open('kb/patterns.jsonl') as f:
    for line in f:
        p = json.loads(line)
        if p.get('entity') == 'player' and p.get('component') == 'HealthComponent':
            print('Found:', p['id'])
            break
"
```

### Solutions

1. **KB file empty:**
   ```bash
   # Seed KB with initial patterns
   python akc_service/seed_kb.py
   ```

2. **Entity:component not in KB:**
   ```bash
   # Check valid combinations
   grep '"entity"' kb/patterns.jsonl | cut -d'"' -f4 | sort -u
   grep '"component"' kb/patterns.jsonl | cut -d'"' -f4 | sort -u
   ```

3. **All patterns demoted:**
   ```bash
   # Check confidence distribution
   python -c "
   import json
   with open('kb/patterns.jsonl') as f:
       patterns = [json.loads(line) for line in f if line.strip()]
       tiers = {}
       for p in patterns:
           tier = p.get('confidence_tier', 'unknown')
           tiers[tier] = tiers.get(tier, 0) + 1
       print('Tier distribution:', tiers)
   "
   ```

---

## "High error rate in tests"

### Symptoms
- Multiple test failures
- Flaky tests (pass sometimes, fail sometimes)

### Debug Steps

```bash
# 1. Run tests with verbose output
pytest tests/ -v -s

# 2. Check if service is running
ps aux | grep uvicorn

# 3. Check service health
curl http://localhost:8000/akc/v1/health

# 4. Check logs for errors
tail -100 /var/log/akc-service.log | grep -i error
```

### Common Causes

| Cause | Fix |
|-------|-----|
| Service not running | Start service before tests |
| Port 8000 in use | Kill conflicting process or use different port |
| KB directory missing | `mkdir -p $AKC_SERVICE_KB_DIR` |
| Corrupted KB file | Restore from backup or reset |
| Timeout (slow server) | Increase client timeout in tests |

### Isolated Test Run

```bash
# Run single test with debug output
pytest tests/test_akc_api_endpoints.py::TestQueryEndpoint::test_query_valid_input -vvv

# Run with timeout
pytest tests/ --timeout=30

# Run in isolation (fresh process per test)
pytest tests/ -p no:cacheprovider --forked
```

---

## "Service baseline expectations"

### Normal Performance

| Metric | Expected | Warning | Critical |
|--------|----------|---------|----------|
| Health check latency | < 5ms | > 10ms | > 20ms |
| Query latency | < 20ms | > 50ms | > 100ms |
| Record latency (202) | < 10ms | > 20ms | > 50ms |
| Memory usage | < 300MB | > 500MB | > 1GB |
| Disk usage (KB) | < 100MB | > 500MB | > 2GB |
| CPU usage | < 10% | > 50% | > 80% |
| Disk I/O (await) | < 2ms | > 10ms | > 30ms |

### Performance Tuning Checklist

```bash
#!/bin/bash
# Check all metrics

echo "=== System Metrics ==="
echo "Memory: $(free -h | awk '/^Mem/ {print $3 "/" $2}')"
echo "Disk: $(df -h $AKC_SERVICE_KB_DIR | awk 'NR==2 {print $3 "/" $2}')"
echo "CPU: $(top -bn1 | awk '/Cpu/ {print $2}')"

echo "=== AKC Metrics ==="
echo "KB file size: $(ls -lh kb/patterns.jsonl | awk '{print $5}')"
echo "Pattern count: $(wc -l < kb/patterns.jsonl)"

echo "=== Service Health ==="
curl -s http://localhost:8000/akc/v1/health | jq '.status'
curl -s http://localhost:8000/akc/v1/stats | jq '.latency_stats'
```

---

## "Getting help"

### Collect Debug Information

```bash
#!/bin/bash
# Create debug bundle
mkdir -p akc-debug
cd akc-debug

# Logs
tail -100 /var/log/akc-service.log > service.log 2>&1
journalctl -u akc-service -n 100 > systemd.log 2>&1

# System info
uname -a > system.txt
ps aux | grep uvicorn >> system.txt
free -h >> system.txt
df -h >> system.txt

# AKC info
ls -lh ../kb/ > kb-info.txt
wc -l ../kb/patterns.jsonl >> kb-info.txt
curl http://localhost:8000/akc/v1/health > health.json 2>&1
curl http://localhost:8000/akc/v1/stats > stats.json 2>&1

# Config
env | grep AKC > config.env

# Create tarball
cd ..
tar czf akc-debug.tar.gz akc-debug/
echo "Debug bundle: akc-debug.tar.gz"
```

### Report Format

Include:
1. **Error message** (full text)
2. **Steps to reproduce** (exact commands)
3. **Expected behavior**
4. **Actual behavior**
5. **Environment:**
   ```bash
   python --version
   pip list | grep -E 'fastapi|uvicorn|pydantic|requests'
   uname -a
   ```
6. **Debug logs** (from script above)

---

## Performance Baseline & Load Testing

### Load Test Script

```bash
#!/bin/bash
# Test service under load

echo "Starting load test..."

# Warm up
for i in {1..10}; do
    curl -s http://localhost:8000/akc/v1/query \
      -H "Content-Type: application/json" \
      -d '{"task_id":"warmup","entity":"player","component":"HealthComponent"}' > /dev/null
done

# Load test (100 concurrent queries)
echo "100 concurrent queries..."
for i in {1..100}; do
    (curl -w "%{time_total}\n" -o /dev/null -s \
      -X POST http://localhost:8000/akc/v1/query \
      -H "Content-Type: application/json" \
      -d '{"task_id":"load-'$i'","entity":"player","component":"HealthComponent"}') &
done
wait

# Check results
echo "Service health after load test:"
curl -s http://localhost:8000/akc/v1/stats | jq '.latency_stats'
```

---

## Related Documentation

- [ERROR_HANDLING.md](ERROR_HANDLING.md) — Detailed failure modes
- [CONFIGURATION.md](CONFIGURATION.md) — Environment setup
- [API_REFERENCE.md](API_REFERENCE.md) — REST endpoints
- [INTEGRATION.md](INTEGRATION.md) — Godot adapter issues
