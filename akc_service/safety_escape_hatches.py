#!/usr/bin/env python3
"""
AKC Safety Escape Hatches
Phase 4, Wave 4 — SAFE-05 Emergency Recovery Mechanisms

Implements 4 escape hatch modes for safe recovery under adverse conditions:
1. CAUTION   — Reduce deployment velocity to 10% cohort only (no auto-promotion)
2. QUARANTINE — Lock a specific pattern from agent loading (manual review required)
3. REVALIDATION — Pause all deployments; re-run full test suite on active fixes
4. RESET     — Rollback all non-gold patterns (confidence < 0.85) to baseline

All state changes are persisted to safety_state.json with timestamp and reason.
Mode history is immutable (append-only mode_history list).
Auto-recovery implemented for CAUTION and REVALIDATION modes.

Usage:
    from safety_escape_hatches import (
        enter_caution_mode, quarantine_pattern, trigger_revalidation,
        reset_to_safe_state, auto_recovery, SafetyEscapeHatches
    )

    result = enter_caution_mode("error_rate_elevated")
    result = quarantine_pattern("pat-001", "repeated_failures")
    result = trigger_revalidation(cohort=1, reason="integration_test_failure")
    result = reset_to_safe_state("cascading_guardrail_violations")
    result = auto_recovery()
"""

import json
import logging
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import os
_DEFAULT_KB_DIR = Path(__file__).parent.parent / "kb"
KB_DIR = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))
_REPO_ROOT = Path(os.environ.get("AKC_SERVICE_REPO_ROOT", str(Path.cwd())))

SAFETY_STATE_PATH = KB_DIR / "safety_state.json"
PATTERNS_PATH = KB_DIR / "patterns.jsonl"
_DEFAULT_LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR = Path(os.environ.get("AKC_SERVICE_LOGS_DIR", str(_DEFAULT_LOGS_DIR)))
LOGS_PATH = LOGS_DIR / "safety_escape_hatches.log"

# Create logs directory if needed
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGS_PATH),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SafetyMode(Enum):
    """Safety state machine modes."""
    NORMAL = "normal"              # Standard operation; all deployments proceed normally
    CAUTION = "caution"            # Reduced deployment rate (10% cohort only; no auto-promotion)
    QUARANTINE = "quarantine"      # A specific pattern locked from agent loading
    REVALIDATION = "revalidation"  # All deployments paused; full re-test in progress
    RESET = "reset"                # All non-gold patterns reverted to baseline


class SafetyEscapeHatches:
    """
    Safety state machine for emergency recovery.

    Manages mode transitions, persists state to safety_state.json,
    and records an immutable mode_history for audit trail.

    Per SAFE-05 requirement:
    - enter_caution_mode: reduce deployment velocity (10% cohort only)
    - quarantine_pattern: lock pattern from agent loading
    - trigger_revalidation: pause deployments and re-run tests
    - reset_to_safe_state: rollback all non-gold patterns (confidence < 0.85)
    - auto_recovery: check conditions for exiting safety modes
    """

    def __init__(self, safety_state_path: str = None):
        self.safety_state_path = Path(safety_state_path) if safety_state_path else SAFETY_STATE_PATH
        self.state = self._load_safety_state()

    def _load_safety_state(self) -> dict:
        """Load current safety state from JSON, with migration from legacy format."""
        try:
            with open(self.safety_state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raw = {}
        except json.JSONDecodeError as e:
            logger.error(f"_load_safety_state: JSON decode error: {e}; resetting state")
            raw = {}

        # Migrate from legacy format (only had escape_hatch key)
        if "current_mode" not in raw:
            logger.info("_load_safety_state: migrating from legacy safety_state format")
            raw = {
                "current_mode": SafetyMode.NORMAL.value,
                "quarantined_patterns": [],
                "revalidation_queue": [],
                "last_safe_checkpoint": _now_iso(),
                "locked_patterns": [],
                "mode_history": [
                    {
                        "mode": SafetyMode.NORMAL.value,
                        "timestamp": _now_iso(),
                        "reason": "Initialized (migrated from legacy format)"
                    }
                ]
            }

        # Ensure all required keys are present (forward-compatibility)
        defaults = {
            "current_mode": SafetyMode.NORMAL.value,
            "quarantined_patterns": [],
            "revalidation_queue": [],
            "last_safe_checkpoint": _now_iso(),
            "locked_patterns": [],
            "mode_history": []
        }
        for key, default_val in defaults.items():
            if key not in raw:
                raw[key] = default_val

        return raw

    def _save_safety_state(self) -> None:
        """Persist current safety state atomically to JSON."""
        self.safety_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.safety_state_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            tmp_path.replace(self.safety_state_path)  # Atomic rename
        except Exception as e:
            logger.error(f"_save_safety_state: failed to save: {e}")
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def enter_caution_mode(self, reason: str) -> dict:
        """
        Caution Mode: Reduce deployment velocity to 10% cohort only.

        - No auto-promotion to 50% or 100% cohorts while in CAUTION.
        - Used when non-critical anomalies detected (e.g., minor error rate elevation,
          pattern confidence drop > 15pp warning alert).
        - Auto-recovery after 24h of stable operation with error_rate within 1pp of baseline.

        Args:
            reason: Human-readable reason for entering caution mode.

        Returns:
            {"status": "caution_active", "reason": reason, "action": description}
        """
        logger.warning(f"CAUTION MODE activated: {reason}")

        self.state["current_mode"] = SafetyMode.CAUTION.value
        self.state["mode_history"].append({
            "mode": SafetyMode.CAUTION.value,
            "timestamp": _now_iso(),
            "reason": reason
        })
        self._save_safety_state()

        return {
            "status": "caution_active",
            "reason": reason,
            "action": "Deployment limited to 10% cohort only; no auto-promotion to 50%/100%"
        }

    def quarantine_pattern(self, pattern_id: str, reason: str) -> dict:
        """
        Quarantine: Lock a specific pattern from being loaded by agents.

        - Pattern is added to quarantined_patterns list and locked_patterns list.
        - Agents check quarantined_patterns at pattern loading time; quarantined
          patterns are excluded from akc_context.knowledge_patterns_active.
        - Manual review required to remove from quarantine.

        Args:
            pattern_id: The pattern ID to quarantine.
            reason: Why the pattern is being quarantined.

        Returns:
            {"status": "quarantined", "pattern_id": pattern_id, "reason": reason}
        """
        if pattern_id not in self.state["quarantined_patterns"]:
            self.state["quarantined_patterns"].append(pattern_id)
            logger.error(f"QUARANTINE: Pattern {pattern_id} locked — {reason}")
        else:
            logger.info(f"quarantine_pattern: {pattern_id} already quarantined")

        if pattern_id not in self.state["locked_patterns"]:
            self.state["locked_patterns"].append(pattern_id)

        self.state["mode_history"].append({
            "mode": "quarantine",
            "timestamp": _now_iso(),
            "pattern_id": pattern_id,
            "reason": reason
        })
        self._save_safety_state()

        return {
            "status": "quarantined",
            "pattern_id": pattern_id,
            "reason": reason,
            "action": "Pattern removed from agent loading; manual review required to restore"
        }

    def trigger_revalidation(self, cohort: int, reason: str) -> dict:
        """
        Re-Validation: Pause all deployments; re-run full test suite on all active fixes.

        - Moves system to REVALIDATION mode; all cohort promotions halt.
        - Adds cohort to revalidation_queue with "pending" status.
        - Auto-recovery when revalidation_queue latest entry status == "passed".

        Args:
            cohort: Which cohort to re-validate (1-3).
            reason: Why re-validation was triggered.

        Returns:
            {"status": "revalidation_triggered", "cohort": cohort, "reason": reason}
        """
        logger.error(f"REVALIDATION triggered for Cohort {cohort}: {reason}")

        self.state["current_mode"] = SafetyMode.REVALIDATION.value
        self.state["revalidation_queue"].append({
            "cohort": cohort,
            "triggered_at": _now_iso(),
            "reason": reason,
            "status": "pending"
        })
        self.state["mode_history"].append({
            "mode": SafetyMode.REVALIDATION.value,
            "timestamp": _now_iso(),
            "cohort": cohort,
            "reason": reason
        })
        self._save_safety_state()

        return {
            "status": "revalidation_triggered",
            "cohort": cohort,
            "reason": reason,
            "action": "All deployments paused; full test suite re-running for all active fixes"
        }

    def reset_to_safe_state(self, reason: str) -> dict:
        """
        Reset: Rollback all non-gold patterns (confidence < 0.85) to baseline.

        - Pattern IDs with confidence < 0.85 are added to locked_patterns.
        - Agents will not load locked patterns (same mechanism as quarantine).
        - Only gold-tier patterns (0.85+) remain available.
        - Manual review required to restore reset patterns.
        - Used in extreme scenarios: cascading guardrail violations, system instability.

        Args:
            reason: Why the system reset was initiated.

        Returns:
            {"status": "reset", "patterns_rolled_back": int, "reason": reason}
        """
        logger.critical(f"SYSTEM RESET initiated: {reason}")

        # Load all patterns and identify those to lock (confidence < 0.85)
        patterns_to_lock = []
        try:
            if PATTERNS_PATH.exists():
                seen_ids = set()
                latest_patterns = {}
                with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            p = json.loads(line)
                            pid = p.get("id")
                            if pid:
                                latest_patterns[pid] = p  # Last occurrence = latest version
                        except json.JSONDecodeError:
                            continue

                for pid, pattern in latest_patterns.items():
                    confidence = pattern.get("confidence", 0.0)
                    if confidence < 0.85:
                        patterns_to_lock.append(pid)
        except Exception as e:
            logger.error(f"reset_to_safe_state: failed to load patterns: {e}")
            return {
                "status": "reset_failed",
                "reason": f"patterns_load_error: {e}"
            }

        # Merge with existing locked patterns (no duplicates)
        existing_locked = set(self.state.get("locked_patterns", []))
        all_locked = list(existing_locked | set(patterns_to_lock))
        self.state["locked_patterns"] = all_locked
        self.state["current_mode"] = SafetyMode.RESET.value
        self.state["mode_history"].append({
            "mode": SafetyMode.RESET.value,
            "timestamp": _now_iso(),
            "reason": reason,
            "patterns_reset": len(patterns_to_lock)
        })
        self._save_safety_state()

        logger.critical(
            f"System reset complete: {len(patterns_to_lock)} patterns locked (confidence < 0.85)"
        )

        return {
            "status": "reset",
            "patterns_rolled_back": len(patterns_to_lock),
            "reason": reason,
            "action": "All non-gold patterns locked; only gold-tier patterns available to agents"
        }

    def auto_recovery(self) -> dict:
        """
        Automatic recovery: Check if system can exit current safety mode.

        Recovery conditions:
        - CAUTION → NORMAL: 24h stable operation with error_rate within 1pp of baseline.
        - REVALIDATION → NORMAL: Latest revalidation_queue entry has status "passed".
        - QUARANTINE: Manual review required — cannot auto-recover.
        - RESET: Manual review required — cannot auto-recover.

        Returns:
            {
                "status": "recovered" | "recovery_eligible" | "recovery_blocked" | "no_action",
                "reason": "...",
                "action": "..."
            }
        """
        # Check for risk spike escalation (SAFE-03) — must be first check so spike takes precedence
        risk_spike_result = self.detect_risk_spike()
        if risk_spike_result.get("spike_detected"):
            logger.critical(
                f"Escalating to CAUTION mode due to risk spike: "
                f"{risk_spike_result['current_pct']:.1f}% vs baseline "
                f"{risk_spike_result['baseline_pct']:.1f}% "
                f"(delta {risk_spike_result['delta_pct']:.1f}pp, "
                f"threshold {risk_spike_result['threshold_pct']:.1f}pp)"
            )
            self.enter_caution_mode(
                reason=f"Risk spike detected: {risk_spike_result['current_pct']:.1f}% "
                       f"vs baseline {risk_spike_result['baseline_pct']:.1f}%"
            )
            return {
                "status": "escalated_to_caution",
                "reason": "risk_spike_detected",
                "current_pct": risk_spike_result["current_pct"],
                "baseline_pct": risk_spike_result["baseline_pct"],
                "delta_pct": risk_spike_result["delta_pct"],
                "threshold_pct": risk_spike_result["threshold_pct"],
                "action": "Entered CAUTION mode; deployment limited to 10% cohort only"
            }

        current_mode = self.state.get("current_mode", SafetyMode.NORMAL.value)

        if current_mode == SafetyMode.NORMAL.value:
            return {
                "status": "no_action",
                "reason": "already_in_normal_mode",
                "current_mode": SafetyMode.NORMAL.value
            }

        if current_mode == SafetyMode.CAUTION.value:
            # Check if 24h of stable operation have elapsed
            mode_history = self.state.get("mode_history", [])
            caution_entries = [e for e in mode_history if e.get("mode") == SafetyMode.CAUTION.value]

            if not caution_entries:
                return {"status": "recovery_blocked", "reason": "caution_entry_not_found"}

            last_caution = caution_entries[-1]
            mode_start_str = last_caution.get("timestamp", "")
            try:
                mode_start = datetime.fromisoformat(mode_start_str.replace("Z", "+00:00"))
                elapsed_hours = (
                    datetime.now(timezone.utc) - mode_start
                ).total_seconds() / 3600
            except (ValueError, AttributeError):
                return {"status": "recovery_blocked", "reason": "cannot_parse_caution_timestamp"}

            if elapsed_hours < 24:
                hours_remaining = round(24 - elapsed_hours, 1)
                return {
                    "status": "recovery_blocked",
                    "reason": f"caution_stability_window_not_met",
                    "hours_remaining": hours_remaining,
                    "action": f"Wait {hours_remaining}h more for 24h stability window"
                }

            # 24h elapsed — check error rate (placeholder: uses monitoring stub)
            error_rate = self._get_current_error_rate()
            baseline = self._get_baseline_error_rate()

            if error_rate <= baseline + 0.01:  # Within 1pp
                self.state["current_mode"] = SafetyMode.NORMAL.value
                self.state["mode_history"].append({
                    "mode": SafetyMode.NORMAL.value,
                    "timestamp": _now_iso(),
                    "reason": "auto_recovery: caution mode — 24h stability satisfied"
                })
                self._save_safety_state()
                logger.info("AUTO-RECOVERY: Exiting CAUTION mode — system stable 24h")
                return {
                    "status": "recovered",
                    "reason": "caution_24h_stability_met",
                    "action": "Resumed normal deployment acceleration"
                }
            else:
                return {
                    "status": "recovery_blocked",
                    "reason": f"error_rate_elevated: {error_rate:.3f} > baseline+1pp ({baseline+0.01:.3f})",
                    "action": "Monitor error rate; retry recovery when within 1pp of baseline"
                }

        if current_mode == SafetyMode.REVALIDATION.value:
            # Check if last revalidation passed
            revalidation_queue = self.state.get("revalidation_queue", [])
            if revalidation_queue:
                latest = revalidation_queue[-1]
                if latest.get("status") == "passed":
                    self.state["current_mode"] = SafetyMode.NORMAL.value
                    self.state["mode_history"].append({
                        "mode": SafetyMode.NORMAL.value,
                        "timestamp": _now_iso(),
                        "reason": "auto_recovery: revalidation passed"
                    })
                    self._save_safety_state()
                    logger.info("AUTO-RECOVERY: Exiting REVALIDATION mode — all tests passed")
                    return {
                        "status": "recovered",
                        "reason": "revalidation_passed",
                        "action": "Resumed staged deployment"
                    }
                else:
                    return {
                        "status": "recovery_blocked",
                        "reason": f"revalidation_status={latest.get('status', 'unknown')}",
                        "action": "Wait for revalidation to complete and pass"
                    }
            return {"status": "recovery_blocked", "reason": "revalidation_queue_empty"}

        # QUARANTINE and RESET: require manual intervention
        return {
            "status": "recovery_blocked",
            "reason": f"{current_mode}_requires_manual_review",
            "action": f"Manual review and intervention required to exit {current_mode} mode"
        }

    def _get_current_error_rate(self) -> float:
        """
        Get current error rate from monitoring metrics.

        Stub: reads from monitoring_engine.py if available, otherwise returns 0.10.
        Will be connected to compute_dashboard_metrics() in Phase 4 integration.
        """
        try:
            from akc_service.monitoring_engine import compute_dashboard_metrics
            metrics = compute_dashboard_metrics()
            error_by_cohort = metrics.get("error_rate_by_cohort", {})
            if error_by_cohort:
                rates = [v.get("error_rate", 0.0) for v in error_by_cohort.values()
                         if isinstance(v, dict)]
                return sum(rates) / len(rates) if rates else 0.10
        except Exception as e:
            logger.warning(f"[CR-03] Error loading monitoring metrics: {e} — using fallback")
        logger.warning("[CR-03] Using fallback error rate 0.10 — monitoring unavailable")
        return 0.10  # Conservative default

    def _get_baseline_error_rate(self) -> float:
        """
        Get baseline error rate from pre-deployment snapshot.

        Stub: returns 0.09 (9%) as the initial baseline.
        Will be connected to staging metrics in production integration.
        """
        logger.debug("[baseline] Using hardcoded baseline error rate 0.09 — production integration pending")
        return 0.09

    def is_pattern_quarantined(self, pattern_id: str) -> bool:
        """Check if a pattern is currently quarantined (locked from agent loading)."""
        return pattern_id in self.state.get("quarantined_patterns", [])

    def is_pattern_locked(self, pattern_id: str) -> bool:
        """Check if a pattern is locked (quarantined or reset-locked)."""
        return pattern_id in self.state.get("locked_patterns", [])

    def get_current_mode(self) -> str:
        """Return current safety mode string."""
        return self.state.get("current_mode", SafetyMode.NORMAL.value)

    def get_mode_history(self) -> list:
        """Return full immutable mode history."""
        return self.state.get("mode_history", [])

    def detect_risk_spike(self) -> dict:
        """
        Check if residual_risk_pct has spiked >2pp above baseline.
        Called from auto_recovery() to trigger escalation if risk jumps.

        Returns: {
            "spike_detected": bool,
            "current_pct": float,
            "baseline_pct": float,
            "delta_pct": float,
            "threshold_pct": float,
            "action": "escalate_to_caution" | "no_action"
        }
        """
        risk_data = self.state.get("residual_risk", {})
        current_pct = risk_data.get("pct", 0.0)
        baseline_pct = risk_data.get("baseline", 2.0)  # Default to <2% target if not set

        delta_pct = current_pct - baseline_pct
        threshold_pct = 2.0  # Spike threshold: >2pp above baseline

        spike_detected = delta_pct > threshold_pct

        result = {
            "spike_detected": spike_detected,
            "current_pct": current_pct,
            "baseline_pct": baseline_pct,
            "delta_pct": delta_pct,
            "threshold_pct": threshold_pct,
            "action": "escalate_to_caution" if spike_detected else "no_action"
        }

        if spike_detected:
            logger.critical(
                f"RISK SPIKE DETECTED: residual_risk {current_pct:.1f}% is "
                f"{delta_pct:.1f}pp above baseline {baseline_pct:.1f}%; "
                f"threshold is {threshold_pct:.1f}pp"
            )

        return result


# ─── Module-Level API Functions ──────────────────────────────────────────────────
# These are the exported functions referenced in the plan's exports list.

def _default_hatch() -> SafetyEscapeHatches:
    """Create a SafetyEscapeHatches instance with the default safety state path."""
    return SafetyEscapeHatches(str(SAFETY_STATE_PATH))


def enter_caution_mode(reason: str) -> dict:
    """Enter caution mode (reduce deployment velocity to 10% cohort only)."""
    return _default_hatch().enter_caution_mode(reason)


def quarantine_pattern(pattern_id: str, reason: str = "manual_quarantine") -> dict:
    """Quarantine a pattern (lock from agent loading until manual review)."""
    return _default_hatch().quarantine_pattern(pattern_id, reason)


def trigger_revalidation(cohort: int, reason: str = "manual_revalidation") -> dict:
    """Trigger re-validation (pause deployments; re-run full test suite)."""
    return _default_hatch().trigger_revalidation(cohort, reason)


def reset_to_safe_state(reason: str = "manual_reset") -> dict:
    """Reset to safe state (lock all non-gold patterns; confidence < 0.85)."""
    return _default_hatch().reset_to_safe_state(reason)


def auto_recovery() -> dict:
    """Check for automatic recovery from safety modes based on stability conditions."""
    return _default_hatch().auto_recovery()


def get_safety_status() -> dict:
    """Return current safety mode and key state metrics."""
    hatch = _default_hatch()
    return {
        "current_mode": hatch.get_current_mode(),
        "quarantined_patterns": hatch.state.get("quarantined_patterns", []),
        "locked_patterns": hatch.state.get("locked_patterns", []),
        "revalidation_queue_length": len(hatch.state.get("revalidation_queue", [])),
        "mode_history_length": len(hatch.get_mode_history())
    }


def compute_residual_risk() -> dict:
    """
    Compute residual risk: percentage of fixes that could pass invalid guardrails.

    Reads fix_history.jsonl and counts entries with guardrail violations.
    If no history exists, returns 0.0 with basis "no_history".

    Returns: {
        "residual_risk_pct": float (0.0-100.0),
        "basis": "n_qc_checks" | "no_history",
        "total_checks": int,
        "guardrail_failures": int,
        "computed_at": ISO8601 timestamp
    }
    """
    fix_history_path = KB_DIR / "fix_history.jsonl"

    if not fix_history_path.exists():
        return {
            "residual_risk_pct": 0.0,
            "basis": "no_history",
            "total_checks": 0,
            "guardrail_failures": 0,
            "computed_at": datetime.now(timezone.utc).isoformat() + "Z"
        }

    total_checks = 0
    guardrail_failures = 0

    try:
        with open(fix_history_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Count all entries as "checks"
                total_checks += 1

                # Count guardrail violations
                # Type 1: fix generation entry with guardrails_violated
                if "guardrails_violated" in entry:
                    if entry["guardrails_violated"]:  # non-empty list = violations
                        guardrail_failures += 1

                # Type 2: cohort event with guardrail_violations count
                elif "guardrail_violations" in entry:
                    if entry["guardrail_violations"] > 0:
                        guardrail_failures += 1

                # Type 3: any entry with pipeline_status that's not "success" or "approved"
                elif "pipeline_status" in entry:
                    if entry["pipeline_status"] not in ("success", "approved", "passed"):
                        guardrail_failures += 1

    except Exception as e:
        logger.warning(f"Error computing residual risk: {e} — returning 0.0")
        return {
            "residual_risk_pct": 0.0,
            "basis": "error",
            "total_checks": total_checks,
            "guardrail_failures": guardrail_failures,
            "computed_at": datetime.now(timezone.utc).isoformat() + "Z"
        }

    # Calculate risk percentage
    if total_checks == 0:
        residual_risk_pct = 0.0
    else:
        residual_risk_pct = (guardrail_failures / total_checks) * 100.0

    return {
        "residual_risk_pct": residual_risk_pct,
        "basis": "n_qc_checks",
        "total_checks": total_checks,
        "guardrail_failures": guardrail_failures,
        "computed_at": datetime.now(timezone.utc).isoformat() + "Z"
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="AKC Safety Escape Hatches — Emergency recovery mechanisms"
    )
    parser.add_argument("--status", action="store_true", help="Show current safety status")
    parser.add_argument("--caution", metavar="REASON", help="Enter caution mode")
    parser.add_argument("--quarantine", metavar="PATTERN_ID", help="Quarantine a pattern")
    parser.add_argument("--quarantine-reason", metavar="REASON", default="manual_quarantine",
                        help="Reason for quarantine (use with --quarantine)")
    parser.add_argument("--revalidate", metavar="COHORT", type=int, help="Trigger re-validation for cohort")
    parser.add_argument("--revalidate-reason", metavar="REASON", default="manual_revalidation",
                        help="Reason for re-validation (use with --revalidate)")
    parser.add_argument("--reset", metavar="REASON", help="Reset to safe state")
    parser.add_argument("--auto-recover", action="store_true", help="Attempt auto-recovery")

    args = parser.parse_args()

    if args.status:
        print(json.dumps(get_safety_status(), indent=2))
    elif args.caution:
        print(json.dumps(enter_caution_mode(args.caution), indent=2))
    elif args.quarantine:
        print(json.dumps(quarantine_pattern(args.quarantine, args.quarantine_reason), indent=2))
    elif args.revalidate is not None:
        print(json.dumps(trigger_revalidation(args.revalidate, args.revalidate_reason), indent=2))
    elif args.reset:
        print(json.dumps(reset_to_safe_state(args.reset), indent=2))
    elif args.auto_recover:
        print(json.dumps(auto_recovery(), indent=2))
    else:
        parser.print_help()
        sys.exit(0)
