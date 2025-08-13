# worker_autoreply.py — Render Worker for CivReply
# Polls Outlook (Microsoft Graph) for unread messages and replies using
# retriever_catalog.answer(). Supports three auto-send modes:
#   AUTO_SEND_MODE = "green"  -> send only if single GREEN topic (default if AUTO_SEND_GREEN=1)
#   AUTO_SEND_MODE = "always" -> send everything
#   AUTO_SEND_MODE = "off"    -> never auto-send (only draft)
#
# Required Azure Graph (Application) permissions (admin-consented):
#   - Mail.ReadWrite
#   - Mail.Send
#
# Env vars:
#   GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_MAILBOX_ADDRESS
#   COUNCIL_NAME="Wyndham City Council"
#   POLL_SECONDS=45
#   AUTO_SEND_MODE=green|always|off
#   (legacy) AUTO_SEND_GREEN=0|1  # if AUTO_SEND_MODE not set, 1 -> green, 0 -> off
#   GREEN_TOPICS="find_bin_day,book_hard_waste,bin_requests,rates_pay,parking_fines_pay,disability_parking,libraries,contact"
#   HEADER_AUTO="Automated council reply — sourced from official Wyndham pages"

import os
import time
import json
import re
import html
import requests
from bs4 import BeautifulSoup

import retriever_catalog as rc  # your link-grounded answerer

# ------------ Configuration ------------
TENANT = os.environ["GRAPH_TENANT_ID"]
CLIENT_ID = os.environ["GRAPH_CLIENT_ID"]
CLIENT_SECRET = os.environ["GRAPH_CLIENT_SECRET"]
MAILBOX = os.environ["GRAPH_MAILBOX_ADDRESS"]

COUNCIL_NAME = os.getenv("COUNCIL_NAME", "Wyndham City Council")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "45"))

AUTO_SEND_MODE = os.getenv("AUTO_SEND_MODE")
if not AUTO_SEND_MODE:
    # Back-compat with earlier env
    AUTO_SEND_MODE = "green" if os.getenv("AUTO_SEND_GREEN", "0") == "1" else "off"
AUTO_SEND_MODE = AUTO_SEND_MODE.lower()

GREEN_TOPICS = {
    t.strip()
    for t in os.getenv(
        "GREEN_TOPICS",
        "find_bin_day,book_hard_waste,bin_requests,rates_pay,parking_fines_pay,disability_parking,libraries,contact",
    ).split(",")
    if t.strip()
}

HEADER_AUTO = os.getenv(
    "HEADER_AUTO",
    "Automated council reply — sourced from official Wyndham pages",
)

CATEGORY_SENT = os.getenv("CATEGORY_SENT", "Auto-sent")
CATEGORY_REVIEW = os.getenv("CATEGORY_REVIEW", "Needs review")

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
UA = "Mozilla/5.0 CivReply Worker"

session = requests.Session()
session.headers.update({"User-Agent": UA})

# ------------ Graph helpers ------------

def _token():
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
    }
    r = session.post(TOKEN_URL, data=data, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def _auth_headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

def _get_user_id(tok):
    r = session.get(f"{GRAPH}/users/{MAILBOX}", headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json()["id"]

def _list_unread(tok, user_id, top=20):
    url = (
        f"{GRAPH}/users/{user_id}/mailFolders/Inbox/messages"
        f"?$select=id,subject,from,body,bodyPreview,categories,receivedDateTime,internetMessageId"
        f"&$filter=isRead eq false"
        f"&$orderby=receivedDateTime desc&$top={top}"
    )
    r = session.get(url, headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json().get("value", [])

def _create_reply_draft(tok, user_id, msg_id):
    r = session.post(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}/createReply",
        headers=_auth_headers(tok),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["id"]

def _update_message_html(tok, user_id, msg_id, html_body):
    payload = {"body": {"contentType": "HTML", "content": html_body}}
    r = session.patch(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}",
        headers=_auth_headers(tok),
        data=json.dumps(payload),
        timeout=20,
    )
    r.raise_for_status()

def _send_message(tok, user_id, msg_id):
    r = session.post(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}/send",
        headers=_auth_headers(tok),
        timeout=20,
    )
    if r.status_code not in (200, 202):
        r.raise_for_status()

def _patch_categories(tok, user_id, msg_id, add):
    r = session.get(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}?$select=categories",
        headers=_auth_headers(tok),
        timeout=20,
    )
    r.raise_for_status()
    current = set(r.json().get("categories", []))
    new_cats = list(sorted(current.union(set(add))))
    r2 = session.patch(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}",
        headers=_auth_headers(tok),
        data=json.dumps({"categories": new_cats}),
        timeout=20,
    )
    r2.raise_for_status()

def _mark_read(tok, user_id, msg_id, is_read=True):
    r = session.patch(
        f"{GRAPH}/users/{user_id}/messages/{msg_id}",
        headers=_auth_headers(tok),
        data=json.dumps({"isRead": bool(is_read)}),
        timeout=20,
    )
    r.raise_for_status()

# ------------ Message helpers ------------

def _html_to_text(s):
    if not s:
        return ""
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = soup.get_text(" ").strip()
    return re.sub(r"\s+", " ", text)

def _extract_sender(m):
    try:
        return m["from"]["emailAddress"]["address"]
    except Exception:
        return None

def _message_text(m):
    subj = (m.get("subject") or "").strip()
    body_html = (m.get("body") or {}).get("content") or ""
    body_text = _html_to_text(body_html)
    return (("Subject: " + subj + "\n\n") if subj else "") + body_text

def _should_autosend(topics):
    """Decide whether to auto-send based on mode and topics."""
    mode = AUTO_SEND_MODE
    if mode == "always":
        return True
    if mode == "off":
        return False
    # "green" mode: one clear topic and in GREEN_TOPICS
    if not topics or len(topics) > 1:
        return False
    return topics[0] in GREEN_TOPICS

# ------------ Core processing ------------

def process_message(tok, user_id, msg):
    mid = msg["id"]
    sender = _extract_sender(msg) or "(unknown)"
    text = _message_text(msg)

    # Classify & decide send policy
    topics = rc._classify(text)
    autosend = _should_autosend(topics)

    # Generate the reply (HTML) + citations
    body_html, citations = rc.answer(text, COUNCIL_NAME)

    # If we will auto-send, swap the banner text to an autoresponder header
    if autosend:
        body_html = body_html.replace(
            "Auto-drafted reply — please review before sending",
            HEADER_AUTO,
        )

    # Create reply draft in the thread
    reply_id = _create_reply_draft(tok, user_id, mid)
    _update_message_html(tok, user_id, reply_id, body_html)

    if autosend:
        _send_message(tok, user_id, reply_id)
        _patch_categories(tok, user_id, mid, [CATEGORY_SENT])
        _mark_read(tok, user_id, mid, True)
        print(f"[SENT] to {sender} | topics={topics} | cites={len(citations)}")
    else:
        _patch_categories(tok, user_id, mid, [CATEGORY_REVIEW])
        # leave unread for a human to view in the shared inbox
        print(f"[DRAFTED] for {sender} | topics={topics} | cites={len(citations)}")

# ------------ Main loop ------------

def main():
    tok = _token()
    user_id = _get_user_id(tok)

    print(
        f"Worker up. Mailbox: {MAILBOX} | council='{COUNCIL_NAME}' | "
        f"mode={AUTO_SEND_MODE} | greens={sorted(GREEN_TOPICS)}"
    )

    while True:
        try:
            msgs = _list_unread(tok, user_id, top=15)
            for m in msgs:
                cats = set(m.get("categories") or [])
                # Skip if already processed in a previous cycle
                if CATEGORY_SENT in cats or CATEGORY_REVIEW in cats:
                    continue
                process_message(tok, user_id, m)

        except requests.HTTPError as e:
            # Refresh token on 401, otherwise log and continue
            status = getattr(e.response, "status_code", None)
            if status == 401:
                try:
                    tok = _token()
                    print("[INFO] Token refreshed after 401")
                except Exception as ee:
                    print(f"[AUTH ERR] {ee}")
                    time.sleep(POLL_SECONDS)
            else:
                body = getattr(e.response, "text", "") or ""
                print(f"[HTTP ERR] {e} | body={body[:300]}")
                time.sleep(POLL_SECONDS)

        except Exception as e:
            print(f"[ERR] {e}")
            time.sleep(POLL_SECONDS)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
