from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.customers import ensure_customer_contact_table, sync_table_sequence
from neon_ai.database.estimates import ensure_estimate_sequence


DEFAULT_ESTIMATE_BILLING_TYPE = "Time and Materials"
AMBIGUOUS_MATCH_OUTCOMES = {
    "AMBIGUOUS_CUSTOMER_MATCH",
    "AMBIGUOUS_SITE_MATCH",
}


@dataclass(frozen=True)
class LeadIntakeApplyServiceResult:
    created_customer_id: int | None
    created_site_id: int | None
    created_estimate_id: int | None
    used_existing_customer_id: int | None
    used_existing_site_id: int | None
    used_existing_estimate_id: int | None
    warnings: list[str]
    source_proposal_id: int
    source_inbound_message_id: int | None
    applied_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_customer_id": self.created_customer_id,
            "created_site_id": self.created_site_id,
            "created_estimate_id": self.created_estimate_id,
            "used_existing_customer_id": self.used_existing_customer_id,
            "used_existing_site_id": self.used_existing_site_id,
            "used_existing_estimate_id": self.used_existing_estimate_id,
            "warnings": list(self.warnings),
            "source_proposal_id": self.source_proposal_id,
            "source_inbound_message_id": self.source_inbound_message_id,
            "applied_by": self.applied_by,
        }


def apply_lead_intake_draft_records(
    *,
    proposal_id: int,
    proposed_change_json: dict[str, Any],
    evidence_json: dict[str, Any],
    applied_by: str,
) -> LeadIntakeApplyServiceResult:
    proposed_change = proposed_change_json if isinstance(proposed_change_json, dict) else {}
    evidence = evidence_json if isinstance(evidence_json, dict) else {}
    match_outcome = str(proposed_change.get("match_outcome") or "").strip().upper()
    if match_outcome in AMBIGUOUS_MATCH_OUTCOMES:
        raise RuntimeError(
            f"Lead intake apply is blocked because the proposal match outcome is still ambiguous: {match_outcome}."
        )

    minimum_detail_errors = _minimum_detail_errors(proposed_change)
    if minimum_detail_errors:
        raise RuntimeError("; ".join(minimum_detail_errors))

    target_type = str(proposed_change.get("target_type") or "").strip()
    target_id = _coerce_int(proposed_change.get("target_id"))
    source_inbound_message_id = _coerce_int(evidence.get("inbound_message_id"))
    warnings: list[str] = []

    ensure_customer_contact_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        customer_context = _resolve_customer_context(
            cur,
            proposed_change=proposed_change,
            target_type=target_type,
            target_id=target_id,
        )
        if customer_context["ambiguous"]:
            raise RuntimeError(customer_context["reason"])

        site_context = _resolve_site_context(
            cur,
            proposed_change=proposed_change,
            target_type=target_type,
            target_id=target_id,
            customer_id=customer_context["customer_id"],
        )
        if site_context["ambiguous"]:
            raise RuntimeError(site_context["reason"])

        estimate_context = _resolve_estimate_context(
            cur,
            proposed_change=proposed_change,
            target_type=target_type,
            target_id=target_id,
            site_id=site_context["site_id"],
        )
        if estimate_context["ambiguous"]:
            raise RuntimeError(estimate_context["reason"])

        created_customer_id: int | None = None
        created_site_id: int | None = None
        created_estimate_id: int | None = None
        used_existing_customer_id = customer_context["customer_id"]
        used_existing_site_id = site_context["site_id"]
        used_existing_estimate_id = estimate_context["estimate_id"]

        if used_existing_customer_id is None:
            created_customer_id = _insert_customer(cur, proposed_change)
            used_existing_customer_id = created_customer_id

        if used_existing_site_id is None and _has_site_detail(proposed_change):
            created_site_id = _insert_site(cur, used_existing_customer_id, proposed_change)
            used_existing_site_id = created_site_id
        elif used_existing_site_id is None:
            warnings.append("No safe site/address detail was available, so no Site record was created.")

        if used_existing_estimate_id is None:
            if used_existing_site_id is not None and _has_estimate_shell_detail(proposed_change):
                duplicate_draft = _find_existing_draft_estimate(
                    cur,
                    site_id=used_existing_site_id,
                    project_description=str(proposed_change.get("project_description") or "").strip(),
                )
                if duplicate_draft["ambiguous"]:
                    raise RuntimeError(duplicate_draft["reason"])
                if duplicate_draft["estimate_id"] is not None:
                    used_existing_estimate_id = duplicate_draft["estimate_id"]
                    warnings.append(
                        f"Reused existing draft Estimate #{used_existing_estimate_id} for the same site/project description."
                    )
                else:
                    created_estimate_id = _insert_estimate_shell(cur, used_existing_site_id, proposed_change)
            elif used_existing_site_id is None:
                warnings.append("No safe Site context was available, so no Estimate shell was created.")
            else:
                warnings.append("Project/service detail was too sparse to create a draft Estimate shell.")

        conn.commit()
        return LeadIntakeApplyServiceResult(
            created_customer_id=created_customer_id,
            created_site_id=created_site_id,
            created_estimate_id=created_estimate_id,
            used_existing_customer_id=None if created_customer_id is not None else used_existing_customer_id,
            used_existing_site_id=None if created_site_id is not None else used_existing_site_id,
            used_existing_estimate_id=None if created_estimate_id is not None else used_existing_estimate_id,
            warnings=warnings,
            source_proposal_id=int(proposal_id),
            source_inbound_message_id=source_inbound_message_id,
            applied_by=str(applied_by or "Operator").strip() or "Operator",
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _minimum_detail_errors(proposed_change: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    has_contact_signal = any(
        str(proposed_change.get(field_name) or "").strip()
        for field_name in ("sender_email", "phone_number", "contact_name", "company_name")
    )
    has_project_signal = any(
        str(proposed_change.get(field_name) or "").strip()
        for field_name in ("project_description", "trade_work_type", "site_address_text")
    )
    if not has_contact_signal:
        errors.append("Lead intake apply requires at least one safe contact signal (email, phone, contact name, or company).")
    if not has_project_signal:
        errors.append("Lead intake apply requires at least one safe project/site signal (project description, trade/work type, or site/address text).")
    return errors


def _resolve_customer_context(
    cur,
    *,
    proposed_change: dict[str, Any],
    target_type: str,
    target_id: int | None,
) -> dict[str, Any]:
    if target_id is not None and target_type == "Customer":
        row = _fetch_customer(cur, target_id)
        if row:
            return {"customer_id": int(row["CustomerID"]), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Customer #{target_id} was not found.")

    if target_id is not None and target_type == "Site":
        row = _fetch_site(cur, target_id)
        if row:
            return {"customer_id": _coerce_int(row.get("CustomerID")), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Site #{target_id} was not found.")

    if target_id is not None and target_type == "Estimate":
        row = _fetch_estimate(cur, target_id)
        if row:
            return {"customer_id": _coerce_int(row.get("CustomerID")), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Estimate #{target_id} was not found.")

    sender_email = str(proposed_change.get("sender_email") or "").strip().lower()
    if sender_email:
        cur.execute(
            '''
            SELECT DISTINCT "CustomerID"
            FROM (
                SELECT "CustomerID"
                FROM public."Customer"
                WHERE LOWER(COALESCE("Email", '')) = %s
                UNION
                SELECT "CustomerID"
                FROM public."CustomerContact"
                WHERE LOWER(COALESCE("Email", '')) = %s
            ) matches
            ORDER BY "CustomerID" ASC
            ''',
            (sender_email, sender_email),
        )
        rows = cur.fetchall()
        if len(rows) == 1:
            return {"customer_id": int(rows[0]["CustomerID"]), "ambiguous": False, "reason": None}
        if len(rows) > 1:
            return {
                "customer_id": None,
                "ambiguous": True,
                "reason": f"Lead intake apply found multiple Customer matches for sender email '{sender_email}'.",
            }

    names_to_try = [
        str(proposed_change.get("company_name") or "").strip(),
        str(proposed_change.get("contact_name") or "").strip(),
    ]
    names_to_try = [name for name in names_to_try if name]
    for candidate_name in names_to_try:
        cur.execute(
            '''
            SELECT "CustomerID"
            FROM public."Customer"
            WHERE LOWER(COALESCE("CustomerName", '')) = LOWER(%s)
            ORDER BY "CustomerID" ASC
            ''',
            (candidate_name,),
        )
        rows = cur.fetchall()
        if len(rows) == 1:
            return {"customer_id": int(rows[0]["CustomerID"]), "ambiguous": False, "reason": None}
        if len(rows) > 1:
            return {
                "customer_id": None,
                "ambiguous": True,
                "reason": f"Lead intake apply found multiple Customer matches for '{candidate_name}'.",
            }

    return {"customer_id": None, "ambiguous": False, "reason": None}


def _resolve_site_context(
    cur,
    *,
    proposed_change: dict[str, Any],
    target_type: str,
    target_id: int | None,
    customer_id: int | None,
) -> dict[str, Any]:
    if target_id is not None and target_type == "Site":
        row = _fetch_site(cur, target_id)
        if row:
            return {"site_id": int(row["SiteID"]), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Site #{target_id} was not found.")

    if target_id is not None and target_type == "Estimate":
        row = _fetch_estimate(cur, target_id)
        if row and row.get("SiteID") is not None:
            return {"site_id": int(row["SiteID"]), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Estimate #{target_id} did not resolve to a Site.")

    if customer_id is None:
        return {"site_id": None, "ambiguous": False, "reason": None}

    site_text = str(proposed_change.get("site_address_text") or "").strip()
    if not site_text:
        return {"site_id": None, "ambiguous": False, "reason": None}

    cur.execute(
        '''
        SELECT
            "SiteID",
            "CustomerID",
            COALESCE("SiteName", '') AS "SiteName",
            LOWER(
                TRIM(
                    CONCAT(
                        COALESCE("StreetNumber", ''),
                        CASE WHEN COALESCE("StreetName", '') = '' THEN '' ELSE ' ' || COALESCE("StreetName", '') END,
                        CASE WHEN COALESCE("City", '') = '' THEN '' ELSE ', ' || COALESCE("City", '') END
                    )
                )
            ) AS "AddressSummary"
        FROM public."Site"
        WHERE "CustomerID" = %s
        ORDER BY "SiteID" ASC
        ''',
        (customer_id,),
    )
    lowered_site = site_text.lower()
    candidates: list[dict[str, Any]] = []
    for row in cur.fetchall():
        site_name = str(row.get("SiteName") or "").strip().lower()
        address_summary = str(row.get("AddressSummary") or "").strip().lower()
        if lowered_site == site_name or lowered_site == address_summary:
            candidates.append(row)
    if len(candidates) == 1:
        return {"site_id": int(candidates[0]["SiteID"]), "ambiguous": False, "reason": None}
    if len(candidates) > 1:
        return {
            "site_id": None,
            "ambiguous": True,
            "reason": f"Lead intake apply found multiple Site matches for '{site_text}'.",
        }
    return {"site_id": None, "ambiguous": False, "reason": None}


def _resolve_estimate_context(
    cur,
    *,
    proposed_change: dict[str, Any],
    target_type: str,
    target_id: int | None,
    site_id: int | None,
) -> dict[str, Any]:
    if target_id is not None and target_type == "Estimate":
        row = _fetch_estimate(cur, target_id)
        if row:
            return {"estimate_id": int(row["EstimateID"]), "ambiguous": False, "reason": None}
        raise RuntimeError(f"Lead intake apply target Estimate #{target_id} was not found.")

    return {"estimate_id": None, "ambiguous": False, "reason": None}


def _find_existing_draft_estimate(
    cur,
    *,
    site_id: int,
    project_description: str,
) -> dict[str, Any]:
    if not project_description.strip():
        return {"estimate_id": None, "ambiguous": False, "reason": None}
    cur.execute(
        '''
        SELECT "EstimateID"
        FROM public."Estimate"
        WHERE "SiteID" = %s
          AND LOWER(COALESCE("Description", '')) = LOWER(%s)
          AND LOWER(COALESCE("Status", 'draft')) = 'draft'
        ORDER BY "EstimateID" DESC
        ''',
        (site_id, project_description.strip()),
    )
    rows = cur.fetchall()
    if len(rows) == 1:
        return {"estimate_id": int(rows[0]["EstimateID"]), "ambiguous": False, "reason": None}
    if len(rows) > 1:
        return {
            "estimate_id": None,
            "ambiguous": True,
            "reason": "Lead intake apply found multiple matching draft Estimates for the same site/project description.",
        }
    return {"estimate_id": None, "ambiguous": False, "reason": None}


def _insert_customer(cur, proposed_change: dict[str, Any]) -> int:
    sync_table_sequence(cur, "Customer", "CustomerID")
    customer_name = _derive_customer_name(proposed_change)
    email = _nullable_text(proposed_change.get("sender_email"))
    phone = _nullable_text(proposed_change.get("phone_number"))
    address_parts = _parse_address_text(_nullable_text(proposed_change.get("site_address_text")))
    cur.execute(
        '''
        INSERT INTO public."Customer" (
            "CustomerName",
            "Address",
            "StreetName",
            "CityName",
            "PostalCode",
            "Email",
            "Phone"
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING "CustomerID"
        ''',
        (
            customer_name,
            address_parts.get("street_number"),
            address_parts.get("street_name"),
            address_parts.get("city"),
            address_parts.get("postal_code"),
            email,
            phone,
        ),
    )
    customer_id = int(cur.fetchone()["CustomerID"])
    contact_name = _nullable_text(proposed_change.get("contact_name"))
    if any((contact_name, phone, email)):
        cur.execute(
            '''
            INSERT INTO public."CustomerContact" ("CustomerID", "ContactName", "Phone", "Email")
            VALUES (%s, %s, %s, %s)
            ''',
            (customer_id, contact_name, phone, email),
        )
    return customer_id


def _insert_site(cur, customer_id: int, proposed_change: dict[str, Any]) -> int:
    sync_table_sequence(cur, "Site", "SiteID")
    site_text = _nullable_text(proposed_change.get("site_address_text")) or f"Pending Site - {_derive_customer_name(proposed_change)}"
    address_parts = _parse_address_text(site_text)
    cur.execute(
        '''
        INSERT INTO public."Site" ("CustomerID", "SiteName", "StreetNumber", "StreetName", "City")
        VALUES (%s, %s, %s, %s, %s)
        RETURNING "SiteID"
        ''',
        (
            int(customer_id),
            site_text,
            address_parts.get("street_number"),
            address_parts.get("street_name"),
            address_parts.get("city"),
        ),
    )
    return int(cur.fetchone()["SiteID"])


def _insert_estimate_shell(cur, site_id: int, proposed_change: dict[str, Any]) -> int:
    ensure_estimate_sequence(cur)
    description = _derive_estimate_description(proposed_change)
    cur.execute(
        '''
        INSERT INTO public."Estimate" ("SiteID", "Description", "Status", "CreatedDate", "BillingType")
        VALUES (%s, %s, %s, CURRENT_DATE, %s)
        RETURNING "EstimateID"
        ''',
        (int(site_id), description, "Draft", DEFAULT_ESTIMATE_BILLING_TYPE),
    )
    return int(cur.fetchone()["EstimateID"])


def _fetch_customer(cur, customer_id: int) -> dict[str, Any] | None:
    cur.execute('SELECT * FROM public."Customer" WHERE "CustomerID" = %s LIMIT 1', (int(customer_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def _fetch_site(cur, site_id: int) -> dict[str, Any] | None:
    cur.execute('SELECT * FROM public."Site" WHERE "SiteID" = %s LIMIT 1', (int(site_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def _fetch_estimate(cur, estimate_id: int) -> dict[str, Any] | None:
    cur.execute(
        '''
        SELECT
            e.*,
            s."CustomerID"
        FROM public."Estimate" e
        LEFT JOIN public."Site" s ON s."SiteID" = e."SiteID"
        WHERE e."EstimateID" = %s
        LIMIT 1
        ''',
        (int(estimate_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _derive_customer_name(proposed_change: dict[str, Any]) -> str:
    for field_name in ("company_name", "contact_name", "sender_email"):
        text = _nullable_text(proposed_change.get(field_name))
        if text:
            if field_name == "sender_email":
                local_part = text.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
                return local_part.title() or "Lead Intake Draft"
            return text
    return "Lead Intake Draft"


def _derive_estimate_description(proposed_change: dict[str, Any]) -> str:
    project_description = _nullable_text(proposed_change.get("project_description"))
    trade_work_type = _nullable_text(proposed_change.get("trade_work_type"))
    if project_description and trade_work_type:
        return f"{project_description} [{trade_work_type}]"
    if project_description:
        return project_description
    if trade_work_type:
        return f"Lead Intake - {trade_work_type}"
    return "Lead Intake Draft"


def _has_site_detail(proposed_change: dict[str, Any]) -> bool:
    return bool(_nullable_text(proposed_change.get("site_address_text")))


def _has_estimate_shell_detail(proposed_change: dict[str, Any]) -> bool:
    return bool(
        _nullable_text(proposed_change.get("project_description"))
        or _nullable_text(proposed_change.get("trade_work_type"))
    )


def _parse_address_text(site_text: str | None) -> dict[str, str | None]:
    text = str(site_text or "").strip()
    if not text:
        return {
            "street_number": None,
            "street_name": None,
            "city": None,
            "postal_code": None,
        }
    postal_match = re.search(r"\b([A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d)\b", text)
    postal_code = postal_match.group(1).upper() if postal_match else None
    city = None
    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            city = parts[-1]
            text = parts[0]
    street_number = None
    street_name = None
    match = re.match(r"^\s*(\d{1,8})\s+(.+?)\s*$", text)
    if match:
        street_number = match.group(1).strip()
        street_name = match.group(2).strip()
    return {
        "street_number": street_number,
        "street_name": street_name,
        "city": city,
        "postal_code": postal_code,
    }


def _nullable_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
