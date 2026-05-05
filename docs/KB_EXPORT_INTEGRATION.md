# KB Export Integration Guide

## Overview

KB Export is a feature of akc-service that converts curator patterns from JSON Lines format (patterns.jsonl) into organized markdown documents for consumption by graphRAG. This document describes the complete workflow for exporting, organizing, and integrating exported patterns with graphRAG for knowledge graph construction.

**Purpose:**
- **AKC Service Role:** Maintains the canonical knowledge base in patterns.jsonl, provides export functionality
- **graphRAG Role:** Consumes exported markdown files, builds a knowledge graph for semantic retrieval and RAG operations

The two systems operate independently — KB Export does not disrupt normal synchronization or JSONL storage.

---

## Architecture

### System Components

```
┌─────────────────────────────────────┐
│       AKC Service (akc-service)     │
│  ┌─────────────────────────────────┤
│  │ patterns.jsonl (canonical JSONL) │
│  │ (continuously synced)             │
│  └─────────────────────────────────┤
│  ┌──────────────────────────────────┤
│  │ KB_EXPORTER Module               │
│  │ (exports to markdown)             │
│  └──────────────────────────────────┤
└─────────────────────────────────────┘
           ↓ (on-demand export)
┌──────────────────────────────────────┐
│     Markdown Export Folder (KB_EXPORT_DIR)     │
│  ┌────────────────────────────────────┤
│  │ by-entity/                         │
│  │   ├── entity_name/                 │
│  │   │   └── *.md files               │
│  │   └── ...                          │
│  │ INDEX.md (export metadata)         │
│  └────────────────────────────────────┤
└──────────────────────────────────────┘
           ↓ (automatic scan)
┌──────────────────────────────────────┐
│         graphRAG System              │
│  ┌────────────────────────────────────┤
│  │ Scans markdown folder              │
│  │ Indexes patterns into knowledge    │
│  │ graph for semantic retrieval       │
│  └────────────────────────────────────┤
└──────────────────────────────────────┘
```

### Data Flow

1. **Pattern Collection:** AKC Service continuously syncs patterns to patterns.jsonl
2. **Export Trigger:** User initiates export via API or CLI
3. **Organization:** Patterns organized by chosen strategy (by-entity, by-tier, by-pattern-type)
4. **Markdown Generation:** Each pattern converted to markdown with metadata
5. **Index Creation:** INDEX.md generated with statistics and folder structure
6. **graphRAG Integration:** graphRAG scans folder, indexes patterns, constructs knowledge graph
7. **RAG Operations:** Semantic queries use knowledge graph for retrieval

---

## Configuration

### Environment Variables

#### AKC_SERVICE_KB_EXPORT_DIR

**Type:** Path  
**Default:** `./kb_export`  
**Purpose:** Root directory where markdown files are exported

**Example:**
```bash
export AKC_SERVICE_KB_EXPORT_DIR=/var/lib/akc-service/kb_export
```

**Validation:**
```bash
# Check directory exists and is writable
test -d "$AKC_SERVICE_KB_EXPORT_DIR" && test -w "$AKC_SERVICE_KB_EXPORT_DIR" && echo "OK"
```

#### AKC_SERVICE_KB_EXPORT_FORMAT

**Type:** String  
**Default:** `by-entity`  
**Valid Values:** `by-entity`, `by-tier`, `by-pattern-type`  
**Purpose:** Default organization strategy for exported patterns

**Example:**
```bash
export AKC_SERVICE_KB_EXPORT_FORMAT=by-tier
```

#### AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE

**Type:** Float (0.0 to 1.0)  
**Default:** `0.0`  
**Purpose:** Default minimum confidence threshold for export (excludes patterns below this)

**Example:**
```bash
export AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE=0.5
```

### Setup Example

```bash
# Create export directory structure
mkdir -p /var/lib/akc-service/kb_export
chmod 755 /var/lib/akc-service/kb_export

# Set environment variables in .env or shell profile
cat >> ~/.bashrc << 'EOF'
export AKC_SERVICE_KB_EXPORT_DIR=/var/lib/akc-service/kb_export
export AKC_SERVICE_KB_EXPORT_FORMAT=by-entity
export AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE=0.0
EOF

source ~/.bashrc
```

---

## Usage - API Endpoint

### Endpoint: POST /akc/v1/kb/export-markdown

Exports patterns from patterns.jsonl to organized markdown files.

#### Request Schema

```json
{
  "export_path": "/path/to/export",  // Optional, overrides AKC_SERVICE_KB_EXPORT_DIR
  "organization": "by-entity",        // Required: by-entity | by-tier | by-pattern-type
  "min_confidence": 0.5,              // Optional, 0.0-1.0, default: 0.0
  "include_demoted": false,           // Optional, default: false
  "dry_run": false                    // Optional, default: false
}
```

#### Response Schema

```json
{
  "success": true,
  "patterns_exported": 42,
  "folder": "/var/lib/akc-service/kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z",
  "error": null
}
```

#### Example: Basic Export (by Entity)

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-entity"
  }'
```

Response:
```json
{
  "success": true,
  "patterns_exported": 42,
  "folder": "./kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z"
}
```

#### Example: Custom Export Path

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "export_path": "/tmp/kb_snapshot",
    "organization": "by-entity"
  }'
```

#### Example: High Confidence Filtering

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-tier",
    "min_confidence": 0.7,
    "include_demoted": false
  }'
```

Response (only patterns with confidence ≥ 0.7):
```json
{
  "success": true,
  "patterns_exported": 28,
  "folder": "./kb_export",
  "organization": "by-tier",
  "exported_at": "2026-05-05T14:22:15Z"
}
```

#### Example: Dry-Run (Validation Without Writing)

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-pattern-type",
    "dry_run": true
  }'
```

Response (reports what would be exported without creating files):
```json
{
  "success": true,
  "patterns_exported": 42,
  "folder": "./kb_export",
  "organization": "by-pattern-type",
  "exported_at": "2026-05-05T14:22:15Z",
  "dry_run": true
}
```

---

## Usage - CLI

### Command: python -m akc_service.kb_exporter

Export patterns via command line for batch operations and scripting.

#### Basic Syntax

```bash
python -m akc_service.kb_exporter [OPTIONS]
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--export-path` | Path | AKC_SERVICE_KB_EXPORT_DIR | Target directory for exports |
| `--patterns-file` | Path | {KB_DIR}/patterns.jsonl | Source JSONL file |
| `--organization` | Choice | AKC_SERVICE_KB_EXPORT_FORMAT | Organization: by-entity \| by-tier \| by-pattern-type |
| `--min-confidence` | Float | AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE | Minimum confidence (0.0-1.0) |
| `--include-demoted` | Flag | False | Include demoted patterns |
| `--dry-run` | Flag | False | Validate without writing |

#### Examples

**Export all patterns with default settings:**

```bash
python -m akc_service.kb_exporter --export-path ./kb_export
```

Output:
```json
{
  "success": true,
  "patterns_exported": 42,
  "folder": "/path/to/kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z"
}
✓ Exported 42 patterns to /path/to/kb_export
```

**Export by tier with confidence filtering:**

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --organization by-tier \
  --min-confidence 0.7
```

Output:
```json
{
  "success": true,
  "patterns_exported": 28,
  "folder": "/path/to/kb_export",
  "organization": "by-tier",
  "exported_at": "2026-05-05T14:22:15Z"
}
✓ Exported 28 patterns to /path/to/kb_export
```

**Export by pattern type including demoted patterns:**

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --organization by-pattern-type \
  --include-demoted
```

**Dry-run to validate before export:**

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --dry-run
```

Output (no files written):
```json
{
  "success": true,
  "patterns_exported": 42,
  "folder": "/path/to/kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z",
  "dry_run": true
}
✓ Exported 42 patterns to /path/to/kb_export
```

**Custom patterns file (useful for testing):**

```bash
python -m akc_service.kb_exporter \
  --patterns-file ./patterns_backup.jsonl \
  --export-path ./kb_export_backup
```

**All options combined:**

```bash
python -m akc_service.kb_exporter \
  --export-path /var/lib/kb_staging \
  --patterns-file ./patterns.jsonl \
  --organization by-tier \
  --min-confidence 0.6 \
  --include-demoted
```

---

## Organization Strategies

Export patterns can be organized using three different strategies, each optimized for different use cases.

### Strategy 1: by-entity (Default)

**When to use:** Default choice for most use cases. Best when graphRAG queries often reference specific game entities (Player, Enemy, Environment).

**Folder Structure:**

```
kb_export/
├── by-entity/
│   ├── player/
│   │   ├── movement_PATTERN_001.md
│   │   ├── health_PATTERN_002.md
│   │   └── ...
│   ├── enemy/
│   │   ├── ai_PATTERN_003.md
│   │   ├── health_PATTERN_004.md
│   │   └── ...
│   ├── environment/
│   │   ├── collision_PATTERN_005.md
│   │   └── ...
│   └── ...
├── INDEX.md
└── ...
```

**Use Cases:**
- Game entity patterns (Player physics, Enemy behavior, Item mechanics)
- Component-based architecture queries
- Entity-centric knowledge retrieval
- Multi-entity interaction patterns

**graphRAG Integration:**
```
[graphRAG Index]
├── Player entity node
│   ├── movement pattern
│   ├── health pattern
│   └── ...
├── Enemy entity node
│   ├── ai pattern
│   └── ...
└── ...
```

**CLI Command:**
```bash
python -m akc_service.kb_exporter --export-path ./kb_export --organization by-entity
```

### Strategy 2: by-tier

**When to use:** When confidence/maturity of patterns is the primary filter. Best for progressive rollout or tier-based pattern consumption.

**Folder Structure:**

```
kb_export/
├── by-tier/
│   ├── gold/
│   │   ├── player_PATTERN_001.md
│   │   ├── enemy_PATTERN_003.md
│   │   └── ...
│   ├── production/
│   │   ├── player_PATTERN_002.md
│   │   ├── environment_PATTERN_005.md
│   │   └── ...
│   ├── experimental/
│   │   ├── player_PATTERN_010.md
│   │   └── ...
│   ├── demoted/
│   │   ├── player_PATTERN_015.md
│   │   └── ...
│   └── unrated/
│       └── ...
├── INDEX.md
└── ...
```

**Use Cases:**
- Confidence-based filtering (only use Gold and Production patterns)
- Progressive feature rollout (experimental patterns to limited users)
- Pattern maturity workflows
- Quality-gated pattern consumption

**graphRAG Integration:**
```
[graphRAG Index]
├── Gold tier node (high confidence patterns)
│   └── all gold patterns
├── Production tier node (vetted patterns)
│   └── all production patterns
├── Experimental tier node (beta patterns)
│   └── all experimental patterns
└── Demoted tier node (inactive patterns)
    └── all demoted patterns
```

**CLI Command:**
```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --organization by-tier \
  --min-confidence 0.7
```

### Strategy 3: by-pattern-type

**When to use:** When pattern type/category is the primary organization. Best for domain-specific pattern grouping (physics, AI, rendering).

**Folder Structure:**

```
kb_export/
├── by-pattern-type/
│   ├── collision-detection/
│   │   ├── player_PATTERN_001.md
│   │   ├── environment_PATTERN_005.md
│   │   └── ...
│   ├── health-tracking/
│   │   ├── player_PATTERN_002.md
│   │   ├── enemy_PATTERN_003.md
│   │   └── ...
│   ├── ai-behavior/
│   │   ├── enemy_PATTERN_004.md
│   │   └── ...
│   ├── rendering/
│   │   ├── player_PATTERN_008.md
│   │   └── ...
│   └── ...
├── INDEX.md
└── ...
```

**Use Cases:**
- Domain-specific pattern grouping
- Cross-entity pattern types (e.g., all collision detection patterns)
- Specialized retrieval by pattern category
- Technical documentation organization

**graphRAG Integration:**
```
[graphRAG Index]
├── Collision Detection domain node
│   ├── player collision pattern
│   ├── environment collision pattern
│   └── ...
├── Health Tracking domain node
│   ├── player health pattern
│   ├── enemy health pattern
│   └── ...
├── AI Behavior domain node
│   └── ...
└── ...
```

**CLI Command:**
```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --organization by-pattern-type
```

---

## Markdown Format

### Output Example

Each pattern is exported as a single markdown file with the following structure:

```markdown
# Pattern: collision_detection_001

**Tier:** gold
**Confidence:** 95.0%
**Entity:** player → **Component:** physics
**Type:** collision-detection

## Description
Handles player-to-obstacle collision response, computing bounce-back velocity
and updating health status. This pattern is critical for platformer mechanics.

## Rule
When player velocity is not zero AND player bounds intersects obstacle bounds,
compute normal vector, apply impulse, and reduce health by collision_damage.

## Example (Correct)
```
var collision_point = player.global_position
var obstacle_normal = (collision_point - obstacle.global_position).normalized()
var impulse = obstacle_normal * bounce_force
player.velocity += impulse
player.health -= collision_damage
```

## Example (Incorrect)
```
# Missing normal computation
player.velocity.y = -player.velocity.y  # This is too simple
player.health -= 1  # Should scale with collision force
```

## Metadata
- **Tags:** platformer, physics, core-mechanic
- **Created:** 2026-04-01T10:00:00Z
- **Updated:** 2026-05-01T15:30:00Z
- **Source:** gameplay-testing
- **Dependencies:** physics-system
- **Conflicts With:** None
- **Guardrail Protected:** true
- **Usage Count:** 127
- **Failure Count:** 2

---
*Last synced: 2026-05-05T14:22:15Z | Schema version: 2.0*
```

### Field Descriptions

| Field | Source | Description |
|-------|--------|-------------|
| `Pattern ID` | pattern.id | Unique identifier for the pattern |
| `Tier` | confidence_tier | Pattern maturity tier (gold, production, experimental, demoted, unrated) |
| `Confidence` | confidence | Numerical confidence (0-1), formatted as percentage |
| `Entity` | entity | Game entity this pattern applies to |
| `Component` | component | Specific component or subsystem |
| `Type` | pattern_type | Category/classification of the pattern |
| `Description` | description | Human-readable explanation of the pattern |
| `Rule` | rule | Logical rule or condition the pattern implements |
| `Example (Correct)` | example_correct | Code/example showing correct pattern usage |
| `Example (Incorrect)` | example_incorrect | Code/example showing antipattern or wrong usage |
| `Tags` | tags | List of searchable tags/keywords |
| `Created` | created_at | ISO timestamp of pattern creation |
| `Updated` | updated_at | ISO timestamp of last update |
| `Source` | source | Where the pattern came from (testing, analytics, etc.) |
| `Dependencies` | dependencies | Other patterns this pattern depends on |
| `Conflicts With` | conflicts_with | Patterns that conflict with this one |
| `Guardrail Protected` | guardrail_protected | Whether pattern is protected from demotion |
| `Usage Count` | usage_count | Number of successful uses |
| `Failure Count` | failure_count | Number of failures or misapplications |
| `Schema Version` | schema_version | Version of pattern schema |
| `Last Synced` | (computed) | ISO timestamp of export |

---

## Filtering

### Confidence Threshold Filtering

Filter patterns by minimum confidence level. Patterns below the threshold are excluded from export.

#### API Example: Gold-tier Only

Export only patterns with confidence ≥ 0.8 (typical threshold for gold tier):

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-entity",
    "min_confidence": 0.8
  }'
```

Result:
```json
{
  "success": true,
  "patterns_exported": 15,
  "folder": "./kb_export",
  "organization": "by-entity",
  "exported_at": "2026-05-05T14:22:15Z"
}
```

#### API Example: Production and Above

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-tier",
    "min_confidence": 0.6
  }'
```

#### CLI Example: High Confidence Only

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --min-confidence 0.8
```

Output: Only patterns with 80%+ confidence

#### CLI Example: All Non-Demoted Patterns

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --organization by-tier
```

### Demotion Status

Control whether demoted patterns are included in exports.

#### API Example: Exclude Demoted Patterns (Default)

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-entity",
    "include_demoted": false
  }'
```

Demoted patterns are excluded from export.

#### API Example: Include All Patterns

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-entity",
    "include_demoted": true
  }'
```

Result: All patterns included, including demoted ones

#### CLI Example: Export with Demoted Patterns

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --include-demoted
```

#### CLI Example: Export Archive (All Patterns, High Min Confidence)

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export_archive \
  --min-confidence 0.0 \
  --include-demoted \
  --organization by-tier
```

Exports complete historical record: all tiers, all patterns.

---

## graphRAG Integration

### How graphRAG Consumes Exports

1. **Folder Scan:** graphRAG monitors AKC_SERVICE_KB_EXPORT_DIR for changes
2. **Markdown Parse:** Reads and parses all .md files
3. **Metadata Extraction:** Extracts pattern metadata (tier, entity, type, tags)
4. **Graph Construction:** Builds knowledge graph nodes and edges from patterns
5. **Indexing:** Indexes patterns for semantic retrieval
6. **RAG Queries:** Semantic queries retrieve relevant patterns from knowledge graph

### Step-by-Step Integration

#### Step 1: Configure AKC Service Export Directory

Set a shared path that graphRAG can access:

```bash
export AKC_SERVICE_KB_EXPORT_DIR=/var/lib/knowledge-base/patterns
export AKC_SERVICE_KB_EXPORT_FORMAT=by-entity
export AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE=0.5
```

#### Step 2: Create Export Directory and Set Permissions

```bash
mkdir -p /var/lib/knowledge-base/patterns
chmod 755 /var/lib/knowledge-base/patterns
# Allow both AKC and graphRAG processes to read/write
chown :knowledge-group /var/lib/knowledge-base/patterns
chmod 775 /var/lib/knowledge-base/patterns
```

#### Step 3: Export Patterns to Markdown

**Option A: Via API**

```bash
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "by-entity",
    "min_confidence": 0.5
  }'
```

**Option B: Via CLI (batch/scheduled)**

```bash
cd /home/akc-service
python -m akc_service.kb_exporter \
  --export-path /var/lib/knowledge-base/patterns \
  --organization by-entity \
  --min-confidence 0.5
```

**Option C: Automated Export (Cron Job)**

```bash
# /etc/cron.d/akc-kb-export
# Export patterns daily at 2:00 AM
0 2 * * * akc-user cd /home/akc-service && \
  python -m akc_service.kb_exporter \
  --export-path /var/lib/knowledge-base/patterns \
  --organization by-entity \
  --min-confidence 0.5 >> /var/log/akc-kb-export.log 2>&1
```

#### Step 4: Configure graphRAG to Scan Export Directory

In graphRAG configuration (example: GraphRAG .env or settings):

```bash
# graphRAG settings
GRAPHRAG_KB_SOURCE_DIR=/var/lib/knowledge-base/patterns
GRAPHRAG_AUTO_RESCAN=true
GRAPHRAG_RESCAN_INTERVAL=3600  # 1 hour
```

#### Step 5: Verify Integration

**Check exported files:**

```bash
ls -la /var/lib/knowledge-base/patterns/
ls -la /var/lib/knowledge-base/patterns/by-entity/
```

**Verify INDEX.md created:**

```bash
cat /var/lib/knowledge-base/patterns/INDEX.md
```

**Test graphRAG indexing:**

```bash
# Query graphRAG for indexed patterns
curl http://localhost:7860/api/rag/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "player collision detection",
    "top_k": 5
  }'
```

### Configuration Example: Docker Compose

```yaml
version: '3.8'

services:
  akc-service:
    image: akc-service:latest
    environment:
      AKC_SERVICE_KB_DIR: /data/kb
      AKC_SERVICE_KB_EXPORT_DIR: /data/kb_export
      AKC_SERVICE_KB_EXPORT_FORMAT: by-entity
      AKC_SERVICE_KB_EXPORT_MIN_CONFIDENCE: 0.5
    volumes:
      - kb_data:/data/kb
      - kb_export:/data/kb_export
    ports:
      - "8000:8000"

  graphrag:
    image: graphrag:latest
    environment:
      GRAPHRAG_KB_SOURCE_DIR: /data/kb_export
      GRAPHRAG_AUTO_RESCAN: "true"
      GRAPHRAG_RESCAN_INTERVAL: 3600
    volumes:
      - kb_export:/data/kb_export:ro
    ports:
      - "7860:7860"
    depends_on:
      - akc-service

volumes:
  kb_data:
  kb_export:
```

**Usage:**

```bash
# Start both services
docker-compose up -d

# Trigger export via API
curl -X POST http://localhost:8000/akc/v1/kb/export-markdown \
  -H "Content-Type: application/json" \
  -d '{"organization": "by-entity"}'

# graphRAG automatically rescans and indexes
```

---

## Notes

### Continuous Synchronization

KB Export does not interfere with normal pattern synchronization:

- **patterns.jsonl** continues to sync normally
- **Export** is on-demand, triggered via API or CLI
- **No breaking changes** to existing AKC Service functionality
- Multiple exports can coexist (different export paths, organizations)

### JSONL Storage Unaffected

- patterns.jsonl remains the canonical storage format
- All pattern updates continue to append to JSONL
- Exports are derived views, not replacements
- Deleting exports does not affect JSONL

### Disk Space Considerations

**Storage overhead:** Markdown exports typically consume 2-5× the JSONL file size (due to formatting, examples, metadata expansion).

**Example:**
- patterns.jsonl: 10 MB
- Markdown export: 20-50 MB

**Management strategies:**
- Use `min_confidence` filter to exclude experimental patterns
- Organize by-tier and exclude demoted patterns
- Keep multiple versions in dated directories: `/kb_export/2026-05-05/`, `/kb_export/2026-05-06/`
- Archive old exports to S3/cold storage

### Thread Safety

- **Thread-safe:** Multiple concurrent exports to different paths are safe
- **Not thread-safe:** Exporting to the same path concurrently may result in partial writes
- **Recommendation:** Use unique export paths per export or serialize exports via a lock

**Example (safe concurrent exports):**

```bash
# Process 1
python -m akc_service.kb_exporter --export-path ./kb_export_v1 &

# Process 2
python -m akc_service.kb_exporter --export-path ./kb_export_v2 &

# Both complete successfully
wait
```

**Example (unsafe concurrent exports):**

```bash
# Process 1
python -m akc_service.kb_exporter --export-path ./kb_export &

# Process 2 (same path!) — may overwrite Process 1's files
python -m akc_service.kb_exporter --export-path ./kb_export &

# Race condition risk
```

---

## Troubleshooting

### Export Succeeds But Files Not Created

**Symptom:** API returns `success: true` but no markdown files in export directory.

**Cause 1: Directory Permissions**

```bash
# Check permissions
ls -ld /path/to/kb_export
# Should show: drwxrwxr-x or similar (writable)

# Fix permissions
chmod 755 /path/to/kb_export
```

**Cause 2: Disk Space**

```bash
# Check available space
df -h /path/to/kb_export

# If full, free space or use different directory
export AKC_SERVICE_KB_EXPORT_DIR=/var/lib/akc-service/kb_export
```

**Cause 3: Path Issues**

```bash
# Verify path is absolute
export AKC_SERVICE_KB_EXPORT_DIR=$(cd /path/to/kb_export && pwd)

# Re-run export
python -m akc_service.kb_exporter --export-path "$AKC_SERVICE_KB_EXPORT_DIR"
```

**Diagnostic Script:**

```bash
#!/bin/bash
EXPORT_DIR="${AKC_SERVICE_KB_EXPORT_DIR:-./ kb_export}"

echo "Export directory: $EXPORT_DIR"
echo "Exists: $(test -d "$EXPORT_DIR" && echo "yes" || echo "no")"
echo "Writable: $(test -w "$EXPORT_DIR" && echo "yes" || echo "no")"
echo "Space available: $(df -h "$EXPORT_DIR" | tail -1)"
ls -la "$EXPORT_DIR" 2>&1 | head -20
```

### Patterns Missing (Confidence Threshold)

**Symptom:** Expected patterns don't appear in export, but INDEX.md shows lower count.

**Cause:** min_confidence filter is excluding patterns.

**Solution 1: Lower Confidence Threshold**

```bash
# Current: min-confidence 0.8 (excludes experimental)
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --min-confidence 0.0  # Include all patterns
```

**Solution 2: Check Pattern Confidence**

```bash
# View patterns.jsonl to check confidence values
cat kb/patterns.jsonl | python3 -m json.tool | grep -A 5 '"confidence"'

# Count patterns by tier
cat kb/patterns.jsonl | python3 << 'EOF'
import json, sys
tiers = {}
for line in sys.stdin:
    p = json.loads(line)
    t = p.get('confidence_tier', 'unrated')
    tiers[t] = tiers.get(t, 0) + 1
for t, c in sorted(tiers.items()):
    print(f"{t}: {c}")
EOF
```

**Solution 3: Include Demoted Patterns**

```bash
python -m akc_service.kb_exporter \
  --export-path ./kb_export \
  --include-demoted  # Include patterns with confidence_tier='demoted'
```

### graphRAG Not Finding Patterns

**Symptom:** graphRAG queries return no results despite exported patterns in folder.

**Cause 1: Folder Not Scanned**

```bash
# Verify graphRAG is configured to scan correct directory
# Check graphRAG logs
tail -f /var/log/graphrag/index.log | grep -i "scan\|pattern\|markdown"

# Manually trigger scan in graphRAG
curl -X POST http://localhost:7860/api/index/rescan
```

**Cause 2: File Format Issues**

```bash
# Verify markdown files are valid
file /path/to/kb_export/by-entity/*/*.md
# Should show: ASCII text or UTF-8 Unicode text

# Check for encoding issues
iconv -f UTF-8 -t UTF-8 -c /path/to/file.md > /tmp/test.md
cmp /path/to/file.md /tmp/test.md
```

**Cause 3: Permissions Preventing Read**

```bash
# Verify graphRAG can read files
sudo -u graphrag cat /path/to/kb_export/INDEX.md
# If permission denied, fix permissions
chmod 644 /path/to/kb_export/**/*.md
chmod 755 /path/to/kb_export
chmod 755 /path/to/kb_export/*/*
```

**Cause 4: INDEX.md Metadata**

```bash
# Check INDEX.md structure
cat /path/to/kb_export/INDEX.md

# Should contain sections:
# - Statistics
# - Organization
# - Integration with graphRAG

# If missing, re-export
python -m akc_service.kb_exporter \
  --export-path /path/to/kb_export \
  --organization by-entity
```

**Comprehensive Diagnostic:**

```bash
#!/bin/bash
EXPORT_DIR="${AKC_SERVICE_KB_EXPORT_DIR:-./ kb_export}"

echo "=== KB Export Diagnostic ==="
echo "Export directory: $EXPORT_DIR"
echo ""

echo "=== Directory Structure ==="
find "$EXPORT_DIR" -type f -name "*.md" | head -20
echo ""

echo "=== INDEX.md ==="
head -30 "$EXPORT_DIR/INDEX.md"
echo ""

echo "=== Sample Pattern ==="
find "$EXPORT_DIR" -name "*.md" -type f ! -name "INDEX.md" | head -1 | xargs head -20
echo ""

echo "=== Permission Check ==="
ls -ld "$EXPORT_DIR"
ls -l "$EXPORT_DIR/"*.md 2>/dev/null | head -3
echo ""

echo "=== File Count ==="
echo "Total MD files: $(find "$EXPORT_DIR" -name "*.md" -type f | wc -l)"
```

---

## Summary

KB Export enables seamless integration between AKC Service's pattern knowledge base and graphRAG's semantic retrieval system. By organizing patterns into markdown documents and exposing them via API and CLI, teams can:

1. **Export** patterns on-demand or on schedule
2. **Organize** by entity, tier, or type
3. **Filter** by confidence and status
4. **Integrate** with graphRAG for semantic search
5. **Sync** continuously without disruption

For questions or issues, consult the API Reference or CONFIGURATION guide.
