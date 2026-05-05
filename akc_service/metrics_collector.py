#!/usr/bin/env python3
"""
AKC Metrics Collector
Phase 1, Wave 6 - Task 1.30

Collects and aggregates daily metrics for trend analysis.
Runs daily via cron or on-demand.

Usage:
    python metrics_collector.py                  # Collect today's metrics
    python metrics_collector.py --summary        # Show weekly/monthly summary
    python metrics_collector.py --trend          # Show trend analysis
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

# MetricsDB integration — optional; degrades gracefully if unavailable
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import metrics_db as _metrics_db
    _METRICSDB_AVAILABLE = True
except ImportError:
    _METRICSDB_AVAILABLE = False

METRICS_HISTORY_PATH = _REPO_ROOT / ".planning" / "METRICS_HISTORY.jsonl"

# sys.path manipulation removed — akc_service is now a proper package

# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_metrics_history() -> list:
    records = []
    if not METRICS_HISTORY_PATH.exists():
        return records
    with open(METRICS_HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def append_metrics(entry: dict) -> None:
    METRICS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def update_metrics_for_date(date_str: str, updates: dict) -> bool:
    """Update existing entry for a date or append new."""
    records = load_metrics_history()

    found = False
    new_records = []
    for r in records:
        if r.get("date") == date_str:
            r.update(updates)
            new_records.append(r)
            found = True
        else:
            new_records.append(r)

    if not found:
        return False

    METRICS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_HISTORY_PATH, "w", encoding="utf-8") as f:
        for r in new_records:
            f.write(json.dumps(r) + "\n")

    return True


# ─── Daily Collection ──────────────────────────────────────────────────────────

def collect_daily_metrics() -> dict:
    """
    Collect all 6+ metrics for today and append to METRICS_HISTORY.jsonl.

    Metrics collected:
    1. Task success rate
    2. Pattern count & distribution by tier
    3. Confidence histogram
    4. Rollback count
    5. Conflict count
    6. Learning speed (avg days to gold)
    """
    try:
        from akc_service import monitoring_engine
        monitor_data = monitoring_engine.run_monitor()
    except Exception as e:
        monitor_data = {"metrics": {}, "alerts": [], "error": str(e)}

    try:
        from akc_service import learning_engine
        kb_metrics = learning_engine.analyze_kb()
    except Exception as e:
        kb_metrics = {"error": str(e), "total_patterns": 0}

    metrics = monitor_data.get("metrics", {})
    tsr = metrics.get("task_success_rate", {})
    cd = metrics.get("confidence_distribution", {})
    rf = metrics.get("rollback_frequency", {})
    ac = metrics.get("active_conflicts", {})

    today = today_str()

    # Metric 3: confidence histogram (binned)
    confidence_histogram = _compute_confidence_histogram()

    # Metric 6: learning speed
    learning_speed = kb_metrics.get("learning_speed_days_avg", 0.0)

    entry = {
        "date": today,
        "timestamp": now_iso(),

        # Metric 1: Task success rate
        "task_success_rate": tsr.get("current", 0.0),
        "task_success_rate_7d": tsr.get("7day_rate", 0.0),
        "total_tasks": tsr.get("total_tasks", 0),
        "failed_tasks": tsr.get("failed_tasks", 0),

        # Metric 2: Pattern count & distribution
        "total_patterns": cd.get("total_patterns", 0),
        "patterns_by_tier": cd.get("by_tier", {}),

        # Metric 3: Confidence histogram
        "confidence_histogram": confidence_histogram,

        # Metric 4: Rollback count
        "rollbacks_today": rf.get("rollbacks_today", 0),
        "rollbacks_7d": rf.get("rollbacks_7d", 0),
        "rollback_rate": rf.get("rollback_rate_7d", 0.0),

        # Metric 5: Conflict count
        "active_conflicts": ac.get("conflict_count", 0),
        "high_severity_conflicts": ac.get("high_severity", 0),

        # Metric 6: Learning speed
        "learning_speed_days_avg": learning_speed,

        # KB health
        "kb_coverage_pct": kb_metrics.get("entity_component_coverage", {}).get("coverage_pct", 0.0),
        "all_kb_checks_pass": kb_metrics.get("all_checks_pass", False),

        # Alerts summary
        "alert_count": monitor_data.get("alert_count", 0),
        "system_status": monitor_data.get("status", "unknown"),
    }

    # Check if we already have today's entry
    if not update_metrics_for_date(today, entry):
        append_metrics(entry)

    # Ingest all sources into MetricsDB for indexed time-range queries (METR-01)
    if _METRICSDB_AVAILABLE:
        try:
            ingest_result = _metrics_db.ingest_all()
            entry["metricsdb_ingest"] = ingest_result
        except Exception as e:
            entry["metricsdb_ingest_error"] = str(e)

    entry["collected"] = True
    return entry


def _compute_confidence_histogram() -> dict:
    """Compute confidence distribution across 10 buckets."""
    patterns_path = KB_DIR / "patterns.jsonl"
    if not patterns_path.exists():
        return {}

    # Pre-populate buckets
    buckets = {f"{i/10:.1f}-{(i+1)/10:.1f}": 0 for i in range(10)}

    with open(patterns_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                conf = p.get("confidence", 0.5)
                # Validate confidence is in [0.0, 1.0]
                if not (0.0 <= conf <= 1.0):
                    continue  # Skip invalid entries
                bucket_idx = min(9, int(conf * 10))
                bucket_key = f"{bucket_idx/10:.1f}-{(bucket_idx+1)/10:.1f}"
                if bucket_key in buckets:
                    buckets[bucket_key] += 1
            except (json.JSONDecodeError, TypeError):
                pass

    return buckets


# ─── Summary & Trend Analysis ──────────────────────────────────────────────────

def weekly_summary() -> dict:
    """Generate week-over-week summary."""
    records = load_metrics_history()
    if not records:
        return {"error": "No historical data available", "records": 0}

    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    two_weeks_ago = (now - timedelta(days=14)).strftime("%Y-%m-%d")

    this_week = [r for r in records if r.get("date", "") >= week_ago]
    last_week = [r for r in records if two_weeks_ago <= r.get("date", "") < week_ago]

    def avg(records_list, field):
        vals = [r.get(field, 0) for r in records_list if field in r]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def total_field(records_list, field):
        return sum(r.get(field, 0) for r in records_list)

    return {
        "period": "weekly",
        "this_week": {
            "days": len(this_week),
            "avg_success_rate": avg(this_week, "task_success_rate"),
            "avg_patterns": avg(this_week, "total_patterns"),
            "total_rollbacks": total_field(this_week, "rollbacks_today"),
            "avg_conflicts": avg(this_week, "active_conflicts"),
            "avg_learning_speed": avg(this_week, "learning_speed_days_avg"),
        },
        "last_week": {
            "days": len(last_week),
            "avg_success_rate": avg(last_week, "task_success_rate"),
            "avg_patterns": avg(last_week, "total_patterns"),
            "total_rollbacks": total_field(last_week, "rollbacks_today"),
            "avg_conflicts": avg(last_week, "active_conflicts"),
        },
        "trend": _compute_trend(this_week, last_week),
    }


def _compute_trend(this_week: list, last_week: list) -> dict:
    """Compare this week vs last week."""
    if not this_week or not last_week:
        return {"insufficient_data": True}

    def avg(records_list, field):
        vals = [r.get(field, 0) for r in records_list if field in r]
        return sum(vals) / len(vals) if vals else 0.0

    curr_success = avg(this_week, "task_success_rate")
    prev_success = avg(last_week, "task_success_rate")
    curr_patterns = avg(this_week, "total_patterns")
    prev_patterns = avg(last_week, "total_patterns")

    return {
        "success_rate_change": round(curr_success - prev_success, 4),
        "pattern_count_change": round(curr_patterns - prev_patterns, 1),
        "improving": curr_success > prev_success,
        "kb_growing": curr_patterns > prev_patterns,
        "summary": (
            f"Success rate {'improved' if curr_success > prev_success else 'declined'} "
            f"by {abs(curr_success - prev_success):.1%}. "
            f"KB {'grew' if curr_patterns > prev_patterns else 'shrunk'} "
            f"by {abs(curr_patterns - prev_patterns):.0f} patterns."
        ),
    }


def monthly_summary() -> dict:
    """Generate month-over-month summary."""
    records = load_metrics_history()
    if not records:
        return {"error": "No historical data available"}

    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    two_months_ago = (now - timedelta(days=60)).strftime("%Y-%m-%d")

    this_month = [r for r in records if r.get("date", "") >= month_ago]
    last_month = [r for r in records if two_months_ago <= r.get("date", "") < month_ago]

    def avg(records_list, field):
        vals = [r.get(field, 0) for r in records_list if field in r]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "period": "monthly",
        "this_month": {
            "days": len(this_month),
            "avg_success_rate": avg(this_month, "task_success_rate"),
            "max_patterns": max((r.get("total_patterns", 0) for r in this_month), default=0),
            "total_rollbacks": sum(r.get("rollbacks_today", 0) for r in this_month),
        },
        "last_month": {
            "days": len(last_month),
            "avg_success_rate": avg(last_month, "task_success_rate"),
            "max_patterns": max((r.get("total_patterns", 0) for r in last_month), default=0),
            "total_rollbacks": sum(r.get("rollbacks_today", 0) for r in last_month),
        },
        "trend": _compute_trend(this_month, last_month),
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Metrics Collector — daily metric collection and trend analysis"
    )
    parser.add_argument("--summary", action="store_true",
                        help="Show weekly/monthly summary")
    parser.add_argument("--trend", action="store_true",
                        help="Show trend analysis (week-over-week)")
    parser.add_argument("--monthly", action="store_true",
                        help="Show monthly summary")
    parser.add_argument("--history", action="store_true",
                        help="Show raw metrics history")
    parser.add_argument("--collect", action="store_true",
                        help="Collect today's metrics and ingest into MetricsDB (same as default)")

    args = parser.parse_args()

    if args.summary:
        weekly = weekly_summary()
        monthly = monthly_summary()
        print(json.dumps({
            "weekly": weekly,
            "monthly": monthly,
        }, indent=2))
        return

    if args.trend:
        weekly = weekly_summary()
        print(json.dumps({"weekly_trend": weekly.get("trend", {})}, indent=2))
        return

    if args.monthly:
        print(json.dumps(monthly_summary(), indent=2))
        return

    if args.history:
        records = load_metrics_history()
        print(json.dumps({
            "total_days": len(records),
            "records": records[-30:],  # last 30 days
        }, indent=2))
        return

    # Default (and --collect): collect today's metrics + ingest into MetricsDB
    entry = collect_daily_metrics()
    print(json.dumps({
        "collected": True,
        "date": entry.get("date"),
        "metrics_path": str(METRICS_HISTORY_PATH),
        "entry": entry,
    }, indent=2))


if __name__ == "__main__":
    main()
