"""
drafts_module.py — CivReply Drafts (vertical steps)
- Paste-mode works without credentials.
- Optional Outlook send via Microsoft Graph /sendMail.
- Answers come from a retriever function you pass in (e.g., retriever_catalog.answer).

ENV (for Outlook send):
- GRAPH_TENANT_ID
- GRAPH_CLIENT_ID
- GRAPH_CLIENT_SECRET
- GRAPH_MAILBOX_ADDRESS
"""

from __future__ import annotations
import os
import re
import json
import html as _html
from typing import Callable, Dict, List, Optional, Tuple, Union

import requests
import streamlit as st
import streamlit.components.v1 as components  # for components.html

# ======================
# Graph Client (robust)
# ======================

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
            k for k, v in {
                "GRAPH_TENANT_ID": self.tenant_id,
                "GRAPH_CLIENT_ID": self.client_id,
                "GRAPH_CLIENT_SECRET": self.client_secret,
                "GRAPH_MAILBOX_ADDRESS": self.mailbox_address,
            }.items() if not v
        ]
        self.enabled = len(missing) == 0
        if not self.enabled:
            st.info("Outlook send not configured (missing: %s). Paste-mode still works." % ", ".join(missing))

    @property
    def _token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    def _acquire_token(self) -> Optional[str]:
        try:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            }
            resp = requests.post(self._token_url, data=data, timeout=20)
            if resp.ok:
                self.token = resp.json().get("access_token")
                return self.token
            st.error(f"Token request failed: {resp.status_code} {resp.text[:300]}")
            return None
        except Exception as e:
            st.exception(e)
            return None

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            self._acquire_token()
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    # Optional inbox helpers (not used in vertical flow, kept for future)
    def list_inbox(self, top: int = 25) -> List[Dict]:
        if not self.enabled:
            return []
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/mailFolders/Inbox/messages"
            f"?$top={top}&$orderby=receivedDateTime desc"
            f"&$select=id,subject,from,receivedDateTime,hasAttachments,conversationId,isRead"
        )
        r = requests.get(url, headers=self._headers(), timeout=20)
        if not r.ok:
            st.error(f"Graph list_inbox failed: {r.status_code} {r.text[:300]}")
            return []
        return r.json().get("value", [])

    def get_message(self, message_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/messages/{message_id}"
            f"?$select=subject,body,bodyPreview,uniqueBody"
        )
        r = requests.get(url, headers=self._headers(), timeout=20)
        if not r.ok:
            st.error(f"Graph get_message failed: {r.status_code} {r.text[:300]}")
            return None
        return r.json()

    # Send a NEW message (simpler than replying to a thread)
    def send_mail(self, subject: str, html_body: str, to: str, cc: Optional[List[str]] = None) -> bool:
        if not self.enabled:
            return False
        url = f"https://graph.microsoft.com/v1.0/users/{self.mailbox_address}/sendMail"
        payload = {
            "message": {
                "subject": subject or "Re: your enquiry",
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": to.strip()}}],
                "ccRecipients": [{"emailAddress": {"address": x.strip()}} for x in (cc or []) if x.strip()],
            },
            "saveToSentItems": True,
        }
        r = requests.post(url, headers=self._headers(), data=json.dumps(payload), timeout=20)
        if not r.ok:
            st.error(f"Send failed: {r.status_code} {r.text[:300]}")
            return False
        return True

    # Reply helpers (kept for compatibility if you want thread replies later)
    def create_reply_draft(self, message_id: str, reply_html: str, comment: Optional[str] = None) -> Optional[str]:
        if not self.enabled:
            return None
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

# Lowercased triggers to match a lowercased body
GREEN_KEYWORDS = [
    "bin","waste","hard rubbish","green waste","opening hours","rates notice",
    "parking permit","pets","dogs","cats","fee","application form","contact number",
    "address","event","library","transfer station","tip","recycling"
]
AMBER_TRIGGERS = [
    "complaint","unhappy","delay","refund","appeal","escalate","supervisor",
    "deadline","urgent","threat","media","ombudsman","privacy"
]
RED_TRIGGERS = [
    "foi","freedom of information","accident","injury","legal","threaten","assault",
    "payment dispute","chargeback","personal information request","vulnerable","danger"
]
PII_PATTERNS = [
    re.compile(r"\b\d{8,}\b"),      # long numeric ids
    re.compile(r"\b\+?\d{9,15}\b"), # phone numbers
]

def classify_risk(text: str) -> Tuple[str, List[str]]:
    t = (text or "").lower()
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

def _default_reply(council_name: str, links: Optional[List[Dict[str,str]]] = None) -> str:
    intro = f"<p>Thanks for contacting {_html.escape(council_name)}.</p>"
    body = (
        "<p>We received your enquiry and will get back to you with more detail soon. "
        "For common questions about services, permits, rates, and waste collection, "
        "please see the links below.</p>"
    )
    links = links or [{"title": f"{council_name} services", "url": f"https://www.{council_name.lower().split()[0]}.vic.gov.au/services"}]
    items = "".join(f"<li><a href=\"{_html.escape(l['url'])}\">{_html.escape(l['title'])}</a></li>" for l in links if l.get("url"))
    footer = "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
    return f"{intro}{body}<ul>{items}</ul>{footer}"

# Accept multiple return shapes from get_answer_fn
GetAnswerReturn = Union[Tuple[str, List[str]], Dict[str, object], str]

def build_cited_reply(
    email_text: str,
    council_name: str,
    get_answer_fn: Optional[Callable[[str, str], GetAnswerReturn]] = None,
) -> Tuple[str, List[str]]:
    """
    Returns (html_body, citations_as_strings).
    Accepts get_answer_fn returning:
      - (html, [citations])
      - {"answer_html": html, "links": [{"title":..., "url":...}, ...]}
      - "html string"
    """
    try:
        if get_answer_fn:
            res = get_answer_fn(email_text, council_name)

            # Case A: tuple(html, [citations])
            if isinstance(res, (list, tuple)) and len(res) >= 1:
                html_body = str(res[0])
                citations = []
                if len(res) >= 2 and isinstance(res[1], (list, tuple)):
                    citations = [str(x) for x in res[1]]
                if "Auto-drafted reply" not in html_body:
                    html_body += "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
                return html_body, citations

            # Case B: dict with answer_html/links
            if isinstance(res, dict):
                html_body = str(res.get("answer_html") or res.get("html") or "")
                links = res.get("links") or []
                citations: List[str] = []
                for l in links:
                    if isinstance(l, dict) and l.get("url"):
                        title = l.get("title") or l["url"]
                        citations.append(f"{title} | {l['url']}")
                    elif isinstance(l, str):
                        citations.append(l)
                if not html_body:
                    html_body = _default_reply(council_name)
                if "Auto-drafted reply" not in html_body:
                    html_body += "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
                return html_body, citations

            # Case C: plain html string
            if isinstance(res, str):
                html_body = res
                if "Auto-drafted reply" not in html_body:
                    html_body += "<p><em>Auto-drafted reply. Please review before sending.</em></p>"
                return html_body, []

        # Fallback if get_answer_fn missing or failed shapes
        html_body = _default_reply(council_name)
        return html_body, [f"{council_name} services | https://www.{council_name.lower().split()[0]}.vic.gov.au/services"]

    except Exception as e:
        st.warning(f"Reply generation failed; using fallback. Error: {e}")
        html_body = _default_reply(council_name)
        return html_body, []


# ==============
# Streamlit UI (vertical steps)
# ==============

def render_drafts_ui(
    get_answer_fn: Optional[Callable[[str, str], GetAnswerReturn]] = None,
    councils: Optional[List[str]] = None,
):
    st.header("📬 CivReply Drafts")
    st.caption("Link-first, cited email drafts for Victorian councils — auto-send only when it’s safe.")

    # basic styling for vertical steps
    st.markdown(
        """
        <style>
          .step { padding: 14px 0; border-bottom: 1px solid #eee; }
          .step h3 { margin: 0 0 8px 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    councils = councils or ["Wyndham City Council", "Yarra City Council", "City of Melbourne"]
    graph = GraphClient()

    # Keep preview in session so step 2/3 stay visible after "Generate draft"
    if "civreply_draft_html" not in st.session_state:
        st.session_state["civreply_draft_html"] = ""
        st.session_state["civreply_citations"] = []
        st.session_state["civreply_subject"] = ""
        st.session_state["civreply_risk"] = "GREEN"
        st.session_state["civreply_risk_reasons"] = []

    # ----------------------------
    # STEP 1 — Paste an email
    # ----------------------------
    with st.container():
        st.markdown('<div class="step"><h3>1) Paste an email (subject + body)</h3>', unsafe_allow_html=True)

        council_name = st.selectbox("Council", councils, index=0)

        col_a, col_b = st.columns([0.7, 0.3])
        with col_a:
            subj = st.text_input("Email subject", value=st.session_state.get("civreply_subject", "Wyndham – Bin collection day for Hoppers Crossing"))
        with col_b:
            if st.button("Insert example"):
                st.session_state["email_body"] = (
                    "Hi team,\n"
                    "I’m a Wyndham resident. What day is general waste and recycling collected for Hoppers Crossing (3029)? "
                    "Please include the official Wyndham links and what to do if a collection is missed.\n"
                    "Thanks!"
                )

        body = st.text_area("Email body", key="email_body", height=180, placeholder="Paste the customer's email here…")

        if st.button("✨ Generate draft"):
            full_text = f"Subject: {subj}\n\n{body}".strip()
            if not full_text:
                st.warning("Please enter a subject or body.")
            else:
                # classify risk for info
                risk, reasons = classify_risk(full_text)
                st.session_state["civreply_risk"] = risk
                st.session_state["civreply_risk_reasons"] = reasons
                st.session_state["civreply_subject"] = subj

                html_body, citations = build_cited_reply(full_text, council_name, get_answer_fn)
                st.session_state["civreply_draft_html"] = html_body
                st.session_state["civreply_citations"] = citations

        st.markdown("</div>", unsafe_allow_html=True)

    # ----------------------------
    # STEP 2 — Preview & export
    # ----------------------------
    with st.container():
        st.markdown('<div class="step"><h3>2) Preview & export</h3>', unsafe_allow_html=True)

        html_body = st.session_state["civreply_draft_html"]
        if not html_body:
            st.info("Generate a draft to preview here.")
        else:
            st.write(f"Risk level: **{st.session_state['civreply_risk']}** — {', '.join(st.session_state['civreply_risk_reasons'])}")
            components.html(
                f"<div style='font-family:sans-serif; padding:8px'>{html_body}</div>",
                height=480, scrolling=True
            )
            st.download_button(
                "⬇️ Download .html",
                data=html_body.encode("utf-8"),
                file_name="reply_draft.html",
                mime="text/html",
            )
            if st.session_state["civreply_citations"]:
                with st.expander("Citations"):
                    for c in st.session_state["civreply_citations"]:
                        st.write("•", c)

        st.markdown("</div>", unsafe_allow_html=True)

    # ----------------------------
    # STEP 3 — (Optional) Send via Outlook
    # ----------------------------
    with st.container():
        st.markdown('<div class="step"><h3>3) (Optional) Send via Outlook</h3>', unsafe_allow_html=True)

        if not graph.enabled:
            st.caption("Configure GRAPH_* env vars to enable sending: Mail.Send permission and a mailbox.")
        to_addr = st.text_input("To (recipient)")
        cc_line = st.text_input("CC (comma-separated)")

        disabled = not (graph.enabled and st.session_state["civreply_draft_html"] and to_addr.strip())
        if st.button("Send now via Outlook ✉️", disabled=disabled):
            try:
                ok = graph.send_mail(
                    subject=st.session_state.get("civreply_subject") or "Re: your enquiry",
                    html_body=st.session_state["civreply_draft_html"],
                    to=to_addr.strip(),
                    cc=[x.strip() for x in cc_line.split(",") if x.strip()],
                )
                if ok:
                    st.success("Sent ✔️ (saved to Sent Items)")
                else:
                    st.error("Send failed. Check app permissions (Mail.Send) and credentials.")
            except Exception as e:
                st.exception(e)

        st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        "Notes:\n"
        "- Paste mode works offline; no Graph credentials needed.\n"
        "- Outlook send requires app permissions Mail.Send (and typically Mail.ReadWrite) and a mailbox in your tenant.\n"
        "- The retriever is provided via `get_answer_fn`; if FAISS indexes exist and `OPENAI_API_KEY` is set, it can enrich answers."
    )
