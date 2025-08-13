# app.py — CivReply Drafts (catalog-based answers + Outlook-ready)
# - Paste mode works without credentials.
# - Outlook mode needs GRAPH_* env vars.
# - Answers come from retriever_catalog.answer() (optionally backed by FAISS/LLM) — no embeddings required to run.

from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import os
import re
import json
import requests
from datetime import datetime, timezone
import streamlit as st
from streamlit.components.v1 import html as st_html

# Local retriever (you already have this from earlier step)
from retriever_catalog import answer as catalog_answer

# =========================
# Helpers: councils
# =========================

def load_councils() -> list[str]:
    """Load council names for the dropdown from councils.json; fallback if missing."""
    try:
        with open("councils.json", "r") as f:
            data = json.load(f)
            # Expect keys as display names; preserve JSON order
            return list(data.keys())
    except Exception:
        return [
            "Wyndham City Council",
            "City of Melbourne",
            "Yarra City Council",
            "City of Port Phillip",
            "City of Stonnington",
            "Glen Eira City Council",
            "City of Boroondara",
            "City of Monash",
            "Bayside City Council",
            "City of Kingston",
            "Greater Dandenong City Council",
            "City of Casey",
            "Frankston City Council",
            "Hobsons Bay City Council",
            "Maribyrnong City Council",
            "Brimbank City Council",
            "Melton City Council",
            "Hume City Council",
            "City of Whittlesea",
            "Darebin City Council",
            "City of Greater Geelong",
        ]

# Known mappings from display name -> slug your retriever understands
DISPLAY_TO_SLUG = {
    "Wyndham City Council": "wyndham",
}

_SLUG_STRIP = re.compile(r"\b(city of|city|shire|council|borough)\b", re.I)

def council_to_slug(name: str) -> str:
    """Map display names to a short slug; default is a simple slugify of the display name.
    You can extend DISPLAY_TO_SLUG for exact matches.
    """
    if name in DISPLAY_TO_SLUG:
        return DISPLAY_TO_SLUG[name]
    s = name.lower().strip()
    s = _SLUG_STRIP.sub("", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    # Many retrievers expect bare council root (e.g., "wyndham"); if the slug contains dashes,
    # keep it but your retriever may not have data for it yet.
    return s

# =========================
# Helpers: Outlook Graph (optional)
# =========================

TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
MAILBOX_ADDRESS = os.getenv("GRAPH_MAILBOX_ADDRESS", "")  # the sender mailbox in your tenant
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"


def outlook_configured() -> bool:
    return all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, MAILBOX_ADDRESS])


def _graph_token() -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_via_outlook(to_address: str, subject: str, html_body: str, cc: list[str] | None = None) -> str:
    """Send an email via the app-permission mailbox using /sendMail.
    Returns Graph message id (internetMessageId not returned here).
    """
    token = _graph_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": True,
    }
    if cc:
        payload["message"]["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc]
    r = requests.post(f"{GRAPH_BASE}/users/{MAILBOX_ADDRESS}/sendMail", headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    # No content on success. Return a timestamp marker for UI.
    return datetime.now(timezone.utc).isoformat()

# =========================
# Streamlit UI
# =========================

COUNCILS = load_councils()

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")
st.title("📬 CivReply Drafts")
st.caption("Link-first, cited email drafts for Victorian councils — auto-send only when it’s safe.")

# Sidebar status
with st.sidebar:
    st.subheader("Status")
    st.write("Outlook (Graph):", "✅ Ready" if outlook_configured() else "⚠️ Not configured")
    st.write("retriever_catalog.py:", "✅ found" if os.path.exists("retriever_catalog.py") else "⚠️ missing")
    st.write("Councils loaded:", len(COUNCILS))
    st.divider()
    st.markdown("**Tip:** Configure `GRAPH_*` env vars to enable one-click sending via Outlook.")

# Session state
if "draft_html" not in st.session_state:
    st.session_state["draft_html"] = ""
if "draft_links" not in st.session_state:
    st.session_state["draft_links"] = []
if "draft_subject" not in st.session_state:
    st.session_state["draft_subject"] = "CivReply – Information"

# Main two columns
left, right = st.columns([7, 5])

with left:
    st.subheader("1) Paste an email (subject + body)")
    st.write("No credentials needed. Choose a council and generate a draft.")

    council_name = st.selectbox("Council", options=COUNCILS, index=max(COUNCILS.index("Wyndham City Council") if "Wyndham City Council" in COUNCILS else 0, 0))

    col_a, col_b = st.columns([3, 1])
    with col_a:
        subj = st.text_input("Email subject", value="Wyndham – Bin collection day for Hoppers Crossing")
    with col_b:
        st.write("")
        if st.button("Insert example"):
            st.session_state["example_body"] = (
                "Hi team,\n"
                "I’m a Wyndham resident. What day is general waste and recycling collected for Hoppers Crossing (3029)?\n"
                "Please include the official Wyndham links and what to do if a collection is missed.\n"
                "Thanks!"
            )
    body = st.text_area("Email body", height=180, value=st.session_state.get("example_body", ""))

    col1, col2 = st.columns([1, 2])
    with col1:
        gen = st.button("Generate draft ✨", type="primary", use_container_width=True)
    with col2:
        clear = st.button("Clear", use_container_width=True)

    if clear:
        st.session_state["draft_html"] = ""
        st.session_state["draft_links"] = []
        st.session_state["draft_subject"] = subj or "CivReply – Information"
        st.session_state.pop("example_body", None)
        st.experimental_rerun()

    if gen:
        if not (subj.strip() and body.strip()):
            st.warning("Please provide both subject and body.")
        else:
            with st.spinner("Generating draft..."):
                try:
                    slug = council_to_slug(council_name)
                    result = catalog_answer(f"Subject: {subj}\n\nBody: {body}", council=slug, format="email")
                    # result: {"answer_html": ..., "links": [...]}
                    st.session_state["draft_html"] = result.get("answer_html", "<p>No content.</p>")
                    st.session_state["draft_links"] = result.get("links", [])
                    st.session_state["draft_subject"] = subj
                    st.success("Draft generated.")
                except Exception as e:
                    st.error(f"Error generating draft: {e}")

with right:
    st.subheader("2) Preview & export")
    if st.session_state["draft_html"]:
        # Show a live HTML preview (safe/contained)
        st_html(st.session_state["draft_html"], height=420, scrolling=True)

        # Links list
        links = st.session_state["draft_links"]
        if links:
            st.markdown("**Links included:**")
            for l in links:
                url = l.get("url")
                title = l.get("title") or url
                if url:
                    st.markdown(f"- [{title}]({url})")

        # Raw HTML / download
        with st.expander("View / copy raw HTML"):
            st.code(st.session_state["draft_html"], language="html")
        st.download_button(
            label="Download draft as HTML",
            file_name="civreply_draft.html",
            data=st.session_state["draft_html"],
            mime="text/html",
            use_container_width=True,
        )
    else:
        st.info("Generate a draft to preview here.")

    st.divider()

    st.subheader("3) (Optional) Send via Outlook")
    if not outlook_configured():
        st.caption("Configure `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_MAILBOX_ADDRESS` in your environment to enable sending.")
    to_addr = st.text_input("To (recipient)", value=os.getenv("TEST_TO", ""))
    cc_addrs = st.text_input("CC (comma-separated)", value="")

    colx, coly = st.columns([1, 2])
    with colx:
        can_send = outlook_configured() and bool(st.session_state["draft_html"]) and bool(to_addr.strip())
        send_click = st.button("Send now via Outlook 🚀", disabled=not can_send, use_container_width=True)
    with coly:
        st.caption("Uses Microsoft Graph /sendMail from your configured mailbox.")

    if send_click:
        try:
            cc_list = [a.strip() for a in cc_addrs.split(",") if a.strip()] if cc_addrs else None
            stamp = send_via_outlook(
                to_address=to_addr.strip(),
                subject=st.session_state["draft_subject"],
                html_body=st.session_state["draft_html"],
                cc=cc_list,
            )
            st.success(f"Sent ✔️  ({stamp})")
        except requests.HTTPError as he:
            st.error(f"Graph error {he.response.status_code}: {he.response.text}")
        except Exception as e:
            st.error(f"Send failed: {e}")

st.divider()
st.markdown(
    """
**Notes**
- Paste mode works offline; no Graph credentials needed.
- Outlook send requires app permissions **Mail.ReadWrite**, **Mail.Send**, **offline_access** and a mailbox in your tenant.
- The retriever is powered by `retriever_catalog.answer()`; if FAISS indexes exist (e.g., `index/wyndham/`) and `OPENAI_API_KEY` is set, it will enrich answers automatically.
    """
)
