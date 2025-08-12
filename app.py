# app.py — CivReply Drafts (Outlook-ready)
# Paste-mode works without credentials. Outlook mode needs Graph env vars.

from dotenv import load_dotenv
load_dotenv()  # enables local .env during dev

import html
import streamlit as st
from drafts_module import render_drafts_ui

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")

# ---- prettier stub retriever (replace with your FAISS/LangChain retriever later) ----
def my_retriever(email_text: str, council_name: str):
    # Safely escape inbound email before injecting into HTML
    snippet = html.escape((email_text or "").strip()[:1200]).replace("\n", "<br>")

    email_html = f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
       style="font-family: Arial, 'Segoe UI', sans-serif; color:#0f172a; line-height:1.55;">
  <tr>
    <td style="padding:20px; border:1px solid #e5e7eb; border-radius:12px;">
      <div style="font-size:18px; font-weight:700;">{html.escape(council_name)}</div>
      <div style="font-size:12px; color:#64748b; margin-top:2px;">Auto-drafted reply — please review before sending</div>

      <hr style="border:none; border-top:1px solid #e5e7eb; margin:12px 0 16px 0;">

      <p style="margin:0 0 10px 0;">Thanks for contacting {html.escape(council_name)}.</p>
      <p style="margin:0 0 14px 0;">We received your enquiry and prepared a draft reply based on your message:</p>

      <blockquote style="margin:0; padding:12px 14px; background:#f8fafc; border-left:3px solid #0ea5e9; border-radius:6px;">
        {snippet}
      </blockquote>

      <p style="margin:16px 0 6px 0;">Here’s the information you may need:</p>
      <ul style="margin:0 0 14px 18px; padding:0;">
        <li>Collection days vary by address — check your address on the waste calendar.</li>
        <li>Hard rubbish can be booked online; limits and fees may apply.</li>
      </ul>

      <p style="margin:0 0 8px 0;">Helpful links:</p>
      <ul style="margin:0 0 16px 18px; padding:0;">
        <li><a href="https://www.wyndham.vic.gov.au/services" style="color:#0ea5e9; text-decoration:none;">Council services</a> — overview</li>
      </ul>

      <p style="margin:0;">Kind regards,<br>Customer Service Team</p>
    </td>
  </tr>
</table>
"""
    citations = ["Council services | https://www.wyndham.vic.gov.au/services | Overview"]
    return email_html, citations

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
