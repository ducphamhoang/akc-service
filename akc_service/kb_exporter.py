#!/usr/bin/env python3
"""
KB Export Module

Converts curator patterns from JSONL to markdown documents optimized for graphRAG.
Provides multiple folder organization strategies (by-entity, by-tier, by-pattern-type).

Usage:
    from akc_service.kb_exporter import export_patterns_to_markdown

    result = export_patterns_to_markdown(
        export_path="/path/to/kb",
        jsonl_path="/path/to/patterns.jsonl",
        organization="by-entity",
        min_confidence=0.5,
        include_demoted=False,
        dry_run=False
    )

    Or via CLI:
    python -m akc_service.kb_exporter --export-path /path/to/kb --organization by-entity
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Callable, Optional
import argparse


class PatternToMarkdown:
    """Converts curator pattern dictionaries to markdown strings."""

    @staticmethod
    def convert(pattern: Dict) -> str:
        """
        Transform a JSONL pattern dict to markdown string.

        Args:
            pattern: Dictionary containing pattern fields (pattern_id, confidence, entity, etc.)

        Returns:
            Formatted markdown string with all pattern sections and metadata.
        """
        # Extract fields with safe defaults for missing values
        pattern_id = pattern.get("id", "unknown")
        confidence = pattern.get("confidence", 0.0)
        confidence_tier = pattern.get("confidence_tier", "unrated")
        entity = pattern.get("entity", "unknown")
        component = pattern.get("component", "unknown")
        pattern_type = pattern.get("pattern_type", "unknown")

        description = pattern.get("description", "No description provided")
        rule = pattern.get("rule", "No rule provided")
        example_correct = pattern.get("example_correct", "No example provided")
        example_incorrect = pattern.get("example_incorrect", "No example provided")

        # Metadata fields
        tags = pattern.get("tags", [])
        tags_str = ", ".join(tags) if tags else "None"

        created_at = pattern.get("created_at", "unknown")
        updated_at = pattern.get("updated_at", "unknown")
        source = pattern.get("source", "unknown")

        dependencies = pattern.get("dependencies", [])
        deps_str = ", ".join(dependencies) if dependencies else "None"

        conflicts_with = pattern.get("conflicts_with", [])
        conflicts_str = ", ".join(conflicts_with) if conflicts_with else "None"

        guardrail_protected = pattern.get("guardrail_protected", False)
        usage_count = pattern.get("usage_count", 0)
        failure_count = pattern.get("failure_count", 0)

        schema_version = pattern.get("schema_version", "unknown")

        # Generate timestamp for footer
        sync_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build markdown content
        markdown = f"""# Pattern: {pattern_id}

**Tier:** {confidence_tier}
**Confidence:** {confidence:.1%}
**Entity:** {entity} → **Component:** {component}
**Type:** {pattern_type}

## Description
{description}

## Rule
{rule}

## Example (Correct)
```
{example_correct}
```

## Example (Incorrect)
```
{example_incorrect}
```

## Metadata
- **Tags:** {tags_str}
- **Created:** {created_at}
- **Updated:** {updated_at}
- **Source:** {source}
- **Dependencies:** {deps_str}
- **Conflicts With:** {conflicts_str}
- **Guardrail Protected:** {guardrail_protected}
- **Usage Count:** {usage_count}
- **Failure Count:** {failure_count}

---
*Last synced: {sync_timestamp} | Schema version: {schema_version}*
"""
        return markdown


class FolderOrganizer:
    """Provides multiple folder organization strategies for pattern exports."""

    @staticmethod
    def get_folder_path_by_entity(pattern: Dict, base_dir: str) -> str:
        """
        Get folder path organized by entity.

        Folder structure: {base_dir}/by-entity/{entity}/{component_id}.md

        Args:
            pattern: Pattern dictionary
            base_dir: Base export directory

        Returns:
            Full path to markdown file
        """
        entity = pattern.get("entity", "unknown").lower()
        component = pattern.get("component", "unknown").lower()
        pattern_id = pattern.get("id", "unknown")

        # Create filename from component + pattern id
        filename = f"{component}_{pattern_id}.md"

        base_path = Path(base_dir) / "by-entity" / entity
        return str(base_path / filename)

    @staticmethod
    def get_folder_path_by_tier(pattern: Dict, base_dir: str) -> str:
        """
        Get folder path organized by confidence tier.

        Folder structure: {base_dir}/by-tier/{tier}/{entity}_{component_id}.md

        Args:
            pattern: Pattern dictionary
            base_dir: Base export directory

        Returns:
            Full path to markdown file
        """
        tier = pattern.get("confidence_tier", "unrated").lower()
        entity = pattern.get("entity", "unknown").lower()
        pattern_id = pattern.get("id", "unknown")

        # Create filename from entity + pattern id
        filename = f"{entity}_{pattern_id}.md"

        base_path = Path(base_dir) / "by-tier" / tier
        return str(base_path / filename)

    @staticmethod
    def get_folder_path_by_pattern_type(pattern: Dict, base_dir: str) -> str:
        """
        Get folder path organized by pattern type.

        Folder structure: {base_dir}/by-pattern-type/{type}/{entity}_{component_id}.md

        Args:
            pattern: Pattern dictionary
            base_dir: Base export directory

        Returns:
            Full path to markdown file
        """
        pattern_type = pattern.get("pattern_type", "unknown").lower()
        entity = pattern.get("entity", "unknown").lower()
        pattern_id = pattern.get("id", "unknown")

        # Create filename from entity + pattern id
        filename = f"{entity}_{pattern_id}.md"

        base_path = Path(base_dir) / "by-pattern-type" / pattern_type
        return str(base_path / filename)

    @staticmethod
    def get_organizer_func(strategy: str) -> Callable:
        """
        Get the appropriate organizer function for a given strategy.

        Args:
            strategy: Organization strategy ('by-entity', 'by-tier', 'by-pattern-type')

        Returns:
            The corresponding static method

        Raises:
            ValueError: If strategy is not recognized
        """
        strategies = {
            "by-entity": FolderOrganizer.get_folder_path_by_entity,
            "by-tier": FolderOrganizer.get_folder_path_by_tier,
            "by-pattern-type": FolderOrganizer.get_folder_path_by_pattern_type,
        }

        if strategy not in strategies:
            raise ValueError(
                f"Invalid organization strategy '{strategy}'. "
                f"Must be one of: {', '.join(strategies.keys())}"
            )

        return strategies[strategy]


def load_patterns_from_jsonl(jsonl_path: str) -> List[Dict]:
    """
    Load all patterns from a JSONL file.

    Args:
        jsonl_path: Path to patterns.jsonl file

    Returns:
        List of pattern dictionaries

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file contains invalid JSON
    """
    patterns = []
    path = Path(jsonl_path)

    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    with open(path, "r") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:  # Skip empty lines
                continue

            try:
                pattern = json.loads(line)
                patterns.append(pattern)
            except json.JSONDecodeError as e:
                raise json.JSONDecodeError(
                    f"Invalid JSON on line {line_num}: {e.msg}",
                    e.doc,
                    e.pos
                )

    return patterns


def filter_patterns(
    patterns: List[Dict],
    min_confidence: float = 0.0,
    include_demoted: bool = True
) -> List[Dict]:
    """
    Filter patterns by confidence threshold and demotion status.

    Args:
        patterns: List of pattern dictionaries
        min_confidence: Minimum confidence threshold (0.0 to 1.0)
        include_demoted: Whether to include demoted patterns

    Returns:
        Filtered list of patterns
    """
    filtered = []

    for pattern in patterns:
        confidence = pattern.get("confidence", 0.0)
        tier = pattern.get("confidence_tier", "unrated").lower()

        # Skip if confidence below threshold
        if confidence < min_confidence:
            continue

        # Skip demoted patterns if not included
        if not include_demoted and tier == "demoted":
            continue

        filtered.append(pattern)

    return filtered


def export_patterns_to_markdown(
    export_path: str,
    jsonl_path: str,
    organization: str = "by-entity",
    min_confidence: float = 0.0,
    include_demoted: bool = True,
    dry_run: bool = False
) -> Dict:
    """
    Export patterns from JSONL to organized markdown files.

    Creates folder structure and markdown files according to the chosen organization
    strategy. Also creates an INDEX.md with export metadata.

    Args:
        export_path: Target directory for exported markdown files
        jsonl_path: Source JSONL file with patterns
        organization: Organization strategy ('by-entity', 'by-tier', 'by-pattern-type')
        min_confidence: Minimum confidence to include (0.0 to 1.0)
        include_demoted: Whether to include demoted patterns
        dry_run: If True, only report what would be exported without writing files

    Returns:
        Dictionary with export metadata:
        {
            'success': bool,
            'patterns_exported': int,
            'folder': str,
            'organization': str,
            'exported_at': str,
            'patterns': List[Dict] (if dry_run, the patterns that would be exported)
        }
    """
    try:
        # Load patterns
        all_patterns = load_patterns_from_jsonl(jsonl_path)

        # Filter patterns
        filtered_patterns = filter_patterns(
            all_patterns,
            min_confidence=min_confidence,
            include_demoted=include_demoted
        )

        # Get organizer function
        organizer_func = FolderOrganizer.get_organizer_func(organization)

        # Timestamp for export
        export_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not dry_run:
            # Create base export directory
            export_dir = Path(export_path)
            export_dir.mkdir(parents=True, exist_ok=True)

            # Export each pattern
            exported_count = 0
            for pattern in filtered_patterns:
                # Get target file path
                file_path = organizer_func(pattern, export_path)
                file_path_obj = Path(file_path)

                # Create parent directories
                file_path_obj.parent.mkdir(parents=True, exist_ok=True)

                # Convert pattern to markdown
                markdown_content = PatternToMarkdown.convert(pattern)

                # Write file
                with open(file_path_obj, "w") as f:
                    f.write(markdown_content)

                exported_count += 1

            # Create INDEX.md with export metadata
            index_content = _create_index_md(
                filtered_patterns,
                organization,
                export_timestamp,
                min_confidence,
                include_demoted
            )

            index_path = export_dir / "INDEX.md"
            with open(index_path, "w") as f:
                f.write(index_content)

            return {
                "success": True,
                "patterns_exported": exported_count,
                "folder": str(export_dir.resolve()),
                "organization": organization,
                "exported_at": export_timestamp,
            }
        else:
            # Dry run: just report what would be exported
            return {
                "success": True,
                "patterns_exported": len(filtered_patterns),
                "folder": str(Path(export_path).resolve()),
                "organization": organization,
                "exported_at": export_timestamp,
                "patterns": filtered_patterns,
                "dry_run": True,
            }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "patterns_exported": 0,
        }


def _create_index_md(
    patterns: List[Dict],
    organization: str,
    export_timestamp: str,
    min_confidence: float,
    include_demoted: bool
) -> str:
    """
    Create INDEX.md with export metadata.

    Args:
        patterns: List of exported patterns
        organization: Organization strategy used
        export_timestamp: ISO timestamp of export
        min_confidence: Minimum confidence filter used
        include_demoted: Whether demoted patterns were included

    Returns:
        Markdown content for INDEX.md
    """
    tier_counts = {}
    entity_counts = {}
    type_counts = {}

    for pattern in patterns:
        tier = pattern.get("confidence_tier", "unrated")
        entity = pattern.get("entity", "unknown")
        ptype = pattern.get("pattern_type", "unknown")

        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        entity_counts[entity] = entity_counts.get(entity, 0) + 1
        type_counts[ptype] = type_counts.get(ptype, 0) + 1

    # Build statistics section
    stats_lines = []
    stats_lines.append("## Statistics")
    stats_lines.append(f"- **Total patterns:** {len(patterns)}")
    stats_lines.append(f"- **Organization:** {organization}")
    stats_lines.append(f"- **Min confidence filter:** {min_confidence:.1%}")
    stats_lines.append(f"- **Include demoted:** {include_demoted}")
    stats_lines.append("")
    stats_lines.append("### By Tier")
    for tier, count in sorted(tier_counts.items()):
        stats_lines.append(f"- {tier}: {count}")
    stats_lines.append("")
    stats_lines.append("### By Entity")
    for entity, count in sorted(entity_counts.items()):
        stats_lines.append(f"- {entity}: {count}")
    stats_lines.append("")
    stats_lines.append("### By Pattern Type")
    for ptype, count in sorted(type_counts.items()):
        stats_lines.append(f"- {ptype}: {count}")

    index_content = f"""# Knowledge Base Export Index

**Exported:** {export_timestamp}
**Schema Version:** v2

{chr(10).join(stats_lines)}

## Organization

This knowledge base is organized by **{organization}**:

"""

    if organization == "by-entity":
        index_content += "```\nby-entity/\n├── entity_name/\n│   ├── component_pattern_id.md\n│   └── ...\n└── ...\n```\n"
    elif organization == "by-tier":
        index_content += "```\nby-tier/\n├── gold/\n├── production/\n├── experimental/\n├── demoted/\n└── ...\n```\n"
    elif organization == "by-pattern-type":
        index_content += "```\nby-pattern-type/\n├── collision_detection/\n├── health_tracking/\n└── ...\n```\n"

    index_content += f"""
## Integration with graphRAG

graphRAG will automatically scan this folder and index all markdown files into its knowledge graph. Each pattern document contains:
- Structured metadata for filtering and relationships
- Pattern ID, confidence tier, and entity:component mapping
- Rule descriptions and code examples
- Dependencies and conflict information
- Usage and failure statistics

For more information, see the individual pattern files.

---
*Index generated automatically on {export_timestamp}*
"""

    return index_content


if __name__ == "__main__":
    from akc_service.config import PATTERNS_JSONL, KB_EXPORT_DIR, KB_EXPORT_FORMAT, KB_EXPORT_MIN_CONFIDENCE

    parser = argparse.ArgumentParser(
        description="Export curator patterns to markdown files for graphRAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m akc_service.kb_exporter --export-path ./kb_export
  python -m akc_service.kb_exporter --export-path ./kb_export --organization by-tier --min-confidence 0.7
  python -m akc_service.kb_exporter --export-path ./kb_export --dry-run
        """
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=KB_EXPORT_DIR,
        help=f"Target directory for exported markdown files (default: {KB_EXPORT_DIR})"
    )
    parser.add_argument(
        "--patterns-file",
        type=Path,
        default=PATTERNS_JSONL,
        help=f"Source JSONL file with patterns (default: {PATTERNS_JSONL})"
    )
    parser.add_argument(
        "--organization",
        choices=["by-entity", "by-tier", "by-pattern-type"],
        default=KB_EXPORT_FORMAT,
        help=f"Organization strategy (default: {KB_EXPORT_FORMAT})"
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=KB_EXPORT_MIN_CONFIDENCE,
        help=f"Minimum confidence threshold (0.0-1.0, default: {KB_EXPORT_MIN_CONFIDENCE})"
    )
    parser.add_argument(
        "--include-demoted",
        action="store_true",
        help="Include demoted patterns in export"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be exported without writing files"
    )

    args = parser.parse_args()

    # Validate confidence
    if not 0.0 <= args.min_confidence <= 1.0:
        print("Error: --min-confidence must be between 0.0 and 1.0", file=sys.stderr)
        sys.exit(1)

    # Run export
    result = export_patterns_to_markdown(
        export_path=str(args.export_path),
        jsonl_path=str(args.patterns_file),
        organization=args.organization,
        min_confidence=args.min_confidence,
        include_demoted=args.include_demoted,
        dry_run=args.dry_run
    )

    # Print result dict
    print(json.dumps(result, indent=2, default=str))

    # Print summary
    if result["success"]:
        patterns_count = result['patterns_exported']
        folder = result['folder']
        print(f"✓ Exported {patterns_count} patterns to {folder}")
        sys.exit(0)
    else:
        print("✗ Export failed", file=sys.stderr)
        sys.exit(1)
