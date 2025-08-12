"""
drafts_module.py — Microsoft Graph + Streamlit (components.html variant)

Drop-in module that lets you:
1) Paste an inbound email and generate a grounded reply draft with citations.
2) (Optional) Connect to a Microsoft 365 mailbox via Graph to list recent emails,
   pick one, and create a **reply draft** in Outlook (Drafts folder). Optionally send
   automatically for low-risk ("Green") categories.

ENV VARS REQUIRED (for Outlook mode):
- GRAPH_TENANT_ID
- GRAPH_CLIENT_ID
- GRAPH_CLIENT_SECRET
- GRAPH_MAILBOX_ADDRESS        # e.g. "civreply@yourtenant.onmicrosoft.com"
- (Optional) OPENAI_API_KEY    # if you wire to your retriever/LLM

AZURE APP PERMISSIONS (Application permissions, admin consent):
- Mail.ReadWrite   # read inbox and create/update drafts
- Mail.Send        # send a draft when auto-send is enabled
"""

from __future__ import annotations
import os
import re
import json
import html
from typing import Callable, Dict, List, Optional, Tuple

import requests
import streamlit as st
import streamlit.components.v1 as components  # use components.html

# ===============
# Graph Client
# ===============

class GraphClient:
    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mailbox_address: Optional[str] = None,
    ):
        self.tenant_id = tenant_id or os.getenv("GRAPH_TENANT_ID")
        self.client_id = client_id or os.getenv("GRAPH_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("GRAPH_CLIENT_SECRET")
        self.mailbox_address = mailbox_address or os.getenv("GRAPH_MAILBOX_ADDRESS")
        self.token: Optional[str] = None

        missing = [
            k
            for k, v in {
                "GRAPH_TENANT_ID": self.tenant_id,
                "GRAPH_CLIENT_ID": self.client_id,
                "GRAPH_CLIENT_SECRET": self.client_secret,
                "GRAPH_MAILBOX_ADDRESS": self.mailbox_address,
            }.items()
            if not v
        ]
        self.enabled = len(missing) == 0
        if not self.enabled:
            st.info(
                "Graph not configured (missing: %s). Paste-mode still works."
                % ", ".join(missing)
            )

    def _acquire_token(self) -> Optional[str]:
        """Acquire app-only token using client credentials."""
        try:
            token_url = (
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            )
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            }
            resp = requests.post(token_url, data=data, timeout=15)
            if resp.ok:
                self.token = resp.json().get("access_token")
                return self.token
            else:
                st.error(f"Token request failed: {resp.status_code} {resp.text[:300]}")
                return None
        except Exception as e:
            st.exception(e)
            return None

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            self._acquire_token()
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def list_inbox(self, top: int = 25) -> List[Dict]:
        if not self.enabled:
            return []
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/mailFolders/Inbox/messages"
            f"?$top={top}&$orderby=receivedDateTime desc&$select=id,subject,from,receivedDateTime,hasAttachments,conversationId"
        )
        r = requests.get(url, headers=self._headers(), timeout=20)
        if not r.ok:
            st.error(f"Graph list_inbox failed: {r.status_code} {r.text[:300]}")
            return []
        return r.json().get("value", [])

    def get_message(self, message_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/messages/{message_id}"
        r = requests.get(url, headers=self._headers(), timeout=20)
        if not r.ok:
            st.error(f"Graph get_message failed: {r.status_code} {r.text[:300]}")
            return None
        return r.json()

    def create_reply_draft(
        self, message_id: str, reply_html: str, comment: Optional[str] = None
    ) -> Optional[str]:
        """Create a reply draft for a specific message, then patch in our HTML body.
        Returns new draft message id on success.
        """
        if not self.enabled:
            return None
        # 1) create the draft reply
        url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/messages/{message_id}/createReply"
        payload = {"comment": comment or ""}
        r = requests.post(url, headers=self._headers(), data=json.dumps(payload), timeout=20)
        if not r.ok:
            st.error(f"Graph createReply failed: {r.status_code} {r.text[:300]}")
            return None
        draft = r.json()
        draft_id = draft.get("id")
        if not draft_id:
            st.error("Graph createReply returned no draft id.")
            return None
        # 2) patch the body content to our generated HTML
        patch_url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/messages/{draft_id}"
        patch_body = {"body": {"contentType": "HTML", "content": reply_html}}
        r2 = requests.patch(patch_url, headers=self._headers(), data=json.dumps(patch_body), timeout=20)
        if not r2.ok:
            st.error(f"Graph patch draft failed: {r2.status_code} {r2.text[:300]}")
            return None
        return draft_id

    def send_draft(self, draft_id: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/messages/{draft_id}/send"
        r = requests.post(url, headers=self._headers(), timeout=20)
        if not r.ok:
            st.error(f"Graph send draft failed: {r.status_code} {r.text[:300]}")
            return False
        return True

# ==================
# Helpers & Policy
# ==================

GREEN_KEYWORDS = [
    "bin", "waste", "hard rubbish", "green waste", "opening hours", "rates notice",
    "parking permit", "pets", "dogs", "cats", "fee", "application form", "contact number",
    "address", "event", "library", "transfer station", "tip", "recycling"
]

AMBER_TRIGGERS = [
    "complaint", "unhappy", "delay", "refund", "appeal", "escalate", "supervisor",
    "deadline", "urgent", "threat", "media", "ombudsman", "privacy"
]

RED_TRIGGERS = [
    "FOI", "freedom of information", "accident", "injury", "legal", "threaten", "assault",
    "payment dispute", "chargeback", "personal information request", "vulnerable", "danger"
]

PII_PATTERNS = [
    re.compile(r"\b\d{8,}\b"),      # long ids/account numbers
    re.compile(r"\b\+?\d{9,15}\b"), # phone numbers
]

def classify_risk(text: str) -> Tuple[str, List[str]]:
    """Return (risk_level, reasons[]) where risk_level in {GREEN, AMBER, RED}."""
    t = text.lower()
    reasons: List[str] = []
    risk = "GREEN"
    if any(k in t for k in RED_TRIGGERS):
        risk = "RED"; reasons.append("High-risk keyword")
    elif any(k in t for k in AMBER_TRIGGERS):
        risk = "AMBER"; reasons.append("Potential complaint/escalation")
    if any(p.search(t) for p in PII_PATTERNS):
        risk = "AMBER" if risk == "GREEN" else risk
        reasons.append("PII detected")
    if not reasons:
        reasons.append("No risk triggers detected")
    return risk, reasons

def default_reply(email_text: str, council_name: str, citations: Optional[List[str]] = None) -> str:
    """Fallback HTML reply when no get_answer_fn is provided."""
    intro = f"<p>Thanks for contacting {html.escape(council_name)}.</p>"
    body = (
        "<p>We received your enquiry and will get back to you with more detail soon. "
        "For common questions about services, permits, rates, and waste collection, "
        "please see the links below.</p>"
    )
    cites = citations or [
        "General services | https://www.wyndham.vic.gov.au/services | Overview",
    ]
    cite_html = "".join(f"<li>{html.escape(c)}</li>" for c in cites)
    footer = (
        "<p>Kind regards,<br/>Customer Service Team</p>"
        "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
    )
    return f"{intro}{body}<ul>{cite_html}</ul>{footer}"

# ==============
# Draft Builder
# ==============

def build_cited_reply(
    email_text: str,
    council_name: str,
    get_answer_fn: Optional[Callable[[str, str], Tuple[str, List[str]]]] = None,
) -> Tuple[str, List[str]]:
    """Build an HTML reply and citations list using provided retriever."""
    try:
        if get_answer_fn:
            html_body, citations = get_answer_fn(email_text, council_name)
            if not isinstance(html_body, str):
                html_body = str(html_body)
            if citations is None:
                citations = []
            html_body += "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
            return html_body, citations
        else:
            return default_reply(email_text, council_name), [
                f"{council_name} services | https://www.{council_name.lower().split()[0]}.vic.gov.au/ | Overview"
            ]
    except Exception as e:
        st.warning(f"Reply generation failed; using fallback. Error: {e}")
        return default_reply(email_text, council_name), []

# ==============
# Streamlit UI
# ==============

def render_drafts_ui(
    get_answer_fn: Optional[Callable[[str, str], Tuple[str, List[str]]]] = None,
    councils: Optional[List[str]] = None,
):
    st.header("📬 Inbox AI — Drafts")
    st.caption("Classify → Ground → Draft → (optional) Auto-send for safe topics")

    councils = councils or ["Wyndham City Council", "Yarra City Council", "City of Melbourne"]
    council_name = st.selectbox("Council", councils, index=0)

    with st.expander("Graph connection (optional)", expanded=False):
        st.write("If configured, you can list recent Inbox emails and draft replies directly in Outlook.")
        st.code("Required app permissions (Application): Mail.ReadWrite, Mail.Send")
        st.write("Mailbox:", os.getenv("GRAPH_MAILBOX_ADDRESS", "<not set>"))

    graph = GraphClient()

    mode = st.radio("Choose input mode", ["Paste email", "Pick from Outlook Inbox"], horizontal=True)

    selected_message = None
    original_text = ""

    if mode == "Pick from Outlook Inbox":
        if not graph.enabled:
            st.stop()
        msgs = graph.list_inbox(top=25)
        if not msgs:
            st.info("No messages found or Graph not authorized.")
            st.stop()
        options = [
            f"{i+1:02d}. {m.get('receivedDateTime','')[:19]} | "
            f"{m.get('from',{}).get('emailAddress',{}).get('address','')} | "
            f"{m.get('subject','')}"
            for i, m in enumerate(msgs)
        ]
        choice = st.selectbox("Select an email", options)
        idx = options.index(choice)
        selected_message = msgs[idx]
        mid = selected_message["id"]
        full = graph.get_message(mid)
        if full:
            body_html = full.get("body", {}).get("content", "") or ""
            stripped = re.sub("<[^<]+?>", "", body_html)
            original_text = stripped
            with st.expander("Original message (preview)", expanded=False):
                st.text_area("", original_text, height=160)
        else:
            st.warning("Could not load the full message body.")
    else:
        original_text = st.text_area(
            "Paste an inbound email (plain text)",
            height=180,
            placeholder="Subject: Green waste pickup\n\nHi Council, when is my green bin collected in Tarneit? ..."
        )

    # Classify risk & build reply
    if st.button("✨ Generate Draft"):
        if not original_text.strip():
            st.warning("Please paste or select an email.")
            st.stop()
        risk, reasons = classify_risk(original_text)
        st.write(f"Risk level: **{risk}** — {', '.join(reasons)}")

        html_body, citations = build_cited_reply(original_text, council_name, get_answer_fn)

        st.markdown("**Draft preview (HTML)**")
        components.html(
            f"<div style='font-family:sans-serif; padding:4px'>{html_body}</div>",
            height=480,
            scrolling=True,
        )

        if citations:
            st.markdown("**Citations**")
            for c in citations:
                st.write("• ", c)

        # Actions
        col1, col2, col3 = st.columns(3)
        with col1:
            st.download_button(
                "⬇️ Download .html",
                data=html_body.encode("utf-8"),
                file_name="reply_draft.html",
                mime="text/html",
            )
        with col2:
            if graph.enabled and selected_message:
                if st.button("📥 Create Outlook Draft"):
                    did = graph.create_reply_draft(
                        selected_message["id"], html_body, comment="Auto-drafted reply"
                    )
                    if did:
                        st.success(f"Draft created in Outlook. (id: {did[:12]}…)")
        with col3:
            if graph.enabled and selected_message and risk == "GREEN":
                if st.button("✅ Auto-send (GREEN only)"):
                    did = graph.create_reply_draft(
                        selected_message["id"], html_body, comment="Auto-drafted reply"
                    )
                    if did and graph.send_draft(did):
                        st.success("Draft sent ✔️ (saved in Sent Items)")
                    else:
                        st.error("Could not send draft. Check permissions (Mail.Send) and retry.")

    st.caption(
        "Admin tip: tune the GREEN/AMBER/RED heuristics in drafts_module.py and wire a proper get_answer_fn for grounded replies."
    )
