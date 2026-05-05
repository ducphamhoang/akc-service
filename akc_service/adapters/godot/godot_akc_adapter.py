"""
GodotAKCAdapter — bridges GDScript tooling output to akc-service REST API.

Records lint violations and test runner outcomes into the AKC learning engine
via POST /akc/v1/record. Degrades gracefully when akc-service is unreachable.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests


class GodotAKCAdapter:
    """Adapter that wires GDScript lint and test output to akc-service metrics.

    Posts lint violations and test outcomes to the akc-service REST endpoint
    at POST /akc/v1/record. All HTTP errors are caught and logged — callers
    never receive an exception from this class.

    Threat model (T-ext3-01): 0.15s timeout enforced on all HTTP calls.
    Connection refused and timeouts are swallowed with a warning log.
    """

    def __init__(self, akc_url: str = "") -> None:
        """Initialize the adapter.

        Args:
            akc_url: Base URL of the running akc-service instance.
                     If not provided, reads from AKC_SERVICE_URL env var.
        """
        if not akc_url:
            akc_url = os.environ.get("AKC_SERVICE_URL", "http://localhost:8000")
        self.akc_url = akc_url.rstrip("/")
        self.record_endpoint = f"{self.akc_url}/akc/v1/record"
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()

    def record_lint_result(self, lint_result: dict, file_path: str) -> None:
        """Record GDScript lint output to akc-service.

        Posts one record per lint error when lint_result["passed"] is False.
        Posts a single clean-lint record when lint_result["passed"] is True.

        Args:
            lint_result: Dict from lint_gdscript() with keys "passed", "errors",
                         and "raw_output". errors is a list of
                         {"file": str, "line": int, "message": str}.
            file_path: Path to the GDScript file that was linted.
        """
        basename = Path(file_path).name

        if lint_result.get("passed", True):
            # Single clean-lint record
            payload = self._build_record_payload(
                task_id=f"lint_{basename}",
                status="success",
                entity="gdscript",
                component=basename,
                outcome="lint_pass",
                error_signature="",
                source="gdlint",
            )
            self._post_record(payload)
        else:
            # One record per lint error
            errors = lint_result.get("errors", [])
            for error in errors:
                payload = self._build_record_payload(
                    task_id=f"lint_{basename}",
                    status="failed",
                    entity="gdscript",
                    component=basename,
                    outcome="lint_fail",
                    error_signature=error.get("message", ""),
                    source="gdlint",
                    line=error.get("line"),
                )
                self._post_record(payload)

    def record_test_result(
        self, test_output: str, passed: bool, test_file: str
    ) -> None:
        """Record test runner output to akc-service.

        Posts a single pass or fail record. On failure, the first 500 chars of
        test_output are captured as the error_signature.

        Args:
            test_output: Raw output from the test runner (e.g. pytest stdout).
            passed: True if all tests passed, False otherwise.
            test_file: Path or name of the test file that was run.
        """
        basename = Path(test_file).name
        status = "success" if passed else "failed"
        outcome = "pass" if passed else "fail"
        error_signature = "" if passed else test_output[:500]

        payload = self._build_record_payload(
            task_id=f"test_{basename}",
            status=status,
            entity="godot_test",
            component=basename,
            outcome=outcome,
            error_signature=error_signature,
            source="pytest",
        )
        self._post_record(payload)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_record_payload(
        self,
        task_id: str,
        status: str,
        entity: str,
        component: str,
        outcome: str,
        error_signature: str,
        source: str,
        line: int | None = None,
    ) -> dict:
        """Build a RecordRequest-compatible payload dict."""
        akc_context: dict = {
            "entity": entity,
            "component": component,
            "outcome": outcome,
            "error_signature": error_signature,
            "source": source,
        }
        if line is not None:
            akc_context["line"] = line

        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "akc_context": akc_context,
        }

    def _post_record(self, payload: dict) -> None:
        """POST a record to akc-service. Swallows all exceptions.

        Args:
            payload: RecordRequest-compatible dict to post as JSON.
        """
        try:
            self.session.post(
                self.record_endpoint,
                json=payload,
                timeout=0.15,
            )
        except requests.exceptions.ConnectionError as e:
            self.logger.warning(f"AKC record failed (connection): {e}")
        except requests.exceptions.Timeout as e:
            self.logger.warning(f"AKC record failed (timeout): {e}")
        except requests.exceptions.HTTPError as e:
            self.logger.warning(f"AKC record failed (HTTP error): {e}")
        except Exception as e:
            self.logger.warning(f"AKC record failed: {e}")
