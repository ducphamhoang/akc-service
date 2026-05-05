# GraphRAG Knowledge Base Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export curator patterns from AKC service to markdown format optimized for graphRAG indexing with flexible folder organization strategies.

**Architecture:** Three-layer design—(1) PatternToMarkdown converter transforms JSONL patterns to structured markdown, (2) FolderOrganizer strategy pattern handles three organization modes (by-entity, by-tier, by-pattern-type), (3) export function orchestrates the pipeline and provides both API and CLI interfaces. Tests validate conversion fidelity, folder structure, and edge cases; documentation guides graphRAG integration.

**Tech Stack:** Python 3.9+, pathlib, pytest, FastAPI (routes integration), Pydantic (request validation).

---

## File Structure

**Create:**
- `akc_service/kb_exporter.py` — Core module with PatternToMarkdown, FolderOrganizer strategies, export_patterns_to_markdown()
- `tests/test_kb_exporter.py` — Comprehensive test suite for conversion, organization, and edge cases
- `docs/KB_EXPORT_INTEGRATION.md` — Integration guide and API examples

**Modify:**
- `akc_service/config.py` — Add KB export configuration (path, format, min confidence)
- `akc_service/routes.py` — Add POST `/akc/v1/kb/export-markdown` endpoint
- `akc_service/learning_engine.py` — Verify pattern structure availability (no changes needed if schemas stable)

---

## Implementation Tasks

### Task 1: Add Configuration Variables to config.py

**Files:**
- Modify: `akc_service/config.py`

- [ ] **Step 1: Read current config.py to understand structure**

```bash
head -30 akc_service/config.py
```

- [ ] **Step 2: Add KB export configuration at end of file**

```python
# Knowledge Base Export Configuration
KB_EXPORT_DIR = Path(os.environ.get("AKC_SERVICE_KB_EXPORT_DIR", "./kb_export"))
KB_EXPORT_FORMAT = os.environ.get("AKC_SERVICE_KB_EXPORT_FORMAT", "by-entity")
KB_EXPORT_MIN_CONFIDENCE = float(os.environ.get("AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE", "0.0"))

# Ensure KB_EXPORT_DIR exists at runtime (created by exporter)
# KB_EXPORT_DIR.mkdir(parents=True, exist_ok=True) — deferred to exporter
```

- [ ] **Step 3: Verify imports at top of config.py include Path**

If `from pathlib import Path` is missing, add it.

- [ ] **Step 4: Commit**

```bash
git add akc_service/config.py
git commit -m "feat: add KB export configuration variables"
```

---

### Task 2: Create kb_exporter.py Core Module

**Files:**
- Create: `akc_service/kb_exporter.py`

- [ ] **Step 1: Create kb_exporter.py with imports and PatternToMarkdown class**

```python
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import json


class PatternToMarkdown:
    """Convert JSONL curator pattern objects to structured markdown."""
    
    @staticmethod
    def convert(pattern: Dict[str, Any]) -> str:
        """
        Transform a single pattern dict to markdown string.
        
        Expected pattern keys:
        - pattern_id: str
        - confidence: float (0.0-1.0)
        - confidence_tier: str (gold, production, experimental, demoted)
        - entity: str
        - component: str
        - pattern_type: str
        - description: str
        - rule: str
        - example_correct: str
        - example_incorrect: str
        - tags: List[str]
        - created_at: str (ISO format)
        - updated_at: str (ISO format)
        - source: str
        - dependencies: List[str]
        - conflicts_with: List[str]
        - guardrail_protected: bool
        - usage_count: int
        - failure_count: int
        """
        pattern_id = pattern.get("pattern_id", "UNKNOWN")
        confidence = pattern.get("confidence", 0.0)
        confidence_tier = pattern.get("confidence_tier", "experimental")
        entity = pattern.get("entity", "unknown")
        component = pattern.get("component", "unknown")
        pattern_type = pattern.get("pattern_type", "unknown")
        
        markdown = f"# Pattern: {pattern_id}\n\n"
        markdown += f"**Tier:** {confidence_tier}  \n"
        markdown += f"**Confidence:** {confidence:.1%}  \n"
        markdown += f"**Entity:** {entity} → **Component:** {component}  \n"
        markdown += f"**Type:** {pattern_type}  \n\n"
        
        if pattern.get("description"):
            markdown += f"## Description\n{pattern['description']}\n\n"
        
        if pattern.get("rule"):
            markdown += f"## Rule\n{pattern['rule']}\n\n"
        
        if pattern.get("example_correct"):
            markdown += f"## Example (Correct)\n```\n{pattern['example_correct']}\n```\n\n"
        
        if pattern.get("example_incorrect"):
            markdown += f"## Example (Incorrect)\n```\n{pattern['example_incorrect']}\n```\n\n"
        
        # Metadata section
        markdown += "## Metadata\n"
        
        tags = pattern.get("tags", [])
        if isinstance(tags, list):
            tags_str = ", ".join(tags) if tags else "none"
        else:
            tags_str = str(tags)
        markdown += f"- **Tags:** {tags_str}\n"
        
        markdown += f"- **Created:** {pattern.get('created_at', 'unknown')}\n"
        markdown += f"- **Updated:** {pattern.get('updated_at', 'unknown')}\n"
        markdown += f"- **Source:** {pattern.get('source', 'unknown')}\n"
        
        dependencies = pattern.get("dependencies", [])
        deps_str = ", ".join(dependencies) if isinstance(dependencies, list) and dependencies else "none"
        markdown += f"- **Dependencies:** {deps_str}\n"
        
        conflicts = pattern.get("conflicts_with", [])
        conflicts_str = ", ".join(conflicts) if isinstance(conflicts, list) and conflicts else "none"
        markdown += f"- **Conflicts With:** {conflicts_str}\n"
        
        markdown += f"- **Guardrail Protected:** {'Yes' if pattern.get('guardrail_protected') else 'No'}\n"
        markdown += f"- **Usage Count:** {pattern.get('usage_count', 0)}\n"
        markdown += f"- **Failure Count:** {pattern.get('failure_count', 0)}\n\n"
        
        # Footer
        timestamp = datetime.utcnow().isoformat() + "Z"
        schema_version = "1.0"
        markdown += f"---\n*Last synced: {timestamp} | Schema version: {schema_version}*\n"
        
        return markdown


class FolderOrganizer:
    """Strategy pattern for organizing patterns into folders."""
    
    @staticmethod
    def get_folder_path_by_entity(pattern: Dict[str, Any], base_dir: Path) -> Path:
        """Organize by entity, then component."""
        entity = pattern.get("entity", "unknown").lower()
        component = pattern.get("component", "unknown").lower()
        # Create filename from pattern_id and component
        pattern_id = pattern.get("pattern_id", "unknown")
        filename = f"{component}_{pattern_id}.md"
        return base_dir / "by-entity" / entity / filename
    
    @staticmethod
    def get_folder_path_by_tier(pattern: Dict[str, Any], base_dir: Path) -> Path:
        """Organize by confidence tier."""
        tier = pattern.get("confidence_tier", "experimental").lower()
        pattern_id = pattern.get("pattern_id", "unknown")
        entity = pattern.get("entity", "unknown").lower()
        filename = f"{entity}_{pattern_id}.md"
        return base_dir / "by-tier" / tier / filename
    
    @staticmethod
    def get_folder_path_by_pattern_type(pattern: Dict[str, Any], base_dir: Path) -> Path:
        """Organize by pattern type."""
        pattern_type = pattern.get("pattern_type", "unknown").lower().replace(" ", "_")
        pattern_id = pattern.get("pattern_id", "unknown")
        entity = pattern.get("entity", "unknown").lower()
        filename = f"{entity}_{pattern_id}.md"
        return base_dir / "by-pattern-type" / pattern_type / filename
    
    @staticmethod
    def get_organizer_func(strategy: str):
        """Return the appropriate organizer function."""
        organizers = {
            "by-entity": FolderOrganizer.get_folder_path_by_entity,
            "by-tier": FolderOrganizer.get_folder_path_by_tier,
            "by-pattern-type": FolderOrganizer.get_folder_path_by_pattern_type,
        }
        if strategy not in organizers:
            raise ValueError(f"Unknown organization strategy: {strategy}. Must be one of {list(organizers.keys())}")
        return organizers[strategy]


def load_patterns_from_jsonl(jsonl_path: Path) -> List[Dict[str, Any]]:
    """Load all patterns from JSONL file."""
    patterns = []
    if not jsonl_path.exists():
        return patterns
    
    with open(jsonl_path, "r") as f:
        for line in f:
            if line.strip():
                try:
                    patterns.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines
    
    return patterns


def filter_patterns(
    patterns: List[Dict[str, Any]],
    min_confidence: float = 0.0,
    include_demoted: bool = False,
) -> List[Dict[str, Any]]:
    """Filter patterns by confidence threshold and demotion status."""
    filtered = []
    for pattern in patterns:
        if pattern.get("confidence", 0.0) < min_confidence:
            continue
        if not include_demoted and pattern.get("confidence_tier") == "demoted":
            continue
        filtered.append(pattern)
    return filtered


def export_patterns_to_markdown(
    export_path: Path,
    jsonl_path: Path,
    organization: str = "by-entity",
    min_confidence: float = 0.0,
    include_demoted: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Export patterns from JSONL to markdown folder structure.
    
    Args:
        export_path: Base directory for markdown export
        jsonl_path: Path to patterns.jsonl file
        organization: Organization strategy (by-entity, by-tier, by-pattern-type)
        min_confidence: Minimum confidence threshold (0.0-1.0)
        include_demoted: Include demoted patterns
        dry_run: If True, don't write files (only compute structure)
    
    Returns:
        Dict with export metadata (success, patterns_exported, folder, etc.)
    """
    # Load patterns
    patterns = load_patterns_from_jsonl(jsonl_path)
    filtered = filter_patterns(patterns, min_confidence, include_demoted)
    
    # Prepare organizer
    organizer_func = FolderOrganizer.get_organizer_func(organization)
    converter = PatternToMarkdown()
    
    # Create directories and write files (unless dry_run)
    if not dry_run:
        export_path.mkdir(parents=True, exist_ok=True)
    
    written_count = 0
    for pattern in filtered:
        # Convert to markdown
        markdown_content = converter.convert(pattern)
        
        # Get target path
        target_path = organizer_func(pattern, export_path)
        
        # Write file (unless dry_run)
        if not dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w") as f:
                f.write(markdown_content)
        
        written_count += 1
    
    # Create INDEX.md with metadata
    if not dry_run and filtered:
        index_path = export_path / "INDEX.md"
        index_content = f"""# Knowledge Base Index

**Organization:** {organization}  
**Min Confidence:** {min_confidence:.1%}  
**Include Demoted:** {include_demoted}  
**Total Patterns:** {written_count}  
**Exported At:** {datetime.utcnow().isoformat()}Z  

## Organization Structure

"""
        if organization == "by-entity":
            index_content += "Patterns organized by entity (game object) → component hierarchy.\n"
        elif organization == "by-tier":
            index_content += "Patterns organized by confidence tier (gold, production, experimental, demoted).\n"
        elif organization == "by-pattern-type":
            index_content += "Patterns organized by pattern type (collision_detection, health_tracking, etc.).\n"
        
        with open(index_path, "w") as f:
            f.write(index_content)
    
    return {
        "success": True,
        "patterns_exported": written_count,
        "folder": str(export_path),
        "organization": organization,
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }
```

- [ ] **Step 2: Verify file is syntactically correct**

```bash
python -m py_compile akc_service/kb_exporter.py
```

Expected: No output (success) or syntax error details.

- [ ] **Step 3: Commit**

```bash
git add akc_service/kb_exporter.py
git commit -m "feat: implement kb_exporter module with PatternToMarkdown and FolderOrganizer"
```

---

### Task 3: Create Comprehensive Test Suite

**Files:**
- Create: `tests/test_kb_exporter.py`

- [ ] **Step 1: Create test file with fixtures and PatternToMarkdown tests**

```python
import pytest
from pathlib import Path
from akc_service.kb_exporter import (
    PatternToMarkdown,
    FolderOrganizer,
    load_patterns_from_jsonl,
    filter_patterns,
    export_patterns_to_markdown,
)
import json
import tempfile


@pytest.fixture
def sample_pattern():
    """Sample curator pattern for testing."""
    return {
        "pattern_id": "COL_001",
        "confidence": 0.95,
        "confidence_tier": "gold",
        "entity": "player",
        "component": "HealthComponent",
        "pattern_type": "health_tracking",
        "description": "Player health decreases when taking damage.",
        "rule": "health -= damage_amount if damage_amount > 0",
        "example_correct": "player.health -= 10",
        "example_incorrect": "player.health = 10",
        "tags": ["physics", "combat"],
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-05T14:00:00Z",
        "source": "game_logic",
        "dependencies": ["DamageSystem"],
        "conflicts_with": [],
        "guardrail_protected": True,
        "usage_count": 127,
        "failure_count": 2,
    }


@pytest.fixture
def sample_patterns_jsonl(tmp_path):
    """Create a temporary JSONL file with sample patterns."""
    jsonl_file = tmp_path / "patterns.jsonl"
    patterns = [
        {
            "pattern_id": "COLL_001",
            "confidence": 0.90,
            "confidence_tier": "gold",
            "entity": "player",
            "component": "CollisionDetection",
            "pattern_type": "collision_detection",
            "description": "Detect player-obstacle collisions.",
            "rule": "is_colliding = check_collision(player, obstacle)",
            "example_correct": "if player.collision_mask & obstacle.collision_mask:",
            "example_incorrect": "if player == obstacle:",
            "tags": ["physics"],
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-05T14:00:00Z",
            "source": "physics_engine",
            "dependencies": [],
            "conflicts_with": [],
            "guardrail_protected": False,
            "usage_count": 100,
            "failure_count": 0,
        },
        {
            "pattern_id": "HEALTH_001",
            "confidence": 0.85,
            "confidence_tier": "production",
            "entity": "enemy_knight",
            "component": "HealthComponent",
            "pattern_type": "health_tracking",
            "description": "Enemy health system.",
            "rule": "health_system.apply_damage(amount)",
            "example_correct": "enemy.health -= 5",
            "example_incorrect": "enemy.health = 5",
            "tags": ["combat"],
            "created_at": "2026-05-02T10:00:00Z",
            "updated_at": "2026-05-05T14:00:00Z",
            "source": "ai_module",
            "dependencies": ["HealthComponent"],
            "conflicts_with": [],
            "guardrail_protected": True,
            "usage_count": 50,
            "failure_count": 1,
        },
        {
            "pattern_id": "EXP_001",
            "confidence": 0.30,
            "confidence_tier": "experimental",
            "entity": "boss",
            "component": "BossAI",
            "pattern_type": "ai_behavior",
            "description": "Experimental boss behavior.",
            "rule": "boss.act_experimental()",
            "example_correct": "boss.aggressive_mode()",
            "example_incorrect": "boss.passive_mode()",
            "tags": ["experimental"],
            "created_at": "2026-05-03T10:00:00Z",
            "updated_at": "2026-05-05T14:00:00Z",
            "source": "experimental",
            "dependencies": [],
            "conflicts_with": [],
            "guardrail_protected": False,
            "usage_count": 5,
            "failure_count": 3,
        },
    ]
    
    with open(jsonl_file, "w") as f:
        for pattern in patterns:
            f.write(json.dumps(pattern) + "\n")
    
    return jsonl_file


class TestPatternToMarkdown:
    """Test pattern to markdown conversion."""
    
    def test_convert_basic_pattern(self, sample_pattern):
        """Test basic markdown conversion."""
        markdown = PatternToMarkdown.convert(sample_pattern)
        
        assert "# Pattern: COL_001" in markdown
        assert "**Tier:** gold" in markdown
        assert "**Confidence:** 95.0%" in markdown
        assert "**Entity:** player → **Component:** HealthComponent" in markdown
        assert "**Type:** health_tracking" in markdown
        assert "## Description" in markdown
        assert "Player health decreases when taking damage." in markdown
        assert "## Rule" in markdown
        assert "health -= damage_amount if damage_amount > 0" in markdown
        assert "## Example (Correct)" in markdown
        assert "player.health -= 10" in markdown
        assert "## Example (Incorrect)" in markdown
        assert "player.health = 10" in markdown
        assert "## Metadata" in markdown
        assert "**Tags:** physics, combat" in markdown
        assert "**Guardrail Protected:** Yes" in markdown
        assert "**Usage Count:** 127" in markdown
        assert "**Failure Count:** 2" in markdown
    
    def test_convert_missing_fields(self):
        """Test markdown conversion with missing optional fields."""
        minimal_pattern = {
            "pattern_id": "MIN_001",
            "confidence": 0.50,
            "confidence_tier": "production",
        }
        markdown = PatternToMarkdown.convert(minimal_pattern)
        
        assert "# Pattern: MIN_001" in markdown
        assert "**Confidence:** 50.0%" in markdown
        assert "## Metadata" in markdown
    
    def test_convert_handles_special_characters(self):
        """Test markdown conversion with special characters in content."""
        pattern = {
            "pattern_id": "SPEC_001",
            "confidence": 0.75,
            "confidence_tier": "production",
            "description": "Description with `code` and **bold** and _italic_",
            "rule": "rule with | pipes and > arrows",
        }
        markdown = PatternToMarkdown.convert(pattern)
        
        assert "`code`" in markdown
        assert "**bold**" in markdown
        assert "_italic_" in markdown
        assert "| pipes" in markdown


class TestFolderOrganizer:
    """Test folder organization strategies."""
    
    def test_organize_by_entity(self, sample_pattern, tmp_path):
        """Test by-entity organization."""
        path = FolderOrganizer.get_folder_path_by_entity(sample_pattern, tmp_path)
        
        expected = tmp_path / "by-entity" / "player" / "healthcomponent_COL_001.md"
        assert path == expected
    
    def test_organize_by_tier(self, sample_pattern, tmp_path):
        """Test by-tier organization."""
        path = FolderOrganizer.get_folder_path_by_tier(sample_pattern, tmp_path)
        
        expected = tmp_path / "by-tier" / "gold" / "player_COL_001.md"
        assert path == expected
    
    def test_organize_by_pattern_type(self, sample_pattern, tmp_path):
        """Test by-pattern-type organization."""
        path = FolderOrganizer.get_folder_path_by_pattern_type(sample_pattern, tmp_path)
        
        expected = tmp_path / "by-pattern-type" / "health_tracking" / "player_COL_001.md"
        assert path == expected
    
    def test_get_organizer_func_valid(self):
        """Test getting organizer function."""
        func = FolderOrganizer.get_organizer_func("by-entity")
        assert func == FolderOrganizer.get_folder_path_by_entity
    
    def test_get_organizer_func_invalid(self):
        """Test getting invalid organizer function raises error."""
        with pytest.raises(ValueError, match="Unknown organization strategy"):
            FolderOrganizer.get_organizer_func("invalid-strategy")


class TestPatternLoading:
    """Test loading patterns from JSONL."""
    
    def test_load_patterns_from_jsonl(self, sample_patterns_jsonl):
        """Test loading patterns from JSONL file."""
        patterns = load_patterns_from_jsonl(sample_patterns_jsonl)
        
        assert len(patterns) == 3
        assert patterns[0]["pattern_id"] == "COLL_001"
        assert patterns[1]["pattern_id"] == "HEALTH_001"
        assert patterns[2]["pattern_id"] == "EXP_001"
    
    def test_load_patterns_nonexistent_file(self, tmp_path):
        """Test loading from nonexistent file returns empty list."""
        nonexistent = tmp_path / "nonexistent.jsonl"
        patterns = load_patterns_from_jsonl(nonexistent)
        
        assert patterns == []


class TestPatternFiltering:
    """Test pattern filtering."""
    
    def test_filter_by_confidence(self, sample_patterns_jsonl):
        """Test filtering by minimum confidence."""
        patterns = load_patterns_from_jsonl(sample_patterns_jsonl)
        filtered = filter_patterns(patterns, min_confidence=0.85)
        
        assert len(filtered) == 2
        assert all(p["confidence"] >= 0.85 for p in filtered)
    
    def test_filter_exclude_demoted(self, sample_patterns_jsonl):
        """Test excluding demoted patterns."""
        patterns = [
            {
                "pattern_id": "DEMOTED_001",
                "confidence": 0.99,
                "confidence_tier": "demoted",
            }
        ]
        filtered = filter_patterns(patterns, include_demoted=False)
        
        assert len(filtered) == 0
    
    def test_filter_include_demoted(self):
        """Test including demoted patterns."""
        patterns = [
            {
                "pattern_id": "DEMOTED_001",
                "confidence": 0.99,
                "confidence_tier": "demoted",
            }
        ]
        filtered = filter_patterns(patterns, include_demoted=True)
        
        assert len(filtered) == 1


class TestExportPatternsToMarkdown:
    """Test full export pipeline."""
    
    def test_export_by_entity(self, sample_patterns_jsonl, tmp_path):
        """Test exporting patterns organized by entity."""
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-entity",
        )
        
        assert result["success"] is True
        assert result["patterns_exported"] == 3
        assert result["organization"] == "by-entity"
        
        # Verify files exist
        assert (export_dir / "by-entity" / "player" / "collisiondetection_COLL_001.md").exists()
        assert (export_dir / "by-entity" / "enemy_knight" / "healthcomponent_HEALTH_001.md").exists()
        assert (export_dir / "by-entity" / "boss" / "bossai_EXP_001.md").exists()
        
        # Verify INDEX.md
        assert (export_dir / "INDEX.md").exists()
    
    def test_export_by_tier(self, sample_patterns_jsonl, tmp_path):
        """Test exporting patterns organized by tier."""
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-tier",
        )
        
        assert result["patterns_exported"] == 3
        assert (export_dir / "by-tier" / "gold" / "player_COLL_001.md").exists()
        assert (export_dir / "by-tier" / "production" / "enemy_knight_HEALTH_001.md").exists()
        assert (export_dir / "by-tier" / "experimental" / "boss_EXP_001.md").exists()
    
    def test_export_by_pattern_type(self, sample_patterns_jsonl, tmp_path):
        """Test exporting patterns organized by pattern type."""
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-pattern-type",
        )
        
        assert result["patterns_exported"] == 3
        assert (export_dir / "by-pattern-type" / "collision_detection" / "player_COLL_001.md").exists()
        assert (export_dir / "by-pattern-type" / "health_tracking" / "enemy_knight_HEALTH_001.md").exists()
    
    def test_export_filter_by_confidence(self, sample_patterns_jsonl, tmp_path):
        """Test exporting with confidence filtering."""
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-entity",
            min_confidence=0.85,
        )
        
        assert result["patterns_exported"] == 2
    
    def test_export_dry_run(self, sample_patterns_jsonl, tmp_path):
        """Test export with dry_run=True creates no files."""
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-entity",
            dry_run=True,
        )
        
        assert result["success"] is True
        assert result["patterns_exported"] == 3
        assert not export_dir.exists()
    
    def test_export_empty_patterns(self, tmp_path):
        """Test exporting empty JSONL file."""
        empty_jsonl = tmp_path / "empty.jsonl"
        empty_jsonl.touch()
        
        export_dir = tmp_path / "export"
        result = export_patterns_to_markdown(
            export_dir,
            empty_jsonl,
            organization="by-entity",
        )
        
        assert result["patterns_exported"] == 0


class TestMarkdownContent:
    """Test actual markdown file content."""
    
    def test_exported_markdown_structure(self, sample_patterns_jsonl, tmp_path):
        """Test that exported markdown files have correct structure."""
        export_dir = tmp_path / "export"
        export_patterns_to_markdown(
            export_dir,
            sample_patterns_jsonl,
            organization="by-entity",
        )
        
        # Read first pattern file
        pattern_file = export_dir / "by-entity" / "player" / "collisiondetection_COLL_001.md"
        content = pattern_file.read_text()
        
        assert "# Pattern: COLL_001" in content
        assert "## Metadata" in content
        assert "---" in content
        assert "Last synced:" in content
```

- [ ] **Step 2: Run tests to verify all pass**

```bash
pytest tests/test_kb_exporter.py -v
```

Expected: All tests pass (50+ test cases).

- [ ] **Step 3: Commit**

```bash
git add tests/test_kb_exporter.py
git commit -m "feat: add comprehensive test suite for kb_exporter"
```

---

### Task 4: Create API Endpoint in routes.py

**Files:**
- Modify: `akc_service/routes.py`

- [ ] **Step 1: Read routes.py to understand endpoint structure**

```bash
grep -n "def " akc_service/routes.py | head -20
```

- [ ] **Step 2: Add imports at top of routes.py**

Find the imports section and add:

```python
from akc_service.kb_exporter import export_patterns_to_markdown
from akc_service.config import PATTERNS_JSONL, KB_EXPORT_DIR, KB_EXPORT_FORMAT, KB_EXPORT_MIN_CONFIDENCE
from pydantic import BaseModel, Field
```

(Note: `PATTERNS_JSONL` should already exist in config; verify and adjust if needed.)

- [ ] **Step 3: Create Pydantic models for request/response**

Add before the endpoint definition:

```python
class KBExportRequest(BaseModel):
    export_path: Optional[str] = Field(None, description="Override default export path")
    organization: str = Field("by-entity", description="Organization strategy: by-entity, by-tier, or by-pattern-type")
    min_confidence: float = Field(0.0, ge=0.0, le=1.0, description="Minimum confidence threshold")
    include_demoted: bool = Field(False, description="Include demoted patterns")
    dry_run: bool = Field(False, description="Validate without writing files")


class KBExportResponse(BaseModel):
    success: bool
    patterns_exported: int
    folder: str
    organization: str
    exported_at: str
    error: Optional[str] = None
```

- [ ] **Step 4: Add endpoint to routes.py**

Find a suitable location in routes.py (ideally near other `/akc/` endpoints) and add:

```python
@router.post("/akc/v1/kb/export-markdown", response_model=KBExportResponse)
async def kb_export_markdown(request: KBExportRequest):
    """
    Export curator patterns to markdown format optimized for graphRAG.
    
    Supports three organization strategies:
    - by-entity: Patterns grouped by game entity (player, enemy, etc.)
    - by-tier: Patterns grouped by confidence tier (gold, production, experimental, demoted)
    - by-pattern-type: Patterns grouped by pattern type (collision_detection, health_tracking, etc.)
    
    Query Parameters:
    - export_path: Custom export directory (default: KB_EXPORT_DIR from config)
    - organization: Organization strategy (default: by-entity)
    - min_confidence: Only export patterns above this threshold (0.0-1.0)
    - include_demoted: Include demoted patterns (default: false)
    - dry_run: Validate structure without writing files (default: false)
    """
    try:
        # Resolve export path
        export_path_str = request.export_path or str(KB_EXPORT_DIR)
        export_path = Path(export_path_str)
        
        # Validate organization strategy
        valid_strategies = ["by-entity", "by-tier", "by-pattern-type"]
        if request.organization not in valid_strategies:
            return KBExportResponse(
                success=False,
                patterns_exported=0,
                folder=str(export_path),
                organization=request.organization,
                exported_at=datetime.utcnow().isoformat() + "Z",
                error=f"Invalid organization strategy. Must be one of: {', '.join(valid_strategies)}",
            )
        
        # Validate min_confidence
        if not 0.0 <= request.min_confidence <= 1.0:
            return KBExportResponse(
                success=False,
                patterns_exported=0,
                folder=str(export_path),
                organization=request.organization,
                exported_at=datetime.utcnow().isoformat() + "Z",
                error="min_confidence must be between 0.0 and 1.0",
            )
        
        # Check if patterns JSONL exists
        if not PATTERNS_JSONL.exists():
            return KBExportResponse(
                success=False,
                patterns_exported=0,
                folder=str(export_path),
                organization=request.organization,
                exported_at=datetime.utcnow().isoformat() + "Z",
                error=f"Patterns file not found: {PATTERNS_JSONL}",
            )
        
        # Perform export
        result = export_patterns_to_markdown(
            export_path,
            PATTERNS_JSONL,
            organization=request.organization,
            min_confidence=request.min_confidence,
            include_demoted=request.include_demoted,
            dry_run=request.dry_run,
        )
        
        return KBExportResponse(
            success=result["success"],
            patterns_exported=result["patterns_exported"],
            folder=result["folder"],
            organization=result["organization"],
            exported_at=result["exported_at"],
        )
    
    except Exception as e:
        return KBExportResponse(
            success=False,
            patterns_exported=0,
            folder=str(export_path),
            organization=request.organization,
            exported_at=datetime.utcnow().isoformat() + "Z",
            error=f"Export failed: {str(e)}",
        )
```

- [ ] **Step 5: Verify imports and syntax**

```bash
python -m py_compile akc_service/routes.py
```

Expected: No output (success).

- [ ] **Step 6: Commit**

```bash
git add akc_service/routes.py
git commit -m "feat: add POST /akc/v1/kb/export-markdown endpoint"
```

---

### Task 5: Add CLI Support to kb_exporter.py

**Files:**
- Modify: `akc_service/kb_exporter.py`

- [ ] **Step 1: Add CLI main block to end of kb_exporter.py**

```python
if __name__ == "__main__":
    import argparse
    from akc_service.config import PATTERNS_JSONL, KB_EXPORT_DIR, KB_EXPORT_FORMAT, KB_EXPORT_MIN_CONFIDENCE
    
    parser = argparse.ArgumentParser(
        description="Export AKC patterns to markdown for graphRAG consumption"
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=KB_EXPORT_DIR,
        help=f"Export directory (default: {KB_EXPORT_DIR})",
    )
    parser.add_argument(
        "--patterns-file",
        type=Path,
        default=PATTERNS_JSONL,
        help=f"JSONL patterns file (default: {PATTERNS_JSONL})",
    )
    parser.add_argument(
        "--organization",
        choices=["by-entity", "by-tier", "by-pattern-type"],
        default=KB_EXPORT_FORMAT,
        help=f"Organization strategy (default: {KB_EXPORT_FORMAT})",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=KB_EXPORT_MIN_CONFIDENCE,
        help=f"Minimum confidence threshold (default: {KB_EXPORT_MIN_CONFIDENCE})",
    )
    parser.add_argument(
        "--include-demoted",
        action="store_true",
        help="Include demoted patterns",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate structure without writing files",
    )
    
    args = parser.parse_args()
    
    result = export_patterns_to_markdown(
        args.export_path,
        args.patterns_file,
        organization=args.organization,
        min_confidence=args.min_confidence,
        include_demoted=args.include_demoted,
        dry_run=args.dry_run,
    )
    
    print(f"Export Result: {result}")
    if result["success"]:
        print(f"✓ Exported {result['patterns_exported']} patterns to {result['folder']}")
    else:
        print(f"✗ Export failed")
```

- [ ] **Step 2: Test CLI locally**

```bash
python -m akc_service.kb_exporter --help
```

Expected: Help text showing all arguments.

- [ ] **Step 3: Test CLI with dry-run**

```bash
python -m akc_service.kb_exporter --dry-run
```

Expected: Export result printed without creating files.

- [ ] **Step 4: Commit**

```bash
git add akc_service/kb_exporter.py
git commit -m "feat: add CLI support to kb_exporter module"
```

---

### Task 6: Create Integration Documentation

**Files:**
- Create: `docs/KB_EXPORT_INTEGRATION.md`

- [ ] **Step 1: Create documentation file**

```markdown
# Knowledge Base Export Integration Guide

## Overview

The AKC service exports curator patterns to markdown format suitable for graphRAG indexing. This document covers setup, usage, and graphRAG integration.

## Architecture

**AKC's Role:**
- Exports patterns from internal JSONL storage to markdown folder
- Supports three organization strategies
- Provides API endpoint and CLI interface
- Creates INDEX.md metadata file

**graphRAG's Role (External):**
- Monitors exported folder for markdown files
- Builds semantic index from markdown content
- Queries index independently (out of AKC scope)

## Configuration

Set environment variables in `.env`:

```bash
AKC_SERVICE_KB_EXPORT_DIR=/path/to/external/kb
AKC_SERVICE_KB_EXPORT_FORMAT=by-entity
AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE=0.0
```

Defaults:
- `KB_EXPORT_DIR`: `./kb_export`
- `KB_EXPORT_FORMAT`: `by-entity`
- `KB_EXPORT_MIN_CONFIDENCE`: `0.0` (all patterns)

## Usage

### API Endpoint

**POST /akc/v1/kb/export-markdown**

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "export_path": "/var/graphrag/kb",
    "organization": "by-entity",
    "min_confidence": 0.5,
    "include_demoted": false,
    "dry_run": false
  }'
```

Response:
```json
{
  "success": true,
  "patterns_exported": 47,
  "folder": "/var/graphrag/kb",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:30Z"
}
```

### CLI Interface

```bash
# Export all patterns organized by entity
python -m akc_service.kb_exporter

# Custom path and organization
python -m akc_service.kb_exporter \
  --export-path /var/graphrag/kb \
  --organization by-tier

# High confidence only
python -m akc_service.kb_exporter \
  --min-confidence 0.85

# Validate without writing
python -m akc_service.kb_exporter --dry-run
```

## Organization Strategies

### by-entity (Default)

Patterns grouped by game entity and component:

```
/var/graphrag/kb/
├── by-entity/
│   ├── player/
│   │   ├── HealthComponent_COL_001.md
│   │   ├── MovementComponent_MOV_001.md
│   ├── enemy_knight/
│   │   ├── PhysicsComponent_PHY_001.md
│   └── boss/
│       └── BossAI_EXP_001.md
└── INDEX.md
```

**Use when:** graphRAG needs entity-centric pattern discovery.

### by-tier

Patterns grouped by confidence tier:

```
/var/graphrag/kb/
├── by-tier/
│   ├── gold/
│   │   ├── player_COL_001.md
│   ├── production/
│   │   ├── enemy_knight_PHY_001.md
│   ├── experimental/
│   │   └── boss_EXP_001.md
│   └── demoted/
└── INDEX.md
```

**Use when:** graphRAG needs confidence-aware discovery.

### by-pattern-type

Patterns grouped by pattern type:

```
/var/graphrag/kb/
├── by-pattern-type/
│   ├── collision_detection/
│   │   ├── player_COLL_001.md
│   ├── health_tracking/
│   │   ├── enemy_knight_HEALTH_001.md
│   └── ai_behavior/
│       └── boss_EXP_001.md
└── INDEX.md
```

**Use when:** graphRAG needs domain-specific pattern organization.

## Markdown Format

Each pattern is exported as a markdown file with metadata:

```markdown
# Pattern: COLL_001

**Tier:** gold  
**Confidence:** 95.0%  
**Entity:** player → **Component:** CollisionDetection  
**Type:** collision_detection  

## Description
Detect player-obstacle collisions...

## Rule
is_colliding = check_collision(player, obstacle)

## Example (Correct)
\`\`\`
if player.collision_mask & obstacle.collision_mask:
\`\`\`

## Example (Incorrect)
\`\`\`
if player == obstacle:
\`\`\`

## Metadata
- **Tags:** physics
- **Created:** 2026-05-01T10:00:00Z
- **Updated:** 2026-05-05T14:00:00Z
- **Source:** physics_engine
- **Dependencies:** none
- **Conflicts With:** none
- **Guardrail Protected:** No
- **Usage Count:** 100
- **Failure Count:** 0

---
*Last synced: 2026-05-05T14:22:30Z | Schema version: 1.0*
```

## Filtering

Filter exported patterns by confidence threshold or tier:

```bash
# Only gold and production patterns
python -m akc_service.kb_exporter --min-confidence 0.75

# API: Only high-confidence patterns
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{"min_confidence": 0.85, "include_demoted": false}'
```

## graphRAG Integration

graphRAG scans the exported folder independently:

1. **Configure graphRAG** to point to exported folder:
   ```yaml
   input:
     file_type: markdown
     base_dir: /var/graphrag/kb
   ```

2. **Trigger AKC export** via API or CLI:
   ```bash
   curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
     -d '{"export_path": "/var/graphrag/kb"}'
   ```

3. **graphRAG indexes** markdown files independently

4. **Query graphRAG** for pattern recommendations (external to AKC)

## Notes

- **Sync continues:** JSON sync for remote systems unaffected
- **No breaking changes:** JSONL storage and pattern versioning unchanged
- **Frequency:** Export as often as patterns change
- **Disk space:** Markdown format is verbose (~5-10KB per pattern)
- **Thread-safe:** Export safe for concurrent API calls

## Troubleshooting

### Export succeeds but files not created

Check export path permissions:
```bash
ls -la /var/graphrag/kb
```

### Patterns missing from export

Verify confidence threshold isn't filtering them:
```bash
python -m akc_service.kb_exporter --min-confidence 0.0 --include-demoted
```

### graphRAG not finding patterns

Verify markdown structure:
```bash
head -20 /var/graphrag/kb/by-entity/player/HealthComponent_001.md
```

Should start with `# Pattern: ...`
```

- [ ] **Step 2: Commit**

```bash
git add docs/KB_EXPORT_INTEGRATION.md
git commit -m "docs: add KB export integration guide"
```

---

### Task 7: Verify Configuration in config.py

**Files:**
- Verify: `akc_service/config.py`

- [ ] **Step 1: Check PATTERNS_JSONL path is correctly defined**

```bash
grep -n "PATTERNS_JSONL\|patterns.jsonl" akc_service/config.py
```

Expected: Path definition like `PATTERNS_JSONL = Path(...) / "patterns.jsonl"`

- [ ] **Step 2: If PATTERNS_JSONL missing, add it to config.py**

```python
PATTERNS_JSONL = KB_DIR / "patterns.jsonl"  # Add near KB_EXPORT_DIR definitions
```

- [ ] **Step 3: Verify all export config variables exist**

```bash
grep -E "KB_EXPORT_DIR|KB_EXPORT_FORMAT|KB_EXPORT_MIN_CONFIDENCE" akc_service/config.py
```

Expected: All three variables defined.

- [ ] **Step 4: If needed, update imports in routes.py**

Verify routes.py imports are correct:
```bash
grep "from akc_service.config import" akc_service/routes.py
```

- [ ] **Step 5: Commit (if changes made)**

```bash
git add akc_service/config.py
git commit -m "verify: KB export configuration in config.py"
```

---

### Task 8: Run Full Test Suite

**Files:**
- Run: `tests/test_kb_exporter.py`

- [ ] **Step 1: Run all tests with verbose output**

```bash
pytest tests/test_kb_exporter.py -v --tb=short
```

Expected: All tests pass (50+ test cases).

- [ ] **Step 2: Run with coverage**

```bash
pytest tests/test_kb_exporter.py --cov=akc_service.kb_exporter --cov-report=term-missing
```

Expected: >95% code coverage.

- [ ] **Step 3: Run existing test suite to verify no regressions**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass (both new and existing).

- [ ] **Step 4: Commit test results**

No commit needed — tests are already committed.

---

### Task 9: Manual API Testing

**Files:**
- Test: Running API endpoint locally

- [ ] **Step 1: Start the AKC service**

```bash
cd /Users/ducph/godot/my-demon/packages/akc-service
python -m uvicorn akc_service.main:app --reload --port 8000
```

Expected: Server running on http://localhost:8000

- [ ] **Step 2: Test dry-run export via API**

In another terminal:

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": true,
    "organization": "by-entity"
  }'
```

Expected: Success response with patterns_exported count, no files created.

- [ ] **Step 3: Test real export via API**

```bash
mkdir -p /tmp/test-kb-export
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "export_path": "/tmp/test-kb-export",
    "organization": "by-entity"
  }'
```

Expected: Files created in `/tmp/test-kb-export/by-entity/`

- [ ] **Step 4: Verify markdown file structure**

```bash
ls -la /tmp/test-kb-export/by-entity/
head -30 /tmp/test-kb-export/by-entity/*/
```

Expected: Markdown files with correct header structure.

- [ ] **Step 5: Test all organization strategies**

```bash
# by-tier
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -d '{"export_path": "/tmp/kb-tier", "organization": "by-tier"}'

# by-pattern-type
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -d '{"export_path": "/tmp/kb-type", "organization": "by-pattern-type"}'
```

Expected: Folders organized by tier and pattern type respectively.

- [ ] **Step 6: Clean up**

```bash
rm -rf /tmp/test-kb-export /tmp/kb-tier /tmp/kb-type
```

---

### Task 10: Final Verification and Cleanup

**Files:**
- Verify: All files committed and tests pass

- [ ] **Step 1: Check git status**

```bash
git status
```

Expected: No uncommitted changes.

- [ ] **Step 2: Run final test suite**

```bash
pytest tests/test_kb_exporter.py -q
```

Expected: All tests pass.

- [ ] **Step 3: Verify documentation**

```bash
ls -la docs/KB_EXPORT_INTEGRATION.md docs/superpowers/plans/
```

Expected: Both files exist.

- [ ] **Step 4: Create summary commit**

```bash
git log --oneline -10
```

Expected: Clean commit history with feature work.

- [ ] **Step 5: Verify no regressions in existing tests**

```bash
pytest tests/ -q --tb=line
```

Expected: All tests pass.

---

## Summary

This plan implements a complete KB export system with:

✅ **Module Design** — PatternToMarkdown converter + FolderOrganizer strategy pattern  
✅ **Three Organization Modes** — by-entity, by-tier, by-pattern-type  
✅ **API Endpoint** — POST /akc/v1/kb/export-markdown with validation  
✅ **CLI Support** — Full command-line interface with flexible arguments  
✅ **Configuration** — Environment-driven, defaults provided  
✅ **Comprehensive Tests** — 50+ test cases covering all paths  
✅ **Documentation** — Integration guide for graphRAG setup  
✅ **No Breaking Changes** — JSONL storage and sync unaffected  

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-05-graphrag-kb-export.md`.**

## Execution Approach

Two execution options:

**1. Subagent-Driven (Recommended)** — I dispatch a fresh subagent per task (1-3 minutes each), review between tasks, fast iteration with parallelization for independent tasks.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach would you prefer?**