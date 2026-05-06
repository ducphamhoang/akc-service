"""
Central configuration module for AKC Service.

Provides centralized environment variable reading and defaults for all modules.
Other modules should import configuration values from this module instead of
reading environment variables directly.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


# ─── KB Directory ───────────────────────────────────────────────────────────

_DEFAULT_KB_DIR = Path(__file__).parent / "kb"
KB_DIR: Path = Path(os.environ.get("AKC_SERVICE_KB_DIR", str(_DEFAULT_KB_DIR)))


# ─── KB Export Configuration ─────────────────────────────────────────────────

PATTERNS_JSONL: Path = KB_DIR / "patterns.jsonl"
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


# ─── KB Routing ──────────────────────────────────────────────────────────────


@dataclass
class KBContext:
    path: Path
    name: str           # "physics", "default", etc.
    safety_level: int   # Global SAFETY_LEVEL for MVP; extensible to per-KB later


def _parse_kb_registry() -> Dict[str, str]:
    """Parse AKC_SERVICE_KB_REGISTRY JSON env var into a dict of {name: path_str}.

    Returns default registry pointing to package-internal kb/ when env var is absent.
    Raises ValueError with a descriptive message and example if JSON is invalid.
    """
    raw = os.environ.get("AKC_SERVICE_KB_REGISTRY")
    if raw is None:
        return {"default": str(_DEFAULT_KB_DIR)}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in AKC_SERVICE_KB_REGISTRY: {e}\n"
            f'Example: {{"default": "./kb/default", "physics": "./kb/physics"}}'
        ) from e
    if not isinstance(parsed, dict):
        raise ValueError(
            f"AKC_SERVICE_KB_REGISTRY must be a JSON object mapping KB names to paths, "
            f"got {type(parsed).__name__}.\n"
            f'Example: {{"default": "./kb/default", "physics": "./kb/physics"}}'
        )
    return {str(k): str(v) for k, v in parsed.items()}


def _parse_entity_kb_mapping() -> Dict[str, str]:
    """Parse AKC_SERVICE_ENTITY_KB_MAPPING JSON env var into a dict of {entity_key: kb_name}.

    Returns default wildcard mapping to "default" KB when env var is absent.
    Raises ValueError with a descriptive message and example if JSON is invalid.
    """
    raw = os.environ.get("AKC_SERVICE_ENTITY_KB_MAPPING")
    if raw is None:
        return {"entity:*": "default"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in AKC_SERVICE_ENTITY_KB_MAPPING: {e}\n"
            f'Example: {{"entity:physics": "physics", "entity:*": "default"}}'
        ) from e
    if not isinstance(parsed, dict):
        raise ValueError(
            f"AKC_SERVICE_ENTITY_KB_MAPPING must be a JSON object, "
            f"got {type(parsed).__name__}.\n"
            f'Example: {{"entity:physics": "physics", "entity:*": "default"}}'
        )
    return {str(k): str(v) for k, v in parsed.items()}


KB_REGISTRY: Dict[str, str] = _parse_kb_registry()
ENTITY_KB_MAPPING: Dict[str, str] = _parse_entity_kb_mapping()


def validate_kb_config(
    registry: Optional[Dict[str, str]] = None,
    mapping: Optional[Dict[str, str]] = None,
) -> None:
    """Validate KB registry and entity mapping at startup.

    Per D-03: raises ValueError when ENTITY_KB_MAPPING references a KB name
    not present in KB_REGISTRY. Per D-04: logs WARNING (does not raise) for
    missing KB directories. Per D-08: logs registered KB names and mapping count.
    """
    if registry is None:
        registry = KB_REGISTRY
    if mapping is None:
        mapping = ENTITY_KB_MAPPING

    # Validate entity mapping references known KBs
    for mapping_key, kb_name in mapping.items():
        if kb_name not in registry:
            raise ValueError(
                f"ENTITY_KB_MAPPING references unknown KB: '{kb_name}' "
                f"(mapping key: {mapping_key}). "
                f"Available KBs: {list(registry.keys())}"
            )

    # Warn on missing directories (D-04: warn, don't fail)
    for kb_name, path_str in registry.items():
        kb_path = Path(path_str)
        if not kb_path.exists():
            logger.warning(
                f"KB directory does not exist (will be created on first write): "
                f"kb_name={kb_name!r} path={kb_path}"
            )

    # Startup logging (D-08)
    logger.info(f"KB Registry loaded: {list(registry.keys())}")
    logger.info(f"Entity mappings loaded: {len(mapping)} entries")


def resolve_kb_dir(
    kb_override: Optional[str] = None,
    entity: Optional[str] = None,
    global_safety_level: int = SAFETY_LEVEL,
) -> KBContext:
    """Resolve which KB directory to use for a request.

    Resolution tiers (per D-06):
      Tier 1: explicit kb_override — highest priority
      Tier 2: entity-based inference from ENTITY_KB_MAPPING
      Tier 3: default KB — final fallback

    Performance: dict lookups only, zero file I/O (per D-07, ROUTE-06).
    """
    kb_name: Optional[str] = None
    routing_tier: str

    # Tier 1: explicit override
    if kb_override and kb_override in KB_REGISTRY:
        kb_name = kb_override
        routing_tier = "explicit"
    # Tier 2: entity-based inference
    elif entity:
        pattern_key = f"entity:{entity}"
        if pattern_key in ENTITY_KB_MAPPING:
            kb_name = ENTITY_KB_MAPPING[pattern_key]
            routing_tier = "entity_mapping"
        else:
            kb_name = ENTITY_KB_MAPPING.get("entity:*", "default")
            routing_tier = "entity_wildcard"
    else:
        routing_tier = "fallback"

    # Tier 3: fallback to default
    if kb_name is None:
        kb_name = "default"

    path = Path(KB_REGISTRY[kb_name])

    logger.info(
        f"KB routing: {routing_tier}={kb_override or entity} "
        f"→ kb_name={kb_name} → {path}"
    )

    return KBContext(path=path, name=kb_name, safety_level=global_safety_level)


# ─── Startup Validation ──────────────────────────────────────────────────────
validate_kb_config()
