# app.py — CivReply Drafts (cited answers + Outlook-ready)
# - Paste-mode works without credentials.
# - Outlook mode needs GRAPH_* env vars.
# - Cited answers need OPENAI_API_KEY and indexes built by ingest.py.

from dotenv import load_dotenv
load_dotenv()  # enables local .env during dev

import os
import json
import streamlit as st

from drafts_module import render_drafts_ui

# Try to use the real retriever (OpenAI + FAISS). Fallback to a simple stub if unavailable.
try:
    from retriever import AnswerService
    _svc = AnswerService()
    def my_retriever(email_text: str, council_name: str):
        try:
            return _svc.answer(email_text, council_name)
        except Exception as e:
            # Graceful fallback if index not built yet for this council
            return (
                f"<p>Thanks for contacting {council_name}.</p>"
                f"<p>We’re preparing an answer with the correct links. "
                f"This enquiry will be escalated to an officer.</p>",
                []
            )
except Exception:
    def my_retriever(email_text: str, council_name: str):
        return (
            f"<p>Thanks for contacting {council_name}.</p>"
            f"<p>We received your enquiry and will get back to you with more detail soon.</p>",
            []
        )

# Councils for the dropdown — load from councils.json if present, else fallback to common VIC councils.
def load_councils():
    try:
        with open("councils.json", "r") as f:
            data = json.load(f)
            # keep JSON order; keys are council names
            return list(data.keys())
    except Exception:
        return [
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
            "Wyndham City Council",
            "Hobsons Bay City Council",
            "Maribyrnong City Council",
            "Brimbank City Council",
            "Melton City Council",
            "Hume City Council",
            "City of Whittlesea",
            "Darebin City Council"
        ]

COUNCILS = load_councils()

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")
st.title("📬 CivReply Drafts")
st.caption("Cited email drafts for council inboxes — auto-send only when it’s safe.")

# Tiny status hints so you know what’s configured
with st.sidebar:
    st.subheader("Status")
    outlook_ok = all(os.getenv(k) for k in ("GRAPH_TENANT_ID","GRAPH_CLIENT_ID","GRAPH_CLIENT_SECRET","GRAPH_MAILBOX_ADDRESS"))
    st.write("Outlook (Graph):", "✅ Ready" if outlook_ok else "⚠️ Not configured")
    st.write("OpenAI (citations):", "✅ Key found" if os.getenv("OPENAI_API_KEY") else "⚠️ OPENAI_API_KEY missing")

# Main UI (paste mode + Outlook mode if Graph configured)
render_drafts_ui(get_answer_fn=my_retriever, councils=COUNCILS)

st.divider()
st.markdown(
    "To enable Outlook integration, set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, "
    "`GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX_ADDRESS` in your environment. "
    "For cited answers, set `OPENAI_API_KEY` and run `python ingest.py` to build indexes."
)
