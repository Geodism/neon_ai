from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import re
from typing import Any


RISK_LEVELS = {"low", "medium", "high"}
EVENT_SEVERITY_LEVELS = {"debug", "info", "warning", "error"}


@dataclass(frozen=True)
class JsonContractValidationResult:
    valid: bool
    contract_name: str
    errors: list[str]
    warnings: list[str]
    normalized_json: Any


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def format_validation_errors(
    result_or_errors: JsonContractValidationResult | list[str] | tuple[list[str], list[str]],
    warnings: list[str] | None = None,
) -> str:
    if isinstance(result_or_errors, JsonContractValidationResult):
        errors = list(result_or_errors.errors)
        warnings = list(result_or_errors.warnings)
    elif isinstance(result_or_errors, tuple):
        errors = list(result_or_errors[0])
        warnings = list(result_or_errors[1])
    else:
        errors = list(result_or_errors)
        warnings = list(warnings or [])

    parts: list[str] = []
    if errors:
        parts.append("errors: " + "; ".join(errors))
    if warnings:
        parts.append("warnings: " + "; ".join(warnings))
    return " | ".join(parts) if parts else "no validation issues"


def validate_email_classification(payload: Any) -> JsonContractValidationResult:
    contract_name = "email_classification"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "message_id", errors)
    _require_text(mapping, normalized, "sender", errors)
    _optional_text(mapping, normalized, "sender_type")
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "intent", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_dict_value(mapping, normalized, "matched_entities", errors)
    _require_bool(mapping, normalized, "requires_question", errors)
    _require_text(mapping, normalized, "suggested_next_action", errors)
    _require_choice(mapping, normalized, "risk_level", RISK_LEVELS, errors, warnings)

    if not normalized.get("sender_type"):
        warnings.append("sender_type is missing; keep sender classification explicit when available.")
    return _result(contract_name, errors, warnings, normalized)


def validate_record_match(payload: Any) -> JsonContractValidationResult:
    contract_name = "record_match"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "customer_id",
        "site_id",
        "estimate_id",
        "work_order_id",
        "material_call_id",
        "price_request_id",
        "purchase_order_id",
        "invoice_id",
    ):
        _optional_int(mapping, normalized, field_name, errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text(mapping, normalized, "ambiguity_reason")
    if not any(normalized.get(field_name) is not None for field_name in normalized if field_name.endswith("_id")):
        warnings.append("No entity ids were matched in the record_match payload.")
    return _result(contract_name, errors, warnings, normalized)


def validate_extraction_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "extraction_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    if not normalized:
        warnings.append("Extraction payload is empty.")
        return _result(contract_name, errors, warnings, normalized)

    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    for field_name in ("price_request_id", "material_call_id"):
        _optional_int(mapping, normalized, field_name, errors)
    for field_name in ("vendor_quote_number", "vendor_quote_date", "packing_slip_number", "permit_status"):
        _optional_text(mapping, normalized, field_name)
    if normalized.get("vendor_quote_date") and not _looks_like_iso_date(str(normalized["vendor_quote_date"])):
        warnings.append("vendor_quote_date is present but not ISO-formatted.")

    attachment_ids = mapping.get("attachment_ids")
    if attachment_ids is not None:
        if not isinstance(attachment_ids, list):
            errors.append("attachment_ids must be a list when provided.")
        else:
            normalized["attachment_ids"] = [_coerce_int(item) for item in attachment_ids if _coerce_int(item) is not None]

    quoted_line_candidates = mapping.get("quoted_line_candidates")
    if quoted_line_candidates is not None:
        normalized["quoted_line_candidates"] = _validate_quote_line_candidates(
            quoted_line_candidates,
            errors,
            warnings,
            allow_empty=True,
            require_pr_item_id=False,
            field_name="quoted_line_candidates",
        )
        if not normalized["quoted_line_candidates"]:
            warnings.append("No usable quoted_line_candidates were normalized from the extraction payload.")
    else:
        warnings.append("quoted_line_candidates is missing from extraction payload.")

    return _result(contract_name, errors, warnings, normalized)


def validate_receive_quotes_update_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "receive_quotes_update_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_text(mapping, normalized, "vendor_quote_number")
    _optional_text(mapping, normalized, "vendor_quote_date")
    if normalized.get("vendor_quote_date") and not _looks_like_iso_date(str(normalized["vendor_quote_date"])):
        warnings.append("vendor_quote_date is present but not ISO-formatted.")

    normalized["quoted_unit_prices"] = _validate_quote_line_candidates(
        mapping.get("quoted_unit_prices"),
        errors,
        warnings,
        allow_empty=False,
        require_pr_item_id=True,
        field_name="quoted_unit_prices",
    )
    return _result(contract_name, errors, warnings, normalized)


def validate_po_eta_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "po_eta_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "po_number")
    _optional_text(mapping, normalized, "vendor")
    _optional_text(mapping, normalized, "eta_date")
    if normalized.get("eta_date") and not _looks_like_iso_date(str(normalized["eta_date"])):
        warnings.append("eta_date is present but not ISO-formatted.")
    _optional_bool(mapping, normalized, "parts_ready", errors)
    _optional_text(mapping, normalized, "backorder_hint")
    _optional_text(mapping, normalized, "pickup_note")
    _optional_text(mapping, normalized, "pickup_location")
    _optional_text(mapping, normalized, "vendor_message_summary")
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)

    if (
        normalized.get("purchase_order_id") is None
        and not normalized.get("po_number")
        and not normalized.get("vendor_message_summary")
    ):
        warnings.append("Extraction has no PO identifier and no vendor_message_summary.")
    return _result(contract_name, errors, warnings, normalized)


def validate_po_eta_status_observation_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "po_eta_status_observation_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_text(mapping, normalized, "eta_date")
    if normalized.get("eta_date") and not _looks_like_iso_date(str(normalized["eta_date"])):
        warnings.append("eta_date is present but not ISO-formatted.")
    _optional_bool(mapping, normalized, "parts_ready", errors)
    _optional_text(mapping, normalized, "backorder_hint")
    _optional_text(mapping, normalized, "pickup_note")
    _require_text(mapping, normalized, "vendor_message_summary", errors)

    if not any(
        normalized.get(field_name) not in (None, "", False)
        for field_name in ("eta_date", "parts_ready", "backorder_hint", "pickup_note")
    ):
        warnings.append("PO ETA proposal has no ETA/parts-ready/backorder/pickup observation fields set.")
    return _result(contract_name, errors, warnings, normalized)


def validate_packing_slip_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "packing_slip_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "po_number")
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "delivery_date")
    if normalized.get("delivery_date") and not _looks_like_iso_date(str(normalized["delivery_date"])):
        warnings.append("delivery_date is present but not ISO-formatted.")
    _optional_text(mapping, normalized, "vendor")
    _optional_text(mapping, normalized, "shipment_reference")
    _optional_text(mapping, normalized, "source_text_excerpt")
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)

    raw_candidates = mapping.get("line_candidates")
    if raw_candidates is None:
        warnings.append("line_candidates is missing from packing slip extraction.")
        normalized["line_candidates"] = []
    elif not isinstance(raw_candidates, list):
        errors.append("line_candidates must be a list.")
        normalized["line_candidates"] = []
    else:
        normalized_candidates: list[dict[str, Any]] = []
        for index, item in enumerate(raw_candidates):
            if not isinstance(item, dict):
                errors.append(f"line_candidates[{index}] must be an object.")
                continue
            normalized_item = json_safe(item)
            _optional_int(item, normalized_item, "purchase_order_item_id", errors)
            _optional_text(item, normalized_item, "part_number")
            _optional_text(item, normalized_item, "description")
            _optional_text(item, normalized_item, "unit")
            _require_float(
                item,
                normalized_item,
                "received_qty_candidate",
                errors,
                warnings,
                min_value=0.0,
            )
            _optional_float(item, normalized_item, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
            if not normalized_item.get("part_number") and not normalized_item.get("description"):
                warnings.append(
                    f"line_candidates[{index}] has neither part_number nor description for matching."
                )
            normalized_candidates.append(normalized_item)
        normalized["line_candidates"] = normalized_candidates
    return _result(contract_name, errors, warnings, normalized)


def validate_staged_receipt_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "staged_receipt_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "delivery_date")
    if normalized.get("delivery_date") and not _looks_like_iso_date(str(normalized["delivery_date"])):
        warnings.append("delivery_date is present but not ISO-formatted.")
    _optional_text(mapping, normalized, "shipment_reference")
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)

    value = mapping.get("line_candidates")
    if not isinstance(value, list) or not value:
        errors.append("line_candidates must be a non-empty list.")
        normalized["line_candidates"] = []
    else:
        normalized_candidates: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"line_candidates[{index}] must be an object.")
                continue
            normalized_item = json_safe(item)
            _require_int(item, normalized_item, "purchase_order_item_id", errors)
            _optional_text(item, normalized_item, "packing_slip_part_number")
            _optional_text(item, normalized_item, "packing_slip_description")
            _optional_text(item, normalized_item, "po_line_part_number")
            _optional_text(item, normalized_item, "po_line_description")
            _optional_text(item, normalized_item, "vendor_quote_part_number")
            _optional_text(item, normalized_item, "vendor_quote_description")
            _optional_text(item, normalized_item, "internal_material_part_number")
            _require_text(item, normalized_item, "match_outcome", errors)
            _require_text(item, normalized_item, "matching_basis", errors)
            _require_bool(item, normalized_item, "quote_line_checked", errors)
            _require_bool(item, normalized_item, "quote_line_available", errors)
            _optional_text(item, normalized_item, "discrepancy_reason")
            _optional_text_list(item, normalized_item, "uncertainty_notes", errors)
            _require_bool(item, normalized_item, "operator_resolution_required", errors)
            _require_float(
                item,
                normalized_item,
                "received_qty_candidate",
                errors,
                warnings,
                min_value=0.0,
            )
            _optional_float(item, normalized_item, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
            normalized_candidates.append(normalized_item)
        normalized["line_candidates"] = normalized_candidates

    return _result(contract_name, errors, warnings, normalized)


def validate_operator_receipt_resolution_answer(payload: Any) -> JsonContractValidationResult:
    contract_name = "packing_slip_receipt_resolution_answer"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "answer_type", errors)
    if str(normalized.get("answer_type") or "").strip() != "packing_slip_receipt_resolution":
        errors.append("answer_type must be packing_slip_receipt_resolution.")
    _require_int(mapping, normalized, "selected_purchase_order_id", errors)
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "received_date")
    if normalized.get("received_date") and not _looks_like_iso_date(str(normalized["received_date"])):
        warnings.append("received_date is present but not ISO-formatted.")
    _require_text(mapping, normalized, "operator_note", errors)
    _optional_text(mapping, normalized, "answered_by")

    raw_lines = mapping.get("line_resolutions")
    if not isinstance(raw_lines, list) or not raw_lines:
        errors.append("line_resolutions must be a non-empty list.")
        normalized["line_resolutions"] = []
    else:
        normalized_lines: list[dict[str, Any]] = []
        for index, item in enumerate(raw_lines):
            if not isinstance(item, dict):
                errors.append(f"line_resolutions[{index}] must be an object.")
                continue
            normalized_item = json_safe(item)
            _optional_text(item, normalized_item, "packing_slip_line_ref")
            _require_int(item, normalized_item, "selected_purchase_order_item_id", errors)
            _require_float(
                item,
                normalized_item,
                "received_qty",
                errors,
                warnings,
                min_value=0.0,
            )
            qty = normalized_item.get("received_qty")
            if qty is not None and float(qty) <= 0:
                errors.append(f"line_resolutions[{index}].received_qty must be positive.")
            _require_text(item, normalized_item, "resolution_basis", errors)
            _require_text(item, normalized_item, "operator_note", errors)
            _require_bool(item, normalized_item, "allow_overage", errors)
            normalized_lines.append(normalized_item)
        normalized["line_resolutions"] = normalized_lines

    manual_extracted_payload = mapping.get("manual_extracted_payload")
    if manual_extracted_payload is not None:
        extraction_result = validate_packing_slip_extraction(manual_extracted_payload)
        normalized["manual_extracted_payload"] = extraction_result.normalized_json
        if not extraction_result.valid:
            errors.extend(f"manual_extracted_payload: {error}" for error in extraction_result.errors)
        warnings.extend(f"manual_extracted_payload: {warning}" for warning in extraction_result.warnings)

    return _result(contract_name, errors, warnings, normalized)


def validate_operator_po_eta_resolution_answer(payload: Any) -> JsonContractValidationResult:
    contract_name = "po_eta_status_resolution_answer"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "answer_type", errors)
    if str(normalized.get("answer_type") or "").strip() != "po_eta_status_resolution":
        errors.append("answer_type must be po_eta_status_resolution.")
    _require_int(mapping, normalized, "selected_purchase_order_id", errors)
    _require_text(mapping, normalized, "normalized_intent", errors)
    _optional_text(mapping, normalized, "eta_date")
    if normalized.get("eta_date") and not _looks_like_iso_date(str(normalized["eta_date"])):
        warnings.append("eta_date is present but not ISO-formatted.")
    _require_bool(mapping, normalized, "parts_ready", errors)
    backorder_value = mapping.get("backorder_hint")
    if isinstance(backorder_value, bool):
        normalized["backorder_hint"] = backorder_value
    else:
        _optional_text(mapping, normalized, "backorder_hint")
    _optional_text(mapping, normalized, "pickup_note")
    _require_text(mapping, normalized, "vendor_message_summary", errors)
    _require_text(mapping, normalized, "operator_note", errors)
    _require_text(mapping, normalized, "how_i_know", errors)
    _optional_text(mapping, normalized, "answered_by")

    allowed_intents = {
        "PARTS_READY",
        "ETA_UPDATE",
        "BACKORDER_NOTICE",
        "PARTIAL_READY",
        "PICKUP_NOTICE",
        "SHIPPING_NOTICE",
        "POSSIBLE_PO_STATUS_UPDATE",
    }
    normalized_intent = str(normalized.get("normalized_intent") or "").strip().upper()
    if normalized_intent and normalized_intent not in allowed_intents:
        errors.append("normalized_intent is not recognized for po eta/status resolution.")
    if normalized_intent == "ETA_UPDATE" and not normalized.get("eta_date"):
        errors.append("eta_date is required for ETA_UPDATE operator resolution.")
    if normalized_intent in {"PARTS_READY", "PICKUP_NOTICE"} and normalized.get("parts_ready") is not True:
        errors.append("parts_ready must be true for PARTS_READY and PICKUP_NOTICE resolutions.")
    if normalized_intent == "BACKORDER_NOTICE":
        backorder_hint = normalized.get("backorder_hint")
        if backorder_hint in (None, "", False):
            errors.append("backorder_hint is required for BACKORDER_NOTICE resolutions.")
    if normalized_intent == "POSSIBLE_PO_STATUS_UPDATE":
        warnings.append("Resolution remains broad; proposal review should confirm this is not too ambiguous to approve.")

    return _result(contract_name, errors, warnings, normalized)


def validate_operator_vendor_invoice_reconciliation_answer(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_reconciliation_resolution_answer"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "answer_type", errors)
    if str(normalized.get("answer_type") or "").strip() != "vendor_invoice_reconciliation_resolution":
        errors.append("answer_type must be vendor_invoice_reconciliation_resolution.")
    _require_text(mapping, normalized, "resolution_mode", errors)
    _require_text(mapping, normalized, "operator_note", errors)
    _require_text(mapping, normalized, "how_i_know", errors)
    _optional_text(mapping, normalized, "answered_by")
    _optional_int(mapping, normalized, "selected_vendor_id", errors)
    _optional_int(mapping, normalized, "selected_purchase_order_id", errors)
    _optional_int(mapping, normalized, "duplicate_of_vendor_invoice_id", errors)
    _optional_text(mapping, normalized, "invoice_number")
    _optional_text(mapping, normalized, "invoice_date")
    _optional_text(mapping, normalized, "due_date")
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "reconciliation_outcome")
    _optional_text(mapping, normalized, "customer_billing_status")
    _optional_text(mapping, normalized, "expense_category_hint")
    _optional_text(mapping, normalized, "vendor_message_summary")
    _optional_float(mapping, normalized, "invoice_total", errors, warnings, min_value=0.0)
    _optional_float(mapping, normalized, "tax_amount", errors, warnings, min_value=0.0)

    for date_field in ("invoice_date", "due_date"):
        if normalized.get(date_field) and not _looks_like_iso_date(str(normalized[date_field])):
            warnings.append(f"{date_field} is present but not ISO-formatted.")

    receipt_ids = mapping.get("selected_receipt_ids")
    if receipt_ids in (None, ""):
        normalized["selected_receipt_ids"] = []
    elif not isinstance(receipt_ids, list):
        errors.append("selected_receipt_ids must be a list when provided.")
        normalized["selected_receipt_ids"] = []
    else:
        normalized["selected_receipt_ids"] = _normalize_int_list(receipt_ids, "selected_receipt_ids", errors)

    allowed_modes = {"po_backed", "duplicate", "non_po_expense", "field_correction", "unresolved"}
    mode = str(normalized.get("resolution_mode") or "").strip()
    if mode and mode not in allowed_modes:
        errors.append("resolution_mode is not recognized for vendor invoice reconciliation resolution.")

    allowed_customer_billing = {"unknown", "not_invoiced", "draft_invoice_exists", "sent", "paid"}
    customer_billing_status = str(normalized.get("customer_billing_status") or "").strip()
    if customer_billing_status and customer_billing_status not in allowed_customer_billing:
        errors.append("customer_billing_status is not recognized for vendor invoice reconciliation resolution.")

    allowed_po_outcomes = {
        "MATCHED_PO_AND_RECEIPT_CONTEXT",
        "MATCHED_PO_ONLY",
        "MATCHED_PO_BUT_NO_RECEIPT",
        "READY_FOR_OPERATOR_RECONCILIATION_REVIEW",
    }
    reconciliation_outcome = str(normalized.get("reconciliation_outcome") or "").strip()
    if reconciliation_outcome and reconciliation_outcome not in allowed_po_outcomes:
        errors.append("reconciliation_outcome is not recognized for vendor invoice reconciliation resolution.")

    if mode == "po_backed":
        if normalized.get("selected_purchase_order_id") is None:
            errors.append("selected_purchase_order_id is required for po_backed resolution.")
        if not normalized.get("invoice_number"):
            errors.append("invoice_number is required for po_backed resolution.")
        invoice_total = normalized.get("invoice_total")
        if invoice_total is not None and float(invoice_total) <= 0:
            errors.append("invoice_total must be positive when provided for po_backed resolution.")
        if normalized.get("selected_receipt_ids") == [] and reconciliation_outcome == "MATCHED_PO_AND_RECEIPT_CONTEXT":
            errors.append("selected_receipt_ids are required when reconciliation_outcome claims MATCHED_PO_AND_RECEIPT_CONTEXT.")
    elif mode == "duplicate":
        if normalized.get("duplicate_of_vendor_invoice_id") is None:
            errors.append("duplicate_of_vendor_invoice_id is required for duplicate resolution.")
        if not normalized.get("invoice_number"):
            warnings.append("duplicate resolution is missing invoice_number; review proposal context carefully.")
    elif mode == "non_po_expense":
        if normalized.get("selected_vendor_id") is None:
            errors.append("selected_vendor_id is required for non_po_expense resolution.")
        if not normalized.get("invoice_number"):
            errors.append("invoice_number is required for non_po_expense resolution.")
        invoice_total = normalized.get("invoice_total")
        if invoice_total is None or float(invoice_total) <= 0:
            errors.append("invoice_total must be positive for non_po_expense resolution.")
    elif mode == "field_correction":
        if not normalized.get("invoice_number"):
            errors.append("invoice_number is required for field_correction resolution.")
        invoice_total = normalized.get("invoice_total")
        if invoice_total is None or float(invoice_total) <= 0:
            errors.append("invoice_total must be positive for field_correction resolution.")
    elif mode == "unresolved":
        warnings.append("unresolved answer does not support revised proposal creation.")

    return _result(contract_name, errors, warnings, normalized)


def validate_operator_customer_billing_status_resolution_answer(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_billing_status_resolution_answer"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "answer_type", errors)
    if str(normalized.get("answer_type") or "").strip() != "customer_billing_status_resolution":
        errors.append("answer_type must be customer_billing_status_resolution.")
    _require_text(mapping, normalized, "resolution_outcome", errors)
    _require_text(mapping, normalized, "operator_note", errors)
    _require_text(mapping, normalized, "how_i_know", errors)
    _optional_text(mapping, normalized, "answered_by")
    _optional_int(mapping, normalized, "selected_customer_invoice_id", errors)
    _optional_int(mapping, normalized, "selected_work_order_id", errors)

    allowed_outcomes = {
        "selected_customer_invoice",
        "customer_invoice_missing",
        "already_billed",
        "not_billable_to_customer",
        "manual_review_required",
        "non_po_exception",
    }
    outcome = str(normalized.get("resolution_outcome") or "").strip()
    if outcome and outcome not in allowed_outcomes:
        errors.append("resolution_outcome is not recognized for customer billing status resolution.")

    if outcome in {"selected_customer_invoice", "already_billed"} and normalized.get("selected_customer_invoice_id") is None:
        errors.append("selected_customer_invoice_id is required for the selected_customer_invoice and already_billed outcomes.")
    if outcome in {"customer_invoice_missing", "not_billable_to_customer", "manual_review_required", "non_po_exception"} and normalized.get("selected_customer_invoice_id") is not None:
        warnings.append("selected_customer_invoice_id will be ignored for this resolution_outcome.")

    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "vendor_name",
        "vendor_email",
        "invoice_number",
        "invoice_date",
        "due_date",
        "po_number",
        "packing_slip_number",
        "work_order_reference",
        "source_text_excerpt",
        "classification_route",
    ):
        _optional_text(mapping, normalized, field_name)
    for date_field in ("invoice_date", "due_date"):
        if normalized.get(date_field) and not _looks_like_iso_date(str(normalized[date_field])):
            warnings.append(f"{date_field} is present but not ISO-formatted.")
    for amount_field in ("subtotal_amount", "tax_amount", "total_amount"):
        _optional_float(mapping, normalized, amount_field, errors, warnings, min_value=0.0)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text_list(mapping, normalized, "source_snippets", errors)

    line_candidates = mapping.get("line_candidates")
    if line_candidates in (None, ""):
        normalized["line_candidates"] = []
    elif not isinstance(line_candidates, list):
        errors.append("line_candidates must be a list when provided.")
        normalized["line_candidates"] = []
    else:
        normalized_candidates: list[dict[str, Any]] = []
        for index, item in enumerate(line_candidates):
            if not isinstance(item, dict):
                errors.append(f"line_candidates[{index}] must be an object.")
                continue
            normalized_item = json_safe(item)
            _optional_text(item, normalized_item, "part_number")
            _optional_text(item, normalized_item, "description")
            _optional_float(item, normalized_item, "quantity", errors, warnings, min_value=0.0)
            _optional_float(item, normalized_item, "line_amount", errors, warnings, min_value=0.0)
            normalized_candidates.append(normalized_item)
        normalized["line_candidates"] = normalized_candidates

    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in ("vendor_name", "invoice_number", "total_amount", "po_number", "line_candidates")
    ):
        errors.append("Extraction does not contain enough invoice-identifying data.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_intake_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_intake_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _require_text(mapping, normalized, "vendor_name", errors)
    _require_text(mapping, normalized, "invoice_number", errors)
    _optional_int(mapping, normalized, "vendor_id", errors)
    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "invoice_date")
    _optional_text(mapping, normalized, "due_date")
    for date_field in ("invoice_date", "due_date"):
        if normalized.get(date_field) and not _looks_like_iso_date(str(normalized[date_field])):
            warnings.append(f"{date_field} is present but not ISO-formatted.")
    for amount_field in ("subtotal_amount", "tax_amount"):
        _optional_float(mapping, normalized, amount_field, errors, warnings, min_value=0.0)
    _require_float(mapping, normalized, "total_amount", errors, warnings, min_value=0.0)
    _optional_text(mapping, normalized, "po_number")
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "work_order_reference")
    _require_text(mapping, normalized, "match_outcome", errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_int(mapping, normalized, "resolution_source_question_id", errors)
    _optional_bool(mapping, normalized, "operator_resolved", errors)
    _optional_text(mapping, normalized, "resolved_by")
    _optional_text(mapping, normalized, "resolved_at")
    _optional_text(mapping, normalized, "operator_resolution_notes")
    _optional_text_list(mapping, normalized, "original_uncertainty_notes", errors)
    _optional_float(mapping, normalized, "confidence_after_operator_review", errors, warnings, min_value=0.0, max_value=1.0)

    receipt_ids = mapping.get("receipt_ids")
    if receipt_ids in (None, ""):
        normalized["receipt_ids"] = []
    elif not isinstance(receipt_ids, list):
        errors.append("receipt_ids must be a list when provided.")
        normalized["receipt_ids"] = []
    else:
        normalized["receipt_ids"] = _normalize_int_list(receipt_ids, "receipt_ids", errors)

    line_candidates = mapping.get("line_candidates")
    if line_candidates in (None, ""):
        normalized["line_candidates"] = []
    elif not isinstance(line_candidates, list):
        errors.append("line_candidates must be a list when provided.")
        normalized["line_candidates"] = []
    else:
        normalized["line_candidates"] = [json_safe(item) for item in line_candidates]
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_duplicate_review_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_duplicate_review_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "duplicate_of_vendor_invoice_id", errors)
    _optional_text(mapping, normalized, "invoice_number")
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "resolution_mode")
    _optional_text(mapping, normalized, "operator_resolution_notes")
    _optional_text(mapping, normalized, "resolved_by")
    _optional_text(mapping, normalized, "resolved_at")
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text(mapping, normalized, "duplicate_outcome")

    matching_vendor_invoice_ids = mapping.get("matching_vendor_invoice_ids")
    if matching_vendor_invoice_ids in (None, ""):
        normalized["matching_vendor_invoice_ids"] = []
    elif not isinstance(matching_vendor_invoice_ids, list):
        errors.append("matching_vendor_invoice_ids must be a list when provided.")
        normalized["matching_vendor_invoice_ids"] = []
    else:
        normalized["matching_vendor_invoice_ids"] = _normalize_int_list(matching_vendor_invoice_ids, "matching_vendor_invoice_ids", errors)

    matching_receipt_ids = mapping.get("matching_receipt_ids")
    if matching_receipt_ids in (None, ""):
        normalized["matching_receipt_ids"] = []
    elif not isinstance(matching_receipt_ids, list):
        errors.append("matching_receipt_ids must be a list when provided.")
        normalized["matching_receipt_ids"] = []
    else:
        normalized["matching_receipt_ids"] = _normalize_int_list(matching_receipt_ids, "matching_receipt_ids", errors)

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "vendor_invoice_duplicate_review":
        warnings.append("vendor invoice duplicate review proposal has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "vendor_invoice_payables":
        warnings.append("vendor invoice duplicate review proposal has an unexpected workflow.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_non_po_exception_review_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_non_po_exception_review_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "vendor_id", errors)
    _optional_text(mapping, normalized, "invoice_number")
    _optional_float(mapping, normalized, "invoice_total", errors, warnings, min_value=0.0)
    _optional_text(mapping, normalized, "expense_category_hint")
    _optional_text(mapping, normalized, "operator_resolution_notes")
    _optional_text(mapping, normalized, "resolved_by")
    _optional_text(mapping, normalized, "resolved_at")
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "vendor_invoice_non_po_exception_review":
        warnings.append("vendor invoice non-PO exception review proposal has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "vendor_invoice_payables":
        warnings.append("vendor invoice non-PO exception review proposal has an unexpected workflow.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_non_po_expense_review_proposal(payload: Any) -> JsonContractValidationResult:
    return validate_vendor_invoice_non_po_exception_review_proposal(payload)


def validate_vendor_invoice_customer_billing_review_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_customer_billing_review_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "vendor_invoice_id", errors)
    _optional_text(mapping, normalized, "vendor_name")
    _optional_text(mapping, normalized, "vendor_invoice_number")
    _optional_float(mapping, normalized, "vendor_invoice_total", errors, warnings, min_value=0.0)
    _optional_text(mapping, normalized, "vendor_invoice_due_date")
    if normalized.get("vendor_invoice_due_date") and not _looks_like_iso_date(str(normalized["vendor_invoice_due_date"])):
        warnings.append("vendor_invoice_due_date is present but not ISO-formatted.")
    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "purchase_order_number")
    _optional_int(mapping, normalized, "work_order_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    _require_text(mapping, normalized, "customer_billing_status", errors)
    _require_text(mapping, normalized, "recommended_operator_action", errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "cash_flow_flags", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    customer_invoice_ids = mapping.get("customer_invoice_ids")
    if customer_invoice_ids in (None, ""):
        normalized["customer_invoice_ids"] = []
    elif not isinstance(customer_invoice_ids, list):
        errors.append("customer_invoice_ids must be a list when provided.")
        normalized["customer_invoice_ids"] = []
    else:
        normalized["customer_invoice_ids"] = _normalize_int_list(customer_invoice_ids, "customer_invoice_ids", errors)

    allowed_statuses = {
        "CUSTOMER_BILLING_UNKNOWN",
        "NO_CUSTOMER_INVOICE_FOUND",
        "CUSTOMER_INVOICE_DRAFT_EXISTS",
        "CUSTOMER_INVOICE_EXPORTED_NOT_SENT",
        "CUSTOMER_INVOICE_SENT_UNPAID",
        "CUSTOMER_INVOICE_SENT_PAID",
        "CUSTOMER_BILLING_NOT_APPLICABLE",
        "MULTIPLE_CUSTOMER_INVOICES_FOUND",
        "UNSAFE_BILLING_RELATIONSHIP",
    }
    if normalized.get("customer_billing_status") and normalized.get("customer_billing_status") not in allowed_statuses:
        errors.append("customer_billing_status is not recognized for vendor invoice customer billing review.")

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "vendor_invoice_customer_billing_review":
        warnings.append("vendor invoice customer billing review proposal has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "cash_flow_review":
        warnings.append("vendor invoice customer billing review proposal has an unexpected workflow.")
    if normalized.get("requires_approval") is not True:
        warnings.append("vendor invoice customer billing review should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("vendor invoice customer billing review should not be auto-applicable at Level 2.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_billing_status_review_choices_json(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_billing_status_review_choices_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    extracted_fields = mapping.get("extracted_fields")
    if extracted_fields in (None, ""):
        normalized["extracted_fields"] = {}
    elif not isinstance(extracted_fields, dict):
        errors.append("extracted_fields must be an object when provided.")
        normalized["extracted_fields"] = {}
    else:
        normalized["extracted_fields"] = json_safe(extracted_fields)

    for field_name in ("customer_billing_status", "source_record_type", "source_record_id"):
        _require_text(mapping, normalized, field_name, errors)
    _optional_text_list(mapping, normalized, "cash_flow_flags", errors)
    _optional_text_list(mapping, normalized, "suggested_operator_actions", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    for list_field in ("candidate_work_orders", "candidate_customer_invoices", "candidate_customers_sites"):
        value = mapping.get(list_field)
        if value in (None, ""):
            normalized[list_field] = []
        elif not isinstance(value, list):
            errors.append(f"{list_field} must be a list when provided.")
            normalized[list_field] = []
        else:
            normalized[list_field] = [json_safe(item) for item in value]

    for dict_field in ("vendor_invoice_details", "purchase_order_details", "evidence_snapshot"):
        value = mapping.get(dict_field)
        if value in (None, ""):
            normalized[dict_field] = {}
        elif not isinstance(value, dict):
            errors.append(f"{dict_field} must be an object when provided.")
            normalized[dict_field] = {}
        else:
            normalized[dict_field] = json_safe(value)

    allowed_statuses = {
        "CUSTOMER_BILLING_UNKNOWN",
        "NO_CUSTOMER_INVOICE_FOUND",
        "CUSTOMER_INVOICE_DRAFT_EXISTS",
        "CUSTOMER_INVOICE_EXPORTED_NOT_SENT",
        "CUSTOMER_INVOICE_SENT_UNPAID",
        "CUSTOMER_INVOICE_SENT_PAID",
        "CUSTOMER_BILLING_NOT_APPLICABLE",
        "MULTIPLE_CUSTOMER_INVOICES_FOUND",
        "UNSAFE_BILLING_RELATIONSHIP",
    }
    if normalized.get("customer_billing_status") and normalized.get("customer_billing_status") not in allowed_statuses:
        errors.append("customer_billing_status is not recognized for customer billing status review choices.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_billing_status_review_resolution_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_billing_status_review_resolution_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "vendor_invoice_id", errors)
    _optional_text(mapping, normalized, "vendor_name")
    _optional_text(mapping, normalized, "vendor_invoice_number")
    _optional_float(mapping, normalized, "vendor_invoice_total", errors, warnings, min_value=0.0)
    _optional_text(mapping, normalized, "vendor_invoice_due_date")
    if normalized.get("vendor_invoice_due_date") and not _looks_like_iso_date(str(normalized["vendor_invoice_due_date"])):
        warnings.append("vendor_invoice_due_date is present but not ISO-formatted.")
    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "purchase_order_number")
    _optional_int(mapping, normalized, "work_order_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    _require_text(mapping, normalized, "customer_billing_status", errors)
    _require_text(mapping, normalized, "recommended_operator_action", errors)
    _require_int(mapping, normalized, "resolution_source_question_id", errors)
    _require_text(mapping, normalized, "resolution_outcome", errors)
    _require_bool(mapping, normalized, "operator_resolved", errors)
    _require_text(mapping, normalized, "resolved_by", errors)
    _require_text(mapping, normalized, "resolved_at", errors)
    if normalized.get("resolved_at") and not _looks_like_iso_datetime(str(normalized["resolved_at"])):
        warnings.append("resolved_at is present but not ISO-formatted.")
    _require_text(mapping, normalized, "operator_note", errors)
    _require_text(mapping, normalized, "how_i_know", errors)
    _optional_int(mapping, normalized, "selected_customer_invoice_id", errors)
    _optional_int(mapping, normalized, "selected_work_order_id", errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "cash_flow_flags", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    customer_invoice_ids = mapping.get("customer_invoice_ids")
    if customer_invoice_ids in (None, ""):
        normalized["customer_invoice_ids"] = []
    elif not isinstance(customer_invoice_ids, list):
        errors.append("customer_invoice_ids must be a list when provided.")
        normalized["customer_invoice_ids"] = []
    else:
        normalized["customer_invoice_ids"] = _normalize_int_list(customer_invoice_ids, "customer_invoice_ids", errors)

    allowed_statuses = {
        "CUSTOMER_BILLING_UNKNOWN",
        "NO_CUSTOMER_INVOICE_FOUND",
        "CUSTOMER_INVOICE_DRAFT_EXISTS",
        "CUSTOMER_INVOICE_EXPORTED_NOT_SENT",
        "CUSTOMER_INVOICE_SENT_UNPAID",
        "CUSTOMER_INVOICE_SENT_PAID",
        "CUSTOMER_BILLING_NOT_APPLICABLE",
        "MULTIPLE_CUSTOMER_INVOICES_FOUND",
        "UNSAFE_BILLING_RELATIONSHIP",
    }
    if normalized.get("customer_billing_status") and normalized.get("customer_billing_status") not in allowed_statuses:
        errors.append("customer_billing_status is not recognized for customer billing review resolution.")

    allowed_outcomes = {
        "selected_customer_invoice",
        "customer_invoice_missing",
        "already_billed",
        "not_billable_to_customer",
        "manual_review_required",
        "non_po_exception",
    }
    if normalized.get("resolution_outcome") and normalized.get("resolution_outcome") not in allowed_outcomes:
        errors.append("resolution_outcome is not recognized for customer billing review resolution.")

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "customer_billing_status_review_resolution":
        warnings.append("customer billing review resolution proposal has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "cash_flow_review":
        warnings.append("customer billing review resolution proposal has an unexpected workflow.")
    if normalized.get("operator_resolved") is not True:
        warnings.append("customer billing review resolution proposal should record operator_resolved as true.")
    if normalized.get("requires_approval") is not True:
        warnings.append("customer billing review resolution proposal should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("customer billing review resolution proposal should not be auto-applicable at Level 2.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_scheduling_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_scheduling_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "normalized_intent",
        "customer_name",
        "sender_email",
        "phone_number",
        "site_address_text",
        "requested_date_text",
        "requested_time_text",
        "access_instructions",
        "service_description",
        "body_excerpt",
        "classification_route",
    ):
        _optional_text(mapping, normalized, field_name)
    for reference_field in ("estimate_reference", "work_order_reference"):
        _optional_int(mapping, normalized, reference_field, errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text_list(mapping, normalized, "matched_snippets", errors)

    if not normalized.get("normalized_intent"):
        warnings.append("normalized_intent is missing from customer scheduling extraction.")
    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in (
            "customer_name",
            "sender_email",
            "site_address_text",
            "requested_date_text",
            "access_instructions",
            "service_description",
        )
    ):
        errors.append("Extraction does not contain enough scheduling/service inquiry data.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_scheduling_service_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_scheduling_service_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _require_text(mapping, normalized, "normalized_intent", errors)
    for field_name in (
        "customer_name",
        "sender_email",
        "phone_number",
        "site_address_text",
        "requested_date_text",
        "requested_time_text",
        "access_instructions",
        "service_description",
        "match_outcome",
    ):
        _optional_text(mapping, normalized, field_name)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)

    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in ("customer_name", "sender_email", "site_address_text", "service_description")
    ):
        warnings.append("Customer scheduling proposal has sparse customer/site/service context.")
    return _result(contract_name, errors, warnings, normalized)


def validate_lead_intake_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "lead_intake_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "normalized_intent",
        "contact_name",
        "sender_email",
        "phone_number",
        "company_name",
        "site_address_text",
        "project_description",
        "requested_timeline",
        "trade_work_type",
        "source_hint",
        "body_excerpt",
        "classification_route",
    ):
        _optional_text(mapping, normalized, field_name)
    _optional_int(mapping, normalized, "estimate_reference", errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text_list(mapping, normalized, "matched_snippets", errors)
    _optional_text_list(mapping, normalized, "attachment_summary", errors)

    if not normalized.get("normalized_intent"):
        warnings.append("normalized_intent is missing from lead intake extraction.")
    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in (
            "contact_name",
            "sender_email",
            "phone_number",
            "company_name",
            "site_address_text",
            "project_description",
            "trade_work_type",
        )
    ):
        errors.append("Extraction does not contain enough lead-intake detail.")
    return _result(contract_name, errors, warnings, normalized)


def validate_lead_intake_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "lead_intake_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _require_text(mapping, normalized, "normalized_intent", errors)
    for field_name in (
        "contact_name",
        "sender_email",
        "phone_number",
        "company_name",
        "site_address_text",
        "project_description",
        "requested_timeline",
        "trade_work_type",
        "source_hint",
        "match_outcome",
    ):
        _optional_text(mapping, normalized, field_name)
    _optional_bool(mapping, normalized, "phone_call_followup", errors)
    _optional_text_list(mapping, normalized, "attachments_mentioned", errors)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)

    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in (
            "contact_name",
            "sender_email",
            "phone_number",
            "company_name",
            "site_address_text",
            "project_description",
        )
    ):
        warnings.append("Lead intake proposal has sparse contact/site/project context.")
    return _result(contract_name, errors, warnings, normalized)


def validate_lead_intake_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_lead_intake_proposal(payload)
    contract_name = "lead_intake_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)

    if normalized.get("action_type") and normalized.get("action_type") != "lead_intake_observation":
        errors.append("action_type must be lead_intake_observation for lead intake apply.")
    if normalized.get("proposal_type") and normalized.get("proposal_type") != "lead_intake":
        warnings.append("lead_intake apply payload has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "lead_intake":
        warnings.append("lead_intake apply payload has an unexpected workflow.")
    if normalized.get("requires_approval") is not True:
        warnings.append("lead_intake apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("lead_intake apply payload should not be auto-applicable at Level 2.")

    has_contact_signal = any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in ("sender_email", "phone_number", "contact_name", "company_name")
    )
    has_project_signal = any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in ("site_address_text", "project_description", "trade_work_type")
    )
    if not has_contact_signal:
        errors.append("lead intake apply requires at least one contact signal.")
    if not has_project_signal:
        errors.append("lead intake apply requires at least one project/site signal.")
    return _result(contract_name, errors, warnings, normalized)


def validate_lead_intake_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "lead_intake_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "created_customer_id",
        "created_site_id",
        "created_estimate_id",
        "used_existing_customer_id",
        "used_existing_site_id",
        "used_existing_estimate_id",
        "source_inbound_message_id",
    ):
        _optional_int(mapping, normalized, field_name, errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)

    if not any(
        normalized.get(field_name) is not None
        for field_name in (
            "created_customer_id",
            "created_site_id",
            "created_estimate_id",
            "used_existing_customer_id",
            "used_existing_site_id",
            "used_existing_estimate_id",
        )
    ):
        warnings.append("lead intake apply result did not record any created or reused draft intake records.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_intake_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_vendor_invoice_intake_proposal(payload)
    contract_name = "vendor_invoice_intake_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)

    if normalized.get("action_type") and normalized.get("action_type") != "vendor_invoice_intake_observation":
        errors.append("action_type must be vendor_invoice_intake_observation for vendor invoice draft apply.")
    if normalized.get("proposal_type") and normalized.get("proposal_type") != "vendor_invoice_intake":
        warnings.append("vendor invoice draft apply payload has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "vendor_invoice_payables":
        warnings.append("vendor invoice draft apply payload has an unexpected workflow.")
    if normalized.get("target_type") and normalized.get("target_type") != "PurchaseOrder":
        errors.append("vendor invoice draft apply currently requires target_type = PurchaseOrder.")
    if normalized.get("requires_approval") is not True:
        warnings.append("vendor invoice draft apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("vendor invoice draft apply payload should not be auto-applicable at Level 2.")

    match_outcome = str(normalized.get("match_outcome") or "").strip()
    if match_outcome not in {"MATCHED_PO_AND_RECEIPT_CONTEXT", "MATCHED_PO_ONLY"}:
        errors.append(
            "vendor invoice draft apply currently supports only MATCHED_PO_AND_RECEIPT_CONTEXT or MATCHED_PO_ONLY proposals."
        )
    if not normalized.get("po_number"):
        warnings.append("vendor invoice draft apply payload is missing po_number and will rely on target_id.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_intake_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "vendor_invoice_intake_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "created_vendor_invoice_id",
        "reused_existing_vendor_invoice_id",
        "used_existing_vendor_id",
        "matched_purchase_order_id",
        "source_inbound_message_id",
    ):
        _optional_int(mapping, normalized, field_name, errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)

    if normalized.get("created_vendor_invoice_id") is None and normalized.get("reused_existing_vendor_invoice_id") is None:
        warnings.append("vendor invoice draft apply result did not record a created or reused draft invoice.")
    if normalized.get("matched_purchase_order_id") is None:
        errors.append("vendor invoice draft apply result must record matched_purchase_order_id.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_draft_request(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_invoice_draft_request"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "work_order_id", errors)
    _require_text(mapping, normalized, "created_by", errors)
    _require_text(mapping, normalized, "source_tag", errors)
    if normalized.get("work_order_id") is not None and int(normalized["work_order_id"]) <= 0:
        errors.append("work_order_id must be a positive integer.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_invoice_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_customer_invoice_id", errors)
    _optional_int(mapping, normalized, "used_existing_customer_invoice_id", errors)
    _require_text(mapping, normalized, "invoice_number", errors)
    _require_text(mapping, normalized, "status", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_work_order_id", errors)

    if (
        normalized.get("created_customer_invoice_id") is None
        and normalized.get("used_existing_customer_invoice_id") is None
    ):
        errors.append("customer invoice draft result must record either a created or reused draft invoice id.")
    if normalized.get("created_customer_invoice_id") is not None and normalized.get("used_existing_customer_invoice_id") is not None:
        errors.append("customer invoice draft result cannot mark the invoice as both created and reused.")
    if normalized.get("status") and normalized.get("status") != "Draft":
        errors.append("customer invoice draft wrapper must return status = Draft.")
    return _result(contract_name, errors, warnings, normalized)


def validate_work_order_draft_request(payload: Any) -> JsonContractValidationResult:
    contract_name = "work_order_draft_request"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "estimate_id", errors)
    _require_text(mapping, normalized, "desired_status", errors)
    _require_text(mapping, normalized, "created_by", errors)
    _require_text(mapping, normalized, "source_tag", errors)
    if normalized.get("estimate_id") is not None and int(normalized["estimate_id"]) <= 0:
        errors.append("estimate_id must be a positive integer.")
    desired_status = str(normalized.get("desired_status") or "").strip()
    if desired_status and desired_status not in {"Draft", "PendingStart"}:
        errors.append("desired_status must be Draft or PendingStart.")
    return _result(contract_name, errors, warnings, normalized)


def validate_work_order_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "work_order_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_work_order_id", errors)
    _optional_int(mapping, normalized, "used_existing_work_order_id", errors)
    _require_text(mapping, normalized, "work_order_status", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_estimate_id", errors)

    if normalized.get("created_work_order_id") is None and normalized.get("used_existing_work_order_id") is None:
        errors.append("work order draft result must record either a created or reused draft work order id.")
    if normalized.get("created_work_order_id") is not None and normalized.get("used_existing_work_order_id") is not None:
        errors.append("work order draft result cannot mark the work order as both created and reused.")
    if normalized.get("work_order_status") and normalized.get("work_order_status") not in {"Draft", "PendingStart"}:
        errors.append("work order draft wrapper must return work_order_status = Draft or PendingStart.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_acceptance_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "estimate_acceptance_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_work_order_id", errors)
    _optional_int(mapping, normalized, "used_existing_work_order_id", errors)
    _require_text(mapping, normalized, "job_status", errors)
    _require_bool(mapping, normalized, "is_approved", errors)
    _require_int(mapping, normalized, "source_estimate_id", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)

    if normalized.get("created_work_order_id") is None and normalized.get("used_existing_work_order_id") is None:
        errors.append("estimate acceptance apply result must record either a created or reused draft work order id.")
    if (
        normalized.get("created_work_order_id") is not None
        and normalized.get("used_existing_work_order_id") is not None
    ):
        errors.append("estimate acceptance apply result cannot mark the work order as both created and reused.")
    if normalized.get("job_status") and normalized.get("job_status") not in {"Draft", "PendingStart"}:
        errors.append("estimate acceptance apply result must keep job_status = Draft or PendingStart.")
    if normalized.get("is_approved") is not None and bool(normalized.get("is_approved")):
        errors.append("estimate acceptance apply result must keep is_approved = false.")
    return _result(contract_name, errors, warnings, normalized)


def validate_po_eta_status_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "po_eta_status_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_observation_id", errors)
    _optional_int(mapping, normalized, "used_existing_observation_id", errors)
    _require_int(mapping, normalized, "updated_purchase_order_id", errors)
    _optional_text(mapping, normalized, "eta_date")
    if normalized.get("eta_date") and not _looks_like_iso_date(str(normalized["eta_date"])):
        warnings.append("eta_date is present but not ISO-formatted.")
    _require_text(mapping, normalized, "normalized_intent", errors)
    _require_text(mapping, normalized, "status", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)

    if normalized.get("created_observation_id") is None and normalized.get("used_existing_observation_id") is None:
        errors.append("po eta/status apply result must record either a created or reused observation id.")
    if (
        normalized.get("created_observation_id") is not None
        and normalized.get("used_existing_observation_id") is not None
    ):
        errors.append("po eta/status apply result cannot mark the observation as both created and reused.")
    if normalized.get("status") and normalized.get("status") != "Recorded":
        errors.append("po eta/status apply result must keep status = Recorded.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_due_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_invoice_due_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_invoice_id", errors)
    _optional_int(mapping, normalized, "used_existing_invoice_id", errors)
    _require_text(mapping, normalized, "invoice_number", errors)
    _require_text(mapping, normalized, "invoice_status", errors)
    _require_int(mapping, normalized, "source_work_order_id", errors)
    _optional_int(mapping, normalized, "created_memory_id", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)

    if normalized.get("created_invoice_id") is None and normalized.get("used_existing_invoice_id") is None:
        errors.append("customer invoice due apply result must record either a created or reused invoice id.")
    if normalized.get("created_invoice_id") is not None and normalized.get("used_existing_invoice_id") is not None:
        errors.append("customer invoice due apply result cannot mark the invoice as both created and reused.")
    if normalized.get("invoice_status") and normalized.get("invoice_status") != "Draft":
        errors.append("customer invoice due apply result must keep invoice_status = Draft.")
    if normalized.get("created_memory_id") not in (None, 0):
        warnings.append("created_memory_id is recorded, but customer invoice due apply should not rely on a separate memory row.")
    return _result(contract_name, errors, warnings, normalized)


def validate_po_eta_status_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_po_eta_status_observation_proposal(payload)
    contract_name = "po_eta_status_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "workflow")
    _optional_text(mapping, normalized, "normalized_intent")
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_bool(mapping, normalized, "requires_approval", errors)
    _optional_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    if normalized.get("purchase_order_id") is None and normalized.get("target_id") is not None:
        normalized["purchase_order_id"] = normalized.get("target_id")

    if not normalized.get("normalized_intent"):
        normalized["normalized_intent"] = _derive_po_eta_normalized_intent(normalized)

    if normalized.get("action_type") and normalized.get("action_type") != "po_eta_status_observation":
        errors.append("action_type must be po_eta_status_observation for this apply payload.")
    if normalized.get("workflow") and normalized.get("workflow") != "po_receiving":
        warnings.append("po eta/status apply payload has an unexpected workflow.")
    if normalized.get("target_type") and normalized.get("target_type") != "PurchaseOrder":
        errors.append("po eta/status apply currently requires target_type = PurchaseOrder.")
    if normalized.get("purchase_order_id") is None:
        errors.append("purchase_order_id or target_id is required for po eta/status apply.")
    if normalized.get("requires_approval") is not True:
        warnings.append("po eta/status apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("po eta/status apply payload should not be auto-applicable at Level 2.")

    normalized_intent = str(normalized.get("normalized_intent") or "").strip().upper()
    allowed_intents = {
        "PARTS_READY",
        "ETA_UPDATE",
        "BACKORDER_NOTICE",
        "PARTIAL_READY",
        "PICKUP_NOTICE",
        "SHIPPING_NOTICE",
        "POSSIBLE_PO_STATUS_UPDATE",
    }
    if normalized_intent and normalized_intent not in allowed_intents:
        errors.append("normalized_intent is not recognized for po eta/status apply.")
    if normalized_intent == "POSSIBLE_PO_STATUS_UPDATE":
        warnings.append("po eta/status apply payload remains ambiguous and should be blocked by the apply wrapper.")
    if not any(
        normalized.get(field_name) not in (None, "", False)
        for field_name in ("eta_date", "parts_ready", "backorder_hint", "pickup_note", "vendor_message_summary")
    ):
        errors.append("po eta/status apply requires at least one ETA/status observation field.")

    for forbidden_field in (
        "quantity_received",
        "quantity_received_delta",
        "receipt_id",
        "receipt_item_id",
        "received_lines",
        "receipt_lines",
        "receive_goods",
    ):
        if mapping.get(forbidden_field) not in (None, "", [], False):
            errors.append(f"{forbidden_field} is not allowed in po eta/status apply payload.")
    return _result(contract_name, errors, warnings, normalized)


def validate_staged_receipt_apply_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "staged_receipt_apply_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_int(mapping, normalized, "created_receipt_id", errors)
    _optional_int(mapping, normalized, "used_existing_receipt_id", errors)
    _require_int(mapping, normalized, "updated_purchase_order_id", errors)
    _optional_text(mapping, normalized, "purchase_order_status")
    _require_text(mapping, normalized, "packing_slip_number", errors)
    _require_text(mapping, normalized, "receive_date", errors)
    if normalized.get("receive_date") and not _looks_like_iso_date(str(normalized["receive_date"])):
        errors.append("receive_date must be ISO-formatted.")
    _require_int(mapping, normalized, "received_item_count", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _require_int(mapping, normalized, "source_proposal_id", errors)
    _require_text(mapping, normalized, "applied_by", errors)

    if normalized.get("created_receipt_id") is None and normalized.get("used_existing_receipt_id") is None:
        errors.append("staged receipt apply result must record either a created or reused receipt id.")
    if (
        normalized.get("created_receipt_id") is not None
        and normalized.get("used_existing_receipt_id") is not None
    ):
        errors.append("staged receipt apply result cannot mark the receipt as both created and reused.")
    if _coerce_int(normalized.get("received_item_count")) is not None and int(normalized["received_item_count"]) <= 0:
        errors.append("received_item_count must be positive.")
    return _result(contract_name, errors, warnings, normalized)


def validate_staged_receipt_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_staged_receipt_proposal(payload)
    contract_name = "staged_receipt_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "purchase_order_id", errors)
    _optional_text(mapping, normalized, "workflow")
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)

    if normalized.get("purchase_order_id") is None and normalized.get("target_id") is not None:
        normalized["purchase_order_id"] = normalized.get("target_id")

    if normalized.get("action_type") and normalized.get("action_type") != "staged_receipt_observation":
        errors.append("action_type must be staged_receipt_observation for this apply payload.")
    if normalized.get("workflow") and normalized.get("workflow") != "receive_goods":
        warnings.append("staged receipt apply payload has an unexpected workflow.")
    if normalized.get("target_type") and normalized.get("target_type") != "PurchaseOrder":
        errors.append("staged receipt apply currently requires target_type = PurchaseOrder.")
    if normalized.get("purchase_order_id") is None:
        errors.append("purchase_order_id or target_id is required for staged receipt apply.")
    if normalized.get("requires_approval") is not True:
        warnings.append("staged receipt apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("staged receipt apply payload should not be auto-applicable at Level 2.")

    if not str(normalized.get("packing_slip_number") or normalized.get("shipment_reference") or "").strip():
        errors.append("staged receipt apply requires a packing_slip_number or shipment_reference.")
    if not str(normalized.get("delivery_date") or "").strip():
        errors.append("staged receipt apply requires delivery_date.")

    line_candidates = normalized.get("line_candidates")
    if not isinstance(line_candidates, list) or not line_candidates:
        errors.append("staged receipt apply requires one or more normalized line_candidates.")
    else:
        allowed_outcomes = {
            "MATCHED_PO_LINE_EXACT",
            "MATCHED_VENDOR_QUOTE_LINE",
            "MATCHED_PO_CONTEXT_NO_QUOTE",
            "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT",
            "OPERATOR_CONFIRMED_MATCH",
            "MATERIAL_CATALOGUE_ONLY_MATCH",
            "VENDOR_QUOTE_PACKING_SLIP_MISMATCH",
            "PO_PACKING_SLIP_MISMATCH",
            "AMBIGUOUS_LINE_MATCH",
            "NO_MATCH",
        }
        for index, item in enumerate(line_candidates):
            if not isinstance(item, dict):
                errors.append(f"line_candidates[{index}] must be an object.")
                continue
            match_outcome = str(item.get("match_outcome") or "").strip()
            if match_outcome and match_outcome not in allowed_outcomes:
                errors.append(f"line_candidates[{index}] has unsupported match_outcome '{match_outcome}'.")
            if item.get("operator_resolution_required") is True:
                warnings.append(
                    f"line_candidates[{index}] still requires operator resolution and should be blocked by the apply wrapper."
                )
            if match_outcome == "OPERATOR_CONFIRMED_MATCH":
                line_note = str(item.get("operator_resolution_note") or "").strip()
                top_level_note = str(normalized.get("operator_resolution_notes") or "").strip()
                if not line_note and not top_level_note:
                    errors.append(
                        f"line_candidates[{index}] operator-confirmed matches require operator resolution notes."
                    )
            qty = _coerce_float(item.get("received_qty_candidate"))
            if qty is not None and qty <= 0:
                errors.append(f"line_candidates[{index}] must have positive received_qty_candidate.")

    for forbidden_field in (
        "quantity_received",
        "quantity_received_delta",
        "receipt_id",
        "receipt_item_id",
        "received_lines",
        "receipt_lines",
        "receive_goods",
        "mark_received",
    ):
        if mapping.get(forbidden_field) not in (None, "", [], False):
            errors.append(f"{forbidden_field} is not allowed in staged receipt apply payload.")

    return _result(contract_name, errors, warnings, normalized)


def validate_workflow_obligation_proposal_evidence(payload: Any) -> JsonContractValidationResult:
    contract_name = "workflow_obligation_proposal_evidence"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "obligation_id", errors)
    _require_text(mapping, normalized, "source_record_type", errors)
    _require_text(mapping, normalized, "source_record_id", errors)
    _require_text(mapping, normalized, "workflow_type", errors)
    _require_text(mapping, normalized, "expected_event_type", errors)
    _require_text(mapping, normalized, "expected_by", errors)
    _require_int(mapping, normalized, "days_overdue", errors)
    _optional_text(mapping, normalized, "satisfaction_reason")
    if "satisfaction_evidence" in mapping:
        normalized["satisfaction_evidence"] = json_safe(mapping.get("satisfaction_evidence"))
    else:
        warnings.append("workflow obligation proposal evidence has no satisfaction_evidence object.")
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_followup_extraction(payload: Any) -> JsonContractValidationResult:
    contract_name = "estimate_followup_extraction"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "normalized_intent",
        "customer_name",
        "contact_name",
        "company_name",
        "sender_email",
        "phone_number",
        "site_address_text",
        "customer_reply_summary",
        "requested_change_or_question",
        "body_excerpt",
        "classification_route",
    ):
        _optional_text(mapping, normalized, field_name)
    _optional_int(mapping, normalized, "estimate_reference", errors)
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text_list(mapping, normalized, "matched_snippets", errors)
    _optional_text_list(mapping, normalized, "attachment_summary", errors)

    estimate_reference_ids = mapping.get("estimate_reference_ids")
    if estimate_reference_ids in (None, ""):
        normalized["estimate_reference_ids"] = []
    else:
        normalized["estimate_reference_ids"] = _normalize_int_list(
            estimate_reference_ids,
            "estimate_reference_ids",
            errors,
        )

    if not normalized.get("normalized_intent"):
        warnings.append("normalized_intent is missing from estimate follow-up extraction.")
    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in (
            "customer_name",
            "sender_email",
            "site_address_text",
            "customer_reply_summary",
            "requested_change_or_question",
            "estimate_reference_ids",
        )
    ):
        errors.append("Extraction does not contain enough estimate follow-up detail.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_followup_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "estimate_followup_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "proposal_type", errors)
    _require_text(mapping, normalized, "workflow", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _require_text(mapping, normalized, "normalized_intent", errors)
    for field_name in (
        "customer_name",
        "contact_name",
        "company_name",
        "sender_email",
        "phone_number",
        "estimate_reference",
        "site_address_text",
        "customer_reply_summary",
        "requested_change_or_question",
        "match_outcome",
    ):
        _optional_text(mapping, normalized, field_name)
    for field_name in ("matched_customer_id", "matched_site_id", "matched_estimate_id"):
        _optional_int(mapping, normalized, field_name, errors)
    _optional_text_list(mapping, normalized, "urgency_hints", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)

    if normalized.get("target_type") == "Estimate" and normalized.get("matched_estimate_id") is None:
        warnings.append("Estimate follow-up proposal targets Estimate but matched_estimate_id is missing.")
    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in (
            "customer_name",
            "sender_email",
            "estimate_reference",
            "customer_reply_summary",
            "requested_change_or_question",
        )
    ):
        warnings.append("Estimate follow-up proposal has sparse customer/reply context.")
    return _result(contract_name, errors, warnings, normalized)


def validate_workflow_obligation_overdue_proposal(payload: Any) -> JsonContractValidationResult:
    contract_name = "workflow_obligation_overdue_proposal"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    for field_name in (
        "proposal_type",
        "workflow",
        "target_type",
        "source_record_type",
        "expected_event_type",
        "expected_by",
        "recommended_action",
    ):
        _require_text(mapping, normalized, field_name, errors)
    for field_name in ("target_id", "source_record_id", "obligation_id"):
        _require_int(mapping, normalized, field_name, errors)
    _require_int(mapping, normalized, "days_overdue", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    if "evidence" in mapping:
        normalized["evidence"] = json_safe(mapping.get("evidence"))
    else:
        warnings.append("workflow obligation overdue proposal has no evidence object.")
    if normalized.get("days_overdue") is not None and int(normalized["days_overdue"]) < 0:
        errors.append("days_overdue must be zero or greater.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_followup_due_observation(payload: Any) -> JsonContractValidationResult:
    base = validate_workflow_obligation_overdue_proposal(payload)
    contract_name = "estimate_followup_due_observation"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _optional_int(mapping, normalized, "estimate_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    for field_name in ("estimate_status", "sent_date", "customer_name", "site_name", "action_type"):
        _optional_text(mapping, normalized, field_name)

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "estimate_followup_due_observation":
        warnings.append("estimate follow-up due observation payload has an unexpected proposal_type.")
    if normalized.get("source_record_type") and normalized.get("source_record_type") != "Estimate":
        errors.append("source_record_type must be Estimate for estimate_followup_due_observation.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "draft_followup_email":
        warnings.append("estimate follow-up due observation recommended_action is not draft_followup_email.")
    if normalized.get("estimate_id") is None:
        warnings.append("estimate_followup_due_observation is missing estimate_id.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_followup_due_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_estimate_followup_due_observation(payload)
    contract_name = "estimate_followup_due_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)

    evidence = normalized.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        errors.append("evidence is required for estimate_followup_due apply.")

    if normalized.get("action_type") and normalized.get("action_type") != "estimate_followup_due_observation":
        errors.append("action_type must be estimate_followup_due_observation for this apply payload.")
    if normalized.get("estimate_id") is None and normalized.get("source_record_id") is None:
        errors.append("estimate_id or source_record_id is required for estimate follow-up draft apply.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "draft_followup_email":
        warnings.append("estimate follow-up draft apply payload recommended_action is not draft_followup_email.")
    return _result(contract_name, errors, warnings, normalized)


def validate_outbound_followup_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "outbound_followup_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "outbound_message_log_id", errors)
    _require_text(mapping, normalized, "entity_type", errors)
    _require_int(mapping, normalized, "entity_id", errors)
    _require_text(mapping, normalized, "source_action_type", errors)
    _require_text(mapping, normalized, "draft_status", errors)
    _require_text(mapping, normalized, "recipient_email", errors)
    _require_text(mapping, normalized, "subject", errors)
    _require_text(mapping, normalized, "body", errors)
    _optional_int(mapping, normalized, "estimate_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    _optional_int(mapping, normalized, "proposal_id", errors)
    _optional_int(mapping, normalized, "obligation_id", errors)
    _optional_text(mapping, normalized, "created_by")
    _optional_text(mapping, normalized, "created_at")
    if normalized.get("created_at") and not str(normalized["created_at"]).strip():
        warnings.append("created_at is blank.")
    if normalized.get("draft_status") and normalized.get("draft_status") != "Prepared":
        warnings.append("Outbound follow-up draft result uses a non-standard draft_status for this slice.")
    if normalized.get("entity_type") and normalized.get("entity_type") != "AutomationProposal":
        warnings.append("Outbound follow-up draft result entity_type is not AutomationProposal.")
    if normalized.get("source_action_type") and normalized.get("source_action_type") != "estimate_followup_due_observation":
        errors.append("source_action_type must be estimate_followup_due_observation.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_request_info_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_request_info_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "outbound_message_log_id", errors)
    _require_text(mapping, normalized, "entity_type", errors)
    _require_int(mapping, normalized, "entity_id", errors)
    _require_text(mapping, normalized, "draft_status", errors)
    _require_text(mapping, normalized, "recipient_email", errors)
    _require_text(mapping, normalized, "subject", errors)
    _require_text(mapping, normalized, "body", errors)
    _optional_int(mapping, normalized, "question_id", errors)
    _optional_int(mapping, normalized, "proposal_id", errors)
    _optional_text(mapping, normalized, "source_question_type")
    _optional_text(mapping, normalized, "source_action_type")
    _optional_text(mapping, normalized, "created_by")
    _optional_text(mapping, normalized, "created_at")

    supported_question_types = {
        "lead_intake_review_required",
        "missing_contact_detail",
        "missing_project_detail",
        "customer_scheduling_service_inquiry",
        "customer_billing_status_review",
        "inbound_routing_review",
    }
    supported_action_types = {
        "lead_intake_observation",
        "customer_scheduling_service_inquiry_observation",
    }
    if normalized.get("draft_status") and normalized.get("draft_status") != "Prepared":
        errors.append("Customer request-info draft result must be Prepared.")
    if normalized.get("entity_type") == "AutomationQuestion":
        if not normalized.get("source_question_type"):
            errors.append("source_question_type is required for AutomationQuestion-backed CustomerRequestInfo:draft.")
        elif normalized.get("source_question_type") not in supported_question_types:
            errors.append("source_question_type is not supported for CustomerRequestInfo:draft.")
    elif normalized.get("entity_type") == "AutomationProposal":
        if not normalized.get("source_action_type"):
            errors.append("source_action_type is required for AutomationProposal-backed CustomerRequestInfo:draft.")
        elif normalized.get("source_action_type") not in supported_action_types:
            errors.append("source_action_type is not supported for CustomerRequestInfo:draft.")
    elif normalized.get("entity_type"):
        errors.append("Customer request-info draft result entity_type must be AutomationQuestion or AutomationProposal.")
    if normalized.get("created_at") and not str(normalized["created_at"]).strip():
        warnings.append("created_at is blank.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_scheduling_reply_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "customer_scheduling_reply_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "outbound_message_log_id", errors)
    _require_text(mapping, normalized, "entity_type", errors)
    _require_int(mapping, normalized, "entity_id", errors)
    _require_text(mapping, normalized, "draft_status", errors)
    _require_text(mapping, normalized, "recipient_email", errors)
    _require_text(mapping, normalized, "subject", errors)
    _require_text(mapping, normalized, "body", errors)
    _require_text(mapping, normalized, "reply_intent", errors)
    _require_bool(mapping, normalized, "confirmed_schedule_time_supported", errors)
    _optional_int(mapping, normalized, "question_id", errors)
    _optional_int(mapping, normalized, "proposal_id", errors)
    _optional_text(mapping, normalized, "source_question_type")
    _optional_text(mapping, normalized, "source_action_type")
    _optional_text(mapping, normalized, "created_by")
    _optional_text(mapping, normalized, "created_at")

    supported_question_types = {
        "customer_scheduling_service_inquiry",
        "customer_scheduling_service_reply_required",
        "urgent_service_review",
        "customer_disambiguation",
        "site_disambiguation",
        "callback_request",
        "access_instruction_review",
        "schedule_confirmation_review",
        "reschedule_request_review",
        "crew_eta_question_review",
        "inbound_routing_review",
    }
    supported_action_types = {
        "customer_scheduling_service_observation",
        "customer_scheduling_service_inquiry",
        "customer_scheduling_service_inquiry_observation",
    }
    supported_reply_intents = {
        "acknowledgement",
        "access_instructions",
        "callback_request",
        "reschedule_request",
        "crew_eta_question",
        "schedule_confirmation",
    }
    if normalized.get("draft_status") and normalized.get("draft_status") != "Prepared":
        errors.append("Customer scheduling/service reply draft result must be Prepared.")
    if normalized.get("reply_intent") and normalized.get("reply_intent") not in supported_reply_intents:
        errors.append("reply_intent is not supported for CustomerSchedulingServiceReply:draft.")
    if normalized.get("entity_type") == "AutomationQuestion":
        if not normalized.get("source_question_type"):
            errors.append("source_question_type is required for AutomationQuestion-backed CustomerSchedulingServiceReply:draft.")
        elif normalized.get("source_question_type") not in supported_question_types:
            errors.append("source_question_type is not supported for CustomerSchedulingServiceReply:draft.")
    elif normalized.get("entity_type") == "AutomationProposal":
        if not normalized.get("source_action_type"):
            errors.append("source_action_type is required for AutomationProposal-backed CustomerSchedulingServiceReply:draft.")
        elif normalized.get("source_action_type") not in supported_action_types:
            errors.append("source_action_type is not supported for CustomerSchedulingServiceReply:draft.")
    elif normalized.get("entity_type"):
        errors.append("Customer scheduling/service reply draft result entity_type must be AutomationQuestion or AutomationProposal.")
    if normalized.get("created_at") and not str(normalized["created_at"]).strip():
        warnings.append("created_at is blank.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_customer_reply_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "estimate_customer_reply_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "outbound_message_log_id", errors)
    _require_text(mapping, normalized, "entity_type", errors)
    _require_int(mapping, normalized, "entity_id", errors)
    _require_text(mapping, normalized, "draft_status", errors)
    _require_text(mapping, normalized, "recipient_email", errors)
    _require_text(mapping, normalized, "subject", errors)
    _require_text(mapping, normalized, "body", errors)
    _require_text(mapping, normalized, "reply_intent", errors)
    _require_bool(mapping, normalized, "no_revised_estimate_document", errors)
    _optional_int(mapping, normalized, "question_id", errors)
    _optional_int(mapping, normalized, "proposal_id", errors)
    _optional_text(mapping, normalized, "source_question_type")
    _optional_text(mapping, normalized, "source_action_type")
    _optional_text(mapping, normalized, "created_by")
    _optional_text(mapping, normalized, "created_at")

    supported_question_types = {
        "estimate_follow_up_or_customer_reply",
        "estimate_followup_review_required",
        "estimate_disambiguation",
        "reply_required_review",
        "existing_workflow_reply_review",
        "urgent_customer_reply_review",
        "estimate_question_review",
        "estimate_revision_review",
    }
    supported_action_types = {
        "estimate_revision_observation",
        "estimate_question_observation",
        "estimate_follow_up_or_customer_reply",
        "estimate_reply_observation",
    }
    supported_reply_intents = {
        "estimate_question_acknowledgement",
        "revision_request_acknowledgement",
        "clarification_request",
        "scope_change_caution",
    }
    if normalized.get("draft_status") and normalized.get("draft_status") != "Prepared":
        errors.append("Estimate customer reply draft result must be Prepared.")
    if normalized.get("reply_intent") and normalized.get("reply_intent") not in supported_reply_intents:
        errors.append("reply_intent is not supported for EstimateCustomerReply:draft.")
    if normalized.get("no_revised_estimate_document") is not True:
        errors.append("no_revised_estimate_document must be true for EstimateCustomerReply:draft.")
    if normalized.get("entity_type") == "AutomationQuestion":
        if not normalized.get("source_question_type"):
            errors.append("source_question_type is required for AutomationQuestion-backed EstimateCustomerReply:draft.")
        elif normalized.get("source_question_type") not in supported_question_types:
            errors.append("source_question_type is not supported for EstimateCustomerReply:draft.")
    elif normalized.get("entity_type") == "AutomationProposal":
        if not normalized.get("source_action_type"):
            errors.append("source_action_type is required for AutomationProposal-backed EstimateCustomerReply:draft.")
        elif normalized.get("source_action_type") not in supported_action_types:
            errors.append("source_action_type is not supported for EstimateCustomerReply:draft.")
    elif normalized.get("entity_type"):
        errors.append("Estimate customer reply draft result entity_type must be AutomationQuestion or AutomationProposal.")
    if normalized.get("created_at") and not str(normalized["created_at"]).strip():
        warnings.append("created_at is blank.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_hold_off_ack_draft_result(payload: Any) -> JsonContractValidationResult:
    contract_name = "estimate_hold_off_ack_draft_result"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "outbound_message_log_id", errors)
    _require_text(mapping, normalized, "entity_type", errors)
    _require_int(mapping, normalized, "entity_id", errors)
    _require_text(mapping, normalized, "draft_status", errors)
    _require_text(mapping, normalized, "recipient_email", errors)
    _require_text(mapping, normalized, "subject", errors)
    _require_text(mapping, normalized, "body", errors)
    _require_text(mapping, normalized, "reply_intent", errors)
    _require_bool(mapping, normalized, "no_estimate_status_mutation", errors)
    _optional_int(mapping, normalized, "question_id", errors)
    _optional_int(mapping, normalized, "proposal_id", errors)
    _optional_text(mapping, normalized, "source_question_type")
    _optional_text(mapping, normalized, "source_action_type")
    _optional_text(mapping, normalized, "created_by")
    _optional_text(mapping, normalized, "created_at")

    supported_question_types = {
        "estimate_follow_up_or_customer_reply",
        "estimate_disambiguation",
        "reply_required_review",
        "existing_workflow_reply_review",
        "urgent_customer_reply_review",
        "estimate_rejection_review",
        "estimate_hold_off_review",
    }
    supported_action_types = {
        "estimate_rejection_observation",
        "estimate_hold_off_observation",
        "estimate_follow_up_or_customer_reply",
    }
    supported_reply_intents = {
        "estimate_rejected_or_went_elsewhere",
        "estimate_hold_off",
        "too_expensive_no_revision",
        "deferred_until_later",
    }
    if normalized.get("draft_status") and normalized.get("draft_status") != "Prepared":
        errors.append("Estimate hold-off acknowledgement draft result must be Prepared.")
    if normalized.get("reply_intent") and normalized.get("reply_intent") not in supported_reply_intents:
        errors.append("reply_intent is not supported for EstimateHoldOffAcknowledgement:draft.")
    if normalized.get("no_estimate_status_mutation") is not True:
        errors.append("no_estimate_status_mutation must be true for EstimateHoldOffAcknowledgement:draft.")
    if normalized.get("entity_type") == "AutomationQuestion":
        if not normalized.get("source_question_type"):
            errors.append("source_question_type is required for AutomationQuestion-backed EstimateHoldOffAcknowledgement:draft.")
        elif normalized.get("source_question_type") not in supported_question_types:
            errors.append("source_question_type is not supported for EstimateHoldOffAcknowledgement:draft.")
    elif normalized.get("entity_type") == "AutomationProposal":
        if not normalized.get("source_action_type"):
            errors.append("source_action_type is required for AutomationProposal-backed EstimateHoldOffAcknowledgement:draft.")
        elif normalized.get("source_action_type") not in supported_action_types:
            errors.append("source_action_type is not supported for EstimateHoldOffAcknowledgement:draft.")
    elif normalized.get("entity_type"):
        errors.append("Estimate hold-off acknowledgement draft result entity_type must be AutomationQuestion or AutomationProposal.")
    if normalized.get("created_at") and not str(normalized["created_at"]).strip():
        warnings.append("created_at is blank.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_due_observation(payload: Any) -> JsonContractValidationResult:
    base = validate_workflow_obligation_overdue_proposal(payload)
    contract_name = "customer_invoice_due_observation"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _optional_int(mapping, normalized, "work_order_id", errors)
    _optional_int(mapping, normalized, "source_estimate_id", errors)
    _optional_bool(mapping, normalized, "is_closed", errors)
    for field_name in ("job_status", "customer_name", "site_name", "action_type"):
        _optional_text(mapping, normalized, field_name)

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "customer_invoice_due_observation":
        warnings.append("customer invoice due observation payload has an unexpected proposal_type.")
    if normalized.get("source_record_type") and normalized.get("source_record_type") != "WorkOrder":
        errors.append("source_record_type must be WorkOrder for customer_invoice_due_observation.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "create_customer_invoice_draft":
        warnings.append("customer invoice due observation recommended_action is not create_customer_invoice_draft.")
    if normalized.get("work_order_id") is None:
        warnings.append("customer_invoice_due_observation is missing work_order_id.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_due_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_customer_invoice_due_observation(payload)
    contract_name = "customer_invoice_due_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    _optional_text(mapping, normalized, "reason")

    evidence = normalized.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        errors.append("evidence is required for customer_invoice_due apply.")

    if normalized.get("action_type") and normalized.get("action_type") != "customer_invoice_due_observation":
        errors.append("action_type must be customer_invoice_due_observation for this apply payload.")
    if normalized.get("target_type") and normalized.get("target_type") != "WorkOrder":
        errors.append("customer invoice due apply currently requires target_type = WorkOrder.")
    if normalized.get("source_record_type") and normalized.get("source_record_type") != "WorkOrder":
        errors.append("customer invoice due apply currently requires source_record_type = WorkOrder.")
    if normalized.get("work_order_id") is None and normalized.get("source_record_id") is None:
        errors.append("work_order_id or source_record_id is required for customer invoice draft apply.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "create_customer_invoice_draft":
        warnings.append("customer invoice due apply payload recommended_action is not create_customer_invoice_draft.")
    if normalized.get("requires_approval") is not True:
        warnings.append("customer invoice due apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("customer invoice due apply payload should not be auto-applicable at Level 2.")
    return _result(contract_name, errors, warnings, normalized)


def validate_estimate_acceptance_apply_payload(payload: Any) -> JsonContractValidationResult:
    base = validate_estimate_followup_proposal(payload)
    contract_name = "estimate_acceptance_apply_payload"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    _require_text(mapping, normalized, "action_type", errors)
    _require_text(mapping, normalized, "target_type", errors)
    _require_int(mapping, normalized, "target_id", errors)
    _optional_int(mapping, normalized, "estimate_id", errors)
    _optional_int(mapping, normalized, "customer_id", errors)
    _optional_int(mapping, normalized, "site_id", errors)
    _optional_text(mapping, normalized, "acceptance_summary")

    if normalized.get("estimate_id") is None and normalized.get("matched_estimate_id") is not None:
        normalized["estimate_id"] = normalized.get("matched_estimate_id")
    if normalized.get("customer_id") is None and normalized.get("matched_customer_id") is not None:
        normalized["customer_id"] = normalized.get("matched_customer_id")
    if normalized.get("site_id") is None and normalized.get("matched_site_id") is not None:
        normalized["site_id"] = normalized.get("matched_site_id")

    if normalized.get("action_type") and normalized.get("action_type") != "estimate_acceptance_observation":
        errors.append("action_type must be estimate_acceptance_observation for this apply payload.")
    if normalized.get("workflow") and normalized.get("workflow") != "estimate":
        warnings.append("estimate acceptance apply payload has an unexpected workflow.")
    if normalized.get("target_type") and normalized.get("target_type") != "Estimate":
        errors.append("estimate acceptance apply currently requires target_type = Estimate.")
    if normalized.get("requires_approval") is not True:
        warnings.append("estimate acceptance apply payload should remain approval-gated.")
    if normalized.get("can_auto_apply_level_2") is not False:
        warnings.append("estimate acceptance apply payload should not be auto-applicable at Level 2.")

    normalized_intent = str(normalized.get("normalized_intent") or "").strip().lower()
    if normalized_intent not in {"estimate_acceptance_possible", "acceptance"}:
        errors.append("estimate acceptance apply payload must come from a clear acceptance proposal intent.")
    if normalized.get("estimate_id") is None:
        errors.append("estimate_id or matched_estimate_id is required for estimate acceptance apply.")
    if not any(
        normalized.get(field_name) not in (None, "", [])
        for field_name in ("acceptance_summary", "customer_reply_summary", "requested_change_or_question")
    ):
        warnings.append("estimate acceptance apply payload has sparse acceptance summary text.")
    return _result(contract_name, errors, warnings, normalized)


def validate_customer_invoice_payment_followup_due_observation(payload: Any) -> JsonContractValidationResult:
    base = validate_workflow_obligation_overdue_proposal(payload)
    contract_name = "customer_invoice_payment_followup_due_observation"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    for field_name in ("customer_name", "invoice_number", "payment_status", "followup_status", "action_type"):
        _optional_text(mapping, normalized, field_name)
    for field_name in ("invoice_date", "sent_or_exported_date", "due_date"):
        _optional_text(mapping, normalized, field_name)
        if normalized.get(field_name) and not _looks_like_iso_date(str(normalized[field_name])[:10]):
            warnings.append(f"{field_name} is present but not ISO-formatted.")
    _optional_float(mapping, normalized, "total_amount", errors, warnings, min_value=0.0)
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "cash_flow_flags", errors)

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "customer_invoice_payment_followup_due":
        warnings.append("customer invoice payment follow-up due observation payload has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "customer_invoice_ar":
        warnings.append("customer invoice payment follow-up due observation payload has an unexpected workflow.")
    if normalized.get("source_record_type") and normalized.get("source_record_type") != "CustomerInvoice":
        errors.append("source_record_type must be CustomerInvoice for customer_invoice_payment_followup_due_observation.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "review_customer_invoice_payment_followup":
        warnings.append("customer invoice payment follow-up due observation recommended_action is not review_customer_invoice_payment_followup.")
    if not normalized.get("invoice_number"):
        warnings.append("customer invoice payment follow-up due observation is missing invoice_number.")
    return _result(contract_name, errors, warnings, normalized)


def validate_vendor_invoice_reconciliation_due_proposal(payload: Any) -> JsonContractValidationResult:
    base = validate_workflow_obligation_overdue_proposal(payload)
    contract_name = "vendor_invoice_reconciliation_due_proposal"
    errors = list(base.errors)
    warnings = list(base.warnings)
    normalized = base.normalized_json if isinstance(base.normalized_json, dict) else {}
    mapping = payload if isinstance(payload, dict) else {}

    for field_name in ("vendor_name", "invoice_number", "match_outcome", "customer_billing_status"):
        _optional_text(mapping, normalized, field_name)
    for field_name in ("invoice_date", "due_date", "po_number", "packing_slip_number"):
        _optional_text(mapping, normalized, field_name)
        if normalized.get(field_name) and field_name in {"invoice_date", "due_date"} and not _looks_like_iso_date(str(normalized[field_name])):
            warnings.append(f"{field_name} is present but not ISO-formatted.")
    _optional_float(mapping, normalized, "total_amount", errors, warnings, min_value=0.0)
    _optional_text(mapping, normalized, "risk_level")
    _require_bool(mapping, normalized, "requires_approval", errors)
    _require_bool(mapping, normalized, "can_auto_apply_level_2", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text_list(mapping, normalized, "reconciliation_flags", errors)

    line_candidates = mapping.get("line_candidates")
    if line_candidates in (None, ""):
        normalized["line_candidates"] = []
    elif not isinstance(line_candidates, list):
        errors.append("line_candidates must be a list when provided.")
        normalized["line_candidates"] = []
    else:
        normalized["line_candidates"] = [json_safe(item) for item in line_candidates]

    if normalized.get("proposal_type") and normalized.get("proposal_type") != "vendor_invoice_reconciliation_due":
        warnings.append("vendor invoice reconciliation due proposal has an unexpected proposal_type.")
    if normalized.get("workflow") and normalized.get("workflow") != "vendor_invoice_payables":
        warnings.append("vendor invoice reconciliation due proposal has an unexpected workflow.")
    if normalized.get("recommended_action") and normalized.get("recommended_action") != "review_vendor_invoice_reconciliation":
        warnings.append("vendor invoice reconciliation due proposal recommended_action is not review_vendor_invoice_reconciliation.")
    if not normalized.get("vendor_name") or not normalized.get("invoice_number"):
        warnings.append("vendor invoice reconciliation due proposal has sparse vendor/invoice identification.")
    return _result(contract_name, errors, warnings, normalized)


def validate_attachment_text_extraction_json(payload: Any) -> JsonContractValidationResult:
    contract_name = "attachment_text_extraction_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "extraction_type", errors)
    _optional_text(mapping, normalized, "filename")
    _optional_text(mapping, normalized, "mime_type")
    _require_text(mapping, normalized, "method", errors)
    _require_bool(mapping, normalized, "success", errors)
    _require_int(mapping, normalized, "text_length", errors)
    _require_int(mapping, normalized, "page_count", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _optional_text_list(mapping, normalized, "errors", errors)
    _require_bool(mapping, normalized, "requires_ocr", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _require_text(mapping, normalized, "extracted_at", errors)
    _require_text(mapping, normalized, "extractor_version", errors)
    _optional_text(mapping, normalized, "source")

    if normalized.get("text_length") is not None and int(normalized["text_length"]) < 0:
        errors.append("text_length must be zero or greater.")
    if normalized.get("page_count") is not None and int(normalized["page_count"]) < 0:
        errors.append("page_count must be zero or greater.")
    if normalized.get("extraction_type") and normalized.get("extraction_type") != "attachment_text":
        warnings.append("attachment extraction json has an unexpected extraction_type.")
    return _result(contract_name, errors, warnings, normalized)


def validate_attachment_review_choices_json(payload: Any) -> JsonContractValidationResult:
    contract_name = "attachment_review_choices_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "attachment_id", errors)
    _require_int(mapping, normalized, "inbound_message_id", errors)
    _optional_text(mapping, normalized, "filename")
    _optional_text(mapping, normalized, "mime_type")
    _optional_text(mapping, normalized, "file_path")
    _require_text(mapping, normalized, "extraction_status", errors)
    _require_bool(mapping, normalized, "requires_ocr", errors)
    _optional_text_list(mapping, normalized, "warnings", errors)
    _optional_text_list(mapping, normalized, "errors", errors)
    _require_int(mapping, normalized, "text_length", errors)
    _optional_text(mapping, normalized, "parent_message_sender")
    _optional_text(mapping, normalized, "parent_message_subject")
    _optional_text(mapping, normalized, "parent_workflow_guess")
    _optional_bool(mapping, normalized, "cash_flow_critical", errors)
    _optional_text(mapping, normalized, "cash_flow_reason")
    _optional_text(mapping, normalized, "review_kind")

    actions = mapping.get("suggested_operator_actions")
    if not isinstance(actions, list) or not actions:
        errors.append("suggested_operator_actions must be a non-empty list.")
        normalized["suggested_operator_actions"] = []
    else:
        normalized["suggested_operator_actions"] = []
        for index, item in enumerate(actions):
            text = str(item or "").strip()
            if not text:
                errors.append(f"suggested_operator_actions[{index}] must not be blank.")
                continue
            normalized["suggested_operator_actions"].append(text)

    if normalized.get("text_length") is not None and int(normalized["text_length"]) < 0:
        errors.append("text_length must be zero or greater.")
    if not normalized.get("filename"):
        warnings.append("attachment review choices json is missing filename.")
    if (
        normalized.get("extraction_status") in {"NeedsOCR", "MissingFile", "UnsupportedType", "ExtractionFailed"}
        and not normalized.get("warnings")
        and not normalized.get("errors")
    ):
        warnings.append("attachment review choices json has no warnings/errors for a review-required status.")
    return _result(contract_name, errors, warnings, normalized)


def validate_evidence(payload: Any) -> JsonContractValidationResult:
    contract_name = "evidence_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_int(mapping, normalized, "inbound_message_id", errors)
    _optional_text(mapping, normalized, "external_message_id")
    _optional_text(mapping, normalized, "sender")
    _optional_text(mapping, normalized, "sender_name")
    _optional_text(mapping, normalized, "subject")
    _optional_text(mapping, normalized, "source_excerpt")
    _optional_text(mapping, normalized, "packing_slip_number")
    _optional_text(mapping, normalized, "extracted_po_number")
    _optional_text(mapping, normalized, "vendor_name")
    _optional_bool(mapping, normalized, "quote_line_available", errors)
    _optional_bool(mapping, normalized, "quote_line_checked", errors)
    _optional_float(mapping, normalized, "overall_confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    normalized["matched_po_item_ids"] = _normalize_int_list(mapping.get("matched_po_item_ids"), "matched_po_item_ids", errors)
    normalized["candidate_po_item_ids"] = _normalize_int_list(mapping.get("candidate_po_item_ids"), "candidate_po_item_ids", errors)
    normalized["line_match_outcomes"] = _normalize_text_list(mapping.get("line_match_outcomes"), "line_match_outcomes", errors)
    normalized["line_matching_bases"] = _normalize_text_list(mapping.get("line_matching_bases"), "line_matching_bases", errors)
    normalized["source_snippets"] = _normalize_text_list(mapping.get("source_snippets"), "source_snippets", errors)
    line_confidences = mapping.get("line_confidences")
    if line_confidences in (None, ""):
        normalized["line_confidences"] = []
    elif not isinstance(line_confidences, list):
        errors.append("line_confidences must be a list.")
        normalized["line_confidences"] = []
    else:
        normalized["line_confidences"] = []
        for index, item in enumerate(line_confidences):
            coerced = _coerce_float(item)
            if coerced is None:
                errors.append(f"line_confidences[{index}] must be numeric.")
                continue
            normalized["line_confidences"].append(coerced)

    attachments = mapping.get("attachments")
    attachment_ids = mapping.get("attachment_ids")
    attachment_filenames = mapping.get("attachment_filenames")
    if attachments is not None:
        if not isinstance(attachments, list):
            errors.append("attachments must be a list when provided.")
        else:
            normalized["attachments"] = [json_safe(item) for item in attachments]
    elif attachment_ids is not None or attachment_filenames is not None:
        normalized["attachment_ids"] = _normalize_int_list(attachment_ids, "attachment_ids", errors)
        normalized["attachment_filenames"] = _normalize_text_list(attachment_filenames, "attachment_filenames", errors)
    else:
        warnings.append("EvidenceJson has no attachment references.")

    extracted_payload = mapping.get("extracted_payload")
    if extracted_payload is not None:
        extraction_result = validate_extraction_result(extracted_payload)
        normalized["extracted_payload"] = extraction_result.normalized_json
        if not extraction_result.valid:
            errors.extend(f"extracted_payload: {error}" for error in extraction_result.errors)
        warnings.extend(f"extracted_payload: {warning}" for warning in extraction_result.warnings)

    line_match_evidence = mapping.get("line_match_evidence")
    if line_match_evidence is not None:
        if not isinstance(line_match_evidence, list):
            errors.append("line_match_evidence must be a list when provided.")
        else:
            normalized_evidence: list[dict[str, Any]] = []
            for index, item in enumerate(line_match_evidence):
                if not isinstance(item, dict):
                    errors.append(f"line_match_evidence[{index}] must be an object.")
                    continue
                normalized_item = json_safe(item)
                _optional_text(item, normalized_item, "packing_slip_part_number")
                _optional_text(item, normalized_item, "packing_slip_description")
                _optional_text(item, normalized_item, "po_line_part_number")
                _optional_text(item, normalized_item, "po_line_description")
                _optional_text(item, normalized_item, "vendor_quote_part_number")
                _optional_text(item, normalized_item, "vendor_quote_description")
                _optional_text(item, normalized_item, "internal_material_part_number")
                _require_text(item, normalized_item, "match_outcome", errors)
                _require_text(item, normalized_item, "matching_basis", errors)
                _require_bool(item, normalized_item, "quote_line_checked", errors)
                _require_bool(item, normalized_item, "quote_line_available", errors)
                _optional_text(item, normalized_item, "discrepancy_reason")
                _optional_text_list(item, normalized_item, "uncertainty_notes", errors)
                _require_bool(item, normalized_item, "operator_resolution_required", errors)
                _optional_float(item, normalized_item, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
                normalized_evidence.append(normalized_item)
            normalized["line_match_evidence"] = normalized_evidence

    return _result(contract_name, errors, warnings, normalized)


def validate_workflow_obligation_evidence(payload: Any) -> JsonContractValidationResult:
    contract_name = "workflow_obligation_evidence"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_text(mapping, normalized, "source_entity_type")
    _optional_text(mapping, normalized, "source_entity_id")
    _optional_text(mapping, normalized, "source_message_id")
    _optional_int(mapping, normalized, "source_automation_proposal_id", errors)
    _optional_int(mapping, normalized, "source_automation_question_id", errors)
    _optional_int(mapping, normalized, "source_automation_run_id", errors)
    _optional_int(mapping, normalized, "source_vendor_invoice_id", errors)
    _optional_int(mapping, normalized, "source_purchase_order_id", errors)
    _optional_int(mapping, normalized, "source_work_order_id", errors)
    _optional_text(mapping, normalized, "detected_obligation_type")
    _optional_text(mapping, normalized, "due_date")
    _optional_text(mapping, normalized, "due_window")
    _optional_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    _optional_bool(mapping, normalized, "requires_human_review", errors)
    _optional_text_list(mapping, normalized, "matched_phrases", errors)
    _optional_text_list(mapping, normalized, "source_snippets", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    _optional_text(mapping, normalized, "reason")
    _optional_text(mapping, normalized, "explicit_review_statement")

    if normalized.get("due_date") and not _looks_like_iso_date(str(normalized["due_date"])):
        warnings.append("due_date is present but not ISO-formatted.")
    if not normalized.get("detected_obligation_type"):
        warnings.append("detected_obligation_type is missing; UNKNOWN_NOVEL should still be explicit when applicable.")
    if not normalized.get("source_entity_type") and not normalized.get("source_automation_proposal_id") and not normalized.get("source_automation_question_id"):
        warnings.append("WorkflowObligation evidence has no primary source reference.")
    return _result(contract_name, errors, warnings, normalized)


def validate_workflow_obligation_resolution_json(payload: Any) -> JsonContractValidationResult:
    contract_name = "workflow_obligation_resolution_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _optional_text(mapping, normalized, "resolution_type")
    _optional_text(mapping, normalized, "resolution_status")
    _optional_text(mapping, normalized, "resolved_by")
    _optional_text(mapping, normalized, "resolved_at")
    _optional_text(mapping, normalized, "notes")
    _optional_bool(mapping, normalized, "no_business_mutation_occurred", errors)
    _optional_text_list(mapping, normalized, "uncertainty_notes", errors)
    if normalized.get("resolved_at") and not _looks_like_iso_datetime(str(normalized["resolved_at"])):
        warnings.append("resolved_at is present but not ISO-formatted.")
    if not normalized.get("resolution_type"):
        warnings.append("resolution_type is missing from workflow obligation resolution json.")
    return _result(contract_name, errors, warnings, normalized)


def validate_memory_content(payload: Any) -> JsonContractValidationResult:
    contract_name = "memory_content"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "scope_type", errors)
    _require_text(mapping, normalized, "memory_type", errors)
    if "scope_id" not in mapping or mapping.get("scope_id") in (None, ""):
        if str(normalized.get("scope_type") or "").lower() == "global":
            warnings.append("Global memory content does not require a scope_id.")
            normalized["scope_id"] = None
        else:
            errors.append("scope_id is required for non-Global memory content.")
    else:
        normalized["scope_id"] = json_safe(mapping.get("scope_id"))
    if "content" not in mapping:
        errors.append("content is required.")
    else:
        normalized["content"] = json_safe(mapping.get("content"))
    _require_text(mapping, normalized, "source", errors)
    _require_float(mapping, normalized, "confidence", errors, warnings, min_value=0.0, max_value=1.0)
    return _result(contract_name, errors, warnings, normalized)


def validate_question_choices(payload: Any) -> JsonContractValidationResult:
    contract_name = "question_choices"
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(payload, list):
        mapping = {"choices": payload, "allow_free_text": False}
    else:
        mapping = _require_dict(payload, contract_name, errors)
        if not mapping:
            return _result(contract_name, errors, warnings, {})

    normalized = _base_normalized(mapping)
    choices = mapping.get("choices")
    allow_free_text = bool(mapping.get("allow_free_text") or mapping.get("free_text"))
    normalized["allow_free_text"] = allow_free_text

    if choices is None:
        errors.append("choices is required.")
        normalized["choices"] = []
        return _result(contract_name, errors, warnings, normalized)
    if not isinstance(choices, list):
        errors.append("choices must be a list.")
        normalized["choices"] = []
        return _result(contract_name, errors, warnings, normalized)
    if not choices and not allow_free_text:
        errors.append("choices may be empty only when allow_free_text is true.")

    normalized_choices: list[Any] = []
    for index, choice in enumerate(choices):
        if isinstance(choice, str):
            text = choice.strip()
            if not text:
                errors.append(f"choices[{index}] must not be blank.")
                continue
            normalized_choices.append(text)
            continue
        if isinstance(choice, dict):
            label = str(choice.get("label") or "").strip()
            value = str(choice.get("value") or "").strip()
            if not label or not value:
                errors.append(f"choices[{index}] objects must include non-empty label and value.")
                continue
            normalized_choices.append({"label": label, "value": value})
            continue
        errors.append(f"choices[{index}] must be a string or object with label/value.")
    normalized["choices"] = normalized_choices
    return _result(contract_name, errors, warnings, normalized)


def validate_event_json(payload: Any) -> JsonContractValidationResult:
    contract_name = "event_json"
    errors: list[str] = []
    warnings: list[str] = []
    mapping = _require_dict(payload, contract_name, errors)
    normalized = _base_normalized(mapping)
    if not mapping:
        return _result(contract_name, errors, warnings, normalized)

    _require_text(mapping, normalized, "event_type", errors)
    _require_text(mapping, normalized, "summary", errors)
    for field_name in ("payload", "evidence", "context"):
        if field_name in mapping:
            normalized[field_name] = json_safe(mapping.get(field_name))
    severity = mapping.get("severity")
    if severity is not None:
        _require_choice(mapping, normalized, "severity", EVENT_SEVERITY_LEVELS, errors, warnings)
    else:
        warnings.append("severity is not set on EventJson.")
    return _result(contract_name, errors, warnings, normalized)


def _result(
    contract_name: str,
    errors: list[str],
    warnings: list[str],
    normalized_json: Any,
) -> JsonContractValidationResult:
    return JsonContractValidationResult(
        valid=not errors,
        contract_name=contract_name,
        errors=list(errors),
        warnings=list(warnings),
        normalized_json=normalized_json,
    )


def _base_normalized(mapping: dict[str, Any]) -> dict[str, Any]:
    return json_safe(mapping) if mapping else {}


def _require_dict(payload: Any, contract_name: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        errors.append(f"{contract_name} payload must be a JSON object.")
        return {}
    return payload


def _require_text(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    text = str(mapping.get(field_name) or "").strip()
    if not text:
        errors.append(f"{field_name} is required.")
        return
    normalized[field_name] = text


def _optional_text(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str) -> None:
    value = mapping.get(field_name)
    if value in (None, ""):
        normalized[field_name] = None
        return
    normalized[field_name] = str(value).strip() or None


def _optional_text_list(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = mapping.get(field_name)
    if value in (None, ""):
        normalized[field_name] = []
        return
    if isinstance(value, str):
        text = value.strip()
        normalized[field_name] = [text] if text else []
        return
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a string or list of strings when provided.")
        normalized[field_name] = []
        return
    normalized[field_name] = _normalize_text_list(value, field_name, errors)


def _require_bool(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = _coerce_bool(mapping.get(field_name))
    if value is None:
        errors.append(f"{field_name} must be true or false.")
        return
    normalized[field_name] = value


def _optional_bool(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = mapping.get(field_name)
    if value in (None, ""):
        normalized[field_name] = None
        return
    coerced = _coerce_bool(value)
    if coerced is None:
        errors.append(f"{field_name} must be true or false when provided.")
        return
    normalized[field_name] = coerced


def _require_int(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = _coerce_int(mapping.get(field_name))
    if value is None:
        errors.append(f"{field_name} must be an integer.")
        return
    normalized[field_name] = value


def _optional_int(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = mapping.get(field_name)
    if value in (None, ""):
        normalized[field_name] = None
        return
    coerced = _coerce_int(value)
    if coerced is None:
        errors.append(f"{field_name} must be an integer when provided.")
        return
    normalized[field_name] = coerced


def _require_float(
    mapping: dict[str, Any],
    normalized: dict[str, Any],
    field_name: str,
    errors: list[str],
    warnings: list[str],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> None:
    value = _coerce_float(mapping.get(field_name))
    if value is None:
        errors.append(f"{field_name} must be numeric.")
        return
    normalized[field_name] = value
    _check_float_range(field_name, value, warnings, min_value=min_value, max_value=max_value)


def _optional_float(
    mapping: dict[str, Any],
    normalized: dict[str, Any],
    field_name: str,
    errors: list[str],
    warnings: list[str],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> None:
    value = mapping.get(field_name)
    if value in (None, ""):
        normalized[field_name] = None
        return
    coerced = _coerce_float(value)
    if coerced is None:
        errors.append(f"{field_name} must be numeric when provided.")
        return
    normalized[field_name] = coerced
    _check_float_range(field_name, coerced, warnings, min_value=min_value, max_value=max_value)


def _check_float_range(
    field_name: str,
    value: float,
    warnings: list[str],
    *,
    min_value: float | None,
    max_value: float | None,
) -> None:
    if min_value is not None and value < min_value:
        warnings.append(f"{field_name} is below the expected minimum of {min_value}.")
    if max_value is not None and value > max_value:
        warnings.append(f"{field_name} is above the expected maximum of {max_value}.")


def _require_dict_value(mapping: dict[str, Any], normalized: dict[str, Any], field_name: str, errors: list[str]) -> None:
    value = mapping.get(field_name)
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be a JSON object.")
        return
    normalized[field_name] = json_safe(value)


def _require_choice(
    mapping: dict[str, Any],
    normalized: dict[str, Any],
    field_name: str,
    allowed_values: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    value = str(mapping.get(field_name) or "").strip().lower()
    if not value:
        errors.append(f"{field_name} is required.")
        return
    normalized[field_name] = value
    if value not in allowed_values:
        warnings.append(f"{field_name} uses non-standard value '{value}'.")


def _validate_quote_line_candidates(
    value: Any,
    errors: list[str],
    warnings: list[str],
    *,
    allow_empty: bool,
    require_pr_item_id: bool,
    field_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list.")
        return []
    if not value and not allow_empty:
        errors.append(f"{field_name} must be a non-empty list.")
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{field_name}[{index}] must be an object.")
            continue
        normalized_item = json_safe(item)
        pr_item_id = _coerce_int(item.get("pr_item_id"))
        if require_pr_item_id and pr_item_id is None:
            errors.append(f"{field_name}[{index}].pr_item_id is required.")
            continue
        normalized_item["pr_item_id"] = pr_item_id
        material_call_item_id = _coerce_int(item.get("material_call_item_id"))
        normalized_item["material_call_item_id"] = material_call_item_id
        if not require_pr_item_id and pr_item_id is None and material_call_item_id is None:
            if not any(str(item.get(name) or "").strip() for name in ("part_number", "description")):
                warnings.append(
                    f"{field_name}[{index}] has no pr_item_id, material_call_item_id, part_number, or description."
                )
        quoted_unit_price = _coerce_float(item.get("quoted_unit_price"))
        if quoted_unit_price is None:
            errors.append(f"{field_name}[{index}].quoted_unit_price must be numeric.")
            continue
        if quoted_unit_price < 0:
            errors.append(f"{field_name}[{index}].quoted_unit_price must not be negative.")
            continue
        normalized_item["quoted_unit_price"] = quoted_unit_price
        for text_field in ("part_number", "description"):
            if text_field in item:
                normalized_item[text_field] = str(item.get(text_field) or "").strip() or None
        normalized.append(normalized_item)
    return normalized


def _normalize_int_list(value: Any, field_name: str, errors: list[str]) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list.")
        return []
    normalized: list[int] = []
    for index, item in enumerate(value):
        coerced = _coerce_int(item)
        if coerced is None:
            errors.append(f"{field_name}[{index}] must be an integer.")
            continue
        normalized.append(coerced)
    return normalized


def _normalize_text_list(value: Any, field_name: str, errors: list[str]) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list.")
        return []
    normalized: list[str] = []
    for index, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            errors.append(f"{field_name}[{index}] must not be blank.")
            continue
        normalized.append(text)
    return normalized


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _derive_po_eta_normalized_intent(mapping: dict[str, Any]) -> str:
    pickup_note = str(mapping.get("pickup_note") or "").strip()
    backorder_hint = str(mapping.get("backorder_hint") or "").strip()
    eta_date = str(mapping.get("eta_date") or "").strip()
    vendor_message_summary = str(mapping.get("vendor_message_summary") or "").strip().lower()
    parts_ready = _coerce_bool(mapping.get("parts_ready"))

    if pickup_note and parts_ready:
        return "PICKUP_NOTICE"
    if pickup_note:
        return "PICKUP_NOTICE"
    if backorder_hint and parts_ready:
        return "PARTIAL_READY"
    if backorder_hint:
        return "BACKORDER_NOTICE"
    if parts_ready and "ship" in vendor_message_summary:
        return "SHIPPING_NOTICE"
    if parts_ready:
        return "PARTS_READY"
    if eta_date:
        return "ETA_UPDATE"
    return "POSSIBLE_PO_STATUS_UPDATE"


def _looks_like_iso_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()))


def _looks_like_iso_datetime(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
