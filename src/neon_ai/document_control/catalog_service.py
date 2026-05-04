from __future__ import annotations

from dataclasses import replace

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

            version = self.get_template_version(template_id=int(summary.template_id))
            if version is None:
                continue

            normalized_summary = summary
            if summary.active_version_id is None or summary.active_version_number is None:
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
        if seed:
            context.update(seed)
        return context

    def get_token_help(self) -> dict[str, str]:
        return {
            "CustomerName": "Customer or account name.",
            "CustomerEmail": "Primary customer email when available in the current workflow context.",
            "CustomerPhone": "Primary customer phone when available in the current workflow context.",
            "CustomerBillingAddress": "Customer billing address block when the workflow provides it.",
            "CustomerContactName": "Specific customer contact name when the workflow provides it.",
            "CustomerContactEmail": "Specific customer contact email when the workflow provides it.",
            "CustomerContactPhone": "Specific customer contact phone when the workflow provides it.",
            "CompanyName": "Company name used for the current document or message.",
            "CompanyAddress": "Company mailing or office address when configured for the workflow.",
            "CompanyPhone": "Main company phone when configured for the workflow.",
            "CompanyEmail": "Main company email when configured for the workflow.",
            "CompanyWebsite": "Company website when configured for the workflow.",
            "OwnerName": "Operator or owner signoff name.",
            "OwnerEmail": "Owner or sender email when configured for the workflow.",
            "OwnerPhone": "Owner or sender phone when configured for the workflow.",
            "SenderName": "Explicit sender display name for delivery messages.",
            "SenderEmail": "Explicit sender email for delivery messages.",
            "SenderTitle": "Explicit sender title or role for signoff blocks.",
            "SiteCity": "Project or site city when available from the current workflow.",
            "SitePostalCode": "Project or site postal code when available from the current workflow.",
            "ProjectName": "Project display name when distinct from site name.",
            "ProjectAddress": "Project address block when distinct from site address.",
            "ProjectDescription": "Project description when distinct from scope of work.",
            "EstimateNumber": "Customer-facing estimate number.",
            "EstimateDate": "Formatted estimate date.",
            "EstimateStatus": "Estimate status when the current workflow exposes it.",
            "EstimateTotal": "Calculated estimate total.",
            "EstimateSubtotal": "Calculated estimate subtotal before tax.",
            "EstimateMaterialSubtotal": "Calculated estimate material subtotal.",
            "EstimateLaborSubtotal": "Calculated estimate labor subtotal.",
            "EstimateTaxAmount": "Calculated estimate tax amount.",
            "EstimateMarkupPercent": "Estimate markup percentage when available.",
            "EstimateBillingType": "Estimate billing type when available.",
            "EstimateValidUntil": "Estimate validity / expiry date when available.",
            "EstimateTerms": "Estimate terms text.",
            "WorkOrderID": "Work order identifier.",
            "WorkOrderNumber": "Customer-facing or display work order number.",
            "WorkOrderStatus": "Work order status when available in the current workflow.",
            "WorkOrderDate": "Primary work order date when available.",
            "WorkOrderCreatedDate": "Work order created date when available.",
            "WorkOrderApprovedDate": "Work order approved date when available.",
            "WorkOrderClosedDate": "Work order closed date when available.",
            "CustomerPO": "Customer purchase order reference.",
            "AssignedElectrician": "Assigned electrician or field lead when available.",
            "CustomerInvoiceId": "Invoice primary key or internal invoice identifier.",
            "InvoiceNumber": "Customer-facing invoice number.",
            "InvoiceDate": "Formatted invoice date.",
            "DueDate": "Payment or delivery due date when provided by the current workflow.",
            "InvoiceStatus": "Invoice status when available in the current workflow.",
            "InvoiceType": "Invoice type label such as progress, manual, or T&M.",
            "InvoiceTotal": "Calculated invoice total.",
            "InvoiceSubtotal": "Calculated invoice subtotal before tax.",
            "InvoiceTaxAmount": "Calculated invoice tax amount.",
            "InvoiceBalanceDue": "Outstanding invoice balance when available.",
            "PaymentTerms": "Payment terms text used for invoice or delivery messages.",
            "PercentOfContract": "Invoice percent-of-contract value when available.",
            "BillingMilestone": "Billing milestone or progress billing note.",
            "TotalAmount": "Rendered total amount text.",
            "DocumentTitle": "Human-friendly preview title.",
            "RFQID": "Request for quotation identifier.",
            "PriceRequestID": "Database RFQ record identifier.",
            "EstimateID": "Estimate linked to the RFQ.",
            "RFQDate": "RFQ created or issued date when available in the workflow.",
            "RFQDueDate": "Requested RFQ due date.",
            "RFQStatus": "RFQ status when available in the workflow.",
            "RFQNumber": "Customer-facing RFQ number or label.",
            "RFQNotes": "RFQ notes text when available in the workflow.",
            "RequestedDueDate": "Requested due date text for RFQ or delivery workflows.",
            "PurchaseOrderID": "Purchase order identifier.",
            "PurchaseOrderNumber": "Customer-facing purchase order number.",
            "PODate": "Purchase order date.",
            "POStatus": "Purchase order status when available in the workflow.",
            "POTotal": "Calculated purchase order total.",
            "POSubtotal": "Calculated purchase order subtotal.",
            "POTaxAmount": "Calculated purchase order tax amount.",
            "POExpectedArrival": "Purchase order expected arrival date text.",
            "POETA": "Purchase order ETA text.",
            "PONotes": "Purchase order notes when available in the workflow.",
            "VendorName": "Vendor company name.",
            "VendorEmail": "Vendor contact email used for the RFQ.",
            "VendorPhone": "Vendor phone when available in the workflow.",
            "VendorAddress": "Vendor address when available in the workflow.",
            "VendorContactName": "Vendor contact name when available in the workflow.",
            "VendorAccountNumber": "Vendor account number when available in the workflow.",
            "VendorQuoteNumber": "Vendor quote or reference number when available.",
            "MaterialLineItems": "Structured material line items collection.",
            "MaterialLineItemsHtml": "Rendered HTML list of material line items.",
            "MaterialLineItemsText": "Rendered plain-text list of material line items.",
            "RFQRequestedMaterialTable": "Rendered RFQ requested-material table placed where the RFQ body template inserts the token.",
            "LaborLineItems": "Structured labor line items collection.",
            "LaborLineItemsHtml": "Rendered HTML list of labor line items.",
            "LaborLineItemsText": "Rendered plain-text list of labor line items.",
            "LineItems": "Structured combined line items collection.",
            "LineItemsHtml": "Rendered HTML list of combined line items.",
            "LineItemsText": "Rendered plain-text list of combined line items.",
            "PartNumber": "Material part number when a workflow exposes a single material row.",
            "MaterialDescription": "Material description when a workflow exposes a single material row.",
            "MaterialQuantity": "Material quantity when a workflow exposes a single material row.",
            "MaterialUnitCost": "Material unit cost when a workflow exposes a single material row.",
            "MaterialLineTotal": "Material extended total when a workflow exposes a single material row.",
            "MaterialNotes": "Material notes when a workflow exposes a single material row.",
            "LaborRole": "Labor role when a workflow exposes a single labor row.",
            "LaborHours": "Labor hours when a workflow exposes a single labor row.",
            "LaborRate": "Labor rate when a workflow exposes a single labor row.",
            "LaborLineTotal": "Labor extended total when a workflow exposes a single labor row.",
            "TotalEstimateAmount": "Financial summary total estimate amount.",
            "PreviouslyInvoicedAmount": "Financial summary previously invoiced amount.",
            "AmountStillToInvoice": "Financial summary remaining amount to invoice.",
            "LabourCostToDate": "Financial summary labour cost to date.",
            "ApprovedPurchaseOrderCost": "Financial summary approved purchase order cost.",
            "CommittedCost": "Financial summary committed cost.",
            "BillingPositionAmount": "Financial summary billing position amount.",
            "BillingPositionLabel": "Financial summary billing position label.",
            "EstimatePositionAmount": "Financial summary estimate position amount.",
            "EstimatePositionLabel": "Financial summary estimate position label.",
            "Today": "Current date at render time when a workflow exposes it.",
            "CurrentDate": "Current date at render time when a workflow exposes it.",
            "RequestedDate": "Requested date for workflow actions when available.",
            "DeliveryDate": "Requested or actual delivery date when available.",
            "PaymentDueDate": "Payment due date when available.",
            "DueDate": "Workflow due date such as invoice due date or requested RFQ due date.",
            "SiteName": "Project or site label.",
            "SiteAddress": "Formatted project/site address.",
            "AttachmentFileName": "Generated RFQ attachment filename.",
            "AttachmentPath": "Generated RFQ attachment full path.",
            "EstimateDocumentPath": "Resolved estimate document path when the workflow exposes it.",
            "InvoiceDocumentPath": "Resolved invoice document path when the workflow exposes it.",
            "RFQAttachmentPath": "Resolved RFQ attachment path when the workflow exposes it.",
            "PurchaseOrderDocumentPath": "Resolved purchase order document path when the workflow exposes it.",
        }

    def extract_template_tokens(self, body_content: str) -> set[str]:
        return extract_tokens(body_content)
