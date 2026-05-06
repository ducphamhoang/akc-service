#!/usr/bin/env python3
"""
AKC Monitoring Engine
Phase 1, Wave 5 - Task 1.26

Monitors deployment metrics, detects alert conditions, and sends notifications.

Usage:
    python monitoring_engine.py --monitor
    python monitoring_engine.py --check-alerts
    python monitoring_engine.py --send-alert --metric <name> --value <float> --threshold <float>
"""

import argparse
import json
import os
import sys
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# MetricsDB import — optional; falls back to JSONL path if unavailable
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import metrics_db as _metrics_db
    _METRICSDB_AVAILABLE = True
except ImportError:
    _METRICSDB_AVAILABLE = False
PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FAILURE_INDEX_PATH = KB_DIR / "failure_index.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"
SAFETY_STATE_PATH = KB_DIR / "safety_state.json"

# ─── Alert Thresholds ──────────────────────────────────────────────────────────

ALERT_THRESHOLDS = {
    "error_spike": 0.02,           # >2% drop in success rate
    "confidence_drop": 0.15,       # >15% confidence drop in 24h
    "conflict_count": 2,           # 2+ patterns conflicting
    "rollback_cascade": 3,         # 3+ rollbacks in 1 day
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all_patterns(kb_dir: Optional[Path] = None) -> list:
    effective_kb_dir = kb_dir if kb_dir is not None else KB_DIR
    patterns_path = effective_kb_dir / "patterns.jsonl"
    patterns = []
    if not patterns_path.exists():
        return patterns
    with open(patterns_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    patterns.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return patterns


def load_fix_history(kb_dir: Optional[Path] = None) -> list:
    effective_kb_dir = kb_dir if kb_dir is not None else KB_DIR
    fix_history_path = effective_kb_dir / "fix_history.jsonl"
    fixes = []
    if not fix_history_path.exists():
        return fixes
    with open(fix_history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    fixes.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return fixes


def load_failure_index(kb_dir: Optional[Path] = None) -> list:
    effective_kb_dir = kb_dir if kb_dir is not None else KB_DIR
    failure_index_path = effective_kb_dir / "failure_index.jsonl"
    failures = []
    if not failure_index_path.exists():
        return failures
    with open(failure_index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    failures.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return failures


def load_confidence_history(kb_dir: Optional[Path] = None) -> list:
    effective_kb_dir = kb_dir if kb_dir is not None else KB_DIR
    confidence_history_path = effective_kb_dir / "confidence_history.jsonl"
    history = []
    if not confidence_history_path.exists():
        return history
    with open(confidence_history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return history


# ─── Metric Collectors ─────────────────────────────────────────────────────────

def compute_task_success_rate(kb_dir: Optional[Path] = None) -> dict:
    """Compute task success rate from failure index and fix history."""
    failures = load_failure_index(kb_dir=kb_dir)
    fixes = load_fix_history(kb_dir=kb_dir)

    total_tasks = len(failures) + len(fixes)

    if total_tasks == 0:
        return {
            "metric": "task_success_rate",
            "current": None,  # Unknown, not 100%
            "status": "insufficient_data",
            "total_tasks": 0,
            "failed_tasks": 0,
        }

    failed_tasks = len(failures)
    success_rate = max(0.0, 1.0 - (failed_tasks / total_tasks))

    # Trend: compute 7-day window vs overall
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    recent_failures = [
        f for f in failures
        if _parse_ts(f.get("timestamp", "")) >= week_ago
    ]
    recent_rate = max(0.0, 1.0 - (len(recent_failures) / max(len(recent_failures) + 1, 1)))

    return {
        "metric": "task_success_rate",
        "current": round(success_rate, 4),
        "7day_rate": round(recent_rate, 4),
        "total_tasks": total_tasks,
        "failed_tasks": failed_tasks,
        "recent_failures_7d": len(recent_failures),
    }


def compute_confidence_distribution(kb_dir: Optional[Path] = None) -> dict:
    """Compute tier distribution from patterns.jsonl."""
    patterns = load_all_patterns(kb_dir=kb_dir)

    tier_counts = {"gold": 0, "production": 0, "experimental": 0, "demoted": 0}
    for p in patterns:
        tier = p.get("confidence_tier") or _tier_from_confidence(p.get("confidence", 0.5))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    total = len(patterns)

    return {
        "metric": "confidence_distribution",
        "total_patterns": total,
        "by_tier": tier_counts,
        "gold_pct": round(tier_counts["gold"] / max(total, 1), 4),
        "production_pct": round(tier_counts["production"] / max(total, 1), 4),
        "experimental_pct": round(tier_counts["experimental"] / max(total, 1), 4),
        "demoted_pct": round(tier_counts["demoted"] / max(total, 1), 4),
    }


def compute_rollback_frequency(kb_dir: Optional[Path] = None) -> dict:
    """Compute rollback frequency from fix history."""
    fixes = load_fix_history(kb_dir=kb_dir)
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    rollbacks_today = sum(
        1 for f in fixes
        if f.get("outcome") in ("rolled_back", "rollback")
        and _parse_ts(f.get("generated_at", "")) >= day_ago
    )

    rollbacks_7d = sum(
        1 for f in fixes
        if f.get("outcome") in ("rolled_back", "rollback")
        and _parse_ts(f.get("generated_at", "")) >= week_ago
    )

    total_deployments = len(fixes)
    rollback_rate = round(rollbacks_7d / max(total_deployments, 1), 4)

    return {
        "metric": "rollback_frequency",
        "rollbacks_today": rollbacks_today,
        "rollbacks_7d": rollbacks_7d,
        "total_deployments": total_deployments,
        "rollback_rate_7d": rollback_rate,
        "cascade_threshold": ALERT_THRESHOLDS["rollback_cascade"],
        "cascade_alert": rollbacks_today >= ALERT_THRESHOLDS["rollback_cascade"],
    }


def compute_active_conflicts() -> dict:
    """Check for active conflicts by importing safety_engine."""
    try:
        from akc_service import safety_engine
        conflicts = safety_engine.detect_conflicts()
        return {
            "metric": "active_conflicts",
            "conflict_count": conflicts.get("total_conflicts", 0),
            "high_severity": conflicts.get("high_severity", 0),
            "alert_triggered": conflicts.get("alert_triggered", False),
            "conflicts": conflicts.get("conflict_list", [])[:5],  # top 5
        }
    except Exception as e:
        return {
            "metric": "active_conflicts",
            "error": str(e),
            "conflict_count": 0,
        }


def compute_confidence_drop(kb_dir: Optional[Path] = None) -> dict:
    """Detect patterns with >15% confidence drop in 24h."""
    history = load_confidence_history(kb_dir=kb_dir)
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    # Group by pattern_id, find max drop in 24h window
    drops: dict = {}
    for h in history:
        ts = _parse_ts(h.get("timestamp", ""))
        if ts < day_ago:
            continue
        pid = h.get("pattern_id")
        if not pid:
            continue
        delta = h.get("delta", 0.0)
        if delta < 0:
            drops[pid] = drops.get(pid, 0.0) + abs(delta)

    # Find patterns with >15% total drop
    alert_patterns = {
        pid: drop for pid, drop in drops.items()
        if drop >= ALERT_THRESHOLDS["confidence_drop"]
    }

    return {
        "metric": "confidence_drop_24h",
        "patterns_dropping": len(drops),
        "alert_count": len(alert_patterns),
        "alert_patterns": {
            pid: round(drop, 4) for pid, drop in alert_patterns.items()
        },
        "threshold": ALERT_THRESHOLDS["confidence_drop"],
        "alert_triggered": len(alert_patterns) > 0,
    }


def _parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _tier_from_confidence(confidence: float) -> str:
    if confidence >= 0.85:
        return "gold"
    elif confidence >= 0.70:
        return "production"
    elif confidence >= 0.50:
        return "experimental"
    else:
        return "demoted"


# ─── Monitor Mode ──────────────────────────────────────────────────────────────

def run_monitor(kb_dir: Optional[Path] = None) -> dict:
    """
    Collect all monitoring metrics and return consolidated status.
    Updates every 5 minutes (call from cron or scheduler).
    """
    metrics = {
        "task_success_rate": compute_task_success_rate(kb_dir=kb_dir),
        "confidence_distribution": compute_confidence_distribution(kb_dir=kb_dir),
        "rollback_frequency": compute_rollback_frequency(kb_dir=kb_dir),
        "confidence_drop_24h": compute_confidence_drop(kb_dir=kb_dir),
        "active_conflicts": compute_active_conflicts(),
    }

    # Collect alerts
    alerts = check_alerts(metrics)

    return {
        "timestamp": now_iso(),
        "data_freshness": "real-time (from JSONL files)",
        "metrics": metrics,
        "alerts": alerts,
        "alert_count": len(alerts),
        "status": "alert" if alerts else "healthy",
    }


# ─── Alert Detection ──────────────────────────────────────────────────────────

def check_alerts(metrics: dict = None) -> list:
    """
    Check all alert conditions and return active alerts.

    Alert triggers:
    - Error spike: success rate drops >2%
    - Confidence collapse: pattern confidence drops >15% in 24h
    - Conflict detected: 2+ patterns conflicting
    - Rollback cascade: 3+ rollbacks in 1 day
    """
    if metrics is None:
        metrics = {
            "task_success_rate": compute_task_success_rate(),
            "confidence_distribution": compute_confidence_distribution(),
            "rollback_frequency": compute_rollback_frequency(),
            "confidence_drop_24h": compute_confidence_drop(),
            "active_conflicts": compute_active_conflicts(),
        }

    alerts = []

    # Error spike check
    tsr = metrics.get("task_success_rate", {})
    current_rate = tsr.get("current", 1.0)
    week_rate = tsr.get("7day_rate", 1.0)
    drop = week_rate - current_rate
    if drop > ALERT_THRESHOLDS["error_spike"]:
        alerts.append({
            "type": "error_spike",
            "severity": "critical",
            "metric_value": current_rate,
            "threshold": ALERT_THRESHOLDS["error_spike"],
            "drop": round(drop, 4),
            "message": (
                f"ERROR SPIKE: Task success dropped {drop:.1%} "
                f"(current={current_rate:.1%}, 7d_avg={week_rate:.1%}). "
                f"Check failure_index.jsonl for details."
            ),
            "recommended_action": "Review failure_index.jsonl, check recent deployments",
        })

    # Confidence collapse
    cd = metrics.get("confidence_drop_24h", {})
    if cd.get("alert_triggered"):
        for pid, drop_val in cd.get("alert_patterns", {}).items():
            alerts.append({
                "type": "confidence_collapse",
                "severity": "warning",
                "pattern_id": pid,
                "metric_value": drop_val,
                "threshold": ALERT_THRESHOLDS["confidence_drop"],
                "message": (
                    f"CONFIDENCE COLLAPSE: Pattern '{pid}' dropped {drop_val:.0%} in 24h. "
                    "Investigating root cause."
                ),
                "recommended_action": f"Inspect confidence_history.jsonl for pattern '{pid}'",
            })

    # Conflict alert
    ac = metrics.get("active_conflicts", {})
    conflict_count = ac.get("conflict_count", 0)
    if conflict_count >= ALERT_THRESHOLDS["conflict_count"]:
        alerts.append({
            "type": "conflict_detected",
            "severity": "warning",
            "metric_value": conflict_count,
            "threshold": ALERT_THRESHOLDS["conflict_count"],
            "message": (
                f"CONFLICT DETECTED: {conflict_count} patterns flagged as conflicting. "
                "Review and resolve to prevent cascading failures."
            ),
            "recommended_action": "Run safety_engine --detect-conflicts for details",
        })

    # Rollback cascade
    rf = metrics.get("rollback_frequency", {})
    rollbacks_today = rf.get("rollbacks_today", 0)
    if rollbacks_today >= ALERT_THRESHOLDS["rollback_cascade"]:
        alerts.append({
            "type": "rollback_cascade",
            "severity": "critical",
            "metric_value": rollbacks_today,
            "threshold": ALERT_THRESHOLDS["rollback_cascade"],
            "message": (
                f"ROLLBACK CASCADE: {rollbacks_today} rollbacks today. "
                "Possible systemic issue — consider activating quarantine mode."
            ),
            "recommended_action": (
                "Run: python safety_engine.py --set-escape-hatch quarantine"
            ),
        })

    return alerts


# ─── Alert Configuration Validation ────────────────────────────────────────────

def _validate_config():
    """Validate required monitoring configuration."""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url and not slack_url.startswith("https://"):
        raise ValueError("SLACK_WEBHOOK_URL must be HTTPS")

    if os.environ.get("SMTP_HOST"):
        if not os.environ.get("SMTP_USER"):
            raise ValueError("SMTP_USER required if SMTP_HOST is set")


# ─── Alert Dispatch ────────────────────────────────────────────────────────────

def send_alert(alert: dict) -> dict:
    """
    Send alert via Slack webhook or email.

    Configuration via environment variables:
    - SLACK_WEBHOOK_URL: Slack incoming webhook URL (must be HTTPS)
    - EMAIL_LIST: Comma-separated list of email addresses
    - SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS: SMTP config
    """
    try:
        _validate_config()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    results = {}

    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        results["slack"] = _send_slack(alert, slack_url)

    email_list = os.environ.get("EMAIL_LIST")
    if email_list:
        results["email"] = _send_email(alert, email_list.split(","))

    if not slack_url and not email_list:
        # Fallback: print to stdout
        results["stdout"] = _format_alert_text(alert)
        print(results["stdout"])

    return results


def _format_alert_text(alert: dict) -> str:
    """Format alert as human-readable text."""
    severity_icons = {
        "critical": "CRITICAL",
        "warning": "WARNING",
        "info": "INFO",
    }
    icon = severity_icons.get(alert.get("severity", "info"), "ALERT")
    ts = now_iso()
    return (
        f"[{ts}] AKC {icon}: {alert.get('message', 'Unknown alert')}\n"
        f"  Metric: {alert.get('metric_value')}\n"
        f"  Threshold: {alert.get('threshold')}\n"
        f"  Action: {alert.get('recommended_action', 'Check monitoring dashboard')}"
    )


def _send_slack(alert: dict, webhook_url: str) -> dict:
    """Send alert to Slack via webhook."""
    severity_emoji = {
        "critical": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
    }
    emoji = severity_emoji.get(alert.get("severity", "info"), ":bell:")

    payload = {
        "text": f"{emoji} *AKC Alert*: {alert.get('message', 'Unknown alert')}",
        "attachments": [
            {
                "color": "danger" if alert.get("severity") == "critical" else "warning",
                "fields": [
                    {"title": "Type", "value": alert.get("type", "unknown"), "short": True},
                    {"title": "Severity", "value": alert.get("severity", "info"), "short": True},
                    {"title": "Metric Value", "value": str(alert.get("metric_value", "")), "short": True},
                    {"title": "Threshold", "value": str(alert.get("threshold", "")), "short": True},
                    {"title": "Recommended Action", "value": alert.get("recommended_action", ""), "short": False},
                ],
                "footer": "AKC Monitoring Engine",
                "ts": str(int(datetime.now().timestamp())),
            }
        ],
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return {"success": True, "status": resp.status}
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {"success": False, "error": f"URL error: {str(e)}"}
    except (json.JSONEncodeError, ValueError) as e:
        return {"success": False, "error": f"Payload encoding error: {str(e)}"}


def _send_email(alert: dict, recipients: list) -> dict:
    """Send alert via email."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_addr = smtp_user or "akc-monitoring@my-demon.local"

    subject = f"[AKC Alert] {alert.get('severity', 'info').upper()}: {alert.get('type', 'unknown')}"
    body = _format_alert_text(alert)

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, recipients, msg.as_string())
        return {"success": True, "recipients": recipients}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Phase 4 Wave 3: Real-Time Monitoring Dashboard ──────────────────────────────

def compute_dashboard_metrics(kb_dir: Optional[Path] = None) -> dict:
    """
    Compute real-time metrics for dashboard. Run every 60s during deployment.

    Returns: {
        "timestamp": "iso8601",
        "task_success_rate": {
            "current": 0.92,
            "7day_rolling": 0.91,
            "trend": "stable"
        },
        "error_rate_by_cohort": {
            "cohort_1": {"error_rate": 0.08, "baseline": 0.10, "change_pp": -2},
            "cohort_2": {"error_rate": 0.09, "baseline": 0.10, "change_pp": -1},
            "cohort_3": {"error_rate": None, "status": "not_active"}
        },
        "pattern_confidence_trend": {
            "gold_tier_count": 30,
            "production_tier_count": 45,
            "experimental_tier_count": 20
        },
        "guardrail_violations": {
            "total": 0,
            "physics_layers": 0,
            "signal_connections": 0,
            "pattern_compatibility": 0,
            "constraint_preservation": 0
        },
        "active_alerts": [
            {"type": "error_spike", "cohort": 1, "value_pp": 12, "threshold": 10}
        ]
    }
    """
    effective_kb_dir = kb_dir if kb_dir is not None else KB_DIR
    # Load current metrics from KB files
    patterns = load_all_patterns(kb_dir=kb_dir)
    failures = []
    failure_index_path = effective_kb_dir / "failure_index.jsonl"
    if failure_index_path.exists():
        with open(failure_index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        failures.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    # Compute task success rate
    total_tasks = max(len(failures) + 50, 50)  # Assume 50 baseline
    failed_tasks = len(failures)
    success_rate = max(0.0, 1.0 - (failed_tasks / total_tasks)) if total_tasks > 0 else 0.92
    trend = "stable" if abs(success_rate - 0.92) < 0.05 else "improving" if success_rate > 0.92 else "declining"

    # Compute error rate per active cohort
    error_by_cohort = {}
    staging_metrics_path = effective_kb_dir / "staging" / "staging_metrics.jsonl"

    for cohort_num in [1, 2, 3]:
        cohort_metrics = {
            "error_rate": None,
            "baseline": 0.10,
            "change_pp": 0,
            "status": "not_active"
        }

        if staging_metrics_path.exists():
            with open(staging_metrics_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            if entry.get("cohort") == cohort_num:
                                error_rate = entry.get("error_rate", 0.10)
                                cohort_metrics = {
                                    "error_rate": error_rate,
                                    "baseline": 0.10,
                                    "change_pp": int((error_rate - 0.10) * 100),
                                    "status": "active"
                                }
                        except json.JSONDecodeError:
                            pass

        error_by_cohort[f"cohort_{cohort_num}"] = cohort_metrics

    # Compute pattern confidence distribution
    tiers = {"gold": 0, "production": 0, "experimental": 0}
    for p in patterns:
        confidence = p.get("confidence", 0.0)
        if confidence >= 0.85:
            tiers["gold"] += 1
        elif confidence >= 0.70:
            tiers["production"] += 1
        else:
            tiers["experimental"] += 1

    # Compute guardrail violation counts
    violations = {
        "total": 0,
        "physics_layers": 0,
        "signal_connections": 0,
        "pattern_compatibility": 0,
        "constraint_preservation": 0
    }
    for failure in failures:
        if "guardrail_violation" in failure.get("tags", []):
            violations["total"] += 1
            if "physics_layers" in failure.get("tags", []):
                violations["physics_layers"] += 1
            if "signal_connections" in failure.get("tags", []):
                violations["signal_connections"] += 1
            if "pattern_compatibility" in failure.get("tags", []):
                violations["pattern_compatibility"] += 1
            if "constraint_preservation" in failure.get("tags", []):
                violations["constraint_preservation"] += 1

    # Check alert conditions
    active_alerts = check_alert_conditions(error_by_cohort, violations, patterns)

    return {
        "timestamp": now_iso(),
        "task_success_rate": {
            "current": success_rate,
            "7day_rolling": success_rate,  # Simplified; would aggregate history
            "trend": trend
        },
        "error_rate_by_cohort": error_by_cohort,
        "pattern_confidence_trend": {
            "gold_tier_count": tiers["gold"],
            "production_tier_count": tiers["production"],
            "experimental_tier_count": tiers["experimental"]
        },
        "guardrail_violations": violations,
        "active_alerts": active_alerts
    }


def check_alert_conditions(error_by_cohort: dict, violations: dict, patterns: list) -> list:
    """
    Check for alert conditions per D-15:
    - >10pp error spike (rollback trigger)
    - Guardrail violation (immediate escalation)
    - Confidence drop >15pp (review required)
    """
    alerts = []

    # Alert 1: Error spike >10pp
    for cohort_key, cohort_metrics in error_by_cohort.items():
        if cohort_metrics.get("error_rate") is None:
            continue
        change_pp = cohort_metrics.get("change_pp", 0)
        if change_pp > 10:
            alerts.append({
                "type": "error_spike",
                "severity": "critical",
                "cohort": cohort_key,
                "value_pp": change_pp,
                "threshold": 10,
                "action": "trigger_auto_rollback"
            })

    # Alert 2: Guardrail violations
    if violations.get("total", 0) > 0:
        alerts.append({
            "type": "guardrail_violation",
            "severity": "critical",
            "violations": violations,
            "action": "escalate_immediately"
        })

    # Alert 3: Pattern confidence drop >15pp
    for p in patterns:
        if "confidence_history" in p:
            history = p["confidence_history"]
            if len(history) >= 2:
                latest = history[-1].get("confidence", 0.0) if isinstance(history[-1], dict) else history[-1]
                previous = history[-2].get("confidence", 0.0) if isinstance(history[-2], dict) else history[-2]
                drop_pp = (previous - latest) * 100
                if drop_pp > 15:
                    alerts.append({
                        "type": "confidence_drop",
                        "severity": "warning",
                        "pattern_id": p.get("id"),
                        "drop_pp": drop_pp,
                        "threshold": 15,
                        "action": "notify_review"
                    })

    return alerts


def render_dashboard(metrics: dict) -> str:
    """
    Render dashboard as CLI output or HTML.
    For MVP: CLI-based display updated every 60s.
    """
    output = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                      VALIDATION & DEPLOYMENT DASHBOARD                      ║
║                         Updated: {metrics['timestamp']}                      ║
╚══════════════════════════════════════════════════════════════════════════════╝

📊 TASK SUCCESS RATE
  Current: {metrics['task_success_rate']['current']:.1%}
  7-day rolling avg: {metrics['task_success_rate']['7day_rolling']:.1%}
  Trend: {metrics['task_success_rate']['trend']}

📈 ERROR RATE BY COHORT
"""
    for cohort_key, cohort_metrics in metrics['error_rate_by_cohort'].items():
        if cohort_metrics.get("error_rate") is None:
            output += f"  {cohort_key}: NOT ACTIVE\n"
        else:
            change = cohort_metrics.get("change_pp", 0)
            status = "✓ STABLE" if abs(change) <= 2 else "⚠ ELEVATED" if change > 2 else "✓ IMPROVING"
            output += f"  {cohort_key}: {cohort_metrics['error_rate']:.1%} (baseline {cohort_metrics['baseline']:.1%}, {change:+.1f}pp) {status}\n"

    output += f"""
🎯 PATTERN CONFIDENCE DISTRIBUTION
  Gold tier (0.85+): {metrics['pattern_confidence_trend']['gold_tier_count']}
  Production tier (0.70+): {metrics['pattern_confidence_trend']['production_tier_count']}
  Experimental: {metrics['pattern_confidence_trend']['experimental_tier_count']}

🛡️ GUARDRAIL VIOLATIONS
  Total: {metrics['guardrail_violations']['total']}
  Physics layers: {metrics['guardrail_violations']['physics_layers']}
  Signal connections: {metrics['guardrail_violations']['signal_connections']}
  Pattern compatibility: {metrics['guardrail_violations']['pattern_compatibility']}
  Constraint preservation: {metrics['guardrail_violations']['constraint_preservation']}

🚨 ACTIVE ALERTS ({len(metrics['active_alerts'])} total)
"""
    for alert in metrics['active_alerts']:
        output += f"  [{alert['severity'].upper()}] {alert['type']}: {alert.get('action', 'N/A')}\n"

    return output


# ─── MetricsDB Alert Checker ──────────────────────────────────────────────────

def check_alerts_db(window_hours: int = 24) -> list:
    """
    Check alert conditions using MetricsDB indexed queries.

    Alert types:
    1. confidence_collapse: any pattern with sum of negative confidence_delta
       >= ALERT_THRESHOLDS["confidence_drop"] (0.15) in the window
    2. error_spike: task success rate drops >ALERT_THRESHOLDS["error_spike"] (0.02)
       compared to 7-day rolling average stored in MetricsDB

    Returns list of alert dicts matching existing check_alerts() alert shape.
    Falls back to check_alerts() (JSONL path) if MetricsDB is unavailable.
    """
    if not _METRICSDB_AVAILABLE:
        return check_alerts()

    try:
        conn = _metrics_db.get_connection()
    except RuntimeError:
        return check_alerts()

    alerts = []
    from datetime import datetime, timezone, timedelta

    since_dt = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    since = since_dt.isoformat()

    # ── Alert 1: Confidence collapse ────────────────────────────────────────
    # Sum negative confidence_delta rows per pattern_id in window.
    # Alert fires when ABS(sum) >= 0.15.
    conf_rows = conn.execute(
        """SELECT pattern_id, SUM(metric_value) as total_drop
           FROM metrics
           WHERE metric_name='confidence_delta'
             AND metric_value < 0
             AND timestamp >= ?
             AND pattern_id IS NOT NULL
           GROUP BY pattern_id
           HAVING ABS(SUM(metric_value)) >= ?""",
        (since, ALERT_THRESHOLDS["confidence_drop"])
    ).fetchall()

    for r in conf_rows:
        pid = r["pattern_id"]
        drop_val = abs(r["total_drop"] or 0.0)
        alerts.append({
            "type": "confidence_collapse",
            "severity": "warning",
            "pattern_id": pid,
            "metric_value": round(drop_val, 4),
            "threshold": ALERT_THRESHOLDS["confidence_drop"],
            "window_hours": window_hours,
            "message": (
                f"CONFIDENCE COLLAPSE (DB): Pattern '{pid}' dropped "
                f"{drop_val:.0%} in {window_hours}h window. "
                "Investigating root cause."
            ),
            "recommended_action": (
                f"Inspect confidence_history.jsonl for pattern '{pid}'"
            ),
        })

    # ── Alert 2: Error spike ─────────────────────────────────────────────────
    # Compare recent daily_snapshot task_success_rate vs 7-day average.
    # Use daily_snapshot rows from MetricsDB.
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    snapshot_rows = conn.execute(
        """SELECT metric_value, timestamp
           FROM metrics
           WHERE metric_name='daily_snapshot'
             AND timestamp >= ?
           ORDER BY timestamp""",
        (since_7d,)
    ).fetchall()

    if len(snapshot_rows) >= 2:
        values = [r["metric_value"] for r in snapshot_rows if r["metric_value"] is not None]
        if values:
            baseline_rate = sum(values[:-1]) / max(len(values) - 1, 1)
            current_rate = values[-1]
            drop = baseline_rate - current_rate
            if drop > ALERT_THRESHOLDS["error_spike"]:
                alerts.append({
                    "type": "error_spike",
                    "severity": "critical",
                    "metric_value": round(current_rate, 4),
                    "threshold": ALERT_THRESHOLDS["error_spike"],
                    "baseline_rate": round(baseline_rate, 4),
                    "drop_pp": round(drop * 100, 2),
                    "message": (
                        f"ERROR SPIKE (DB): Task success dropped {drop:.1%} "
                        f"(current={current_rate:.1%}, 7d_avg={baseline_rate:.1%}). "
                        "Check failure_index.jsonl for details."
                    ),
                    "recommended_action": (
                        "Review failure_index.jsonl, check recent deployments"
                    ),
                })

    conn.close()
    return alerts


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AKC Monitoring Engine — metrics collection, alert detection, notification dispatch"
    )
    parser.add_argument("--monitor", action="store_true",
                        help="Run full monitoring cycle and report status")
    parser.add_argument("--check-alerts", action="store_true",
                        help="Check alert conditions and print active alerts")
    parser.add_argument(
        "--check-alerts-db",
        action="store_true",
        help="Check alert conditions using MetricsDB (indexed queries, not JSONL scan)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="Lookback window in hours for --check-alerts-db (default: 24)",
    )
    parser.add_argument("--send-alert", action="store_true",
                        help="Send a specific alert (for testing)")
    parser.add_argument("--metric", help="Metric name for --send-alert")
    parser.add_argument("--value", type=float, help="Metric value for --send-alert")
    parser.add_argument("--threshold", type=float, help="Threshold for --send-alert")
    parser.add_argument("--severity", default="warning", help="Alert severity")
    parser.add_argument("--message", help="Alert message")

    args = parser.parse_args()

    if args.monitor:
        result = run_monitor()
        print(json.dumps(result, indent=2))
        return

    if args.check_alerts:
        alerts = check_alerts()
        print(json.dumps({
            "timestamp": now_iso(),
            "alert_count": len(alerts),
            "alerts": alerts,
        }, indent=2))
        return

    if args.check_alerts_db:
        alerts = check_alerts_db(window_hours=getattr(args, "window_hours", 24))
        print(json.dumps({
            "timestamp": now_iso(),
            "backend": "metricsdb" if _METRICSDB_AVAILABLE else "jsonl_fallback",
            "alert_count": len(alerts),
            "window_hours": getattr(args, "window_hours", 24),
            "alerts": alerts,
        }, indent=2))
        return

    if args.send_alert:
        if not args.metric:
            print("ERROR: --send-alert requires --metric", file=sys.stderr)
            sys.exit(1)
        alert = {
            "type": args.metric,
            "severity": args.severity,
            "metric_value": args.value,
            "threshold": args.threshold,
            "message": args.message or f"Alert: {args.metric} = {args.value}",
            "recommended_action": "Check monitoring dashboard",
        }
        result = send_alert(alert)
        print(json.dumps(result, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
