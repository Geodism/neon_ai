from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neon_ai.database.invoices import create_customer_invoice_draft_safe
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_customer_invoice_draft_request,
    validate_customer_invoice_draft_result,
)


@dataclass(frozen=True)
class CustomerInvoiceDraftServiceResult:
    created_customer_invoice_id: int | None
    used_existing_customer_invoice_id: int | None
    invoice_number: str
    status: str
    warnings: list[str]
    source_work_order_id: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_customer_invoice_id": self.created_customer_invoice_id,
            "used_existing_customer_invoice_id": self.used_existing_customer_invoice_id,
            "invoice_number": self.invoice_number,
            "status": self.status,
            "warnings": list(self.warnings),
            "source_work_order_id": self.source_work_order_id,
        }


def create_customer_invoice_draft_from_work_order(
    work_order_id: int,
    *,
    created_by: str = "automation_apply",
    source_tag: str = "customer_invoice_draft_wrapper",
) -> CustomerInvoiceDraftServiceResult:
    request_contract = validate_customer_invoice_draft_request(
        {
            "work_order_id": work_order_id,
            "created_by": created_by,
            "source_tag": source_tag,
        }
    )
    if not request_contract.valid:
        raise ValueError(format_validation_errors(request_contract))

    normalized_request = request_contract.normalized_json if isinstance(request_contract.normalized_json, dict) else {}
    draft_payload = create_customer_invoice_draft_safe(
        work_order_id=int(normalized_request["work_order_id"]),
        created_by=str(normalized_request.get("created_by") or "automation_apply"),
        source_tag=str(normalized_request.get("source_tag") or "customer_invoice_draft_wrapper"),
    )
    invoice_row = draft_payload.get("invoice") or {}
    invoice_id = int(invoice_row["CustomerInvoiceId"])
    draft_reused = bool(draft_payload.get("draft_reused"))
    result = CustomerInvoiceDraftServiceResult(
        created_customer_invoice_id=None if draft_reused else invoice_id,
        used_existing_customer_invoice_id=invoice_id if draft_reused else None,
        invoice_number=str(draft_payload.get("invoice_number") or invoice_id),
        status=str(invoice_row.get("InvoiceStatus") or "Draft"),
        warnings=[str(item) for item in (draft_payload.get("warnings") or []) if str(item or "").strip()],
        source_work_order_id=int(normalized_request["work_order_id"]),
    )
    result_contract = validate_customer_invoice_draft_result(result.as_dict())
    if not result_contract.valid:
        raise RuntimeError(format_validation_errors(result_contract))
    return result
