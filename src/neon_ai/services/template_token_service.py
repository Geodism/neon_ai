from __future__ import annotations

import datetime
import html
import os
from typing import Any

from neon_ai.database.estimates import get_detailed_estimate_data
from neon_ai.database.invoices import get_invoice_detail


def _money(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_money(value: Any) -> str:
    return f"${_money(value):,.2f}"


def _clean_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _site_address(parent: dict[str, Any]) -> str:
    parts = [
        _clean_text(parent.get("SiteNum")),
        _clean_text(parent.get("SiteStreet")),
        _clean_text(parent.get("SiteCity")),
    ]
    joined = " ".join(part for part in parts[:2] if part).strip()
    city = parts[2]
    if joined and city:
        return f"{joined}, {city}"
    if joined:
        return joined
    if city:
        return city
    return "Site address pending"


def _build_customer_block(parent: dict[str, Any]) -> str:
    lines = [
        _clean_text(parent.get("CustomerName"), "Customer"),
        " ".join(
            part
            for part in (
                _clean_text(parent.get("CustAddr")),
                _clean_text(parent.get("CustStreet")),
            )
            if part
        ).strip(),
        ", ".join(
            part
            for part in (
                _clean_text(parent.get("CustCity")),
                _clean_text(parent.get("CustZip")),
            )
            if part
        ).strip(", "),
        f"Ph: {_clean_text(parent.get('Phone'), 'N/A')}",
        f"Email: {_clean_text(parent.get('Email'), 'N/A')}",
    ]
    return "\n".join(line for line in lines if line)


def _line_items_from_estimate(data: dict[str, Any]) -> dict[str, Any]:
    parent = data.get("parent") or {}
    labor_markup = _money(parent.get("LaborMarkUp") or 20.0) / 100.0
    material_markup = _money(parent.get("MaterialMarkUp") or 20.0) / 100.0

    labor_rows: list[dict[str, Any]] = []
    material_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    labor_subtotal = 0.0
    for line in data.get("labor") or []:
        hours = _money(line.get("Hours"))
        rate = _money(line.get("Rate"))
        base_total = hours * rate
        sell_total = base_total * (1 + labor_markup)
        row = {
            "LineType": "Labor",
            "Description": _clean_text(line.get("RoleDescription"), "Labor"),
            "Hours": hours,
            "Rate": rate,
            "BaseTotal": round(base_total, 2),
            "SellTotal": round(sell_total, 2),
            "SellTotalFormatted": _fmt_money(sell_total),
        }
        labor_rows.append(row)
        all_rows.append(row)
        labor_subtotal += sell_total

    material_subtotal = 0.0
    for line in data.get("materials") or []:
        quantity = _money(line.get("Quantity"))
        unit_cost = _money(line.get("UnitCost"))
        base_total = quantity * unit_cost
        sell_total = base_total * (1 + material_markup)
        row = {
            "LineType": "Material",
            "Description": _clean_text(line.get("Description"), "Material"),
            "Quantity": quantity,
            "UnitCost": unit_cost,
            "BaseTotal": round(base_total, 2),
            "SellTotal": round(sell_total, 2),
            "SellTotalFormatted": _fmt_money(sell_total),
        }
        material_rows.append(row)
        all_rows.append(row)
        material_subtotal += sell_total

    estimate_subtotal = labor_subtotal + material_subtotal
    return {
        "LaborLineItems": labor_rows,
        "MaterialLineItems": material_rows,
        "LineItems": all_rows,
        "LaborSubtotal": round(labor_subtotal, 2),
        "MaterialSubtotal": round(material_subtotal, 2),
        "EstimateSubtotal": round(estimate_subtotal, 2),
        "EstimateTotal": round(estimate_subtotal, 2),
    }


def _rows_to_text(rows: list[dict[str, Any]], include_type: bool = False) -> str:
    lines: list[str] = []
    for row in rows:
        prefix = f"[{row.get('LineType')}] " if include_type else ""
        lines.append(
            f"{prefix}{row.get('Description', '')}: {row.get('SellTotalFormatted', _fmt_money(row.get('SellTotal')))}"
        )
    return "\n".join(lines) if lines else "None"


def _rows_to_html(rows: list[dict[str, Any]], include_type: bool = False) -> str:
    if not rows:
        return "<p>None</p>"
    items = []
    for row in rows:
        prefix = f"[{html.escape(str(row.get('LineType', '')))}] " if include_type else ""
        desc = html.escape(str(row.get("Description", "")))
        amount = html.escape(str(row.get("SellTotalFormatted", _fmt_money(row.get("SellTotal")))))
        items.append(f"<li>{prefix}{desc} - {amount}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def get_estimate_document_tokens(estimate_id: int) -> dict[str, Any]:
    data = get_detailed_estimate_data(estimate_id)
    if not data or not data.get("parent"):
        raise ValueError(f"Estimate #{estimate_id} could not be loaded.")

    parent = data["parent"]
    line_breakdown = _line_items_from_estimate(data)
    estimate_date = parent.get("CreatedDate")
    scope = _clean_text(parent.get("Description"), "Standard electrical scope.")
    company_name = (
        os.environ.get("NEON_COMPANY_NAME")
        or os.environ.get("COMPANY_NAME")
        or "Argon Electrical"
    )
    owner_name = (
        os.environ.get("NEON_OWNER_NAME")
        or os.environ.get("OWNER_NAME")
        or "Project Team"
    )
    terms = (
        os.environ.get("NEON_ESTIMATE_TERMS")
        or "Estimate pricing is based on the listed scope of work and current assumptions."
    )

    tokens: dict[str, Any] = {
        "EstimateID": parent.get("EstimateID"),
        "EstimateDate": str(estimate_date or ""),
        "EstimateNumber": parent.get("EstimateID"),
        "CustomerName": _clean_text(parent.get("CustomerName"), "Customer"),
        "CustomerEmail": _clean_text(parent.get("Email")),
        "CustomerPhone": _clean_text(parent.get("Phone"), "N/A"),
        "CustomerInfo": _build_customer_block(parent),
        "SiteName": _clean_text(parent.get("SiteName"), "Project Site"),
        "SiteAddress": _site_address(parent),
        "ScopeOfWork": scope,
        "TaxAmount": 0.0,
        "TaxAmountFormatted": _fmt_money(0.0),
        "Terms": terms,
        "CompanyName": company_name,
        "OwnerName": owner_name,
        "DocumentTitle": f"Estimate #{parent.get('EstimateID')}",
    }
    tokens.update(line_breakdown)
    tokens.update(
        {
            "LaborSubtotalFormatted": _fmt_money(tokens["LaborSubtotal"]),
            "MaterialSubtotalFormatted": _fmt_money(tokens["MaterialSubtotal"]),
            "EstimateSubtotalFormatted": _fmt_money(tokens["EstimateSubtotal"]),
            "EstimateTotalFormatted": _fmt_money(tokens["EstimateTotal"]),
            "LaborLineItemsText": _rows_to_text(tokens["LaborLineItems"]),
            "MaterialLineItemsText": _rows_to_text(tokens["MaterialLineItems"]),
            "LineItemsText": _rows_to_text(tokens["LineItems"], include_type=True),
            "LaborLineItemsHtml": _rows_to_html(tokens["LaborLineItems"]),
            "MaterialLineItemsHtml": _rows_to_html(tokens["MaterialLineItems"]),
            "LineItemsHtml": _rows_to_html(tokens["LineItems"], include_type=True),
        }
    )
    return tokens


def validate_estimate_document_tokens(tokens: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    required_fields = (
        "EstimateID",
        "EstimateDate",
        "CustomerName",
        "SiteAddress",
        "ScopeOfWork",
    )
    for field_name in required_fields:
        value = tokens.get(field_name)
        if value is None or str(value).strip() == "":
            missing.append(field_name)

    if not tokens.get("LaborLineItems") and not tokens.get("MaterialLineItems"):
        missing.append("LineItems")

    if tokens.get("EstimateTotal") is None:
        missing.append("EstimateTotal")

    return (len(missing) == 0, missing)


def build_estimate_document_fallback_text(
    estimate_id: int | None = None,
    tokens: dict[str, Any] | None = None,
) -> str:
    resolved_tokens = dict(tokens or {})
    if not resolved_tokens:
        if estimate_id is None:
            raise ValueError("estimate_id or tokens is required to build fallback estimate draft content.")
        resolved_tokens = get_estimate_document_tokens(estimate_id)

    estimate_id_value = resolved_tokens.get("EstimateID") or "Unknown"
    estimate_date = _clean_text(resolved_tokens.get("EstimateDate"), "Pending")
    customer_name = _clean_text(resolved_tokens.get("CustomerName"), "Customer")
    customer_email = _clean_text(resolved_tokens.get("CustomerEmail"), "N/A")
    site_address = _clean_text(resolved_tokens.get("SiteAddress"), "Site address pending")
    scope = _clean_text(resolved_tokens.get("ScopeOfWork"), "Scope of work pending.")
    estimate_total = _fmt_money(resolved_tokens.get("EstimateTotal"))
    labor_lines = _clean_text(resolved_tokens.get("LaborLineItemsText"), "None")
    material_lines = _clean_text(resolved_tokens.get("MaterialLineItemsText"), "None")
    terms = _clean_text(
        resolved_tokens.get("Terms"),
        "Estimate pricing is based on the listed scope of work and current assumptions.",
    )
    company_name = _clean_text(resolved_tokens.get("CompanyName"), "Neon_ai")
    owner_name = _clean_text(resolved_tokens.get("OwnerName"), "Project Team")

    sections = [
        f"Estimate #{estimate_id_value}",
        f"Date: {estimate_date}",
        "",
        f"Customer: {customer_name}",
        f"Customer Email: {customer_email}",
        f"Project Site: {site_address}",
        "",
        "Scope of Work",
        scope,
        "",
        "Estimated Labor Services",
        labor_lines,
        "",
        "Estimated Material Requirements",
        material_lines,
        "",
        f"Total Estimated Project Price: {estimate_total}",
        "",
        "Terms",
        terms,
        "",
        f"{company_name}",
        owner_name,
    ]
    return "\n".join(sections).strip()


def _derive_invoice_due_date(invoice_date_value: Any) -> str:
    text = str(invoice_date_value or "").strip()
    if not text:
        return ""
    for parser in (datetime.date.fromisoformat, lambda value: datetime.datetime.strptime(value, "%Y-%m-%d").date()):
        try:
            parsed = parser(text)
            return (parsed + datetime.timedelta(days=30)).isoformat()
        except Exception:
            continue
    return ""


def _normalize_invoice_type(billing_mode: Any) -> str:
    normalized = str(billing_mode or "").strip().lower()
    if normalized in {"t&m", "time and materials", "time & materials"}:
        return "WeeklyTM"
    if normalized in {"stipulated", "stipulated price", "fixed price"}:
        return "Progress"
    return "Manual"


def _build_invoice_line_items(detail: dict[str, Any]) -> dict[str, Any]:
    invoice = detail.get("invoice") or {}
    memory = detail.get("memory") or {}
    header = detail.get("header") or {}
    labor_lines = list(memory.get("labor_lines") or [])
    material_lines = list(memory.get("material_lines") or [])
    all_rows: list[dict[str, Any]] = []

    labor_amount = 0.0
    normalized_labor_rows: list[dict[str, Any]] = []
    for row in labor_lines:
        line_total = _money(row.get("LineTotal"))
        labor_amount += line_total
        normalized = {
            "LineType": "Labor",
            "Description": _clean_text(
                f"{row.get('DateWorked') or ''} | {row.get('EmployeeName') or ''} | {row.get('TaskName') or ''}".strip(" |"),
                "Labor",
            ),
            "SellTotal": round(line_total, 2),
            "SellTotalFormatted": _fmt_money(line_total),
        }
        normalized_labor_rows.append(normalized)
        all_rows.append(normalized)

    material_amount = 0.0
    normalized_material_rows: list[dict[str, Any]] = []
    for row in material_lines:
        line_total = _money(row.get("LineTotal"))
        material_amount += line_total
        normalized = {
            "LineType": "Material",
            "Description": _clean_text(
                f"PO #{row.get('PurchaseOrderID') or ''} | {row.get('Description') or ''}".strip(" |"),
                "Material",
            ),
            "SellTotal": round(line_total, 2),
            "SellTotalFormatted": _fmt_money(line_total),
        }
        normalized_material_rows.append(normalized)
        all_rows.append(normalized)

    if not all_rows:
        billing_mode = str(invoice.get("BillingMode") or header.get("BillingType") or "").strip()
        if billing_mode:
            labor_percent = _money(invoice.get("LaborPercent"))
            material_percent = _money(invoice.get("MaterialPercent"))
            estimate_total = _money(header.get("EstimateTotal"))
            if labor_percent:
                labor_amount = estimate_total * labor_percent / 100.0
                normalized = {
                    "LineType": "Labor",
                    "Description": _clean_text(invoice.get("LaborMilestoneNote"), "Labor progress claim"),
                    "SellTotal": round(labor_amount, 2),
                    "SellTotalFormatted": _fmt_money(labor_amount),
                }
                normalized_labor_rows.append(normalized)
                all_rows.append(normalized)
            if material_percent:
                material_amount = estimate_total * material_percent / 100.0
                normalized = {
                    "LineType": "Material",
                    "Description": _clean_text(invoice.get("MaterialMilestoneNote"), "Material progress claim"),
                    "SellTotal": round(material_amount, 2),
                    "SellTotalFormatted": _fmt_money(material_amount),
                }
                normalized_material_rows.append(normalized)
                all_rows.append(normalized)

    subtotal = labor_amount + material_amount
    invoice_total = _money(invoice.get("CustomerInvoiceAmount") or subtotal)
    return {
        "LaborLineItems": normalized_labor_rows,
        "MaterialLineItems": normalized_material_rows,
        "LineItems": all_rows,
        "LaborAmount": round(labor_amount, 2),
        "MaterialAmount": round(material_amount, 2),
        "Subtotal": round(subtotal, 2),
        "InvoiceTotal": round(invoice_total, 2),
    }


def get_customer_invoice_document_tokens(invoice_id: int) -> dict[str, Any]:
    detail = get_invoice_detail(invoice_id)
    if not detail or not detail.get("invoice"):
        raise ValueError(f"Invoice #{invoice_id} could not be loaded.")

    invoice = detail.get("invoice") or {}
    header = detail.get("header") or {}
    memory = detail.get("memory") or {}
    line_breakdown = _build_invoice_line_items(detail)
    invoice_date = header.get("CustomerInvoiceDate") or invoice.get("CustomerInvoiceDate")
    due_date = _derive_invoice_due_date(invoice_date)
    invoice_type = _normalize_invoice_type(invoice.get("BillingMode") or header.get("BillingType"))
    labor_note = _clean_text(invoice.get("LaborMilestoneNote"))
    material_note = _clean_text(invoice.get("MaterialMilestoneNote"))
    billing_milestone = " | ".join(bit for bit in (labor_note, material_note) if bit)
    payment_terms = os.environ.get("NEON_INVOICE_PAYMENT_TERMS") or "Net 30"
    company_name = (
        os.environ.get("NEON_COMPANY_NAME")
        or os.environ.get("COMPANY_NAME")
        or "Neon_ai"
    )
    owner_name = (
        os.environ.get("NEON_OWNER_NAME")
        or os.environ.get("OWNER_NAME")
        or "Project Team"
    )

    tokens: dict[str, Any] = {
        "CustomerInvoiceID": invoice.get("CustomerInvoiceId"),
        "InvoiceID": invoice.get("CustomerInvoiceId"),
        "InvoiceNumber": invoice.get("CustomerInvoiceId"),
        "InvoiceDate": str(invoice_date or ""),
        "DueDate": due_date,
        "InvoiceType": invoice_type,
        "CustomerName": _clean_text(header.get("CustomerName"), "Customer"),
        "CustomerEmail": _clean_text(header.get("CustomerEmail")),
        "SiteAddress": _clean_text(header.get("SiteAddress"), "Site address pending"),
        "WorkOrderID": header.get("WorkOrderID") or invoice.get("WorkOrderID"),
        "CustomerPO": _clean_text(header.get("CustomerPO")),
        "ScopeOfWork": _clean_text(invoice.get("ScopeOfWork") or header.get("Scope"), "Scope of work pending."),
        "TaxAmount": 0.0,
        "TaxAmountFormatted": _fmt_money(0.0),
        "PercentOfContract": round(
            _money(invoice.get("LaborPercent")) + _money(invoice.get("MaterialPercent")),
            2,
        ),
        "BillingMilestone": billing_milestone,
        "CompanyName": company_name,
        "OwnerName": owner_name,
        "PaymentTerms": payment_terms,
        "DocumentTitle": f"Invoice #{invoice.get('CustomerInvoiceId')}",
        "InvoiceMemorySummary": _clean_text(memory.get("review_summary")),
    }
    tokens.update(line_breakdown)
    tokens.update(
        {
            "LaborAmountFormatted": _fmt_money(tokens["LaborAmount"]),
            "MaterialAmountFormatted": _fmt_money(tokens["MaterialAmount"]),
            "SubtotalFormatted": _fmt_money(tokens["Subtotal"]),
            "InvoiceTotalFormatted": _fmt_money(tokens["InvoiceTotal"]),
            "LineItemsText": _rows_to_text(tokens["LineItems"], include_type=True),
            "LaborLineItemsText": _rows_to_text(tokens["LaborLineItems"]),
            "MaterialLineItemsText": _rows_to_text(tokens["MaterialLineItems"]),
            "LineItemsHtml": _rows_to_html(tokens["LineItems"], include_type=True),
            "LaborLineItemsHtml": _rows_to_html(tokens["LaborLineItems"]),
            "MaterialLineItemsHtml": _rows_to_html(tokens["MaterialLineItems"]),
        }
    )
    return tokens


def validate_customer_invoice_document_tokens(tokens: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    required_fields = (
        "InvoiceID",
        "InvoiceDate",
        "CustomerName",
        "WorkOrderID",
        "SiteAddress",
    )
    for field_name in required_fields:
        value = tokens.get(field_name)
        if value is None or str(value).strip() == "":
            missing.append(field_name)

    if tokens.get("InvoiceTotal") is None:
        missing.append("InvoiceTotal")

    return (len(missing) == 0, missing)


def build_customer_invoice_plain_text_fallback(tokens: dict[str, Any]) -> str:
    resolved_tokens = dict(tokens or {})
    invoice_id = resolved_tokens.get("CustomerInvoiceID") or resolved_tokens.get("InvoiceID") or "Unknown"
    invoice_date = _clean_text(resolved_tokens.get("InvoiceDate"), "Pending")
    due_date = _clean_text(resolved_tokens.get("DueDate"), "Pending")
    invoice_type = _clean_text(resolved_tokens.get("InvoiceType"), "Manual")
    customer_name = _clean_text(resolved_tokens.get("CustomerName"), "Customer")
    customer_email = _clean_text(resolved_tokens.get("CustomerEmail"), "N/A")
    workorder_id = _clean_text(resolved_tokens.get("WorkOrderID"), "Unknown")
    customer_po = _clean_text(resolved_tokens.get("CustomerPO"), "N/A")
    site_address = _clean_text(resolved_tokens.get("SiteAddress"), "Site address pending")
    scope = _clean_text(resolved_tokens.get("ScopeOfWork"), "Scope of work pending.")
    labor_lines = _clean_text(resolved_tokens.get("LaborLineItemsText"), "None")
    material_lines = _clean_text(resolved_tokens.get("MaterialLineItemsText"), "None")
    subtotal = _fmt_money(resolved_tokens.get("Subtotal"))
    tax_amount = _fmt_money(resolved_tokens.get("TaxAmount"))
    invoice_total = _fmt_money(resolved_tokens.get("InvoiceTotal"))
    percent_of_contract = str(resolved_tokens.get("PercentOfContract") or "0")
    milestone = _clean_text(resolved_tokens.get("BillingMilestone"), "N/A")
    payment_terms = _clean_text(resolved_tokens.get("PaymentTerms"), "Net 30")
    company_name = _clean_text(resolved_tokens.get("CompanyName"), "Neon_ai")
    owner_name = _clean_text(resolved_tokens.get("OwnerName"), "Project Team")

    sections = [
        f"Invoice #{invoice_id}",
        f"Date: {invoice_date}",
        f"Due Date: {due_date}",
        f"Invoice Type: {invoice_type}",
        "",
        f"Customer: {customer_name}",
        f"Customer Email: {customer_email}",
        f"Work Order: {workorder_id}",
        f"Customer PO: {customer_po}",
        f"Project Site: {site_address}",
        "",
        "Scope of Work",
        scope,
        "",
        "Labor Billing",
        labor_lines,
        "",
        "Material Billing",
        material_lines,
        "",
        f"Subtotal: {subtotal}",
        f"Tax: {tax_amount}",
        f"Invoice Total: {invoice_total}",
        f"Percent of Contract: {percent_of_contract}%",
        f"Billing Milestone: {milestone}",
        "",
        "Payment Terms",
        payment_terms,
        "",
        f"{company_name}",
        owner_name,
    ]
    return "\n".join(sections).strip()
