from __future__ import annotations

"""Level 2.5 naming-aligned alias for the canonical staged-receipt resolution bridge.

The active implementation remains in `packing_slip_resolution_service.py`.
This module exists only to align service naming with the Level 2.5 route map.
It adds no new authority and performs no direct business mutation.
"""

from neon_ai.services.packing_slip_resolution_service import (
    PackingSlipResolutionResult as StagedReceiptResolutionBridgeResult,
    build_resolved_staged_receipt_payload,
    create_revised_staged_receipt_proposal_from_answer,
    resolve_packing_slip_question_to_proposal as resolve_staged_receipt_question_to_proposal,
)

__all__ = [
    "StagedReceiptResolutionBridgeResult",
    "build_resolved_staged_receipt_payload",
    "create_revised_staged_receipt_proposal_from_answer",
    "resolve_staged_receipt_question_to_proposal",
]
