# build_catalog.py — build catalog.json of key URLs per council (broad topics, no embeddings)
# Python 3.8+ compatible

import os, json, re, argparse, time, sys
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ---------- Tuning ----------
TIMEOUT = int(os.getenv("CATALOG_TIMEOUT", "18"))
RATE_LIMIT_SEC = float(os.getenv("CATALOG_RATE_LIMIT_SEC", "0.4"))  # polite delay between requests
RETRIES_TOTAL = int(os.getenv("CATALOG_RETRIES_TOTAL", "4"))
RETRIES_BACKOFF = float(os.getenv("CATALOG_RETRIES_BACKOFF", "0.6"))
SAVE_EVERY = int(os.getenv("CATALOG_SAVE_EVERY", "3"))  # write partial progress every N councils

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126 Safari/537.36 CivReplyCatalogBot/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

# ---------- Topic patterns ----------
TOPICS = {
    # Waste & recycling
    "waste": {
        "slugs": [
            "/services/waste-recycling","/services/rubbish-and-recycling",
            "/residents/waste-and-recycling","/waste-recycling","/waste-and-recycling",
            "/Residents/Rubbish-and-recycling","/Environment/Waste-and-recycling",
            "/city/rubbish-and-recycling"
        ],
        "keywords": ["waste","recycling","bin","garbage","litter","collection"]
    },
    "waste_calendar": {
        "slugs": [
            "/bin-collection","/bin-collection-days","/waste-collection","/kerbside-collections",
            "/rubbish-collection","/collection-days"
        ],
        "keywords": ["collection day","calendar","address","schedule","bin day","lookup"]
    },
    "hard_rubbish": {
        "slugs": [
            "/hard-waste","/hard-rubbish","/waste-recycling/hard",
            "/residents/waste-and-recycling/hard-waste-collection"
        ],
        "keywords": ["hard waste","hard rubbish","bulky","book","collection","mattress"]
    },
    "green_waste": {
        "slugs": [
            "/green-waste","/garden-waste","/organics",
            "/waste-recycling/green","/kerbside-collections"
        ],
        "keywords": ["green","garden","organics","FOGO","bin","collection"]
    },
    "missed_bin": {
        "slugs": ["/missed-bin","/missed-collection","/report/missed-bin","/bin-collection/missed"],
        "keywords": ["missed","bin not collected","collection missed","overflow"]
    },
    "bin_repair": {
        "slugs": ["/damaged-bin","/broken-bin","/bin-repair","/replace-bin","/new-bin","/request-bin"],
        "keywords": ["damaged","broken","stolen","replacement","new bin","repair"]
    },
    "transfer_station": {
        "slugs": ["/transfer-station","/resource-recovery","/tip","/landfill","/waste-facility"],
        "keywords": ["transfer station","tip","landfill","resource recovery","fees","hours"]
    },
    "recycling_az": {
        "slugs": ["/a-z-recycling","/recycling-a-z","/what-goes-in","/waste-guide"],
        "keywords": ["what goes in","a-z","guide","recycle","dispose"]
    },
    "hazardous_waste": {
        "slugs": ["/hazardous","/chemicals","/detox-your-home","/paint-batteries","/ewaste"],
        "keywords": ["hazardous","chemical","paint","battery","e-waste","asbestos"]
    },

    # Parking & roads
    "parking_permits": {
        "slugs": [
            "/parking-permits","/parking/permits",
            "/residents/parking-permits","/transport/parking/permits"
        ],
        "keywords": ["parking","permit","resident","visitor","zone","apply","renew"]
    },
    "parking_fines": {
        "slugs": ["/parking-fines","/infringements","/pay-fine","/review-infringement","/appeal-fine"],
        "keywords": ["fine","infringement","pay","review","appeal","nominate"]
    },
    "report_issue": {
        "slugs": ["/report","/report-it","/report-a-problem","/request","/roads/maintenance","/footpath"],
        "keywords": ["report","request","fix","pothole","footpath","streetlight","graffiti"]
    },
    "noise_complaints": {
        "slugs": ["/noise","/amenity/noise","/local-laws/noise","/report/noise"],
        "keywords": ["noise","after hours","music","party","construction"]
    },
    "graffiti": {
        "slugs": ["/graffiti","/report/graffiti","/graffiti-removal"],
        "keywords": ["graffiti","remove","report"]
    },
    "trees": {
        "slugs": ["/trees","/nature-strips","/street-trees","/report/tree"],
        "keywords": ["street tree","pruning","remove","permit","nature strip"]
    },

    # Rates
    "rates": {
        "slugs": [
            "/rates","/services/rates","/council/rates",
            "/services/rates-and-valuations","/rates-and-valuation","/rates-and-valuations"
        ],
        "keywords": ["rates","valuation","instalment","due","BPAY","notice"]
    },
    "rates_hardship": {
        "slugs": ["/rates/hardship","/financial-hardship","/rates-assistance"],
        "keywords": ["hardship","assistance","payment plan","relief"]
    },

    # Animals
    "pet_registration": {
        "slugs": ["/pets","/animals","/dogs-cats","/animal-registration","/register-your-pet"],
        "keywords": ["pet","dog","cat","registration","microchip","renew"]
    },

    # Planning & building
    "planning_permits": {
        "slugs": ["/planning-permits","/planning/apply","/planning-building/planning","/planning-and-building/planning"],
        "keywords": ["planning","permit","application","advertising","neighbor"]
    },
    "building_permits": {
        "slugs": [
            "/building-permits","/planning-building/building-permits",
            "/building-and-development/building-permits","/planning-and-building/building","/building"
        ],
        "keywords": ["building","permit","surveyor","construction","demolition"]
    },
    "local_laws": {
        "slugs": ["/local-laws","/local-law","/laws-and-permits","/local-laws-permits","/compliance"],
        "keywords": ["local law","permit","trading","footpath","amplified"]
    },

    # Community services
    "libraries": {
        "slugs": ["/libraries","/library","/community/libraries","/residents/libraries","/Leisure-and-culture/Libraries","/Our-Community/Libraries"],
        "keywords": ["library","libraries","hours","borrowing","membership"]
    },
    "venue_hire": {
        "slugs": ["/venues","/halls","/venue-hire","/community-centres","/book-a-venue"],
        "keywords": ["venue","hall","community centre","hire","book"]
    },
    "sports_bookings": {
        "slugs": ["/sports","/sport-and-recreation","/sportsgrounds","/book-a-ground","/pavilions"],
        "keywords": ["sportsground","oval","pavilion","seasonal allocation","book"]
    },
    "childcare_kindergarten": {
        "slugs": ["/kindergarten","/early-years","/childcare","/children-services/kindergarten"],
        "keywords": ["kinder","kindergarten","child care","enrol","registration"]
    },
    "maternal_child_health": {
        "slugs": ["/maternal-and-child-health","/mch","/child-health"],
        "keywords": ["maternal","child health","nurse","appointment","key ages"]
    },
    "immunisation": {
        "slugs": ["/immunisation","/vaccination","/immunisation-sessions"],
        "keywords": ["immunisation","vaccination","clinic","session","schedule"]
    },
    "leisure_centres_pools": {
        "slugs": ["/pools","/aquatic","/leisure-centre","/recreation-centre"],
        "keywords": ["pool","aquatic","leisure","swim","membership","hours"]
    },

    # Emergencies / compliance
    "fire_permits": {
        "slugs": ["/fire","/burn-off","/permits-to-burn","/total-fire-ban"],
        "keywords": ["fire","burn off","permit","CFA","ban"]
    },
    "storm_flood_sandbags": {
        "slugs": ["/storms","/flood","/sandbag","/emergency"],
        "keywords": ["storm","flood","sandbag","emergency","SES"]
    },
    "foi": {
        "slugs": ["/freedom-of-information","/foi","/council/governance/freedom-of-information"],
        "keywords": ["freedom of information","foi","request","access"]
    },
    "privacy": {
        "slugs": ["/privacy","/information-privacy","/privacy-policy"],
        "keywords": ["privacy","personal information","IPP","policy"]
    },

    # Always
    "contact": {
        "slugs": ["/contact-us","/about-council/contact-us","/council/contact-us","/contact","/Contact-us","/Your-Council/Contact-us"],
        "keywords": ["contact","phone","email","hours","after hours","request","service centre"]
    }
}

# ---------- HTTP session with retries ----------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=RETRIES_TOTAL,
        backoff_factor=RETRIES_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = make_session()

def fetch(url: str):
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or not r.text or not r.text.strip():
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script","style","noscript","svg"]): 
            t.decompose()
        title = (soup.title.string.strip() if soup.title and soup.title.string else url)
        text = re.sub(r"\s+", " ", soup.get_text(" ").strip())
        return title, text[:20000]
    except Exception:
        return None, None
    finally:
        if RATE_LIMIT_SEC > 0:
            time.sleep(RATE_LIMIT_SEC)

def score(text, kws):
    if not text: 
        return 0
    t = text.lower()
    total = 0
    for k in kws:
        if " " in k and k in t:
            total += 2
        elif k in t:
            total += 1
    return total

def normalize_base(base):
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if not base.startswith("http"):
        base = "https://" + base
    return base

def best_url(base, topic, cfg):
    tried = set()
    best = None
    best_score = -1
    for slug in cfg["slugs"]:
        url = urljoin(base.rstrip("/") + "/", slug.lstrip("/"))
        if url in tried: 
            continue
        tried.add(url)
        title, text = fetch(url)
        if not text:
            continue
        s = score(text, cfg["keywords"])
        if topic in ("contact", "libraries") and "hours" in text.lower():
            s += 3
        if s > best_score:
            best_score = s
            best = {"url": url, "title": title or url}
    return best

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def maybe_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="Comma-separated council names to build (exact match)")
    ap.add_argument("--outfile", default="catalog.json", help="Output file")
    ap.add_argument("--overrides", default="overrides.json", help="Optional overrides file (topic->url per council)")
    args = ap.parse_args()

    try:
        councils = load_json("councils.json")
    except Exception as e:
        print("❌ Missing or invalid councils.json. Expected format: { 'Council Name': 'www.example.vic.gov.au', ... }")
        print("Error:", e)
        sys.exit(1)

    overrides = {}
    if os.path.exists(args.overrides):
        try:
            overrides = load_json(args.overrides)  # { "Council Name": { "topic": "https://..." } }
            print(f"✓ Loaded overrides from {args.overrides}")
        except Exception:
            print(f"⚠️ Could not parse overrides file: {args.overrides}")

    names = list(councils.keys())
    if args.only:
        pick = {n.strip() for n in args.only.split(",") if n.strip()}
        names = [n for n in names if n in pick]

    out = {}
    t0 = time.time()
    failures = []

    for i, name in enumerate(names, 1):
        base = normalize_base(councils[name])
        if not base:
            print(f"• {name}: (no base URL?) — skipped")
            failures.append(name)
            continue

        print(f"• {name}: {base}")

        # Try HTTPS; if no topics found at all, retry once with http://
        bases_to_try = [base]
        if base.startswith("https://"):
            bases_to_try.append("http://" + base[len("https://"):])

        found_any = False
        council_entry = {"base": base, "topics": {}}

        # Apply any manual overrides first
        ov = overrides.get(name, {})
        for topic, url in (ov.items() if isinstance(ov, dict) else []):
            council_entry["topics"][topic] = {"url": url, "title": url}
            found_any = True
            print(f"  - {topic:22s} -> (override) {url}")

        for base_try in bases_to_try:
            for topic, cfg in TOPICS.items():
                if topic in council_entry["topics"]:
                    continue  # already set via override
                info = best_url(base_try, topic, cfg)
                if info:
                    council_entry["topics"][topic] = {"url": info["url"], "title": info["title"]}
                    print(f"  - {topic:22s} -> {info['url']}")
                    found_any = True

            if found_any:
                break  # no need to try the http:// fallback if we found some on https://

        out[name] = council_entry
        if not found_any:
            failures.append(name)
            print("  (no topics found)")

        # Save partial progress every few councils
        if i % SAVE_EVERY == 0:
            maybe_write(args.outfile, out)
            print(f"  …partial save → {args.outfile}")

    maybe_write(args.outfile, out)
    dt = int(time.time() - t0)
    print(f"\nWrote {args.outfile} in {dt}s. Councils processed: {len(names)}. Failures: {len(failures)}")
    if failures:
        print("No topics found for:", ", ".join(failures))

if __name__ == "__main__":
    main()
