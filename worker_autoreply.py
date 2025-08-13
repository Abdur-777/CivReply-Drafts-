# worker_autoreply.py
# Polls Microsoft Graph for unread emails and auto-replies using your retriever.

import os, time, json, re, traceback
import requests
from datetime import datetime, timezone

# ====== ENV ======
TENANT_ID         = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID         = os.environ.get("GRAPH_CLIENT_ID", "")
CLIENT_SECRET     = os.environ.get("GRAPH_CLIENT_SECRET", "")
MAILBOX_ADDRESS   = os.environ.get("GRAPH_MAILBOX_ADDRESS", "")   # e.g. "wyndham-auto@yourdomain.com"
AUTO_SEND_GREEN   = os.environ.get("AUTO_SEND_GREEN", "1") == "1" # "1" to auto-send, else drafts
GREEN_TOPICS_ENV  = os.environ.get("GREEN_TOPICS", "waste,rates,libraries,animals,opening hours,general info")
POLL_SECONDS      = int(os.environ.get("POLL_SECONDS", "30"))
FROM_DISPLAY_NAME = os.environ.get("REPLY_FROM_NAME", "Wyndham Information Assistant")
REPLY_SIGNATURE   = os.environ.get("REPLY_SIGNATURE", "—\nWyndham Information Assistant\n(This is an automated reply)")

# Optional labeling/categories on the original message
CATEGORY_REPLIED  = os.environ.get("CATEGORY_REPLIED", "AutoReplied")
CATEGORY_NEEDSREV = os.environ.get("CATEGORY_NEEDS_REVIEW", "Needs review")

# ====== GRAPH AUTH ======
AUTH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_URL  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

session = requests.Session()
session.headers["Content-Type"] = "application/json"

def get_token():
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
    data = resp.json()
    return data["access_token"]

def graph_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ====== SIMPLE STATE (dedupe) ======
STATE_PATH = "/tmp/processed_ids.json"
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

# ====== CLASSIFIER ======
GREEN_TOPICS = [t.strip().lower() for t in GREEN_TOPICS_ENV.split(",") if t.strip()]

TOPIC_KEYWORDS = {
    "waste": ["bin", "bins", "waste", "rubbish", "garbage", "recycling", "collection", "hard rubbish"],
    "rates": ["rates", "rate notice", "pay rates", "instalment", "due date"],
    "libraries": ["library", "libraries", "books", "tarneit library", "werribee library", "hours"],
    "animals": ["dog", "cat", "animal", "pet registration", "microchip"],
    "opening hours": ["opening hours", "hours", "what time", "open today", "public holiday hours"],
    "general info": ["information", "contact", "help", "assistance", "services"],
}

def classify_topic(text):
    t = text.lower()
    scores = {topic: 0 for topic in TOPIC_KEYWORDS}
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                scores[topic] += 1
    # choose best topic
    topic = max(scores, key=scores.get)
    matched = scores[topic] > 0
    if not matched:
        topic = "general info"
    is_green = topic in GREEN_TOPICS
    return topic, is_green

# ====== RETRIEVER ======
# Expect retriever_catalog.answer(query, topic=None, council="wyndham", format="email") -> dict with fields:
#   { "answer_html": "<p>...</p>", "links": [{"title":"...", "url":"..."}] }
try:
    from retriever_catalog import answer as retrieve_answer
except Exception:
    def retrieve_answer(query, topic=None, council="wyndham", format="email"):
        # Minimal safe fallback so you can test immediately.
        # Replace with your real retrieval pipeline.
        base = (
            "<p>Thanks for your email. Here’s information related to your question.</p>"
            "<ul>"
            "<li>Use the council’s online tools to check collection days by address/suburb.</li>"
            "<li>If a collection is missed, report it via the council’s waste/service request portal and follow the prompts.</li>"
            "<li>Keep bins out by 6am on collection day and ensure lid closes.</li>"
            "</ul>"
        )
        links = [
            {"title": "Wyndham – Waste & Recycling", "url": "https://www.wyndham.vic.gov.au/services/waste-recycling"},
            {"title": "Wyndham – Report a missed bin", "url": "https://www.wyndham.vic.gov.au"}  # replace by retriever
        ]
        return {
            "answer_html": base,
            "links": links,
        }

def build_email_html(user_body_text, generated):
    # generated: dict from retriever with "answer_html" + "links"
    links_html = ""
    if generated.get("links"):
        items = "".join([f'<li><a href="{l["url"]}">{l["title"]}</a></li>' for l in generated["links"][:6]])
        links_html = f"<p><strong>Official links:</strong></p><ul>{items}</ul>"
    signature_html = f"<p>{REPLY_SIGNATURE.replace(chr(10), '<br>')}</p>"
    # Keep it simple, top-summary style
    return f"""
<div style="font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5">
  <p>Hi,</p>
  {generated.get("answer_html","<p>Thanks for your email.</p>")}
  {links_html}
  {signature_html}
  <hr>
  <p style="color:#777">Original question:</p>
  <blockquote style="margin:0 0 0 1em;color:#555;border-left:3px solid #ddd;padding-left:.8em">{user_body_text}</blockquote>
</div>
    """.strip()

# ====== GRAPH HELPERS ======
def list_unread_messages(token):
    # Only basic fields + bodyPreview to keep payload small. We'll fetch the body content per-message.
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

def get_message_body(token, msg_id):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    params = {"$select": "id,subject,body,from"}
    r = session.get(url, headers=graph_headers(token), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    # body is HTML; we’ll also extract a text version for classification
    body_html = data.get("body", {}).get("content", "") or ""
    # naïve text strip:
    body_text = re.sub("<[^<]+?>", " ", body_html)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    return data, body_text, body_html

def add_categories(token, msg_id, cats):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    payload = {"categories": cats}
    r = session.patch(url, headers=graph_headers(token), data=json.dumps(payload), timeout=20)
    # Don’t fail if categories not enabled; best-effort
    return r.status_code

def mark_read(token, msg_id):
    url = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{msg_id}"
    payload = {"isRead": True}
    r = session.patch(url, headers=graph_headers(token), data=json.dumps(payload), timeout=20)
    return r.status_code

def create_reply_draft(token, original_msg_id, html_body):
    # 1) createReply -> returns a draft with default body
    url_create = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{original_msg_id}/createReply"
    r = session.post(url_create, headers=graph_headers(token), timeout=20)
    r.raise_for_status()
    draft = r.json()
    draft_id = draft["id"]
    # 2) overwrite body with our HTML
    url_update = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{draft_id}"
    payload = {"body": {"contentType": "HTML", "content": html_body}}
    r2 = session.patch(url_update, headers=graph_headers(token), data=json.dumps(payload), timeout=20)
    r2.raise_for_status()
    return draft_id

def send_draft(token, draft_id):
    url_send = f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/messages/{draft_id}/send"
    r = session.post(url_send, headers=graph_headers(token), timeout=20)
    r.raise_for_status()
    return True

def process_message(token, m):
    msg_id = m["id"]
    if msg_id in processed_ids:
        return

    try:
        data, body_text, _ = get_message_body(token, msg_id)
        subject = data.get("subject", "")
        sender  = (data.get("from", {}) or {}).get("emailAddress", {}).get("address", "")

        print(f"Processing: {subject!r} from {sender}")

        topic, is_green = classify_topic(f"{subject}\n{body_text}")
        print(f"Classified topic: {topic} | GREEN={is_green}")

        # Generate answer via retriever
        generated = retrieve_answer(
            query=f"Subject: {subject}\n\nBody: {body_text}",
            topic=topic,
            council="wyndham",
            format="email",
        )
        html = build_email_html(body_text, generated)

        draft_id = create_reply_draft(token, msg_id, html)

        if is_green and AUTO_SEND_GREEN:
            send_draft(token, draft_id)
            try:
                add_categories(token, msg_id, [CATEGORY_REPLIED])
            except Exception:
                pass
            mark_read(token, msg_id)
            print(f"✅ Auto-sent reply to message {msg_id}")
        else:
            try:
                add_categories(token, msg_id, [CATEGORY_NEEDSREV])
            except Exception:
                pass
            print(f"✳️ Draft created for review (message {msg_id})")

        processed_ids.add(msg_id)
        save_state(processed_ids)

    except Exception as e:
        print("❌ Error processing message:", e)
        traceback.print_exc()

def main():
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, MAILBOX_ADDRESS]):
        raise RuntimeError("Missing required env vars: GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_MAILBOX_ADDRESS")
    token = get_token()
    print(f"[{datetime.now(timezone.utc).isoformat()}] Worker started. Poll every {POLL_SECONDS}s. Auto-send GREEN={AUTO_SEND_GREEN}. GREEN_TOPICS={GREEN_TOPICS}")

    while True:
        try:
            # Refresh token roughly every 40 minutes or when we get 401s; simple approach: fetch each loop
            token = get_token()
            msgs = list_unread_messages(token)
            if msgs:
                for m in msgs:
                    process_message(token, m)
            else:
                print(f"[{datetime.now().isoformat()}] No unread messages.")
        except requests.HTTPError as he:
            print("HTTP error:", he.response.status_code, he.response.text)
        except Exception as e:
            print("Unexpected error:", e)
            traceback.print_exc()
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
