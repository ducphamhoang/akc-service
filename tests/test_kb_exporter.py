"""
KB Exporter Module Tests

Comprehensive test suite for akc_service.kb_exporter module covering pattern
conversion to markdown, folder organization strategies, pattern loading/filtering,
and the full export pipeline with multiple organization modes.

Test classes:
- TestPatternToMarkdown: Markdown conversion with all field variations
- TestFolderOrganizer: Path generation for all organization strategies
- TestPatternLoading: JSONL file loading and error handling
- TestPatternFiltering: Confidence and demotion-based filtering
- TestExportPatternsToMarkdown: Full export pipeline scenarios
- TestMarkdownContent: Exported markdown content structure validation
"""

import json
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from akc_service.kb_exporter import (
    PatternToMarkdown,
    FolderOrganizer,
    load_patterns_from_jsonl,
    filter_patterns,
    export_patterns_to_markdown,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_pattern() -> Dict:
    """Single curator pattern dictionary with all fields populated."""
    return {
        "id": "collision_detection_001",
        "confidence": 0.95,
        "confidence_tier": "gold",
        "entity": "Player",
        "component": "Physics",
        "pattern_type": "collision_detection",
        "description": "Detects collision between player and static obstacles",
        "rule": "Check area2d overlapping physics bodies with collision layer mask",
        "example_correct": "if area.overlapping_bodies:\n    handle_collision(area)",
        "example_incorrect": "if area:\n    handle_collision(area)",
        "tags": ["physics", "collision", "safety"],
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-02-01T14:22:00Z",
        "source": "production_incident",
        "dependencies": ["area2d_setup", "physics_layers"],
        "conflicts_with": ["direct_collision_raycast"],
        "guardrail_protected": True,
        "usage_count": 152,
        "failure_count": 2,
        "schema_version": "v2",
    }


@pytest.fixture
def sample_patterns_jsonl(tmp_path) -> Path:
    """Temporary JSONL file with 3 test patterns."""
    patterns = [
        {
            "id": "pattern_001",
            "confidence": 0.95,
            "confidence_tier": "gold",
            "entity": "Player",
            "component": "Movement",
            "pattern_type": "collision_detection",
            "description": "Test pattern 1",
            "rule": "Test rule 1",
            "example_correct": "correct 1",
            "example_incorrect": "incorrect 1",
            "tags": ["test"],
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "source": "test",
            "dependencies": [],
            "conflicts_with": [],
            "guardrail_protected": False,
            "usage_count": 10,
            "failure_count": 0,
            "schema_version": "v2",
        },
        {
            "id": "pattern_002",
            "confidence": 0.75,
            "confidence_tier": "production",
            "entity": "Enemy",
            "component": "AI",
            "pattern_type": "state_machine",
            "description": "Test pattern 2",
            "rule": "Test rule 2",
            "example_correct": "correct 2",
            "example_incorrect": "incorrect 2",
            "tags": ["ai"],
            "created_at": "2025-01-02T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
            "source": "test",
            "dependencies": [],
            "conflicts_with": [],
            "guardrail_protected": False,
            "usage_count": 5,
            "failure_count": 1,
            "schema_version": "v2",
        },
        {
            "id": "pattern_003",
            "confidence": 0.45,
            "confidence_tier": "demoted",
            "entity": "UI",
            "component": "Button",
            "pattern_type": "input_handling",
            "description": "Test pattern 3",
            "rule": "Test rule 3",
            "example_correct": "correct 3",
            "example_incorrect": "incorrect 3",
            "tags": ["ui"],
            "created_at": "2025-01-03T00:00:00Z",
            "updated_at": "2025-01-03T00:00:00Z",
            "source": "test",
            "dependencies": [],
            "conflicts_with": [],
            "guardrail_protected": False,
            "usage_count": 2,
            "failure_count": 2,
            "schema_version": "v2",
        },
    ]

    jsonl_file = tmp_path / "patterns.jsonl"
    with open(jsonl_file, "w") as f:
        for pattern in patterns:
            f.write(json.dumps(pattern) + "\n")

    return jsonl_file


@pytest.fixture
def minimal_pattern() -> Dict:
    """Pattern with only required/minimal fields."""
    return {
        "id": "minimal_001",
        "confidence": 0.5,
        "confidence_tier": "experimental",
        "entity": "Generic",
        "component": "Component",
        "pattern_type": "generic",
    }


@pytest.fixture
def patterns_with_special_chars() -> Dict:
    """Pattern with special characters that need markdown escaping."""
    return {
        "id": "special_001",
        "confidence": 0.8,
        "confidence_tier": "production",
        "entity": "Player",
        "component": "Movement",
        "pattern_type": "collision",
        "description": "Pattern with **bold** and _italic_ and `code`",
        "rule": "Rule with [link](https://example.com) and > blockquote",
        "example_correct": "```python\nprint('hello')\n```",
        "example_incorrect": "print 'hello'",
        "tags": ["test", "special"],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "source": "test",
        "dependencies": [],
        "conflicts_with": [],
        "guardrail_protected": False,
        "usage_count": 0,
        "failure_count": 0,
        "schema_version": "v2",
    }


# ============================================================================
# TestPatternToMarkdown
# ============================================================================


class TestPatternToMarkdown:
    """Test pattern-to-markdown conversion."""

    def test_convert_basic_pattern(self, sample_pattern):
        """Test markdown conversion with all fields populated."""
        markdown = PatternToMarkdown.convert(sample_pattern)

        # Verify title
        assert "# Pattern: collision_detection_001" in markdown

        # Verify metadata fields
        assert "**Tier:** gold" in markdown
        assert "**Confidence:** 95.0%" in markdown
        assert "**Entity:** Player → **Component:** Physics" in markdown
        assert "**Type:** collision_detection" in markdown

        # Verify sections
        assert "## Description" in markdown
        assert "## Rule" in markdown
        assert "## Example (Correct)" in markdown
        assert "## Example (Incorrect)" in markdown
        assert "## Metadata" in markdown

        # Verify metadata content
        assert "**Tags:** physics, collision, safety" in markdown
        assert "**Usage Count:** 152" in markdown
        assert "**Failure Count:** 2" in markdown
        assert "**Guardrail Protected:** True" in markdown

    def test_convert_missing_fields(self, minimal_pattern):
        """Test markdown conversion with minimal/missing fields."""
        markdown = PatternToMarkdown.convert(minimal_pattern)

        # Should not raise error
        assert markdown is not None
        assert isinstance(markdown, str)

        # Verify default values are used
        assert "No description provided" in markdown
        assert "No rule provided" in markdown
        assert "No example provided" in markdown
        assert "**Tags:** None" in markdown

    def test_convert_handles_special_characters(self, patterns_with_special_chars):
        """Test markdown conversion preserves special characters."""
        markdown = PatternToMarkdown.convert(patterns_with_special_chars)

        # Special characters should be preserved in markdown
        assert "**bold**" in markdown
        assert "_italic_" in markdown
        assert "`code`" in markdown
        assert "[link](https://example.com)" in markdown

    def test_convert_empty_tags_list(self, sample_pattern):
        """Test pattern with empty tags list."""
        sample_pattern["tags"] = []
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "**Tags:** None" in markdown

    def test_convert_empty_dependencies_list(self, sample_pattern):
        """Test pattern with empty dependencies list."""
        sample_pattern["dependencies"] = []
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "**Dependencies:** None" in markdown

    def test_convert_empty_conflicts_list(self, sample_pattern):
        """Test pattern with empty conflicts_with list."""
        sample_pattern["conflicts_with"] = []
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "**Conflicts With:** None" in markdown

    def test_convert_confidence_formatting(self, sample_pattern):
        """Test confidence percentage formatting."""
        sample_pattern["confidence"] = 0.5
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "**Confidence:** 50.0%" in markdown

        sample_pattern["confidence"] = 0.333
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "**Confidence:** 33.3%" in markdown

    def test_convert_includes_timestamp(self, sample_pattern):
        """Test that markdown includes sync timestamp."""
        markdown = PatternToMarkdown.convert(sample_pattern)
        assert "*Last synced:" in markdown
        assert "| Schema version: v2*" in markdown

    def test_convert_metadata_section_complete(self, sample_pattern):
        """Test all metadata fields are present."""
        markdown = PatternToMarkdown.convert(sample_pattern)

        # Check for all metadata fields
        assert "**Tags:**" in markdown
        assert "**Created:**" in markdown
        assert "**Updated:**" in markdown
        assert "**Source:**" in markdown
        assert "**Dependencies:**" in markdown
        assert "**Conflicts With:**" in markdown
        assert "**Guardrail Protected:**" in markdown
        assert "**Usage Count:**" in markdown
        assert "**Failure Count:**" in markdown


# ============================================================================
# TestFolderOrganizer
# ============================================================================


class TestFolderOrganizer:
    """Test folder organization strategies."""

    def test_organize_by_entity(self, sample_pattern):
        """Verify by-entity path structure: by-entity/{entity}/{component_id}.md"""
        path = FolderOrganizer.get_folder_path_by_entity(sample_pattern, "/export")
        assert "/export/by-entity/player/physics_collision_detection_001.md" in path

    def test_organize_by_entity_lowercase(self, sample_pattern):
        """Verify entity and component are lowercased."""
        sample_pattern["entity"] = "PLAYER"
        sample_pattern["component"] = "PHYSICS"
        path = FolderOrganizer.get_folder_path_by_entity(sample_pattern, "/export")
        assert "player" in path
        assert "physics_" in path

    def test_organize_by_tier(self, sample_pattern):
        """Verify by-tier path structure: by-tier/{tier}/{entity_id}.md"""
        path = FolderOrganizer.get_folder_path_by_tier(sample_pattern, "/export")
        assert "/export/by-tier/gold/player_collision_detection_001.md" in path

    def test_organize_by_tier_lowercase(self, sample_pattern):
        """Verify tier and entity are lowercased."""
        sample_pattern["confidence_tier"] = "GOLD"
        sample_pattern["entity"] = "PLAYER"
        path = FolderOrganizer.get_folder_path_by_tier(sample_pattern, "/export")
        assert "gold" in path
        assert "player_" in path

    def test_organize_by_pattern_type(self, sample_pattern):
        """Verify by-pattern-type path structure: by-pattern-type/{type}/{entity_id}.md"""
        path = FolderOrganizer.get_folder_path_by_pattern_type(sample_pattern, "/export")
        assert "/export/by-pattern-type/collision_detection/player_collision_detection_001.md" in path

    def test_organize_by_pattern_type_lowercase(self, sample_pattern):
        """Verify pattern_type and entity are lowercased."""
        sample_pattern["pattern_type"] = "COLLISION_DETECTION"
        sample_pattern["entity"] = "PLAYER"
        path = FolderOrganizer.get_folder_path_by_pattern_type(sample_pattern, "/export")
        assert "collision_detection" in path
        assert "player_" in path

    def test_get_organizer_func_valid_by_entity(self):
        """Test retrieval of by-entity organizer function."""
        func = FolderOrganizer.get_organizer_func("by-entity")
        assert func == FolderOrganizer.get_folder_path_by_entity

    def test_get_organizer_func_valid_by_tier(self):
        """Test retrieval of by-tier organizer function."""
        func = FolderOrganizer.get_organizer_func("by-tier")
        assert func == FolderOrganizer.get_folder_path_by_tier

    def test_get_organizer_func_valid_by_pattern_type(self):
        """Test retrieval of by-pattern-type organizer function."""
        func = FolderOrganizer.get_organizer_func("by-pattern-type")
        assert func == FolderOrganizer.get_folder_path_by_pattern_type

    def test_get_organizer_func_invalid_strategy(self):
        """Test error handling for invalid strategy."""
        with pytest.raises(ValueError) as exc_info:
            FolderOrganizer.get_organizer_func("invalid-strategy")
        assert "Invalid organization strategy" in str(exc_info.value)
        assert "by-entity" in str(exc_info.value)

    def test_get_organizer_func_case_sensitive(self):
        """Test that strategy names are case-sensitive."""
        with pytest.raises(ValueError):
            FolderOrganizer.get_organizer_func("By-Entity")

    def test_path_construction_with_special_chars(self, sample_pattern):
        """Test path construction with special characters in fields."""
        sample_pattern["entity"] = "Player-Special"
        sample_pattern["component"] = "Physics@2D"
        path = FolderOrganizer.get_folder_path_by_entity(sample_pattern, "/export")
        assert "player-special" in path.lower()
        assert "physics@2d" in path.lower()


# ============================================================================
# TestPatternLoading
# ============================================================================


class TestPatternLoading:
    """Test pattern loading from JSONL files."""

    def test_load_patterns_from_jsonl(self, sample_patterns_jsonl):
        """Load patterns from JSONL file."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        assert len(patterns) == 3
        assert patterns[0]["id"] == "pattern_001"
        assert patterns[1]["id"] == "pattern_002"
        assert patterns[2]["id"] == "pattern_003"

    def test_load_patterns_preserves_all_fields(self, sample_patterns_jsonl):
        """Verify all pattern fields are preserved."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        pattern = patterns[0]
        assert pattern["confidence"] == 0.95
        assert pattern["confidence_tier"] == "gold"
        assert pattern["entity"] == "Player"

    def test_load_patterns_nonexistent_file(self):
        """Handle missing JSONL file gracefully."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_patterns_from_jsonl("/nonexistent/path.jsonl")
        assert "not found" in str(exc_info.value)

    def test_load_patterns_invalid_json(self, tmp_path):
        """Handle invalid JSON in JSONL file."""
        bad_jsonl = tmp_path / "bad.jsonl"
        with open(bad_jsonl, "w") as f:
            f.write('{"valid": true}\n')
            f.write('{"invalid": json}\n')  # Missing quotes

        with pytest.raises(json.JSONDecodeError):
            load_patterns_from_jsonl(str(bad_jsonl))

    def test_load_patterns_skip_empty_lines(self, tmp_path):
        """Skip empty lines in JSONL file."""
        jsonl_file = tmp_path / "patterns.jsonl"
        with open(jsonl_file, "w") as f:
            f.write('{"id": "p1"}\n')
            f.write('\n')  # Empty line
            f.write('{"id": "p2"}\n')
            f.write('   \n')  # Whitespace-only line
            f.write('{"id": "p3"}\n')

        patterns = load_patterns_from_jsonl(str(jsonl_file))
        assert len(patterns) == 3

    def test_load_patterns_empty_file(self, tmp_path):
        """Handle empty JSONL file."""
        empty_jsonl = tmp_path / "empty.jsonl"
        empty_jsonl.touch()

        patterns = load_patterns_from_jsonl(str(empty_jsonl))
        assert patterns == []

    def test_load_patterns_file_with_only_empty_lines(self, tmp_path):
        """Handle file with only empty lines."""
        jsonl_file = tmp_path / "patterns.jsonl"
        with open(jsonl_file, "w") as f:
            f.write('\n\n\n')

        patterns = load_patterns_from_jsonl(str(jsonl_file))
        assert patterns == []

    def test_load_patterns_line_number_in_error(self, tmp_path):
        """Error message includes line number of invalid JSON."""
        bad_jsonl = tmp_path / "bad.jsonl"
        with open(bad_jsonl, "w") as f:
            f.write('{"valid": true}\n')
            f.write('{"valid": true}\n')
            f.write('{"invalid": json}\n')

        with pytest.raises(json.JSONDecodeError) as exc_info:
            load_patterns_from_jsonl(str(bad_jsonl))
        assert "line 3" in str(exc_info.value)


# ============================================================================
# TestPatternFiltering
# ============================================================================


class TestPatternFiltering:
    """Test pattern filtering by confidence and demotion status."""

    def test_filter_by_confidence(self, sample_patterns_jsonl):
        """Filter patterns by minimum confidence."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.8, include_demoted=True)

        assert len(filtered) == 1
        assert patterns[0] in filtered  # 0.95 >= 0.8
        assert patterns[1] not in filtered  # 0.75 < 0.8
        assert patterns[2] not in filtered  # 0.45 < 0.8

    def test_filter_exclude_demoted(self, sample_patterns_jsonl):
        """Exclude demoted patterns."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.0, include_demoted=False)

        assert len(filtered) == 2
        assert patterns[0] in filtered
        assert patterns[1] in filtered
        assert patterns[2] not in filtered  # Demoted tier excluded

    def test_filter_include_demoted(self, sample_patterns_jsonl):
        """Include demoted patterns."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.0, include_demoted=True)

        assert len(filtered) == 3
        assert patterns[2] in filtered

    def test_filter_confidence_and_demotion(self, sample_patterns_jsonl):
        """Filter by both confidence and demotion status."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.7, include_demoted=False)

        assert len(filtered) == 2
        assert patterns[0] in filtered  # 0.95 >= 0.7, not demoted
        assert patterns[1] in filtered  # 0.75 >= 0.7, not demoted
        assert patterns[2] not in filtered  # Demoted

    def test_filter_no_patterns_match(self, sample_patterns_jsonl):
        """Return empty list when no patterns match."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=1.0, include_demoted=False)

        assert filtered == []

    def test_filter_all_patterns_match(self, sample_patterns_jsonl):
        """Return all patterns when filters are permissive."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.0, include_demoted=True)

        assert len(filtered) == len(patterns)

    def test_filter_confidence_defaults(self, sample_patterns_jsonl):
        """Test default filter parameters."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns)  # No arguments

        assert len(filtered) == 3  # All patterns included by default

    def test_filter_preserves_order(self, sample_patterns_jsonl):
        """Filtered patterns maintain original order."""
        patterns = load_patterns_from_jsonl(str(sample_patterns_jsonl))
        filtered = filter_patterns(patterns, min_confidence=0.4, include_demoted=True)

        # All patterns have >= 0.4 confidence
        assert filtered == patterns


# ============================================================================
# TestExportPatternsToMarkdown
# ============================================================================


class TestExportPatternsToMarkdown:
    """Test the full export pipeline."""

    def test_export_by_entity(self, sample_patterns_jsonl, tmp_path):
        """Export with by-entity organization."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 3
        assert result["organization"] == "by-entity"

        # Verify directory structure
        assert (export_path / "by-entity" / "player").exists()
        assert (export_path / "by-entity" / "enemy").exists()
        assert (export_path / "by-entity" / "ui").exists()

    def test_export_by_tier(self, sample_patterns_jsonl, tmp_path):
        """Export with by-tier organization."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-tier",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 3
        assert result["organization"] == "by-tier"

        # Verify directory structure
        assert (export_path / "by-tier" / "gold").exists()
        assert (export_path / "by-tier" / "production").exists()
        assert (export_path / "by-tier" / "demoted").exists()

    def test_export_by_pattern_type(self, sample_patterns_jsonl, tmp_path):
        """Export with by-pattern-type organization."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-pattern-type",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 3
        assert result["organization"] == "by-pattern-type"

        # Verify directory structure
        assert (export_path / "by-pattern-type" / "collision_detection").exists()
        assert (export_path / "by-pattern-type" / "state_machine").exists()
        assert (export_path / "by-pattern-type" / "input_handling").exists()

    def test_export_filter_by_confidence(self, sample_patterns_jsonl, tmp_path):
        """Export with confidence filtering."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
            min_confidence=0.8,
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 1  # Only pattern with 0.95

    def test_export_exclude_demoted(self, sample_patterns_jsonl, tmp_path):
        """Export excluding demoted patterns."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
            include_demoted=False,
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 2

    def test_export_dry_run(self, sample_patterns_jsonl, tmp_path):
        """Verify dry-run creates no files."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
            dry_run=True,
        )

        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["patterns_exported"] == 3
        assert "patterns" in result
        assert len(result["patterns"]) == 3

        # Verify no files were created
        assert not export_path.exists()

    def test_export_empty_patterns(self, tmp_path):
        """Handle empty JSONL file."""
        empty_jsonl = tmp_path / "empty.jsonl"
        empty_jsonl.touch()

        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(empty_jsonl),
            organization="by-entity",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 0

    def test_export_creates_index_md(self, sample_patterns_jsonl, tmp_path):
        """Verify INDEX.md creation."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        assert result["success"] is True

        # Verify INDEX.md exists
        index_path = export_path / "INDEX.md"
        assert index_path.exists()

        # Verify content
        content = index_path.read_text()
        assert "# Knowledge Base Export Index" in content
        assert "## Statistics" in content
        assert "**Total patterns:** 3" in content

    def test_export_invalid_jsonl_path(self, tmp_path):
        """Handle invalid JSONL file path."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path="/nonexistent/file.jsonl",
            organization="by-entity",
        )

        assert result["success"] is False
        assert "error" in result

    def test_export_invalid_organization(self, sample_patterns_jsonl, tmp_path):
        """Handle invalid organization strategy."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="invalid-strategy",
        )

        assert result["success"] is False
        assert "error" in result

    def test_export_result_contains_timestamp(self, sample_patterns_jsonl, tmp_path):
        """Verify export result contains timestamp."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        assert "exported_at" in result
        assert "T" in result["exported_at"]  # ISO format
        assert "Z" in result["exported_at"]

    def test_export_result_contains_folder_path(self, sample_patterns_jsonl, tmp_path):
        """Verify export result contains resolved folder path."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        assert "folder" in result
        assert str(export_path.resolve()) in result["folder"]

    def test_export_creates_markdown_files(self, sample_patterns_jsonl, tmp_path):
        """Verify markdown files are created."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        # Find all .md files
        md_files = list(export_path.rglob("*.md"))
        assert len(md_files) == 4  # 3 patterns + 1 INDEX.md

    def test_export_markdown_content_valid(self, sample_patterns_jsonl, tmp_path):
        """Verify exported markdown files have valid content."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        # Read a pattern file
        pattern_files = list((export_path / "by-entity").rglob("*.md"))
        assert len(pattern_files) > 0

        content = pattern_files[0].read_text()
        assert "# Pattern:" in content
        assert "## Description" in content

    def test_export_confidence_combination_filters(self, sample_patterns_jsonl, tmp_path):
        """Test combination of confidence and demotion filters."""
        export_path = tmp_path / "kb_export"
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
            min_confidence=0.5,
            include_demoted=True,
        )

        # pattern_001: 0.95 >= 0.5 ✓
        # pattern_002: 0.75 >= 0.5 ✓
        # pattern_003: 0.45 < 0.5 ✗
        assert result["patterns_exported"] == 2


# ============================================================================
# TestMarkdownContent
# ============================================================================


class TestMarkdownContent:
    """Test exported markdown content structure and validity."""

    def test_exported_markdown_structure(self, sample_pattern, tmp_path):
        """Verify exported markdown file structure."""
        # Create JSONL with single pattern
        jsonl_file = tmp_path / "patterns.jsonl"
        with open(jsonl_file, "w") as f:
            f.write(json.dumps(sample_pattern) + "\n")

        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(jsonl_file),
            organization="by-entity",
        )

        # Find the pattern file
        pattern_files = list((export_path / "by-entity").rglob("*.md"))
        assert len(pattern_files) == 1

        content = pattern_files[0].read_text()

        # Verify structure
        assert "# Pattern: collision_detection_001" in content
        assert "## Description" in content
        assert "## Rule" in content
        assert "## Example (Correct)" in content
        assert "## Example (Incorrect)" in content
        assert "## Metadata" in content

    def test_index_md_statistics(self, sample_patterns_jsonl, tmp_path):
        """Verify INDEX.md contains correct statistics."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        index_content = (export_path / "INDEX.md").read_text()

        # Check statistics
        assert "**Total patterns:** 3" in index_content
        assert "## By Tier" in index_content
        assert "## By Entity" in index_content
        assert "## By Pattern Type" in index_content

    def test_index_md_organization_info(self, sample_patterns_jsonl, tmp_path):
        """Verify INDEX.md describes organization strategy."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        index_content = (export_path / "INDEX.md").read_text()
        assert "by-entity" in index_content
        assert "## Organization" in index_content

    def test_index_md_contains_graphrag_info(self, sample_patterns_jsonl, tmp_path):
        """Verify INDEX.md mentions graphRAG integration."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        index_content = (export_path / "INDEX.md").read_text()
        assert "graphRAG" in index_content
        assert "## Integration with graphRAG" in index_content

    def test_markdown_file_encoding(self, sample_pattern, tmp_path):
        """Verify markdown files use UTF-8 encoding."""
        jsonl_file = tmp_path / "patterns.jsonl"
        with open(jsonl_file, "w") as f:
            f.write(json.dumps(sample_pattern) + "\n")

        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(jsonl_file),
            organization="by-entity",
        )

        # Read file and verify encoding
        pattern_files = list((export_path / "by-entity").rglob("*.md"))
        content = pattern_files[0].read_text(encoding="utf-8")
        assert isinstance(content, str)

    def test_exported_files_are_readable(self, sample_patterns_jsonl, tmp_path):
        """Verify all exported files are readable."""
        export_path = tmp_path / "kb_export"
        export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )

        # Try to read all markdown files
        for md_file in export_path.rglob("*.md"):
            content = md_file.read_text()
            assert isinstance(content, str)
            assert len(content) > 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for complete workflows."""

    def test_full_export_workflow_by_entity(self, sample_patterns_jsonl, tmp_path):
        """Test complete export workflow with by-entity organization."""
        export_path = tmp_path / "kb_export"

        # Export patterns
        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
            min_confidence=0.5,
            include_demoted=False,
        )

        # Verify result
        assert result["success"] is True
        assert result["patterns_exported"] == 2

        # Verify structure
        assert (export_path / "by-entity").exists()
        assert (export_path / "INDEX.md").exists()

        # Verify files
        md_files = list(export_path.rglob("*.md"))
        assert len(md_files) == 3  # 2 patterns + INDEX

    def test_full_export_workflow_by_tier(self, sample_patterns_jsonl, tmp_path):
        """Test complete export workflow with by-tier organization."""
        export_path = tmp_path / "kb_export"

        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-tier",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 3

        # Verify tier directories
        assert (export_path / "by-tier" / "gold").exists()
        assert (export_path / "by-tier" / "production").exists()
        assert (export_path / "by-tier" / "demoted").exists()

    def test_full_export_workflow_by_pattern_type(self, sample_patterns_jsonl, tmp_path):
        """Test complete export workflow with by-pattern-type organization."""
        export_path = tmp_path / "kb_export"

        result = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-pattern-type",
        )

        assert result["success"] is True
        assert result["patterns_exported"] == 3

        # Verify type directories
        assert (export_path / "by-pattern-type" / "collision_detection").exists()
        assert (export_path / "by-pattern-type" / "state_machine").exists()
        assert (export_path / "by-pattern-type" / "input_handling").exists()

    def test_multiple_exports_to_same_directory(self, sample_patterns_jsonl, tmp_path):
        """Test multiple exports to the same directory (overwrites)."""
        export_path = tmp_path / "kb_export"

        # First export
        result1 = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-entity",
        )
        assert result1["patterns_exported"] == 3

        # Second export with different organization (should replace)
        result2 = export_patterns_to_markdown(
            export_path=str(export_path),
            jsonl_path=str(sample_patterns_jsonl),
            organization="by-tier",
        )
        assert result2["patterns_exported"] == 3

        # Verify second organization is present
        assert (export_path / "by-tier").exists()
