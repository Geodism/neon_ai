from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from neon_ai.services.automation_json_contracts import validate_question_choices
from neon_ai.services.automation_policy_service import evaluate_confidence_policy
from neon_ai.services.automation_control_service import (
    AutomationRunRecord,
    create_automation_run,
    finish_automation_run,
    log_automation_event,
)
from neon_ai.services.automation_proposal_service import AutomationQuestionRecord, create_question
from neon_ai.services.inbound_attachment_text_extraction_service import (
    build_attachment_extraction_evidence,
    extract_text_for_message_attachments,
    summarize_message_attachment_text,
)
from neon_ai.services.inbound_intake_service import (
    InboundMessageRecord,
    get_inbound_message,
    list_inbound_attachments,
    list_inbound_messages,
    update_message_classification,
    update_message_status,
)
from neon_ai.services.llm_provider_service import extract_json, get_llm_provider_config


AUTOMATION_KEY = "inbox_intake_watcher_classifier"
UNKNOWN_REVIEW_AUTOMATION_KEY = "unknown_inbound_review"
UNKNOWN_REVIEW_WORKFLOW = "unknown_review"
UNKNOWN_REVIEW_QUESTION_TYPE = "inbound_routing_review"
UNKNOWN_REVIEW_CHOICES = [
    "RFQ Reply / Receive Quotes",
    "PO ETA / Parts Ready",
    "Packing Slip / Receiving",
    "Vendor Invoice / Payables",
    "Customer Scheduling / Service Inquiry",
    "Lead / New Customer Inquiry",
    "Estimate Follow-up / Customer Reply",
    "AHJ / Permit Status",
    "Document Control",
    "Spam / Ignore",
    "Other / Explain",
]
SUPPORTED_ROUTES = (
    "rfq_reply",
    "customer_scheduling_service_inquiry",
    "po_eta_parts_ready",
    "packing_slip_receiving",
    "ahj_permit_status",
    "vendor_invoice_intake",
    "estimate_follow_up_or_customer_reply",
    "lead_intake",
    "unknown_review",
)
ROUTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "packing_slip_receiving": ("packing slip", "delivery receipt", "shipment enclosed", "bill of lading"),
    "ahj_permit_status": ("permit passed", "inspection passed", "permit approved", "ahj", "final inspection"),
    "po_eta_parts_ready": ("parts are ready", "ready for pickup", "ready for pick up", "available for pickup", "eta", "backorder"),
    "vendor_invoice_intake": ("invoice attached", "vendor invoice", "invoice number", "statement attached", "remit"),
    "rfq_reply": ("quote number", "rfq", "pricing attached", "quote attached", "quoted price", "please find attached our pricing"),
    "customer_scheduling_service_inquiry": ("are you coming today", "coming today", "what time are you coming", "service call", "schedule us", "do you offer"),
    "estimate_follow_up_or_customer_reply": ("estimate", "proposal", "customer reply", "approve estimate", "follow up on estimate"),
    "lead_intake": ("need a quote", "new project", "looking for electrician", "can you quote", "request service"),
}
BUSINESS_PLAUSIBLE_TERMS = (
    "order",
    "quote",
    "invoice",
    "site",
    "estimate",
    "permit",
    "inspection",
    "schedule",
    "delivery",
    "pickup",
    "material",
    "purchase order",
    "po #",
    "rfq",
    "job",
    "address",
    "service",
)
URGENT_TERMS = ("urgent", "asap", "today", "immediately", "right away", "deadline", "rush")
SPAM_TERMS = ("unsubscribe", "buy now", "limited time", "special offer", "webinar", "crypto", "gift card", "marketing")


@dataclass(frozen=True)
class InboxClassificationResult:
    message: InboundMessageRecord
    classification_json: dict[str, Any]
    run: AutomationRunRecord
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool


def classify_inbound_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    create_question_on_low_confidence: bool = True,
    trigger_type: str = "manual_classify",
) -> InboxClassificationResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    config = get_llm_provider_config()
    requested_llm = bool(use_llm)
    llm_allowed = requested_llm and config.provider != "disabled"
    mode_used = "deterministic_mock"
    if llm_allowed:
        mode_used = f"llm:{config.provider}"

    run = create_automation_run(
        AUTOMATION_KEY,
        status="Started",
        trigger_type=trigger_type,
        model_provider=config.provider if llm_allowed else "disabled",
        model_name=config.model if llm_allowed else None,
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
        event_type="classification_started",
        summary=f"Started inbound message classification for message #{message.inbound_message_id}.",
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        event_json={
            "mode_used": mode_used,
            "requested_llm": requested_llm,
            "provider": config.provider,
            "model": config.model,
        },
    )

    question: AutomationQuestionRecord | None = None
    llm_called = False
    try:
        attachment_results = extract_text_for_message_attachments(
            message.inbound_message_id,
            force=False,
            source=AUTOMATION_KEY,
        )
        attachment_records = [result.attachment for result in attachment_results]
        attachment_context = summarize_message_attachment_text(attachment_records)
        if attachment_context.get("attachment_count"):
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="attachment_text_checked",
                summary=f"Checked shared attachment text extraction for inbound message #{message.inbound_message_id}.",
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                event_json={
                    "attachment_count": attachment_context.get("attachment_count"),
                    "has_usable_attachment_text": attachment_context.get("has_usable_attachment_text"),
                    "needs_ocr_count": attachment_context.get("needs_ocr_count"),
                    "failed_count": attachment_context.get("failed_count"),
                },
            )

        if llm_allowed:
            llm_result = _classify_with_llm(
                message,
                attachments=attachment_records,
                allow_live_call=allow_live_call,
            )
            if llm_result["success"]:
                classification_json = llm_result["classification_json"]
                llm_called = bool(llm_result["llm_called"])
                input_tokens = int(llm_result.get("input_tokens") or 0)
                output_tokens = int(llm_result.get("output_tokens") or 0)
                estimated_cost = float(llm_result.get("estimated_cost") or 0)
                mode_used = str(llm_result.get("mode_used") or mode_used)
            else:
                classification_json = _classify_deterministically(message)
                input_tokens = 0
                output_tokens = 0
                estimated_cost = 0.0
                mode_used = f"{mode_used}_fallback"
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="classification_fallback",
                    summary=f"LLM classification fell back to deterministic mode for message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            classification_json = _classify_deterministically(message, attachment_records)
            input_tokens = 0
            output_tokens = 0
            estimated_cost = 0.0

        classification_json = _apply_attachment_context(classification_json, attachment_context)
        classification_json = _apply_unknown_review_boundary(classification_json, message)
        disposition = str(classification_json.get("routing_disposition") or "classified")
        message_status = "Classified"
        if disposition == "question_required":
            message_status = "QuestionCreated" if create_question_on_low_confidence else "ReviewRequired"
        elif disposition == "ignored_not_actionable":
            message_status = "Ignored"

        updated_message = update_message_classification(
            message.inbound_message_id,
            classification_json=classification_json,
            workflow_guess=str(classification_json.get("workflow") or ""),
            intent_guess=str(classification_json.get("intent") or ""),
            confidence=classification_json.get("confidence"),
            status=message_status,
        )
        if updated_message is None:
            raise RuntimeError("InboundMessage classification update returned no row.")
        updated_message = update_message_status(
            updated_message.inbound_message_id,
            status=message_status,
            processed_at=datetime.now(timezone.utc),
            error_message=None,
        ) or updated_message

        if create_question_on_low_confidence and disposition == "question_required":
            question_payload = _build_unknown_review_question_choices(updated_message, classification_json)
            validation = validate_question_choices(question_payload)
            if not validation.valid:
                raise RuntimeError(
                    "Unknown inbound review question payload is invalid: "
                    + "; ".join(validation.errors)
                )
            question = create_question(
                automation_key=UNKNOWN_REVIEW_AUTOMATION_KEY,
                workflow=UNKNOWN_REVIEW_WORKFLOW,
                question_type=UNKNOWN_REVIEW_QUESTION_TYPE,
                target_type="InboundMessage",
                target_id=updated_message.inbound_message_id,
                question_text=(
                    "This inbound message could not be confidently routed. "
                    "What workflow does it belong to, and how did you determine that?"
                ),
                choices_json=validation.normalized_json,
                required_before_action=True,
                urgency=_review_urgency(updated_message, classification_json),
                status="Open",
            )
            updated_message = update_message_status(
                updated_message.inbound_message_id,
                status="QuestionCreated",
                processed_at=datetime.now(timezone.utc),
                error_message=None,
            ) or updated_message
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="classification_review_required",
                summary=f"Inbound message #{updated_message.inbound_message_id} requires operator routing review.",
                target_type="InboundMessage",
                target_id=updated_message.inbound_message_id,
                event_json={
                    "uncertainty_reason": classification_json.get("uncertainty_reason"),
                    "candidate_workflows": classification_json.get("candidate_workflows"),
                    "business_plausible": classification_json.get("business_plausible"),
                    "question_id": question.automation_question_id,
                },
            )
        elif disposition == "question_required":
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="classification_review_required",
                summary=f"Inbound message #{updated_message.inbound_message_id} requires operator routing review.",
                target_type="InboundMessage",
                target_id=updated_message.inbound_message_id,
                event_json={
                    "uncertainty_reason": classification_json.get("uncertainty_reason"),
                    "candidate_workflows": classification_json.get("candidate_workflows"),
                    "business_plausible": classification_json.get("business_plausible"),
                    "question_created": None,
                },
            )
        elif disposition == "ignored_not_actionable":
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="classification_ignored",
                summary=f"Inbound message #{updated_message.inbound_message_id} was classified as not actionable noise/spam.",
                target_type="InboundMessage",
                target_id=updated_message.inbound_message_id,
                event_json={
                    "uncertainty_reason": classification_json.get("uncertainty_reason"),
                    "spam_or_noise": classification_json.get("spam_or_noise"),
                    "business_plausible": classification_json.get("business_plausible"),
                },
            )

        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="classification_completed",
            summary=(
                f"Classified inbound message #{updated_message.inbound_message_id} "
                f"as {classification_json.get('intent')}."
            ),
            target_type="InboundMessage",
            target_id=updated_message.inbound_message_id,
            event_json={
                "mode_used": mode_used,
                "llm_called": llm_called,
                "classification_json": classification_json,
                "message_status": updated_message.status,
                "question_created": question.automation_question_id if question else None,
            },
        )
        finished_run = finish_automation_run(
            run.automation_run_id,
            status="Completed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            actions_created=0,
            proposals_created=0,
            questions_created=1 if question else 0,
            error_message=None,
        )
        if finished_run is None:
            raise RuntimeError("AutomationRun finish returned no row.")
        return InboxClassificationResult(
            message=updated_message,
            classification_json=classification_json,
            run=finished_run,
            question=question,
            mode_used=mode_used,
            llm_called=llm_called,
        )
    except Exception as exc:
        update_message_status(
            message.inbound_message_id,
            status="Failed",
            processed_at=datetime.now(timezone.utc),
            error_message=str(exc),
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="classification_failed",
            summary=f"Classification failed for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={"error": str(exc), "mode_used": mode_used},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        raise


def classify_inbound_messages(
    *,
    limit: int = 25,
    status: str = "Imported",
    source_system: str | None = None,
    use_llm: bool = False,
    allow_live_call: bool = False,
    create_question_on_low_confidence: bool = True,
    trigger_type: str = "manual_batch_classify",
) -> list[InboxClassificationResult]:
    messages = list_inbound_messages(limit=limit, status=status, source_system=source_system)
    results: list[InboxClassificationResult] = []
    for message in messages:
        results.append(
            classify_inbound_message(
                message.inbound_message_id,
                use_llm=use_llm,
                allow_live_call=allow_live_call,
                create_question_on_low_confidence=create_question_on_low_confidence,
                trigger_type=trigger_type,
            )
        )
    return results


def _classify_with_llm(
    message: InboundMessageRecord,
    *,
    attachments: list[Any],
    allow_live_call: bool,
) -> dict[str, Any]:
    attachment_context = summarize_message_attachment_text(attachments)
    attachment_text = str(attachment_context.get("combined_text") or "").strip()
    prompt = (
        "Classify this inbound Neon_ai workflow message. "
        "Return JSON only with keys: message_id, sender, sender_type, workflow, intent, confidence, "
        "matched_entities, requires_question, suggested_next_action, risk_level.\n\n"
        f"Message ID: {message.inbound_message_id}\n"
        f"Sender: {message.sender or '-'}\n"
        f"Sender Name: {message.sender_name or '-'}\n"
        f"Subject: {message.subject or '-'}\n"
        f"Body:\n{message.body_text or message.body_excerpt or '-'}\n\n"
        f"Attachment Text:\n{attachment_text[:2000] or '-'}\n\n"
        "Allowed intent values:\n"
        + "\n".join(f"- {route}" for route in SUPPORTED_ROUTES)
    )
    result = extract_json(
        prompt,
        system_prompt=(
            "You are a structured inbox intake classifier for Neon_ai. "
            "Return only valid JSON. Do not propose external actions."
        ),
        allow_live_call=allow_live_call,
    )
    if not result.success or not isinstance(result.json_data, dict):
        return {"success": False, "error": result.error or "LLM classification failed."}
    parsed = _normalize_classification_json(result.json_data, message)
    return {
        "success": True,
        "classification_json": parsed,
        "input_tokens": result.input_tokens or 0,
        "output_tokens": result.output_tokens or 0,
        "estimated_cost": result.estimated_cost or 0,
        "llm_called": True,
        "mode_used": f"llm:{result.provider}",
    }


def _classify_deterministically(
    message: InboundMessageRecord,
    attachments: list[Any],
) -> dict[str, Any]:
    subject = _normalize_text(message.subject)
    body = _normalize_text(message.body_text or message.body_excerpt)
    sender = _normalize_text(message.sender)
    attachment_context = summarize_message_attachment_text(attachments)
    attachment_text = _normalize_text(str(attachment_context.get("combined_text") or ""))
    combined = " ".join(part for part in (subject, body, sender, attachment_text) if part).strip()
    sender_type = _infer_sender_type(sender, combined)

    candidate_workflows = _candidate_workflows_for_text(combined)
    top_candidate = candidate_workflows[0] if candidate_workflows else None
    route = str(top_candidate.get("intent") or "unknown_review") if top_candidate else "unknown_review"
    workflow = _workflow_for_route(route)
    confidence = float(top_candidate.get("confidence") or 0.42) if top_candidate else 0.42
    suggested_next_action = _default_next_action(route)
    matched_entities: dict[str, Any] = {}
    risk_level = "medium" if route == "unknown_review" else "low"
    business_plausible = _is_business_plausible(message, combined, sender_type)
    spam_or_noise = _looks_like_spam_or_noise(message, combined, sender_type)
    matched_phrases = list(top_candidate.get("matched_phrases") or []) if top_candidate else []

    if route == "rfq_reply":
        matched_entities["message_kind"] = "vendor_quote_reply"
    elif route == "vendor_invoice_intake":
        matched_entities["message_kind"] = "vendor_invoice"
    elif route == "packing_slip_receiving":
        matched_entities["message_kind"] = "packing_slip"

    requires_question = route == "unknown_review" or confidence < 0.65
    return _normalize_classification_json(
        {
            "message_id": str(message.inbound_message_id),
            "sender": message.sender or message.sender_name or "",
            "sender_type": sender_type,
            "workflow": workflow,
            "intent": route,
            "confidence": confidence,
            "matched_entities": matched_entities,
            "requires_question": requires_question,
            "suggested_next_action": suggested_next_action,
            "risk_level": risk_level,
            "candidate_workflows": candidate_workflows,
            "matched_phrases": matched_phrases,
            "business_plausible": business_plausible,
            "spam_or_noise": spam_or_noise,
        },
        message,
    )


def _normalize_classification_json(raw: dict[str, Any], message: InboundMessageRecord) -> dict[str, Any]:
    intent = str(raw.get("intent") or "unknown_review").strip()
    if intent not in SUPPORTED_ROUTES:
        intent = "unknown_review"
    workflow = str(raw.get("workflow") or _workflow_for_route(intent)).strip()
    confidence = _clamp_confidence(raw.get("confidence"))
    sender_type = str(raw.get("sender_type") or _infer_sender_type(_normalize_text(message.sender), _normalize_text(message.body_text or message.body_excerpt))).strip() or "unknown"
    matched_entities = raw.get("matched_entities")
    if not isinstance(matched_entities, dict):
        matched_entities = {}
    risk_level = str(raw.get("risk_level") or ("medium" if intent == "unknown_review" else "low")).strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
    requires_question = bool(raw.get("requires_question")) or intent == "unknown_review" or confidence < 0.65
    suggested_next_action = str(raw.get("suggested_next_action") or _default_next_action(intent)).strip()
    candidate_workflows = _normalize_candidate_workflows(raw.get("candidate_workflows"))
    matched_phrases = _normalize_text_list(raw.get("matched_phrases"))
    business_plausible = bool(raw.get("business_plausible"))
    spam_or_noise = bool(raw.get("spam_or_noise"))
    uncertainty_reason = str(raw.get("uncertainty_reason") or "").strip() or None
    routing_disposition = str(raw.get("routing_disposition") or "").strip() or None
    return {
        "message_id": str(raw.get("message_id") or message.inbound_message_id),
        "sender": str(raw.get("sender") or message.sender or message.sender_name or "").strip(),
        "sender_type": sender_type,
        "workflow": workflow,
        "intent": intent,
        "confidence": confidence,
        "matched_entities": matched_entities,
        "requires_question": requires_question,
        "suggested_next_action": suggested_next_action,
        "risk_level": risk_level,
        "candidate_workflows": candidate_workflows,
        "matched_phrases": matched_phrases,
        "business_plausible": business_plausible,
        "spam_or_noise": spam_or_noise,
        "uncertainty_reason": uncertainty_reason,
        "routing_disposition": routing_disposition,
    }


def _workflow_for_route(route: str) -> str:
    mapping = {
        "rfq_reply": "Material Call / RFQ",
        "customer_scheduling_service_inquiry": "Scheduling / Work Order visits",
        "po_eta_parts_ready": "PO / Receiving",
        "packing_slip_receiving": "PO / Receiving",
        "ahj_permit_status": "AHJ / permits",
        "vendor_invoice_intake": "Vendor Invoice / Payables",
        "estimate_follow_up_or_customer_reply": "Estimate / Customer Proposal",
        "lead_intake": "Lead / Customer / Site",
        "unknown_review": "Inbox Intake",
    }
    return mapping.get(route, "Inbox Intake")


def _default_next_action(route: str) -> str:
    mapping = {
        "rfq_reply": "review_in_receive_quotes",
        "customer_scheduling_service_inquiry": "review_customer_scheduling_question",
        "po_eta_parts_ready": "review_po_eta_or_parts_ready_note",
        "packing_slip_receiving": "review_packing_slip_intake",
        "ahj_permit_status": "review_permit_status_note",
        "vendor_invoice_intake": "review_vendor_invoice_intake",
        "estimate_follow_up_or_customer_reply": "review_estimate_follow_up",
        "lead_intake": "review_lead_intake",
        "unknown_review": "review_in_automation_center",
    }
    return mapping.get(route, "review_in_automation_center")


def _normalize_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _candidate_workflows_for_text(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for route, keywords in ROUTE_KEYWORDS.items():
        matched = [keyword for keyword in keywords if keyword in text]
        if not matched:
            continue
        score = min(0.76 + (0.11 * len(matched)), 0.97)
        candidates.append(
            {
                "intent": route,
                "workflow": _workflow_for_route(route),
                "confidence": round(score, 2),
                "matched_phrases": matched,
            }
        )
    candidates.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return candidates


def _normalize_candidate_workflows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "intent": str(item.get("intent") or "").strip(),
                "workflow": str(item.get("workflow") or "").strip(),
                "confidence": _clamp_confidence(item.get("confidence")),
                "matched_phrases": _normalize_text_list(item.get("matched_phrases")),
            }
        )
    return [item for item in normalized if item["intent"]]


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


def _looks_like_spam_or_noise(message: InboundMessageRecord, combined_text: str, sender_type: str) -> bool:
    if _contains_any(combined_text, SPAM_TERMS):
        return True
    sender = _normalize_text(message.sender)
    subject = _normalize_text(message.subject)
    if "newsletter" in sender or "no-reply" in sender or "noreply" in sender:
        return True
    if sender_type == "unknown" and _contains_any(subject, ("sale", "promotion", "discount", "offer")):
        return True
    return False


def _is_business_plausible(message: InboundMessageRecord, combined_text: str, sender_type: str) -> bool:
    if sender_type in {"vendor", "customer", "lead", "ahj"}:
        return True
    if message.attachment_count > 0:
        return True
    return _contains_any(combined_text, BUSINESS_PLAUSIBLE_TERMS)


def _has_conflicting_candidates(candidate_workflows: list[dict[str, Any]]) -> bool:
    if len(candidate_workflows) < 2:
        return False
    top = float(candidate_workflows[0].get("confidence") or 0.0)
    second = float(candidate_workflows[1].get("confidence") or 0.0)
    return top >= 0.60 and second >= 0.60 and abs(top - second) <= 0.15


def _apply_unknown_review_boundary(classification_json: dict[str, Any], message: InboundMessageRecord) -> dict[str, Any]:
    final = dict(classification_json)
    candidate_workflows = _normalize_candidate_workflows(final.get("candidate_workflows"))
    sender_type = str(final.get("sender_type") or "unknown").strip().lower()
    combined = _normalize_text(" ".join(part for part in (message.subject, message.body_text or message.body_excerpt, message.sender) if part))
    confidence = _clamp_confidence(final.get("confidence"))
    business_plausible = bool(final.get("business_plausible")) or _is_business_plausible(message, combined, sender_type)
    spam_or_noise = bool(final.get("spam_or_noise")) or _looks_like_spam_or_noise(message, combined, sender_type)
    conflicting = _has_conflicting_candidates(candidate_workflows)
    policy = evaluate_confidence_policy(confidence, workflow=str(final.get("workflow") or ""))

    uncertainty_reason = str(final.get("uncertainty_reason") or "").strip()
    disposition = "classified"

    if confidence >= 0.85 and str(final.get("intent") or "").strip() != "unknown_review" and not conflicting:
        disposition = "classified"
    elif confidence >= 0.60 and conflicting:
        disposition = "question_required"
        uncertainty_reason = uncertainty_reason or "Conflicting workflow signals were detected."
    elif confidence >= 0.45:
        disposition = "question_required"
        uncertainty_reason = uncertainty_reason or "The message is business-plausible but the workflow is unclear."
    elif business_plausible:
        disposition = "question_required"
        uncertainty_reason = uncertainty_reason or "The message appears business-related but lacks enough routing evidence."
    elif spam_or_noise:
        disposition = "ignored_not_actionable"
        uncertainty_reason = uncertainty_reason or "The message appears to be spam/noise and is not actionable."
    else:
        disposition = "ignored_not_actionable"
        uncertainty_reason = uncertainty_reason or "The message is not business-plausible and is not actionable."

    if disposition == "question_required":
        final["workflow"] = UNKNOWN_REVIEW_WORKFLOW
        final["intent"] = "unknown_review"
        final["requires_question"] = True
        final["risk_level"] = "medium"
        final["suggested_next_action"] = "operator_routing_review"
    elif disposition == "ignored_not_actionable":
        final["workflow"] = UNKNOWN_REVIEW_WORKFLOW
        final["intent"] = "unknown_review"
        final["requires_question"] = False
        final["risk_level"] = "low"
        final["suggested_next_action"] = "ignore_not_actionable"

    final["confidence"] = confidence
    final["candidate_workflows"] = candidate_workflows
    final["business_plausible"] = business_plausible
    final["spam_or_noise"] = spam_or_noise
    final["uncertainty_reason"] = uncertainty_reason
    final["routing_disposition"] = disposition
    final["confidence_policy"] = {
        "policy_key": policy.policy_key,
        "reason": policy.reason,
        "recommended_next_action": policy.recommended_next_action,
    }
    return final


def _build_unknown_review_question_choices(
    message: InboundMessageRecord,
    classification_json: dict[str, Any],
) -> dict[str, Any]:
    attachments = list_inbound_attachments(inbound_message_id=message.inbound_message_id, limit=20)
    candidate_workflows = classification_json.get("candidate_workflows") or []
    if not candidate_workflows:
        candidate_workflows = [
            {
                "intent": "unknown_review",
                "workflow": UNKNOWN_REVIEW_WORKFLOW,
                "confidence": classification_json.get("confidence"),
                "matched_phrases": classification_json.get("matched_phrases") or [],
            }
        ]
    return {
        "choices": list(UNKNOWN_REVIEW_CHOICES),
        "allow_free_text": True,
        "candidate_workflows": candidate_workflows,
        "matched_phrases": classification_json.get("matched_phrases") or [],
        "sender": message.sender or "",
        "sender_name": message.sender_name or "",
        "subject": message.subject or "",
        "body_excerpt": message.body_excerpt or message.body_text or "",
        "attachments": build_attachment_extraction_evidence(attachments),
        "uncertainty_reason": classification_json.get("uncertainty_reason") or "Routing confidence was too low.",
        "required_operator_fields": [
            "selected_workflow",
            "selected_intent",
            "how_i_know",
            "target_record",
            "save_as_routing_memory",
        ],
        "how_i_know_field": {
            "id": "how_i_know",
            "label": "How I know",
            "required": True,
        },
        "future_memory_option": {
            "label": "Save as routing memory",
            "memory_type": "operator_routing_explanation",
            "source": "operator_review",
        },
    }


def _apply_attachment_context(
    classification_json: dict[str, Any],
    attachment_context: dict[str, Any],
) -> dict[str, Any]:
    final = dict(classification_json)
    final["attachment_text_available"] = bool(attachment_context.get("has_usable_attachment_text"))
    final["attachment_extraction_summary"] = attachment_context.get("attachments") or []
    if attachment_context.get("attachment_count") and not attachment_context.get("has_usable_attachment_text"):
        reason = str(final.get("uncertainty_reason") or "").strip()
        attachment_reason = "Attachment extraction produced no usable text; routing relied on subject/body and attachment metadata."
        final["uncertainty_reason"] = (
            f"{reason} {attachment_reason}".strip()
            if reason
            else attachment_reason
        )
        final["requires_question"] = True
    return final


def _review_urgency(message: InboundMessageRecord, classification_json: dict[str, Any]) -> str:
    combined = _normalize_text(" ".join(part for part in (message.subject, message.body_text or message.body_excerpt) if part))
    return "High" if _contains_any(combined, URGENT_TERMS) else "Normal"


def _infer_sender_type(sender: str, combined_text: str) -> str:
    if _contains_any(sender, ("@city.", "@gov.", "@inspection", "permit", "ahj")) or _contains_any(combined_text, ("permit", "inspection", "ahj")):
        return "ahj"
    if _contains_any(combined_text, ("invoice", "quote", "pricing", "wholesaler", "vendor", "parts ready", "packing slip")):
        return "vendor"
    if _contains_any(combined_text, ("coming today", "service", "customer", "estimate", "proposal", "site")):
        return "customer"
    if _contains_any(combined_text, ("new project", "need a quote", "looking for electrician", "request service")):
        return "lead"
    return "unknown"


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.50
    return max(0.0, min(1.0, confidence))
