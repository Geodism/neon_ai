from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


CANONICAL_TOKEN_STYLE = "single-brace"
TOKEN_SYNTAX_NOTE = (
    "Canonical template syntax uses single braces like {CompanyName}. "
    "The renderer also accepts double braces like {{CompanyName}} for compatibility."
)


@dataclass(frozen=True)
class DocumentToken:
    category: str
    bare_token: str
    token: str
    description: str
    example_output: str
    used_in: str
    support_level: str


TOKEN_GUIDE_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Company / Sender",
        [
            "CompanyName",
            "CompanyAddress",
            "CompanyPhone",
            "CompanyEmail",
            "CompanyWebsite",
            "OwnerName",
            "OwnerEmail",
            "OwnerPhone",
            "SenderName",
            "SenderEmail",
            "SenderTitle",
        ],
    ),
    (
        "Customer",
        [
            "CustomerName",
            "CustomerEmail",
            "CustomerPhone",
            "CustomerBillingAddress",
            "CustomerContactName",
            "CustomerContactEmail",
            "CustomerContactPhone",
        ],
    ),
    (
        "Site / Project",
        [
            "SiteName",
            "SiteAddress",
            "SiteCity",
            "SitePostalCode",
            "ProjectName",
            "ProjectAddress",
            "ProjectDescription",
            "ScopeOfWork",
        ],
    ),
    (
        "Estimate",
        [
            "EstimateID",
            "EstimateNumber",
            "EstimateDate",
            "EstimateStatus",
            "EstimateTotal",
            "EstimateSubtotal",
            "EstimateMaterialSubtotal",
            "EstimateLaborSubtotal",
            "EstimateTaxAmount",
            "EstimateMarkupPercent",
            "EstimateBillingType",
            "EstimateValidUntil",
            "EstimateTerms",
        ],
    ),
    (
        "Work Order",
        [
            "WorkOrderID",
            "WorkOrderNumber",
            "WorkOrderStatus",
            "WorkOrderDate",
            "WorkOrderCreatedDate",
            "WorkOrderApprovedDate",
            "WorkOrderClosedDate",
            "CustomerPO",
            "AssignedElectrician",
        ],
    ),
    (
        "Customer Invoice",
        [
            "CustomerInvoiceId",
            "InvoiceID",
            "InvoiceNumber",
            "InvoiceDate",
            "DueDate",
            "InvoiceStatus",
            "InvoiceType",
            "InvoiceTotal",
            "InvoiceSubtotal",
            "InvoiceTaxAmount",
            "InvoiceBalanceDue",
            "PaymentTerms",
            "PercentOfContract",
            "BillingMilestone",
        ],
    ),
    (
        "RFQ",
        [
            "RFQID",
            "PriceRequestID",
            "RFQDate",
            "RFQDueDate",
            "RFQStatus",
            "RFQNumber",
            "RFQNotes",
            "RequestedDueDate",
        ],
    ),
    (
        "Purchase Order",
        [
            "PurchaseOrderID",
            "PurchaseOrderNumber",
            "PODate",
            "POStatus",
            "POTotal",
            "POSubtotal",
            "POTaxAmount",
            "POExpectedArrival",
            "POETA",
            "PONotes",
            "ExpectedArrivalDate",
            "PurchaseOrderNotes",
        ],
    ),
    (
        "Vendor / Wholesaler",
        [
            "VendorName",
            "VendorEmail",
            "VendorPhone",
            "VendorAddress",
            "VendorContactName",
            "VendorAccountNumber",
            "VendorQuoteNumber",
        ],
    ),
    (
        "Materials / Line Items",
        [
            "MaterialLineItems",
            "MaterialLineItemsHtml",
            "MaterialLineItemsText",
            "RFQRequestedMaterialTable",
            "PurchaseOrderLineTable",
            "LaborLineItems",
            "LaborLineItemsHtml",
            "LaborLineItemsText",
            "LineItems",
            "LineItemsHtml",
            "LineItemsText",
            "PartNumber",
            "MaterialDescription",
            "MaterialQuantity",
            "MaterialUnitCost",
            "MaterialLineTotal",
            "MaterialNotes",
            "LaborRole",
            "LaborHours",
            "LaborRate",
            "LaborLineTotal",
        ],
    ),
    (
        "Financial Summary",
        [
            "TotalEstimateAmount",
            "PreviouslyInvoicedAmount",
            "AmountStillToInvoice",
            "LabourCostToDate",
            "ApprovedPurchaseOrderCost",
            "CommittedCost",
            "BillingPositionAmount",
            "BillingPositionLabel",
            "EstimatePositionAmount",
            "EstimatePositionLabel",
        ],
    ),
    (
        "Dates / Terms",
        [
            "Today",
            "CurrentDate",
            "RequestedDate",
            "DeliveryDate",
            "PaymentDueDate",
            "Terms",
        ],
    ),
    (
        "Attachments / Files",
        [
            "AttachmentFileName",
            "AttachmentPath",
            "EstimateDocumentPath",
            "InvoiceDocumentPath",
            "RFQAttachmentPath",
            "PurchaseOrderDocumentPath",
        ],
    ),
    (
        "General",
        [
            "DocumentTitle",
            "TotalAmount",
        ],
    ),
]


SUPPORTED_TEMPLATE_TOKENS: set[str] = {
    "CompanyName",
    "OwnerName",
    "CustomerName",
    "CustomerEmail",
    "CustomerPhone",
    "SiteName",
    "SiteAddress",
    "ScopeOfWork",
    "EstimateID",
    "EstimateNumber",
    "EstimateDate",
    "EstimateTotal",
    "EstimateSubtotal",
    "WorkOrderID",
    "CustomerInvoiceId",
    "InvoiceID",
    "InvoiceNumber",
    "InvoiceDate",
    "DueDate",
    "InvoiceType",
    "InvoiceTotal",
    "PaymentTerms",
    "PercentOfContract",
    "BillingMilestone",
    "RFQID",
    "PriceRequestID",
    "VendorName",
    "VendorEmail",
    "PurchaseOrderID",
    "PurchaseOrderNumber",
    "PODate",
    "POStatus",
    "POTotal",
    "POSubtotal",
    "POTaxAmount",
    "POExpectedArrival",
    "POETA",
    "PONotes",
    "ExpectedArrivalDate",
    "PurchaseOrderNotes",
    "MaterialLineItems",
    "MaterialLineItemsHtml",
    "MaterialLineItemsText",
    "RFQRequestedMaterialTable",
    "PurchaseOrderLineTable",
    "LaborLineItems",
    "LaborLineItemsHtml",
    "LaborLineItemsText",
    "LineItems",
    "LineItemsHtml",
    "LineItemsText",
    "Terms",
    "AttachmentFileName",
    "AttachmentPath",
}


PARTIAL_TEMPLATE_TOKENS: set[str] = {
    "CompanyAddress",
    "CompanyPhone",
    "CompanyEmail",
    "CompanyWebsite",
    "OwnerEmail",
    "OwnerPhone",
    "SenderName",
    "SenderEmail",
    "SenderTitle",
    "CustomerBillingAddress",
    "CustomerContactName",
    "CustomerContactEmail",
    "CustomerContactPhone",
    "SiteCity",
    "SitePostalCode",
    "ProjectName",
    "ProjectAddress",
    "ProjectDescription",
    "EstimateMaterialSubtotal",
    "EstimateLaborSubtotal",
    "EstimateTaxAmount",
    "WorkOrderNumber",
    "InvoiceSubtotal",
    "InvoiceTaxAmount",
    "RFQDueDate",
    "RFQNumber",
    "RequestedDueDate",
    "RFQAttachmentPath",
}


TOKEN_HELP_MAP: dict[str, str] = {
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
    "InvoiceID": "Invoice primary key alias used by invoice delivery and document workflows.",
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
    "EstimateID": "Estimate linked to the RFQ or estimate document.",
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
    "ExpectedArrivalDate": "Purchase order expected arrival date alias used by PO document templates.",
    "PurchaseOrderNotes": "Purchase order notes alias used by PO document templates.",
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
    "PurchaseOrderLineTable": "Rendered purchase-order line-item table placed where the PO body template inserts the token.",
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
    "Terms": "General terms text used by estimate or document workflows.",
    "SiteName": "Project or site label.",
    "SiteAddress": "Formatted project/site address.",
    "AttachmentFileName": "Generated attachment filename for delivery workflows.",
    "AttachmentPath": "Generated attachment full path.",
    "EstimateDocumentPath": "Resolved estimate document path when the workflow exposes it.",
    "InvoiceDocumentPath": "Resolved invoice document path when the workflow exposes it.",
    "RFQAttachmentPath": "Resolved RFQ attachment path when the workflow exposes it.",
    "PurchaseOrderDocumentPath": "Resolved purchase order document path when the workflow exposes it.",
}


TOKEN_EXAMPLE_OVERRIDES: dict[str, str] = {
    "CompanyName": "Argon Electrical",
    "CompanyAddress": "123 Sample Street, Victoria, BC",
    "CompanyPhone": "250-555-0100",
    "CompanyEmail": "info@example.com",
    "CompanyWebsite": "https://example.com",
    "OwnerName": "Project Team",
    "OwnerEmail": "owner@example.com",
    "OwnerPhone": "250-555-0199",
    "SenderName": "Alex Project Manager",
    "SenderEmail": "alex@example.com",
    "SenderTitle": "Project Manager",
    "CustomerName": "TEST_3MO_Customer_05",
    "CustomerEmail": "customer@example.com",
    "CustomerPhone": "250-555-0100",
    "CustomerBillingAddress": "Accounts Payable, 500 Billing Ave, Victoria, BC",
    "CustomerContactName": "Casey Customer",
    "CustomerContactEmail": "casey.customer@example.com",
    "CustomerContactPhone": "250-555-0105",
    "SiteName": "TEST_3MO_Customer_05_Site",
    "SiteAddress": "101 Test Site Road, Victoria, BC",
    "SiteCity": "Victoria",
    "SitePostalCode": "V8V 1A1",
    "ProjectName": "Panel Upgrade",
    "ProjectAddress": "101 Test Site Road, Victoria, BC",
    "ProjectDescription": "Panel upgrade and lighting refresh",
    "ScopeOfWork": "Supply and install new distribution equipment.",
    "EstimateID": "5",
    "EstimateNumber": "EST-5",
    "EstimateDate": "2026-05-04",
    "EstimateStatus": "Draft",
    "EstimateTotal": "$12,345.67",
    "EstimateSubtotal": "$11,111.11",
    "EstimateMaterialSubtotal": "$5,432.10",
    "EstimateLaborSubtotal": "$5,679.01",
    "EstimateTaxAmount": "$1,234.56",
    "EstimateMarkupPercent": "20%",
    "EstimateBillingType": "Fixed Price",
    "EstimateValidUntil": "2026-06-03",
    "EstimateTerms": "Estimate valid for 30 days.",
    "WorkOrderID": "4",
    "WorkOrderNumber": "WO-4",
    "WorkOrderStatus": "Open",
    "WorkOrderDate": "2026-05-04",
    "WorkOrderCreatedDate": "2026-05-02",
    "WorkOrderApprovedDate": "2026-05-03",
    "WorkOrderClosedDate": "2026-05-20",
    "CustomerPO": "PO-7782",
    "AssignedElectrician": "Jordan Field Lead",
    "CustomerInvoiceId": "7",
    "InvoiceID": "7",
    "InvoiceNumber": "INV-7",
    "InvoiceDate": "2026-05-04",
    "DueDate": "2026-06-03",
    "InvoiceStatus": "Draft",
    "InvoiceType": "Progress",
    "InvoiceTotal": "$12,345.67",
    "InvoiceSubtotal": "$11,111.11",
    "InvoiceTaxAmount": "$1,234.56",
    "InvoiceBalanceDue": "$4,567.89",
    "PaymentTerms": "Net 30",
    "PercentOfContract": "50%",
    "BillingMilestone": "Rough-in complete",
    "TotalAmount": "$12,345.67",
    "DocumentTitle": "Sample Document",
    "RFQID": "10",
    "PriceRequestID": "10",
    "RFQDate": "2026-05-04",
    "RFQDueDate": "2026-05-10",
    "RFQStatus": "Draft",
    "RFQNumber": "10",
    "RFQNotes": "Need pricing for missed material lines.",
    "RequestedDueDate": "2026-05-10",
    "PurchaseOrderID": "12",
    "PurchaseOrderNumber": "PO-12",
    "PODate": "2026-05-04",
    "POStatus": "Draft",
    "POTotal": "$6,789.10",
    "POSubtotal": "$6,123.45",
    "POTaxAmount": "$665.65",
    "POExpectedArrival": "2026-05-12",
    "POETA": "2026-05-12",
    "PONotes": "Deliver to site office.",
    "ExpectedArrivalDate": "2026-05-12",
    "PurchaseOrderNotes": "Deliver to site office.",
    "VendorName": "TEST_3MO_Eecol",
    "VendorEmail": "vendor@example.com",
    "VendorPhone": "250-555-0110",
    "VendorAddress": "880 Supply Way, Victoria, BC",
    "VendorContactName": "Taylor Buyer",
    "VendorAccountNumber": "EEC-4421",
    "VendorQuoteNumber": "Q-2026-199",
    "MaterialLineItems": "2 material lines",
    "MaterialLineItemsHtml": "<ul><li>14/2 NMD90 - $120.00</li></ul>",
    "MaterialLineItemsText": "14/2 NMD90 - $120.00",
    "RFQRequestedMaterialTable": "Qty / Unit / Part Number / Description / Notes table",
    "PurchaseOrderLineTable": "<table><tr><th>Qty</th><th>Description</th><th>Unit Price</th><th>Ext Price</th></tr><tr><td>12</td><td>14/2 NMD90</td><td>$10.00</td><td>$120.00</td></tr></table>",
    "LaborLineItems": "2 labor lines",
    "LaborLineItemsHtml": "<ul><li>Journeyperson - $450.00</li></ul>",
    "LaborLineItemsText": "Journeyperson - $450.00",
    "LineItems": "4 line items",
    "LineItemsHtml": "<ul><li>Journeyperson - $450.00</li><li>14/2 NMD90 - $120.00</li></ul>",
    "LineItemsText": "Journeyperson - $450.00\n14/2 NMD90 - $120.00",
    "PartNumber": "ABC-123",
    "MaterialDescription": "14/2 NMD90",
    "MaterialQuantity": "12",
    "MaterialUnitCost": "$10.00",
    "MaterialLineTotal": "$120.00",
    "MaterialNotes": "Need pricing confirmation",
    "LaborRole": "Journeyperson",
    "LaborHours": "6",
    "LaborRate": "$75.00",
    "LaborLineTotal": "$450.00",
    "TotalEstimateAmount": "$12,345.67",
    "PreviouslyInvoicedAmount": "$6,172.84",
    "AmountStillToInvoice": "$6,172.83",
    "LabourCostToDate": "$3,210.00",
    "ApprovedPurchaseOrderCost": "$4,500.00",
    "CommittedCost": "$5,750.00",
    "BillingPositionAmount": "$6,172.84",
    "BillingPositionLabel": "Progress Invoice 2",
    "EstimatePositionAmount": "$12,345.67",
    "EstimatePositionLabel": "Approved Estimate",
    "Today": "2026-05-04",
    "CurrentDate": "2026-05-04",
    "RequestedDate": "2026-05-04",
    "DeliveryDate": "2026-05-12",
    "PaymentDueDate": "2026-06-03",
    "Terms": "Standard terms apply.",
    "AttachmentFileName": "RFQ_5_TEST_3MO_Eecol_10.pdf",
    "AttachmentPath": "D:/Neon_ai/generated_documents/SampleSite/RFQ_5_TEST_3MO_Eecol_10.pdf",
    "EstimateDocumentPath": "D:/Neon_ai/generated_documents/Estimates/Estimate_5.html",
    "InvoiceDocumentPath": "D:/Neon_ai/generated_documents/Invoices/Invoice_7.html",
    "RFQAttachmentPath": "D:/Neon_ai/generated_documents/RFQ/RFQ_10.pdf",
    "PurchaseOrderDocumentPath": "D:/Neon_ai/generated_documents/PO/PO_12.pdf",
}


CATEGORY_USED_IN_MAP: dict[str, str] = {
    "Company / Sender": "General templates, estimates, RFQ, invoices, purchase orders",
    "Customer": "Estimate documents, invoice documents",
    "Site / Project": "Estimate documents, RFQ, invoices, purchase orders",
    "Estimate": "Estimate documents, RFQ",
    "Work Order": "RFQ, purchase orders, invoices",
    "Customer Invoice": "Customer invoice templates",
    "RFQ": "RFQ Delivery Body",
    "Purchase Order": "Purchase order templates",
    "Vendor / Wholesaler": "RFQ, purchase orders",
    "Materials / Line Items": "Estimate, invoice, RFQ, and purchase order templates",
    "Financial Summary": "Estimate and invoice templates",
    "Dates / Terms": "General templates, RFQ, invoices, purchase orders",
    "Attachments / Files": "Delivery email templates",
    "General": "General templates",
}


USED_IN_OVERRIDES: dict[str, str] = {
    "RFQRequestedMaterialTable": "RFQ Delivery Body",
    "PurchaseOrderLineTable": "Purchase Order Body",
    "AttachmentFileName": "RFQ, estimate, invoice, and purchase order delivery templates",
    "RFQAttachmentPath": "RFQ delivery templates",
    "InvoiceDocumentPath": "Customer invoice delivery templates",
    "EstimateDocumentPath": "Estimate delivery templates",
    "PurchaseOrderDocumentPath": "Purchase order delivery templates",
    "InvoiceID": "Customer invoice templates",
}


def format_document_token(token_name: str, *, style: str = CANONICAL_TOKEN_STYLE) -> str:
    bare_name = str(token_name or "").strip("{} ")
    if style == "double-brace":
        return f"{{{{{bare_name}}}}}"
    return f"{{{bare_name}}}"


def get_document_token_help() -> dict[str, str]:
    return dict(TOKEN_HELP_MAP)


def get_token_guide_groups() -> list[tuple[str, list[str]]]:
    return [(category, list(tokens)) for category, tokens in TOKEN_GUIDE_GROUPS]


def support_level_for_token(token_name: str) -> str:
    if token_name in SUPPORTED_TEMPLATE_TOKENS:
        return "Supported"
    if token_name in PARTIAL_TEMPLATE_TOKENS:
        return "Partial"
    return "Planned"


def _default_example_output(token_name: str, category: str) -> str:
    if token_name in TOKEN_EXAMPLE_OVERRIDES:
        return TOKEN_EXAMPLE_OVERRIDES[token_name]
    if token_name.endswith("Email"):
        return "example@example.com"
    if token_name.endswith("Phone"):
        return "250-555-0100"
    if token_name.endswith("Address"):
        return "123 Sample Street, Victoria, BC"
    if "Date" in token_name or token_name.endswith("Today"):
        return "2026-05-04"
    if token_name.endswith("Status"):
        return "Draft"
    if token_name.endswith("Total") or token_name.endswith("Subtotal") or token_name.endswith("Amount") or token_name.endswith("Cost") or token_name.endswith("Rate"):
        return "$12,345.67"
    if token_name.endswith("Percent"):
        return "20%"
    if token_name.endswith("Path"):
        return f"D:/Neon_ai/generated_documents/{token_name}.html"
    if token_name.endswith("FileName"):
        return f"{token_name}.pdf"
    if token_name.endswith("ID") or token_name.endswith("Number"):
        return "1"
    if category == "Materials / Line Items":
        return "Workflow-specific line item output"
    return "Sample output"


def _used_in_value(category: str, token_name: str) -> str:
    return USED_IN_OVERRIDES.get(token_name, CATEGORY_USED_IN_MAP.get(category, "General templates"))


@lru_cache(maxsize=1)
def list_document_tokens() -> tuple[DocumentToken, ...]:
    records: list[DocumentToken] = []
    seen: set[str] = set()
    for category, token_names in TOKEN_GUIDE_GROUPS:
        for bare_token in token_names:
            if bare_token in seen:
                continue
            seen.add(bare_token)
            records.append(
                DocumentToken(
                    category=category,
                    bare_token=bare_token,
                    token=format_document_token(bare_token),
                    description=TOKEN_HELP_MAP.get(
                        bare_token,
                        "Workflow-specific token. Missing values will be surfaced during preview/render.",
                    ),
                    example_output=_default_example_output(bare_token, category),
                    used_in=_used_in_value(category, bare_token),
                    support_level=support_level_for_token(bare_token),
                )
            )
    return tuple(records)


def grouped_document_tokens() -> list[tuple[str, list[DocumentToken]]]:
    grouped: list[tuple[str, list[DocumentToken]]] = []
    all_tokens = list_document_tokens()
    for category, _token_names in TOKEN_GUIDE_GROUPS:
        category_tokens = [token for token in all_tokens if token.category == category]
        grouped.append((category, category_tokens))
    return grouped
