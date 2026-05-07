from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neon_ai.database.workorders import create_work_order_draft_safe
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_work_order_draft_request,
    validate_work_order_draft_result,
)


@dataclass(frozen=True)
class WorkOrderDraftServiceResult:
    created_work_order_id: int | None
    used_existing_work_order_id: int | None
    work_order_status: str
    warnings: list[str]
    source_estimate_id: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_work_order_id": self.created_work_order_id,
            "used_existing_work_order_id": self.used_existing_work_order_id,
            "work_order_status": self.work_order_status,
            "warnings": list(self.warnings),
            "source_estimate_id": self.source_estimate_id,
        }


def create_work_order_draft_from_estimate(
    estimate_id: int,
    *,
    desired_status: str = "Draft",
    created_by: str = "automation_apply",
    source_tag: str = "work_order_draft_wrapper",
) -> WorkOrderDraftServiceResult:
    request_contract = validate_work_order_draft_request(
        {
            "estimate_id": estimate_id,
            "desired_status": desired_status,
            "created_by": created_by,
            "source_tag": source_tag,
        }
    )
    if not request_contract.valid:
        raise ValueError(format_validation_errors(request_contract))

    normalized_request = request_contract.normalized_json if isinstance(request_contract.normalized_json, dict) else {}
    draft_payload = create_work_order_draft_safe(
        estimate_id=int(normalized_request["estimate_id"]),
        desired_status=str(normalized_request.get("desired_status") or "Draft"),
        created_by=str(normalized_request.get("created_by") or "automation_apply"),
        source_tag=str(normalized_request.get("source_tag") or "work_order_draft_wrapper"),
    )
    work_order_row = draft_payload.get("work_order") or {}
    work_order_id = int(work_order_row["WorkOrderID"])
    draft_reused = bool(draft_payload.get("draft_reused"))
    result = WorkOrderDraftServiceResult(
        created_work_order_id=None if draft_reused else work_order_id,
        used_existing_work_order_id=work_order_id if draft_reused else None,
        work_order_status=str(work_order_row.get("JobStatus") or normalized_request.get("desired_status") or "Draft"),
        warnings=[str(item) for item in (draft_payload.get("warnings") or []) if str(item or "").strip()],
        source_estimate_id=int(normalized_request["estimate_id"]),
    )
    result_contract = validate_work_order_draft_result(result.as_dict())
    if not result_contract.valid:
        raise RuntimeError(format_validation_errors(result_contract))
    return result
