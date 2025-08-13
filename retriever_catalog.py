# retriever_catalog.py — link-first answers using catalog.json + built-in Wyndham map
# Python 3.8+ compatible. No external APIs required.

import os
import json
import re
import html

CATALOG_PATH = "catalog.json"

# ---------------- Built-in catalog (Wyndham) ----------------
# Used as a fallback and/or to override missing topics in catalog.json
BUILTIN_CATALOG = {
    "Wyndham City Council": {
        "base": "https://www.wyndham.vic.gov.au",
        "topics": {
            # Waste & bins
            "waste_overview": {
                "title": "About Waste & Recycling",
                "url": "https://www.wyndham.vic.gov.au/services/about-waste-and-recycling"
            },
            "household_bins": {
                "title": "Household Bin Services",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/household-bins/household-bin-services"
            },
            "find_bin_day": {
                "title": "Find My Bin Collection Day",
                "url": "https://digital.wyndham.vic.gov.au/myWyndham/"
            },
            "waste_calendar_pdf": {
                "title": "Waste Collection Map & Calendar (PDF)",
                "url": "https://www.wyndham.vic.gov.au/sites/default/files/2024-05/waste%20calendar.pdf"
            },
            "waste_guide": {
                "title": "Waste & Recycling Guide 2025–26",
                "url": "https://www.wyndham.vic.gov.au/sites/default/files/2025-06/Waste%20and%20Recycling%20Guide%202025-2026.pdf"
            },
            "hard_waste": {
                "title": "Hard & Green Waste Collection Service",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/hard-and-green-waste-collection-service"
            },
            "book_hard_waste": {
                "title": "Book a Hard & Green Waste Collection",
                "url": "https://www.wyndham.vic.gov.au/book-hard-green-waste-collection"
            },
            "fogo": {
                "title": "Green-lid Bin (FOGO)",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/household-bins/green-lid-bin-food-organics-and-garden-organics-fogo"
            },
            "bin_requests": {
                "title": "Bin Requests (new, missing, damaged)",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/household-bins/household-bin-services#bin-requests"
            },
            "recycling_az": {
                "title": "A–Z Interactive Residential Waste Guide",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/a-z-interactive-residential-waste-guide"
            },
            "transfer_station": {
                "title": "Municipal Tip / RDF (fees, hours)",
                "url": "https://www.wyndham.vic.gov.au/venues/municipal-tiprdf-refuse-disposal-facility"
            },
            "hazardous_waste": {
                "title": "Other Waste & Recycling (hazardous, Detox Your Home)",
                "url": "https://www.wyndham.vic.gov.au/services/waste-recycling/other-waste-and-recycling-services-initiatives/other-waste-recycling"
            },

            # Rates
            "rates_home": {
                "title": "Rates & Valuations",
                "url": "https://www.wyndham.vic.gov.au/services/rates-valuations/rates-valuations"
            },
            "rates_pay": {
                "title": "Rates Payments",
                "url": "https://www.wyndham.vic.gov.au/payments/rates-payments"
            },
            "rates_hardship": {
                "title": "Difficulty Paying Rates",
                "url": "https://www.wyndham.vic.gov.au/difficulty-paying-rates"
            },
            "rates_payment_plan": {
                "title": "Rates Payment Plan (form)",
                "url": "https://www.wyndham.vic.gov.au/form/rates-payment-plan"
            },

            # Animals
            "pet_registration": {
                "title": "Pet Registration & Ownership",
                "url": "https://www.wyndham.vic.gov.au/services/pets-animals/animal-registration-regulations/pet-registration-and-ownership"
            },
            "animal_permits": {
                "title": "Animal Permits",
                "url": "https://www.wyndham.vic.gov.au/services/pets-animals/animal-registration-regulations/animal-permits"
            },
            "barking_dog": {
                "title": "Dogs & Cats (complaints, barking, attacks)",
                "url": "https://www.wyndham.vic.gov.au/services/pets-animals/animal-complaints-pests/dogs-and-cats"
            },

            # Parking & infringements
            "parking_permits_regs": {
                "title": "Parking Regulations & Permits",
                "url": "https://www.wyndham.vic.gov.au/services/roads-parking-transport/parking-regulations-fines/parking-regulations-permits"
            },
            "disability_parking": {
                "title": "Disability Parking Permits",
                "url": "https://www.wyndham.vic.gov.au/services/aged-disability/disability-parking-permits"
            },
            "parking_fines_pay": {
                "title": "Infringement (Parking Fine) Payments",
                "url": "https://www.wyndham.vic.gov.au/payments/infringement-payments"
            },
            "parking_fine_review": {
                "title": "Request a Fine Review or Payment Plan",
                "url": "https://www.wyndham.vic.gov.au/services/roads-parking-transport/parking-regulations-fines/request-review-fine-infringement-notice"
            },

            # Requests & local laws
            "report_issue": {
                "title": "Raise a Request or Report an Issue",
                "url": "https://www.wyndham.vic.gov.au/raise-request-or-issue"
            },
            "noise": {
                "title": "Noise & Odour (what’s allowed, how to report)",
                "url": "https://www.wyndham.vic.gov.au/services/local-laws-permits/laws-permits-residents/noise-odour-pollution"
            },
            "graffiti": {
                "title": "Graffiti (how Council manages it)",
                "url": "https://theloop.wyndham.vic.gov.au/download_file/view/1773/783"
            },
            "trees_nature_strips": {
                "title": "Maintaining Your Property (trees, nature strips)",
                "url": "https://www.wyndham.vic.gov.au/services/local-laws-permits/laws-permits-residents/maintaining-your-property"
            },

            # Planning & building
            "planning_permits": {
                "title": "Planning Application Process",
                "url": "https://www.wyndham.vic.gov.au/services/building-planning/applying-planning-permit/planning-application-process"
            },
            "building_permits": {
                "title": "When is a Building Permit Required?",
                "url": "https://www.wyndham.vic.gov.au/services/building-planning/do-i-need-approval/when-building-permit-required"
            },
            "local_laws": {
                "title": "Local Laws & Permits (Residents)",
                "url": "https://www.wyndham.vic.gov.au/services/local-laws-permits/laws-permits-residents"
            },

            # Community services & facilities
            "libraries": {
                "title": "Libraries (locations, hours, catalogue)",
                "url": "https://www.wyndham.vic.gov.au/services/libraries"
            },
            "hire_a_space": {
                "title": "Hire a Space (venues & community centres)",
                "url": "https://www.wyndham.vic.gov.au/services/community-centres-venues/hire-space"
            },
            "sports_bookings": {
                "title": "Sporting Facilities & Reserves for Hire",
                "url": "https://www.wyndham.vic.gov.au/services/sports-parks-recreation/hire-sports-reserve/sporting-facilities-and-reserves-hire"
            },
            "kindergarten_register": {
                "title": "Register for Kindergarten",
                "url": "https://www.wyndham.vic.gov.au/services/childrens-services/kindergarten/step-3-register-kindergarten"
            },
            "immunisation": {
                "title": "Immunisation Services",
                "url": "https://www.wyndham.vic.gov.au/services/childrens-services/immunisation"
            },
            "leisure_centres": {
                "title": "Aquapulse (major leisure centre) & pools",
                "url": "https://www.wyndham.vic.gov.au/services/sports-parks-recreation/major-sporting-leisure-facilities/aquapulse"
            },

            # Governance
            "foi": {
                "title": "Freedom of Information",
                "url": "https://www.wyndham.vic.gov.au/about-council/your-council/administration/freedom-information-request"
            },
            "privacy": {
                "title": "Privacy Policy",
                "url": "https://www.wyndham.vic.gov.au/about-council/your-council/administration/privacy-policy"
            },
            "contact": {
                "title": "Contact Us (phone, hours, after-hours)",
                "url": "https://www.wyndham.vic.gov.au/contact-us"
            }
        }
    }
}
# -----------------------------------------------------------

# Classifier rules: maps incoming email text to topic keys above.
TOPIC_RULES = [
    # Waste & bins (order matters)
    ("book_hard_waste", [r"\b(book|booking)\b.*hard\s*(waste|rubbish)", r"hard\s*(waste|rubbish).*\bbook"]),
    ("hard_waste",      [r"hard\s*(waste|rubbish)", r"\bbulky\b", r"mattress"]),
    ("find_bin_day",    [r"\b(bin|waste)\s*(day|calendar|schedule)", r"what\s*day\s*(is|are)\s*.*bin"]),
    ("bin_requests",    [r"(broken|damaged|stolen)\s*bin", r"replace\s*bin", r"new\s*bin", r"miss(ed|ing)\s*bin", r"bin\s*not\s*collected"]),
    ("fogo",            [r"\bFOGO\b", r"green\s*bin", r"garden\s*waste", r"organics"]),
    ("recycling_az",    [r"\b(a-?z|what\s*goes\s*in)\b", r"recycl(e|ing)\s*guide"]),
    ("transfer_station",[r"\btip\b", r"transfer\s*station", r"\blandfill\b", r"resource\s*recovery"]),
    ("hazardous_waste", [r"hazard(ous)?", r"chemicals?", r"paint", r"battery", r"e-?waste", r"asbestos"]),
    ("waste_overview",  [r"\b(bin|waste|recycling|collection)\b"]),
    ("household_bins",  [r"household\s*bin"]),

    # Parking & infringements
    ("disability_parking",[r"disability\s*parking", r"\bDPP\b"]),
    ("parking_fine_review",[r"(appeal|review)\s*(fine|infringement)", r"\bnominat(e|ion)\b"]),
    ("parking_fines_pay",[r"parking\s*(fine|infringement).*(pay|payment)", r"\bpay\s*fine\b"]),
    ("parking_permits_regs",[r"parking\s*permit", r"resident\s*permit", r"visitor\s*permit", r"parking\s*regulation"]),

    # Rates
    ("rates_payment_plan",[r"payment\s*plan.*rates"]),
    ("rates_hardship",   [r"rates?\s*(hardship|assistance)"]),
    ("rates_pay",        [r"\bpay(ing)?\s*rates?\b", r"rates?\s*payment"]),
    ("rates_home",       [r"\brates?\b", r"\bvaluation\b"]),

    # Animals
    ("pet_registration", [r"register\s*(dog|cat|pet)", r"pet\s*registration", r"microchip"]),
    ("animal_permits",   [r"animal\s*permit"]),
    ("barking_dog",      [r"\bbark(ing)?\b", r"dog\s*noise", r"dog\s*attack"]),

    # Requests & local laws
    ("report_issue",     [r"\breport\b", r"request\s*(fix|service)", r"pothole", r"footpath", r"street\s*light", r"graffiti"]),
    ("noise",            [r"\bnoise\b", r"loud\s*music", r"party", r"construction\s*noise"]),
    ("graffiti",         [r"\bgraffiti\b"]),
    ("trees_nature_strips",[r"street\s*tree", r"nature\s*strip", r"prun(e|ing)"]),

    # Planning & building
    ("planning_permits", [r"planning\s*permit", r"plan\s*permit", r"advertis(ing|ed)"]),
    ("building_permits", [r"building\s*permit", r"construction", r"demolition", r"surveyor"]),
    ("local_laws",       [r"local\s*law", r"footpath\s*(trading|dining)", r"amplified\s*sound"]),

    # Community services & facilities
    ("libraries",        [r"\blibrar(y|ies)\b", r"library\s*hours", r"borrow", r"membership"]),
    ("hire_a_space",     [r"venue\s*hire", r"hall\s*hire", r"community\s*centre", r"book\s*(a\s*)?venue"]),
    ("sports_bookings",  [r"sports?(ground| oval| pavilion)", r"book\s*(ground|oval|court)", r"seasonal\s*allocation"]),
    ("kindergarten_register",[r"\bkind(er|ergarten)\b", r"enrol(l)?", r"child\s*care", r"early\s*years"]),
    ("immunisation",     [r"\bimmuni[sz]ation\b", r"vaccin(e|ation)"]),
    ("leisure_centres",  [r"\baquapulse\b", r"\bpool\b", r"leisure\s*centre"]),

    # Governance
    ("foi",              [r"freedom\s*of\s*information", r"\bFOI\b"]),
    ("privacy",          [r"\bprivacy\b", r"personal\s*information"]),

    # Fallback
    ("contact",          [r"\bcontact\b", r"phone", r"email", r"customer\s*service"])
]

def _classify(text):
    """Return up to 4 topic keys that match the email text."""
    t = (text or "").lower()
    hits = []
    for topic, patterns in TOPIC_RULES:
        if any(re.search(p, t) for p in patterns):
            if topic not in hits:
                hits.append(topic)
        if len(hits) >= 4:
            break
    if not hits:
        hits = ["contact"]
    return hits

def _deep_merge_catalog(base_cat, override_cat):
    """Merge override_cat into base_cat without losing existing councils/topics."""
    out = json.loads(json.dumps(base_cat))  # deep copy
    for council, cdata in override_cat.items():
        if council not in out:
            out[council] = {}
        # merge base, topics
        out[council]["base"] = cdata.get("base", out[council].get("base"))
        out_topics = out[council].get("topics", {})
        for k, v in cdata.get("topics", {}).items():
            out_topics[k] = v
        out[council]["topics"] = out_topics
    return out

def _load_catalog():
    # Start with built-in Wyndham
    catalog = BUILTIN_CATALOG
    # If there's a catalog.json, merge it (it may include other councils)
    if os.path.exists(CATALOG_PATH):
        try:
            with open(CATALOG_PATH, "r") as f:
                file_cat = json.load(f)
            # If file follows the {council: {...}} shape, merge; if it’s a single-council blob, adapt
            if isinstance(file_cat, dict) and "topics" in file_cat and "base" in file_cat:
                # Single-council blob? Assume Wyndham replacement
                file_cat = {"Wyndham City Council": file_cat}
            catalog = _deep_merge_catalog(file_cat, catalog)
        except Exception:
            pass
    return catalog

def answer(email_text, council_name):
    """Return (html_body, citations) for the given email text and council name."""
    catalog = _load_catalog()

    # Choose council: if not Wyndham and not in file, still try Wyndham if the name contains 'wyndham'
    if council_name in catalog:
        council = catalog[council_name]
    elif "wyndham" in council_name.lower():
        council = catalog["Wyndham City Council"]
        council_name = "Wyndham City Council"
    else:
        # Unknown council → graceful fallback
        body = (
            f"<p>Thanks for contacting {html.escape(council_name)}.</p>"
            f"<p>We’ll escalate this to an officer and follow up shortly.</p>"
        )
        return body, []

    topics_map = council.get("topics", {})
    wanted = _classify(email_text)

    links = []
    # choose preferred links for a couple of special cases
    if "hard_waste" in wanted and "book_hard_waste" in topics_map:
        # prefer booking page when user says 'book'
        if re.search(r"\bbook(ing)?\b", (email_text or "").lower()):
            wanted.insert(0, "book_hard_waste")

    for key in wanted:
        info = topics_map.get(key)
        if info and info.get("url"):
            links.append(info)

    # Always include general contact if nothing else
    if not links and topics_map.get("contact"):
        links.append(topics_map["contact"])

    # Build Outlook-safe HTML body
    snippet = html.escape((email_text or "").strip()[:900]).replace("\n", "<br>")
    lis = "".join(
        f"<li><a href='{html.escape(x['url'])}'>{html.escape(x['title'])}</a></li>"
        for x in links[:6]
    )

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
    citations = [f"{x.get('title','Council page')} | {x.get('url','')}" for x in links[:6]]
    return body, citations
