from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:
    import fitz
except Exception:  # pragma: no cover - optional runtime fallback
    fitz = None

from neon_ai.services.inbound_intake_service import (
    InboundAttachmentRecord,
    InboundMessageRecord,
    get_inbound_message,
    get_inbound_attachment,
    list_inbound_attachments,
    update_attachment_extraction,
)
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import validate_attachment_review_choices_json
from neon_ai.services.automation_proposal_service import (
    AutomationQuestionRecord,
    create_question,
    get_active_question_for_target,
)
from neon_ai.services.workflow_obligation_route_attachment_service import (
    attach_selected_obligation_for_question_best_effort,
)


EXTRACTOR_VERSION = "level2_attachment_text_v1"
PDF_METHOD = "pdf_text"
TEXT_METHOD = "plain_text"
CSV_METHOD = "csv_text"
MIN_USABLE_TEXT_LENGTH = 40
PREVIEW_LENGTH = 240
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".text", ".md", ".log", ".eml"}
SUPPORTED_CSV_EXTENSIONS = {".csv"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
LIKELY_ATTACHMENT_STATUSES = {
    "Imported",
    "PendingExtraction",
    "Extracted",
    "ExtractionFailed",
    "NeedsOCR",
    "MissingFile",
    "UnsupportedType",
}
ATTACHMENT_REVIEW_AUTOMATION_KEY = "attachment_text_extraction"
ATTACHMENT_REVIEW_WORKFLOW = "attachment_intake"
ATTACHMENT_REVIEW_QUESTION_TYPES = {
    "attachment_review_required",
    "attachment_text_extraction_required",
    "attachment_needs_ocr",
    "unsupported_attachment_review",
    "missing_attachment_file_review",
}
CASH_FLOW_WORKFLOWS = {"vendor_invoice_payables", "customer_invoice_ar"}
ATTACHMENT_REVIEW_ACTIONS = [
    "review_attachment_manually",
    "enter_extracted_text_manually",
    "replace_or_relink_file",
    "mark_not_needed",
    "request_sender_resend_clear_copy_later",
]
HIGH_URGENCY_TERMS = ("urgent", "asap", "today", "immediately", "right away", "rush")


@dataclass(frozen=True)
class AttachmentTextExtractionResult:
    attachment: InboundAttachmentRecord
    extraction_json: dict[str, Any]
    content_text: str | None
    skipped: bool = False
    question: AutomationQuestionRecord | None = None


def extract_text_for_attachment(
    attachment_id: int,
    *,
    force: bool = False,
    source: str = "watcher",
) -> AttachmentTextExtractionResult:
    attachment = get_inbound_attachment(int(attachment_id))
    if attachment is None:
        raise RuntimeError(f"InboundAttachment {attachment_id} was not found.")

    existing_text = maybe_normalize_attachment_text(attachment.content_text)
    existing_summary = get_attachment_text_summary(attachment)
    if existing_text and not force:
        return AttachmentTextExtractionResult(
            attachment=attachment,
            extraction_json=existing_summary,
            content_text=existing_text,
            skipped=True,
        )

    file_path = str(attachment.file_path or "").strip()
    if not file_path:
        return _persist_extraction_result(
            attachment,
            content_text="",
            extraction_json=_build_extraction_json(
                attachment,
                method=_method_for_attachment(attachment),
                success=False,
                warnings=[],
                errors=["missing_file_path"],
                requires_ocr=False,
                confidence=0.0,
                source=source,
            ),
            status="MissingFile",
            error_message="missing_file_path",
        )

    resolved_path = Path(file_path)
    if not resolved_path.exists():
        return _persist_extraction_result(
            attachment,
            content_text="",
            extraction_json=_build_extraction_json(
                attachment,
                method=_method_for_attachment(attachment),
                success=False,
                warnings=[],
                errors=["missing_file"],
                requires_ocr=False,
                confidence=0.0,
                source=source,
            ),
            status="MissingFile",
            error_message="missing_file",
        )

    if _is_pdf_attachment(attachment):
        return _extract_pdf_attachment(attachment, resolved_path, source=source)
    if _is_text_attachment(attachment):
        return _extract_text_file_attachment(attachment, resolved_path, source=source, method=TEXT_METHOD)
    if _is_csv_attachment(attachment):
        return _extract_text_file_attachment(attachment, resolved_path, source=source, method=CSV_METHOD)

    return _persist_extraction_result(
        attachment,
        content_text="",
        extraction_json=_build_extraction_json(
            attachment,
            method=_method_for_attachment(attachment),
            success=False,
            warnings=["unsupported_mime_type"],
            errors=[],
            requires_ocr=False,
            confidence=0.0,
            source=source,
        ),
        status="UnsupportedType",
        error_message="unsupported_mime_type",
    )


def extract_text_for_message_attachments(
    inbound_message_id: int,
    *,
    force: bool = False,
    source: str = "watcher",
) -> list[AttachmentTextExtractionResult]:
    attachments = list_inbound_attachments(inbound_message_id=int(inbound_message_id), limit=100)
    return [
        extract_text_for_attachment(attachment.inbound_attachment_id, force=force, source=source)
        for attachment in attachments
    ]


def list_attachments_needing_extraction(*, limit: int = 200) -> list[InboundAttachmentRecord]:
    attachments = list_inbound_attachments(limit=max(int(limit) * 4, int(limit)))
    needing: list[InboundAttachmentRecord] = []
    for attachment in attachments:
        summary = get_attachment_text_summary(attachment)
        if summary["has_usable_text"]:
            continue
        if str(attachment.status or "").strip() not in LIKELY_ATTACHMENT_STATUSES:
            continue
        needing.append(attachment)
        if len(needing) >= int(limit):
            break
    return needing


def attachment_requires_review(
    attachment_or_summary: InboundAttachmentRecord | dict[str, Any] | int,
) -> bool:
    summary = (
        attachment_or_summary
        if isinstance(attachment_or_summary, dict)
        else get_attachment_text_summary(attachment_or_summary)
    )
    status = str(summary.get("status") or "").strip()
    if status in {"NeedsOCR", "MissingFile", "UnsupportedType", "ExtractionFailed"}:
        return True
    if bool(summary.get("requires_ocr")):
        return True
    if status == "Extracted":
        return False
    return bool(summary.get("is_likely_document")) and not bool(summary.get("has_usable_text"))


def get_active_attachment_review_question(attachment_id: int) -> AutomationQuestionRecord | None:
    return get_active_question_for_target(
        target_type="InboundAttachment",
        target_id=int(attachment_id),
        automation_key=ATTACHMENT_REVIEW_AUTOMATION_KEY,
        question_types=ATTACHMENT_REVIEW_QUESTION_TYPES,
    )


def create_attachment_review_question(
    attachment_id: int,
    *,
    source: str = "watcher",
) -> AutomationQuestionRecord:
    attachment = get_inbound_attachment(int(attachment_id))
    if attachment is None:
        raise RuntimeError(f"InboundAttachment {attachment_id} was not found.")
    summary = get_attachment_text_summary(attachment)
    if not attachment_requires_review(summary):
        raise ValueError("Attachment already has usable extracted text; review question is not needed.")
    existing = get_active_attachment_review_question(int(attachment_id))
    if existing is not None:
        return existing

    message = get_inbound_message(int(attachment.inbound_message_id))
    choices_json = _build_attachment_review_choices_json(attachment, summary=summary, message=message)
    validation = validate_attachment_review_choices_json(choices_json)
    if not validation.valid:
        raise ValueError(
            "Attachment review choices payload is invalid: " + "; ".join(validation.errors)
        )
    question = create_question(
        automation_key=ATTACHMENT_REVIEW_AUTOMATION_KEY,
        workflow=ATTACHMENT_REVIEW_WORKFLOW,
        question_type=_attachment_review_question_type(summary),
        target_type="InboundAttachment",
        target_id=attachment.inbound_attachment_id,
        question_text=_attachment_review_question_text(summary),
        choices_json=validation.normalized_json,
        required_before_action=False,
        urgency=_attachment_review_urgency(message),
        status="Open",
    )
    attach_selected_obligation_for_question_best_effort(
        question,
        automation_key=ATTACHMENT_REVIEW_AUTOMATION_KEY,
        automation_run_id=None,
    )
    log_automation_event(
        automation_run_id=None,
        automation_key=ATTACHMENT_REVIEW_AUTOMATION_KEY,
        event_type="attachment_review_question_created",
        summary=f"Created attachment review question for inbound attachment #{attachment.inbound_attachment_id}.",
        target_type="AutomationQuestion",
        target_id=question.automation_question_id,
        event_json={
            "source": str(source or "watcher").strip() or "watcher",
            "attachment_id": attachment.inbound_attachment_id,
            "inbound_message_id": attachment.inbound_message_id,
            "question_type": question.question_type,
            "attachment_status": summary.get("status"),
            "requires_ocr": summary.get("requires_ocr"),
            "warnings": summary.get("warnings") or [],
            "errors": summary.get("errors") or [],
        },
    )
    return question


def get_attachment_text_summary(attachment_or_id: InboundAttachmentRecord | int) -> dict[str, Any]:
    attachment = (
        attachment_or_id
        if isinstance(attachment_or_id, InboundAttachmentRecord)
        else get_inbound_attachment(int(attachment_or_id))
    )
    if attachment is None:
        return {
            "attachment_id": None,
            "status": "MissingAttachment",
            "success": False,
            "has_usable_text": False,
            "warnings": ["attachment_not_found"],
            "errors": ["attachment_not_found"],
        }

    extraction = _as_dict(attachment.extraction_json)
    content_text = maybe_normalize_attachment_text(attachment.content_text) or ""
    warnings = _normalize_text_list(extraction.get("warnings"))
    errors = _normalize_text_list(extraction.get("errors"))
    text_length = int(extraction.get("text_length") or len(content_text))
    page_count = int(extraction.get("page_count") or 0)
    method = str(extraction.get("method") or _method_for_attachment(attachment)).strip() or "unknown"
    success = bool(extraction.get("success")) if extraction else bool(content_text)
    requires_ocr = bool(extraction.get("requires_ocr"))
    is_pdf = _is_pdf_attachment(attachment)
    has_usable_text = bool(content_text) and (
        text_length >= MIN_USABLE_TEXT_LENGTH or not is_pdf
    )
    return {
        "attachment_id": int(attachment.inbound_attachment_id),
        "inbound_message_id": int(attachment.inbound_message_id),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "file_path": attachment.file_path,
        "status": attachment.status,
        "success": success,
        "method": method,
        "text_length": text_length,
        "page_count": page_count,
        "warnings": warnings,
        "errors": errors,
        "requires_ocr": requires_ocr,
        "confidence": _coerce_float(extraction.get("confidence"), default=1.0 if has_usable_text else 0.0),
        "has_usable_text": has_usable_text,
        "is_pdf": is_pdf,
        "is_likely_document": _is_likely_document(attachment),
        "preview_text": content_text[:PREVIEW_LENGTH],
        "extractor_version": str(extraction.get("extractor_version") or EXTRACTOR_VERSION),
    }


def summarize_message_attachment_text(
    attachments: list[InboundAttachmentRecord],
) -> dict[str, Any]:
    summaries = [get_attachment_text_summary(attachment) for attachment in attachments]
    combined_text = "\n\n".join(
        maybe_normalize_attachment_text(attachment.content_text) or ""
        for attachment in attachments
        if maybe_normalize_attachment_text(attachment.content_text)
    ).strip()
    return {
        "attachments": summaries,
        "combined_text": combined_text,
        "has_usable_attachment_text": bool(combined_text),
        "attachment_count": len(summaries),
        "pdf_count": sum(1 for item in summaries if item.get("is_pdf")),
        "needs_ocr_count": sum(1 for item in summaries if item.get("requires_ocr")),
        "failed_count": sum(
            1
            for item in summaries
            if str(item.get("status") or "") in {"ExtractionFailed", "MissingFile", "UnsupportedType"}
        ),
    }


def find_attachment_extraction_review_gap(
    attachments: list[InboundAttachmentRecord],
    *,
    expected_keywords: tuple[str, ...] = (),
) -> dict[str, Any]:
    summaries = [get_attachment_text_summary(attachment) for attachment in attachments]
    expected = tuple(str(keyword or "").strip().lower() for keyword in expected_keywords if str(keyword or "").strip())
    blocking: list[dict[str, Any]] = []
    for summary in summaries:
        if not summary.get("is_pdf"):
            continue
        filename = str(summary.get("filename") or "").strip().lower()
        if expected and not any(keyword in filename for keyword in expected):
            continue
        if summary.get("has_usable_text"):
            continue
        blocking.append(summary)
    if not blocking:
        return {"requires_question": False, "attachments": summaries}
    filenames = ", ".join(str(item.get("filename") or f"attachment #{item.get('attachment_id')}") for item in blocking)
    return {
        "requires_question": True,
        "attachments": summaries,
        "blocking_attachment_ids": [int(item.get("attachment_id") or 0) for item in blocking],
        "reason": (
            "A likely PDF attachment is present but no usable extracted text is available. "
            f"Review attachment text extraction for: {filenames}."
        ),
    }


def build_attachment_extraction_evidence(
    attachments: list[InboundAttachmentRecord],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for attachment in attachments:
        summary = get_attachment_text_summary(attachment)
        evidence.append(
            {
                "attachment_id": summary.get("attachment_id"),
                "filename": summary.get("filename"),
                "mime_type": summary.get("mime_type"),
                "status": summary.get("status"),
                "method": summary.get("method"),
                "success": summary.get("success"),
                "text_length": summary.get("text_length"),
                "page_count": summary.get("page_count"),
                "requires_ocr": summary.get("requires_ocr"),
                "warnings": summary.get("warnings") or [],
                "errors": summary.get("errors") or [],
                "confidence": summary.get("confidence"),
                "preview_text": summary.get("preview_text"),
            }
        )
    return evidence


def maybe_normalize_attachment_text(value: str | None) -> str | None:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines).strip()
    return normalized or None


def _extract_pdf_attachment(
    attachment: InboundAttachmentRecord,
    path: Path,
    *,
    source: str,
) -> AttachmentTextExtractionResult:
    warnings: list[str] = []
    errors: list[str] = []
    page_count = 0
    text = ""
    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception as exc:
                warnings.append(f"page_extract_error:{type(exc).__name__}")
                chunks.append("")
        text = maybe_normalize_attachment_text("\n\n".join(chunks)) or ""
    except Exception as exc:
        errors.append(f"pdf_read_error:{type(exc).__name__}")

    if len(text) < MIN_USABLE_TEXT_LENGTH and not errors and fitz is not None:
        try:
            doc = fitz.open(str(path))
            try:
                if page_count <= 0:
                    page_count = len(doc)
                fitz_text = maybe_normalize_attachment_text(
                    "\n\n".join(doc.load_page(index).get_text("text") or "" for index in range(len(doc)))
                ) or ""
            finally:
                doc.close()
            if len(fitz_text) > len(text):
                text = fitz_text
                warnings.append("fitz_text_fallback_used")
        except Exception as exc:
            warnings.append(f"fitz_fallback_failed:{type(exc).__name__}")

    normalized_text = maybe_normalize_attachment_text(text) or ""
    text_length = len(normalized_text)
    success = text_length >= MIN_USABLE_TEXT_LENGTH and not errors
    requires_ocr = not success
    if text_length == 0 and not errors:
        warnings.append("blank_or_low_text_pdf")
    elif 0 < text_length < MIN_USABLE_TEXT_LENGTH and not errors:
        warnings.append("blank_or_low_text_pdf")

    extraction_json = _build_extraction_json(
        attachment,
        method=PDF_METHOD,
        success=success,
        warnings=warnings,
        errors=errors,
        requires_ocr=requires_ocr,
        confidence=0.96 if success else 0.15,
        source=source,
        text_length=text_length,
        page_count=page_count,
    )
    status = "Extracted" if success else "NeedsOCR"
    error_message = "; ".join(errors or warnings) if (errors or warnings) else ""
    return _persist_extraction_result(
        attachment,
        content_text=normalized_text,
        extraction_json=extraction_json,
        status=status,
        error_message=error_message,
    )


def _extract_text_file_attachment(
    attachment: InboundAttachmentRecord,
    path: Path,
    *,
    source: str,
    method: str,
) -> AttachmentTextExtractionResult:
    warnings: list[str] = []
    errors: list[str] = []
    try:
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        errors.append(f"text_read_error:{type(exc).__name__}")
        raw_text = ""
    normalized_text = maybe_normalize_attachment_text(raw_text) or ""
    success = bool(normalized_text) and not errors
    if not success and not errors:
        warnings.append("blank_attachment_text")
    extraction_json = _build_extraction_json(
        attachment,
        method=method,
        success=success,
        warnings=warnings,
        errors=errors,
        requires_ocr=False,
        confidence=0.98 if success else 0.1,
        source=source,
        text_length=len(normalized_text),
        page_count=0,
    )
    status = "Extracted" if success else "ExtractionFailed"
    error_message = "; ".join(errors or warnings) if (errors or warnings) else ""
    return _persist_extraction_result(
        attachment,
        content_text=normalized_text,
        extraction_json=extraction_json,
        status=status,
        error_message=error_message,
    )


def _persist_extraction_result(
    attachment: InboundAttachmentRecord,
    *,
    content_text: str,
    extraction_json: dict[str, Any],
    status: str,
    error_message: str,
) -> AttachmentTextExtractionResult:
    updated = update_attachment_extraction(
        attachment.inbound_attachment_id,
        extraction_json=extraction_json,
        content_text=content_text,
        status=status,
        error_message=error_message,
    )
    if updated is None:
        raise RuntimeError(
            f"InboundAttachment #{attachment.inbound_attachment_id} could not be updated after extraction."
        )
    question = None
    if attachment_requires_review(updated):
        question = create_attachment_review_question(
            updated.inbound_attachment_id,
            source=str(extraction_json.get("source") or "watcher"),
        )
    return AttachmentTextExtractionResult(
        attachment=updated,
        extraction_json=extraction_json,
        content_text=content_text or None,
        skipped=False,
        question=question,
    )


def _build_extraction_json(
    attachment: InboundAttachmentRecord,
    *,
    method: str,
    success: bool,
    warnings: list[str],
    errors: list[str],
    requires_ocr: bool,
    confidence: float,
    source: str,
    text_length: int = 0,
    page_count: int = 0,
) -> dict[str, Any]:
    return {
        "extraction_type": "attachment_text",
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "method": method,
        "success": bool(success),
        "text_length": int(text_length),
        "page_count": int(page_count),
        "warnings": [str(item) for item in warnings if str(item).strip()],
        "errors": [str(item) for item in errors if str(item).strip()],
        "requires_ocr": bool(requires_ocr),
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extractor_version": EXTRACTOR_VERSION,
        "source": str(source or "watcher").strip() or "watcher",
    }


def _method_for_attachment(attachment: InboundAttachmentRecord) -> str:
    if _is_pdf_attachment(attachment):
        return PDF_METHOD
    if _is_csv_attachment(attachment):
        return CSV_METHOD
    if _is_text_attachment(attachment):
        return TEXT_METHOD
    return "unsupported"


def _is_pdf_attachment(attachment: InboundAttachmentRecord) -> bool:
    return _attachment_suffix(attachment) in SUPPORTED_PDF_EXTENSIONS or "pdf" in str(attachment.mime_type or "").lower()


def _is_text_attachment(attachment: InboundAttachmentRecord) -> bool:
    suffix = _attachment_suffix(attachment)
    mime_type = str(attachment.mime_type or "").lower()
    return suffix in SUPPORTED_TEXT_EXTENSIONS or mime_type.startswith("text/plain")


def _is_csv_attachment(attachment: InboundAttachmentRecord) -> bool:
    suffix = _attachment_suffix(attachment)
    mime_type = str(attachment.mime_type or "").lower()
    return suffix in SUPPORTED_CSV_EXTENSIONS or "csv" in mime_type


def _is_likely_document(attachment: InboundAttachmentRecord) -> bool:
    return _is_pdf_attachment(attachment) or _is_text_attachment(attachment) or _is_csv_attachment(attachment)


def _attachment_suffix(attachment: InboundAttachmentRecord) -> str:
    return Path(str(attachment.filename or attachment.file_path or "")).suffix.lower()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_float(value: Any, *, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _build_attachment_review_choices_json(
    attachment: InboundAttachmentRecord,
    *,
    summary: dict[str, Any] | None = None,
    message: InboundMessageRecord | None = None,
) -> dict[str, Any]:
    current_summary = summary or get_attachment_text_summary(attachment)
    current_message = message or get_inbound_message(int(attachment.inbound_message_id))
    parent_workflow_guess = str(
        (current_message.workflow_guess if current_message else "")
        or _message_classification_value(current_message, "workflow")
        or ""
    ).strip()
    parent_sender = (
        current_message.sender_name
        or current_message.sender
        if current_message is not None
        else None
    )
    return {
        "attachment_id": int(attachment.inbound_attachment_id),
        "inbound_message_id": int(attachment.inbound_message_id),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "file_path": attachment.file_path,
        "extraction_status": current_summary.get("status"),
        "requires_ocr": bool(current_summary.get("requires_ocr")),
        "warnings": current_summary.get("warnings") or [],
        "errors": current_summary.get("errors") or [],
        "text_length": int(current_summary.get("text_length") or 0),
        "parent_message_sender": parent_sender,
        "parent_message_subject": current_message.subject if current_message is not None else None,
        "parent_workflow_guess": parent_workflow_guess,
        "cash_flow_critical": parent_workflow_guess in CASH_FLOW_WORKFLOWS,
        "cash_flow_reason": (
            "Parent workflow is cash-flow critical."
            if parent_workflow_guess in CASH_FLOW_WORKFLOWS
            else None
        ),
        "review_kind": _attachment_review_question_type(current_summary),
        "suggested_operator_actions": list(ATTACHMENT_REVIEW_ACTIONS),
    }


def _attachment_review_question_type(summary: dict[str, Any]) -> str:
    status = str(summary.get("status") or "").strip()
    errors = {str(item).strip() for item in (summary.get("errors") or []) if str(item).strip()}
    warnings = {str(item).strip() for item in (summary.get("warnings") or []) if str(item).strip()}
    if status == "MissingFile" or {"missing_file_path", "missing_file"} & errors:
        return "missing_attachment_file_review"
    if status == "UnsupportedType" or "unsupported_mime_type" in warnings:
        return "unsupported_attachment_review"
    if status == "NeedsOCR" or bool(summary.get("requires_ocr")):
        return "attachment_needs_ocr"
    return "attachment_text_extraction_required"


def _attachment_review_question_text(summary: dict[str, Any]) -> str:
    question_type = _attachment_review_question_type(summary)
    if question_type == "missing_attachment_file_review":
        return "Attachment file path is missing. Reattach or locate the source file."
    if question_type == "unsupported_attachment_review":
        return "Unsupported attachment type. Review manually."
    if question_type == "attachment_needs_ocr":
        return "This attachment appears to be a scanned/image-only PDF and needs OCR or manual review."
    return "Attachment text extraction failed. Review the file before route watchers can use it."


def _attachment_review_urgency(message: InboundMessageRecord | None) -> str:
    if message is None:
        return "Normal"
    workflow_guess = str(
        message.workflow_guess
        or _message_classification_value(message, "workflow")
        or ""
    ).strip()
    if workflow_guess in CASH_FLOW_WORKFLOWS:
        return "High"
    urgency_value = str(_message_classification_value(message, "urgency") or "").strip().lower()
    if urgency_value in {"high", "urgent", "critical"}:
        return "High"
    haystack = " ".join(
        part
        for part in (
            str(message.subject or "").strip().lower(),
            str(message.body_excerpt or message.body_text or "").strip().lower(),
        )
        if part
    )
    if any(term in haystack for term in HIGH_URGENCY_TERMS):
        return "High"
    return "Normal"


def _message_classification_value(message: InboundMessageRecord | None, field_name: str) -> Any:
    if message is None or not isinstance(message.classification_json, dict):
        return None
    return message.classification_json.get(field_name)
