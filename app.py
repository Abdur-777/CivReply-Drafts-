# app.py — CivReply Drafts (Outlook-ready)
# Paste-mode works without credentials. Outlook mode needs Graph env vars.

from dotenv import load_dotenv
load_dotenv()  # enables local .env during dev

import html
import streamlit as st
from drafts_module import render_drafts_ui

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")

# ---- temporary stub retriever (replace with your FAISS/LangChain retriever later) ----
def my_retriever(email_text: str, council_name: str):
    # Safely escape any user content before injecting into HTML
    snippet = html.escape(email_text[:800]) if email_text else ""
    body = (
        f"<p>Thanks for contacting {html.escape(council_name)}.</p>"
        f"<p>We received your enquiry and prepared a draft reply based on your message:</p>"
        f"<blockquote>{snippet}</blockquote>"
        f"<p>For common questions on services, permits, rates, and waste collection, "
        f"please see the resources below.</p>"
    )
    citations = [
        "Council services | https://www.wyndham.vic.gov.au/services | Overview",
    ]
    return body, citations

COUNCILS = ["Wyndham City Council", "Yarra City Council", "City of Melbourne"]

st.title("📬 CivReply Drafts")
st.caption("Cited email drafts for council inboxes — auto-send only when it’s safe.")

# Renders the full Drafts UI (paste mode + Outlook mode if Graph is configured)
render_drafts_ui(get_answer_fn=my_retriever, councils=COUNCILS)

st.divider()
st.markdown(
    "To enable Outlook integration, set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, "
    "`GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX_ADDRESS` in your environment."
)
