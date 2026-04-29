import difflib
import re
from typing import List

from neon_ai.database.connection import get_connection
from neon_ai.database.customers import get_customer_portfolio
from neon_ai.database.estimates import get_dashboard_estimates
from neon_ai.database.purchases import get_backordered_purchase_orders
from neon_ai.database.vendor_invoices import get_open_purchase_order_invoice_pipeline
from neon_ai.database.workorders import get_dashboard_work_orders


def _fetch_all(query, params=None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, params or ())
        return cur.fetchall()
    finally:
        conn.close()


def _fetch_one(query, params=None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, params or ())
        return cur.fetchone()
    finally:
        conn.close()


def _normalize_text(value: str):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _extract_name_candidate(query_text: str):
    cleaned = _normalize_text(query_text)
    cleaned = re.sub(
        r"\b(what|whats|what is|show|give|find|lookup|look up|contact|information|info|for|the|customer|phone|email|address|details|of|on|please|probably|spelled|differently|its|it s)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def detect_intent(user_message: str):
    lower = (user_message or "").lower()
    if any(phrase in lower for phrase in ("contact info", "contact information", "phone number", "email address", "contact details")):
        return "customer_contact"
    if "address" in lower and any(word in lower for word in ("customer", "contact", "site", "bella", "project")):
        return "customer_contact"
    if any(phrase in lower for phrase in ("closest match", "spelled differently", "spell differently", "did you mean")):
        return "customer_match_help"
    if any(phrase in lower for phrase in ("backorder", "back order", "backordered")):
        return "backorders"
    if any(phrase in lower for phrase in ("ready to pay", "vendor invoice", "payables")):
        return "vendor_invoices"
    if re.search(r"\bestimate\s*#?\s*\d+", lower):
        return "estimate_status"
    if re.search(r"\b(work[\s-]?order|wo)\s*#?\s*\d+", lower):
        return "workorder_status"
    if re.search(r"\b(purchase order|po)\s*#?\s*\d+", lower):
        return "po_status"
    if any(phrase in lower for phrase in ("what is going on with", "status of", "what's going on with")):
        return "customer_status"
    return "general"


def _customer_similarity_score(query_text: str, customer_row):
    candidate = _extract_name_candidate(query_text) or _normalize_text(query_text)
    customer_name = _normalize_text(customer_row.get("CustomerName") or "")
    if not candidate or not customer_name:
        return 0.0

    if candidate in customer_name:
        return 1.0

    candidate_tokens = set(candidate.split())
    customer_tokens = set(customer_name.split())
    token_overlap = len(candidate_tokens & customer_tokens) / max(1, len(candidate_tokens))
    sequence_ratio = difflib.SequenceMatcher(None, candidate, customer_name).ratio()
    token_best = 0.0
    for token in candidate_tokens:
        token_best = max(
            token_best,
            max((difflib.SequenceMatcher(None, token, ct).ratio() for ct in customer_tokens), default=0.0),
        )

    return max(sequence_ratio, token_overlap, token_best * 0.92)


def get_operational_overview():
    estimates = get_dashboard_estimates()
    work_orders = get_dashboard_work_orders()
    backorders = get_backordered_purchase_orders()
    invoice_pipeline = get_open_purchase_order_invoice_pipeline()
    ready_to_pay = [row for row in invoice_pipeline if str(row.get("VendorInvoiceStatus") or "") == "ReadyToPay"]

    return {
        "draft_estimates": len(estimates),
        "open_work_orders": len([row for row in work_orders if str(row.get("Status") or "").upper() != "CLOSED"]),
        "backordered_pos": len(backorders),
        "ready_to_pay_vendor_invoices": len(ready_to_pay),
    }


def search_customers(query_text: str, limit: int = 5):
    terms = [term.strip() for term in re.split(r"\s+", query_text or "") if term.strip()]
    if not terms:
        return []

    where_parts = []
    params: List[str] = []
    for term in terms[:5]:
        pattern = f"%{term}%"
        where_parts.append(
            '(c."CustomerName" ILIKE %s OR COALESCE(c."Email", \'\') ILIKE %s OR COALESCE(c."Phone", \'\') ILIKE %s)'
        )
        params.extend([pattern, pattern, pattern])

    params.append(max(limit * 6, 20))
    direct_matches = _fetch_all(
        f'''
        SELECT c."CustomerID", c."CustomerName", c."Email", c."Phone", c."CityName"
        FROM "Customer" c
        WHERE {' AND '.join(where_parts)}
        ORDER BY c."CustomerName"
        LIMIT %s
        ''',
        tuple(params),
    )

    if len(direct_matches) >= limit:
        return direct_matches[:limit]

    all_customers = _fetch_all(
        '''
        SELECT c."CustomerID", c."CustomerName", c."Email", c."Phone", c."CityName"
        FROM "Customer" c
        ORDER BY c."CustomerName"
        '''
    )
    scored = []
    seen_ids = {row["CustomerID"] for row in direct_matches}
    for row in all_customers:
        if row["CustomerID"] in seen_ids:
            scored.append((1.0, row))
            continue
        score = _customer_similarity_score(query_text, row)
        if score >= 0.58:
            scored.append((score, row))

    scored.sort(key=lambda item: (-item[0], item[1].get("CustomerName") or ""))
    return [row for _, row in scored[:limit]]


def get_customer_snapshot(customer_id: int):
    customer = _fetch_one(
        '''
        SELECT "CustomerID", "CustomerName", "Email", "Phone", "Address", "StreetName", "CityName", "PostalCode"
        FROM "Customer"
        WHERE "CustomerID" = %s
        ''',
        (customer_id,),
    )
    if not customer:
        return None

    portfolio = get_customer_portfolio(customer_id)
    return {
        "customer": customer,
        "portfolio": portfolio,
    }


def format_customer_contact_card(customer_snapshot):
    if not customer_snapshot or not customer_snapshot.get("customer"):
        return None

    customer = customer_snapshot["customer"]
    portfolio = customer_snapshot.get("portfolio") or {}
    sites = portfolio.get("sites") or []
    primary_site = sites[0] if sites else None

    contact_address = " ".join(
        part for part in [
            customer.get("Address") or "",
            customer.get("StreetName") or "",
            customer.get("CityName") or "",
            customer.get("PostalCode") or "",
        ]
        if part
    ) or "Not on file"

    if primary_site:
        site_address = " ".join(
            part for part in [
                primary_site.get("StreetNumber") or "",
                primary_site.get("StreetName") or "",
                primary_site.get("City") or "",
            ]
            if part
        ) or (primary_site.get("SiteName") or "Not on file")
    else:
        site_address = "No site on file"

    return {
        "CustomerName": customer.get("CustomerName") or "",
        "Email": customer.get("Email") or "Not on file",
        "Phone": customer.get("Phone") or "Not on file",
        "ContactAddress": contact_address,
        "PrimarySite": primary_site.get("SiteName") if primary_site else "Not on file",
        "PrimarySiteAddress": site_address,
    }


def format_customer_status_card(customer_snapshot):
    if not customer_snapshot or not customer_snapshot.get("customer"):
        return None
    customer = customer_snapshot["customer"]
    portfolio = customer_snapshot.get("portfolio") or {}
    estimates = portfolio.get("estimates") or []
    work_orders = portfolio.get("work_orders") or []
    sites = portfolio.get("sites") or []
    total_owing = sum(float(row.get("Owing") or 0) for row in work_orders)
    open_work_orders = [row for row in work_orders if str(row.get("WOStatus") or "").upper() != "CLOSED"]
    return {
        "CustomerName": customer.get("CustomerName") or "",
        "SiteCount": len(sites),
        "EstimateCount": len(estimates),
        "WorkOrderCount": len(work_orders),
        "OpenWorkOrderCount": len(open_work_orders),
        "TotalOwing": total_owing,
        "LatestEstimate": estimates[0] if estimates else None,
        "LatestWorkOrder": work_orders[0] if work_orders else None,
    }


def get_estimate_snapshot(estimate_id: int):
    return _fetch_one(
        '''
        SELECT
            e."EstimateID",
            COALESCE(e."CreatedDate"::text, '') AS "CreatedDate",
            COALESCE(e."Status", '') AS "Status",
            COALESCE(e."Description", '') AS "Description",
            COALESCE(e."TotalAmount", 0) AS "TotalAmount",
            COALESCE(c."CustomerName", '') AS "CustomerName",
            COALESCE(c."Email", '') AS "CustomerEmail",
            COALESCE(s."SiteName", '') AS "SiteName"
        FROM "Estimate" e
        JOIN "Site" s ON e."SiteID" = s."SiteID"
        JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        WHERE e."EstimateID" = %s
        ''',
        (estimate_id,),
    )


def get_workorder_snapshot(workorder_id: int):
    return _fetch_one(
        '''
        WITH invoice_rollup AS (
            SELECT
                i."WorkOrderID",
                COALESCE(SUM(i."CustomerInvoiceAmount"::numeric), 0) AS "TotalBilled",
                COALESCE(SUM(CASE WHEN i."InvDatePaid" IS NOT NULL THEN i."CustomerInvoiceAmount"::numeric ELSE 0 END), 0) AS "TotalPaid"
            FROM "Invoice" i
            GROUP BY i."WorkOrderID"
        )
        SELECT
            wo."WorkOrderID",
            COALESCE(wo."JobStatus", CASE WHEN COALESCE(wo."IsClosed", FALSE) THEN 'CLOSED' ELSE 'OPEN' END) AS "Status",
            COALESCE(wo."Description", '') AS "Description",
            COALESCE(wo."BillingType", '') AS "BillingType",
            COALESCE(s."SiteName", '') AS "SiteName",
            COALESCE(c."CustomerName", '') AS "CustomerName",
            COALESCE(ir."TotalBilled", 0) AS "TotalBilled",
            COALESCE(ir."TotalPaid", 0) AS "TotalPaid",
            (COALESCE(ir."TotalBilled", 0) - COALESCE(ir."TotalPaid", 0)) AS "Owing"
        FROM "WorkOrder" wo
        JOIN "Site" s ON wo."SiteID" = s."SiteID"
        JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        LEFT JOIN invoice_rollup ir ON ir."WorkOrderID" = wo."WorkOrderID"
        WHERE wo."WorkOrderID" = %s
        ''',
        (workorder_id,),
    )


def get_po_snapshot(po_id: int):
    header = _fetch_one(
        '''
        SELECT
            po."PurchaseOrderID",
            COALESCE(po."Date", '') AS "Date",
            COALESCE(po."Status", '') AS "Status",
            COALESCE(po."PurchaseOrderTotal", 0) AS "PurchaseOrderTotal",
            COALESCE(v."VendorName", '') AS "VendorName",
            COALESCE(s."SiteName", '') AS "SiteName",
            COALESCE(c."CustomerName", '') AS "CustomerName"
        FROM "PurchaseOrder" po
        JOIN "Vendor" v ON po."VendorID" = v."VendorID"
        LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
        LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
        LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
        WHERE po."PurchaseOrderID" = %s
        ''',
        (po_id,),
    )
    if not header:
        return None

    items = _fetch_all(
        '''
        SELECT
            COALESCE(poi."Description", em."Description", m."Description", '') AS "Description",
            COALESCE(poi."QuantityOrdered", 0) AS "QuantityOrdered",
            COALESCE(poi."QuantityReceived", 0) AS "QuantityReceived",
            COALESCE(
                NULLIF(poi."UnitPriceAtOrder", 0),
                NULLIF(em."CommittedUnitCost", 0),
                NULLIF(em."AwardedUnitCost", 0),
                NULLIF(em."UnitCost", 0),
                CASE COALESCE(m."CarryPriceSource", 'Internal')
                    WHEN 'Nedco' THEN NULLIF(m."NedcoPrice", 0)
                    WHEN 'Gescan' THEN NULLIF(m."GescanPrice", 0)
                    WHEN 'Eecol' THEN NULLIF(m."EecolPrice", 0)
                    WHEN 'Guillevin' THEN NULLIF(m."GuillevinPrice", 0)
                    ELSE NULLIF(m."InternalPrice", 0)
                END,
                0
            ) AS "UnitPriceAtOrder"
        FROM "PurchaseOrderItem" poi
        LEFT JOIN "EstimateMaterial" em ON poi."MaterialID" = em."EstimateMaterialID"
        LEFT JOIN "Material" m ON poi."CatalogItemID" = m."ItemID"
        WHERE poi."PurchaseOrderID" = %s
        ORDER BY poi."POItemID"
        ''',
        (po_id,),
    )
    return {"header": header, "items": items}


def get_vendor_invoice_snapshot(invoice_id: int):
    return _fetch_one(
        '''
        SELECT
            vi."VendorInvoiceID",
            vi."VendorInvoiceNumber",
            vi."VendorInvoiceDate",
            vi."VendorInvoiceDueDate",
            vi."VendorInvoiceAmount",
            vi."VendorInvoiceStatus",
            po."PurchaseOrderID",
            COALESCE(v."VendorName", '') AS "VendorName",
            COALESCE(s."SiteName", '') AS "SiteName"
        FROM "VendorInvoice" vi
        JOIN "PurchaseOrder" po ON vi."PurchaseOrderID" = po."PurchaseOrderID"
        JOIN "Vendor" v ON po."VendorID" = v."VendorID"
        LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
        LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
        WHERE vi."VendorInvoiceID" = %s
        ''',
        (invoice_id,),
    )


def search_recent_notes(query_text: str, limit: int = 5):
    if not query_text.strip():
        return []
    pattern = f"%{query_text.strip()}%"
    return _fetch_all(
        '''
        SELECT
            n."EstimateID",
            TO_CHAR(n."Timestamp", 'YYYY-MM-DD HH24:MI') AS "Timestamp",
            COALESCE(n."Category", '') AS "Category",
            LEFT(COALESCE(n."NoteText", ''), 220) AS "NoteText"
        FROM "Note" n
        WHERE n."NoteText" ILIKE %s
        ORDER BY n."Timestamp" DESC
        LIMIT %s
        ''',
        (pattern, limit),
    )


def build_chat_context(user_message: str):
    lower = user_message.lower()
    context = {
        "intent": detect_intent(user_message),
        "overview": get_operational_overview(),
        "matches": {},
    }

    customer_matches = search_customers(user_message, limit=3)
    if customer_matches:
        context["matches"]["customers"] = customer_matches
        top_customer = customer_matches[0]
        context["matches"]["top_customer_snapshot"] = get_customer_snapshot(top_customer["CustomerID"])
        context["matches"]["top_customer_contact_card"] = format_customer_contact_card(context["matches"]["top_customer_snapshot"])
        context["matches"]["top_customer_status_card"] = format_customer_status_card(context["matches"]["top_customer_snapshot"])

    estimate_match = re.search(r"estimate\s*#?\s*(\d+)", lower)
    if estimate_match:
        context["matches"]["estimate"] = get_estimate_snapshot(int(estimate_match.group(1)))

    workorder_match = re.search(r"(?:work[\s-]?order|wo)\s*#?\s*(\d+)", lower)
    if workorder_match:
        context["matches"]["work_order"] = get_workorder_snapshot(int(workorder_match.group(1)))

    po_match = re.search(r"(?:purchase order|po)\s*#?\s*(\d+)", lower)
    if po_match:
        context["matches"]["purchase_order"] = get_po_snapshot(int(po_match.group(1)))

    vendor_invoice_match = re.search(r"(?:vendor invoice|invoice)\s*#?\s*(\d+)", lower)
    if vendor_invoice_match and ("vendor" in lower or "ready to pay" in lower):
        context["matches"]["vendor_invoice"] = get_vendor_invoice_snapshot(int(vendor_invoice_match.group(1)))

    if any(keyword in lower for keyword in ("backorder", "back order", "backordered")):
        context["matches"]["backordered_pos"] = get_backordered_purchase_orders()[:10]

    if any(keyword in lower for keyword in ("ready to pay", "vendor invoice", "payables")):
        ready = [row for row in get_open_purchase_order_invoice_pipeline() if str(row.get("VendorInvoiceStatus") or "") == "ReadyToPay"]
        context["matches"]["ready_to_pay_vendor_invoices"] = ready[:10]

    note_terms = re.sub(r"(estimate|work order|wo|purchase order|po|vendor invoice|invoice)\s*#?\s*\d+", "", user_message, flags=re.I).strip()
    if len(note_terms) >= 4:
        note_hits = search_recent_notes(note_terms, limit=5)
        if note_hits:
            context["matches"]["notes"] = note_hits

    return context


def build_direct_answer(user_message: str, live_context: dict):
    matches = (live_context or {}).get("matches") or {}
    intent = (live_context or {}).get("intent") or "general"
    contact_card = matches.get("top_customer_contact_card")
    status_card = matches.get("top_customer_status_card")
    customer_matches = matches.get("customers") or []

    if intent == "customer_contact":
        if not contact_card:
            return "I couldn't resolve that customer confidently from live data yet. Try the customer name, part of the email, or a site name."
        lines = [
            contact_card["CustomerName"],
            f"- Email: {contact_card['Email']}",
            f"- Phone: {contact_card['Phone']}",
            f"- Contact Address: {contact_card['ContactAddress']}",
            f"- Primary Site: {contact_card['PrimarySite']}",
            f"- Primary Site Address: {contact_card['PrimarySiteAddress']}",
        ]
        if len(customer_matches) > 1:
            alternatives = ", ".join(row.get("CustomerName") or "" for row in customer_matches[1:3] if row.get("CustomerName"))
            if alternatives:
                lines.append(f"- Possible alternates: {alternatives}")
        return "\n".join(lines)

    if intent == "customer_match_help":
        if customer_matches:
            return "Closest customer matches I found: " + ", ".join(
                row.get("CustomerName") or "" for row in customer_matches if row.get("CustomerName")
            )
        return "I still don't have a confident customer match. Try a site name, phone number, or part of the email address."

    if intent == "customer_status":
        if not status_card:
            return "I couldn't resolve that customer confidently from live data yet."
        latest_est = status_card.get("LatestEstimate")
        latest_wo = status_card.get("LatestWorkOrder")
        lines = [
            status_card["CustomerName"],
            f"- Sites: {status_card['SiteCount']}",
            f"- Estimates: {status_card['EstimateCount']}",
            f"- Work Orders: {status_card['WorkOrderCount']} ({status_card['OpenWorkOrderCount']} open)",
            f"- Total Owing: ${status_card['TotalOwing']:,.2f}",
        ]
        if latest_est:
            lines.append(
                f"- Latest Estimate: #{latest_est.get('EstimateID')} | {latest_est.get('Status')} | {latest_est.get('SiteName')} | ${float(latest_est.get('TotalAmount') or 0):,.2f}"
            )
        if latest_wo:
            lines.append(
                f"- Latest Work Order: WO #{latest_wo.get('WorkOrderID')} | {latest_wo.get('WOStatus')} | {latest_wo.get('SiteName')} | ${float(latest_wo.get('WOValue') or 0):,.2f} | Owing ${float(latest_wo.get('Owing') or 0):,.2f}"
            )
        return "\n".join(lines)

    if intent == "backorders":
        backorders = matches.get("backordered_pos") or []
        if not backorders:
            return "I don't see any current backordered purchase orders in live data."
        lines = ["Current backordered purchase orders:"]
        for row in backorders[:8]:
            lines.append(
                f"- PO #{row.get('PurchaseOrderID')} | {row.get('VendorName')} | {row.get('SiteName')} | {row.get('BackorderedLineCount')} backordered lines | Status: {row.get('Status')}"
            )
        return "\n".join(lines)

    if intent == "vendor_invoices":
        invoices = matches.get("ready_to_pay_vendor_invoices") or []
        if not invoices:
            return "I don't see any vendor invoices currently marked ReadyToPay."
        lines = ["Vendor invoices ready to pay:"]
        for row in invoices[:8]:
            lines.append(
                f"- PO #{row.get('PurchaseOrderID')} | {row.get('VendorName')} | {row.get('SiteName')} | Invoice Status: {row.get('VendorInvoiceStatus')} | Amount: ${float(row.get('VendorInvoiceAmount') or 0):,.2f}"
            )
        return "\n".join(lines)

    if intent == "estimate_status":
        estimate = matches.get("estimate")
        if estimate:
            return (
                f"Estimate #{estimate.get('EstimateID')}\n"
                f"- Customer: {estimate.get('CustomerName')}\n"
                f"- Site: {estimate.get('SiteName')}\n"
                f"- Status: {estimate.get('Status')}\n"
                f"- Created: {estimate.get('CreatedDate')}\n"
                f"- Total: ${float(estimate.get('TotalAmount') or 0):,.2f}\n"
                f"- Scope: {estimate.get('Description') or 'No scope on file'}"
            )
        return "I couldn't find that estimate in live data."

    if intent == "workorder_status":
        work_order = matches.get("work_order")
        if work_order:
            return (
                f"Work Order #{work_order.get('WorkOrderID')}\n"
                f"- Customer: {work_order.get('CustomerName')}\n"
                f"- Site: {work_order.get('SiteName')}\n"
                f"- Status: {work_order.get('Status')}\n"
                f"- Billing Type: {work_order.get('BillingType')}\n"
                f"- Owing: ${float(work_order.get('Owing') or 0):,.2f}\n"
                f"- Scope: {work_order.get('Description') or 'No scope on file'}"
            )
        return "I couldn't find that work order in live data."

    if intent == "po_status":
        po = matches.get("purchase_order")
        if po:
            header = po["header"]
            lines = [
                f"PO #{header.get('PurchaseOrderID')}",
                f"- Vendor: {header.get('VendorName')}",
                f"- Customer: {header.get('CustomerName')}",
                f"- Site: {header.get('SiteName')}",
                f"- Status: {header.get('Status')}",
                f"- Date: {header.get('Date')}",
                f"- Total: ${float(header.get('PurchaseOrderTotal') or 0):,.2f}",
            ]
            for item in po.get("items", [])[:5]:
                lines.append(
                    f"- Item: {item.get('Description')} | Ordered {float(item.get('QuantityOrdered') or 0):.2f} | Received {float(item.get('QuantityReceived') or 0):.2f}"
                )
            return "\n".join(lines)
        return "I couldn't find that purchase order in live data."

    return None


def get_debug_summary(live_context: dict):
    matches = (live_context or {}).get("matches") or {}
    debug = []
    if live_context.get("intent"):
        debug.append(f"Intent: {live_context['intent']}")
    customers = matches.get("customers") or []
    if customers:
        names = ", ".join(row.get("CustomerName") or "" for row in customers[:3] if row.get("CustomerName"))
        if names:
            debug.append(f"Customer Match: {names}")
    if matches.get("estimate"):
        debug.append(f"Estimate Match: #{matches['estimate'].get('EstimateID')}")
    if matches.get("work_order"):
        debug.append(f"WO Match: #{matches['work_order'].get('WorkOrderID')}")
    if matches.get("purchase_order"):
        debug.append(f"PO Match: #{matches['purchase_order']['header'].get('PurchaseOrderID')}")
    return debug
