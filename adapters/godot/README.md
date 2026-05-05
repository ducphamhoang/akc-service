# Godot Adapter (Phase 3 — ext-3)

This directory is reserved for the Godot-specific adapter.

## What Gets Added in Phase 3

- `godot_akc_adapter.py` — wires GDScript linting to akc-service
- `test runner integration` — sends test metrics to akc-service
- Example Godot project using extracted packages

## Dependency Direction (after Phase 3)

```
My Demon (Godot project)
  → godot_akc_adapter.py (in this directory)
    → akc-service REST API (port 8000)
```

Do not add code here until Phase 3 (ext-3: Godot Adapter Integration).
