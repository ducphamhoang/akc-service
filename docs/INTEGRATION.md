# akc-service: Godot Integration Guide

## Overview

The **Godot Adapter** (`adapters/godot/`) bridges the Godot game engine and akc-service, enabling:
- Query pattern recommendations from orchestrator
- Record GDScript linting results
- Record test execution outcomes
- Handle service unavailability gracefully

## Setup

### Prerequisites

- Godot 4.6+ with GL Compatibility renderer
- Python 3.11+ (akc-service running)
- agent-system package installed
- HTTP client access to akc-service

### Installation

1. **Copy adapter to Godot project:**
   ```bash
   cp -r packages/akc-service/adapters/godot res://addons/akc_adapter/
   ```

2. **Enable plugin in Godot:**
   - Open `res://project.godot`
   - Edit or create `[autoload]` section:
     ```ini
     akc_adapter="res://addons/akc_adapter/akc_adapter.gd"
     ```
   - Restart Godot editor

3. **Configure .env:**
   ```bash
   cp packages/akc-service/.env.example packages/akc-service/.env
   # Edit .env:
   AKC_SERVICE_URL=http://localhost:8000
   AKC_SERVICE_SAFETY_LEVEL=1
   ```

4. **Start akc-service:**
   ```bash
   cd packages/akc-service
   uvicorn akc_service.api.main:app --port 8000
   ```

5. **Verify health:**
   ```bash
   curl http://localhost:8000/akc/v1/health
   ```

---

## GodotAKCAdapter API

### Class: GodotAKCAdapter

GDScript autoload providing HTTP integration with akc-service.

#### Initialization

```gdscript
# Automatically loaded as singleton: AkcAdapter
var adapter = AkcAdapter

# Or instantiate manually
var adapter = GodotAKCAdapter.new()
```

#### Configuration Properties

```gdscript
class_name GodotAKCAdapter
extends Node

# Base URL for akc-service
var service_url: String = "http://localhost:8000"

# Request timeout (seconds)
var timeout_sec: float = 0.15

# Enable debug logging
var debug: bool = false

# Fire-and-forget: return 202 (HTTP) without waiting for KB update
var fire_and_forget: bool = true
```

### Methods

#### query_patterns(task_id, entity, component, context = {}) -> Array

Query knowledge base for patterns matching entity:component.

**Parameters:**
- `task_id` (String): Unique task identifier
- `entity` (String): Entity name (e.g., "player")
- `component` (String): Component name (e.g., "HealthComponent")
- `context` (Dictionary, optional): Additional context (difficulty, phase, etc.)

**Returns:** Array of pattern dictionaries or empty array on failure

**Example:**
```gdscript
func _ready():
    var patterns = await AkcAdapter.query_patterns(
        "task-001",
        "player",
        "HealthComponent",
        {"difficulty": "hard"}
    )
    
    if patterns.is_empty():
        print("No patterns available, proceeding without AKC")
        return
    
    for pattern in patterns:
        print("Pattern %s: confidence=%.2f tier=%s" % [
            pattern["id"],
            pattern["confidence"],
            pattern["tier"]
        ])
```

#### record_lint_results(lint_data) -> bool

Record GDScript linting results (fire-and-forget, HTTP 202).

**Parameters:**
- `lint_data` (Dictionary): Linting results

**Returns:** True if accepted, false on error

**Lint data structure:**
```gdscript
var lint_data = {
    "schema_version": "1.0",
    "task_id": "lint-player-health",
    "status": "success",  # "success" or "failed"
    "timestamp": Time.get_ticks_msec(),
    "akc_context": {
        "knowledge_patterns_active": [
            {"id": "pattern_001", "confidence": 0.85}
        ],
        "lint_results": {
            "errors": 0,
            "warnings": 2,
            "checked_files": ["res://scenes/player.gd", "res://scripts/health.gd"]
        }
    }
}
```

**Example:**
```gdscript
func lint_player_script():
    var lint_results = GDScript.lint("res://scenes/player.gd")
    
    var accepted = await AkcAdapter.record_lint_results({
        "schema_version": "1.0",
        "task_id": "lint-player",
        "status": "success" if lint_results.is_empty() else "failed",
        "timestamp": Time.get_ticks_msec(),
        "akc_context": {
            "knowledge_patterns_active": [],
            "lint_results": {
                "errors": lint_results.size(),
                "warnings": 0,
                "checked_files": ["res://scenes/player.gd"]
            }
        }
    })
    
    if accepted:
        print("Lint results recorded to AKC")
    else:
        print("Failed to record lint results (service unavailable?)")
```

#### record_test_results(test_data) -> bool

Record test execution results (fire-and-forget, HTTP 202).

**Parameters:**
- `test_data` (Dictionary): Test execution results

**Returns:** True if accepted, false on error

**Test data structure:**
```gdscript
var test_data = {
    "schema_version": "1.0",
    "task_id": "test-player-health-v1",
    "status": "success",  # "success" or "failed"
    "timestamp": Time.get_ticks_msec(),
    "akc_context": {
        "knowledge_patterns_active": [
            {"id": "pattern_001", "confidence": 0.85}
        ],
        "test_results": {
            "total": 10,
            "passed": 9,
            "failed": 1,
            "duration_sec": 2.5,
            "test_suite": "player_health_tests"
        }
    }
}
```

**Example:**
```gdscript
func run_health_tests():
    var start_time = Time.get_ticks_msec()
    var tests = [
        {"name": "test_spawn_with_full_health", "passed": true},
        {"name": "test_take_damage", "passed": true},
        {"name": "test_heal", "passed": true},
        # ... more tests
    ]
    
    var passed = tests.filter(func(t): return t["passed"]).size()
    var total = tests.size()
    var elapsed = (Time.get_ticks_msec() - start_time) / 1000.0
    
    var accepted = await AkcAdapter.record_test_results({
        "schema_version": "1.0",
        "task_id": "test-player-health-v1",
        "status": "success" if passed == total else "failed",
        "timestamp": Time.get_ticks_msec(),
        "akc_context": {
            "knowledge_patterns_active": [],  # Patterns used during test
            "test_results": {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "duration_sec": elapsed,
                "test_suite": "player_health_tests"
            }
        }
    })
    
    print("Tests: %d/%d passed (%.2fs)" % [passed, total, elapsed])
```

#### get_stats() -> Dictionary

Fetch knowledge base statistics.

**Returns:** Dictionary with stats or empty dict on error

**Example:**
```gdscript
func check_kb_health():
    var stats = await AkcAdapter.get_stats()
    
    if stats.is_empty():
        print("Could not fetch KB stats (service unavailable)")
        return
    
    print("KB Statistics:")
    print("  Sample count: %d" % stats["sample_count"])
    print("  Avg latency: %.2fms" % stats["latency_stats"]["avg_ms"])
    print("  SLA status: %s" % stats["sla_status"])
    print("  Gold-tier patterns: %d" % stats["gold_tier_count"])
    print("  Avg confidence: %.2f" % stats["avg_confidence"])
```

---

## Integration Patterns

### Pattern 1: Orchestrator Query

Query patterns at task start, use for decision-making.

```gdscript
# In Orchestrator agent
func execute_task(entity: String, component: String):
    var task_id = "task-%d" % randi()
    
    # Query patterns
    var patterns = await AkcAdapter.query_patterns(
        task_id,
        entity,
        component,
        {"difficulty": get_difficulty()}
    )
    
    if patterns.is_empty():
        print("No patterns available, using default behavior")
        return execute_default(entity, component)
    
    # Use top pattern (highest confidence)
    var top_pattern = patterns[0]
    print("Using pattern %s (confidence=%.2f)" % [
        top_pattern["id"],
        top_pattern["confidence"]
    ])
    
    # Store active patterns for outcome recording
    _active_patterns = patterns
    
    # Execute task with pattern
    return execute_with_pattern(entity, component, top_pattern)
```

### Pattern 2: Outcome Recording

Record task success/failure to update pattern confidence.

```gdscript
# In Orchestrator agent
func record_task_outcome(task_id: String, success: bool):
    var status = "success" if success else "failed"
    
    var result = await AkcAdapter.record_outcome(
        task_id,
        status,
        _active_patterns,  # Patterns that were active
        []                 # Fixes applied (if any)
    )
    
    if result:
        print("Outcome recorded: task=%s status=%s" % [task_id, status])
    else:
        print("Failed to record outcome (service unavailable)")
```

### Pattern 3: Graceful Degradation

Handle service unavailability without crashing.

```gdscript
func query_patterns_with_fallback(entity: String, component: String) -> Array:
    var patterns = await AkcAdapter.query_patterns(
        "task-%d" % randi(),
        entity,
        component
    )
    
    if patterns.is_empty():
        print("AKC service unavailable or no patterns found, using defaults")
        return get_default_patterns(entity, component)
    
    return patterns


func get_default_patterns(entity: String, component: String) -> Array:
    # Hardcoded safe defaults when AKC unavailable
    match entity:
        "player":
            match component:
                "HealthComponent":
                    return [{"id": "default_health", "confidence": 0.50, "tier": "experimental"}]
                "MovementComponent":
                    return [{"id": "default_movement", "confidence": 0.60, "tier": "experimental"}]
        "enemy_knight":
            return [{"id": "default_enemy", "confidence": 0.55, "tier": "experimental"}]
    
    return []
```

### Pattern 4: Batch Recording

Record multiple test results in a single batch.

```gdscript
func run_test_suite():
    var suite_results = []
    var suite_start = Time.get_ticks_msec()
    
    # Run all tests
    var tests = [
        run_player_tests(),
        run_enemy_tests(),
        run_physics_tests(),
    ]
    
    for test_result in tests:
        suite_results.append(test_result)
    
    var total_passed = suite_results.filter(func(t): return t["passed"]).size()
    var total_tests = suite_results.size()
    var elapsed = (Time.get_ticks_msec() - suite_start) / 1000.0
    
    # Record batch results
    await AkcAdapter.record_test_results({
        "schema_version": "1.0",
        "task_id": "test-suite-all",
        "status": "success" if total_passed == total_tests else "failed",
        "timestamp": Time.get_ticks_msec(),
        "akc_context": {
            "knowledge_patterns_active": [],
            "test_results": {
                "total": total_tests,
                "passed": total_passed,
                "failed": total_tests - total_passed,
                "duration_sec": elapsed,
                "test_suite": "full_suite",
                "test_details": suite_results
            }
        }
    })
```

---

## Fire-and-Forget Semantics

Recording endpoints (`record_lint_results`, `record_test_results`) use **HTTP 202 Accepted**.

**What this means:**
1. Client receives 202 immediately (< 10ms)
2. KB update happens asynchronously in background
3. No guarantee on completion time
4. If service crashes before update, outcome is lost
5. Caller should not wait for response (async operation)

**Example:**
```gdscript
# This returns immediately, update happens later
var accepted = await AkcAdapter.record_lint_results(lint_data)

if accepted:
    print("Lint results queued for KB update")
    # Safe to continue immediately
else:
    print("Service unavailable (expected behavior)")
    # Continue with default behavior
```

---

## Error Handling

### Network Errors

```gdscript
func query_with_error_handling(entity: String, component: String) -> Array:
    try:
        var patterns = await AkcAdapter.query_patterns(
            "task-001",
            entity,
            component
        )
        return patterns
    except:
        print("Network error querying AKC (service unavailable?)")
        return get_default_patterns(entity, component)
```

### Timeout Handling

```gdscript
# Adapter timeout is 150ms (configurable)
# If akc-service doesn't respond within 150ms:
# - Query patterns: returns empty array []
# - Record outcome: returns false
# - No exception thrown, graceful degradation
```

### Concurrent Requests

```gdscript
# Safe to make concurrent requests
var task1 = AkcAdapter.query_patterns("task-001", "player", "HealthComponent")
var task2 = AkcAdapter.query_patterns("task-002", "enemy", "CombatComponent")

var result1 = await task1
var result2 = await task2

print("Results: %d patterns, %d patterns" % [result1.size(), result2.size()])
```

---

## Testing the Adapter

### Unit Tests

```gdscript
# tests/test_akc_adapter.gd
extends GutTest

func test_query_patterns_success():
    var patterns = await AkcAdapter.query_patterns(
        "test-001",
        "player",
        "HealthComponent"
    )
    assert_is_not_empty(patterns)
    assert_has(patterns[0], "id")
    assert_has(patterns[0], "confidence")
    assert_has(patterns[0], "tier")

func test_query_patterns_missing_entity():
    var patterns = await AkcAdapter.query_patterns(
        "test-002",
        "",  # Empty entity
        "HealthComponent"
    )
    assert_is_empty(patterns, "Should return empty array on invalid input")

func test_record_lint_results():
    var accepted = await AkcAdapter.record_lint_results({
        "schema_version": "1.0",
        "task_id": "lint-test",
        "status": "success",
        "timestamp": Time.get_ticks_msec(),
        "akc_context": {
            "knowledge_patterns_active": [],
            "lint_results": {"errors": 0, "warnings": 0, "checked_files": []}
        }
    })
    assert_true(accepted)
```

### Integration Tests

```gdscript
# tests/test_akc_integration.gd
extends GutTest

func test_query_and_record_flow():
    # Query patterns
    var patterns = await AkcAdapter.query_patterns(
        "integration-001",
        "player",
        "HealthComponent"
    )
    
    # Record outcome
    var accepted = await AkcAdapter.record_outcome(
        "integration-001",
        "success",
        patterns,
        []
    )
    
    assert_true(accepted)

func test_graceful_degradation_on_service_down():
    # This test requires akc-service to be stopped
    var patterns = await AkcAdapter.query_patterns(
        "degradation-001",
        "player",
        "HealthComponent"
    )
    # Should return empty array, not crash
    assert_is_empty(patterns)
```

### Run Tests

```bash
# Using gut (GDScript unit test framework)
godot --headless --path /path/to/project -s res://addons/gut/gut_cmdline.gd

# Or via pytest (if akc-service tests are in Python)
pytest tests/test_akc_integration_e2e.py -v
```

---

## Performance Considerations

### Query Latency Budget

- **Query patterns**: < 50ms (p95)
- **Record outcome**: < 10ms (202 before DB write)
- **Get stats**: < 100ms

**Profile queries:**
```gdscript
var start = Time.get_ticks_msec()
var patterns = await AkcAdapter.query_patterns("task-001", "player", "HealthComponent")
var elapsed = Time.get_ticks_msec() - start
print("Query latency: %dms" % elapsed)
```

### Concurrent Requests

The adapter uses HTTP client pooling (single connection per service).

Safe concurrent queries:
```gdscript
var tasks = []
for i in range(10):
    tasks.append(AkcAdapter.query_patterns(
        "task-%d" % i,
        "player",
        "HealthComponent"
    ))

var results = await asyncio.gather(*tasks)  # Concurrent execution
```

### Caching (Phase 2)

Currently: No client-side caching  
Pattern life: Per-query (re-fetches each time)

Recommended caching strategy:
```gdscript
var _pattern_cache = {}
var _cache_ttl_sec = 30

func get_patterns_cached(entity: String, component: String) -> Array:
    var key = "%s:%s" % [entity, component]
    var cached = _pattern_cache.get(key)
    
    if cached and (Time.get_ticks_msec() - cached["timestamp"]) < _cache_ttl_sec * 1000:
        return cached["patterns"]
    
    var patterns = await AkcAdapter.query_patterns("task-001", entity, component)
    _pattern_cache[key] = {
        "patterns": patterns,
        "timestamp": Time.get_ticks_msec()
    }
    
    return patterns
```

---

## Debugging

### Enable Debug Logging

```gdscript
# In _ready() or startup
AkcAdapter.debug = true

# Or in GDScript
var adapter = GodotAKCAdapter.new()
adapter.debug = true
adapter.query_patterns("task-001", "player", "HealthComponent")
```

**Debug output:**
```
[AKC] → POST /akc/v1/query
[AKC] ← 200 OK (12.5ms)
[AKC] Patterns: {"patterns": [...], "query_latency_ms": 12.5, "source": "kb"}
```

### Check Service Health

```gdscript
func check_service_health():
    var response = await AkcAdapter._get_http_response(
        "/akc/v1/health",
        "GET",
        {}
    )
    
    if response == null:
        print("Service unavailable")
        return false
    
    print("Service response: %s" % response)
    return true
```

### Monitor Network Traffic

```bash
# Capture HTTP requests to akc-service
tcpdump -i lo 'tcp port 8000' -A

# Or use HTTP debugging proxy
mitmproxy -p 8001
# Then set AKC_SERVICE_URL=http://localhost:8001
```

---

## Related Documentation

- [CAPABILITIES.md](CAPABILITIES.md) — Component overview
- [API_REFERENCE.md](API_REFERENCE.md) — REST endpoints
- [CONFIGURATION.md](CONFIGURATION.md) — Environment setup
- [ERROR_HANDLING.md](ERROR_HANDLING.md) — Failure modes
- `adapters/godot/README.md` — Adapter implementation details
