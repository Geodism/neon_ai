from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

from PySide6.QtWidgets import QApplication

from neon_ai.automation.runtime_flags import (
    LEGACY_AUTOMATION_DISABLED_MESSAGE,
    legacy_automation_runtime_enabled,
    require_legacy_automation_runtime,
)
from neon_ai.bootstrap import build_container
from neon_ai.ui.main_window import NeonMainWindow
from neon_ai.database.automation import (
    sweep_for_aging_unsent_estimates,
    sweep_for_completed_rfq_estimates,
    sweep_for_deposit_invoice_reminders,
    sweep_for_locked_estimates,
    sweep_for_ready_leads,
    sweep_for_sent_estimate_followups,
)
from neon_ai.database.purchases import (
    sweep_for_backordered_purchase_orders,
    sweep_for_locked_purchase_orders,
    sweep_for_po_eta_followups,
)
from neon_ai.database.rfq import sweep_for_outstanding_rfq_followups
from neon_ai.database.vendor_invoices import sweep_for_ready_to_pay_vendor_invoices


def _perf_log(area: str, name: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    print(f"[PERF] area={area} name={name} elapsed_ms={elapsed_ms:.2f}")


def _should_start_legacy_gateway_thread() -> tuple[bool, str]:
    if os.environ.get("NEON_DISABLE_GATEWAY") == "1":
        return False, "Gateway disabled by NEON_DISABLE_GATEWAY=1"
    if not legacy_automation_runtime_enabled():
        return False, LEGACY_AUTOMATION_DISABLED_MESSAGE
    return True, "Legacy automation runtime enabled."


def run_gateway_loop() -> None:
    """LEGACY_AUTOMATION_DISABLED_BY_DEFAULT: preserve the old background loop only behind an explicit legacy flag."""
    if not require_legacy_automation_runtime("main.run_gateway_loop"):
        return

    from neon_ai.gateway import check_for_instructions

    print("\n" + "=" * 40)
    print("\U0001F680 ARGON GATEWAY: Multi-Tasking Active")
    print("=" * 40)

    startup_time = time.time()
    initial_delay = 60

    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\U0001F493 [HEARTBEAT] Checking Inbox at {current_time}...")
        try:
            check_for_instructions()
        except Exception as exc:  # pragma: no cover - runtime operational logging
            print(f"\u26A0\uFE0F [INBOX ERROR]: {exc}")

        uptime = time.time() - startup_time
        if uptime > initial_delay:
            print("\U0001F50D [SWEEPER] Checking for Locked Estimates...")
            try:
                sweep_for_ready_leads()
                sweep_for_locked_estimates()
                sweep_for_sent_estimate_followups()
                sweep_for_completed_rfq_estimates()
                sweep_for_aging_unsent_estimates()
                sweep_for_deposit_invoice_reminders()
                sweep_for_outstanding_rfq_followups()
                sweep_for_locked_purchase_orders()
                sweep_for_po_eta_followups()
                sweep_for_backordered_purchase_orders()
                sweep_for_ready_to_pay_vendor_invoices()
            except Exception as exc:  # pragma: no cover - runtime operational logging
                print(f"\u26A0\uFE0F [SWEEPER ERROR]: {exc}")
        else:
            print(f"\u23F3 [WARMUP] Sweeper cooling down... {int(initial_delay - uptime)}s left.")

        time.sleep(120)


def main() -> int:
    main_started_at = time.perf_counter()
    try:
        should_start_gateway, gateway_reason = _should_start_legacy_gateway_thread()
        if not should_start_gateway:
            print(gateway_reason)
        else:
            gateway_thread_started_at = time.perf_counter()
            try:
                gateway_thread = threading.Thread(target=run_gateway_loop, daemon=True)
                gateway_thread.start()
            finally:
                _perf_log("startup", "gateway_thread_start", gateway_thread_started_at)

        qapplication_started_at = time.perf_counter()
        try:
            app = QApplication(sys.argv)
        finally:
            _perf_log("startup", "qapplication_create", qapplication_started_at)

        main_window_started_at = time.perf_counter()
        try:
            container = build_container()
            window = NeonMainWindow(container=container)
        finally:
            _perf_log("startup", "main_window_init", main_window_started_at)

        window.show()
        return app.exec()
    finally:
        _perf_log("startup", "main.total", main_started_at)


if __name__ == "__main__":
    raise SystemExit(main())
