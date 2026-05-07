from __future__ import annotations

import os


LEGACY_AUTOMATION_ENV = "NEON_ENABLE_LEGACY_AUTOMATION"
LEGACY_AUTOMATION_DISABLED_MARKER = "LEGACY_AUTOMATION_DISABLED_BY_DEFAULT"
LEGACY_AUTOMATION_DISABLED_MESSAGE = (
    "Legacy automation runtime disabled. Future automation will run through Automation Center presets."
)


def legacy_automation_runtime_enabled() -> bool:
    return str(os.getenv(LEGACY_AUTOMATION_ENV, "0")).strip().lower() in {"1", "true", "yes", "on"}


def require_legacy_automation_runtime(entrypoint: str, *, log: bool = True) -> bool:
    enabled = legacy_automation_runtime_enabled()
    if not enabled and log:
        print(
            f"[AUTOMATION] {entrypoint}: {LEGACY_AUTOMATION_DISABLED_MESSAGE} "
            f"Set {LEGACY_AUTOMATION_ENV}=1 to re-enable this legacy path."
        )
    return enabled
