#!/usr/bin/env python3
"""
AKC Real-Time Dashboard
Phase 1, Wave 6 - Task 1.28

Text-based monitoring dashboard that displays AKC system health.
Refreshes every 5 minutes. Generates daily DASHBOARD.md report.

Usage:
    python dashboard.py                  # One-shot display
    python dashboard.py --watch          # Refresh every 5 minutes
    python dashboard.py --generate-report  # Write DASHBOARD.md
"""

import argparse
import csv
import io
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

DASHBOARD_MD_PATH = _REPO_ROOT / ".planning" / "DASHBOARD.md"

# sys.path manipulation removed — akc_service is now a proper package


def parse_range_arg(range_str: str) -> tuple:
    """
    Parse --range value into (since_iso, until_iso_or_None).

    Accepted values:
      7d        — last 7 days from now
      30d       — last 30 days from now
      all       — all time (since 2000-01-01T00:00:00Z)
      YYYY-MM-DD:YYYY-MM-DD — explicit date range (inclusive)

    Returns (since: str, until: str | None) — ISO8601 UTC strings.
    Raises ValueError for unrecognized format.
    """
    now = datetime.now(timezone.utc)
    if range_str == "7d":
        return (now - timedelta(days=7)).isoformat(), None
    elif range_str == "30d":
        return (now - timedelta(days=30)).isoformat(), None
    elif range_str == "all":
        return "2000-01-01T00:00:00Z", None
    elif ":" in range_str:
        parts = range_str.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid range format: {range_str}")
        return parts[0] + "T00:00:00Z", parts[1] + "T23:59:59Z"
    else:
        raise ValueError(
            f"Invalid --range value: '{range_str}'. "
            "Use: 7d, 30d, all, or YYYY-MM-DD:YYYY-MM-DD"
        )


def render_time_range_table(since: str, until: str | None, title: str = None) -> None:
    """
    Query MetricsDB for the given time range and render two Rich tables:
    1. Pattern Utilization (from pattern_utilization())
    2. Learning Loop Efficiency (from learning_loop_efficiency())

    Falls back to plain-text message if MetricsDB is unavailable.
    """
    try:
        import metrics_db
    except ImportError:
        print("MetricsDB not available — run Plan 01 first to create metrics_db.py")
        return

    from rich.console import Console
    from rich.table import Table
    console = Console()

    try:
        conn = metrics_db.get_connection()
    except RuntimeError as e:
        console.print(f"[yellow]MetricsDB unavailable: {e}. Run --ingest first.[/yellow]")
        return

    display_range = title or f"Range: {since[:10]} to {(until or 'now')[:10]}"
    console.print(f"\n[bold cyan]AKC Metrics — {display_range}[/bold cyan]\n")

    # Table 1: Pattern Utilization
    util_rows = metrics_db.pattern_utilization(conn, since, until)
    if util_rows:
        table = Table(title="Pattern Utilization", show_header=True, header_style="bold magenta")
        table.add_column("Pattern ID", style="cyan", no_wrap=True)
        table.add_column("Usage", justify="right")
        table.add_column("Successes", justify="right")
        table.add_column("Success Rate", justify="right")
        for r in util_rows:
            table.add_row(
                r["pattern_id"],
                str(r["usage_count"]),
                str(r["success_count"]),
                f"{r['success_rate']:.1%}",
            )
        console.print(table)
    else:
        console.print("[dim]No fix_outcome data in this range.[/dim]")

    # Table 2: Learning Loop Efficiency
    # Derive window_days from since/until for learning_loop_efficiency()
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        window_days = max(1, (datetime.now(timezone.utc) - since_dt).days)
    except Exception:
        window_days = 7
    efficiency = metrics_db.learning_loop_efficiency(conn, window_days=window_days)

    eff_table = Table(title="Learning Loop Efficiency", show_header=True, header_style="bold green")
    eff_table.add_column("Metric", style="cyan")
    eff_table.add_column("Value", justify="right")
    eff_table.add_row("Window (days)", str(efficiency["window_days"]))
    eff_table.add_row("Avg confidence growth/event", f"{efficiency['avg_confidence_growth_per_event']:.4f}")
    eff_table.add_row("Confidence events", str(efficiency["confidence_events"]))
    eff_table.add_row("Fix success rate", f"{efficiency['fix_success_rate']:.1%}")
    eff_table.add_row("Fix total (in window)", str(efficiency["fix_total"]))
    console.print(eff_table)

    conn.close()


def export_csv(since: str, until: str | None, output_path: str | None = None) -> str:
    """
    Export all metrics rows for the range to CSV.
    Returns CSV string. Writes to output_path if provided.
    """
    try:
        import metrics_db
        conn = metrics_db.get_connection()
    except (ImportError, RuntimeError) as e:
        return f"ERROR: {e}"

    sql = "SELECT * FROM metrics WHERE timestamp >= ?"
    params = [since]
    if until:
        sql += " AND timestamp <= ?"
        params.append(until)
    sql += " ORDER BY timestamp"
    db_rows = conn.execute(sql, params).fetchall()
    conn.close()

    buf = io.StringIO()
    if db_rows:
        writer = csv.DictWriter(buf, fieldnames=dict(db_rows[0]).keys())
        writer.writeheader()
        for r in db_rows:
            writer.writerow(dict(r))

    csv_content = buf.getvalue()
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(csv_content)
    return csv_content


def export_json(since: str, until: str | None, output_path: str | None = None) -> str:
    """
    Export all metrics rows for the range to JSON.
    Returns JSON string. Writes to output_path if provided.
    """
    try:
        import metrics_db
        conn = metrics_db.get_connection()
    except (ImportError, RuntimeError) as e:
        return json.dumps({"error": str(e)})

    sql = "SELECT * FROM metrics WHERE timestamp >= ?"
    params = [since]
    if until:
        sql += " AND timestamp <= ?"
        params.append(until)
    sql += " ORDER BY timestamp"
    db_rows = conn.execute(sql, params).fetchall()
    conn.close()

    result = [dict(r) for r in db_rows]
    json_content = json.dumps(result, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_content)
    return json_content


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ─── Data Loader ───────────────────────────────────────────────────────────────

def gather_metrics() -> dict:
    """Gather all metrics for the dashboard."""
    try:
        from akc_service import monitoring_engine
        return monitoring_engine.run_monitor()
    except Exception as e:
        return {
            "timestamp": now_iso(),
            "error": str(e),
            "metrics": {},
            "alerts": [],
            "status": "error",
        }


# ─── Learning Loop Metrics ─────────────────────────────────────────────────────

def gather_learning_loop_metrics() -> dict:
    """Gather learning loop metrics from confidence history and patterns."""
    try:
        import json
        from pathlib import Path

        # Load patterns to count tiers
        patterns_path = KB_DIR / "patterns.jsonl"
        confidence_history_path = KB_DIR / "confidence_history.jsonl"

        tier_counts = {"gold": 0, "production": 0, "experimental": 0, "demoted": 0}
        total_patterns = 0
        total_confidence = 0.0

        if patterns_path.exists():
            with open(patterns_path, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            pattern = json.loads(line)
                            total_patterns += 1
                            tier = pattern.get("confidence_tier", "experimental")
                            tier_counts[tier] = tier_counts.get(tier, 0) + 1
                            total_confidence += pattern.get("confidence", 0.5)
                        except json.JSONDecodeError:
                            pass

        avg_confidence = total_confidence / total_patterns if total_patterns > 0 else 0.0

        # Load recent confidence history entries
        recent_updates = []
        if confidence_history_path.exists():
            entries = []
            with open(confidence_history_path, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            # Get last 5 entries
            recent_updates = entries[-5:] if entries else []

        # Calculate latency stats
        latencies = [e.get("latency_ms", 0) for e in recent_updates if e.get("latency_ms")]
        latency_stats = {
            "min_ms": min(latencies) if latencies else 0,
            "max_ms": max(latencies) if latencies else 0,
            "avg_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else 0,
            "over_sla_count": sum(1 for lat in latencies if lat > 300000)  # 5 min SLA
        }

        # Calculate convergence progress
        gold_target = 40
        gold_current = tier_counts["gold"]
        progress_percent = (gold_current / gold_target) * 100 if gold_target > 0 else 0

        return {
            "timestamp": now_iso(),
            "metrics": {
                "gold_tier_count": tier_counts["gold"],
                "gold_tier_target": gold_target,
                "gold_tier_percent": (tier_counts["gold"] / total_patterns * 100) if total_patterns > 0 else 0,
                "production_tier_count": tier_counts["production"],
                "experimental_tier_count": tier_counts["experimental"],
                "demoted_tier_count": tier_counts["demoted"],
                "total_patterns": total_patterns,
                "avg_confidence": round(avg_confidence, 4),
                "latency": latency_stats,
                "recent_updates": recent_updates
            },
            "convergence": {
                "gold_target": gold_target,
                "gold_current": gold_current,
                "progress_percent": round(progress_percent, 1)
            },
            "status": "healthy" if latency_stats["over_sla_count"] == 0 and gold_current > 0 else "warning"
        }
    except Exception as e:
        return {
            "timestamp": now_iso(),
            "error": str(e),
            "metrics": {},
            "status": "error"
        }


# ─── Text Dashboard Renderer ───────────────────────────────────────────────────

WIDTH = 70


def _box(title: str, lines: list) -> str:
    """Render a bordered box."""
    top = f"┌─ {title} " + "─" * max(0, WIDTH - len(title) - 4) + "┐"
    bottom = "└" + "─" * (WIDTH - 1) + "┘"
    content_lines = []
    for line in lines:
        padding = " " * max(0, WIDTH - len(line) - 3)
        content_lines.append(f"│ {line}{padding}│")
    return "\n".join([top] + content_lines + [bottom])


def _bar(value: float, width: int = 20) -> str:
    """Render a text progress bar."""
    filled = int(value * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def render_dashboard(data: dict) -> str:
    """Render full text dashboard."""
    lines = []
    ts = data.get("timestamp", now_iso())
    status = data.get("status", "unknown")
    alert_count = data.get("alert_count", 0)

    # Header
    lines.append("=" * WIDTH)
    lines.append("  AKC MONITORING DASHBOARD — My Demon (Godot 4.6)".center(WIDTH))
    lines.append(f"  {now_display()}".center(WIDTH))
    status_indicator = "HEALTHY" if status == "healthy" else f"ALERTS: {alert_count}"
    lines.append(f"  Status: {status_indicator}".center(WIDTH))
    lines.append("=" * WIDTH)
    lines.append("")

    metrics = data.get("metrics", {})
    alerts = data.get("alerts", [])

    # ── Metric 1: Task Success Rate ───────────────────────────────────────────
    tsr = metrics.get("task_success_rate", {})
    success_rate = tsr.get("current", 0.0)
    week_rate = tsr.get("7day_rate", 0.0)
    bar = _bar(success_rate)
    trend = "↑" if success_rate >= week_rate else "↓"
    tsr_lines = [
        f"Current:   {bar} {success_rate:.1%}",
        f"7-day avg: {_bar(week_rate)} {week_rate:.1%} {trend}",
        f"Total tasks seen: {tsr.get('total_tasks', 0)}  Failed: {tsr.get('failed_tasks', 0)}",
    ]
    lines.append(_box("Task Success Rate", tsr_lines))
    lines.append("")

    # ── Metric 2: Pattern Confidence Distribution ─────────────────────────────
    cd = metrics.get("confidence_distribution", {})
    total_p = cd.get("total_patterns", 0)
    tiers = cd.get("by_tier", {})
    gold = tiers.get("gold", 0)
    prod = tiers.get("production", 0)
    exp = tiers.get("experimental", 0)
    dem = tiers.get("demoted", 0)
    cd_lines = [
        f"Total patterns: {total_p}",
        f"Gold        (>=0.85): {gold:4d}  {_bar(gold/max(total_p,1), 15)} {cd.get('gold_pct', 0):.0%}",
        f"Production (0.70-0.85): {prod:4d}  {_bar(prod/max(total_p,1), 15)} {cd.get('production_pct', 0):.0%}",
        f"Experiment (0.50-0.70): {exp:4d}  {_bar(exp/max(total_p,1), 15)} {cd.get('experimental_pct', 0):.0%}",
        f"Demoted    (< 0.50): {dem:4d}  {_bar(dem/max(total_p,1), 15)} {cd.get('demoted_pct', 0):.0%}",
    ]
    lines.append(_box("Pattern Confidence Distribution", cd_lines))
    lines.append("")

    # ── Metric 3: Rollback Frequency ─────────────────────────────────────────
    rf = metrics.get("rollback_frequency", {})
    rollbacks_today = rf.get("rollbacks_today", 0)
    rollbacks_7d = rf.get("rollbacks_7d", 0)
    total_dep = rf.get("total_deployments", 0)
    cascade_alert = rf.get("cascade_alert", False)
    rf_lines = [
        f"Rollbacks today: {rollbacks_today}  (cascade threshold: {rf.get('cascade_threshold', 3)})",
        f"Rollbacks 7d:    {rollbacks_7d}",
        f"Total deployments: {total_dep}",
        f"Rollback rate 7d: {rf.get('rollback_rate_7d', 0):.1%}",
        f"Cascade alert: {'YES - TAKE ACTION' if cascade_alert else 'No'}",
    ]
    lines.append(_box("Rollback Frequency", rf_lines))
    lines.append("")

    # ── Metric 4: Active Conflicts ─────────────────────────────────────────────
    ac = metrics.get("active_conflicts", {})
    conflict_count = ac.get("conflict_count", 0)
    high_sev = ac.get("high_severity", 0)
    conflict_list = ac.get("conflicts", [])
    ac_lines = [
        f"Total conflicts: {conflict_count}  High-severity: {high_sev}",
        f"Alert triggered: {'YES' if ac.get('alert_triggered') else 'No'}",
    ]
    for c in conflict_list[:3]:
        pids = c.get("pattern_ids", [])
        ac_lines.append(f"  [{c.get('severity', '?').upper()}] {' vs '.join(str(p) for p in pids)}")
    if not conflict_list:
        ac_lines.append("  No active conflicts detected")
    lines.append(_box("Active Conflicts", ac_lines))
    lines.append("")

    # ── Metric 5: KB Size ─────────────────────────────────────────────────────
    kb_lines = [
        f"Total patterns: {total_p}",
        f"Gold + Production: {gold + prod}  ({(gold+prod)/max(total_p,1):.0%} of KB)",
        f"Coverage target: >=50 patterns  {'PASS' if total_p >= 50 else f'FAIL (need {50-total_p} more)'}",
    ]
    lines.append(_box("Knowledge Base Size", kb_lines))
    lines.append("")

    # ── Alerts ────────────────────────────────────────────────────────────────
    if alerts:
        alert_lines = []
        for a in alerts:
            severity = a.get("severity", "info").upper()
            alert_lines.append(f"[{severity}] {a.get('message', '')[:55]}")
            alert_lines.append(f"  -> {a.get('recommended_action', '')[:55]}")
        lines.append(_box(f"ACTIVE ALERTS ({len(alerts)})", alert_lines))
    else:
        lines.append(_box("Alerts", ["No active alerts"]))
    lines.append("")

    lines.append(f"Data freshness: {data.get('data_freshness', 'N/A')}")
    lines.append(f"Next refresh:   in 5 minutes")
    lines.append("=" * WIDTH)

    return "\n".join(lines)


# ─── Dashboard.md Generator ────────────────────────────────────────────────────

def generate_dashboard_md(data: dict) -> None:
    """Generate .planning/DASHBOARD.md with daily trend data."""
    metrics = data.get("metrics", {})
    alerts = data.get("alerts", [])
    tsr = metrics.get("task_success_rate", {})
    cd = metrics.get("confidence_distribution", {})
    rf = metrics.get("rollback_frequency", {})
    ac = metrics.get("active_conflicts", {})

    content = f"""# AKC System Dashboard
**Generated:** {now_display()}
**Status:** {data.get('status', 'unknown').upper()}

---

## Current Metrics

| Metric | Value | Trend |
|--------|-------|-------|
| Task Success Rate | {tsr.get('current', 0):.1%} | 7d avg: {tsr.get('7day_rate', 0):.1%} |
| Total Patterns | {cd.get('total_patterns', 0)} | - |
| Gold Tier % | {cd.get('gold_pct', 0):.0%} | - |
| Production Tier % | {cd.get('production_pct', 0):.0%} | - |
| Rollbacks Today | {rf.get('rollbacks_today', 0)} | 7d: {rf.get('rollbacks_7d', 0)} |
| Active Conflicts | {ac.get('conflict_count', 0)} | High: {ac.get('high_severity', 0)} |

---

## Pattern Distribution

```
Gold        (>=0.85): {cd.get('by_tier', {}).get('gold', 0):4d} patterns  {cd.get('gold_pct', 0):.0%}
Production (0.70-0.85): {cd.get('by_tier', {}).get('production', 0):4d} patterns  {cd.get('production_pct', 0):.0%}
Experimental (0.50-0.70): {cd.get('by_tier', {}).get('experimental', 0):4d} patterns  {cd.get('experimental_pct', 0):.0%}
Demoted    (< 0.50): {cd.get('by_tier', {}).get('demoted', 0):4d} patterns  {cd.get('demoted_pct', 0):.0%}
```

---

## Active Alerts ({len(alerts)})

{"No active alerts." if not alerts else ""}
"""

    for a in alerts:
        content += f"""
### {a.get('type', 'alert').upper()} — {a.get('severity', 'info').upper()}

**Message:** {a.get('message', '')}
**Metric Value:** {a.get('metric_value', 'N/A')}
**Threshold:** {a.get('threshold', 'N/A')}
**Recommended Action:** {a.get('recommended_action', '')}
"""

    content += f"""
---

*Dashboard refreshes every 5 minutes. Run: `python .claude/scripts/dashboard.py`*
*Daily report generated by: `python .claude/scripts/dashboard.py --generate-report`*
"""

    DASHBOARD_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_MD_PATH, "w", encoding="utf-8") as f:
        f.write(content)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Real-Time Dashboard — text display + daily report generation"
    )
    parser.add_argument("--watch", action="store_true",
                        help="Continuously refresh every 5 minutes")
    parser.add_argument("--generate-report", action="store_true",
                        help="Generate DASHBOARD.md report")
    parser.add_argument("--learning-loop-status", action="store_true",
                        help="Display learning loop metrics as JSON")
    parser.add_argument("--interval", type=int, default=300,
                        help="Refresh interval in seconds (default: 300)")
    parser.add_argument(
        "--range",
        metavar="RANGE",
        help="Time-range filter: 7d, 30d, all, or YYYY-MM-DD:YYYY-MM-DD",
    )
    parser.add_argument(
        "--export-csv",
        metavar="FILE",
        nargs="?",
        const="-",
        help="Export metrics to CSV (FILE or stdout if omitted)",
    )
    parser.add_argument(
        "--export-json",
        metavar="FILE",
        nargs="?",
        const="-",
        help="Export metrics to JSON (FILE or stdout if omitted)",
    )
    args = parser.parse_args()

    if args.learning_loop_status:
        data = gather_learning_loop_metrics()
        # Output as JSON for machine readability
        print(json.dumps(data, indent=2))
        return

    if args.range:
        try:
            since, until = parse_range_arg(args.range)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        render_time_range_table(since, until, title=f"Range: {args.range}")
        return

    if args.export_csv is not None:
        since, until = parse_range_arg(args.range or "all")
        out_path = None if args.export_csv == "-" else args.export_csv
        content = export_csv(since, until, out_path)
        if args.export_csv == "-":
            print(content, end="")
        else:
            print(f"CSV written to: {args.export_csv}")
        return

    if args.export_json is not None:
        since, until = parse_range_arg(args.range or "all")
        out_path = None if args.export_json == "-" else args.export_json
        content = export_json(since, until, out_path)
        if args.export_json == "-":
            print(content, end="")
        else:
            print(f"JSON written to: {args.export_json}")
        return

    if args.generate_report:
        data = gather_metrics()
        generate_dashboard_md(data)
        print(f"Dashboard report written to: {DASHBOARD_MD_PATH}")
        return

    if args.watch:
        try:
            while True:
                data = gather_metrics()
                # Clear terminal
                print("\033[2J\033[H", end="")
                print(render_dashboard(data))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        return

    # One-shot display
    data = gather_metrics()
    print(render_dashboard(data))

    # Also generate the report
    generate_dashboard_md(data)


if __name__ == "__main__":
    main()
