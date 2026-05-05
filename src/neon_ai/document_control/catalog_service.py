from __future__ import annotations

from dataclasses import replace

from neon_ai.services.document_token_catalog import get_document_token_help

from .models import DocumentTemplateDefault, DocumentTemplateKind, DocumentTemplateVersion, DocumentTypeDefinition
from .repository import DocumentControlRepository
from .token_engine import extract_tokens


class DocumentCatalogService:
    def __init__(self, repository: DocumentControlRepository) -> None:
        self._repository = repository

    def list_document_types(self) -> list[DocumentTypeDefinition]:
        return self._repository.list_document_types()

    def list_templates(self, document_type_code: str | None = None):
        return self._repository.list_templates(document_type_code=document_type_code)

    def list_current_templates(
        self,
        *,
        document_type_code: str | None = None,
        template_kind: DocumentTemplateKind | str | None = None,
        include_inactive: bool = False,
    ):
        normalized_kind = None
        if template_kind is not None:
            normalized_kind = (
                template_kind.value
                if isinstance(template_kind, DocumentTemplateKind)
                else str(template_kind or "").strip().lower()
            )

        usable = []
        for summary in self._repository.list_templates(document_type_code=document_type_code):
            if document_type_code and str(summary.document_type_code or "").strip() != str(document_type_code).strip():
                continue
            if normalized_kind is not None:
                summary_kind = summary.kind.value if isinstance(summary.kind, DocumentTemplateKind) else str(summary.kind or "").strip().lower()
                if summary_kind != normalized_kind:
                    continue
            if not include_inactive and not bool(summary.is_active):
                continue

            normalized_summary = summary
            if summary.active_version_id is None or summary.active_version_number is None:
                version = self.get_template_version(template_id=int(summary.template_id))
                if version is None:
                    continue
                normalized_summary = replace(
                    summary,
                    active_version_id=version.template_version_id,
                    active_version_number=version.version_number,
                )
            usable.append(normalized_summary)

        usable.sort(
            key=lambda summary: (
                str(summary.document_type_code or "").lower(),
                summary.kind.value if isinstance(summary.kind, DocumentTemplateKind) else str(summary.kind or "").lower(),
                str(summary.template_name or "").lower(),
                int(summary.template_id or 0),
            )
        )
        return usable

    def get_template_version(
        self,
        template_id: int | None = None,
        version_id: int | None = None,
    ) -> DocumentTemplateVersion | None:
        return self._repository.get_template_version(template_id=template_id, version_id=version_id)

    def save_template(self, version: DocumentTemplateVersion) -> DocumentTemplateVersion:
        return self._repository.save_template_version(version)

    def activate_template(self, template_id: int) -> None:
        self._repository.activate_template(template_id)

    def list_template_defaults(
        self,
        document_type_code: str | None = None,
        usage_context: str | None = None,
    ) -> list[DocumentTemplateDefault]:
        return self._repository.list_template_defaults(
            document_type_code=document_type_code,
            usage_context=usage_context,
        )

    def get_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
    ) -> DocumentTemplateDefault | None:
        return self._repository.get_template_default(
            document_type_code=document_type_code,
            template_kind=template_kind,
            usage_context=usage_context,
        )

    def set_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
        template_id: int,
        updated_by: str = "UI",
    ) -> DocumentTemplateDefault:
        return self._repository.set_template_default(
            document_type_code=document_type_code,
            template_kind=template_kind,
            usage_context=usage_context,
            template_id=template_id,
            updated_by=updated_by,
        )

    def build_sample_context(self, document_type_code: str, seed: dict[str, object] | None = None) -> dict[str, object]:
        context = {
            "DocumentTypeCode": document_type_code,
            "CustomerName": "Sample Customer",
            "WorkOrderID": "WO-1001",
            "InvoiceNumber": "INV-1001",
            "InvoiceDate": "2026-01-01",
            "TotalAmount": "1250.00",
            "DocumentTitle": "Sample Document",
        }
        if document_type_code == "RFQ_DELIVERY":
            context.update(
                {
                    "RFQID": 1001,
                    "PriceRequestID": 1001,
                    "EstimateID": 42,
                    "VendorName": "Sample Vendor",
                    "VendorEmail": "vendor@example.com",
                    "DueDate": "2026-05-15",
                    "SiteName": "Sample Site",
                    "SiteAddress": "123 Sample Street, Sample City",
                    "AttachmentFileName": "RFQ_1001_SampleVendor.pdf",
                    "AttachmentPath": "D:/Neon_ai/generated_documents/SampleSite/RFQ_1001_SampleVendor.pdf",
                    "CompanyName": "Argon Electrical",
                    "OwnerName": "Project Team",
                    "DocumentTitle": "RFQ #1001",
                }
            )
        if document_type_code == "PURCHASE_ORDER":
            context.update(
                {
                    "PurchaseOrderID": 12,
                    "PurchaseOrderNumber": "PO-12",
                    "PODate": "2026-05-05",
                    "POStatus": "Draft",
                    "POTotal": "6789.10",
                    "POSubtotal": "6123.45",
                    "POTaxAmount": "0.00",
                    "POExpectedArrival": "2026-05-12",
                    "POETA": "2026-05-12",
                    "ExpectedArrivalDate": "2026-05-12",
                    "PONotes": "Deliver to site office.",
                    "PurchaseOrderNotes": "Deliver to site office.",
                    "VendorName": "Sample Vendor",
                    "VendorAddress": "880 Supply Way, Victoria, BC",
                    "VendorAccountNumber": "EEC-4421",
                    "SiteName": "Sample Site",
                    "SiteAddress": "123 Sample Street, Sample City",
                    "MaterialLineItemsHtml": (
                        "<table><thead><tr><th>Qty</th><th>Description</th><th>Unit Price</th><th>Ext Price</th></tr></thead>"
                        "<tbody><tr><td>12</td><td>14/2 NMD90</td><td>$10.00</td><td>$120.00</td></tr></tbody></table>"
                    ),
                    "MaterialLineItemsText": "Qty\tDescription\tUnit Price\tExt Price\n12\t14/2 NMD90\t$10.00\t$120.00",
                    "PurchaseOrderLineTable": (
                        "<table><thead><tr><th>Qty</th><th>Description</th><th>Unit Price</th><th>Ext Price</th></tr></thead>"
                        "<tbody><tr><td>12</td><td>14/2 NMD90</td><td>$10.00</td><td>$120.00</td></tr></tbody></table>"
                    ),
                    "DocumentTitle": "Purchase Order #12",
                }
            )
        if seed:
            context.update(seed)
        return context

    def get_token_help(self) -> dict[str, str]:
        return get_document_token_help()

    def extract_template_tokens(self, body_content: str) -> set[str]:
        return extract_tokens(body_content)
