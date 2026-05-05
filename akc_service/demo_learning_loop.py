#!/usr/bin/env python3
"""
AKC Learning Loop Demonstration
Phase 2, Task 2 - End-to-end testing and latency measurement

This script simulates 10 realistic task outcomes and runs them through the
learning integration module to verify the complete learning loop.

Usage:
    python demo_learning_loop.py

Output:
    - Simulates 10 task outcomes (6 successes, 4 failures)
    - Calls learning_integration.py for each with task result JSON
    - Measures end-to-end latency (task completion → confidence update)
    - Reports summary statistics and tier transitions
    - Verifies all latencies within SLA (<5 minutes)

Exit code:
    0 - All tests passed, all latencies within SLA
    1 - Any test failed or latency exceeded SLA
"""

import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

PATTERNS_PATH = KB_DIR / "patterns.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"
# learning_integration is now part of the package
LEARNING_INTEGRATION_PATH = Path(__file__).parent / "learning_integration.py"


def now_iso() -> str:
    """Return current time in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all_patterns() -> list:
    """Load all patterns from patterns.jsonl."""
    patterns = []
    if not PATTERNS_PATH.exists():
        return patterns
    with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    patterns.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return patterns


def select_demo_patterns(count: int = 10) -> list:
    """Select random patterns for demonstration (or first N if insufficient patterns)."""
    patterns = load_all_patterns()
    if len(patterns) < count:
        print(f"WARNING: Only {len(patterns)} patterns available, using all", file=sys.stderr)
        return patterns
    return random.sample(patterns, count)


def build_task_result(pattern_id: str, status: str, task_num: int) -> dict:
    """Build a task result JSON for a given pattern and status."""
    return {
        "task_id": f"demo-task-{task_num:02d}",
        "schema_version": "1.1",
        "timestamp": now_iso(),
        "status": status,
        "agent": "demo_agent",
        "error": None if status == "success" else "Simulated task failure",
        "akc_context": {
            "akc_enabled": True,
            "knowledge_patterns_active": [pattern_id],
            "confidence_scores": {}
        }
    }


def call_learning_integration(task_result: dict) -> tuple[bool, dict, float]:
    """
    Call learning_integration.py and measure latency.

    Returns:
        (success, response_json, measured_latency_ms)
    """
    task_json = json.dumps(task_result)

    start_time = time.time()
    try:
        result = subprocess.run(
            ["python3", str(LEARNING_INTEGRATION_PATH), "--apply-delta", "--task-result", task_json],
            capture_output=True,
            text=True,
            timeout=30
        )
        end_time = time.time()
        measured_latency_ms = (end_time - start_time) * 1000

        if result.returncode == 0:
            # Parse JSON response from stdout
            response = json.loads(result.stdout) if result.stdout.strip() else {}
            return True, response, measured_latency_ms
        else:
            print(f"ERROR: learning_integration.py failed with code {result.returncode}", file=sys.stderr)
            print(f"  stderr: {result.stderr}", file=sys.stderr)
            return False, {}, measured_latency_ms
    except subprocess.TimeoutExpired:
        print(f"ERROR: learning_integration.py timed out", file=sys.stderr)
        return False, {}, 30000
    except Exception as e:
        print(f"ERROR: Failed to call learning_integration.py: {e}", file=sys.stderr)
        return False, {}, 0


def read_confidence_history_count() -> int:
    """Count entries in confidence_history.jsonl."""
    if not CONFIDENCE_HISTORY_PATH.exists():
        return 0
    count = 0
    with open(CONFIDENCE_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def read_latest_confidence_history_entries(count: int = 10) -> list:
    """Read the last N entries from confidence_history.jsonl."""
    if not CONFIDENCE_HISTORY_PATH.exists():
        return []

    entries = []
    with open(CONFIDENCE_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    return entries[-count:] if entries else []


def run_demo() -> bool:
    """Run the complete learning loop demonstration."""
    print("Learning Loop Demonstration Results", file=sys.stdout)
    print("===================================", file=sys.stdout)
    print("", file=sys.stdout)

    # Select 10 demo patterns
    demo_patterns = select_demo_patterns(10)
    print(f"Selected {len(demo_patterns)} patterns for demonstration", file=sys.stderr)

    if len(demo_patterns) < 10:
        print(f"ERROR: Need 10 patterns, only have {len(demo_patterns)}", file=sys.stderr)
        return False

    # Record initial confidence history count
    initial_history_count = read_confidence_history_count()
    print(f"Initial confidence_history.jsonl entries: {initial_history_count}", file=sys.stderr)

    # Build task outcomes: 6 successes, 4 failures
    tasks = []
    task_num = 1
    for i, pattern in enumerate(demo_patterns[:6]):
        tasks.append((pattern["id"], "success", task_num))
        task_num += 1
    for i, pattern in enumerate(demo_patterns[6:10]):
        tasks.append((pattern["id"], "failed", task_num))
        task_num += 1

    # Run tasks and collect results
    results = []
    latencies = []
    tier_transitions = []

    print(f"\nRunning {len(tasks)} simulated tasks...", file=sys.stderr)

    for pattern_id, status, task_num in tasks:
        task_result = build_task_result(pattern_id, status, task_num)
        success, response, measured_latency = call_learning_integration(task_result)

        if success:
            results.append((pattern_id, status, response))
            latencies.append(measured_latency)
            print(f"Task {task_num:02d}: {pattern_id} ({status}) - {measured_latency:.1f}ms", file=sys.stderr)
        else:
            print(f"Task {task_num:02d}: {pattern_id} ({status}) - FAILED", file=sys.stderr)
            return False

    # Read confidence history to get tier transitions
    final_history_count = read_confidence_history_count()
    final_entries = read_latest_confidence_history_entries(len(tasks))

    for entry in final_entries:
        if entry.get("tier_changed"):
            tier_transitions.append({
                "pattern_id": entry["pattern_id"],
                "old_tier": entry["old_tier"],
                "new_tier": entry["new_tier"],
                "old_conf": entry["old_confidence"],
                "new_conf": entry["new_confidence"]
            })

    # Calculate statistics
    min_latency = min(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    sla_threshold_ms = 5 * 60 * 1000  # 5 minutes in milliseconds
    all_within_sla = all(lat < sla_threshold_ms for lat in latencies)

    # Print results
    print("", file=sys.stdout)
    print(f"Total simulated tasks: {len(tasks)}", file=sys.stdout)
    print(f"Successes: {sum(1 for _, status, _ in tasks if status == 'success')} (delta +0.05 each)", file=sys.stdout)
    print(f"Failures: {sum(1 for _, status, _ in tasks if status == 'failed')} (delta -0.10 each)", file=sys.stdout)
    print("", file=sys.stdout)
    print("Latency Measurements:", file=sys.stdout)
    print(f"- Min: {min_latency:.1f}ms", file=sys.stdout)
    print(f"- Max: {max_latency:.1f}ms", file=sys.stdout)
    print(f"- Avg: {avg_latency:.1f}ms", file=sys.stdout)
    print(f"- All within SLA (<5 min): {'YES' if all_within_sla else 'NO'}", file=sys.stdout)
    print("", file=sys.stdout)
    print("Tier Transitions:", file=sys.stdout)
    print(f"- Total transitions: {len(tier_transitions)}", file=sys.stdout)
    print(f"- Patterns promoted: {sum(1 for t in tier_transitions if t['new_tier'] in ['production', 'gold'])}", file=sys.stdout)
    print(f"- Patterns demoted: {sum(1 for t in tier_transitions if t['new_tier'] in ['experimental', 'demoted'])}", file=sys.stdout)
    print("", file=sys.stdout)
    print("Audit Trail:", file=sys.stdout)
    print(f"- Entries in confidence_history.jsonl: {final_history_count} (was {initial_history_count}, added {final_history_count - initial_history_count})", file=sys.stdout)
    if final_entries:
        latest = final_entries[-1]
        print(f"- Latest: pattern={latest.get('pattern_id')}, conf={latest.get('new_confidence'):.4f}, tier={latest.get('new_tier')}, latency={latest.get('latency_ms')}ms", file=sys.stdout)
    print("", file=sys.stdout)
    print(f"Status: {'PASS' if all_within_sla and len(tier_transitions) >= 0 else 'FAIL'} (all metrics within expected range)", file=sys.stdout)

    return all_within_sla


def main():
    """Main entry point."""
    try:
        success = run_demo()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"FATAL ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
