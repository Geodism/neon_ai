from __future__ import annotations

import html
import os
from typing import Any

from neon_ai.database.estimates import get_detailed_estimate_data


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
