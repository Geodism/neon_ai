from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

from neon_ai.automation.runtime_flags import (
    LEGACY_AUTOMATION_DISABLED_MESSAGE,
    LEGACY_AUTOMATION_ENV,
    legacy_automation_runtime_enabled,
)
from neon_ai.services.automation_control_service import AutomationSettingRecord


LEGACY_GATEWAY_DISABLE_ENV = "NEON_DISABLE_GATEWAY"
PRIVATE_BRAIN_DISABLE_ENV = "NEON_DISABLE_PRIVATE_BRAIN"
LEGACY_ESTIMATE_AUTO_SEND_ENV = "NEON_ENABLE_LEGACY_ESTIMATE_AUTO_SEND"
BACKGROUND_VENDOR_RFQ_FOLLOWUPS_ENV = "NEON_ENABLE_BACKGROUND_VENDOR_RFQ_FOLLOWUPS"
BACKGROUND_VENDOR_PO_FOLLOWUPS_ENV = "NEON_ENABLE_BACKGROUND_VENDOR_PO_FOLLOWUPS"


@dataclass(frozen=True)
class AutomationPreset:
    key: str
    display_name: str
    workflow: str
    description: str
    level_2_mode: str
    level_3_mode: str
    risk_level: str
    runtime_state: str
    legacy_state: str
    approval_required: bool
    email_send_allowed: bool
    external_action_allowed: bool
    env_control: str
    allowed_reads: tuple[str, ...]
    allowed_writes: tuple[str, ...]
    disallowed_actions: tuple[str, ...]
    memory_scopes: tuple[str, ...]
    proposal_types: tuple[str, ...]
    question_types: tuple[str, ...]
    notes: str
    next_implementation_status: str


@dataclass(frozen=True)
class AutomationSettingDefaults:
    enabled: bool
    safe_mode: bool
    notify_only: bool
    draft_only: bool
    approval_required: bool
    frequency_minutes: int | None
    max_tokens_per_run: int | None
    max_actions_per_run: int | None
    notes: str | None


def automation_inventory_banner_lines() -> tuple[str, ...]:
    return (
        "Legacy automation runtime is disabled by default.",
        "Level 2 automation execution is not enabled yet.",
        "This page is read-only inventory only.",
    )


def list_automation_presets() -> list[AutomationPreset]:
    return [
        _rfq_reply_watcher(),
        _customer_scheduling_watcher(),
        _po_eta_watcher(),
        _packing_slip_receiving_watcher(),
        _ahj_permit_status_watcher(),
        _vendor_invoice_intake_watcher(),
        _estimate_follow_up_watcher(),
        _ready_to_pay_reminder(),
        _backorder_watcher(),
        _lead_intake_watcher(),
        _email_gateway(),
        _private_brain_local_ai(),
    ]


def default_automation_setting_for_preset(preset: AutomationPreset) -> AutomationSettingRecord:
    defaults = _default_setting_defaults_for_preset(preset)
    return AutomationSettingRecord(
        automation_key=preset.key,
        display_name=preset.display_name,
        workflow=preset.workflow,
        enabled=defaults.enabled,
        safe_mode=defaults.safe_mode,
        notify_only=defaults.notify_only,
        draft_only=defaults.draft_only,
        approval_required=defaults.approval_required,
        frequency_minutes=defaults.frequency_minutes,
        max_tokens_per_run=defaults.max_tokens_per_run,
        max_actions_per_run=defaults.max_actions_per_run,
        last_run_at=None,
        next_run_at=None,
        updated_at=None,
        notes=defaults.notes,
    )


def _truthy_env(env_name: str) -> bool:
    return str(os.getenv(env_name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _planned_runtime_state() -> str:
    if legacy_automation_runtime_enabled():
        return "Planned preset only; legacy automation flag is on, but this Level 2 preset is not executable yet"
    return "Planned preset only; execution not enabled"


def _default_setting_defaults_for_preset(preset: AutomationPreset) -> AutomationSettingDefaults:
    is_high_risk = str(preset.risk_level or "").strip().lower() == "high"
    return AutomationSettingDefaults(
        enabled=False,
        safe_mode=True,
        notify_only=True,
        draft_only=True,
        approval_required=True,
        frequency_minutes=None if is_high_risk else 60,
        max_tokens_per_run=4000,
        max_actions_per_run=0,
        notes=(
            "Default read-only foundation row. Execution remains disabled until future Automation Center slices add explicit policy-backed controls."
        ),
    )


def _base_preset(
    *,
    key: str,
    display_name: str,
    workflow: str,
    description: str,
    level_2_mode: str,
    level_3_mode: str,
    risk_level: str,
    env_control: str,
    allowed_reads: tuple[str, ...],
    allowed_writes: tuple[str, ...],
    disallowed_actions: tuple[str, ...],
    memory_scopes: tuple[str, ...],
    proposal_types: tuple[str, ...],
    question_types: tuple[str, ...],
    notes: str,
    next_implementation_status: str,
    runtime_state: str | None = None,
    legacy_state: str = "New structured preset planned; legacy runtime remains fenced by default",
) -> AutomationPreset:
    return AutomationPreset(
        key=key,
        display_name=display_name,
        workflow=workflow,
        description=description,
        level_2_mode=level_2_mode,
        level_3_mode=level_3_mode,
        risk_level=risk_level,
        runtime_state=runtime_state or _planned_runtime_state(),
        legacy_state=legacy_state,
        approval_required=True,
        email_send_allowed=False,
        external_action_allowed=False,
        env_control=env_control,
        allowed_reads=allowed_reads,
        allowed_writes=allowed_writes,
        disallowed_actions=disallowed_actions,
        memory_scopes=memory_scopes,
        proposal_types=proposal_types,
        question_types=question_types,
        notes=notes,
        next_implementation_status=next_implementation_status,
    )


def _rfq_reply_watcher() -> AutomationPreset:
    return _base_preset(
        key="rfq_reply_watcher",
        display_name="RFQ Reply Watcher",
        workflow="Material Call / RFQ / Receive Quotes",
        description="Reads inbound vendor quote replies, matches MaterialCall-backed RFQs, extracts quote candidates, and stages receive-quotes updates or approval proposals.",
        level_2_mode="Proposal-only, then low-risk quote draft writes under policy",
        level_3_mode="Draft outbound clarification replies only; every email still requires approval",
        risk_level="Medium",
        env_control=f"{LEGACY_AUTOMATION_ENV}, {BACKGROUND_VENDOR_RFQ_FOLLOWUPS_ENV}",
        allowed_reads=(
            "Inbound vendor email metadata and body",
            "Material Call / child RFQ context",
            "Receive Quotes matrix source data",
            "Vendor attachments for quote extraction",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Quote header and Quote Price draft writes only when confidence/policy allow",
        ),
        disallowed_actions=(
            "Send vendor email",
            "Lock quote",
            "Carry prices",
            "Update estimate pricing from quote entry",
            "Mutate locked RFQs",
        ),
        memory_scopes=("Vendor", "PriceRequest", "MaterialCall", "InboundMessage"),
        proposal_types=("quote_intake", "vendor_clarification", "rfq_match_review"),
        question_types=("match_ambiguity", "missing_quote_number", "line_price_unclear"),
        notes="Bid Compare remains the only carry-selection surface.",
        next_implementation_status="Slice 8 proposal-only watcher candidate",
    )


def _customer_scheduling_watcher() -> AutomationPreset:
    return _base_preset(
        key="customer_scheduling_service_inquiry_watcher",
        display_name="Customer Scheduling / Service Inquiry Watcher",
        workflow="Lead / Customer / Site / Scheduling",
        description="Classifies inbound customer messages, matches likely customer/site/work order context, and drafts replies, questions, or intake proposals.",
        level_2_mode="Draft notes, proposals, and operator questions only",
        level_3_mode="Draft approved customer replies and continue approval-gated communication loops",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Inbound customer email metadata and body",
            "Customer, Site, Work Order, and ScheduleVisit context",
            "Existing communication notes",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Draft customer/site/intake notes",
        ),
        disallowed_actions=(
            "Send customer email",
            "Change schedule without approval",
            "Mutate locked workflow records",
        ),
        memory_scopes=("Customer", "Site", "WorkOrder", "InboundMessage"),
        proposal_types=("schedule_reply_draft", "service_inquiry_response", "site_detail_request"),
        question_types=("missing_site_match", "schedule_ambiguity", "service_scope_unclear"),
        notes="Unknown or low-confidence intake becomes a question, not an action.",
        next_implementation_status="Planned after RFQ reply watcher",
    )


def _po_eta_watcher() -> AutomationPreset:
    return _base_preset(
        key="po_eta_parts_ready_watcher",
        display_name="PO ETA / Parts Ready Watcher",
        workflow="PO / Receiving",
        description="Parses vendor ETA and parts-ready messages, matches them to purchase orders, and stages internal notes or proposals for operator review.",
        level_2_mode="Read, classify, and write internal PO ETA observations only",
        level_3_mode="Draft vendor/customer follow-up replies for approval",
        risk_level="Medium",
        env_control=f"{LEGACY_AUTOMATION_ENV}, {BACKGROUND_VENDOR_PO_FOLLOWUPS_ENV}",
        allowed_reads=(
            "Inbound vendor ETA and pickup-ready emails",
            "Purchase Order / Work Order context",
            "Vendor and receiving history",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Internal PO status observation notes",
        ),
        disallowed_actions=(
            "Send vendor email",
            "Lock PO",
            "Mark received automatically",
            "Mutate locked PO records",
        ),
        memory_scopes=("PurchaseOrder", "Vendor", "WorkOrder", "InboundMessage"),
        proposal_types=("po_eta_note", "parts_ready_alert", "vendor_follow_up_draft"),
        question_types=("po_match_ambiguity", "eta_date_unclear"),
        notes="Receiving still happens through Receive Goods; this watcher is observation-first.",
        next_implementation_status="Planned after customer scheduling watcher",
    )


def _packing_slip_receiving_watcher() -> AutomationPreset:
    return _base_preset(
        key="packing_slip_receiving_watcher",
        display_name="Packing Slip Receiving Watcher",
        workflow="Receive Goods",
        description="Extracts line quantities from packing slips or receiving notices and stages draft receipt data for operator review.",
        level_2_mode="Draft receiving proposal / staged receipt only",
        level_3_mode="Draft clarification emails for approval if quantities are ambiguous",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Inbound packing slip email and attachment content",
            "Purchase Order receiving lines",
            "Vendor and Work Order context",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Draft/staged receipt quantities under policy",
        ),
        disallowed_actions=(
            "Finalize receipt without review",
            "Send vendor email",
            "Mutate locked records",
        ),
        memory_scopes=("PurchaseOrder", "PurchaseOrderItem", "Vendor", "InboundAttachment"),
        proposal_types=("receipt_draft", "packing_slip_match_review"),
        question_types=("line_match_ambiguity", "quantity_conflict"),
        notes="This preset stages receiving data but does not change final receiving save behavior.",
        next_implementation_status="Planned after PO ETA watcher",
    )


def _ahj_permit_status_watcher() -> AutomationPreset:
    return _base_preset(
        key="ahj_permit_status_watcher",
        display_name="AHJ / Permit Status Watcher",
        workflow="AHJ / Permits / Work Orders",
        description="Classifies inspection and permit messages, matches the project context, and stages notes or permit-status proposals.",
        level_2_mode="Internal note/proposal only",
        level_3_mode="Draft outbound clarification or acknowledgement replies for approval",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Inbound AHJ / inspector email",
            "Customer, Site, Work Order, and estimate/project context",
            "Permit-related attachments where available",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Internal permit-status notes",
        ),
        disallowed_actions=(
            "Send email to AHJ",
            "Mutate locked records",
            "Commit permit outcome without operator review if ambiguous",
        ),
        memory_scopes=("Site", "WorkOrder", "Estimate", "InboundMessage"),
        proposal_types=("permit_status_note", "inspection_outcome_review"),
        question_types=("project_match_ambiguity", "permit_status_unclear"),
        notes="This preset is ideal for structured JSON extraction of pass/fail/reinspection details.",
        next_implementation_status="Planned after packing slip watcher",
    )


def _vendor_invoice_intake_watcher() -> AutomationPreset:
    return _base_preset(
        key="vendor_invoice_intake_watcher",
        display_name="Vendor Invoice Intake Watcher",
        workflow="Vendor Invoice / Payables",
        description="Reads inbound vendor invoice emails and attachments, extracts draft invoice data, and stages intake proposals for payables review.",
        level_2_mode="Draft vendor invoice intake only",
        level_3_mode="Draft vendor clarification email for approval when required",
        risk_level="High",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Inbound vendor invoice email metadata/body",
            "Invoice attachment text/OCR output",
            "Vendor, PO, and receiving context",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Draft vendor invoice intake records",
        ),
        disallowed_actions=(
            "Mark invoice paid",
            "Send vendor email",
            "Mutate locked records",
        ),
        memory_scopes=("Vendor", "PurchaseOrder", "VendorInvoice", "InboundAttachment"),
        proposal_types=("vendor_invoice_draft", "invoice_match_review"),
        question_types=("invoice_match_ambiguity", "total_mismatch"),
        notes="Payables remains human-approved; mark-paid stays blocked.",
        next_implementation_status="Planned after receiving watcher",
    )


def _estimate_follow_up_watcher() -> AutomationPreset:
    return _base_preset(
        key="estimate_follow_up_watcher",
        display_name="Estimate Follow-Up Watcher",
        workflow="Estimate / Customer Proposal",
        description="Reviews estimate aging and communication context, then drafts reminders, notes, or follow-up proposals for operator approval.",
        level_2_mode="Internal reminder/proposal only",
        level_3_mode="Draft customer follow-up email for approval",
        risk_level="Medium",
        env_control=f"{LEGACY_AUTOMATION_ENV}, {LEGACY_ESTIMATE_AUTO_SEND_ENV}",
        allowed_reads=(
            "Estimate pipeline status and dates",
            "Customer/site context",
            "Communication history and notes",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Internal follow-up notes",
        ),
        disallowed_actions=(
            "Send customer email automatically",
            "Lock or send estimate document",
            "Mutate locked estimate records",
        ),
        memory_scopes=("Estimate", "Customer", "Site"),
        proposal_types=("estimate_follow_up_draft", "estimate_status_review"),
        question_types=("missing_customer_context", "follow_up_timing_review"),
        notes="Legacy estimate auto-send remains fenced off.",
        next_implementation_status="Lower-priority preset after intake/watch foundation",
    )


def _ready_to_pay_reminder() -> AutomationPreset:
    return _base_preset(
        key="ready_to_pay_reminder",
        display_name="Ready-to-Pay Reminder",
        workflow="Vendor Invoice / Payables",
        description="Highlights invoices that appear ready for payables review and creates internal reminders or proposals only.",
        level_2_mode="Notify-only / proposal-only",
        level_3_mode="Draft internal escalation or vendor clarification for approval",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Vendor invoice status and due dates",
            "Receiving / PO match context",
            "Payables notes",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationMemory",
            "Internal reminder/event logs",
        ),
        disallowed_actions=(
            "Mark invoice paid",
            "Send external email",
            "Mutate locked invoice records",
        ),
        memory_scopes=("VendorInvoice", "Vendor", "PurchaseOrder"),
        proposal_types=("ready_to_pay_review", "payables_follow_up_draft"),
        question_types=("invoice_match_unclear",),
        notes="Useful as a low-risk internal alert once run/event logging exists.",
        next_implementation_status="Can arrive after vendor invoice intake",
    )


def _backorder_watcher() -> AutomationPreset:
    return _base_preset(
        key="backorder_watcher",
        display_name="Backorder Watcher",
        workflow="PO / Receiving / Reporting",
        description="Surfaces aged partial receipts or vendor delays and creates internal review items, not autonomous workflow mutations.",
        level_2_mode="Internal note/proposal only",
        level_3_mode="Draft clarification emails for approval",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Purchase Order receiving state",
            "Outstanding/backordered quantities",
            "Vendor communication history",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationMemory",
            "Internal backorder observations",
        ),
        disallowed_actions=(
            "Send vendor email",
            "Change PO quantities automatically",
            "Mutate locked PO records",
        ),
        memory_scopes=("PurchaseOrder", "PurchaseOrderItem", "Vendor"),
        proposal_types=("backorder_alert", "vendor_follow_up_draft"),
        question_types=("backorder_status_unclear",),
        notes="Good fit for the later last-24-hours activity feed.",
        next_implementation_status="Planned after PO/receiving intake watchers",
    )


def _lead_intake_watcher() -> AutomationPreset:
    return _base_preset(
        key="lead_intake_watcher",
        display_name="Lead Intake Watcher",
        workflow="Lead / Customer / Site / Estimate",
        description="Reads inbound lead/service inquiry messages, extracts structured intake data, and stages draft customer/site/estimate shell records or questions.",
        level_2_mode="Draft-only intake assistant",
        level_3_mode="Draft outbound follow-up replies for approval",
        risk_level="Medium",
        env_control=LEGACY_AUTOMATION_ENV,
        allowed_reads=(
            "Inbound lead emails/forms imported into the review queue",
            "Customer and site matching context",
            "Estimate pipeline shell data",
        ),
        allowed_writes=(
            "AutomationProposal",
            "AutomationQuestion",
            "AutomationMemory",
            "Draft Customer / Site / Estimate shell records under policy",
        ),
        disallowed_actions=(
            "Send customer email",
            "Finalize estimates",
            "Mutate locked records",
        ),
        memory_scopes=("Customer", "Site", "Estimate", "InboundMessage"),
        proposal_types=("lead_intake_draft", "site_match_review", "estimate_shell_draft"),
        question_types=("missing_contact_info", "service_scope_unclear"),
        notes="A strong early preset once inbound intake tables and proposal queue exist.",
        next_implementation_status="Planned after RFQ reply watcher proves the pattern",
    )


def _email_gateway() -> AutomationPreset:
    legacy_runtime_enabled = legacy_automation_runtime_enabled()
    gateway_disabled = _truthy_env(LEGACY_GATEWAY_DISABLE_ENV)
    runtime_state = "Disabled by default"
    if legacy_runtime_enabled and gateway_disabled:
        runtime_state = f"Legacy runtime allowed, but blocked by {LEGACY_GATEWAY_DISABLE_ENV}"
    elif legacy_runtime_enabled:
        runtime_state = "Legacy gateway runtime could start if explicitly allowed"

    return _base_preset(
        key="email_gateway",
        display_name="Email Gateway",
        workflow="Cross-workflow inbound automation",
        description="Legacy gateway/instruction loop that historically polled inboxes and dispatched automation pathways. It is inventoried here as a fenced legacy runtime, not a Level 2 preset.",
        level_2_mode="Not executable in Automation Center Slice 1",
        level_3_mode="Will be replaced by structured inbound intake + approval-gated communication services",
        risk_level="High",
        runtime_state=runtime_state,
        legacy_state="Legacy runtime fenced by default behind NEON_ENABLE_LEGACY_AUTOMATION=1",
        env_control=", ".join(
            (
                LEGACY_AUTOMATION_ENV,
                LEGACY_GATEWAY_DISABLE_ENV,
                BACKGROUND_VENDOR_RFQ_FOLLOWUPS_ENV,
                BACKGROUND_VENDOR_PO_FOLLOWUPS_ENV,
            )
        ),
        allowed_reads=(
            "Existing gateway/inbound automation inventory only",
            "Legacy env-flag/runtime posture",
        ),
        allowed_writes=(),
        disallowed_actions=(
            "Start gateway runtime from this page",
            "Poll inbox automatically",
            "Run sweepers",
            "Process inbound emails automatically",
        ),
        memory_scopes=("AutomationInventory",),
        proposal_types=("future_gateway_replacement_plan",),
        question_types=("legacy_runtime_review",),
        notes=LEGACY_AUTOMATION_DISABLED_MESSAGE,
        next_implementation_status="Remain fenced until Automation Center presets and proposal queue replace it",
    )


def _private_brain_local_ai() -> AutomationPreset:
    private_brain_disabled = _truthy_env(PRIVATE_BRAIN_DISABLE_ENV)
    runtime_state = "Manual-only assistant surface; not part of enabled automation runtime"
    if private_brain_disabled:
        runtime_state = f"Manual assistant disabled by {PRIVATE_BRAIN_DISABLE_ENV}"

    return _base_preset(
        key="private_brain_local_ai",
        display_name="Private Brain / Local AI",
        workflow="Cross-workflow assistant tooling",
        description="Existing manual Private Brain surface. It remains a user-invoked assistant and is inventoried separately from automated execution.",
        level_2_mode="Manual-only assistant until policy-backed adapters and proposal queue exist",
        level_3_mode="May help draft approved outbound communication, still approval-gated",
        risk_level="Medium",
        runtime_state=runtime_state,
        legacy_state="Existing manual tool; not an enabled workflow automation runtime",
        env_control=PRIVATE_BRAIN_DISABLE_ENV,
        allowed_reads=(
            "User-supplied prompts",
            "Future scoped workflow context passed through controlled adapters",
        ),
        allowed_writes=("None in Slice 1 inventory mode",),
        disallowed_actions=(
            "Autonomous workflow execution",
            "External email send",
            "Locked-record mutation",
        ),
        memory_scopes=("ManualSession", "Future scoped workflow memory"),
        proposal_types=("assistant_draft", "question_for_operator"),
        question_types=("manual_follow_up_question",),
        notes="This page inventories the tool but does not wire it into automation execution.",
        next_implementation_status="Revisit after LLM provider adapter and proposal queue land",
    )
