# build_catalog.py — build catalog.json of key URLs per council (broad topics, no embeddings)
# Python 3.8+ compatible (no union types)

import os, json, re, argparse, requests, time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 18

# Topic → candidate slug patterns + scoring keywords
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

    # Rates & revenue
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

    # Planning & building & local laws
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

    # Community facilities & services
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

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script","style","noscript","svg"]): t.decompose()
        title = (soup.title.string.strip() if soup.title else url)
        text = re.sub(r"\s+", " ", soup.get_text(" ").strip())
        return title, text[:20000]
    except Exception:
        return None, None

def score(text, kws):
    if not text: return 0
    t = text.lower()
    total = 0
    for k in kws:
        if " " in k and k in t:
            total += 2
        elif k in t:
            total += 1
    return total

def best_url(base, topic, cfg):
    tried = []
    best = None
    best_score = -1
    for slug in cfg["slugs"]:
        url = urljoin(base.rstrip("/") + "/", slug.lstrip("/"))
        if url in tried: continue
        tried.append(url)
        title, text = fetch(url)
        if not text: continue
        s = score(text, cfg["keywords"])
        if topic in ("contact", "libraries") and "hours" in text.lower():
            s += 3
        if s > best_score:
            best_score = s
            best = {"url": url, "title": title or url}
    return best

def normalize_base(base):
    base = base.strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return base

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="Comma-separated council names to build")
    ap.add_argument("--outfile", default="catalog.json", help="Output file")
    args = ap.parse_args()

    councils = json.load(open("councils.json"))
    names = list(councils.keys())
    if args.only:
        pick = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in pick]

    out = {}
    t0 = time.time()
    for name in names:
        base = normalize_base(councils[name])
        print(f"• {name}: {base}")
        out[name] = {"base": base, "topics": {}}
        for topic, cfg in TOPICS.items():
            info = best_url(base, topic, cfg)
            if info:
                out[name]["topics"][topic] = {"url": info["url"], "title": info["title"]}
                print(f"  - {topic:22s} -> {info['url']}")
            else:
                print(f"  - {topic:22s} -> (not found)")
    with open(args.outfile, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {args.outfile} in {int(time.time()-t0)}s")

if __name__ == "__main__":
    main()
