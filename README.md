# Neon_ai

Neon_ai is a parity-first PySide6 port of the legacy Tkinter application in `D:\Argon_ai\app`.

This project is intentionally constrained:

- Same PostgreSQL schema and table names.
- Same legacy database modules under `app/database` as the source of truth.
- Same navigation categories and page boundaries as the Tkinter app.
- Same workflow intent, with PySide6 replacing only the presentation layer.

## Current status

This first pass establishes the new PySide6 project shell, dashboard port, legacy runtime wiring, one-to-one page targets, and parity tracking documents.

The dashboard is implemented against the existing legacy database functions:

- `database.metrics.get_dashboard_metrics`
- `database.estimates.get_dashboard_estimates`

The remaining pages are scaffolded as explicit parity targets so they can be ported screen-by-screen without collapsing legacy workflows into generalized pages.

## Run

```powershell
python -m pip install -e .
python -m neon_ai.main
```

## Legacy spec

Primary specification:

- `D:\Argon_ai\app\main.py`
- `D:\Argon_ai\app\*.py`
- `D:\Argon_ai\app\database\*.py`

Supporting docs:

- `docs/legacy_ui_inventory.md`
- `docs/parity_checklist.md`
