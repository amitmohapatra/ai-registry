"""Labeled benchmark for the tool-similarity engine.

Runs the REAL pipeline (fastembed + reranker + guards) against labeled pairs and
reports precision/recall/F1 at the default threshold, plus the confusion cases.

    ../.venv/bin/python scripts/similarity_bench.py          (from repo root: .venv/bin/python)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("REGISTRY_EMBEDDING_PROVIDER", "fastembed")
os.environ.setdefault("REGISTRY_RERANKER", "fastembed")

from app.similarity import reranker, tool_equivalence  # noqa: E402

def T(name, desc, params=None, ann=None, title=""):
    return {"name": name, "description": desc, "title": title,
            "annotations": ann or {},
            "input_schema": {"type": "object",
                             "properties": {p: {"type": t} for p, t in (params or {}).items()}}}

R, W = {"readOnlyHint": True}, {"readOnlyHint": False}

# label: 1 = duplicate (same capability), 0 = distinct
PAIRS = [
    # exact / near-exact duplicates
    (T("get_invoice", "Fetch an invoice by its ID.", {"invoice_id": "string"}),
     T("fetch_invoice", "Fetch an invoice by ID.", {"invoice_id": "string"}), 1),
    (T("send_email", "Send an email to a recipient.", {"to": "string", "body": "string"}),
     T("email_send", "Sends an email message to a given recipient.", {"recipient": "string", "message": "string"}), 1),
    # paraphrase / synonym duplicates (zero shared words in places)
    (T("get_invoice", "Fetch an invoice by its ID.", {"invoice_id": "string"}),
     T("bill_lookup", "Retrieve a billing document using its identifier.", {"bill_id": "string"}), 1),
    (T("delete_user", "Permanently delete a user account.", {"user_id": "string"}),
     T("remove_member", "Erase a member profile from the system for good.", {"member_id": "string"}), 1),
    (T("track_package", "Track a shipment by tracking number.", {"tracking_no": "string"}),
     T("shipment_status", "Look up the delivery status of a parcel using its tracking code.", {"code": "string"}), 1),
    (T("create_ticket", "Create a new support ticket.", {"subject": "string", "body": "string"}),
     T("open_case", "Opens a new customer support case.", {"title": "string", "details": "string"}), 1),
    # same object, DIFFERENT capability -> distinct
    (T("get_invoice", "Fetch an invoice by its ID.", {"invoice_id": "string"}),
     T("create_invoice", "Create a new invoice for a customer order.", {"customer_id": "string"}), 0),
    (T("get_user", "Fetch a user profile.", {"user_id": "string"}),
     T("delete_user", "Permanently delete a user account.", {"user_id": "string"}), 0),
    (T("list_orders", "List all orders for a customer.", {"customer_id": "string"}),
     T("cancel_order", "Cancel an existing order before it ships.", {"order_id": "string"}), 0),
    # same domain, different scope -> distinct
    (T("get_invoice", "Fetch a single invoice by its ID.", {"invoice_id": "string"}),
     T("list_invoices", "List all invoices for a customer with pagination.", {"customer_id": "string"}), 0),
    (T("send_email", "Send an email to a recipient.", {"to": "string"}),
     T("send_sms", "Send an SMS text message to a phone number.", {"phone": "string"}), 0),
    (T("refund_payment", "Refund a payment to the original method.", {"payment_id": "string"}),
     T("capture_payment", "Capture a previously authorized payment.", {"payment_id": "string"}), 0),
    # unrelated -> distinct
    (T("get_invoice", "Fetch an invoice by its ID.", {"invoice_id": "string"}),
     T("rotate_logs", "Rotate and compress server log files weekly.", {}), 0),
    (T("track_package", "Track a shipment by tracking number.", {"tracking_no": "string"}),
     T("translate_text", "Translate text between two languages.", {"text": "string"}), 0),
    # thin descriptions must not claim duplicate
    (T("fetch_invoice_details", "fetch", {"invoice_id": "string"}),
     T("get_invoice", "Fetch an invoice by its ID.", {"invoice_id": "string"}), 0),
    # ---- declared-effect (annotation) cases: exotic verbs, deterministic verdicts ----
    (T("get_allocation_recommendation", "Recommends the optimal allocation for a portfolio.",
       {"portfolio_id": "string"}, ann=R),
     T("submit_allocation_adjustment", "Applies an allocation adjustment to the portfolio.",
       {"portfolio_id": "string", "adjustment": "object"}, ann=W), 0),
    (T("preview_statement", "Render a preview of the monthly statement.",
       {"account_id": "string"}, ann=R),
     T("finalize_statement", "Finalize and issue the monthly statement.",
       {"account_id": "string"}, ann=W), 0),
    (T("lookup_exchange_rate", "Look up the current exchange rate between two currencies.",
       {"base": "string", "quote": "string"}, ann=R),
     T("get_fx_rate", "Fetch the live FX conversion rate for a currency pair.",
       {"from_ccy": "string", "to_ccy": "string"}, ann=R), 1),
    # title carries the meaning when the name is cryptic
    (T("svc_op_412", "Sends the invoice document to the customer over email.",
       {"invoice_id": "string"}, title="Email invoice to customer"),
     T("send_invoice_email", "Email an invoice to the customer.",
       {"invoice_id": "string"}), 1),
]

THRESHOLD = 0.5

def main():
    rr = reranker()
    print(f"reranker: {rr.name} | threshold: {THRESHOLD}\n")
    tp = fp = tn = fn = 0
    for a, b, label in PAIRS:
        s = tool_equivalence(rr, a, b) or 0.0
        pred = 1 if s >= THRESHOLD else 0
        mark = "ok " if pred == label else ("FP " if pred else "FN ")
        if pred and label: tp += 1
        elif pred and not label: fp += 1
        elif not pred and label: fn += 1
        else: tn += 1
        print(f"  [{mark}] {round(s*100):3d}%  {a['name']} vs {b['name']}  (label={'dup' if label else 'distinct'})")
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    print(f"\nprecision={prec:.2f}  recall={rec:.2f}  F1={f1:.2f}  (tp={tp} fp={fp} tn={tn} fn={fn})")

if __name__ == "__main__":
    main()
