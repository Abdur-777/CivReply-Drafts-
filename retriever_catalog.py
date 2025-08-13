# retriever_catalog.py
# A lightweight, production-ready answerer used by worker_autoreply.py
# API: answer(query: str, topic: Optional[str], council: str="wyndham", format: str="email") -> dict
#
# Behavior:
# - Attempts to load a FAISS index at index/{council}/ and retrieve supporting snippets.
# - If OPENAI_API_KEY + langchain libs are available, it will fuse snippets into a short HTML response.
# - If not, it falls back to high-quality templates per topic with official link registry.
# - Always returns: {"answer_html": "<p>...</p>", "links": [{"title": "...", "url": "..."}, ...]}
#
# Safe to run without any extra deps: templates will still work.

from __future__ import annotations
import os, re, html, json, traceback
from typing import Dict, List, Tuple, Optional

# --------------------------
# Optional dependencies
# --------------------------
_LANGCHAIN_OK = True
_OPENAI_OK = True
try:
    # langchain v0.1+ split across packages
    from langchain_community.vectorstores import FAISS as FAISSStore
    from langchain_openai import OpenAIEmbeddings, ChatOpenAI
    _ = OpenAIEmbeddings  # to silence linters
except Exception:
    _LANGCHAIN_OK = False

try:
    # We only need this if we decide to call the model directly (for nicer wording)
    # Using langchain_openai's ChatOpenAI if available; else try bare openai package.
    import openai  # type: ignore
except Exception:
    _OPENAI_OK = False

# --------------------------
# Config / constants
# --------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY") or os.environ.get("OPENAI_KEY")
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # inexpensive, fast; change if you like

# Where your FAISS index lives (created by your Streamlit admin tool)
INDEX_ROOT = os.environ.get("FAISS_INDEX_ROOT", "index")

# Registry of official links per council/topic. Keep it generic and safe; update as you curate.
COUNCIL_LINKS: Dict[str, Dict[str, List[Dict[str, str]]]] = {
    "wyndham": {
        "waste": [
            {"title": "Wyndham – Waste & Recycling", "url": "https://www.wyndham.vic.gov.au/services/waste-recycling"},
            {"title": "Wyndham – Hard rubbish bookings", "url": "https://www.wyndham.vic.gov.au/services/waste-recycling"},
            {"title": "Report a missed bin (Wyndham portal)", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "rates": [
            {"title": "Wyndham – Rates & valuations", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Pay your rates online", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "libraries": [
            {"title": "Wyndham – Libraries & hours", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Wyndham – Library locations", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "animals": [
            {"title": "Wyndham – Animal registration", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Wyndham – Pets & animals", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "opening hours": [
            {"title": "Wyndham – Opening hours & facilities", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "parking": [
            {"title": "Wyndham – Parking & infringements", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Request a review (parking)", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "planning": [
            {"title": "Wyndham – Planning permits", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Wyndham – Building permits", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "general info": [
            {"title": "Wyndham – Services", "url": "https://www.wyndham.vic.gov.au/services"},
            {"title": "Contact Wyndham City", "url": "https://www.wyndham.vic.gov.au"},
        ],
    }
}

# Short copy used when FAISS/model aren't available
TOPIC_TEMPLATES: Dict[str, str] = {
    "waste": """
<p>Here’s how to confirm your bin collection day and what to do if a collection is missed.</p>
<ol>
  <li><strong>Check collection day:</strong> Use the council’s online tool to confirm your general waste and recycling day by address/suburb.</li>
  <li><strong>Set-out rules:</strong> Put bins out by 6am on collection day, lid closed, wheels to the kerb, with 0.5m clearance around each bin.</li>
  <li><strong>Missed collection:</strong> Lodge a “missed service” request via the waste portal and keep your bin out until collected.</li>
  <li><strong>Hard rubbish:</strong> Book hard rubbish separately via the booking page (limits and eligible items apply).</li>
</ol>
""".strip(),
    "rates": """
<p>Here are your options to pay rates and where to find due dates.</p>
<ul>
  <li><strong>Payment methods:</strong> Online via the rates portal, BPAY, by phone, or in person (see “How to pay” page).</li>
  <li><strong>Due dates:</strong> Check your latest rates notice or the “Rates & valuations” page for instalment schedules.</li>
  <li><strong>Assistance:</strong> If experiencing hardship, review payment arrangement options and contact the rates team.</li>
</ul>
""".strip(),
    "libraries": """
<p>Library hours and services.</p>
<ul>
  <li><strong>Hours today:</strong> View the “Libraries” page for each branch’s opening hours (public holiday hours may differ).</li>
  <li><strong>Services:</strong> Borrow/return, PC access, printing, programs, and events—check each branch page.</li>
</ul>
""".strip(),
    "animals": """
<p>Registering your pet.</p>
<ul>
  <li><strong>Who must register:</strong> Cats and dogs over the minimum age/requirements set by council.</li>
  <li><strong>What you need:</strong> Microchip number, desexing status, and owner details.</li>
  <li><strong>How to apply:</strong> Complete the online registration form; fees vary by animal and concessions.</li>
</ul>
""".strip(),
    "opening hours": """
<p>Opening hours for council facilities vary by site.</p>
<ul>
  <li>Use the “Facilities & opening hours” page to view today’s hours for your location.</li>
  <li>Public holiday hours and special events may change operating times—check the notice on each facility page.</li>
</ul>
""".strip(),
    "parking": """
<p>Parking fines and reviews.</p>
<ul>
  <li><strong>Pay or review:</strong> Use the online infringements portal to pay or request an internal review.</li>
  <li><strong>Evidence:</strong> Provide photos, permits, or receipts that support your circumstances.</li>
  <li><strong>Timeframes:</strong> Reviews must be lodged within the timeframe stated on your notice.</li>
</ul>
""".strip(),
    "planning": """
<p>Planning vs building permits.</p>
<ul>
  <li><strong>Planning permit:</strong> Required for land use/development (e.g., new use, signage, overlays). Check the planning scheme.</li>
  <li><strong>Building permit:</strong> Required for construction safety/compliance (issued by a registered building surveyor).</li>
  <li><strong>Next steps:</strong> Start with “Do I need a planning permit?” then lodge via the council’s portal.</li>
</ul>
""".strip(),
    "general info": """
<p>Thanks for your email. Here are quick ways to find the right info:</p>
<ul>
  <li>Browse “Services” for waste, pets, permits, rates, and more.</li>
  <li>Use the website search to jump to specific documents or forms.</li>
  <li>If this didn’t resolve your question, reply with your address/suburb and any relevant details.</li>
</ul>
""".strip(),
}

# --------------------------
# Utilities
# --------------------------
def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

_SUBURB_POSTCODE_RE = re.compile(
    r"([A-Za-z][A-Za-z\s\-']+)\s*\(?(\d{4})\)?", flags=re.IGNORECASE
)

def _detect_suburb_and_postcode(text: str) -> Tuple[Optional[str], Optional[str]]:
    m = _SUBURB_POSTCODE_RE.search(text or "")
    if m:
        suburb = " ".join(m.group(1).split()).strip()
        pc = m.group(2)
        # Avoid false positives like "hours (2024)" by requiring suburb-like words
        if len(suburb) >= 3 and not suburb.isdigit():
            return suburb, pc
    return None, None

def _pick_topic_heuristic(text: str) -> str:
    t = (text or "").lower()
    def any_kw(*words): return any(w in t for w in words)

    if any_kw("bin", "bins", "waste", "rubbish", "recycling", "hard rubbish", "collection"):
        return "waste"
    if any_kw("rate", "rates", "valuation", "instalment", "bpay"):
        return "rates"
    if any_kw("library", "libraries", "tarneit library", "werribee library", "point cook library", "hours"):
        return "libraries"
    if any_kw("dog", "cat", "pet", "animal", "microchip", "registration"):
        return "animals"
    if any_kw("opening hours", "open today", "close", "closing time"):
        return "opening hours"
    if any_kw("parking", "infringement", "fine", "ticket"):
        return "parking"
    if any_kw("planning permit", "building permit", "planning", "overlays", "construction"):
        return "planning"
    return "general info"

def _council_links(council: str, topic: str) -> List[Dict[str, str]]:
    council = (council or "").lower().strip() or "wyndham"
    topic = (topic or "general info").lower()
    bank = COUNCIL_LINKS.get(council, {})
    links = bank.get(topic, []) + bank.get("general info", [])
    # Deduplicate by URL
    seen = set()
    uniq = []
    for l in links:
        url = l.get("url")
        if url and url not in seen:
            seen.add(url)
            uniq.append(l)
    return uniq[:8]

# --------------------------
# Retrieval layer (optional)
# --------------------------
def _load_retriever(council: str):
    """Try to build a FAISS retriever. Returns (retriever, embeddings) or (None, None) on failure."""
    if not (_LANGCHAIN_OK and OPENAI_API_KEY):
        return None, None
    try:
        idx_dir = os.path.join(INDEX_ROOT, council.lower())
        index_path = os.path.join(idx_dir, "index.faiss")
        if not os.path.exists(index_path):
            return None, None
        emb = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        store = FAISSStore.load_local(idx_dir, emb, allow_dangerous_deserialization=True)
        return store.as_retriever(search_kwargs={"k": 6}), emb
    except Exception:
        return None, None

def _retrieve_snippets(query: str, council: str) -> List[Dict[str, str]]:
    """Return a list of snippets: {"text": ..., "source": ..., "title": ...}"""
    ret, _ = _load_retriever(council)
    if not ret:
        return []
    try:
        docs = ret.get_relevant_documents(query)
        out = []
        for d in docs:
            meta = getattr(d, "metadata", {}) or {}
            out.append({
                "text": d.page_content or "",
                "source": meta.get("source") or meta.get("url") or "",
                "title": meta.get("title") or "",
            })
        return out
    except Exception:
        return []

def _links_from_snippets(snips: List[Dict[str, str]]) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    seen = set()
    for s in snips:
        url = (s.get("source") or "").strip()
        if url and url not in seen and (url.startswith("http://") or url.startswith("https://")):
            seen.add(url)
            title = s.get("title") or "Source"
            links.append({"title": title, "url": url})
    return links

# --------------------------
# Optional LLM fusion (nice wording when available)
# --------------------------
SYSTEM_EMAIL = """You are a concise council information assistant.
You produce short, helpful HTML suitable for an email reply.
Use bullet points or a short ordered list, and avoid fluff.
If collection days depend on an address, say to use the official lookup tool.
Never fabricate URLs; if unsure, keep links generic to the council's waste/rates/libraries pages."""

def _llm_summarize(query: str, topic: str, suburb: Optional[str], snippets: List[Dict[str, str]]) -> Optional[str]:
    if not (_LANGCHAIN_OK and OPENAI_API_KEY):
        return None
    try:
        # Prefer ChatOpenAI via langchain_openai
        llm = ChatOpenAI(model=MODEL_NAME, temperature=0, api_key=OPENAI_API_KEY)
        context = "\n\n".join(
            f"[{i+1}] {s.get('text','')[:1200]}" for i, s in enumerate(snippets[:6])
        )
        suburb_line = f"User suburb context: {suburb}." if suburb else "User suburb context: (not provided)."
        msg = [
            {"role": "system", "content": SYSTEM_EMAIL},
            {"role": "user", "content": f"Topic: {topic}\n{suburb_line}\n\nUser email:\n{query}\n\nContext (snippets):\n{context}\n\nWrite a short HTML reply (2–6 bullets)."}
        ]
        res = llm.invoke(msg)
        content = getattr(res, "content", None)
        if content and isinstance(content, str):
            return content.strip()
    except Exception:
        pass
    # Fallback to plain template
    return None

# --------------------------
# HTML builders
# --------------------------
def _wrap_email_html(user_text: str, body_html: str, links: List[Dict[str, str]]) -> str:
    # Sanitize & assemble
    links_html = ""
    if links:
        items = "".join(
            f'<li><a href="{html.escape(l["url"], quote=True)}">{html.escape(l["title"])}</a></li>'
            for l in links[:8]
        )
        links_html = f"<p><strong>Official links:</strong></p><ul>{items}</ul>"

    return f"""
<div style="font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5">
  {body_html}
  {links_html}
  <hr>
  <p style="color:#777;margin-top:14px">Original question:</p>
  <blockquote style="margin:0 0 0 1em;color:#555;border-left:3px solid #ddd;padding-left:.8em">{html.escape(_strip_html(user_text))}</blockquote>
</div>
""".strip()

def _topic_intro(topic: str, suburb: Optional[str]) -> str:
    if topic == "waste" and suburb:
        return f"<p>For <strong>{html.escape(suburb)}</strong>, here’s how to confirm your collection day and what to do next.</p>"
    # Generic one-liners per topic could be added here
    return ""

# --------------------------
# Public API
# --------------------------
def answer(query: str, topic: Optional[str] = None, council: str = "wyndham", format: str = "email") -> Dict[str, object]:
    """
    Returns:
      {
        "answer_html": "<p>...</p>",
        "links": [{"title": "...", "url": "..."}, ...]
      }
    """
    try:
        topic_final = (topic or _pick_topic_heuristic(query)).lower()
        suburb, pc = _detect_suburb_and_postcode(query)

        # Try to retrieve supporting snippets + links
        snippets = _retrieve_snippets(query, council)
        rag_links = _links_from_snippets(snippets)

        # If LLM available, try to produce a concise HTML
        llm_html = _llm_summarize(query=query, topic=topic_final, suburb=suburb, snippets=snippets)

        # Fallback body from templates
        template_html = TOPIC_TEMPLATES.get(topic_final, TOPIC_TEMPLATES["general info"])
        intro = _topic_intro(topic_final, suburb)
        body_html = llm_html or (intro + template_html)

        # Merge curated links with any RAG sources (curated first)
        curated = _council_links(council, topic_final)
        # Keep curated order; append RAG links that are new
        seen = {l["url"] for l in curated}
        merged_links = list(curated)
        for l in rag_links:
            if l["url"] not in seen:
                seen.add(l["url"])
                merged_links.append(l)

        # Ensure at least something present
        if not merged_links:
            merged_links = _council_links(council, "general info")

        html_email = _wrap_email_html(user_text=query, body_html=body_html, links=merged_links)

        return {
            "answer_html": html_email if format == "email" else body_html,
            "links": merged_links,
            # Optional debug fields (not required by worker). Uncomment if you want them.
            # "topic": topic_final,
            # "suburb": suburb,
            # "snippets_used": len(snippets),
        }

    except Exception as e:
        # Absolute safety fallback
        traceback.print_exc()
        fallback_links = _council_links(council, "general info")
        return {
            "answer_html": """
<p>Thanks for your email. Here’s information relevant to your question:</p>
<ul>
  <li>Browse the council’s Services page for waste, pets, permits, rates, and more.</li>
  <li>If you are asking about collection days or opening hours, please include your suburb (and postcode) for faster help.</li>
</ul>
""".strip(),
            "links": fallback_links or [{"title": "Council Services", "url": "https://www.wyndham.vic.gov.au/services"}],
        }


# --------------------------
# Optional: simple CLI test
# --------------------------
if __name__ == "__main__":
    test_q = """Hi team,
I'm a Wyndham resident. What day is general waste and recycling collected for Hoppers Crossing (3029)?
Please include the official Wyndham links and what to do if a collection is missed.
Thanks!"""
    out = answer(test_q, topic=None, council="wyndham", format="email")
    print("---- HTML ----")
    print(out["answer_html"][:1000], "...\n")
    print("---- LINKS ----")
    for l in out["links"]:
        print("-", l["title"], "=>", l["url"])
