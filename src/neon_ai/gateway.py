import smtplib
import imaplib
import email
import os
import time
import json  
import mimetypes 
import tempfile
import re
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from neon_ai.automation.runtime_flags import require_legacy_automation_runtime
from neon_ai.config import load_neon_env

load_neon_env()

# Core Imports
from neon_ai.database.rfq import find_matching_rfq_for_email, process_inbound_vendor_rfq_email
from neon_ai.database.rfq import extract_text_from_attachment
from neon_ai.database.purchases import find_matching_po_for_email, process_inbound_vendor_po_email
from neon_ai.database.vendor_invoices import find_matching_po_for_vendor_invoice, process_inbound_vendor_invoice_email
from neon_ai.database.customers import (
    get_customer_by_email,
    get_customer_primary_site,
    update_customer_missing_fields,
    update_customer_fields,
    update_site_fields,
)
from neon_ai.database.classification_feedback import (
    create_review_record,
    extract_review_id,
    get_pending_review,
    get_similar_feedback_examples,
    parse_feedback_category,
    record_feedback,
)
from neon_ai.database.automation import (
    create_new_lead, 
    add_note_to_active_job, 
    send_smart_discovery,
    send_customer_info_request,
    send_customer_address_verification_request,
    notify_milton_of_ready_lead,
    mark_estimate_ready,
    get_project_memory,
    process_estimate_response,
    get_customer_intake_status,
    has_customer_info_request_been_sent,
    has_pending_customer_verification,
    log_estimate_action,
    get_estimate_verification_snapshot,
    log_estimate_verification,
)

# 2. Load Environment Variables
EMAIL_ADDR = os.getenv("AGENT_EMAIL")
EMAIL_PASS = os.getenv("AGENT_APP_PASSWORD")
MY_EMAIL = os.getenv("PERSONAL_EMAIL")
LEGACY_DIRECT_SEND_ENV = "NEON_ENABLE_LEGACY_DIRECT_SEND"
public_router = None

NON_WORKFLOW_EMAIL_INDICATORS = (
    "notifications@",
    "no-reply@",
    "noreply@",
    "newsletter",
    "marketing",
    "unsubscribe",
    "account.brilliant.org",
    "brilliant",
    "promotional",
    "learning subscription",
)


def extract_latest_reply_text(email_body: str) -> str:
    """Keep only the newest reply block above quoted history for customer parsing."""
    text = str(email_body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    split_patterns = [
        r"\nOn .+wrote:\n",
        r"\nFrom:\s.*\n",
        r"\n>+",
        r"\n-{2,}\s*Original Message\s*-{2,}\n",
    ]
    for pattern in split_patterns:
        parts = re.split(pattern, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[0].strip()
            break

    return text


def should_skip_non_workflow_email(sender_email, email_subject, email_body, header_text, matching_rfq_id=None):
    """Skip obvious bulk/promotional mail before AI classification, but never skip matched RFQ replies."""
    if matching_rfq_id:
        return False

    combined_text = "\n".join(
        str(value or "")
        for value in (sender_email, email_subject, email_body, header_text)
    ).lower()
    return any(indicator in combined_text for indicator in NON_WORKFLOW_EMAIL_INDICATORS)


def get_public_router():
    global public_router
    if public_router is None:
        from neon_ai.automation.local_brain import ArgonLocalAI

        public_router = ArgonLocalAI()
    return public_router

def save_incoming_attachment(part):
    filename = part.get_filename()
    if not filename:
        return None

    payload = part.get_payload(decode=True)
    if not payload:
        return None

    temp_dir = os.path.join(tempfile.gettempdir(), "argon_inbox_attachments")
    os.makedirs(temp_dir, exist_ok=True)
    safe_name = f"{int(time.time())}_{os.path.basename(filename)}"
    target_path = os.path.join(temp_dir, safe_name)
    with open(target_path, "wb") as handle:
        handle.write(payload)
    print(f"[GATEWAY] Saved inbound attachment to temp file: {target_path}")
    return target_path

def legacy_direct_send_enabled():
    """Return whether legacy direct SMTP send paths are explicitly enabled."""
    return os.getenv(LEGACY_DIRECT_SEND_ENV) == "1"


def direct_send_allowed(*, approved_outbound_authority=False):
    """Single outbound authority guard for SMTP provider access."""
    return bool(approved_outbound_authority or legacy_direct_send_enabled())


def send_to_user(
    subject,
    content,
    recipient=None,
    attachment_path=None,
    cc_recipients=None,
    *,
    approved_outbound_authority=False,
):
    """Send email through the SMTP gateway only from approved authority paths.

    Level 3 external send authority belongs to
    neon_ai.services.approved_outbound_send_service. Legacy direct send callers
    fail closed unless NEON_ENABLE_LEGACY_DIRECT_SEND=1 is set explicitly.
    """
    if not direct_send_allowed(approved_outbound_authority=approved_outbound_authority):
        print(
            "[OUTBOUND SEND BLOCKED] Legacy direct email send is disabled. "
            "Use approved_outbound_send_service.py for Level 3 sends, or set "
            f"{LEGACY_DIRECT_SEND_ENV}=1 only for explicit legacy/manual compatibility."
        )
        return False

    if not EMAIL_ADDR or not EMAIL_PASS:
        print("SMTP Error: sender credentials are not configured.")
        return False

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_ADDR
    msg['To'] = recipient if recipient else MY_EMAIL 
    if cc_recipients:
        valid_cc = [addr for addr in cc_recipients if addr]
        if valid_cc:
            msg['Cc'] = ", ".join(valid_cc)
    msg.set_content(content)

    if attachment_path and os.path.exists(attachment_path):
        import mimetypes
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or 'application/octet-stream').split('/', 1)
        with open(attachment_path, 'rb') as f:
            msg.add_attachment(
                f.read(), 
                maintype=maintype, 
                subtype=subtype, 
                filename=os.path.basename(attachment_path)
            )

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDR, EMAIL_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False


def send_classification_review_to_owner(review_entry):
    body = (
        "Argon needs help classifying an inbound email.\n\n"
        f"Argon Review ID: {review_entry['review_id']}\n"
        f"Predicted Category: {review_entry.get('predicted_category') or 'Unknown'}\n"
        f"Sender: {review_entry.get('sender_email') or 'Unknown'}\n"
        f"Subject: {review_entry.get('subject') or ''}\n"
        f"Attachments: {', '.join(review_entry.get('attachment_names') or []) or 'None'}\n\n"
        "Original message:\n"
        f"{review_entry.get('body') or '(No text body)'}\n\n"
        "Reply to this email with one of the following:\n"
        "- lead\n"
        "- not a lead\n"
        "- spam\n"
        "- classification: NewJobInquiry | VendorQuote | VendorInvoice | PackingSlip | General | Spam\n"
    )
    return send_to_user(
        subject=f"Argon Classification Review Needed [{review_entry['review_id']}]",
        content=body,
        recipient=MY_EMAIL,
    )


def handle_owner_classification_feedback(sender_email, email_subject, email_body):
    review_id = extract_review_id(f"{email_subject}\n{email_body}")
    if not review_id:
        return None

    pending = get_pending_review(review_id)
    if not pending:
        return None

    corrected_category = parse_feedback_category(email_subject, email_body)
    if not corrected_category:
        send_to_user(
            subject=f"Argon Classification Review Incomplete [{review_id}]",
            content=(
                "I found the review ID, but I couldn't read the correction.\n\n"
                "Please reply with one of:\n"
                "- lead\n"
                "- not a lead\n"
                "- spam\n"
                "- classification: NewJobInquiry | VendorQuote | VendorInvoice | PackingSlip | General | Spam"
            ),
            recipient=sender_email,
        )
        return {"status": "OwnerClassificationNeedsLabel", "review_id": review_id}

    resolved = record_feedback(
        review_id,
        corrected_category=corrected_category,
        corrected_by=sender_email,
        correction_subject=email_subject,
        correction_body=email_body,
    )
    if not resolved:
        return None

    lead_result = None
    if corrected_category == "NewJobInquiry":
        original_sender = pending.get("sender_email") or ""
        header_guess = original_sender.split("@")[0].replace(".", " ").title() if "@" in original_sender else "Customer"
        context_for_ai = f"Sender Name from Header: {header_guess}\n\nEmail Content:\n{pending.get('body') or ''}"
        lead_packet = get_public_router().extract_lead_data(context_for_ai) or {}
        lead_name = lead_packet.get("CustomerName")
        if not lead_name or str(lead_name).lower() in {"unknown", "null", "none"}:
            lead_name = header_guess
        lead_result = create_new_lead(
            customer_name=lead_name,
            email=original_sender,
            phone=lead_packet.get("CustomerPhone") or "Pending",
            inquiry_text=pending.get("body") or "",
            address=lead_packet.get("SiteAddress"),
            scope=lead_packet.get("ScopeOfWork"),
        )
        estimate_id = lead_result.get("estimate_id") if isinstance(lead_result, dict) else None
        if estimate_id:
            if lead_result.get("missing"):
                send_smart_discovery(
                    estimate_id,
                    lead_name,
                    original_sender,
                    f"Please provide: {', '.join(lead_result['missing'])}",
                )
            else:
                mark_estimate_ready(estimate_id)

    send_to_user(
        subject=f"Argon Classification Learned [{review_id}]",
        content=(
            f"Stored correction for review {review_id}.\n\n"
            f"Original prediction: {pending.get('predicted_category')}\n"
            f"Corrected category: {corrected_category}\n"
            f"Sender: {pending.get('sender_email')}\n"
            f"Subject: {pending.get('subject')}\n"
            + (
                f"\nLead action result: {lead_result}"
                if lead_result is not None else ""
            )
        ),
        recipient=sender_email,
    )
    return {
        "status": "OwnerClassificationStored",
        "review_id": review_id,
        "corrected_category": corrected_category,
        "lead_result": lead_result,
    }


def fetch_recent_emails_from_sender(sender_email, limit=8):
    """Pull a few recent inbox messages from the same sender so we can backfill missing customer info."""
    clean_sender = (sender_email or "").split("<")[-1].strip("> ").strip()
    if not clean_sender:
        return []

    messages = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_ADDR, EMAIL_PASS)
        mail.select("inbox")
        status, response = mail.search(None, f'FROM "{clean_sender}"')
        if status != "OK":
            mail.logout()
            return []

        message_ids = response[0].split()[-limit:]
        for message_id in message_ids:
            fetch_status, data = mail.fetch(message_id, "(RFC822)")
            if fetch_status != "OK" or not data or not data[0]:
                continue
            hist_msg = email.message_from_bytes(data[0][1])
            subject = str(hist_msg.get("Subject", ""))
            body = ""
            if hist_msg.is_multipart():
                for part in hist_msg.walk():
                    if part.get_content_type() == "text/plain" and not body:
                        try:
                            body = part.get_payload(decode=True).decode(errors="ignore").strip()
                        except Exception:
                            body = ""
            else:
                try:
                    body = hist_msg.get_payload(decode=True).decode(errors="ignore").strip()
                except Exception:
                    body = ""

            if subject or body:
                messages.append(f"Subject: {subject}\nBody:\n{body}")
        mail.logout()
    except Exception as e:
        print(f"[GATEWAY] Sender history lookup failed for {clean_sender}: {e}")
        return []

    return messages


def try_backfill_customer_from_inbox(sender_email, customer_id, header_name=None):
    """Use recent inbox history from the same sender to fill missing customer profile fields."""
    if not customer_id:
        return {"updated_fields": [], "profile": None}

    messages = fetch_recent_emails_from_sender(sender_email)
    if not messages:
        return {"updated_fields": [], "profile": None}

    prompt_context = "\n\n---\n\n".join(messages)
    if header_name:
        prompt_context = f"Header name: {header_name}\n\n{prompt_context}"

    profile = get_public_router().extract_customer_profile(prompt_context)
    if not profile:
        return {"updated_fields": [], "profile": None}

    backfill_result = update_customer_missing_fields(customer_id, profile)
    backfill_result["profile"] = profile
    return backfill_result


def process_customer_verification_reply(customer_id, email_subject, email_body):
    """Process a direct customer verification reply and update customer/site records."""
    from neon_ai.database.customers import get_customer_latest_estimate_id

    estimate_id = get_customer_latest_estimate_id(customer_id)
    before_snapshot = get_estimate_verification_snapshot(estimate_id) if estimate_id else None

    latest_reply_text = extract_latest_reply_text(email_body)
    profile = get_public_router().extract_customer_site_profile(
        f"Subject: {email_subject}\n\nNewest Reply:\n{latest_reply_text or email_body}"
    )
    if not profile:
        return None

    customer_result = update_customer_fields(customer_id, profile)
    primary_site = get_customer_primary_site(customer_id)
    site_result = update_site_fields(primary_site["SiteID"], profile) if primary_site else {"updated_fields": [], "site": None}

    existing_customer = get_customer_by_email(profile.get("CustomerEmail") or "")
    if existing_customer and int(existing_customer["CustomerID"]) == int(customer_id):
        # Keep sender-email based lookup simple; the actual pending verification guard decides whether we log.
        pass

    estimate_id = get_customer_latest_estimate_id(customer_id)
    if estimate_id and (customer_result.get("updated_fields") or site_result.get("updated_fields")):
        verification_field_map = {
            "Email": "CustomerEmail",
            "Phone": "CustomerPhone",
            "Address": "AddressNumber",
            "StreetNumber": "SiteStreetNumber",
            "City": "SiteCity",
        }
        updated_chunks = []
        if customer_result.get("updated_fields"):
            updated_chunks.append(f"customer: {', '.join(customer_result['updated_fields'])}")
        if site_result.get("updated_fields"):
            updated_chunks.append(f"site: {', '.join(site_result['updated_fields'])}")
        log_estimate_action(
            estimate_id,
            f"Customer verification reply processed. Updated {'; '.join(updated_chunks)}.",
            "Customer Verification",
        )
        after_snapshot = get_estimate_verification_snapshot(estimate_id)
        log_estimate_verification(
            estimate_id,
            "customer verification reply",
            before_snapshot,
            after_snapshot,
            expected_changed_fields=[
                verification_field_map.get(field_name, field_name)
                for field_name in list(customer_result.get("updated_fields", [])) + list(site_result.get("updated_fields", []))
            ],
        )

    return {
        "customer_updated": customer_result.get("updated_fields", []),
        "site_updated": site_result.get("updated_fields", []),
        "site_id": primary_site["SiteID"] if primary_site else None,
        "estimate_id": estimate_id,
    }

def check_for_instructions():
    """LEGACY_AUTOMATION_DISABLED_BY_DEFAULT: checks inbox, classifies content, and routes to old gateway automation only when explicitly enabled."""
    if not require_legacy_automation_runtime("gateway.check_for_instructions"):
        return None

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_ADDR, EMAIL_PASS)
        mail.select("inbox")

        status, response = mail.search(None, "UNSEEN")
        unread_ids = response[0].split()

        if not unread_ids:
            return None 

        latest_id = unread_ids[-1]
        status, data = mail.fetch(latest_id, "(RFC822)")
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        raw_sender = str(msg.get("From"))
        email_subject = str(msg.get("Subject", ""))
        header_text = "\n".join(f"{key}: {value}" for key, value in msg.items())

        if "<" in raw_sender:
            header_name = raw_sender.split("<")[0].strip().replace('"', '')
            raw_sender_email = raw_sender.split("<")[-1].strip(">")
        else:
            header_name = "Customer"
            raw_sender_email = raw_sender.strip()

        print(f"\nNew email from: {header_name} ({raw_sender_email})")
        email_date_header = msg.get("Date")
        received_at = None
        if email_date_header:
            try:
                received_at = parsedate_to_datetime(email_date_header)
            except Exception:
                received_at = None

        email_body = ""
        attachment_paths = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not email_body:
                    email_body = part.get_payload(decode=True).decode().strip()
                elif part.get_filename():
                    saved_path = save_incoming_attachment(part)
                    if saved_path:
                        attachment_paths.append(saved_path)
        else:
            email_body = msg.get_payload(decode=True).decode().strip()

        print(f"[GATEWAY] Email subject: {email_subject}")
        print(f"[GATEWAY] Attachment count: {len(attachment_paths)}")
        latest_reply_body = extract_latest_reply_text(email_body)

        if raw_sender_email.strip().lower() == (MY_EMAIL or "").strip().lower():
            feedback_result = handle_owner_classification_feedback(raw_sender_email, email_subject, email_body)
            if feedback_result:
                mail.store(latest_id, '+FLAGS', '\\Seen')
                print(f"[GATEWAY] Owner classification feedback processed: {feedback_result}")
                return feedback_result["status"]

        attachment_text = ""
        if attachment_paths:
            try:
                attachment_text = extract_text_from_attachment(attachment_paths[0])
            except Exception as e:
                print(f"[GATEWAY] Attachment text extraction failed: {e}")

        existing_customer = get_customer_by_email(raw_sender_email)
        if existing_customer:
            backfill_result = try_backfill_customer_from_inbox(
                raw_sender_email,
                existing_customer["CustomerID"],
                header_name=header_name,
            )
            if backfill_result.get("updated_fields"):
                print(
                    f"[GATEWAY] Backfilled customer #{existing_customer['CustomerID']} from inbox history: "
                    f"{backfill_result['updated_fields']}"
                )

        verification_result = None
        if existing_customer:
            from neon_ai.database.customers import get_customer_latest_estimate_id
            pending_estimate_id = get_customer_latest_estimate_id(existing_customer["CustomerID"])
            verification_subject = "verify your contact and project address details" in email_subject.lower()
            if verification_subject or (pending_estimate_id and has_pending_customer_verification(pending_estimate_id)):
                verification_result = process_customer_verification_reply(
                    existing_customer["CustomerID"],
                    email_subject,
                    email_body,
                )
                if verification_result and (verification_result.get("customer_updated") or verification_result.get("site_updated")):
                    print(
                        f"[GATEWAY] Customer verification reply updated customer #{existing_customer['CustomerID']}: "
                        f"customer={verification_result.get('customer_updated')}, "
                        f"site={verification_result.get('site_updated')}"
                    )

        matching_vendor_invoice_po_id = find_matching_po_for_vendor_invoice(raw_sender_email, email_subject, email_body, attachment_text)
        matching_po_id = find_matching_po_for_email(raw_sender_email, email_subject, f"{email_body}\n{attachment_text}")
        if matching_vendor_invoice_po_id:
            print(f"[GATEWAY] Vendor invoice path selected for PO #{matching_vendor_invoice_po_id}")
            invoice_result = process_inbound_vendor_invoice_email(
                matching_vendor_invoice_po_id,
                raw_sender_email,
                email_subject,
                email_body,
                attachment_path=attachment_paths[0] if attachment_paths else None,
                received_at=received_at
            )
            category = "VendorInvoice"
        elif matching_po_id:
            print(f"[GATEWAY] Vendor PO update path selected for PO #{matching_po_id}")
            po_result = process_inbound_vendor_po_email(
                matching_po_id,
                raw_sender_email,
                email_subject,
                email_body,
                attachment_path=attachment_paths[0] if attachment_paths else None,
                received_at=received_at
            )
            if po_result:
                if po_result.get("ready_for_pickup"):
                    category = "VendorPOUpdate"
                elif po_result.get("receipt_logged"):
                    send_to_user(
                        subject=f"PO Receipt Logged: #{po_result['po_id']}",
                        content=(
                            f"Receipt data was logged for PO #{po_result['po_id']}.\n\n"
                            f"Received lines: {po_result.get('received_count')}\n"
                            f"Backordered lines noted: {po_result.get('backordered_count')}\n"
                            f"Site: {po_result['site_name']}"
                        )
                    )
                    category = "VendorPOUpdate"
                elif po_result.get("eta_date"):
                    send_to_user(
                        subject=f"Vendor ETA Logged: PO #{po_result['po_id']}",
                        content=(
                            f"Vendor {po_result['vendor_name']} replied on PO #{po_result['po_id']}.\n\n"
                            f"Site: {po_result['site_name']}\n"
                            f"ETA: {po_result['eta_date']}\n"
                            f"Detail: {po_result.get('eta_text') or 'No detail captured'}\n"
                            f"Long lead: {'Yes' if po_result.get('long_lead') else 'No'}"
                        )
                    )
                    category = "VendorPOUpdate"
                else:
                    category = "VendorPOUpdate"
            else:
                category = "JobUpdate"
        else:
            matching_rfq_id = find_matching_rfq_for_email(raw_sender_email, email_subject, f"{email_body}\n{attachment_text}")
        if not matching_po_id and matching_rfq_id:
            print(f"[GATEWAY] Vendor quote path selected for RFQ #{matching_rfq_id}")
            quote_result = process_inbound_vendor_rfq_email(
                matching_rfq_id,
                raw_sender_email,
                email_subject,
                email_body,
                attachment_path=attachment_paths[0] if attachment_paths else None,
                received_at=received_at
            )
            if quote_result:
                print(f"[GATEWAY] Vendor RFQ response processed for RFQ #{quote_result['rfq_id']}")
                if quote_result.get("matched_count") is not None:
                    send_to_user(
                        subject=f"Vendor Quote Ready For Review: RFQ #{quote_result['rfq_id']}",
                        content=(
                            f"Vendor quote ingested from {quote_result['vendor_name']} for RFQ #{quote_result['rfq_id']}.\n\n"
                            f"Estimate: #{quote_result['estimate_id']}\n"
                            f"Site: {quote_result['site_name']}\n"
                            f"Extraction source: {quote_result['extraction_source']}\n"
                            f"Matched prices: {quote_result['matched_count']}\n"
                            f"Unmatched items: {quote_result['unmatched_count']}\n"
                            f"Material catalog updates: {quote_result['material_updates']}\n"
                            f"Archived quote: {quote_result['archived_quote_path'] or 'Email body only'}\n\n"
                            "You can now review job costs. If the estimate is still draft, review vendor pricing, finalize the electrical estimate, and lock it when ready."
                        )
                    )
                    category = "VendorQuote"
                else:
                    category = "VendorRFQUpdate"
            else:
                print("[GATEWAY] Vendor quote path selected but no quote result was returned.")
                category = "JobUpdate"
        elif email_subject.lower().startswith("re:"):
            print("[GATEWAY] Customer reply path selected.")
            estimate_response = process_estimate_response(
                raw_sender_email,
                email_subject,
                latest_reply_body or email_body,
                msg.get("Message-ID")
            )
            if estimate_response:
                category = estimate_response["status"]
            else:
                category = "JobUpdate"
        else:
            if should_skip_non_workflow_email(
                raw_sender_email,
                email_subject,
                email_body,
                header_text,
                matching_rfq_id=matching_rfq_id,
            ):
                print(f"[GATEWAY] Skipping non-workflow email from {raw_sender_email}: {email_subject}")
                mail.store(latest_id, '+FLAGS', '\\Seen')
                return "SkippedNonWorkflow"
            print("Handing off to Argon-Public for classification...")
            # --- THE FIX: Give the Classifier the Subject Line! ---
            full_classification_context = f"Subject: {email_subject}\n\nBody: {email_body}"
            corrected_examples = get_similar_feedback_examples(email_subject, email_body)
            category = get_public_router().classify_inbound_email(full_classification_context, corrected_examples=corrected_examples)

        if verification_result and (verification_result.get("customer_updated") or verification_result.get("site_updated")):
            if category in ("JobUpdate", "CustomerSchedulingContext", "Error") or not category:
                category = "CustomerVerification"

        if category == "NewJobInquiry":
            print(f"Action: New Lead Detected. Extracting payload...")
            
            context_for_ai = f"Sender Name from Header: {header_name}\n\nEmail Content:\n{email_body}"
            lead_packet = get_public_router().extract_lead_data(context_for_ai)
            
            if not lead_packet: lead_packet = {}

            name = lead_packet.get('CustomerName')
            if not name or str(name).lower() in ["unknown", "null", "none"]:
                name = header_name
                print(f"Fallback: AI missed name, using Header: {name}")

            address = lead_packet.get('SiteAddress')
            scope = lead_packet.get('ScopeOfWork')
            phone = lead_packet.get('CustomerPhone')

            # --- THE FIX: Capture the DB Result to get the ID and Missing Fields ---
            db_result = create_new_lead(
                customer_name=name, 
                email=raw_sender_email, 
                phone=phone or "Pending", 
                inquiry_text=email_body,
                address=address,
                scope=scope 
            )
            
            # Extract the ID and remaining missing fields from the database.
            current_estimate_id = db_result.get("estimate_id") if isinstance(db_result, dict) else None
            current_customer_id = db_result.get("customer_id") if isinstance(db_result, dict) else None
            missing = db_result.get("missing", []) if isinstance(db_result, dict) else []

            if current_customer_id:
                backfill_result = try_backfill_customer_from_inbox(
                    raw_sender_email,
                    current_customer_id,
                    header_name=name,
                )
                if backfill_result.get("updated_fields") and current_estimate_id:
                    add_note_to_active_job(
                        current_estimate_id,
                        f"Customer profile backfilled from inbox history: {', '.join(backfill_result['updated_fields'])}"
                    )

            intake_status = get_customer_intake_status(current_estimate_id) if current_estimate_id else None
            customer_missing = intake_status.get("missing", []) if intake_status else []
            site_missing = "SiteAddress" in missing

            if customer_missing and current_estimate_id:
                if has_customer_info_request_been_sent(current_estimate_id, customer_missing):
                    print(f"Action: Customer profile still missing {customer_missing}, but a request was already sent.")
                else:
                    print(f"Action: Customer profile incomplete ({customer_missing}). Sending intake request...")
                    send_customer_info_request(current_estimate_id, name, raw_sender_email, customer_missing)

            if site_missing and current_estimate_id:
                print("Action: Site address still missing. Sending smart discovery...")
                send_smart_discovery(current_estimate_id, name, raw_sender_email, "Please provide the project site address")

            if (customer_missing or site_missing) and not current_estimate_id:
                print("⚠️ Error: Could not retrieve Estimate ID to send discovery.")
            elif not customer_missing and not site_missing:
                print(f"🚨 Action: Lead #{current_estimate_id} complete. Marking as READY for the Sweeper...")
                mark_estimate_ready(current_estimate_id)

        elif category == "JobUpdate":
            print(f"Action: Job Update from {header_name}. (Database note attachment logic pending ID lookup).")

        elif category in ("Accepted", "Rejected"):
            print(f"Action: Estimate response processed from {header_name}. Status -> {category}.")

        elif category == "EstimateNumberNeeded":
            print(f"Action: Requested estimate number confirmation from {header_name}.")

        elif category == "EstimateNumberMismatch":
            print(f"Action: Customer reply included an unmatched estimate number from {header_name}.")

        elif category == "CustomerSchedulingContext":
            print(f"Action: Post-acceptance customer scheduling context logged for {header_name}.")

        elif category == "CustomerVerification":
            print(f"Action: Customer/site verification reply processed for {header_name}.")

        elif category == "VendorQuote":
            print(f"Action: Vendor quote ingested and routed for review from {header_name}.")

        elif category == "VendorRFQUpdate":
            print(f"Action: Vendor RFQ response logged without pricing from {header_name}.")

        elif category == "VendorPOUpdate":
            print(f"Action: Vendor PO ETA update logged from {header_name}.")

        elif category == "VendorInvoice":
            print(f"Action: Vendor invoice draft created from {header_name}.")

        elif category == "Spam":
            print(f"Action: Purging Spam from {raw_sender}")
            mail.store(latest_id, '+X-GM-LABELS', '\\Trash')
            mail.expunge() 

        elif category == "General":
            review_entry = create_review_record(
                sender_email=raw_sender_email,
                subject=email_subject,
                body=email_body,
                predicted_category=category,
                attachment_names=[os.path.basename(path) for path in attachment_paths],
            )
            send_classification_review_to_owner(review_entry)
            print(f"Action: Routed general message to owner for classification feedback via {review_entry['review_id']}.")

        else: # <--- Added this safety net
            print(f"⚠️ AI Confusion: The AI returned an unknown category -> '{category}'")
            review_entry = create_review_record(
                sender_email=raw_sender_email,
                subject=email_subject,
                body=email_body,
                predicted_category=category,
                attachment_names=[os.path.basename(path) for path in attachment_paths],
            )
            send_classification_review_to_owner(review_entry)
            print(f"Action: Routed unknown classification to owner for feedback via {review_entry['review_id']}.")

        mail.store(latest_id, '+FLAGS', '\\Seen')
        return category

    except Exception as e:
        print(f"IMAP Error: {e}")
        return None

def _deprecated_save_to_drafts_folder(subject, content, recipient_email, attachment_path=None):
    """Deprecated draft helper. Estimates now send directly instead of saving drafts."""
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_ADDR 
    msg['To'] = recipient_email
    msg['Cc'] = MY_EMAIL 
    msg.set_content(content)

    if attachment_path and os.path.exists(attachment_path):
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or 'application/octet-stream').split('/', 1)
        with open(attachment_path, 'rb') as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(attachment_path))

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(EMAIL_ADDR, EMAIL_PASS)
        mail.append("[Gmail]/Drafts", '', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        mail.logout()
        return True
    except Exception as e:
        print(f"❌ Failed to save draft: {e}")
        return False

if __name__ == "__main__":
    print("Checking for new instructions...")
    check_for_instructions()
