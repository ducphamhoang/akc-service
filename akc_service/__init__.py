"""akc-service: Agent Knowledge Collective learning and safety engine."""

__version__ = "0.1.0"

# Public API — key functions from core engines
from .learning_engine import update_confidence, analyze_kb, version_pattern  # noqa: F401
from .safety_engine import check_guardrails, route_fix  # noqa: F401
