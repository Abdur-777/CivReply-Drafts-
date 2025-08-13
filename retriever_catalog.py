# retriever_catalog.py — improved w/ catalog.json support
# A lightweight, production-ready answerer used by worker_autoreply.py
# API: answer(query: str, topic: Optional[str], council: str="wyndham", format: str="email") -> dict
#
# Behavior:
# - Attempts to load a FAISS index at index/{council}/ and retrieve supporting snippets.
# - If OPENAI_API_KEY + langchain libs are available, it will fuse snippets into a short HTML response.
# - If not, it falls back to high-quality templates per topic with official link registry.
# - NEW: If catalog.json is present, prepend per-topic official links (e.g., Find My Bin Day) for the given council.
# - Always returns: {"answer_html": "<p>...</p>", "links": [{"title": "...", "url": "..."}, ...]}
#
# Safe to run without any extra deps: templates will still work.

from __future__ import annotations
import os, re, html, json, traceback, functools
from typing import Dict, List, Tuple, Optional

# --------------------------
# Optional dependencies
# --------------------------
_LANGCHAIN_OK = True
try:
    # langchain v0.1+ split across packages
    from langchain_community.vectorstores import FAISS as FAISSStore
    from langchain_openai import OpenAIEmbeddings, ChatOpenAI
    _ = OpenAIEmbeddings  # silence linters
except Exception:
    _LANGCHAIN_OK = False

# --------------------------
# Config / constants
# --------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY") or os.environ.get("OPENAI_KEY")
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # inexpensive, fast; change if you like

# Where your FAISS index lives (created by your ingest tool)
INDEX_ROOT = os.environ.get("FAISS_INDEX_ROOT", os.environ.get("INDEX_ROOT", "index"))
CATALOG_PATH = os.environ.get("CATALOG_PATH", "catalog.json")

# --------------------------
# Catalog support (optional)
# --------------------------
@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict:
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# Map generic topics -> catalog topic keys (tuned for Wyndham schema; extend per needs)
CATALOG_TOPIC_MAP: Dict[str, List[str]] = {
    "waste": [
        "find_bin_day","waste_overview","household_bins","waste_guide",
        "hard_waste","book_hard_waste","fogo","bin_requests",
        "recycling_az","transfer_station","hazardous_waste","waste_calendar_pdf"
    ],
    "rates": ["rates_home","rates_pay","rates_hardship","rates_payment_plan"],
    "libraries": ["libraries"],
    "animals": ["pet_registration","animal_permits","barking_dog"],
    "parking": ["parking_permits_regs","disability_parking","parking_fines_pay","parking_fine_review"],
    "planning": ["planning_permits","building_permits","local_laws"],
    "general info": ["contact"],
}

# Try several possible council name keys in catalog
_DEF_NAME_MAP = {
    "wyndham": "Wyndham City Council",
}

def _catalog_links_for(council_slug_or_name: str, topic: str) -> List[Dict[str, str]]:
    cat = _load_catalog()
    topic = (topic or "general info").lower()
    # Prefer exact display name if present; else try a default mapping from slug
    council_obj = cat.get(council_slug_or_name) or cat.get(_DEF_NAME_MAP.get(council_slug_or_name.lower(), ""))
    if not council_obj:
        # last resort: try title-cased key
        council_obj = cat.get(council_slug_or_name.title())
    if not council_obj:
        return []
    keys = CATALOG_TOPIC_MAP.get(topic, [])
    topics_section = council_obj.get("topics", {})
    out: List[Dict[str, str]] = []
    seen = set()
    for k in keys:
        ent = topics_section.get(k)
        if not ent:
            continue
        url = ent.get("url")
        title = ent.get("title") or url
        if url and url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out

# --------------------------
# Curated defaults (used when no catalog or to supplement it)
# --------------------------
COUNCIL_LINKS: Dict[str, Dict[str, List[Dict[str, str]]]] = {
    "wyndham": {
        "waste": [
            {"title": "Wyndham – Waste & Recycling", "url": "https://www.wyndham.vic.gov.au/services/waste-recycling"},
            {"title": "Wyndham – Hard rubbish bookings", "url": "https://www.wyndham.vic.gov.au/services/waste-recycling"},
        ],
        "rates": [
            {"title": "Wyndham – Rates & valuations", "url": "https://www.wyndham.vic.gov.au"},
            {"title": "Pay your rates online", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "libraries": [
            {"title": "Wyndham – Libraries & hours", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "animals": [
            {"title": "Wyndham – Animal registration", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "opening hours": [
            {"title": "Wyndham – Opening hours & facilities", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "parking": [
            {"title": "Wyndham – Parking & infringements", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "planning": [
            {"title": "Wyndham – Planning permits", "url": "https://www.wyndham.vic.gov.au"},
        ],
        "general info": [
            {"title": "Wyndham – Services", "url": "https://www.wyndham.vic.gov.au/services"},
            {"title": "Contact Wyndham City", "url": "https://www.wyndham.vic.gov.au/contact-us"},
        ],
    }
}

TOPIC_TEMPLATES: Dict[str, str] = {
    "waste": (
        """
<p>Here’s how to confirm your bin collection day and what to do if a collection is missed.</p>
<ol>
  <li><strong>Check collection day:</strong> Use the council’s online tool to confirm your general waste and recycling day by address/suburb.</li>
  <li><strong>Set-out rules:</strong> Put bins out by 6am on collection day, lid closed, wheels to the kerb, with 0.5m clearance around each bin.</li>
  <li><strong>Missed collection:</strong> Lodge a “missed service” request via the waste portal and keep your bin out until collected.</li>
  <li><strong>Hard rubbish:</strong> Book hard rubbish separately via the booking page (limits and eligible items apply).</li>
</ol>
""".strip()
    ),
    "rates": (
        """
<p>Here are your options to pay rates and where to find due dates.</p>
<ul>
  <li><strong>Payment methods:</strong> Online via the rates portal, BPAY, by phone, or in person (see “How to pay” page).</li>
  <li><strong>Due dates:</strong> Check your latest rates notice or the “Rates & valuations” page for instalment schedules.</li>
  <li><strong>Assistance:</strong> If experiencing hardship, review payment arrangement options and contact the rates team.</li>
</ul>
""".strip()
    ),
    "libraries": (
        """
<p>Library hours and services.</p>
<ul>
  <li><strong>Hours today:</strong> View the “Libraries” page for each branch’s opening hours (public holiday hours may differ).</li>
  <li><strong>Services:</strong> Borrow/return, PC access, printing, programs, and events—check each branch page.</li>
</ul>
""".strip()
    ),
    "animals": (
        """
<p>Registering your pet.</p>
<ul>
  <li><strong>Who must register:</strong> Cats and dogs over the minimum age/requirements set by council.</li>
  <li><strong>What you need:</strong> Microchip number, desexing status, and owner details.</li>
  <li><strong>How to apply:</strong> Complete the online registration form; fees vary by animal and concessions.</li>
</ul>
""".strip()
    ),
    "opening hours": (
        """
<p>Opening hours for council facilities vary by site.</p>
<ul>
  <li>Use the “Facilities & opening hours” page to view today’s hours for your location.</li>
  <li>Public holiday hours and special events may change operating times—check the notice on each facility page.</li>
</ul>
""".strip()
    ),
    "parking": (
        """
<p>Parking fines and reviews.</p>
<ul>
  <li><strong>Pay or review:</strong> Use the online infringements portal to pay or request an internal review.</li>
  <li><strong>Evidence:</strong> Provide photos, permits, or receipts that support your circumstances.</li>
  <li><strong>Timeframes:</strong> Reviews must be lodged within the timeframe stated on your notice.</li>
</ul>
""".strip()
    ),
    "planning": (
        """
<p>Planning vs building permits.</p>
<ul>
  <li><strong>Planning permit:</strong> Required for land use/development (e.g., new use, signage, overlays). Check the planning scheme.</li>
  <li><strong>Building permit:</strong> Required for construction safety/compliance (issued by a registered building surveyor).</li>
  <li><strong>Next steps:</strong> Start with “Do I need a planning permit?” then lodge via the council’s portal.</li>
</ul>
""".strip()
    ),
    "general info": (
        """
<p>Thanks for your email. Here are quick ways to find the right info:</p>
<ul>
  <li>Browse “Services” for waste, pets, permits, rates, and more.</li>
  <li>Use the website search to jump to specific documents or forms.</li>
  <li>If this didn’t resolve your question, reply with your address/suburb and any relevant details.</li>
</ul>
""".strip()
    ),
}

# --------------------------
# Utilities
# --------------------------
_SUBURB_POSTCODE_RE = re.compile(r"([A-Za-z][A-Za-z\s\-']+)\s*\(?([0-9]{4})\)?", flags=re.IGNORECASE)

def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def _detect_suburb_and_postcode(text: str) -> Tuple[Optional[str], Optional[str]]:
    m = _SUBURB_POSTCODE_RE.search(text or "")
    if m:
        suburb = " ".join(m.group(1).split()).strip()
        pc = m.group(2)
        if len(suburb) >= 3 and not suburb.isdigit():
            return suburb, pc
    return None, None

def _pick_topic_heuristic(text: str) -> str:
    t = (text or "").lower()
    def any_kw(*words):
        return any(w in t for w in words)
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

# Merge links from catalog + curated; de-dup by URL (catalog first, then curated)

def _council_links(council: str, topic: str) -> List[Dict[str, str]]:
    council = (council or "").strip() or "wyndham"
    topic = (topic or "general info").lower()
    out: List[Dict[str, str]] = []
    seen = set()

    # Catalog links first (if any)
    for l in _catalog_links_for(council, topic):
        url = l.get("url")
        if url and url not in seen:
            seen.add(url); out.append(l)

    # Curated defaults (by slug)
    bank = COUNCIL_LINKS.get(council.lower(), {})
    for l in bank.get(topic, []) + bank.get("general info", []):
        url = l.get("url")
        if url and url not in seen:
            seen.add(url); out.append(l)

    return out[:8]

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
SYSTEM_EMAIL = (
    "You are a concise council information assistant.\n"
    "You produce short, helpful HTML suitable for an email reply.\n"
    "Use bullet points or a short ordered list, and avoid fluff.\n"
    "If collection days depend on an address, say to use the official lookup tool.\n"
    "Never fabricate URLs; if unsure, keep links generic to the council's waste/rates/libraries pages."
)


def _llm_summarize(query: str, topic: str, suburb: Optional[str], snippets: List[Dict[str, str]]) -> Optional[str]:
    if not (_LANGCHAIN_OK and OPENAI_API_KEY):
        return None
    try:
        llm = ChatOpenAI(model=MODEL_NAME, temperature=0, api_key=OPENAI_API_KEY)
        context = "\n\n".join(f"[{i+1}] {s.get('text','')[:1200]}" for i, s in enumerate(snippets[:6]))
        suburb_line = f"User suburb context: {suburb}." if suburb else "User suburb context: (not provided)."
        msg = [
            {"role": "system", "content": SYSTEM_EMAIL},
            {"role": "user", "content": (
                f"Topic: {topic}\n{suburb_line}\n\nUser email:\n{query}\n\n"
                f"Context (snippets):\n{context}\n\nWrite a short HTML reply (2–6 bullets)."
            )},
        ]
        res = llm.invoke(msg)
        content = getattr(res, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        pass
    return None

# --------------------------
# HTML builders
# --------------------------

def _wrap_email_html(user_text: str, body_html: str, links: List[Dict[str, str]]) -> str:
    links_html = ""
    if links:
        items = "".join(
            f'<li><a href="{html.escape(l["url"], quote=True)}">{html.escape(l["title"])}</a></li>'
            for l in links[:8]
        )
        links_html = f"<p><strong>Official links:</strong></p><ul>{items}</ul>"

    return (
        f"""
<div style="font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5">
  {body_html}
  {links_html}
  <hr>
  <p style="color:#777;margin-top:14px">Original question:</p>
  <blockquote style="margin:0 0 0 1em;color:#555;border-left:3px solid #ddd;padding-left:.8em">{html.escape(_strip_html(user_text))}</blockquote>
</div>
""".strip()
    )


def _topic_intro(topic: str, suburb: Optional[str]) -> str:
    if topic == "waste" and suburb:
        return f"<p>For <strong>{html.escape(suburb)}</strong>, here’s how to confirm your collection day and what to do next.</p>"
    return ""

# Small CTA that points explicitly to catalog’s bin-day tool if available

def _bin_day_cta(council: str) -> str:
    links = _catalog_links_for(council, "waste")
    for l in links:
        if "bin" in l.get("title", "").lower() and "day" in l.get("title", "").lower():
            return (
                f"<p><strong>Quick tip:</strong> Use <a href=\"{html.escape(l['url'])}\">{html.escape(l['title'])}</a> "
                f"to check your collection day by address.</p>"
            )
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
        suburb, _pc = _detect_suburb_and_postcode(query)

        # Retrieval + RAG links
        snippets = _retrieve_snippets(query, council)
        rag_links = _links_from_snippets(snippets)

        # LLM wording if available
        llm_html = _llm_summarize(query=query, topic=topic_final, suburb=suburb, snippets=snippets)

        # Fallback body from templates (+ optional bin-day CTA)
        template_html = TOPIC_TEMPLATES.get(topic_final, TOPIC_TEMPLATES["general info"])
        intro = _topic_intro(topic_final, suburb)
        cta = _bin_day_cta(council) if topic_final == "waste" else ""
        body_html = llm_html or (intro + cta + template_html)

        # Merge links: catalog → curated → RAG
        merged: List[Dict[str, str]] = []
        seen = set()
        for source in (
            _catalog_links_for(council, topic_final),
            COUNCIL_LINKS.get(council.lower(), {}).get(topic_final, []) + COUNCIL_LINKS.get(council.lower(), {}).get("general info", []),
            rag_links,
        ):
            for l in source:
                url = l.get("url")
                if url and url not in seen:
                    seen.add(url); merged.append(l)
        if not merged:
            merged = _catalog_links_for(council, "general info") or COUNCIL_LINKS.get(council.lower(), {}).get("general info", [])

        html_email = _wrap_email_html(user_text=query, body_html=body_html, links=merged)

        return {
            "answer_html": html_email if format == "email" else body_html,
            "links": merged,
        }

    except Exception:
        traceback.print_exc()
        fallback_links = _council_links(council, "general info")
        return {
            "answer_html": (
                """
<p>Thanks for your email. Here’s information relevant to your question:</p>
<ul>
  <li>Browse the council’s Services page for waste, pets, permits, rates, and more.</li>
  <li>If you are asking about collection days or opening hours, please include your suburb (and postcode) for faster help.</li>
</ul>
""".strip()
            ),
            "links": fallback_links or [{"title": "Council Services", "url": "https://www.wyndham.vic.gov.au/services"}],
        }


# --------------------------
# Optional: simple CLI test
# --------------------------
if __name__ == "__main__":
    test_q = (
        "Hi team,\n"
        "I'm a Wyndham resident. What day is general waste and recycling collected for Hoppers Crossing (3029)?\n"
        "Please include the official Wyndham links and what to do if a collection is missed.\n"
        "Thanks!"
    )
    out = answer(test_q, topic=None, council="wyndham", format="email")
    print("---- HTML ----")
    print(out["answer_html"][:1000], "...\n")
    print("---- LINKS ----")
    for l in out["links"]:
        print("-", l["title"], "=>", l["url"])
