# worker_autoreply.py
# Polls Microsoft Graph for unread emails and auto-replies using your retriever.
#
# Modes:
# - AUTO_SEND_ALL=1  -> auto-reply to ALL incoming mail (except loop/bounce/self).
# - otherwise uses GREEN-only autosend: topic in GREEN_TOPICS AND risk == GREEN AND AUTO_SEND_GREEN=1
#
# ENV (required):
#   GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_MAILBOX_ADDRESS
#
# ENV (behavior):
#   AUTO_SEND_ALL=0|1
#   AUTO_SEND_GREEN=1|0
#   GREEN_TOPICS="waste,rates,libraries,animals,opening hours,general info"
#   COUNCIL_NAME="wyndham"
#   POLL_SECONDS=30
#   REPLY_SIGNATURE="—\nWyndham Information Assistant\n(This is an automated reply)"
#   CATEGORY_REPLIED="AutoReplied"
#   CATEGORY_NEEDS_REVIEW="Needs review"
#   STATE_PATH="/tmp/processed_ids.json"
#   SKIP_NOREPLY=1|0              # default 1: skip senders like no-reply@
#   OPENAI_API_KEY=...            # optional (nicer wording via retriever)
#   OPENAI_MODEL=gpt-4o-mini      # optional
#   CATALOG_PATH=./catalog.json   # optional
#   FAISS_INDEX_ROOT=index        # optional
from __future__ import annotations

import os, re, json, time, traceback
from datetime import datetime, timezone
import requests

# ====== ENV ======
TENANT_ID         = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID         = os.environ.get("GRAPH_CLIENT_ID", "")
CLIENT_SECRET     = os.environ.get("GRAPH_CLIENT_SECRET", "")
MAILBOX_ADDRESS   = os.environ.get("GRAPH_MAILBOX_ADDRESS", "")

AUTO_SEND_ALL     = os.environ.get("AUTO_SEND_ALL", "0") == "1"
AUTO_SEND_GREEN   = os.environ.get("AUTO_SEND_GREEN", "1") == "1"
GREEN_TOPICS_ENV  = os.environ.get("GREEN_TOPICS", "waste,rates,libraries,animals,opening hours,general info")
COUNCIL_NAME      = os.environ.get("COUNCIL_NAME", "wyndham")

POLL_SECONDS      = int(os.environ.get("POLL_SECONDS", "30"))
REPLY_SIGNATURE   = os.environ.get("REPLY_SIGNATURE", "—\nWyndham Information Assistant\n(This is an automated reply)")

CATEGORY_REPLIED  = os.environ.get("CATEGORY_REPLIED", "AutoReplied")
CATEGORY_NEEDSREV = os.environ.get("CATEGORY_NEEDS_REVIEW", "Needs review")
STATE_PATH        = os.environ.get("STATE_PATH", "/tmp/processed_ids.json")
SKIP_NOREPLY      = os.environ.get("SKIP_NOREPLY", "1") == "1"

AUTH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

session = requests.Session()
session.headers["Content-Type"] = "application/json"

def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)

# ====== TOKEN ======
def get_token() -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": AUTH_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def graph_headers(token: str):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ====== SIMPLE STATE (dedupe) ======
def load_state():
    try:
        return set(json.load(open(STATE_PATH)))
    except Exception:
        return set()

def save_state(s):
    try:
        json.dump(list(s), open(STATE_PATH, "w"))
    except Exception:
        pass

processed_ids = load_state()

# ====== TOPIC CLASSIFIER ======
GREEN_TOPICS = [t.strip().lower() for t in GREEN_TOPICS_ENV.split(",") if t.strip()]
TOPIC_KEYWORDS = {
    "waste": ["bin","bins","waste","rubbish","garbage","recycling","collection","hard rubbish","green waste","fogo"],
    "rates": ["rates","rate notice","pay rates","instalment","installment","due date","valuation"],
    "libraries": ["library","libraries","books","tarneit library","werribee library","point cook library","hours"],
    "animals": ["dog","cat","animal","pet registration","microchip","desex"],
    "opening hours": ["opening hours","hours","what time","open today","public holiday hours","closing time"],
    "general info": ["information","contact","help","assistance","services"],
}
def classify_topic(text: str):
    t = (text or "").lower()
    scores = {topic: 0 for topic in TOPIC_KEYWORDS}
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                scores[topic] += 1
    topic = max(scores, key=scores.get)
    if scores[topic] == 0:
        topic = "general info"
    return topic, (topic in GREEN_TOPICS)

# ====== RISK HEURISTICS ======
AMBER_TRIGGERS = ["complaint","unhappy","delay","refund","appeal","escalate","supervisor","deadline","urgent","threat","media","ombudsman","privacy","frustrated","angry"]
RED_TRIGGERS   = ["foi","freedom of information","accident","injury","legal","threaten","assault","payment dispute","chargeback","personal information request","vulnerable","danger","police"]
PII_PATTERNS   = [re.compile(r"\b\d{8,}\b"), re.compile(r"\b\+?\d{9,15}\b")]
def classify_risk(text: str):
    t = (text or "").lower()
    risk, reasons = "GREEN", []
    if any(k in t for k in RED_TRIGGERS):
        risk = "RED"; reasons.append("High-risk keyword")
    elif any(k in t for k in AMBER_TRIGGERS):
        risk = "AMBER"; reasons.append("Potential complaint/escalation")
    if any(p.search(t) for p in PII_PATTERNS):
        if risk == "GREEN": risk = "AMBER"
        reasons.append("PII detected")
    if not reasons: reasons.append("No risk triggers detected")
    return risk, reasons

# ====== LOOP GUARDS ======
AUTO_REPLY_SUBJECT_PATTERNS = (
    "automatic reply","auto reply","autoreply","out of office","ooo",
    "delivery status notification","delivery failure","mail delivery","postmaster"
)
def looks_like_auto_reply(subject: str) -> bool:
    s = (subject or "").lower()
    return any(p in s for p in AUTO_REPLY_SUBJECT_PATTERNS)

def looks_like_noreply(address: str) -> bool:
    a = (address or "").lower()
    return ("no-reply@" in a) or ("noreply@" in a) or ("donotreply@" in a)

# ====== RETRIEVER ======
# Expect: retriever_catalog.answer(query, topic=None, council="wyndham", format="body")
try:
    from retriever_catalog import answer as retrieve_answer
except Exception:
    def retrieve_answer(query, topic=None, council="wyndham", format="body"):
        base = (
            "<p>Thanks for your email. Here’s information related to your question.</p>"
            "<ul><li>Check collection days via the council’s address lookup.</li>"
            "<li>Report missed collections via the service request portal.</li>"
            "<li>Set bins out by 6am with lid closed.</li></ul>"
        )
        links = [
            {"title": "Council services", "url": "https://www.wyndham.vic.gov.au/services"},
            {"title": "Contact council", "url": "https://www.wyndham.vic.gov.au/contact-us"},
        ]
        return {"answer_html": base, "links": links}

# ====== GRAPH HELPERS ======
def list_unread_messages(token: str):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/mailFolders/Inbox/messages"
    params = {
        "$filter": "isRead eq false",
        "$select": "id,subject,from,receivedDateTime,hasAttachments,conversationId,internetMessageId",
        "$top": "15",
        "$orderby": "receivedDateTime desc",
    }
    r = session.get(url, headers=graph_headers(token), params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("value", [])

def get_message_body(token: str, msg_id: str):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    params = {"$select": "id,subject,body,uniqueBody,from"}
    r = session.get(url, headers=graph_headers(token), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    body_html = (data.get("uniqueBody") or {}).get("content") or (data.get("body") or {}).get("content") or ""
    body_text = re.sub("<[^<]+?>", " ", body_html)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    return data, body_text, body_html

def add_categories(token: str, msg_id: str, cats):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    payload = {"categories": cats}
    session.patch(url, headers=graph_headers(token), data=json.dumps(payload), timeout=20)

def mark_read(token: str, msg_id: str):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    payload = {"isRead": True}
    session.patch(url, headers=graph_headers(token), data=json.dumps(payload), timeout=20)

def create_reply_draft(token: str, original_msg_id: str, html_body: str) -> str:
    url_create = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{original_msg_id}/createReply"
    r = session.post(url_create, headers=graph_headers(token), timeout=20)
    r.raise_for_status()
    draft_id = r.json()["id"]
    url_update = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{draft_id}"
    payload = {"body": {"contentType": "HTML", "content": html_body}}
    r2 = session.patch(url_update, headers=graph_headers(token), data=json.dumps(payload), timeout=20)
    r2.raise_for_status()
    return draft_id

def send_draft(token: str, draft_id: str) -> bool:
    url_send = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{draft_id}/send"
    r = session.post(url_send, headers=graph_headers(token), timeout=20)
    r.raise_for_status()
    return True

# ====== EMAIL BUILDER ======
def build_email_html(user_body_text: str, generated: dict) -> str:
    answer_body_html = generated.get("answer_html", "<p>Thanks for your email.</p>")
    links_html = ""
    if generated.get("links"):
        items = "".join(f'<li><a href="{l["url"]}">{l["title"]}</a></li>' for l in generated["links"][:6])
        links_html = f"<p><strong>Official links:</strong></p><ul>{items}</ul>"
    signature_html = f"<p>{REPLY_SIGNATURE.replace(chr(10), '<br>')}</p>"
    quote_text = re.sub(r"\s+", " ", user_body_text or "").strip()
    quote_html = (quote_text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
    return f"""
<div style="font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5">
  <p>Hi,</p>
  {answer_body_html}
  {links_html}
  {signature_html}
  <hr>
  <p style="color:#777">Original question:</p>
  <blockquote style="margin:0 0 0 1em;color:#555;border-left:3px solid #ddd;padding-left:.8em">{quote_html}</blockquote>
</div>
    """.strip()

# ====== PROCESS ONE ======
def process_message(token: str, m: dict):
    msg_id = m["id"]
    if msg_id in processed_ids:
        return
    try:
        data, body_text, _ = get_message_body(token, msg_id)
        subject = (data.get("subject") or "").strip()
        sender  = ((data.get("from") or {}).get("emailAddress") or {}).get("address", "").strip()

        # Loop safety
        if sender and MAILBOX_ADDRESS and sender.lower() == MAILBOX_ADDRESS.lower():
            processed_ids.add(msg_id); save_state(processed_ids); log(f"Skip self {msg_id}"); return
        if looks_like_auto_reply(subject):
            processed_ids.add(msg_id); save_state(processed_ids); log(f"Skip auto-reply {msg_id}: {subject!r}"); return
        if SKIP_NOREPLY and looks_like_noreply(sender):
            processed_ids.add(msg_id); save_state(processed_ids); log(f"Skip no-reply sender {sender}"); return

        topic, topic_is_green = classify_topic(f"{subject}\n{body_text}")
        risk, reasons = classify_risk(f"{subject}\n{body_text}")

        # === AUTOSEND DECISION ===
        # If AUTO_SEND_ALL -> send no matter what (still respecting loop guards above).
        # Else green-only gate as before.
        can_autosend = AUTO_SEND_ALL or (topic_is_green and risk == "GREEN" and AUTO_SEND_GREEN)

        log(f"Processing {msg_id}: topic={topic}, risk={risk}, autosend={'YES' if can_autosend else 'NO'}; sender={sender}")

        generated = retrieve_answer(
            query=f"Subject: {subject}\n\nBody: {body_text}",
            topic=topic,
            council=COUNCIL_NAME,
            format="body",
        )
        html = build_email_html(body_text, generated)
        draft_id = create_reply_draft(token, msg_id, html)

        if can_autosend:
            send_draft(token, draft_id)
            try: add_categories(token, msg_id, [CATEGORY_REPLIED])
            except Exception: pass
            mark_read(token, msg_id)
            log(f"✅ Auto-sent reply to message {msg_id}")
        else:
            try: add_categories(token, msg_id, [CATEGORY_NEEDSREV])
            except Exception: pass
            log(f"✳️ Draft created for review (message {msg_id})")

        processed_ids.add(msg_id); save_state(processed_ids)

    except requests.HTTPError as he:
        log(f"HTTP error processing {msg_id}: {he.response.status_code} {he.response.text[:250]}")
    except Exception as e:
        log(f"❌ Error processing {msg_id}: {e}"); traceback.print_exc()

# ====== MAIN LOOP ======
def main():
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, MAILBOX_ADDRESS]):
        raise RuntimeError("Missing env: GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_MAILBOX_ADDRESS")
    log(f"Worker started. Poll {POLL_SECONDS}s. AUTO_SEND_ALL={AUTO_SEND_ALL}. GREEN_TOPICS={GREEN_TOPICS}. COUNCIL={COUNCIL_NAME}")
    token = get_token()
    while True:
        try:
            token = get_token()
            msgs = list_unread_messages(token)
            if not msgs:
                log("No unread messages.")
            else:
                for m in msgs:
                    process_message(token, m)
        except requests.HTTPError as he:
            log(f"HTTP error: {he.response.status_code} {he.response.text[:250]}")
        except Exception as e:
            log(f"Unexpected error: {e}"); traceback.print_exc()
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
