from __future__ import annotations

"""Level 2.5 naming-aligned alias for the canonical packing-slip watcher.

The active implementation remains in `packing_slip_receiving_watcher_service.py`.
This module exists only to align service naming with the Level 2.5 route map.
It adds no new authority and performs no direct business mutation.
"""

from neon_ai.services.packing_slip_receiving_watcher_service import (
    AUTOMATION_KEY,
    ROUTE_ACTION_TYPE,
    ROUTE_WORKFLOW,
    PackingSlipReceivingWatcherResult as StagedReceiptWatcherResult,
    process_packing_slip_receiving_message as process_staged_receipt_message,
)

__all__ = [
    "AUTOMATION_KEY",
    "ROUTE_ACTION_TYPE",
    "ROUTE_WORKFLOW",
    "StagedReceiptWatcherResult",
    "process_staged_receipt_message",
]
