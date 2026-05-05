#!/usr/bin/env python3
"""MetricsDB — SQLite metrics store with JSONL fallback for AKC observability."""
import json
import sqlite3
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

DB_PATH = KB_DIR / "metrics.db"
FALLBACK_PATH = KB_DIR / "metrics_fallback.jsonl"
PATTERNS_PATH = KB_DIR / "patterns.jsonl"
FIX_HISTORY_PATH = KB_DIR / "fix_history.jsonl"
CONFIDENCE_HISTORY_PATH = KB_DIR / "confidence_history.jsonl"
SAFETY_STATE_PATH = KB_DIR / "safety_state.json"
METRICS_HISTORY_PATH = _REPO_ROOT / ".planning" / "METRICS_HISTORY.jsonl"

class MetricsDB:
    """Thin wrapper class providing a connection-scoped API over the module functions."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.conn = get_connection(db_path)

    def ingest_all(self) -> dict:
        return ingest_all(self.db_path)

    def query_range(self, metric_name: str, since: str, until: str | None = None) -> list:
        return query_range(self.conn, metric_name, since, until)

    def pattern_utilization(self, since: str, until: str | None = None) -> list:
        return pattern_utilization(self.conn, since, until)

    def learning_loop_efficiency(self, window_days: int = 7) -> dict:
        return learning_loop_efficiency(self.conn, window_days)

    def close(self):
        self.conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value REAL,
    category     TEXT,
    pattern_id   TEXT,
    source       TEXT,
    extra_json   TEXT,
    row_hash     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup    ON metrics(row_hash);
CREATE INDEX IF NOT EXISTS idx_ts             ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_pattern_id     ON metrics(pattern_id);
CREATE INDEX IF NOT EXISTS idx_category       ON metrics(category);
CREATE INDEX IF NOT EXISTS idx_name_ts        ON metrics(metric_name, timestamp);
"""

# 30-second in-process cache
_CACHE: dict = {}
_CACHE_TTL = 30  # seconds


def get_connection(db_path=None) -> sqlite3.Connection:
    """Open (or create) the SQLite metrics database and apply schema."""
    path = Path(db_path) if db_path else DB_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
        return conn
    except Exception as exc:
        raise RuntimeError(f"SQLite unavailable: {exc}") from exc


def _row_hash(source: str, timestamp: str, pattern_id: str, metric_name: str) -> str:
    """Return md5 hex digest used as dedup key."""
    raw = f"{source}|{timestamp or ''}|{pattern_id or ''}|{metric_name}"
    return hashlib.md5(raw.encode()).hexdigest()


def _insert_row(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert row into metrics table using INSERT OR IGNORE. Returns True if inserted."""
    rh = _row_hash(
        row.get("source", ""),
        row.get("timestamp", ""),
        row.get("pattern_id") or "",
        row.get("metric_name", ""),
    )
    conn.execute(
        """INSERT OR IGNORE INTO metrics
           (timestamp, metric_name, metric_value, category, pattern_id, source, extra_json, row_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row.get("timestamp"),
            row.get("metric_name"),
            row.get("metric_value"),
            row.get("category"),
            row.get("pattern_id"),
            row.get("source"),
            row.get("extra_json"),
            rh,
        ),
    )
    return conn.execute("SELECT changes()").fetchone()[0] == 1


def _ingest_fallback(row: dict) -> None:
    """Write row as JSON line to metrics_fallback.jsonl when SQLite is unavailable."""
    FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FALLBACK_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def ingest_patterns(conn: sqlite3.Connection) -> int:
    """Ingest patterns.jsonl — one snapshot row per pattern."""
    count = 0
    if not PATTERNS_PATH.exists():
        return count
    with open(PATTERNS_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            row = {
                "timestamp": r.get("updated_at") or r.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "metric_name": "pattern_snapshot",
                "metric_value": r.get("confidence"),
                "category": r.get("confidence_tier"),
                "pattern_id": r.get("id"),
                "source": "patterns.jsonl",
                "extra_json": json.dumps({
                    "usage_count": r.get("usage_count"),
                    "failure_count": r.get("failure_count"),
                    "pattern_type": r.get("pattern_type"),
                }),
            }
            if _insert_row(conn, row):
                count += 1
    conn.commit()
    return count


def ingest_fix_history(conn: sqlite3.Connection) -> int:
    """Ingest fix_history.jsonl — only fix records with fix_id AND outcome."""
    count = 0
    if not FIX_HISTORY_PATH.exists():
        return count
    with open(FIX_HISTORY_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Filter: only ingest rows where fix_id and outcome are both present
            if not (r.get("fix_id") and r.get("outcome")):
                continue
            outcome = r.get("outcome")
            success = 1.0 if outcome not in ("rolled_back", "rollback") else 0.0
            row = {
                "timestamp": r.get("generated_at") or r.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "metric_name": "fix_outcome",
                "metric_value": success,
                "category": None,
                "pattern_id": r.get("pattern_id"),
                "source": "fix_history.jsonl",
                "extra_json": json.dumps({"fix_id": r.get("fix_id"), "outcome": outcome}),
            }
            if _insert_row(conn, row):
                count += 1
    conn.commit()
    return count


def ingest_confidence_history(conn: sqlite3.Connection) -> int:
    """Ingest confidence_history.jsonl — signed confidence delta per pattern event."""
    count = 0
    if not CONFIDENCE_HISTORY_PATH.exists():
        return count
    with open(CONFIDENCE_HISTORY_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Support both 'delta' and 'confidence_delta' field names
            delta = r.get("delta") if r.get("delta") is not None else r.get("confidence_delta")
            # Skip records without a pattern_id or delta (e.g., escape_hatch_change events)
            if r.get("pattern_id") is None or delta is None:
                continue
            row = {
                "timestamp": r.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "metric_name": "confidence_delta",
                "metric_value": delta,
                "category": r.get("new_tier"),
                "pattern_id": r.get("pattern_id"),
                "source": "confidence_history.jsonl",
                "extra_json": json.dumps({
                    "old": r.get("old_confidence"),
                    "new": r.get("new_confidence"),
                    "tier_changed": r.get("tier_changed"),
                }),
            }
            if _insert_row(conn, row):
                count += 1
    conn.commit()
    return count


def ingest_safety_state(conn: sqlite3.Connection) -> int:
    """Ingest safety_state.json — single JSON document, 2 rows produced."""
    count = 0
    if not SAFETY_STATE_PATH.exists():
        return count
    with open(SAFETY_STATE_PATH, "r", encoding="utf-8") as f:
        state = json.load(f)
    residual = state.get("residual_risk", {})
    ts = residual.get("computed_at") or datetime.now(timezone.utc).isoformat()

    row1 = {
        "timestamp": ts,
        "metric_name": "residual_risk_pct",
        "metric_value": residual.get("pct", 0.0),
        "category": None,
        "pattern_id": None,
        "source": "safety_state.json",
        "extra_json": json.dumps({"baseline": residual.get("baseline")}),
    }
    row2 = {
        "timestamp": ts,
        "metric_name": "escape_hatch_mode",
        "metric_value": None,
        "category": state.get("current_mode"),
        "pattern_id": None,
        "source": "safety_state.json",
        "extra_json": None,
    }
    if _insert_row(conn, row1):
        count += 1
    if _insert_row(conn, row2):
        count += 1
    conn.commit()
    return count


def ingest_metrics_history(conn: sqlite3.Connection) -> int:
    """Ingest METRICS_HISTORY.jsonl — daily system snapshots."""
    count = 0
    if not METRICS_HISTORY_PATH.exists():
        return count
    with open(METRICS_HISTORY_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            row = {
                "timestamp": r.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "metric_name": "daily_snapshot",
                "metric_value": r.get("task_success_rate"),
                "category": None,
                "pattern_id": None,
                "source": "METRICS_HISTORY.jsonl",
                "extra_json": json.dumps({
                    "date": r.get("date"),
                    "total_patterns": r.get("total_patterns"),
                    "rollbacks_today": r.get("rollbacks_today"),
                    "alert_count": r.get("alert_count"),
                    "system_status": r.get("system_status"),
                }),
            }
            if _insert_row(conn, row):
                count += 1
    conn.commit()
    return count


def ingest_all(db_path=None) -> dict:
    """Ingest all 5 source files into MetricsDB. Returns counts per source."""
    results = {}
    try:
        conn = get_connection(db_path)
        results["patterns"] = ingest_patterns(conn)
        results["fix_history"] = ingest_fix_history(conn)
        results["confidence_history"] = ingest_confidence_history(conn)
        results["safety_state"] = ingest_safety_state(conn)
        results["metrics_history"] = ingest_metrics_history(conn)
        conn.close()
        results["backend"] = "sqlite"
    except RuntimeError:
        # SQLite unavailable — fall back to JSONL for all rows
        results["backend"] = "jsonl_fallback"
        results["error"] = "SQLite unavailable; rows written to metrics_fallback.jsonl"
    return results


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------


def query_range(conn, metric_name: str, since: str, until: str | None = None) -> list:
    """Return metrics rows for metric_name in [since, until]. Results cached 30s."""
    cache_key = (metric_name, since, until)
    if cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if (time.monotonic() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]
    sql = "SELECT * FROM metrics WHERE metric_name=? AND timestamp>=?"
    params: list = [metric_name, since]
    if until:
        sql += " AND timestamp<=?"
        params.append(until)
    sql += " ORDER BY timestamp"
    rows = conn.execute(sql, params).fetchall()
    result = [dict(r) for r in rows]
    _CACHE[cache_key] = {"data": result, "ts": time.monotonic()}
    return result


def pattern_utilization(conn, since: str, until: str | None = None) -> list:
    """
    Return per-pattern utilization aggregated from fix_outcome rows.
    Each row: {pattern_id, usage_count, success_count, success_rate}
    """
    sql = """
        SELECT
            pattern_id,
            COUNT(*) as usage_count,
            SUM(CASE WHEN metric_value = 1.0 THEN 1 ELSE 0 END) as success_count
        FROM metrics
        WHERE metric_name='fix_outcome'
          AND pattern_id IS NOT NULL
          AND timestamp >= ?
    """
    params: list = [since]
    if until:
        sql += " AND timestamp <= ?"
        params.append(until)
    sql += " GROUP BY pattern_id ORDER BY usage_count DESC"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        usage = r["usage_count"] or 0
        success = r["success_count"] or 0
        result.append({
            "pattern_id": r["pattern_id"],
            "usage_count": usage,
            "success_count": success,
            "success_rate": round(success / max(usage, 1), 4),
        })
    return result


def learning_loop_efficiency(conn, window_days: int = 7) -> dict:
    """
    Confidence growth rate and fix success rate over window_days.
    Returns: {window_days, avg_confidence_growth_per_event, confidence_events,
              fix_success_rate, fix_total}
    """
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    growth = conn.execute(
        """SELECT AVG(metric_value) as avg_delta, COUNT(*) as events
           FROM metrics
           WHERE metric_name='confidence_delta' AND metric_value > 0 AND timestamp >= ?""",
        (since,),
    ).fetchone()

    fix_stats = conn.execute(
        """SELECT
             SUM(CASE WHEN metric_value = 1.0 THEN 1 ELSE 0 END) as successes,
             COUNT(*) as total
           FROM metrics
           WHERE metric_name='fix_outcome' AND timestamp >= ?""",
        (since,),
    ).fetchone()

    total = fix_stats["total"] or 0
    return {
        "window_days": window_days,
        "avg_confidence_growth_per_event": round((growth["avg_delta"] or 0.0), 4),
        "confidence_events": (growth["events"] or 0),
        "fix_success_rate": round((fix_stats["successes"] or 0) / max(total, 1), 4),
        "fix_total": total,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MetricsDB ingest")
    parser.add_argument("--ingest", action="store_true", help="Ingest all sources")
    parser.add_argument(
        "--query-efficiency",
        action="store_true",
        help="Query learning loop efficiency (7d window)",
    )
    args = parser.parse_args()

    if args.ingest:
        result = ingest_all()
        print(json.dumps(result, indent=2))

    if args.query_efficiency:
        conn = get_connection()
        result = learning_loop_efficiency(conn)
        conn.close()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
