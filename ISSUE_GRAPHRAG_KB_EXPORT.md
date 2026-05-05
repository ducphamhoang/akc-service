# Issue: GraphRAG Knowledge Base Export Interface

**Status:** Backlog  
**Type:** Feature  
**Priority:** Medium  
**Epic:** Knowledge Base Integration  
**Date Created:** 2026-05-05

---

## Summary

The AKC service needs to export curator patterns to an external markdown-based knowledge base that graphRAG can scan and index. Currently, patterns are stored as JSONL internally and synced as JSON to remote systems. We need a new abstraction layer to format patterns as markdown documents suitable for graphRAG consumption.

---

## Current State

### What Exists
- ✅ Patterns stored as JSONL: `akc_service/kb/patterns.jsonl`
- ✅ Markdown reporting: `KB_ANALYSIS.md` (metrics & validation)
- ✅ Sync API endpoints: `/akc/v1/sync/export`, `/push`, `/pull`, `/receive`
- ✅ Pattern versioning & confidence tracking in `learning_engine.py`
- ✅ Curator pattern schema with examples, rules, dependencies

### What's Missing
- ❌ Markdown export format optimized for graphRAG
- ❌ "Curator pattern → markdown file" pipeline
- ❌ Configuration for external KB folder location
- ❌ Public interface/abstraction for KB markdown export

---

## Proposed Solution

Create a new **KB Export Module** (`kb_exporter.py`) that:

### 1. Pattern → Markdown Conversion
Transform each curator pattern (currently JSONL) into structured markdown:

```
# Pattern: {pattern_id}

**Tier:** {confidence_tier}  
**Confidence:** {confidence:.2%}  
**Entity:** {entity} → **Component:** {component}  
**Type:** {pattern_type}  

## Description
{description}

## Rule
{rule}

## Example (Correct)
\`\`\`
{example_correct}
\`\`\`

## Example (Incorrect)
\`\`\`
{example_incorrect}
\`\`\`

## Metadata
- **Tags:** {tags}
- **Created:** {created_at}
- **Updated:** {updated_at}
- **Source:** {source}
- **Dependencies:** {dependencies}
- **Conflicts With:** {conflicts_with}
- **Guardrail Protected:** {guardrail_protected}
- **Usage Count:** {usage_count}
- **Failure Count:** {failure_count}

---
*Last synced: {timestamp} | Schema version: {schema_version}*
```

### 2. Folder Organization
```
{KB_EXPORT_DIR}/
├── by-entity/
│   ├── player/
│   │   ├── HealthComponent_001.md
│   │   ├── MovementComponent_001.md
│   │   └── ...
│   ├── enemy_knight/
│   │   ├── PhysicsComponent_001.md
│   │   └── ...
│   └── ...
├── by-tier/
│   ├── gold/
│   ├── production/
│   ├── experimental/
│   └── demoted/
├── by-pattern-type/
│   ├── collision_detection/
│   ├── health_tracking/
│   └── ...
└── INDEX.md (master index with metadata)
```

### 3. API Endpoint
```python
POST /akc/v1/kb/export-markdown
{
  "export_path": "/path/to/external/kb",
  "organization": "by-entity",  # or "by-tier", "by-pattern-type"
  "min_confidence": 0.5,
  "include_demoted": false,
  "dry_run": false
}
```

Response:
```json
{
  "success": true,
  "patterns_exported": 47,
  "folder": "/path/to/external/kb",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:30Z"
}
```

### 4. Configuration
Add to `config.py`:
```python
KB_EXPORT_DIR = Path(os.environ.get("AKC_SERVICE_KB_EXPORT_DIR", "./kb_export"))
KB_EXPORT_FORMAT = os.environ.get("AKC_SERVICE_KB_EXPORT_FORMAT", "by-entity")
KB_EXPORT_MIN_CONFIDENCE = float(os.environ.get("AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE", "0.0"))
```

---

## Implementation Tasks

- [ ] Design `kb_exporter.py` module with:
  - `PatternToMarkdown` converter class
  - `FolderOrganizer` strategy (by-entity, by-tier, by-pattern-type)
  - `export_patterns_to_markdown()` function
  
- [ ] Create API endpoint in `routes.py`:
  - `POST /akc/v1/kb/export-markdown`
  - Input validation (path, organization, filters)
  - Error handling (permission, disk space, malformed patterns)
  
- [ ] Add configuration:
  - `config.py` environment variables
  - Defaults for KB export location
  
- [ ] Write tests:
  - Pattern conversion correctness
  - Folder structure validation
  - Dependency/conflict serialization
  - Multiple organization strategies
  - Edge cases (empty KB, special characters in IDs)
  
- [ ] Documentation:
  - How graphRAG integrates (it scans the exported folder independently)
  - Manual export via CLI: `python -m akc_service.kb_exporter --export-path /path`
  - API usage examples

---

## Acceptance Criteria

- [x] Issue documented and backlogged
- [ ] Module design finalized via brainstorming
- [ ] Implementation complete with all tests passing
- [ ] API endpoint working with multiple organization strategies
- [ ] graphRAG can successfully scan exported markdown
- [ ] Documentation updated with integration guide

---

## Notes

- **AKC's responsibility:** Export patterns to markdown folder
- **graphRAG's responsibility:** Scan folder and build RAG index (out of scope)
- **Sync still happens:** JSON sync continues for AKC ↔ remote system
- **Not a breaking change:** Existing JSONL storage + sync are untouched

