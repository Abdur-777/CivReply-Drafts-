# retriever_catalog.py — fast link-first answers using catalog.json (expanded topics)
import json, html, re

CATALOG_PATH = "catalog.json"

# Order matters (first matches win more often)
TOPIC_RULES = [
    ("hard_rubbish", [r"hard\s*(waste|rubbish)", r"\bbulky\b", r"mattress"]),
    ("green_waste",  [r"green\s*bin", r"garden\s*waste", r"\bFOGO\b", r"organics"]),
    ("missed_bin",   [r"miss(ed|ing)\s*bin", r"bin\s*not\s*collected", r"missed\s*collection"]),
    ("bin_repair",   [r"(broken|damaged|stolen)\s*bin", r"replace\s*bin", r"new\s*bin"]),
    ("waste_calendar",[r"bin\s*(day|calendar|schedule)", r"what\s*day\s*is\s*(my|the)\s*bin"]),
    ("recycling_az", [r"\b(a-?z|what\s*goes\s*in)\b", r"recycl(e|ing)\s*guide"]),
    ("hazardous_waste",[r"hazard(ous)?", r"chemicals?", r"paint", r"battery", r"e-?waste", r"asbestos"]),
    ("transfer_station",[r"\btip\b", r"transfer\s*station", r"landfill", r"resource\s*recovery"]),

    ("parking_permits",[r"parking\s*permit", r"resident\s*permit", r"visitor\s*permit"]),
    ("parking_fines", [r"parking\s*(fine|infringement)", r"pay\s*fine", r"(appeal|review)\s*(fine|infringement)", r"\bnominat(e|ion)\b"]),
    ("report_issue",  [r"\breport\b", r"request\s*(fix|service)", r"pothole", r"footpath", r"street\s*light", r"graffiti"]),
    ("noise_complaints",[r"\bnoise\b", r"loud\s*music", r"party", r"construction\s*noise"]),

    ("rates_hardship",[r"(rates?)\s*(hardship|assistance|payment\s*plan)"]),
    ("rates",         [r"\brates?\b", r"valuation", r"instal(l)?ment", r"concession", r"pay\s*rates?"]),

    ("pet_registration",[r"pet\s*registration", r"register\s*(dog|cat)", r"microchip"]),

    ("planning_permits",[r"planning\s*permit", r"plan\s*permit", r"advertis(ing|ed)"]),
    ("building_permits",[r"building\s*permit", r"construction", r"demolition", r"surveyor"]),
    ("local_laws",    [r"local\s*law", r"footpath\s*(trading|dining)", r"amplified\s*sound", r"permit\s*to\s*consume"]),

    ("libraries",     [r"\blibrar(y|ies)\b", r"library\s*hours", r"borrow", r"membership"]),
    ("venue_hire",    [r"venue\s*hire", r"hall\s*hire", r"community\s*centre", r"book\s*a\s*venue"]),
    ("sports_bookings",[r"sports?(ground| oval| pavilion)", r"book\s*(ground|oval|court)", r"seasonal\s*allocation"]),
    ("childcare_kindergarten",[r"\bkind(er|ergarten)\b", r"child\s*care", r"early\s*years", r"enrol(l)?"]),
    ("maternal_child_health",[r"maternal\s*(and\s*)?child\s*health", r"\bMCH\b", r"nurse"]),
    ("immunisation",  [r"\bimmuni[sz]ation\b", r"vaccin(e|ation)", r"clinic", r"session"]),
    ("leisure_centres_pools",[r"pool", r"aquatic", r"leisure\s*centre", r"recreation\s*centre"]),

    ("fire_permits",  [r"burn\s*off", r"fire\s*permit", r"total\s*fire\s*ban"]),
    ("storm_flood_sandbags",[r"storm", r"flood", r"sandbag", r"emergency", r"\bSES\b"]),
    ("foi",           [r"freedom\s*of\s*information", r"\bFOI\b"]),
    ("privacy",       [r"\bprivacy\b", r"personal\s*information"]),

    ("waste",         [r"\b(bin|waste|recycling|collection)\b"]),
    ("contact",       [r"\bcontact\b", r"phone", r"email", r"customer\s*service"]),
]

def _classify(text: str) -> list[str]:
    t = text.lower()
    hits = []
    for topic, patterns in TOPIC_RULES:
        if any(re.search(p, t) for p in patterns):
            hits.append(topic)
    if not hits:
        hits = ["contact"]
    # keep the first 4 topics max to avoid link overload
    dedup = []
    for h in hits:
        if h not in dedup:
            dedup.append(h)
        if len(dedup) >= 4:
            break
    return dedup

def _load():
    with open(CATALOG_PATH, "r") as f:
        return json.load(f)

def answer(email_text: str, council_name: str):
    cat = _load()
    council = cat.get(council_name)
    if not council:
        body = f"<p>Thanks for contacting {html.escape(council_name)}.</p><p>We’ll escalate this to an officer and follow up shortly.</p>"
        return body, []

    topics = council.get("topics", {})
    want = _classify(email_text)
    links = []
    for t in want:
        info = topics.get(t)
        if info:
            links.append(info)
    if not links and topics.get("contact"):
        links.append(topics["contact"])

    snippet = html.escape((email_text or "").strip()[:900]).replace("\n","<br>")
    lis = "".join(f"<li><a href='{html.escape(x['url'])}'>{html.escape(x['title'])}</a></li>" for x in links[:6])

    body = f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
       style="font-family: Arial, 'Segoe UI', sans-serif; color:#0f172a; line-height:1.55;">
  <tr>
    <td style="padding:20px; border:1px solid #e5e7eb; border-radius:12px;">
      <div style="font-size:18px; font-weight:700;">{html.escape(council_name)}</div>
      <div style="font-size:12px; color:#64748b; margin-top:2px;">Auto-drafted reply — please review before sending</div>
      <hr style="border:none; border-top:1px solid #e5e7eb; margin:12px 0 16px 0;">
      <p style="margin:0 0 10px 0;">Thanks for your message. Based on your enquiry, these resources should help:</p>
      <ul style="margin:0 0 14px 18px; padding:0;">{lis or "<li>We’ll escalate this to the right team.</li>"}</ul>
      <p style="margin:0 0 10px 0;">Your message:</p>
      <blockquote style="margin:0; padding:12px 14px; background:#f8fafc; border-left:3px solid #0ea5e9; border-radius:6px;">{snippet}</blockquote>
      <p style="margin:16px 0 0 0;">Kind regards,<br>Customer Service Team</p>
    </td>
  </tr>
</table>
"""
    citations = [f"{x['title']} | {x['url']} | Council page" for x in links[:6]]
    return body, citations
