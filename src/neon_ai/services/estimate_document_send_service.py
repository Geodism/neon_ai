from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.database.estimate_document_drafts import (
    ensure_estimate_document_draft_table,
    get_estimate_document_draft,
    mark_estimate_document_draft_sent,
)
from neon_ai.database.estimates import get_detailed_estimate_data, mark_estimate_sent
from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.document_control.token_engine import render_tokens
from neon_ai.gateway import send_to_user
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)
from neon_ai.services.template_token_service import get_estimate_delivery_tokens


ESTIMATE_DELIVERY_DOCUMENT_TYPE_CODE = "ESTIMATE_DELIVERY"
ESTIMATE_DELIVERY_USAGE_CONTEXTS = ("ESTIMATE_SEND", "SendEstimateToCustomer")
ESTIMATE_DELIVERY_TEMPLATE_CODE = "EstimateDelivery"


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
        "EstimateStatus": (estimate_parent or {}).get("Status"),
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
    if not estimate_parent:
        return False, "Linked estimate was not found.", context

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


def _fallback_estimate_delivery_subject(tokens: dict[str, Any]) -> str:
    estimate_number = tokens.get("EstimateNumber") or tokens.get("EstimateID") or "Unknown"
    company_name = str(tokens.get("CompanyName") or "Argon Electrical").strip() or "Argon Electrical"
    return f"Estimate #{estimate_number} from {company_name}"


def _fallback_estimate_delivery_body(tokens: dict[str, Any]) -> str:
    customer_name = str(tokens.get("CustomerName") or "Customer").strip() or "Customer"
    estimate_number = tokens.get("EstimateNumber") or tokens.get("EstimateID") or "Unknown"
    estimate_total = str(tokens.get("EstimateTotalFormatted") or tokens.get("EstimateTotal") or "").strip()
    company_name = str(tokens.get("CompanyName") or "Argon Electrical").strip() or "Argon Electrical"
    owner_name = str(tokens.get("OwnerName") or "Project Team").strip() or "Project Team"

    lines = [
        f"Hello {customer_name},",
        "",
        f"Please find attached estimate #{estimate_number}.",
    ]
    if estimate_total:
        lines.append(f"Estimated total: {estimate_total}")
    lines.extend(
        [
            "",
            "Please let us know if you have any questions.",
            "",
            f"{company_name}",
            owner_name,
        ]
    )
    return "\n".join(lines).strip()


def _load_estimate_delivery_template() -> tuple[int | None, str | None, int | None, str | None, str | None]:
    try:
        catalog_service = DocumentCatalogService(DocumentControlRepository())
        for usage_context in ESTIMATE_DELIVERY_USAGE_CONTEXTS:
            default_mapping = catalog_service.get_template_default(
                document_type_code=ESTIMATE_DELIVERY_DOCUMENT_TYPE_CODE,
                template_kind=DocumentTemplateKind.BODY,
                usage_context=usage_context,
            )
            if default_mapping is None:
                continue
            version = catalog_service.get_template_version(template_id=int(default_mapping.template_id))
            if version is not None and version.document_type_code == ESTIMATE_DELIVERY_DOCUMENT_TYPE_CODE:
                return (
                    int(version.template_id) if version.template_id is not None else None,
                    str(version.template_name or "").strip() or None,
                    int(version.template_version_id) if version.template_version_id is not None else None,
                    str(version.subject_line or "").strip() or None,
                    str(version.body_content or "").strip() or None,
                )

        summaries = catalog_service.list_templates(document_type_code=ESTIMATE_DELIVERY_DOCUMENT_TYPE_CODE)
    except Exception:
        return None, None, None, None, None

    candidates = []
    for summary in summaries:
        try:
            kind_value = summary.kind.value if hasattr(summary.kind, "value") else str(summary.kind)
        except Exception:
            kind_value = str(getattr(summary, "kind", "") or "")
        if str(kind_value).lower() != DocumentTemplateKind.BODY.value:
            continue
        if not bool(getattr(summary, "is_active", False)):
            continue
        active_version_id = getattr(summary, "active_version_id", None)
        if active_version_id is None:
            continue
        version = catalog_service.get_template_version(version_id=int(active_version_id))
        if version is None:
            continue
        candidates.append(version)

    if not candidates:
        return None, None, None, None, None

    candidates.sort(
        key=lambda version: (
            -(int(version.version_number or 0)),
            str(version.template_name or "").lower(),
            int(version.template_id or 0),
        )
    )
    selected = candidates[0]
    return (
        int(selected.template_id) if selected.template_id is not None else None,
        str(selected.template_name or "").strip() or None,
        int(selected.template_version_id) if selected.template_version_id is not None else None,
        str(selected.subject_line or "").strip() or None,
        str(selected.body_content or "").strip() or None,
    )


def prepare_estimate_delivery_message(draft_id: int) -> dict[str, Any]:
    can_send, reason, context = can_send_estimate_document_draft(draft_id)
    if not can_send:
        return {
            "success": False,
            "reason": reason,
            **(context or {}),
        }

    estimate_id = context.get("EstimateID")
    final_file_path = str(context.get("FinalFilePath") or "").strip()
    attachment_file_name = Path(final_file_path).name if final_file_path else ""

    try:
        normalized_estimate_id = int(estimate_id) if estimate_id is not None else None
    except (TypeError, ValueError):
        normalized_estimate_id = None
    if normalized_estimate_id is None:
        return {
            "success": False,
            "reason": "Linked estimate was not found.",
            **(context or {}),
        }

    tokens = get_estimate_delivery_tokens(
        normalized_estimate_id,
        attachment_path=final_file_path,
    )
    template_id, template_name, template_version_id, template_subject, template_body = _load_estimate_delivery_template()

    fallback_subject = _fallback_estimate_delivery_subject(tokens)
    fallback_body = _fallback_estimate_delivery_body(tokens)
    subject_template = template_subject or fallback_subject
    body_template = template_body or fallback_body

    try:
        rendered_subject = render_tokens(subject_template, tokens).strip() or fallback_subject
        rendered_body = render_tokens(body_template, tokens).strip() or fallback_body
    except Exception:
        rendered_subject = fallback_subject
        rendered_body = fallback_body
        template_id = None
        template_name = None
        template_version_id = None

    return {
        "success": True,
        "reason": "Ready to preview.",
        "EstimateDocumentDraftID": context.get("EstimateDocumentDraftID"),
        "EstimateID": context.get("EstimateID"),
        "CustomerName": context.get("CustomerName"),
        "CustomerEmail": context.get("CustomerEmail"),
        "OriginalCustomerEmail": context.get("CustomerEmail"),
        "ResolvedRecipientEmail": context.get("CustomerEmail"),
        "FinalFilePath": final_file_path,
        "AttachmentPath": final_file_path,
        "AttachmentFileName": attachment_file_name,
        "Subject": rendered_subject,
        "Body": rendered_body,
        "TemplateID": template_id,
        "TemplateUsed": template_name,
        "TemplateVersionID": template_version_id,
    }


def _looks_like_email(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "@" in text and "." in text.rsplit("@", 1)[-1]


def send_estimate_document_draft(
    draft_id: int,
    sent_by: str = "UI",
    override_recipient: str | None = None,
) -> dict[str, Any]:
    preview: dict[str, Any] | None = None
    prepared_log: dict[str, Any] | None = None
    sent_log: dict[str, Any] | None = None
    try:
        ensure_estimate_document_draft_table()
        ensure_outbound_message_log_table()

        can_send, reason, context = can_send_estimate_document_draft(draft_id)
        if not can_send:
            return {
                "success": False,
                "reason": reason,
                **(context or {}),
            }

        preview = prepare_estimate_delivery_message(draft_id)
        if not preview.get("success"):
            return preview

        original_customer_email = str(preview.get("CustomerEmail") or "").strip()
        recipient = str(override_recipient or "").strip() or original_customer_email
        if not _looks_like_email(recipient):
            return {
                "success": False,
                "reason": "Recipient email is invalid.",
                **preview,
            }

        draft_entity_id = preview.get("EstimateDocumentDraftID")
        estimate_entity_id = preview.get("EstimateID")
        try:
            normalized_draft_entity_id = int(draft_entity_id) if draft_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_draft_entity_id = None
        try:
            normalized_estimate_entity_id = int(estimate_entity_id) if estimate_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_estimate_entity_id = None
        if normalized_draft_entity_id is None or normalized_estimate_entity_id is None:
            return {
                "success": False,
                "reason": "Estimate draft send context is incomplete.",
                **preview,
            }

        prepared_log = record_prepared_message(
            entity_type="Estimate",
            entity_id=normalized_estimate_entity_id,
            related_draft_type="EstimateDocumentDraft",
            related_draft_id=normalized_draft_entity_id,
            template_code=(
                ESTIMATE_DELIVERY_TEMPLATE_CODE
                if preview.get("TemplateUsed")
                else "EstimateDelivery:fallback"
            ),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_customer_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
            created_by=sent_by,
        )

        send_ok = send_to_user(
            subject=str(preview.get("Subject") or ""),
            content=str(preview.get("Body") or ""),
            recipient=recipient,
            attachment_path=str(preview.get("FinalFilePath") or preview.get("AttachmentPath") or ""),
        )
        if not send_ok:
            failed_log = record_failed_message(
                entity_type="Estimate",
                entity_id=normalized_estimate_entity_id,
                related_draft_type="EstimateDocumentDraft",
                related_draft_id=normalized_draft_entity_id,
                template_code=(
                    ESTIMATE_DELIVERY_TEMPLATE_CODE
                    if preview.get("TemplateUsed")
                    else "EstimateDelivery:fallback"
                ),
                template_version_id=preview.get("TemplateVersionID"),
                recipient_email=recipient,
                original_recipient_email=original_customer_email,
                subject=preview.get("Subject"),
                body=preview.get("Body"),
                attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
                error_message="The email gateway did not confirm the send.",
                created_by=sent_by,
            )
            return {
                "success": False,
                "reason": "The email gateway did not confirm the send.",
                **preview,
                "PreparedLog": prepared_log,
                "FailedLog": failed_log,
                "ResolvedRecipientEmail": recipient,
                "OriginalCustomerEmail": original_customer_email,
            }

        sent_log = record_sent_message(
            entity_type="Estimate",
            entity_id=normalized_estimate_entity_id,
            related_draft_type="EstimateDocumentDraft",
            related_draft_id=normalized_draft_entity_id,
            template_code=(
                ESTIMATE_DELIVERY_TEMPLATE_CODE
                if preview.get("TemplateUsed")
                else "EstimateDelivery:fallback"
            ),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_customer_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
            created_by=sent_by,
        )
        sent_draft = mark_estimate_document_draft_sent(normalized_draft_entity_id)
        sent_estimate = mark_estimate_sent(normalized_estimate_entity_id)
        return {
            "success": True,
            "reason": "Estimate draft sent successfully.",
            **preview,
            "ResolvedRecipientEmail": recipient,
            "OriginalCustomerEmail": original_customer_email,
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
            "Draft": sent_draft,
            "Estimate": sent_estimate,
        }
    except Exception as exc:
        error_message = str(exc)
        failed_log = None
        try:
            failed_log = record_failed_message(
                entity_type="Estimate",
                entity_id=int(preview.get("EstimateID") or 0) if preview and preview.get("EstimateID") else 0,
                related_draft_type="EstimateDocumentDraft" if preview and preview.get("EstimateDocumentDraftID") else None,
                related_draft_id=preview.get("EstimateDocumentDraftID") if preview else None,
                template_code=(
                    ESTIMATE_DELIVERY_TEMPLATE_CODE
                    if preview and preview.get("TemplateUsed")
                    else "EstimateDelivery:fallback"
                ),
                template_version_id=preview.get("TemplateVersionID") if preview else None,
                recipient_email=(
                    str(override_recipient or "").strip()
                    or (str(preview.get("CustomerEmail") or "").strip() if preview else None)
                ),
                original_recipient_email=(
                    str(preview.get("CustomerEmail") or "").strip() if preview else None
                ),
                subject=preview.get("Subject") if preview else None,
                body=preview.get("Body") if preview else None,
                attachment_path=(
                    preview.get("FinalFilePath") or preview.get("AttachmentPath")
                    if preview
                    else None
                ),
                error_message=error_message,
                created_by=sent_by,
            ) if preview and preview.get("EstimateID") else None
        except Exception:
            failed_log = None
        return {
            "success": False,
            "reason": error_message,
            **(preview if isinstance(preview, dict) else {}),
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
            "FailedLog": failed_log,
            "DeliveryConfirmed": bool(sent_log),
        }
