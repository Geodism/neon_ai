# ARGON Separation Final Audit

Audit date: 2026-04-29  
Workspace: `D:\Neon_ai`  
Scope: source scan plus safe import tests only  
Code changes made: `0`

## 1. Executive Verdict

**Verdict: PARTIAL**

Current evidence indicates `Neon_ai` is no longer hard-wired to `D:\Argon_ai` for the audited import and startup surfaces:

- no live `D:\Argon_ai` path reference was found in `src` or `scripts`
- no `legacy_app_root` or `ensure_legacy_import_paths` usage remains in `src` or `scripts`
- safe imports for `neon_ai.main`, `neon_ai.bootstrap`, `neon_ai.gateway`, `neon_ai.database.connection`, and `neon_ai.ui.main_window` all passed without `D:\Argon_ai` being consulted
- Neon now loads only `D:\Neon_ai\.env`

The verdict is `PARTIAL` instead of `PASS` because:

- this pass did not execute the manual rename-proof test that physically removes `D:\Argon_ai` from the machine during launch
- one live legacy-style bare import still exists in `src/neon_ai/database/estimates.py:790`

Practical answer to the primary question:

- **Based on the current scan and import tests, Neon_ai appears able to run without `D:\Argon_ai` existing on disk.**
- **A final rename-proof launch test is still recommended before calling the separation fully proven.**

## 2. Remaining Hard Dependencies

### `D:\Argon_ai` hard dependencies

No active runtime dependency on `D:\Argon_ai` was found in the current live application source under `src`.

No active `legacy_app_root()` or `ensure_legacy_import_paths()` usage was found in `src` or `scripts`.

### Residual runtime dependency risk

| File | Line | Dependency | Why It Matters | Fix Recommendation |
| --- | ---: | --- | --- | --- |
| `src/neon_ai/database/estimates.py` | 790 | `from database.estimates import get_estimate_materials` | This is a legacy bare import inside live source. It is not a `D:\Argon_ai` disk-path dependency, but if this code path runs it may fail because top-level `database` is no longer guaranteed to exist. | Change to `from neon_ai.database.estimates import get_estimate_materials` or remove the stray UI helper if it is dead code. |

### Python path bridge note

| File | Line | Dependency | Why It Matters | Fix Recommendation |
| --- | ---: | --- | --- | --- |
| `scripts/functional_audit_runner.py` | 26 | `sys.path.insert(0, str(SRC))` | Script-only path setup for the audit harness. This points to `D:\Neon_ai\src`, not `D:\Argon_ai`, so it does not block separation. | Leave as script-only bootstrap or convert the script to package-based execution later. |

## 3. Remaining Cosmetic Argon Naming

These references do not currently prove a runtime dependency on `D:\Argon_ai`, but they do preserve Argon-era branding and may confuse operators or future audits.

| File | Line | Name / String | Runtime Independence Impact | Rename Recommendation |
| --- | ---: | --- | --- | --- |
| `src/neon_ai/__init__.py` | 1 | `legacy Argon AI application` | Cosmetic only | Update package docstring to Neon-only wording. |
| `src/neon_ai/main.py` | 38 | `ARGON GATEWAY` | Cosmetic only | Rename startup banner to Neon branding. |
| `src/neon_ai/ui/main_window.py` | 61, 138 | `Argon Operations Command`, `ARGON` | Cosmetic only | Rename main window and header labels. |
| `src/neon_ai/ui/pages/dashboard_page.py` | 78 | `Argon Operations Command` | Cosmetic only | Align dashboard title with Neon branding. |
| `src/neon_ai/ui/dialogs/private_brain_dialog.py` | 26, 63, 68 | `Argon-Prime Executive Chat`, `ARGON:` | Cosmetic only | Rename chat title and transcript speaker label if desired. |
| `src/neon_ai/gateway.py` | 18, 57, 104, 577 | `ArgonLocalAI`, `Argon SMTP gateway`, `Argon-Public` | No direct disk dependency; internal legacy naming only | Rename internal router and log strings after separation work is fully closed. |
| `src/neon_ai/automation/local_brain.py` | 9, 16, 42, 185 | `ArgonLocalAI`, `Argon Lead-to-Cash`, `Argon-Prime` | No direct disk dependency; internal naming only | Rename class, workflow label, and prompts later if branding matters. |
| `src/neon_ai/ui/pages/customer_page.py` | 795 | `Argon will update...` | Cosmetic only | Update operator messaging. |
| `src/neon_ai/ui/pages/employee_manager_page.py` | 256 | `across all Argon apps` | Cosmetic only | Update success message. |
| `src/neon_ai/database/automation.py` | 433, 1183, 1210, 1297, 1548, 1551, 1556, 1590, 1635, 1676, 1678 | `Argon Electrical`, `ArgonLocalAI` | Cosmetic for signatures and subjects; internal naming for AI helper | Rename customer-facing strings before broader live use. |
| `src/neon_ai/database/rfq.py` | 455, 711 | `Argon Electrical` | Cosmetic only | Rename vendor-facing email signature text. |
| `src/neon_ai/database/purchases.py` | 1337, 1391, 1454, 1720 | `Argon Electrical` | Cosmetic only | Rename PO and vendor email signature text. |
| `src/neon_ai/ui/pages/invoice_creator_page.py` | 501 | `Argon Electrical` | Cosmetic only | Rename invoice email signature text. |
| `src/neon_ai/ui/pages/invoice_viewer_page.py` | 268 | `Argon Electrical` | Cosmetic only | Rename resend email signature text. |
| `src/neon_ai/ui/pages/timesheet_manager_page.py` | 545 | `Argon Electrical` | Cosmetic only | Rename timesheet email signature text. |
| `src/neon_ai/automation/argonleadtocash.json` | 2 | `ArgonLeadToCash` | Cosmetic only if workflow stays Neon-local | Rename later only if branding consistency matters. |

### Docs-only Argon path references

The following files still mention `D:\Argon_ai\app`, but they are documentation or historical audit material rather than live runtime code:

- `README.md:3,34-36`
- `PORT_AUDIT.md:5-9` and many later references
- `ARGON_DEPENDENCY_AUDIT.md` throughout
- `docs/legacy_ui_inventory.md:3`

## 4. Remaining Runtime-State Cleanup

These items are not Argon disk dependencies, but they still place mutable runtime state under `src\neon_ai`, which is not ideal.

| File | Purpose | Current Status | Recommendation |
| --- | --- | --- | --- |
| `src/neon_ai/automation/argonleadtocash.json` | Local workflow/rules asset for the private/public brain logic | Exists and is tracked | Keep tracked as a source asset. Rename later only if branding cleanup is desired. |
| `src/neon_ai/classification_feedback.json` | Classification feedback memory/state | Exists and is tracked | Move to `D:\Neon_ai\data\` or another app-data location and stop tracking generated state in source. |
| `src/neon_ai/rfq_dispatch_log.json` | RFQ dispatch runtime log | Exists and is untracked | Move to `D:\Neon_ai\data\` and ignore it in git. |
| `src/neon_ai/customer_invoice_memory.json` | Invoice memory/runtime state | Referenced in code, not currently present | Change path logic so it is created outside `src` when used. |
| `src/neon_ai/po_dispatch_log.json` | PO dispatch runtime log | Referenced in code, not currently present | Change path logic so it is created outside `src` when used. |
| `src/neon_ai/vendor_invoice_memory.json` | Vendor invoice memory/runtime state | Referenced in code, not currently present | Change path logic so it is created outside `src` when used. |

### Runtime-state path definitions still pointing into `src`

- `src/neon_ai/database/classification_feedback.py:9`
- `src/neon_ai/database/invoices.py:11`
- `src/neon_ai/database/purchases.py:13`
- `src/neon_ai/database/rfq.py:16`
- `src/neon_ai/database/vendor_invoices.py:25`

## 5. Import-Test Results

All import tests were run with safe flags to avoid starting the gateway or opening an interactive display:

- `NEON_DISABLE_GATEWAY=1`
- `NEON_DISABLE_PRIVATE_BRAIN=1`
- `NEON_SAFE_MODE=1`
- `QT_QPA_PLATFORM=offscreen` for `main_window`

| Command | Result | Output Summary |
| --- | --- | --- |
| `python -c "import neon_ai.main; print('main import OK')"` | PASS | Printed `Successfully loaded Argon Lead-to-Cash Workflow rules.` twice, then `main import OK`. |
| `python -c "import neon_ai.bootstrap; print('bootstrap import OK')"` | PASS | Printed `bootstrap import OK`. |
| `python -c "import neon_ai.gateway; print('gateway import OK')"` | PASS | Printed `Successfully loaded Argon Lead-to-Cash Workflow rules.` twice, then `gateway import OK`. |
| `python -c "import neon_ai.database.connection; print('connection import OK')"` | PASS | Printed `connection import OK`. |
| `python -c "import neon_ai.ui.main_window; print('main_window import OK')"` | PASS | Printed `Successfully loaded Argon Lead-to-Cash Workflow rules.`, then `main_window import OK`. |

### Import test interpretation

- `bootstrap` and `connection` imported cleanly with no Argon-era output at all.
- `main`, `gateway`, and `main_window` still import the Neon-local private-brain workflow module, which prints Argon-branded text.
- That behavior does **not** indicate a dependency on `D:\Argon_ai`; it indicates remaining Neon-local branding and private-brain coupling.

## 6. Final Proof-Test Plan

This rename-proof test was **not** executed in this pass.

### Exact commands

```powershell
if (Test-Path 'D:\Argon_ai') {
    Rename-Item -LiteralPath 'D:\Argon_ai' -NewName 'Argon_ai_DISABLED_TEST'
}

Set-Location 'D:\Neon_ai'
$env:NEON_DISABLE_GATEWAY = '1'
$env:NEON_DISABLE_PRIVATE_BRAIN = '1'
$env:NEON_SAFE_MODE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'

& '.\.venv\Scripts\python.exe' -c "import neon_ai.main; print('main import OK')"
& '.\.venv\Scripts\python.exe' -m neon_ai.main
```

### Expected result

- `main import OK` prints successfully
- the app starts without `ModuleNotFoundError`, path-resolution errors, or any attempt to read from `D:\Argon_ai`
- no gateway polling starts because `NEON_DISABLE_GATEWAY=1`

### Rollback command

```powershell
Set-Location 'D:\'
if (Test-Path 'D:\Argon_ai_DISABLED_TEST') {
    Rename-Item -LiteralPath 'D:\Argon_ai_DISABLED_TEST' -NewName 'Argon_ai'
}
```

## 7. Fix Plan

### Phase 1: remove true runtime dependencies

1. Fix the remaining bare import in `src/neon_ai/database/estimates.py:790`.
2. Re-run the five import tests from this audit.
3. Run the manual rename-proof test with `D:\Argon_ai` temporarily renamed.

### Phase 2: move runtime files out of `src` if needed

1. Move dispatch logs and memory JSON files to `D:\Neon_ai\data\` or another app-data directory.
2. Stop tracking generated runtime-state JSON in source control.
3. Leave `argonleadtocash.json` tracked if it is a real source asset.

### Phase 3: rename cosmetic Argon branding if desired

1. Rename UI titles, banners, prompts, and email signatures.
2. Rename `ArgonLocalAI` and `argonleadtocash.json` only after runtime separation is considered complete.
3. Update docs so future audits do not confuse historical references with live dependencies.

### Phase 4: run rename-proof test

1. Temporarily rename `D:\Argon_ai`.
2. Launch Neon with `NEON_DISABLE_GATEWAY=1`, `NEON_DISABLE_PRIVATE_BRAIN=1`, and `NEON_SAFE_MODE=1`.
3. Confirm startup and critical navigation work.
4. Restore `D:\Argon_ai`.

## 8. Bottom Line

For the question, "Can Neon_ai run without `D:\Argon_ai` existing on disk?":

- **Current evidence says probably yes.**
- **The remaining concern is not a discovered `D:\Argon_ai` path dependency, but one leftover legacy bare import and the fact that the physical rename-proof launch test has not yet been performed.**
