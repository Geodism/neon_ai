import datetime
import difflib
import json
import os
import re
import uuid


FEEDBACK_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "classification_feedback.json")

VALID_CATEGORIES = {
    "newjobinquiry": "NewJobInquiry",
    "vendorquote": "VendorQuote",
    "vendorinvoice": "VendorInvoice",
    "packingslip": "PackingSlip",
    "general": "General",
    "spam": "Spam",
}


def load_feedback_entries():
    if not os.path.exists(FEEDBACK_PATH):
        return []
    try:
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_feedback_entries(entries):
    with open(FEEDBACK_PATH, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2)


def create_review_record(sender_email: str, subject: str, body: str, predicted_category: str, attachment_names=None):
    entries = load_feedback_entries()
    review_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
    entry = {
        "review_id": review_id,
        "sender_email": sender_email,
        "subject": subject or "",
        "body": body or "",
        "predicted_category": predicted_category or "",
        "corrected_category": None,
        "status": "pending",
        "created_at": datetime.datetime.now().isoformat(),
        "attachment_names": attachment_names or [],
    }
    entries.append(entry)
    save_feedback_entries(entries)
    return entry


def normalize_feedback_label(text: str):
    lowered = re.sub(r"[^a-z]+", "", str(text or "").lower())
    if lowered in {"lead", "newlead", "newjob", "newjobinquiry"}:
        return "NewJobInquiry"
    if lowered in {"notalead", "notlead", "notjob", "general"}:
        return "General"
    if lowered in {"spam", "junk"}:
        return "Spam"
    if lowered in VALID_CATEGORIES:
        return VALID_CATEGORIES[lowered]
    return None


def extract_review_id(text: str):
    match = re.search(r"\bREV-[A-Z0-9]{8}\b", str(text or "").upper())
    return match.group(0) if match else None


def parse_feedback_category(subject: str, body: str):
    combined = f"{subject}\n{body}".strip()

    explicit_match = re.search(r"classification\s*:\s*([A-Za-z ]+)", combined, flags=re.I)
    if explicit_match:
        normalized = normalize_feedback_label(explicit_match.group(1))
        if normalized:
            return normalized

    for line in str(body or "").splitlines():
        normalized = normalize_feedback_label(line.strip())
        if normalized:
            return normalized

    normalized = normalize_feedback_label(combined)
    if normalized:
        return normalized
    return None


def record_feedback(review_id: str, corrected_category: str, corrected_by: str, correction_subject: str = "", correction_body: str = ""):
    entries = load_feedback_entries()
    for entry in entries:
        if entry.get("review_id") == review_id:
            entry["corrected_category"] = corrected_category
            entry["corrected_by"] = corrected_by
            entry["correction_subject"] = correction_subject
            entry["correction_body"] = correction_body
            entry["status"] = "resolved"
            entry["resolved_at"] = datetime.datetime.now().isoformat()
            save_feedback_entries(entries)
            return entry
    return None


def get_pending_review(review_id: str):
    return next((row for row in load_feedback_entries() if row.get("review_id") == review_id and row.get("status") == "pending"), None)


def get_similar_feedback_examples(subject: str, body: str, topn: int = 3):
    query = f"{subject or ''}\n{body or ''}".strip().lower()
    if not query:
        return []

    scored = []
    for entry in load_feedback_entries():
        if entry.get("status") != "resolved" or not entry.get("corrected_category"):
            continue
        haystack = f"{entry.get('subject') or ''}\n{entry.get('body') or ''}".strip().lower()
        if not haystack:
            continue
        score = difflib.SequenceMatcher(None, query[:1200], haystack[:1200]).ratio()
        if score >= 0.35:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[:topn]]
