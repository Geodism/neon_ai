from __future__ import annotations

"""Level 2.5 naming-aligned alias for the canonical PO ETA resolution bridge.

The active implementation remains in `po_eta_resolution_service.py`.
This module exists only to align service naming with the Level 2.5 route map.
It adds no new authority and performs no direct business mutation.
"""

from neon_ai.services.po_eta_resolution_service import (
    POEtaResolutionResult,
    build_resolved_po_eta_status_payload,
    create_revised_po_eta_status_proposal_from_answer,
    resolve_po_eta_question_to_proposal,
)

__all__ = [
    "POEtaResolutionResult",
    "build_resolved_po_eta_status_payload",
    "create_revised_po_eta_status_proposal_from_answer",
    "resolve_po_eta_question_to_proposal",
]
