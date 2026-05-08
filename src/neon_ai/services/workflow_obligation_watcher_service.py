from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.automation_control_service import (
    AutomationRunRecord,
    create_automation_run,
    finish_automation_run,
    log_automation_event,
)
from neon_ai.services.automation_json_contracts import (
    validate_customer_invoice_payment_followup_due_observation,
    format_validation_errors,
    validate_customer_invoice_due_observation,
    validate_estimate_followup_due_observation,
    validate_vendor_invoice_reconciliation_due_proposal,
)
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    create_proposal,
    list_proposals,
)
from neon_ai.services.inbound_intake_service import InboundMessageRecord
from neon_ai.services.workflow_obligation_service import (
    WorkflowObligationRecord,
    create_obligation_if_missing,
    ensure_workflow_obligation_schema,
    list_workflow_obligations,
    mark_satisfied,
    record_obligation_check,
    STATUS_FAILED,
    STATUS_OVERDUE,
    STATUS_PROPOSAL_CREATED,
    STATUS_SNOOZED,
    STATUS_WAITING,
)
from neon_ai.services.workflow_obligation_route_attachment_service import (
    attach_selected_obligation_for_proposal_best_effort,
)


AUTOMATION_KEY = "workflow_obligation_watcher"

WORKFLOW_ESTIMATE = "estimate_followup"
WORKFLOW_BILLING = "customer_invoicing"
WORKFLOW_VENDOR_INVOICE = "vendor_invoice_payables"
WORKFLOW_CUSTOMER_INVOICE_AR = "customer_invoice_ar"

EXPECTED_ESTIMATE_REPLY = "estimate_customer_reply_or_followup_due"
EXPECTED_CUSTOMER_INVOICE = "customer_invoice_creation_due"
EXPECTED_VENDOR_INVOICE_RECONCILIATION = "vendor_invoice_reconciled_to_po_receipt"
EXPECTED_CUSTOMER_INVOICE_PAYMENT = "customer_invoice_payment_or_followup"

ACTION_ESTIMATE_DUE = "estimate_followup_due_observation"
ACTION_CUSTOMER_INVOICE_DUE = "customer_invoice_due_observation"
ACTION_VENDOR_INVOICE_RECONCILIATION_DUE = "vendor_invoice_reconciliation_due_observation"
ACTION_CUSTOMER_INVOICE_PAYMENT_DUE = "customer_invoice_payment_followup_due_observation"
SOURCE_VENDOR_INVOICE_INTAKE_ACTION = "vendor_invoice_intake_observation"
DEFAULT_CUSTOMER_INVOICE_FOLLOWUP_DAYS = 7

PROPOSAL_STATUSES_ACTIVE = {"Pending", "Approved"}
TERMINAL_OBLIGATION_STATUSES = {"satisfied", "dismissed", "cancelled"}

VENDOR_INVOICE_WORKFLOW_GUESSES = {"vendor_invoice_intake", "vendor_invoice_payables"}
VENDOR_INVOICE_SAFE_RECONCILED_STATUSES = {"readytopay", "ready_to_pay", "paid"}

ESTIMATE_RESPONSE_WORKFLOW_GUESSES = {
    "estimate_followup_customer_reply",
    "estimate_followup",
    "customer_reply",
}
ESTIMATE_SATISFYING_STATUSES = {
    "accepted",
    "rejected",
    "revised",
    "superseded",
    "converted",
    "won",
    "lost",
}
COMPLETED_WORK_ORDER_STATUSES = {
    "complete",
    "completed",
    "closed",
    "done",
    "substantially complete",
}


@dataclass(frozen=True)
class WorkflowObligationWatcherResult:
    run: AutomationRunRecord
    obligations_processed: int
    obligations_created: int
    obligations_satisfied: int
    proposals_created: int
    questions_created: int
    source_counts: dict[str, int]


@dataclass(frozen=True)
class WorkflowObligationSatisfactionResult:
    satisfied: bool
    satisfied_by_record_type: str | None
    satisfied_by_record_id: str | None
    reason: str
    confidence: float
    evidence: dict[str, Any]
    uncertainty_notes: list[str]


def run_workflow_obligation_watcher(
    *,
    estimate_ids: list[int] | None = None,
    work_order_ids: list[int] | None = None,
    customer_invoice_ids: list[int] | None = None,
    vendor_invoice_proposal_ids: list[int] | None = None,
    vendor_invoice_message_ids: list[int] | None = None,
    trigger_type: str = "manual_workflow_obligation_route",
) -> WorkflowObligationWatcherResult:
    ensure_workflow_obligation_schema()
    run = create_automation_run(
        AUTOMATION_KEY,
        status="Started",
        trigger_type=trigger_type,
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=AUTOMATION_KEY,
        event_type="route_started",
        summary="Started WorkflowObligation watcher.",
        target_type="WorkflowObligation",
        target_id=None,
        event_json={
            "estimate_ids": estimate_ids or [],
            "work_order_ids": work_order_ids or [],
            "customer_invoice_ids": customer_invoice_ids or [],
            "vendor_invoice_proposal_ids": vendor_invoice_proposal_ids or [],
            "vendor_invoice_message_ids": vendor_invoice_message_ids or [],
            "mode_used": "deterministic_read_only_checks",
        },
    )

    obligations_processed = 0
    obligations_created = 0
    obligations_satisfied = 0
    proposals_created = 0
    source_counts = {
        "estimate_followup_due": 0,
        "customer_invoice_due": 0,
        "customer_invoice_payment_due": 0,
        "vendor_invoice_reconciliation_due": 0,
    }

    try:
        estimate_sources = _discover_estimate_sources(estimate_ids=estimate_ids)
        work_order_sources = _discover_work_order_sources(work_order_ids=work_order_ids)
        customer_invoice_sources = _discover_customer_invoice_payment_sources(customer_invoice_ids=customer_invoice_ids)
        vendor_invoice_sources = _discover_vendor_invoice_reconciliation_sources(
            proposal_ids=vendor_invoice_proposal_ids,
            message_ids=vendor_invoice_message_ids,
        )
        source_counts["estimate_followup_due"] = len(estimate_sources)
        source_counts["customer_invoice_due"] = len(work_order_sources)
        source_counts["customer_invoice_payment_due"] = len(customer_invoice_sources)
        source_counts["vendor_invoice_reconciliation_due"] = len(vendor_invoice_sources)

        for source in estimate_sources:
            obligation, created_now = _ensure_estimate_obligation(source)
            obligations_processed += 1
            obligations_created += 1 if created_now else 0
            if obligation.status in TERMINAL_OBLIGATION_STATUSES:
                continue
            satisfaction = check_satisfaction(obligation)
            if satisfaction.satisfied:
                mark_satisfied(
                    obligation.obligation_id,
                    satisfied_by_record_type=satisfaction.satisfied_by_record_type,
                    satisfied_by_record_id=satisfaction.satisfied_by_record_id,
                    evidence_json=satisfaction.evidence,
                    resolution_notes=satisfaction.reason,
                )
                obligations_satisfied += 1
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="obligation_satisfied",
                    summary=f"Estimate obligation #{obligation.obligation_id} is satisfied.",
                    target_type="WorkflowObligation",
                    target_id=obligation.obligation_id,
                    event_json=satisfaction.evidence,
                )
                continue

            if _is_active_snooze(obligation):
                _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
                continue

            _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
            if _is_due(obligation.expected_by):
                proposal, created_now = create_overdue_proposal_if_missing(obligation, satisfaction=satisfaction)
                if proposal is not None:
                    proposals_created += 1 if created_now else 0
                    log_automation_event(
                        automation_run_id=run.automation_run_id,
                        automation_key=AUTOMATION_KEY,
                        event_type="overdue_proposal_created" if created_now else "overdue_proposal_reused",
                        summary=f"Estimate obligation #{obligation.obligation_id} is overdue.",
                        target_type="AutomationProposal",
                        target_id=proposal.automation_proposal_id,
                        event_json={
                            "obligation_id": obligation.obligation_id,
                            "action_type": proposal.action_type,
                            "created_now": created_now,
                            "expected_by": _isoformat(obligation.expected_by),
                        },
                    )

        for source in work_order_sources:
            obligation, created_now = _ensure_work_order_obligation(source)
            obligations_processed += 1
            obligations_created += 1 if created_now else 0
            if obligation.status in TERMINAL_OBLIGATION_STATUSES:
                continue
            satisfaction = check_satisfaction(obligation)
            if satisfaction.satisfied:
                mark_satisfied(
                    obligation.obligation_id,
                    satisfied_by_record_type=satisfaction.satisfied_by_record_type,
                    satisfied_by_record_id=satisfaction.satisfied_by_record_id,
                    evidence_json=satisfaction.evidence,
                    resolution_notes=satisfaction.reason,
                )
                obligations_satisfied += 1
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="obligation_satisfied",
                    summary=f"Customer invoice obligation #{obligation.obligation_id} is satisfied.",
                    target_type="WorkflowObligation",
                    target_id=obligation.obligation_id,
                    event_json=satisfaction.evidence,
                )
                continue

            if _is_active_snooze(obligation):
                _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
                continue

            _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
            if _is_due(obligation.expected_by):
                proposal, created_now = create_overdue_proposal_if_missing(obligation, satisfaction=satisfaction)
                if proposal is not None:
                    proposals_created += 1 if created_now else 0
                    log_automation_event(
                        automation_run_id=run.automation_run_id,
                        automation_key=AUTOMATION_KEY,
                        event_type="overdue_proposal_created" if created_now else "overdue_proposal_reused",
                        summary=f"Customer invoice obligation #{obligation.obligation_id} is overdue.",
                        target_type="AutomationProposal",
                        target_id=proposal.automation_proposal_id,
                        event_json={
                            "obligation_id": obligation.obligation_id,
                            "action_type": proposal.action_type,
                            "created_now": created_now,
                            "expected_by": _isoformat(obligation.expected_by),
                        },
                    )

        for source in customer_invoice_sources:
            obligation, created_now = _ensure_customer_invoice_payment_obligation(source)
            obligations_processed += 1
            obligations_created += 1 if created_now else 0
            if obligation.status in TERMINAL_OBLIGATION_STATUSES:
                continue
            satisfaction = check_satisfaction(obligation)
            if satisfaction.satisfied:
                mark_satisfied(
                    obligation.obligation_id,
                    satisfied_by_record_type=satisfaction.satisfied_by_record_type,
                    satisfied_by_record_id=satisfaction.satisfied_by_record_id,
                    evidence_json=satisfaction.evidence,
                    resolution_notes=satisfaction.reason,
                )
                obligations_satisfied += 1
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="obligation_satisfied",
                    summary=f"Customer invoice payment obligation #{obligation.obligation_id} is satisfied.",
                    target_type="WorkflowObligation",
                    target_id=obligation.obligation_id,
                    event_json=satisfaction.evidence,
                )
                continue

            if _is_active_snooze(obligation):
                _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
                continue

            _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
            if _is_due(obligation.expected_by):
                proposal, created_now = create_overdue_proposal_if_missing(obligation, satisfaction=satisfaction)
                if proposal is not None:
                    proposals_created += 1 if created_now else 0
                    log_automation_event(
                        automation_run_id=run.automation_run_id,
                        automation_key=AUTOMATION_KEY,
                        event_type="overdue_proposal_created" if created_now else "overdue_proposal_reused",
                        summary=f"Customer invoice payment obligation #{obligation.obligation_id} is overdue.",
                        target_type="AutomationProposal",
                        target_id=proposal.automation_proposal_id,
                        event_json={
                            "obligation_id": obligation.obligation_id,
                            "action_type": proposal.action_type,
                            "created_now": created_now,
                            "expected_by": _isoformat(obligation.expected_by),
                        },
                    )

        for source in vendor_invoice_sources:
            obligation, created_now = _ensure_vendor_invoice_reconciliation_obligation(source)
            obligations_processed += 1
            obligations_created += 1 if created_now else 0
            if obligation.status in TERMINAL_OBLIGATION_STATUSES:
                continue
            satisfaction = check_satisfaction(obligation)
            if satisfaction.satisfied:
                mark_satisfied(
                    obligation.obligation_id,
                    satisfied_by_record_type=satisfaction.satisfied_by_record_type,
                    satisfied_by_record_id=satisfaction.satisfied_by_record_id,
                    evidence_json=satisfaction.evidence,
                    resolution_notes=satisfaction.reason,
                )
                obligations_satisfied += 1
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="obligation_satisfied",
                    summary=f"Vendor invoice reconciliation obligation #{obligation.obligation_id} is satisfied.",
                    target_type="WorkflowObligation",
                    target_id=obligation.obligation_id,
                    event_json=satisfaction.evidence,
                )
                continue

            if _is_active_snooze(obligation):
                _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
                continue

            _touch_unsatisfied_obligation(obligation, satisfaction=satisfaction)
            if _is_due(obligation.expected_by):
                proposal, created_now = create_overdue_proposal_if_missing(obligation, satisfaction=satisfaction)
                if proposal is not None:
                    proposals_created += 1 if created_now else 0
                    log_automation_event(
                        automation_run_id=run.automation_run_id,
                        automation_key=AUTOMATION_KEY,
                        event_type="overdue_proposal_created" if created_now else "overdue_proposal_reused",
                        summary=f"Vendor invoice reconciliation obligation #{obligation.obligation_id} is overdue.",
                        target_type="AutomationProposal",
                        target_id=proposal.automation_proposal_id,
                        event_json={
                            "obligation_id": obligation.obligation_id,
                            "action_type": proposal.action_type,
                            "created_now": created_now,
                            "expected_by": _isoformat(obligation.expected_by),
                        },
                    )

        finished_run = finish_automation_run(
            run.automation_run_id,
            status="Completed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=proposals_created,
            questions_created=0,
            error_message=None,
        )
        if finished_run is None:
            raise RuntimeError("WorkflowObligation watcher run could not be finalized.")
        return WorkflowObligationWatcherResult(
            run=finished_run,
            obligations_processed=obligations_processed,
            obligations_created=obligations_created,
            obligations_satisfied=obligations_satisfied,
            proposals_created=proposals_created,
            questions_created=0,
            source_counts=source_counts,
        )
    except Exception as exc:
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="route_failed",
            summary="WorkflowObligation watcher failed.",
            target_type="WorkflowObligation",
            target_id=None,
            event_json={"error": str(exc)},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=proposals_created,
            questions_created=0,
            error_message=str(exc),
        )
        raise


def check_satisfaction(obligation: WorkflowObligationRecord) -> WorkflowObligationSatisfactionResult:
    if obligation.expected_event_type == EXPECTED_ESTIMATE_REPLY:
        return _check_estimate_satisfaction(obligation)
    if obligation.expected_event_type == EXPECTED_CUSTOMER_INVOICE:
        return _check_work_order_invoice_satisfaction(obligation)
    if obligation.expected_event_type == EXPECTED_CUSTOMER_INVOICE_PAYMENT:
        return _check_customer_invoice_payment_satisfaction(obligation)
    if obligation.expected_event_type == EXPECTED_VENDOR_INVOICE_RECONCILIATION:
        return _check_vendor_invoice_reconciliation_satisfaction(obligation)
    return WorkflowObligationSatisfactionResult(
        satisfied=False,
        satisfied_by_record_type=None,
        satisfied_by_record_id=None,
        reason="No satisfaction rule exists yet for this obligation type.",
        confidence=0.0,
        evidence={
            "obligation_id": obligation.obligation_id,
            "expected_event_type": obligation.expected_event_type,
            "limitation": "Unsupported obligation type.",
        },
        uncertainty_notes=["No satisfaction rule exists yet for this obligation type."],
    )


def create_overdue_proposal_if_missing(
    obligation: WorkflowObligationRecord,
    *,
    satisfaction: WorkflowObligationSatisfactionResult,
) -> tuple[AutomationProposalRecord | None, bool]:
    existing = _find_existing_active_overdue_proposal(obligation)
    if existing is not None:
        record_obligation_check(
            obligation.obligation_id,
            status=STATUS_PROPOSAL_CREATED,
            last_checked_at=datetime.now(),
            last_proposal_id=existing.automation_proposal_id,
            escalation_level=obligation.escalation_level,
            evidence_json=satisfaction.evidence,
            resolution_notes=satisfaction.reason,
            changed_by="WorkflowObligation watcher",
            change_source="watcher",
            reason="Reused existing overdue proposal for obligation.",
        )
        return existing, False

    proposal_payload = _build_overdue_proposal_payload(obligation, satisfaction=satisfaction)
    if obligation.expected_event_type == EXPECTED_ESTIMATE_REPLY:
        payload_contract = validate_estimate_followup_due_observation(proposal_payload)
    elif obligation.expected_event_type == EXPECTED_CUSTOMER_INVOICE:
        payload_contract = validate_customer_invoice_due_observation(proposal_payload)
    elif obligation.expected_event_type == EXPECTED_CUSTOMER_INVOICE_PAYMENT:
        payload_contract = validate_customer_invoice_payment_followup_due_observation(proposal_payload)
    else:
        payload_contract = validate_vendor_invoice_reconciliation_due_proposal(proposal_payload)
    if not payload_contract.valid:
        log_automation_event(
            automation_run_id=None,
            automation_key=AUTOMATION_KEY,
            event_type="proposal_validation_failed",
            summary=f"WorkflowObligation #{obligation.obligation_id} overdue proposal payload failed Jsondream validation.",
            target_type="WorkflowObligation",
            target_id=obligation.obligation_id,
            event_json={
                "errors": payload_contract.errors,
                "warnings": payload_contract.warnings,
                "contract_name": payload_contract.contract_name,
            },
        )
        record_obligation_check(
            obligation.obligation_id,
            status=STATUS_FAILED,
            last_checked_at=datetime.now(),
            evidence_json=satisfaction.evidence,
            resolution_notes=format_validation_errors(payload_contract),
            changed_by="WorkflowObligation watcher",
            change_source="watcher",
            reason="Overdue proposal payload failed validation.",
        )
        return None, False

    evidence_json = {
        "obligation_id": obligation.obligation_id,
        "source_record_type": obligation.source_record_type,
        "source_record_id": obligation.source_record_id,
        "workflow_type": obligation.workflow_type,
        "expected_event_type": obligation.expected_event_type,
        "expected_by": _isoformat(obligation.expected_by),
        "days_overdue": _days_overdue(obligation.expected_by),
        "satisfaction_reason": satisfaction.reason,
        "satisfaction_evidence": satisfaction.evidence,
        "uncertainty_notes": satisfaction.uncertainty_notes,
    }
    proposal = create_proposal(
        automation_key=AUTOMATION_KEY,
        workflow=str(proposal_payload.get("workflow") or obligation.workflow_type),
        action_type=str(proposal_payload.get("action_type") or "workflow_obligation_due_observation"),
        target_type=str(proposal_payload.get("target_type") or obligation.source_record_type),
        target_id=proposal_payload.get("target_id") or obligation.source_record_id,
        summary=_proposal_summary(proposal_payload),
        proposed_change_json=payload_contract.normalized_json,
        evidence_json=evidence_json,
        confidence=proposal_payload.get("confidence"),
        risk_level=str(proposal_payload.get("risk_level") or "Medium"),
        requires_approval=True,
        can_auto_apply_level_2=False,
        blocked_reason=None,
        status="Pending",
    )
    attach_selected_obligation_for_proposal_best_effort(
        proposal,
        automation_key=AUTOMATION_KEY,
        automation_run_id=None,
    )
    record_obligation_check(
        obligation.obligation_id,
        status=STATUS_PROPOSAL_CREATED,
        last_checked_at=datetime.now(),
        last_proposal_id=proposal.automation_proposal_id,
        escalation_level=int(obligation.escalation_level or 0) + 1,
        evidence_json=satisfaction.evidence,
        resolution_notes=satisfaction.reason,
        changed_by="WorkflowObligation watcher",
        change_source="watcher",
        reason="Created overdue proposal for unsatisfied obligation.",
    )
    return proposal, True


def _discover_estimate_sources(*, estimate_ids: list[int] | None) -> list[dict[str, Any]]:
    clauses = ['UPPER(TRIM(COALESCE(e."Status", \'\'))) = \'SENT\'']
    params: list[Any] = []
    if estimate_ids:
        clauses.append('e."EstimateID" = ANY(%s)')
        params.append([int(item) for item in estimate_ids])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT
                e."EstimateID",
                e."Status",
                e."SubmitDate",
                e."CreatedDate",
                e."SiteID",
                COALESCE(e."Description", '') AS "Description",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(s."StreetNumber", '') AS "StreetNumber",
                COALESCE(s."StreetName", '') AS "StreetName",
                COALESCE(s."City", '') AS "City",
                c."CustomerID",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email"
            FROM "Estimate" e
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(e."SubmitDate", e."CreatedDate") ASC, e."EstimateID" ASC
            ''',
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _discover_work_order_sources(*, work_order_ids: list[int] | None) -> list[dict[str, Any]]:
    clauses = ['(COALESCE(w."IsClosed", FALSE) = TRUE OR LOWER(TRIM(COALESCE(w."JobStatus", \'\'))) = ANY(%s))']
    params: list[Any] = [list(COMPLETED_WORK_ORDER_STATUSES)]
    if work_order_ids:
        clauses.append('w."WorkOrderID" = ANY(%s)')
        params.append([int(item) for item in work_order_ids])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT
                w."WorkOrderID",
                w."JobStatus",
                COALESCE(w."IsClosed", FALSE) AS "IsClosed",
                w."CreatedDate",
                w."SiteID",
                w."SourceEstimateID",
                COALESCE(w."Description", '') AS "Description",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName"
            FROM "WorkOrder" w
            LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE {' AND '.join(clauses)}
            ORDER BY w."WorkOrderID" ASC
            ''',
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _discover_customer_invoice_payment_sources(*, customer_invoice_ids: list[int] | None) -> list[dict[str, Any]]:
    clauses = [
        'COALESCE(i."InvoiceStatus", \'\') != \'Retired\'',
        '('
        'COALESCE(i."InvoiceStatus", \'\') = ANY(%s) '
        'OR i."CustomerInvoiceSentAt" IS NOT NULL '
        'OR COALESCE(i."CustomerInvoiceDocPath", \'\') <> \'\' '
        'OR COALESCE(ld."DraftStatus", \'\') = \'Sent\' '
        'OR COALESCE(ld."FinalFilePath", \'\') <> \'\''
        ')',
    ]
    params: list[Any] = [["Exported", "Sent", "Paid"]]
    if customer_invoice_ids:
        clauses.append('i."CustomerInvoiceId" = ANY(%s)')
        params.append([int(item) for item in customer_invoice_ids])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            WITH latest_draft AS (
                SELECT DISTINCT ON (d."CustomerInvoiceId")
                    d."CustomerInvoiceId",
                    d."CustomerInvoiceDocumentDraftID",
                    COALESCE(d."DraftStatus", '') AS "DraftStatus",
                    d."FinalFilePath",
                    d."SentAt" AS "DocumentDraftSentAt"
                FROM public."CustomerInvoiceDocumentDraft" d
                WHERE d."IsActive" = 1
                  AND COALESCE(d."DraftStatus", '') != 'Retired'
                ORDER BY d."CustomerInvoiceId", d."VersionNumber" DESC, d."CustomerInvoiceDocumentDraftID" DESC
            )
            SELECT
                i."CustomerInvoiceId",
                i."WorkOrderID",
                COALESCE(i."InvoiceStatus", '') AS "InvoiceStatus",
                i."CustomerInvoiceAmount",
                i."CustomerInvoiceDate",
                i."CustomerInvoiceDocPath",
                i."CustomerInvoiceSentAt",
                i."InvDatePaid",
                COALESCE(wo."Description", '') AS "WorkOrderDescription",
                wo."SiteID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                ld."CustomerInvoiceDocumentDraftID",
                COALESCE(ld."DraftStatus", '') AS "DocumentDraftStatus",
                ld."FinalFilePath",
                ld."DocumentDraftSentAt"
            FROM public."Invoice" i
            LEFT JOIN public."WorkOrder" wo ON i."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN public."Site" s ON wo."SiteID" = s."SiteID"
            LEFT JOIN public."Customer" c ON s."CustomerID" = c."CustomerID"
            LEFT JOIN latest_draft ld ON ld."CustomerInvoiceId" = i."CustomerInvoiceId"
            WHERE {' AND '.join(clauses)}
            ORDER BY i."CustomerInvoiceId" ASC
            ''',
            tuple(params),
        )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    sources: list[dict[str, Any]] = []
    for row in rows:
        sent_or_exported_at = (
            _stringify_date_like(row.get("CustomerInvoiceSentAt"))
            or _stringify_date_like(row.get("DocumentDraftSentAt"))
            or _stringify_date_like(row.get("CustomerInvoiceDate"))
        )
        invoice_status = str(row.get("InvoiceStatus") or "").strip()
        draft_status = str(row.get("DocumentDraftStatus") or "").strip()
        has_export = bool(str(row.get("CustomerInvoiceDocPath") or "").strip() or str(row.get("FinalFilePath") or "").strip())
        has_sent_marker = bool(row.get("CustomerInvoiceSentAt") or row.get("DocumentDraftSentAt") or invoice_status in {"Sent", "Paid"} or draft_status == "Sent")
        uncertainty_notes: list[str] = []
        if not str(row.get("CustomerInvoiceSentAt") or "").strip() and not str(row.get("DocumentDraftSentAt") or "").strip():
            uncertainty_notes.append(
                "No dedicated customer invoice due date field is available; follow-up timing may use sent/exported or invoice date fallback."
            )
        sources.append(
            {
                "source_record_type": "CustomerInvoice",
                "source_record_id": int(row["CustomerInvoiceId"]),
                "workflow_type": WORKFLOW_CUSTOMER_INVOICE_AR,
                "expected_event_type": EXPECTED_CUSTOMER_INVOICE_PAYMENT,
                "created_at": row.get("CustomerInvoiceSentAt") or row.get("DocumentDraftSentAt") or row.get("CustomerInvoiceDate"),
                "customer_invoice_id": int(row["CustomerInvoiceId"]),
                "invoice_number": str(row.get("CustomerInvoiceId") or ""),
                "work_order_id": _coerce_int(row.get("WorkOrderID")),
                "customer_name": row.get("CustomerName"),
                "site_name": row.get("SiteName"),
                "invoice_status": invoice_status,
                "invoice_date": _stringify_date_like(row.get("CustomerInvoiceDate")),
                "sent_or_exported_date": sent_or_exported_at,
                "due_date": None,
                "total_amount": _coerce_float(row.get("CustomerInvoiceAmount")),
                "customer_invoice_doc_path": row.get("CustomerInvoiceDocPath"),
                "final_file_path": row.get("FinalFilePath"),
                "payment_received_date": _stringify_date_like(row.get("InvDatePaid")),
                "document_draft_status": draft_status,
                "document_draft_id": _coerce_int(row.get("CustomerInvoiceDocumentDraftID")),
                "has_export": has_export,
                "has_sent_marker": has_sent_marker,
                "uncertainty_notes": uncertainty_notes,
                "evidence_json": {
                    "customer_invoice_id": int(row["CustomerInvoiceId"]),
                    "customer_name": row.get("CustomerName"),
                    "invoice_number": str(row.get("CustomerInvoiceId") or ""),
                    "invoice_status": invoice_status,
                    "invoice_date": _stringify_date_like(row.get("CustomerInvoiceDate")),
                    "sent_or_exported_date": sent_or_exported_at,
                    "document_draft_status": draft_status,
                    "customer_invoice_doc_path": row.get("CustomerInvoiceDocPath"),
                    "final_file_path": row.get("FinalFilePath"),
                    "work_order_id": row.get("WorkOrderID"),
                    "site_name": row.get("SiteName"),
                },
            }
        )
    return sources


def _discover_vendor_invoice_reconciliation_sources(
    *,
    proposal_ids: list[int] | None,
    message_ids: list[int] | None,
) -> list[dict[str, Any]]:
    proposal_sources = _discover_vendor_invoice_reconciliation_proposal_sources(proposal_ids=proposal_ids)
    referenced_message_ids = {
        int(source.get("inbound_message_id") or 0)
        for source in proposal_sources
        if int(source.get("inbound_message_id") or 0) > 0
    }
    inbound_sources = _discover_vendor_invoice_reconciliation_message_sources(
        message_ids=message_ids,
        skip_message_ids=referenced_message_ids,
    )
    return proposal_sources + inbound_sources


def _discover_vendor_invoice_reconciliation_proposal_sources(
    *,
    proposal_ids: list[int] | None,
) -> list[dict[str, Any]]:
    clauses = ['ap."ActionType" = %s', 'ap."Status" = ANY(%s)']
    params: list[Any] = [SOURCE_VENDOR_INVOICE_INTAKE_ACTION, ["Pending", "Approved"]]
    if proposal_ids:
        clauses.append('ap."AutomationProposalID" = ANY(%s)')
        params.append([int(item) for item in proposal_ids])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT *
            FROM public."AutomationProposal" ap
            WHERE {' AND '.join(clauses)}
            ORDER BY ap."CreatedAt" ASC, ap."AutomationProposalID" ASC
            ''',
            tuple(params),
        )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    sources: list[dict[str, Any]] = []
    for row in rows:
        proposed_change = row.get("ProposedChangeJson") if isinstance(row.get("ProposedChangeJson"), dict) else {}
        evidence = row.get("EvidenceJson") if isinstance(row.get("EvidenceJson"), dict) else {}
        source = {
            "source_record_type": "AutomationProposal",
            "source_record_id": int(row["AutomationProposalID"]),
            "workflow_type": WORKFLOW_VENDOR_INVOICE,
            "expected_event_type": EXPECTED_VENDOR_INVOICE_RECONCILIATION,
            "created_at": row.get("CreatedAt"),
            "source_proposal_id": int(row["AutomationProposalID"]),
            "inbound_message_id": _coerce_int(evidence.get("inbound_message_id")),
            "sender": evidence.get("sender"),
            "sender_name": evidence.get("sender_name"),
            "subject": evidence.get("subject"),
            "vendor_name": proposed_change.get("vendor_name") or evidence.get("vendor_name"),
            "invoice_number": proposed_change.get("invoice_number") or _nested_get(evidence, "extracted_payload", "invoice_number"),
            "invoice_date": proposed_change.get("invoice_date") or _nested_get(evidence, "extracted_payload", "invoice_date"),
            "due_date": proposed_change.get("due_date") or _nested_get(evidence, "extracted_payload", "due_date"),
            "total_amount": proposed_change.get("total_amount") if proposed_change.get("total_amount") is not None else _nested_get(evidence, "extracted_payload", "total_amount"),
            "po_number": proposed_change.get("po_number") or _nested_get(evidence, "extracted_payload", "po_number"),
            "packing_slip_number": proposed_change.get("packing_slip_number") or _nested_get(evidence, "extracted_payload", "packing_slip_number"),
            "work_order_reference": proposed_change.get("work_order_reference") or _nested_get(evidence, "extracted_payload", "work_order_reference"),
            "match_outcome": proposed_change.get("match_outcome"),
            "line_candidates": proposed_change.get("line_candidates") if isinstance(proposed_change.get("line_candidates"), list) else [],
            "confidence": proposed_change.get("confidence"),
            "uncertainty_notes": proposed_change.get("uncertainty_notes") if isinstance(proposed_change.get("uncertainty_notes"), list) else [],
            "evidence_json": evidence,
            "candidate_po_list": evidence.get("candidate_po_list") if isinstance(evidence.get("candidate_po_list"), list) else [],
            "candidate_vendor_list": evidence.get("candidate_vendor_list") if isinstance(evidence.get("candidate_vendor_list"), list) else [],
            "duplicate_check_result": evidence.get("duplicate_check_result") if isinstance(evidence.get("duplicate_check_result"), dict) else {"supported": False, "checked": False, "duplicate_found": False},
            "classification_route": evidence.get("classification_route"),
        }
        sources.append(source)
    return sources


def _discover_vendor_invoice_reconciliation_message_sources(
    *,
    message_ids: list[int] | None,
    skip_message_ids: set[int],
) -> list[dict[str, Any]]:
    clauses = [
        "(LOWER(COALESCE(im.\"WorkflowGuess\", '')) = ANY(%s) OR LOWER(COALESCE(im.\"IntentGuess\", '')) = ANY(%s))",
        "COALESCE(im.\"Status\", '') <> 'Ignored'",
    ]
    params: list[Any] = [list(VENDOR_INVOICE_WORKFLOW_GUESSES), list(VENDOR_INVOICE_WORKFLOW_GUESSES)]
    if message_ids:
        clauses.append('im."InboundMessageID" = ANY(%s)')
        params.append([int(item) for item in message_ids])
    if skip_message_ids:
        clauses.append('im."InboundMessageID" <> ALL(%s)')
        params.append([int(item) for item in skip_message_ids])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f'''
            SELECT im.*
            FROM public."InboundMessage" im
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(im."ReceivedAt", im."ImportedAt", NOW()) ASC, im."InboundMessageID" ASC
            ''',
            tuple(params),
        )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    sources: list[dict[str, Any]] = []
    for row in rows:
        classification = row.get("ClassificationJson") if isinstance(row.get("ClassificationJson"), dict) else {}
        body_excerpt = row.get("BodyExcerpt") or row.get("BodyText") or ""
        sources.append(
            {
                "source_record_type": "InboundMessage",
                "source_record_id": int(row["InboundMessageID"]),
                "workflow_type": WORKFLOW_VENDOR_INVOICE,
                "expected_event_type": EXPECTED_VENDOR_INVOICE_RECONCILIATION,
                "created_at": row.get("ImportedAt") or row.get("ReceivedAt"),
                "source_proposal_id": None,
                "inbound_message_id": int(row["InboundMessageID"]),
                "sender": row.get("Sender"),
                "sender_name": row.get("SenderName"),
                "subject": row.get("Subject"),
                "vendor_name": classification.get("vendor_name"),
                "invoice_number": classification.get("invoice_number"),
                "invoice_date": classification.get("invoice_date"),
                "due_date": classification.get("due_date"),
                "total_amount": classification.get("total_amount"),
                "po_number": classification.get("po_number"),
                "packing_slip_number": classification.get("packing_slip_number"),
                "work_order_reference": classification.get("work_order_reference"),
                "match_outcome": "READY_FOR_OPERATOR_RECONCILIATION_REVIEW",
                "line_candidates": classification.get("line_candidates") if isinstance(classification.get("line_candidates"), list) else [],
                "confidence": classification.get("confidence"),
                "uncertainty_notes": ["No vendor invoice intake proposal existed yet; obligation is sourced directly from the inbound intake record."],
                "evidence_json": {
                    "inbound_message_id": int(row["InboundMessageID"]),
                    "sender": row.get("Sender"),
                    "sender_name": row.get("SenderName"),
                    "subject": row.get("Subject"),
                    "source_excerpt": str(body_excerpt)[:600],
                    "classification_route": row.get("WorkflowGuess") or row.get("IntentGuess"),
                    "missing_evidence_list": ["vendor_invoice_intake_proposal_missing"],
                },
                "candidate_po_list": [],
                "candidate_vendor_list": [],
                "duplicate_check_result": {"supported": False, "checked": False, "duplicate_found": False},
                "classification_route": row.get("WorkflowGuess") or row.get("IntentGuess"),
            }
        )
    return sources


def _ensure_estimate_obligation(source: dict[str, Any]) -> tuple[WorkflowObligationRecord, bool]:
    expected_by = _estimate_expected_by(source)
    existing = list_workflow_obligations(
        limit=5,
        source_record_type="Estimate",
        source_record_ids=[source["EstimateID"]],
        expected_event_type=EXPECTED_ESTIMATE_REPLY,
    )
    created_now = not existing
    obligation = create_obligation_if_missing(
        source_record_type="Estimate",
        source_record_id=source["EstimateID"],
        workflow_type=WORKFLOW_ESTIMATE,
        expected_event_type=EXPECTED_ESTIMATE_REPLY,
        expected_by=expected_by,
        severity="Medium",
        status=STATUS_WAITING,
        owner_role="Sales",
        owner_user_id=None,
        escalation_level=0,
        escalation_policy_code="ESTIMATE_SENT_REPLY_DUE",
        title=f"Estimate #{source['EstimateID']} follow-up/customer reply due",
        description=(
            f"Estimate #{source['EstimateID']} for {source.get('CustomerName') or 'Unknown Customer'} "
            f"at {source.get('SiteName') or 'Unknown Site'} is awaiting a customer reply or follow-up."
        ),
        evidence_json={
            "source_record_type": "Estimate",
            "source_record_id": source["EstimateID"],
            "estimate_status": source.get("Status"),
            "submit_date": _isoformat(source.get("SubmitDate")),
            "created_date": _isoformat(source.get("CreatedDate")),
            "customer_id": source.get("CustomerID"),
            "customer_name": source.get("CustomerName"),
            "site_id": source.get("SiteID"),
            "site_name": source.get("SiteName"),
            "workflow_limitation": "Due date currently uses a 3-calendar-day window when no business-day helper is wired.",
        },
    )
    return obligation, created_now


def _ensure_work_order_obligation(source: dict[str, Any]) -> tuple[WorkflowObligationRecord, bool]:
    expected_by = _work_order_expected_by(source)
    existing = list_workflow_obligations(
        limit=5,
        source_record_type="WorkOrder",
        source_record_ids=[source["WorkOrderID"]],
        expected_event_type=EXPECTED_CUSTOMER_INVOICE,
    )
    created_now = not existing
    obligation = create_obligation_if_missing(
        source_record_type="WorkOrder",
        source_record_id=source["WorkOrderID"],
        workflow_type=WORKFLOW_BILLING,
        expected_event_type=EXPECTED_CUSTOMER_INVOICE,
        expected_by=expected_by,
        severity="Medium",
        status=STATUS_WAITING,
        owner_role="Billing",
        owner_user_id=None,
        escalation_level=0,
        escalation_policy_code="WORK_COMPLETE_INVOICE_DUE",
        title=f"Work Order #{source['WorkOrderID']} customer invoice due",
        description=(
            f"Work Order #{source['WorkOrderID']} for {source.get('CustomerName') or 'Unknown Customer'} "
            f"at {source.get('SiteName') or 'Unknown Site'} is awaiting customer invoice creation."
        ),
        evidence_json={
            "source_record_type": "WorkOrder",
            "source_record_id": source["WorkOrderID"],
            "job_status": source.get("JobStatus"),
            "is_closed": bool(source.get("IsClosed")),
            "created_date": _stringify_date_like(source.get("CreatedDate")),
            "customer_name": source.get("CustomerName"),
            "site_name": source.get("SiteName"),
            "workflow_limitation": "WorkOrder completion currently uses JobStatus/IsClosed plus CreatedDate as the best available completion proxy.",
        },
    )
    return obligation, created_now


def _ensure_customer_invoice_payment_obligation(source: dict[str, Any]) -> tuple[WorkflowObligationRecord, bool]:
    expected_by = _customer_invoice_payment_expected_by(source)
    source_record_id = str(source.get("customer_invoice_id") or source.get("source_record_id") or "")
    existing = list_workflow_obligations(
        limit=5,
        source_record_type="CustomerInvoice",
        source_record_ids=[source_record_id],
        expected_event_type=EXPECTED_CUSTOMER_INVOICE_PAYMENT,
    )
    created_now = not existing
    invoice_number = str(source.get("invoice_number") or source_record_id or "unknown invoice")
    customer_name = str(source.get("customer_name") or "Unknown Customer").strip() or "Unknown Customer"
    obligation = create_obligation_if_missing(
        source_record_type="CustomerInvoice",
        source_record_id=source_record_id,
        workflow_type=WORKFLOW_CUSTOMER_INVOICE_AR,
        expected_event_type=EXPECTED_CUSTOMER_INVOICE_PAYMENT,
        expected_by=expected_by,
        severity=_customer_invoice_payment_obligation_severity(source),
        status=STATUS_WAITING,
        owner_role="Accounts Receivable",
        owner_user_id=None,
        escalation_level=0,
        escalation_policy_code="CUSTOMER_INVOICE_PAYMENT_FOLLOWUP_DUE",
        title=f"Customer invoice {invoice_number} payment follow-up due",
        description=(
            f"Customer invoice {invoice_number} for {customer_name} needs payment/follow-up review "
            "because no safe payment evidence has been confirmed yet."
        ),
        evidence_json=_build_customer_invoice_payment_evidence(source),
        resolution_notes=None,
        notes=None,
        changed_by="WorkflowObligation watcher",
        change_source="watcher",
        reason="Created customer invoice payment-followup obligation if missing.",
    )
    return obligation, created_now


def _ensure_vendor_invoice_reconciliation_obligation(source: dict[str, Any]) -> tuple[WorkflowObligationRecord, bool]:
    expected_by = _vendor_invoice_expected_by(source)
    source_record_type = str(source.get("source_record_type") or "AutomationProposal")
    source_record_id = str(source.get("source_record_id") or "")
    existing = list_workflow_obligations(
        limit=5,
        source_record_type=source_record_type,
        source_record_ids=[source_record_id],
        expected_event_type=EXPECTED_VENDOR_INVOICE_RECONCILIATION,
    )
    created_now = not existing
    vendor_name = str(source.get("vendor_name") or "Unknown Vendor").strip() or "Unknown Vendor"
    invoice_number = str(source.get("invoice_number") or "unknown invoice").strip() or "unknown invoice"
    obligation = create_obligation_if_missing(
        source_record_type=source_record_type,
        source_record_id=source_record_id,
        workflow_type=WORKFLOW_VENDOR_INVOICE,
        expected_event_type=EXPECTED_VENDOR_INVOICE_RECONCILIATION,
        expected_by=expected_by,
        severity=_vendor_invoice_obligation_severity(source),
        status=STATUS_WAITING,
        owner_role="Accounting",
        owner_user_id=None,
        escalation_level=0,
        escalation_policy_code="VENDOR_INVOICE_RECONCILIATION_DUE",
        title=f"Vendor invoice {invoice_number} reconciliation due",
        description=(
            f"Vendor invoice {invoice_number} from {vendor_name} needs reconciliation review "
            f"against PO and receipt context before any payable truth is created."
        ),
        evidence_json=_build_vendor_invoice_obligation_evidence(source),
        resolution_notes=None,
        notes=None,
        changed_by="WorkflowObligation watcher",
        change_source="watcher",
        reason="Created vendor invoice reconciliation obligation if missing.",
    )
    return obligation, created_now


def _check_estimate_satisfaction(obligation: WorkflowObligationRecord) -> WorkflowObligationSatisfactionResult:
    source = _fetch_estimate_source(int(obligation.source_record_id))
    if source is None:
        return WorkflowObligationSatisfactionResult(
            satisfied=False,
            satisfied_by_record_type=None,
            satisfied_by_record_id=None,
            reason="Estimate source record was not found during obligation check.",
            confidence=0.0,
            evidence={"estimate_id": obligation.source_record_id, "status": "missing_source"},
            uncertainty_notes=["Estimate source record was not found."],
        )

    current_status = str(source.get("Status") or "").strip().lower()
    if current_status and current_status != "sent" and current_status in ESTIMATE_SATISFYING_STATUSES:
        return WorkflowObligationSatisfactionResult(
            satisfied=True,
            satisfied_by_record_type="Estimate",
            satisfied_by_record_id=source.get("EstimateID"),
            reason=f"Estimate status changed to {source.get('Status')}.",
            confidence=0.98,
            evidence={
                "estimate_id": source.get("EstimateID"),
                "estimate_status": source.get("Status"),
                "satisfaction_type": "estimate_status_change",
            },
            uncertainty_notes=[],
        )

    inbound_reply = _find_estimate_reply_message(source)
    if inbound_reply is not None:
        return WorkflowObligationSatisfactionResult(
            satisfied=True,
            satisfied_by_record_type="InboundMessage",
            satisfied_by_record_id=inbound_reply.get("InboundMessageID"),
            reason="A customer reply linked to the estimate follow-up route was found after the estimate was sent.",
            confidence=0.92,
            evidence={
                "estimate_id": source.get("EstimateID"),
                "estimate_status": source.get("Status"),
                "satisfaction_type": "customer_reply_detected",
                "inbound_message_id": inbound_reply.get("InboundMessageID"),
                "subject": inbound_reply.get("Subject"),
                "received_at": _isoformat(inbound_reply.get("ReceivedAt") or inbound_reply.get("ImportedAt")),
            },
            uncertainty_notes=[],
        )

    note_followup = _find_estimate_followup_note(source)
    if note_followup is not None:
        return WorkflowObligationSatisfactionResult(
            satisfied=True,
            satisfied_by_record_type="Note",
            satisfied_by_record_id=note_followup.get("NoteID"),
            reason="An estimate follow-up note exists after the estimate was sent.",
            confidence=0.75,
            evidence={
                "estimate_id": source.get("EstimateID"),
                "satisfaction_type": "follow_up_note_detected",
                "note_id": note_followup.get("NoteID"),
                "note_text": note_followup.get("NoteText"),
                "timestamp": _isoformat(note_followup.get("Timestamp")),
            },
            uncertainty_notes=[
                "No outbound follow-up activity check is wired yet; note-based follow-up is the current available proxy.",
            ],
        )

    return WorkflowObligationSatisfactionResult(
        satisfied=False,
        satisfied_by_record_type=None,
        satisfied_by_record_id=None,
        reason="No customer reply, follow-up note, or satisfying estimate status change was found.",
        confidence=0.88,
        evidence={
            "estimate_id": source.get("EstimateID"),
            "estimate_status": source.get("Status"),
            "submit_date": _isoformat(source.get("SubmitDate")),
            "created_date": _isoformat(source.get("CreatedDate")),
            "customer_id": source.get("CustomerID"),
            "customer_name": source.get("CustomerName"),
            "customer_email": source.get("Email"),
            "site_id": source.get("SiteID"),
            "site_name": source.get("SiteName"),
            "checked_signals": [
                "estimate_status_change",
                "estimate_followup_inbound_reply",
                "estimate_followup_note",
            ],
        },
        uncertainty_notes=[
            "No dedicated outbound follow-up activity link is checked yet in this first obligation slice.",
        ],
    )


def _check_work_order_invoice_satisfaction(obligation: WorkflowObligationRecord) -> WorkflowObligationSatisfactionResult:
    source = _fetch_work_order_source(int(obligation.source_record_id))
    if source is None:
        return WorkflowObligationSatisfactionResult(
            satisfied=False,
            satisfied_by_record_type=None,
            satisfied_by_record_id=None,
            reason="WorkOrder source record was not found during obligation check.",
            confidence=0.0,
            evidence={"work_order_id": obligation.source_record_id, "status": "missing_source"},
            uncertainty_notes=["WorkOrder source record was not found."],
        )

    invoice_match = _find_work_order_invoice(source)
    if invoice_match is not None:
        return WorkflowObligationSatisfactionResult(
            satisfied=True,
            satisfied_by_record_type="CustomerInvoice",
            satisfied_by_record_id=invoice_match.get("CustomerInvoiceId"),
            reason="A customer invoice or invoice draft exists for the WorkOrder.",
            confidence=0.97,
            evidence={
                "work_order_id": source.get("WorkOrderID"),
                "satisfaction_type": "invoice_exists",
                "customer_invoice_id": invoice_match.get("CustomerInvoiceId"),
                "invoice_status": invoice_match.get("InvoiceStatus"),
                "customer_invoice_date": _stringify_date_like(invoice_match.get("CustomerInvoiceDate")),
                "draft_status": invoice_match.get("DraftStatus"),
                "draft_id": invoice_match.get("CustomerInvoiceDocumentDraftID"),
            },
            uncertainty_notes=[],
        )

    return WorkflowObligationSatisfactionResult(
        satisfied=False,
        satisfied_by_record_type=None,
        satisfied_by_record_id=None,
        reason="No customer invoice or invoice document draft exists for the completed WorkOrder.",
        confidence=0.9,
        evidence={
            "work_order_id": source.get("WorkOrderID"),
            "job_status": source.get("JobStatus"),
            "is_closed": bool(source.get("IsClosed")),
            "created_date": _stringify_date_like(source.get("CreatedDate")),
            "customer_name": source.get("CustomerName"),
            "site_name": source.get("SiteName"),
            "source_estimate_id": source.get("SourceEstimateID"),
            "checked_signals": [
                "invoice_row_exists",
                "invoice_document_draft_exists",
            ],
        },
        uncertainty_notes=[
            "WorkOrder completion currently uses JobStatus/IsClosed plus CreatedDate as a proxy because no completion timestamp exists.",
        ],
    )


def _check_customer_invoice_payment_satisfaction(obligation: WorkflowObligationRecord) -> WorkflowObligationSatisfactionResult:
    source = _fetch_customer_invoice_payment_source(obligation)
    if source is None:
        return WorkflowObligationSatisfactionResult(
            satisfied=False,
            satisfied_by_record_type=None,
            satisfied_by_record_id=None,
            reason="Customer invoice source record was not found during payment-followup obligation check.",
            confidence=0.0,
            evidence={
                "source_record_type": obligation.source_record_type,
                "source_record_id": obligation.source_record_id,
                "status": "missing_source",
            },
            uncertainty_notes=["Customer invoice source record was not found."],
        )

    invoice_status = str(source.get("invoice_status") or "").strip()
    payment_received_date = _stringify_date_like(source.get("payment_received_date"))
    if invoice_status.lower() == "paid" or payment_received_date:
        return WorkflowObligationSatisfactionResult(
            satisfied=True,
            satisfied_by_record_type="CustomerInvoice",
            satisfied_by_record_id=source.get("customer_invoice_id"),
            reason=f"Customer invoice {source.get('invoice_number') or obligation.source_record_id} has safe paid evidence.",
            confidence=0.97,
            evidence={
                "customer_invoice_id": source.get("customer_invoice_id"),
                "invoice_number": source.get("invoice_number"),
                "invoice_status": invoice_status,
                "payment_received_date": payment_received_date,
                "satisfaction_type": "customer_invoice_paid_status",
            },
            uncertainty_notes=[],
        )

    evidence = _build_customer_invoice_payment_evidence(source)
    return WorkflowObligationSatisfactionResult(
        satisfied=False,
        satisfied_by_record_type=None,
        satisfied_by_record_id=None,
        reason="No safe payment or completed A/R follow-up evidence was found for the sent/exported customer invoice.",
        confidence=min(0.92, _coerce_float(source.get("confidence")) or 0.81),
        evidence=evidence,
        uncertainty_notes=list(evidence.get("uncertainty_notes") or []),
    )


def _check_vendor_invoice_reconciliation_satisfaction(obligation: WorkflowObligationRecord) -> WorkflowObligationSatisfactionResult:
    source = _fetch_vendor_invoice_reconciliation_source(obligation)
    if source is None:
        return WorkflowObligationSatisfactionResult(
            satisfied=False,
            satisfied_by_record_type=None,
            satisfied_by_record_id=None,
            reason="Vendor invoice reconciliation source record was not found during obligation check.",
            confidence=0.0,
            evidence={
                "source_record_type": obligation.source_record_type,
                "source_record_id": obligation.source_record_id,
                "status": "missing_source",
            },
            uncertainty_notes=["Vendor invoice reconciliation source record was not found."],
        )

    matched_vendor_invoice = _find_existing_vendor_invoice_for_reconciliation(source)
    if matched_vendor_invoice is not None:
        invoice_status = str(matched_vendor_invoice.get("VendorInvoiceStatus") or "").strip()
        if invoice_status.replace(" ", "").replace("-", "").replace("_", "").lower() in VENDOR_INVOICE_SAFE_RECONCILED_STATUSES:
            return WorkflowObligationSatisfactionResult(
                satisfied=True,
                satisfied_by_record_type="VendorInvoice",
                satisfied_by_record_id=matched_vendor_invoice.get("VendorInvoiceID"),
                reason=f"Existing VendorInvoice #{matched_vendor_invoice.get('VendorInvoiceID')} has safe reconciliation/payables status {invoice_status}.",
                confidence=0.96,
                evidence={
                    "vendor_invoice_id": matched_vendor_invoice.get("VendorInvoiceID"),
                    "vendor_invoice_status": invoice_status,
                    "purchase_order_id": matched_vendor_invoice.get("PurchaseOrderID"),
                    "invoice_number": matched_vendor_invoice.get("VendorInvoiceNumber"),
                    "invoice_amount": matched_vendor_invoice.get("VendorInvoiceAmount"),
                    "satisfaction_type": "existing_vendor_invoice_status",
                },
                uncertainty_notes=[],
            )

    customer_billing = _get_customer_billing_status(source)
    flags, missing_evidence, uncertainty_notes = _vendor_invoice_reconciliation_flags(source, customer_billing=customer_billing)
    evidence = _build_vendor_invoice_obligation_evidence(
        source,
        customer_billing=customer_billing,
        reconciliation_flags=flags,
        missing_evidence=missing_evidence,
        matched_vendor_invoice=matched_vendor_invoice,
    )
    return WorkflowObligationSatisfactionResult(
        satisfied=False,
        satisfied_by_record_type=None,
        satisfied_by_record_id=None,
        reason="Vendor invoice reconciliation has not been confirmed by safe payable/review evidence yet.",
        confidence=min(0.9, _coerce_float(source.get("confidence")) or 0.72),
        evidence=evidence,
        uncertainty_notes=uncertainty_notes,
    )


def _find_existing_active_overdue_proposal(obligation: WorkflowObligationRecord) -> AutomationProposalRecord | None:
    for status in PROPOSAL_STATUSES_ACTIVE:
        for proposal in list_proposals(limit=300, status=status, automation_key=AUTOMATION_KEY):
            if proposal.action_type not in {
                ACTION_ESTIMATE_DUE,
                ACTION_CUSTOMER_INVOICE_DUE,
                ACTION_CUSTOMER_INVOICE_PAYMENT_DUE,
                ACTION_VENDOR_INVOICE_RECONCILIATION_DUE,
            }:
                continue
            if str(proposal.target_type or "") != obligation.source_record_type:
                continue
            if str(proposal.target_id or "") != obligation.source_record_id:
                continue
            proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
            if str(proposed_change.get("obligation_id") or "") != str(obligation.obligation_id):
                continue
            return proposal
    return None


def _build_overdue_proposal_payload(
    obligation: WorkflowObligationRecord,
    *,
    satisfaction: WorkflowObligationSatisfactionResult,
) -> dict[str, Any]:
    days_overdue = _days_overdue(obligation.expected_by)
    evidence = satisfaction.evidence if isinstance(satisfaction.evidence, dict) else {}
    common = {
        "proposal_type": "workflow_obligation_due",
        "workflow": obligation.workflow_type,
        "target_type": obligation.source_record_type,
        "target_id": _coerce_int(obligation.source_record_id) or obligation.source_record_id,
        "source_record_type": obligation.source_record_type,
        "source_record_id": _coerce_int(obligation.source_record_id) or obligation.source_record_id,
        "expected_event_type": obligation.expected_event_type,
        "expected_by": _isoformat(obligation.expected_by),
        "days_overdue": days_overdue,
        "recommended_action": "",
        "confidence": satisfaction.confidence,
        "uncertainty_notes": list(satisfaction.uncertainty_notes or []),
        "evidence": evidence,
        "obligation_id": obligation.obligation_id,
    }
    if obligation.expected_event_type == EXPECTED_ESTIMATE_REPLY:
        common.update(
            {
                "proposal_type": "estimate_followup_due_observation",
                "workflow": WORKFLOW_ESTIMATE,
                "action_type": ACTION_ESTIMATE_DUE,
                "estimate_id": _coerce_int(obligation.source_record_id),
                "estimate_status": evidence.get("estimate_status"),
                "sent_date": evidence.get("submit_date") or evidence.get("created_date"),
                "customer_id": evidence.get("customer_id"),
                "customer_name": evidence.get("customer_name"),
                "site_id": evidence.get("site_id"),
                "site_name": evidence.get("site_name"),
                "recommended_action": "draft_followup_email",
            }
        )
        return common
    if obligation.expected_event_type == EXPECTED_CUSTOMER_INVOICE_PAYMENT:
        cash_flow_flags, payment_status, followup_status, uncertainty_notes = _customer_invoice_payment_cash_flow_flags(evidence)
        common.update(
            {
                "proposal_type": "customer_invoice_payment_followup_due",
                "workflow": WORKFLOW_CUSTOMER_INVOICE_AR,
                "action_type": ACTION_CUSTOMER_INVOICE_PAYMENT_DUE,
                "customer_name": evidence.get("customer_name"),
                "invoice_number": evidence.get("invoice_number"),
                "invoice_date": evidence.get("invoice_date"),
                "sent_or_exported_date": evidence.get("sent_or_exported_date"),
                "due_date": evidence.get("due_date") or common.get("expected_by"),
                "total_amount": evidence.get("total_amount"),
                "payment_status": payment_status,
                "followup_status": followup_status,
                "cash_flow_flags": cash_flow_flags,
                "risk_level": _customer_invoice_payment_risk_level(cash_flow_flags, total_amount=evidence.get("total_amount"), days_overdue=days_overdue),
                "recommended_action": "review_customer_invoice_payment_followup",
                "requires_approval": True,
                "can_auto_apply_level_2": False,
            }
        )
        common["uncertainty_notes"] = list(dict.fromkeys((common.get("uncertainty_notes") or []) + uncertainty_notes))
        common["evidence"] = _build_customer_invoice_payment_evidence(
            evidence,
            cash_flow_flags=cash_flow_flags,
            payment_status=payment_status,
            followup_status=followup_status,
            uncertainty_notes=common["uncertainty_notes"],
        )
        return common
    if obligation.expected_event_type == EXPECTED_VENDOR_INVOICE_RECONCILIATION:
        customer_billing = _get_customer_billing_status(evidence)
        flags, missing_evidence, uncertainty_notes = _vendor_invoice_reconciliation_flags(evidence, customer_billing=customer_billing)
        common.update(
            {
                "proposal_type": "vendor_invoice_reconciliation_due",
                "workflow": WORKFLOW_VENDOR_INVOICE,
                "action_type": ACTION_VENDOR_INVOICE_RECONCILIATION_DUE,
                "vendor_name": evidence.get("vendor_name"),
                "invoice_number": evidence.get("invoice_number"),
                "invoice_date": evidence.get("invoice_date"),
                "due_date": evidence.get("due_date"),
                "total_amount": evidence.get("total_amount"),
                "po_number": evidence.get("po_number"),
                "packing_slip_number": evidence.get("packing_slip_number"),
                "match_outcome": evidence.get("match_outcome") or "READY_FOR_OPERATOR_RECONCILIATION_REVIEW",
                "reconciliation_flags": flags,
                "customer_billing_status": customer_billing.get("status"),
                "risk_level": _vendor_invoice_risk_level(flags),
                "recommended_action": "review_vendor_invoice_reconciliation",
                "requires_approval": True,
                "can_auto_apply_level_2": False,
            }
        )
        common["uncertainty_notes"] = list(dict.fromkeys((common.get("uncertainty_notes") or []) + uncertainty_notes))
        common["evidence"] = _build_vendor_invoice_obligation_evidence(
            evidence,
            customer_billing=customer_billing,
            reconciliation_flags=flags,
            missing_evidence=missing_evidence,
            uncertainty_notes=common["uncertainty_notes"],
            matched_vendor_invoice=_find_existing_vendor_invoice_for_reconciliation(evidence),
        )
        return common

    common.update(
        {
            "proposal_type": "customer_invoice_due_observation",
            "workflow": WORKFLOW_BILLING,
            "action_type": ACTION_CUSTOMER_INVOICE_DUE,
            "work_order_id": _coerce_int(obligation.source_record_id),
            "job_status": evidence.get("job_status"),
            "is_closed": evidence.get("is_closed"),
            "customer_name": evidence.get("customer_name"),
            "site_name": evidence.get("site_name"),
            "source_estimate_id": evidence.get("source_estimate_id"),
            "recommended_action": "create_customer_invoice_draft",
        }
    )
    return common


def _proposal_summary(payload: dict[str, Any]) -> str:
    action_type = str(payload.get("action_type") or "")
    if action_type == ACTION_ESTIMATE_DUE:
        return (
            f"Estimate #{payload.get('estimate_id') or payload.get('source_record_id')} is overdue for "
            f"customer reply/follow-up by {payload.get('days_overdue')} days."
        )
    if action_type == ACTION_CUSTOMER_INVOICE_PAYMENT_DUE:
        invoice_number = payload.get("invoice_number") or payload.get("source_record_id") or "unknown invoice"
        customer_name = payload.get("customer_name") or "customer"
        return (
            f"Customer invoice {invoice_number} for {customer_name} is overdue for payment/follow-up review by "
            f"{payload.get('days_overdue')} days."
        )
    if action_type == ACTION_VENDOR_INVOICE_RECONCILIATION_DUE:
        invoice_number = payload.get("invoice_number") or "unknown invoice"
        vendor_name = payload.get("vendor_name") or "vendor"
        return (
            f"Vendor invoice {invoice_number} from {vendor_name} is overdue for reconciliation review by "
            f"{payload.get('days_overdue')} days."
        )
    return (
        f"Work Order #{payload.get('work_order_id') or payload.get('source_record_id')} is overdue for "
        f"customer invoice creation by {payload.get('days_overdue')} days."
    )


def _touch_unsatisfied_obligation(
    obligation: WorkflowObligationRecord,
    *,
    satisfaction: WorkflowObligationSatisfactionResult,
) -> None:
    next_status = obligation.status
    if _is_active_snooze(obligation):
        next_status = STATUS_SNOOZED
    elif _is_due(obligation.expected_by):
        next_status = STATUS_OVERDUE if obligation.last_proposal_id is None else STATUS_PROPOSAL_CREATED
    else:
        next_status = STATUS_WAITING
    record_obligation_check(
        obligation.obligation_id,
        status=next_status,
        last_checked_at=datetime.now(),
        evidence_json=satisfaction.evidence,
        resolution_notes=satisfaction.reason,
        changed_by="WorkflowObligation watcher",
        change_source="watcher",
        reason="Recorded unsatisfied obligation check.",
    )


def _customer_invoice_payment_expected_by(source: dict[str, Any]) -> datetime | None:
    due_date = _coerce_datetime(source.get("due_date"))
    if due_date is not None:
        return due_date
    sent_or_exported = _coerce_datetime(source.get("sent_or_exported_date"))
    if sent_or_exported is not None:
        return sent_or_exported + timedelta(days=DEFAULT_CUSTOMER_INVOICE_FOLLOWUP_DAYS)
    invoice_date = _coerce_datetime(source.get("invoice_date"))
    if invoice_date is not None:
        return invoice_date + timedelta(days=DEFAULT_CUSTOMER_INVOICE_FOLLOWUP_DAYS)
    created_at = _coerce_datetime(source.get("created_at"))
    if created_at is not None:
        return created_at + timedelta(days=DEFAULT_CUSTOMER_INVOICE_FOLLOWUP_DAYS)
    return None


def _customer_invoice_payment_obligation_severity(source: dict[str, Any]) -> str:
    invoice_status = str(source.get("invoice_status") or "").strip().lower()
    total_amount = _coerce_float(source.get("total_amount")) or 0.0
    expected_by = _customer_invoice_payment_expected_by(source)
    if invoice_status == "paid":
        return "Low"
    if total_amount >= 5000:
        return "High"
    if expected_by is not None and expected_by <= datetime.now(expected_by.tzinfo or timezone.utc):
        return "High"
    return "Medium"


def _vendor_invoice_expected_by(source: dict[str, Any]) -> datetime | None:
    due_date = _coerce_datetime(source.get("due_date"))
    if due_date is not None:
        return due_date
    invoice_date = _coerce_datetime(source.get("invoice_date"))
    if invoice_date is not None:
        return invoice_date + timedelta(days=3)
    created_at = _coerce_datetime(source.get("created_at"))
    if created_at is not None:
        return created_at + timedelta(days=3)
    return None


def _vendor_invoice_obligation_severity(source: dict[str, Any]) -> str:
    duplicate = bool(_nested_get(source.get("duplicate_check_result"), "duplicate_found"))
    if duplicate:
        return "High"
    due_date = _coerce_datetime(source.get("due_date"))
    if due_date is not None and due_date <= datetime.now(due_date.tzinfo or timezone.utc):
        return "High"
    return "Medium"


def _fetch_customer_invoice_payment_source(obligation: WorkflowObligationRecord) -> dict[str, Any] | None:
    if obligation.source_record_type != "CustomerInvoice":
        return None
    invoice_id = _coerce_int(obligation.source_record_id)
    if invoice_id is None:
        return None
    rows = _discover_customer_invoice_payment_sources(customer_invoice_ids=[invoice_id])
    return rows[0] if rows else None


def _fetch_vendor_invoice_reconciliation_source(obligation: WorkflowObligationRecord) -> dict[str, Any] | None:
    if obligation.source_record_type == "AutomationProposal":
        rows = _discover_vendor_invoice_reconciliation_proposal_sources(
            proposal_ids=[int(obligation.source_record_id)]
        )
        return rows[0] if rows else None
    if obligation.source_record_type == "InboundMessage":
        rows = _discover_vendor_invoice_reconciliation_message_sources(
            message_ids=[int(obligation.source_record_id)],
            skip_message_ids=set(),
        )
        return rows[0] if rows else None
    return None


def _customer_invoice_payment_cash_flow_flags(
    source: dict[str, Any],
) -> tuple[list[str], str, str, list[str]]:
    flags: list[str] = []
    uncertainty_notes: list[str] = list(source.get("uncertainty_notes") or [])
    invoice_status = str(source.get("invoice_status") or "").strip()
    payment_status = "paid" if invoice_status.lower() == "paid" or source.get("payment_received_date") else "no_payment_evidence_found"
    followup_status = "no_followup_evidence_found"
    if payment_status != "paid":
        flags.append("NO_PAYMENT_EVIDENCE_FOUND")
    if _is_due(_customer_invoice_payment_expected_by(source)):
        flags.append("CUSTOMER_PAYMENT_OVERDUE")
    if not source.get("due_date"):
        note = "No invoice due date field exists; payment follow-up uses a conservative sent/exported or invoice-date fallback."
        if note not in uncertainty_notes:
            uncertainty_notes.append(note)
    return list(dict.fromkeys(flags)), payment_status, followup_status, list(dict.fromkeys(uncertainty_notes))


def _find_existing_vendor_invoice_for_reconciliation(source: dict[str, Any]) -> dict[str, Any] | None:
    po_number = str(source.get("po_number") or "").strip()
    invoice_number = str(source.get("invoice_number") or "").strip()
    po_id = _coerce_int(source.get("target_id")) if str(source.get("target_type") or "") == "PurchaseOrder" else _coerce_int(po_number)
    if po_id is None or not invoice_number:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT *
            FROM public."VendorInvoice"
            WHERE "PurchaseOrderID" = %s
              AND LOWER(TRIM(COALESCE("VendorInvoiceNumber", ''))) = LOWER(TRIM(COALESCE(%s, '')))
            ORDER BY "VendorInvoiceID" DESC
            LIMIT 1
            ''',
            (int(po_id), invoice_number),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _get_customer_billing_status(source: dict[str, Any]) -> dict[str, Any]:
    po_id = _coerce_int(source.get("target_id")) if str(source.get("target_type") or "") == "PurchaseOrder" else _coerce_int(source.get("po_number"))
    if po_id is None:
        return {
            "status": "unknown",
            "customer_invoice_id": None,
            "customer_invoice_date": None,
            "site_name": None,
            "customer_name": None,
            "uncertainty_note": "No safe customer billing relationship found.",
        }
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                po."PurchaseOrderID",
                po."WorkOrderID",
                wo."SourceEstimateID",
                COALESCE(ci."CustomerInvoiceId", NULL) AS "CustomerInvoiceId",
                COALESCE(ci."InvoiceStatus", '') AS "InvoiceStatus",
                ci."CustomerInvoiceDate",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(s."SiteName", '') AS "SiteName"
            FROM "PurchaseOrder" po
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN LATERAL (
                SELECT i."CustomerInvoiceId", i."InvoiceStatus", i."CustomerInvoiceDate"
                FROM "Invoice" i
                WHERE i."WorkOrderID" = po."WorkOrderID"
                ORDER BY i."CustomerInvoiceId" DESC
                LIMIT 1
            ) ci ON TRUE
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE po."PurchaseOrderID" = %s
            LIMIT 1
            ''',
            (int(po_id),),
        )
        row = cur.fetchone()
        if not row:
            return {
                "status": "unknown",
                "customer_invoice_id": None,
                "customer_invoice_date": None,
                "site_name": None,
                "customer_name": None,
                "uncertainty_note": "No safe customer billing relationship found.",
            }
        invoice_status = str(row.get("InvoiceStatus") or "").strip()
        if row.get("CustomerInvoiceId"):
            status = invoice_status or "invoice_found"
            uncertainty = None
        else:
            status = "no_customer_invoice_found"
            uncertainty = "No safe customer billing relationship found." if not row.get("WorkOrderID") else None
        return {
            "status": status,
            "customer_invoice_id": row.get("CustomerInvoiceId"),
            "customer_invoice_date": _stringify_date_like(row.get("CustomerInvoiceDate")),
            "site_name": row.get("SiteName"),
            "customer_name": row.get("CustomerName"),
            "uncertainty_note": uncertainty,
        }
    except Exception:
        return {
            "status": "unknown",
            "customer_invoice_id": None,
            "customer_invoice_date": None,
            "site_name": None,
            "customer_name": None,
            "uncertainty_note": "No safe customer billing relationship found.",
        }
    finally:
        conn.close()


def _vendor_invoice_reconciliation_flags(
    source: dict[str, Any],
    *,
    customer_billing: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    flags: list[str] = []
    missing_evidence: list[str] = []
    uncertainty_notes: list[str] = list(source.get("uncertainty_notes") or [])

    match_outcome = str(source.get("match_outcome") or "").strip() or "READY_FOR_OPERATOR_RECONCILIATION_REVIEW"
    duplicate_result = source.get("duplicate_check_result") if isinstance(source.get("duplicate_check_result"), dict) else {}
    if duplicate_result.get("duplicate_found"):
        flags.append("DUPLICATE_INVOICE_POSSIBLE")
    if match_outcome == "MATCHED_PO_AND_RECEIPT_CONTEXT":
        pass
    elif match_outcome == "MATCHED_PO_ONLY":
        flags.append("MATCHED_PO_BUT_NO_RECEIPT")
        flags.append("RECEIPT_NOT_FOUND")
        missing_evidence.append("receipt_or_packing_slip_evidence")
    elif match_outcome == "NON_PO_EXPENSE_CANDIDATE":
        flags.append("NON_PO_EXPENSE_REVIEW")
    elif match_outcome == "AMBIGUOUS_PO_MATCH":
        flags.append("AMBIGUOUS_PO_OR_VENDOR")
    elif match_outcome == "AMBIGUOUS_VENDOR_MATCH":
        flags.append("AMBIGUOUS_PO_OR_VENDOR")
    elif match_outcome == "MISSING_INVOICE_NUMBER":
        flags.append("MISSING_REQUIRED_FIELDS")
        missing_evidence.append("invoice_number")
    elif match_outcome == "MISSING_TOTAL":
        flags.append("MISSING_REQUIRED_FIELDS")
        missing_evidence.append("total_amount")
    elif match_outcome == "NO_SAFE_MATCH":
        flags.append("READY_FOR_OPERATOR_RECONCILIATION_REVIEW")
        missing_evidence.append("safe_po_or_vendor_match")
    else:
        flags.append(match_outcome or "READY_FOR_OPERATOR_RECONCILIATION_REVIEW")

    packing_slip_number = str(source.get("packing_slip_number") or "").strip()
    if packing_slip_number and "MATCHED_PO_AND_RECEIPT_CONTEXT" not in flags and match_outcome != "MATCHED_PO_AND_RECEIPT_CONTEXT":
        flags.append("PACKING_SLIP_NOT_FOUND")
        missing_evidence.append("matching_receipt_context")

    if customer_billing.get("status") in {"unknown", "no_customer_invoice_found"}:
        flags.append("CUSTOMER_BILLING_NOT_CONFIRMED")
        uncertainty = customer_billing.get("uncertainty_note") or "No safe customer billing relationship found."
        if uncertainty not in uncertainty_notes:
            uncertainty_notes.append(uncertainty)

    due_date = _coerce_datetime(source.get("due_date"))
    if due_date is not None and customer_billing.get("status") in {"unknown", "no_customer_invoice_found"}:
        if due_date <= datetime.now(due_date.tzinfo or timezone.utc) + timedelta(days=2):
            flags.append("VENDOR_DUE_BEFORE_CUSTOMER_BILLING")

    return list(dict.fromkeys(flags)), list(dict.fromkeys(missing_evidence)), list(dict.fromkeys(note for note in uncertainty_notes if str(note or "").strip()))


def _build_vendor_invoice_obligation_evidence(
    source: dict[str, Any],
    *,
    customer_billing: dict[str, Any] | None = None,
    reconciliation_flags: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    uncertainty_notes: list[str] | None = None,
    matched_vendor_invoice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    customer_billing = customer_billing or {"status": "unknown", "uncertainty_note": "No safe customer billing relationship found."}
    duplicate_result = source.get("duplicate_check_result") if isinstance(source.get("duplicate_check_result"), dict) else {"supported": False, "checked": False, "duplicate_found": False}
    candidate_po_list = source.get("candidate_po_list") if isinstance(source.get("candidate_po_list"), list) else []
    candidate_vendor_list = source.get("candidate_vendor_list") if isinstance(source.get("candidate_vendor_list"), list) else []
    evidence_json = source.get("evidence_json") if isinstance(source.get("evidence_json"), dict) else {}
    inbound_message_id = _coerce_int(source.get("inbound_message_id")) or _coerce_int(evidence_json.get("inbound_message_id"))
    combined_uncertainty_notes = list(
        dict.fromkeys(
            [
                note
                for note in (
                    list(source.get("uncertainty_notes") or [])
                    + list(uncertainty_notes or [])
                    + ([customer_billing.get("uncertainty_note")] if str(customer_billing.get("uncertainty_note") or "").strip() else [])
                )
                if str(note or "").strip()
            ]
        )
    )
    return {
        "source_record_type": source.get("source_record_type"),
        "source_record_id": source.get("source_record_id"),
        "original_vendor_invoice_intake_proposal_id": source.get("source_proposal_id"),
        "inbound_message_id": inbound_message_id,
        "sender": source.get("sender") or evidence_json.get("sender"),
        "sender_name": source.get("sender_name") or evidence_json.get("sender_name"),
        "subject": source.get("subject") or evidence_json.get("subject"),
        "invoice_number": source.get("invoice_number"),
        "invoice_date": source.get("invoice_date"),
        "due_date": source.get("due_date"),
        "total_amount": source.get("total_amount"),
        "vendor_name": source.get("vendor_name"),
        "po_number": source.get("po_number"),
        "packing_slip_number": source.get("packing_slip_number"),
        "match_outcome": source.get("match_outcome") or "READY_FOR_OPERATOR_RECONCILIATION_REVIEW",
        "matched_po_candidates": candidate_po_list,
        "matched_vendor_candidates": candidate_vendor_list,
        "matched_receipt_evidence": {
            "packing_slip_number": source.get("packing_slip_number"),
            "match_outcome": source.get("match_outcome"),
        },
        "duplicate_check_result": duplicate_result,
        "customer_billing_evidence": customer_billing,
        "matched_vendor_invoice": {
            "VendorInvoiceID": matched_vendor_invoice.get("VendorInvoiceID"),
            "VendorInvoiceStatus": matched_vendor_invoice.get("VendorInvoiceStatus"),
        } if isinstance(matched_vendor_invoice, dict) else None,
        "missing_evidence_list": list(missing_evidence or []),
        "reconciliation_flags": list(reconciliation_flags or []),
        "uncertainty_notes": combined_uncertainty_notes,
        "classification_route": source.get("classification_route") or evidence_json.get("classification_route"),
        "source_excerpt": evidence_json.get("source_excerpt"),
    }


def _build_customer_invoice_payment_evidence(
    source: dict[str, Any],
    *,
    cash_flow_flags: list[str] | None = None,
    payment_status: str | None = None,
    followup_status: str | None = None,
    uncertainty_notes: list[str] | None = None,
) -> dict[str, Any]:
    evidence_json = source.get("evidence_json") if isinstance(source.get("evidence_json"), dict) else {}
    due_date_uncertainty = None
    if not source.get("due_date"):
        due_date_uncertainty = (
            "No invoice due date field exists; payment follow-up uses a conservative sent/exported or invoice-date fallback."
        )
    combined_uncertainty_notes = list(
        dict.fromkeys(
            [
                note
                for note in (
                    list(source.get("uncertainty_notes") or [])
                    + list(uncertainty_notes or [])
                    + ([due_date_uncertainty] if due_date_uncertainty else [])
                )
                if str(note or "").strip()
            ]
        )
    )
    return {
        "source_record_type": source.get("source_record_type") or "CustomerInvoice",
        "source_record_id": source.get("source_record_id") or source.get("customer_invoice_id"),
        "customer_invoice_id": source.get("customer_invoice_id"),
        "customer_name": source.get("customer_name"),
        "site_name": source.get("site_name"),
        "work_order_id": source.get("work_order_id"),
        "invoice_number": source.get("invoice_number"),
        "invoice_status": source.get("invoice_status"),
        "invoice_date": source.get("invoice_date"),
        "sent_or_exported_date": source.get("sent_or_exported_date"),
        "due_date": source.get("due_date") or _isoformat(_customer_invoice_payment_expected_by(source)),
        "total_amount": source.get("total_amount"),
        "payment_received_date": source.get("payment_received_date"),
        "payment_status": payment_status or ("paid" if source.get("payment_received_date") else "no_payment_evidence_found"),
        "followup_status": followup_status or "no_followup_evidence_found",
        "payment_evidence_checked": [
            "invoice_status_paid",
            "payment_received_date",
        ],
        "followup_evidence_checked": [
            "no_safe_followup_activity_link_available",
        ],
        "document_draft_status": source.get("document_draft_status"),
        "document_draft_id": source.get("document_draft_id"),
        "customer_invoice_doc_path": source.get("customer_invoice_doc_path"),
        "final_file_path": source.get("final_file_path"),
        "has_export": bool(source.get("has_export")),
        "has_sent_marker": bool(source.get("has_sent_marker")),
        "missing_evidence_list": [] if (payment_status == "paid") else ["payment_evidence", "followup_evidence"],
        "cash_flow_flags": list(cash_flow_flags or []),
        "uncertainty_notes": combined_uncertainty_notes,
        "source_snippets": list(evidence_json.get("source_snippets") or []),
        "source_notes": evidence_json.get("source_notes"),
    }


def _vendor_invoice_risk_level(flags: list[str]) -> str:
    severe = {"DUPLICATE_INVOICE_POSSIBLE", "INVOICE_EXCEEDS_PO", "INVOICE_EXCEEDS_RECEIVED_QTY", "VENDOR_DUE_BEFORE_CUSTOMER_BILLING"}
    return "High" if any(flag in severe for flag in flags) else "Medium"


def _customer_invoice_payment_risk_level(
    flags: list[str],
    *,
    total_amount: Any,
    days_overdue: int,
) -> str:
    amount = _coerce_float(total_amount) or 0.0
    if "CUSTOMER_PAYMENT_OVERDUE" in flags and (days_overdue >= 14 or amount >= 5000):
        return "High"
    return "Medium"


def _estimate_expected_by(source: dict[str, Any]) -> datetime | None:
    base = _coerce_datetime(source.get("SubmitDate")) or _coerce_datetime(source.get("CreatedDate"))
    if base is None:
        return None
    return base + timedelta(days=3)


def _work_order_expected_by(source: dict[str, Any]) -> datetime | None:
    base = _coerce_datetime(source.get("CreatedDate"))
    if base is None:
        return None
    return base + timedelta(days=1)


def _fetch_estimate_source(estimate_id: int) -> dict[str, Any] | None:
    rows = _discover_estimate_sources(estimate_ids=[estimate_id])
    return rows[0] if rows else None


def _fetch_work_order_source(work_order_id: int) -> dict[str, Any] | None:
    rows = _discover_work_order_sources(work_order_ids=[work_order_id])
    return rows[0] if rows else None


def _find_estimate_reply_message(source: dict[str, Any]) -> dict[str, Any] | None:
    estimate_id = int(source.get("EstimateID") or 0)
    customer_email = str(source.get("Email") or "").strip().lower()
    site_name = str(source.get("SiteName") or "").strip().lower()
    submit_date = _coerce_datetime(source.get("SubmitDate")) or _coerce_datetime(source.get("CreatedDate"))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                "InboundMessageID",
                "Sender",
                "Subject",
                "BodyText",
                "BodyExcerpt",
                "ReceivedAt",
                "ImportedAt",
                "WorkflowGuess",
                "IntentGuess"
            FROM public."InboundMessage"
            WHERE COALESCE(LOWER("Sender"), '') = %s
              AND COALESCE("ReceivedAt", "ImportedAt") >= %s
              AND (
                    LOWER(COALESCE("WorkflowGuess", '')) = ANY(%s)
                 OR LOWER(COALESCE("IntentGuess", '')) = ANY(%s)
              )
            ORDER BY COALESCE("ReceivedAt", "ImportedAt") DESC, "InboundMessageID" DESC
            LIMIT 20
            """,
            (
                customer_email,
                submit_date or datetime(2000, 1, 1),
                list(ESTIMATE_RESPONSE_WORKFLOW_GUESSES),
                list(ESTIMATE_RESPONSE_WORKFLOW_GUESSES),
            ),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    estimate_token = f"estimate #{estimate_id}".lower()
    for row in rows:
        text = "\n".join(
            part.lower()
            for part in (row.get("Subject") or "", row.get("BodyText") or "", row.get("BodyExcerpt") or "")
            if str(part).strip()
        )
        if estimate_token in text:
            return dict(row)
        if site_name and site_name in text:
            return dict(row)
    return None


def _find_estimate_followup_note(source: dict[str, Any]) -> dict[str, Any] | None:
    estimate_id = int(source.get("EstimateID") or 0)
    submit_date = _coerce_datetime(source.get("SubmitDate")) or _coerce_datetime(source.get("CreatedDate"))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                "NoteID",
                "EstimateID",
                "NoteText",
                "Category",
                "Timestamp"
            FROM public."Note"
            WHERE "EstimateID" = %s
              AND COALESCE("Timestamp", NOW()) >= %s
              AND (
                    LOWER(COALESCE("Category", '')) LIKE 'follow%%'
                 OR LOWER(COALESCE("NoteText", '')) LIKE '%%follow%%'
              )
            ORDER BY "Timestamp" DESC, "NoteID" DESC
            LIMIT 1
            """,
            (estimate_id, submit_date or datetime(2000, 1, 1)),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _find_work_order_invoice(source: dict[str, Any]) -> dict[str, Any] | None:
    work_order_id = int(source.get("WorkOrderID") or 0)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH latest_invoice AS (
                SELECT DISTINCT ON (i."WorkOrderID")
                    i."CustomerInvoiceId",
                    i."WorkOrderID",
                    COALESCE(i."InvoiceStatus", '') AS "InvoiceStatus",
                    i."CustomerInvoiceDate"
                FROM public."Invoice" i
                WHERE i."WorkOrderID" = %s
                  AND COALESCE(i."InvoiceStatus", '') != 'Retired'
                ORDER BY i."WorkOrderID", i."CustomerInvoiceId" DESC
            ),
            latest_draft AS (
                SELECT DISTINCT ON (d."CustomerInvoiceId")
                    d."CustomerInvoiceId",
                    d."CustomerInvoiceDocumentDraftID",
                    COALESCE(d."DraftStatus", '') AS "DraftStatus"
                FROM public."CustomerInvoiceDocumentDraft" d
                JOIN latest_invoice li ON li."CustomerInvoiceId" = d."CustomerInvoiceId"
                WHERE d."IsActive" = 1
                  AND COALESCE(d."DraftStatus", '') != 'Retired'
                ORDER BY d."CustomerInvoiceId", d."VersionNumber" DESC, d."CustomerInvoiceDocumentDraftID" DESC
            )
            SELECT
                li."CustomerInvoiceId",
                li."WorkOrderID",
                li."InvoiceStatus",
                li."CustomerInvoiceDate",
                ld."CustomerInvoiceDocumentDraftID",
                ld."DraftStatus"
            FROM latest_invoice li
            LEFT JOIN latest_draft ld ON ld."CustomerInvoiceId" = li."CustomerInvoiceId"
            LIMIT 1
            """,
            (work_order_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _is_due(expected_by: datetime | None) -> bool:
    if expected_by is None:
        return False
    return expected_by <= datetime.now(expected_by.tzinfo or timezone.utc)


def _is_active_snooze(obligation: WorkflowObligationRecord) -> bool:
    if obligation.status != STATUS_SNOOZED or obligation.snooze_until is None:
        return False
    return obligation.snooze_until > datetime.now(obligation.snooze_until.tzinfo or timezone.utc)


def _days_overdue(expected_by: datetime | None) -> int:
    if expected_by is None:
        return 0
    now = datetime.now(expected_by.tzinfo or timezone.utc)
    overdue = now - expected_by
    return max(0, overdue.days)


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        for fmt in (datetime.fromisoformat,):
            try:
                parsed = fmt(normalized)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _stringify_date_like(value: Any) -> str | None:
    parsed = _coerce_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    text = str(value or "").strip()
    return text or None


def _isoformat(value: Any) -> str | None:
    parsed = _coerce_datetime(value)
    return parsed.isoformat() if parsed is not None else None
