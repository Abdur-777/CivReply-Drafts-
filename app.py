# app.py — CivReply Drafts (catalog-based answers + Outlook-ready)
# - Paste mode works without credentials.
# - Outlook mode needs GRAPH_* env vars.
# - Answers come from catalog.json via retriever_catalog.py (no embeddings needed).

from dotenv import load_dotenv
load_dotenv()

import os
import json
import streamlit as st

from drafts_module import render_drafts_ui
from retriever_catalog import answer as catalog_answer


def load_councils():
    """Load council names for the dropdown from councils.json; fallback if missing."""
    try:
        with open("councils.json", "r") as f:
            data = json.load(f)
            # Preserve JSON order; keys are council names
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
            "Darebin City Council",
            "City of Greater Geelong",
        ]


# === Catalog-backed retriever ===
def my_retriever(email_text: str, council_name: str):
    return catalog_answer(email_text, council_name)


COUNCILS = load_councils()

st.set_page_config(page_title="CivReply Drafts", page_icon="📬", layout="wide")
st.title("📬 CivReply Drafts")
st.caption("Link-first, cited email drafts for Victorian councils — auto-send only when it’s safe.")


# Sidebar status
with st.sidebar:
    st.subheader("Status")
    outlook_ok = all(os.getenv(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_MAILBOX_ADDRESS"))
    st.write("Outlook (Graph):", "✅ Ready" if outlook_ok else "⚠️ Not configured")
    st.write("Catalog:", "✅ catalog.json found" if os.path.exists("catalog.json") else "⚠️ catalog.json missing")
    st.write("Councils loaded:", len(COUNCILS))


# Main UI
render_drafts_ui(get_answer_fn=my_retriever, councils=COUNCILS)

st.divider()
st.markdown(
    "To enable Outlook integration, set `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, "
    "`GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX_ADDRESS` in your environment. "
    "To power answers, run `python build_catalog.py` to generate `catalog.json`."
)
