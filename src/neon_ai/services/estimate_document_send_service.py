from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.database.estimate_document_drafts import (
    ensure_estimate_document_draft_table,
    get_estimate_document_draft,
)
from neon_ai.database.estimates import get_detailed_estimate_data


def _build_context(
    draft: dict[str, Any] | None,
    estimate_parent: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "EstimateDocumentDraftID": draft.get("EstimateDocumentDraftID") if draft else None,
        "EstimateID": draft.get("EstimateID") if draft else None,
        "CustomerName": (estimate_parent or {}).get("CustomerName"),
        "CustomerEmail": (estimate_parent or {}).get("Email"),
        "FinalFilePath": draft.get("FinalFilePath") if draft else None,
        "DraftStatus": draft.get("DraftStatus") if draft else None,
    }


def can_send_estimate_document_draft(draft_id: int) -> tuple[bool, str, dict[str, Any]]:
    ensure_estimate_document_draft_table()

    draft = get_estimate_document_draft(draft_id)
    if not draft:
        return False, "Draft was not found.", _build_context(None, None)

    estimate_id = draft.get("EstimateID")
    estimate_parent: dict[str, Any] | None = None
    if estimate_id is not None:
        try:
            estimate_data = get_detailed_estimate_data(int(estimate_id))
            estimate_parent = (estimate_data or {}).get("parent") or None
        except Exception:
            estimate_parent = None

    context = _build_context(draft, estimate_parent)
    if estimate_id is None:
        return False, "Draft is missing its estimate reference.", context

    draft_status = str(draft.get("DraftStatus") or "").strip()
    if draft_status == "Sent":
        return False, "Draft has already been sent.", context
    if draft_status == "Retired":
        return False, "Draft is retired.", context
    if draft_status != "Locked":
        return False, "Draft must be locked before sending.", context

    final_file_path = str(draft.get("FinalFilePath") or "").strip()
    if not final_file_path:
        return False, "Export the locked draft before sending.", context
    if not Path(final_file_path).exists():
        return False, "Exported file was not found.", context

    customer_email = str((estimate_parent or {}).get("Email") or "").strip()
    if not customer_email:
        return False, "Customer email is missing.", context

    return True, "Ready to send.", context
