"""
Central configuration module for AKC Service.

Provides centralized environment variable reading and defaults for all modules.
Other modules should import configuration values from this module instead of
reading environment variables directly.
"""

import os
from pathlib import Path


# ─── KB Directory ───────────────────────────────────────────────────────────

_DEFAULT_KB_DIR = Path(__file__).parent / "kb"
KB_DIR: Path = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))


# ─── KB Export Configuration ─────────────────────────────────────────────────

KB_EXPORT_DIR: Path = Path(os.environ.get("AKC_SERVICE_KB_EXPORT_DIR", "./kb_export"))
KB_EXPORT_FORMAT: str = os.environ.get("AKC_SERVICE_KB_EXPORT_FORMAT", "by-entity")
KB_EXPORT_MIN_CONFIDENCE: float = float(os.environ.get("AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE", "0.0"))


# ─── Safety Level ───────────────────────────────────────────────────────────

def _read_safety_level() -> int:
    """Read SAFETY_LEVEL from env var with validation and clamping."""
    raw = os.environ.get("AKC_SERVICE_SAFETY_LEVEL", "1")
    try:
        level = int(raw)
        if level in (0, 1, 2):
            return level
        return 1
    except (ValueError, TypeError):
        return 1


SAFETY_LEVEL: int = _read_safety_level()


# ─── AKC URL ────────────────────────────────────────────────────────────────

AKC_URL: str = os.environ.get("AKC_SERVICE_URL", "http://localhost:8000")


# ─── Log Level ───────────────────────────────────────────────────────────────

LOG_LEVEL: str = os.environ.get("AKC_SERVICE_LOG_LEVEL", "INFO").upper()


# ─── Max Delta for Safety Level ──────────────────────────────────────────────

def max_delta_for_level(level: int) -> float:
    """
    Get the maximum confidence delta for a given safety level.

    Args:
        level: Safety level (0, 1, or 2)

    Returns:
        Maximum confidence delta value for the given level.
        Defaults to level 1's value (0.15) for invalid levels.
    """
    deltas = {
        0: 0.25,
        1: 0.15,
        2: 0.10,
    }
    return deltas.get(level, 0.15)
