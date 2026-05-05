"""
TIER 2 Issue 9 — Tier/Confidence Inconsistency Tests
=====================================================

Verifies that all tier classification functions in the codebase use >= 0.85
as the gold boundary (inclusive), and that the G6 guardrail fires at exactly
confidence == 0.85 (boundary value).

Boundary-value test cases:
  - 0.84  → production (below gold threshold)
  - 0.85  → gold (exact boundary — must be inclusive)
  - 0.86  → gold (above boundary)
  - 0.70  → production (exact production/experimental boundary — inclusive)
  - 0.69  → experimental (below production threshold)
  - 0.50  → experimental (exact experimental/demoted boundary — inclusive)
  - 0.49  → demoted (below experimental threshold)
"""

import pytest


# ─── Import all tier functions under test ────────────────────────────────────

from akc_service.learning_integration import (
    _confidence_tier as li_tier,
    determine_tier,
    normalize_pattern_tier,
)
from akc_service.learning_engine import _confidence_tier as le_tier
from akc_service.detection_engine import _confidence_tier_label as de_tier
from akc_service.monitoring_engine import _tier_from_confidence as me_tier


# ─── Parametrized boundary cases ─────────────────────────────────────────────

EXPECTED_TIERS = [
    # (confidence, expected_tier, label)
    (0.86,  "gold",         "above gold boundary"),
    (0.85,  "gold",         "exact gold boundary — inclusive"),
    (0.84,  "production",   "just below gold boundary"),
    (0.71,  "production",   "above production boundary"),
    (0.70,  "production",   "exact production boundary — inclusive"),
    (0.69,  "experimental", "just below production boundary"),
    (0.51,  "experimental", "above experimental boundary"),
    (0.50,  "experimental", "exact experimental boundary — inclusive"),
    (0.49,  "demoted",      "just below experimental boundary"),
    (0.00,  "demoted",      "zero confidence"),
    (1.00,  "gold",         "max confidence"),
]


class TestLearningIntegrationTier:
    """_confidence_tier and determine_tier in learning_integration.py"""

    @pytest.mark.parametrize("confidence,expected,label", EXPECTED_TIERS)
    def test_confidence_tier(self, confidence, expected, label):
        result = li_tier(confidence)
        assert result == expected, (
            f"learning_integration._confidence_tier({confidence}) → '{result}' "
            f"(expected '{expected}') [{label}]"
        )

    @pytest.mark.parametrize("confidence,expected,label", EXPECTED_TIERS)
    def test_determine_tier(self, confidence, expected, label):
        result = determine_tier(confidence)
        assert result == expected, (
            f"determine_tier({confidence}) → '{result}' "
            f"(expected '{expected}') [{label}]"
        )


class TestLearningEngineTier:
    """_confidence_tier in learning_engine.py"""

    @pytest.mark.parametrize("confidence,expected,label", EXPECTED_TIERS)
    def test_confidence_tier(self, confidence, expected, label):
        result = le_tier(confidence)
        assert result == expected, (
            f"learning_engine._confidence_tier({confidence}) → '{result}' "
            f"(expected '{expected}') [{label}]"
        )


class TestDetectionEngineTier:
    """_confidence_tier_label in detection_engine.py

    Note: detection_engine uses its own label vocabulary (high/medium/low/insufficient)
    for failure detection context — separate from the pattern KB tiers.
    The boundary is already >= 0.85 (correct). We verify it uses inclusive boundaries.
    """

    # detection_engine labels: >= 0.85 → "high", >= 0.70 → "medium",
    #                           >= 0.50 → "low",  else → "insufficient"
    DETECTION_CASES = [
        (0.86,  "high"),
        (0.85,  "high"),
        (0.84,  "medium"),
        (0.70,  "medium"),
        (0.69,  "low"),
        (0.50,  "low"),
        (0.49,  "insufficient"),
    ]

    @pytest.mark.parametrize("confidence,expected", DETECTION_CASES)
    def test_confidence_tier_label(self, confidence, expected):
        result = de_tier(confidence)
        assert result == expected, (
            f"detection_engine._confidence_tier_label({confidence}) → '{result}' "
            f"(expected '{expected}')"
        )

    def test_high_boundary_is_inclusive(self):
        """Exact boundary 0.85 must map to 'high', not 'medium' (>= is inclusive)."""
        assert de_tier(0.85) == "high"

    def test_below_high_is_not_high(self):
        """0.84 must not be 'high'."""
        assert de_tier(0.84) != "high"


class TestMonitoringEngineTier:
    """_tier_from_confidence in monitoring_engine.py — fixed from > 0.85 to >= 0.85"""

    @pytest.mark.parametrize("confidence,expected,label", EXPECTED_TIERS)
    def test_tier_from_confidence(self, confidence, expected, label):
        result = me_tier(confidence)
        assert result == expected, (
            f"monitoring_engine._tier_from_confidence({confidence}) → '{result}' "
            f"(expected '{expected}') [{label}]"
        )

    def test_gold_boundary_inclusive_was_bug(self):
        """
        Regression test: previously _tier_from_confidence used > 0.85 (strict),
        so confidence 0.85 returned 'production' instead of 'gold'.
        This test ensures the fix is permanent.
        """
        assert me_tier(0.85) == "gold", (
            "confidence 0.85 must be 'gold' — boundary is inclusive (>= 0.85)"
        )


class TestSafetyEngineGuardrail:
    """G6 guardrail in safety_engine.py — checks that confidence >= 0.85 triggers the guardrail."""

    def _make_pattern(self, confidence):
        return {"id": "test-pattern", "confidence": confidence}

    def test_g6_fires_at_exact_boundary_085(self):
        """Pattern with confidence 0.85 must trigger G6 (gold-tier guardrail)."""
        from akc_service.safety_engine import check_guardrails
        result = check_guardrails(pattern_entry=self._make_pattern(0.85))
        assert result["guardrail_results"]["G6_high_confidence_patterns"] == "FAIL", (
            "G6 guardrail must FAIL for confidence == 0.85 (boundary value, no override_key provided)"
        )

    def test_g6_fires_above_085(self):
        """Pattern with confidence 0.86 must trigger G6."""
        from akc_service.safety_engine import check_guardrails
        result = check_guardrails(pattern_entry=self._make_pattern(0.86))
        assert result["guardrail_results"]["G6_high_confidence_patterns"] == "FAIL"

    def test_g6_does_not_fire_below_085(self):
        """Pattern with confidence 0.84 must NOT trigger G6."""
        from akc_service.safety_engine import check_guardrails
        result = check_guardrails(pattern_entry=self._make_pattern(0.84))
        assert result["guardrail_results"]["G6_high_confidence_patterns"] == "PASS", (
            "G6 guardrail must PASS for confidence == 0.84 (below gold threshold)"
        )

    def test_g6_passes_with_valid_override_key_at_boundary(self):
        """Pattern with confidence 0.85 and a valid override key must pass G6."""
        from akc_service.safety_engine import check_guardrails
        result = check_guardrails(
            pattern_entry=self._make_pattern(0.85),
            override_key="OVERRIDE-ABC12345",
        )
        assert result["guardrail_results"]["G6_high_confidence_patterns"] == "PASS"

    def test_g6_fails_with_invalid_override_key_at_boundary(self):
        """Pattern with confidence 0.85 and an invalid override key must fail G6."""
        from akc_service.safety_engine import check_guardrails
        result = check_guardrails(
            pattern_entry=self._make_pattern(0.85),
            override_key="bad-key",
        )
        assert result["guardrail_results"]["G6_high_confidence_patterns"] == "FAIL"


class TestCspSolverGuardrail:
    """G6 guardrail in csp_solver.py — fixed from > 0.85 to >= 0.85."""

    def test_g6_fires_at_085_for_high_confidence_modification(self):
        """high_confidence_pattern_modification with confidence 0.85 must violate G6."""
        from akc_service.csp_solver import check_guardrails
        passed, violated = check_guardrails(
            modification_type="high_confidence_pattern_modification",
            pattern={"confidence": 0.85},
        )
        assert "G6_high_confidence_patterns" in violated, (
            "csp_solver G6 must trigger at confidence 0.85 (>= threshold)"
        )

    def test_g6_fires_at_086(self):
        """Confidence 0.86 must also violate G6."""
        from akc_service.csp_solver import check_guardrails
        passed, violated = check_guardrails(
            modification_type="high_confidence_pattern_modification",
            pattern={"confidence": 0.86},
        )
        assert "G6_high_confidence_patterns" in violated

    def test_g6_does_not_fire_at_084(self):
        """Confidence 0.84 must not violate G6 for non-blocked modification type."""
        from akc_service.csp_solver import check_guardrails
        passed, violated = check_guardrails(
            modification_type="add_bounds_check",
            pattern={"confidence": 0.84},
        )
        assert "G6_high_confidence_patterns" not in violated


class TestNormalizePatternTier:
    """normalize_pattern_tier in learning_integration.py — idempotency and correctness."""

    @pytest.mark.parametrize("confidence,expected_tier,label", EXPECTED_TIERS)
    def test_normalizes_tier_correctly(self, confidence, expected_tier, label):
        pattern = {"id": "p1", "confidence": confidence, "confidence_tier": "WRONG"}
        result = normalize_pattern_tier(pattern)
        assert result["confidence_tier"] == expected_tier, (
            f"normalize_pattern_tier({confidence}) set tier to '{result['confidence_tier']}', "
            f"expected '{expected_tier}' [{label}]"
        )

    def test_idempotent_gold(self):
        """Running normalize twice on a gold pattern yields the same result."""
        pattern = {"id": "p1", "confidence": 0.85, "confidence_tier": "production"}
        normalize_pattern_tier(pattern)
        tier_after_first = pattern["confidence_tier"]
        normalize_pattern_tier(pattern)
        tier_after_second = pattern["confidence_tier"]
        assert tier_after_first == tier_after_second == "gold"

    def test_idempotent_production(self):
        pattern = {"id": "p1", "confidence": 0.75, "confidence_tier": "gold"}
        normalize_pattern_tier(pattern)
        assert pattern["confidence_tier"] == "production"
        normalize_pattern_tier(pattern)
        assert pattern["confidence_tier"] == "production"

    def test_returns_same_dict(self):
        pattern = {"id": "p1", "confidence": 0.85}
        result = normalize_pattern_tier(pattern)
        assert result is pattern, "normalize_pattern_tier should mutate and return the same dict"

    def test_stale_tier_corrected_for_boundary_value(self):
        """
        Regression: a pattern written with confidence 0.85 but tier 'production'
        (due to old > 0.85 bug) is corrected to 'gold' after normalization.
        """
        stale_pattern = {
            "id": "p-stale",
            "confidence": 0.85,
            "confidence_tier": "production",  # stale from old buggy code
        }
        normalize_pattern_tier(stale_pattern)
        assert stale_pattern["confidence_tier"] == "gold"


class TestAllTierFunctionsAgree:
    """
    Cross-module consistency: all canonical tier functions must agree on boundary values.
    detection_engine uses different tier names (silver/bronze instead of production/experimental)
    so it's excluded here; only the learning/monitoring tier functions are compared.
    """

    CANONICAL_FUNCTIONS = [
        ("learning_integration._confidence_tier", li_tier),
        ("learning_integration.determine_tier",   determine_tier),
        ("learning_engine._confidence_tier",      le_tier),
        ("monitoring_engine._tier_from_confidence", me_tier),
    ]

    BOUNDARY_VALUES = [0.84, 0.85, 0.86, 0.69, 0.70, 0.71, 0.49, 0.50, 0.51]

    @pytest.mark.parametrize("confidence", BOUNDARY_VALUES)
    def test_all_functions_agree(self, confidence):
        results = {name: fn(confidence) for name, fn in self.CANONICAL_FUNCTIONS}
        unique = set(results.values())
        assert len(unique) == 1, (
            f"Tier functions disagree for confidence={confidence}: {results}"
        )
