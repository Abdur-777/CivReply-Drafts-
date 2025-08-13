# worker_autoreply.py — Render Worker: auto-reply / auto-draft for Outlook (Graph)
# Polls unread Inbox, classifies with retriever_catalog, and replies.
# App permissions needed (admin-consented): Mail.ReadWrite, Mail.Send, offline_access
#
# Env:
#   GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_MAILBOX_ADDRESS
#   COUNCIL_NAME="Wyndham City Council"  (default)
#   POLL_SECONDS=45
#   AUTO_SEND_GREEN=1   (0/1)
#   GREEN_TOPICS="find_bin_day,book_hard_waste,bin_requests,rates_pay,parking_fines_pay,disability_parking,libraries,contact"

import os, time, html, json, re
import requests
from bs4 import BeautifulSoup

import retriever_catalog as rc  # uses your Wyndham catalog + grounded answers

TENANT = os.environ["GRAPH_TENANT_ID"]
CLIENT_ID = os.environ["GRAPH_CLIENT_ID"]
CLIENT_SECRET = os.environ["GRAPH_CLIENT_SECRET"]
MAILBOX = os.environ["GRAPH_MAILBOX_ADDRESS"]

COUNCIL_NAME = os.getenv("COUNCIL_NAME", "Wyndham City Council")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "45"))
AUTO_SEND_GREEN = os.getenv("AUTO_SEND_GREEN", "0") == "1"
GREEN_TOPICS = {t.strip() for t in os.getenv(
    "GREEN_TOPICS",
    "find_bin_day,book_hard_waste,bin_requests,rates_pay,parking_fines_pay,disability_parking,libraries,contact"
).split(",") if t.strip()}

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
UA = "Mozilla/5.0 CivReply Worker"

session = requests.Session()
session.headers.update({"User-Agent": UA})

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
    # MAILBOX can be UPN or email; Graph resolves it
    r = session.get(f"{GRAPH}/users/{MAILBOX}", headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json()["id"]

def _html_to_text(s):
    if not s:
        return ""
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script","style","noscript","svg"]): t.decompose()
    text = soup.get_text(" ").strip()
    return re.sub(r"\s+", " ", text)

def _list_unread(tok, user_id, top=10):
    # Include categories so we can skip ones we already queued for review
    url = (f"{GRAPH}/users/{user_id}/mailFolders/Inbox/messages"
           f"?$select=id,subject,from,body,bodyPreview,categories,receivedDateTime,internetMessageId"
           f"&$filter=isRead eq false"
           f"&$orderby=receivedDateTime desc&$top={top}")
    r = session.get(url, headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json().get("value", [])

def _create_reply_draft(tok, user_id, msg_id):
    # POST createReply -> returns draft reply message object
    r = session.post(f"{GRAPH}/users/{user_id}/messages/{msg_id}/createReply",
                     headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json()["id"]

def _update_message_html(tok, user_id, msg_id, html_body):
    payload = {"body": {"contentType": "HTML", "content": html_body}}
    r = session.patch(f"{GRAPH}/users/{user_id}/messages/{msg_id}",
                      headers=_auth_headers(tok), data=json.dumps(payload), timeout=20)
    r.raise_for_status()

def _send_message(tok, user_id, msg_id):
    r = session.post(f"{GRAPH}/users/{user_id}/messages/{msg_id}/send",
                     headers=_auth_headers(tok), timeout=20)
    # 202 No Content
    if r.status_code not in (200, 202):
        r.raise_for_status()

def _patch_categories(tok, user_id, msg_id, add):
    # fetch existing categories to avoid clobber
    r = session.get(f"{GRAPH}/users/{user_id}/messages/{msg_id}?$select=categories",
                    headers=_auth_headers(tok), timeout=20)
    r.raise_for_status()
    current = set(r.json().get("categories", []))
    new_cats = list(sorted(current.union(set(add))))
    r2 = session.patch(f"{GRAPH}/users/{user_id}/messages/{msg_id}",
                       headers=_auth_headers(tok),
                       data=json.dumps({"categories": new_cats}), timeout=20)
    r2.raise_for_status()

def _mark_read(tok, user_id, msg_id, is_read=True):
    r = session.patch(f"{GRAPH}/users/{user_id}/messages/{msg_id}",
                      headers=_auth_headers(tok),
                      data=json.dumps({"isRead": bool(is_read)}), timeout=20)
    r.raise_for_status()

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

def _is_green(topics):
    # Simple guardrails: single intent & in GREEN list
    if not topics:
        return False
    if len(topics) > 1:
        return False
    return topics[0] in GREEN_TOPICS

def process_message(tok, user_id, msg):
    mid = msg["id"]
    sender = _extract_sender(msg) or "(unknown)"
    text = _message_text(msg)

    # Classify (reuse your classifier)
    topics = rc._classify(text)  # first hit wins
    green = AUTO_SEND_GREEN and _is_green(topics)

    # Compose reply (HTML) + citations
    body_html, cites = rc.answer(text, COUNCIL_NAME)

    # Create reply draft tied to the original thread
    reply_id = _create_reply_draft(tok, user_id, mid)
    _update_message_html(tok, user_id, reply_id, body_html)

    if green:
        _send_message(tok, user_id, reply_id)
        _patch_categories(tok, user_id, mid, ["Auto-sent"])
        _mark_read(tok, user_id, mid, True)
        print(f"[SENT] to {sender} | topics={topics} | cites={len(cites)}")
    else:
        # Leave unread for humans, but tag for triage
        _patch_categories(tok, user_id, mid, ["Needs review"])
        print(f"[DRAFTED] for {sender} | topics={topics} | cites={len(cites)}")

def main():
    tok = _token()
    user_id = _get_user_id(tok)
    print(f"Worker up. Mailbox: {MAILBOX} (userId {user_id}) | auto-send={AUTO_SEND_GREEN} | greens={sorted(GREEN_TOPICS)}")
    while True:
        try:
            msgs = _list_unread(tok, user_id, top=10)
            for m in msgs:
                cats = set(m.get("categories") or [])
                if "Needs review" in cats or "Auto-sent" in cats:
                    continue  # already processed in a previous loop
                process_message(tok, user_id, m)
        except requests.HTTPError as e:
            # refresh token on 401, otherwise log and continue
            if e.response is not None and e.response.status_code == 401:
                tok = _token()
            else:
                print(f"[HTTP] {e} body={getattr(e.response,'text', '')[:300]}")
        except Exception as e:
            print(f"[ERR] {e}")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
