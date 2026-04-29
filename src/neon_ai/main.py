from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

from PySide6.QtWidgets import QApplication

from neon_ai.ui.main_window import NeonMainWindow
from neon_ai.gateway import check_for_instructions
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


def run_gateway_loop() -> None:
    """Preserve the legacy background loop behavior from app/main.py."""
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
    if os.environ.get("NEON_DISABLE_GATEWAY") == "1":
        print("Gateway disabled by NEON_DISABLE_GATEWAY=1")
    else:
        gateway_thread = threading.Thread(target=run_gateway_loop, daemon=True)
        gateway_thread.start()

    app = QApplication(sys.argv)
    window = NeonMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
