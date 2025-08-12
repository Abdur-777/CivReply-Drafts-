# CivReply Drafts — Inbox AI for Councils
# Paste-mode works with NO credentials. Outlook mode needs Graph env vars.

from dotenv import load_dotenv
load_dotenv()  # picks up local .env during dev

import streamlit as st
from drafts_module import render_drafts_ui

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")

# ---- (temporary) simple stub retriever ----
# Replace with your real FAISS/LangChain retriever later.
def my_retriever(email_text: str, council_name: str):
    html = (
        f"<p>Thanks for contacting {council_name}.</p>"
        f"<p>We received your enquiry and prepared a draft reply based on your message:</p>"
        f"<blockquote>{st.escape_markdown(email_text[:800])}</blockquote>"
        f"<p>For common questions on services, permits, rates, and waste collection, "
        f"please see the resources below.</p>"
    )
    citations = [
        "Council services | https://www.wyndham.vic.gov.au/services | Overview",
    ]
    return html, citations

# Councils you want in the dropdown (add more later)
COUNCILS = ["Wyndham City Council", "Yarra City Council", "City of Melbourne"]

st.title("📬 CivReply Drafts")
st.caption("Cited email drafts for council inboxes — auto-send only when it’s safe.")

# Renders the full Drafts UI (paste mode + Outlook mode if Graph is configured)
render_drafts_ui(get_answer_fn=my_retriever, councils=COUNCILS)

st.divider()
st.markdown(
    "Need Outlook integration? Set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, "
    "`GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX_ADDRESS` in your environment."
)
