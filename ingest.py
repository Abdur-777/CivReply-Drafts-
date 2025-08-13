# ingest.py — robust & low-memory index builder for VIC councils
# - Sitemaps + manual seeds + light BFS crawl
# - Embeds incrementally in small batches (low RAM)
#
# Examples:
#   python3 ingest.py --only "Wyndham City Council" --limit-pages 40 --max-chunks 600 --batch 12
#   python3 ingest.py  # build all councils from councils.json with safe defaults

import os, sys, json, re, time, gzip, argparse, hashlib, gzip
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import faiss, numpy as np, tiktoken
from openai import OpenAI
import urllib.robotparser as robotparser

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")  # 1536 dims
DIM = 1536
ENC = tiktoken.get_encoding("cl100k_base")

OUT_ROOT = os.getenv("INDEX_ROOT", "index")  # will write to index/<slug>/
os.makedirs(OUT_ROOT, exist_ok=True)

# Politeness / networking
TIMEOUT = int(os.getenv("INGEST_TIMEOUT", "25"))
RETRY_TOTAL = int(os.getenv("INGEST_RETRIES", "4"))
RETRY_BACKOFF = float(os.getenv("INGEST_BACKOFF", "0.6"))
RATE_LIMIT = float(os.getenv("INGEST_RATE_LIMIT", "0.25"))  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 CivReplyIngest/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

# Keep / skip patterns
KEEP_PATTERNS = [
    "waste","recycling","bin","hard-waste","hardwaste","hard-rubbish","green-waste",
    "parking","permit","permits","rates","fees","payments","pay","fines",
    "contact","customer","libraries","library","opening-hours","hours","holiday",
    "animal","pets","dogs","cats","registration",
    "building","planning","building-permit","building-permits","roads","footpath","maintenance","graffiti",
    "events","collection","book","booking","transfer-station","tip","landfill","garbage"
]
SKIP_PATTERNS = [
    "/wp-admin", "/search?", "/login", "/signin", "/sign-in", "/account", "/cart", "/shop",
    "/terms", "/privacy-policy"  # legal pages are rarely useful for resident queries (except privacy) — adjust if needed
]

FALLBACK_PATHS = [
    "/services","/contact-us","/contact","/rates","/rates-and-valuation",
    "/waste-recycling","/waste-and-recycling","/parking-permits","/parking/permits",
    "/libraries","/library","/building-permits","/planning-building","/planning-and-building","/building","/planning"
]

# --------- MANUAL SEEDS (truncated for brevity — keep your full dict) ----------
MANUAL_SEEDS = {
    "https://www.wyndham.vic.gov.au": [
        "/services/waste-recycling",
        "/services/parking-roads/parking-permits",
        "/services/planning-building/building-permits",
        "/services/rates",
        "/libraries",
        "/contact-us"
    ],
    # ... (keep the rest of your MANUAL_SEEDS here)
}
# --------- /MANUAL SEEDS ----------

def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"(city of|city|shire|council|borough)", "", s, flags=re.I)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "council"

def good(url: str) -> bool:
    u = url.lower()
    if any(x in u for x in SKIP_PATTERNS):
        return False
    return any(p in u for p in KEEP_PATTERNS)

# Session with retries
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = make_session()

def fetch(url: str) -> requests.Response:
    r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
    if RATE_LIMIT > 0:
        time.sleep(RATE_LIMIT)
    return r

def sitemap_candidates(base: str):
    base = base.rstrip("/")
    parsed = urlparse(base)
    hosts = {f"{parsed.scheme}://{parsed.netloc}"}
    if not parsed.netloc.startswith("www."):
        hosts.add(f"{parsed.scheme}://www.{parsed.netloc}")
    paths = ["sitemap.xml","sitemap_index.xml","sitemapindex.xml",
             "sitemap/sitemap.xml","sitemap/sitemap_index.xml",
             "sitemaps/sitemap.xml","sitemaps/sitemap_index.xml","sitemap-1.xml"]
    for h in hosts:
        for p in paths:
            yield f"{h}/{p}"
    # robots.txt → Sitemap:
    for h in hosts:
        try:
            r = fetch(f"{h}/robots.txt")
            if r.status_code == 200:
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        yield line.split(":",1)[1].strip()
        except Exception:
            pass

def iter_sitemap_urls(sm_url: str):
    try:
        r = fetch(sm_url)
        if r.status_code != 200 or not r.content:
            return
        data = r.content
        if sm_url.endswith(".gz") or r.headers.get("Content-Encoding","").lower() == "gzip":
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        root = ET.fromstring(data)
        if root.tag.endswith("sitemapindex"):
            for node in root.iter():
                if node.tag.endswith("loc") and node.text:
                    yield from iter_sitemap_urls(node.text.strip())
        else:
            for node in root.iter():
                if node.tag.endswith("loc") and node.text:
                    yield node.text.strip()
    except Exception:
        return

def allow_url(rp: robotparser.RobotFileParser, url: str) -> bool:
    try:
        return rp.can_fetch(HEADERS["User-Agent"], url)
    except Exception:
        return True

def load_robots(base: str) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    robots_url = urljoin(base.rstrip("/") + "/", "robots.txt")
    try:
        rp.set_url(robots_url)
        # use our session for consistency
        r = fetch(robots_url)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        else:
            rp.parse([])
    except Exception:
        rp.parse([])
    return rp

def bfs_crawl(base: str, starts: list, limit_pages=120, max_depth=2, rp=None):
    host = urlparse(base).netloc
    q, seen, urls = [], set(), []
    for s in starts:
        absu = s if s.startswith("http") else urljoin(base, s)
        q.append((absu, 0)); seen.add(absu)
    while q and len(urls) < limit_pages:
        url, depth = q.pop(0)
        if rp and not allow_url(rp, url):
            continue
        try:
            r = fetch(url)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if r.status_code != 200 or "text/html" not in ctype or not r.text:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            continue
        if good(url):
            urls.append(url)
        if depth >= max_depth:
            continue
        for a in soup.select("a[href]"):
            href = urljoin(url, a.get("href"))
            p = urlparse(href)
            if not p.scheme.startswith("http"): continue
            if not p.netloc.endswith(host): continue
            if href in seen: continue
            if rp and not allow_url(rp, href): continue
            seen.add(href); q.append((href, depth+1))
            if len(urls) >= limit_pages: break
    # dedupe preserving order
    out, seen2 = [], set()
    for u in urls:
        if u not in seen2:
            seen2.add(u); out.append(u)
    return out

def discover_urls(base: str, limit=150, ignore_robots=False) -> list:
    urls, seen = [], set()
    rp = None if ignore_robots else load_robots(base)

    # sitemaps
    for cand in sitemap_candidates(base):
        for u in iter_sitemap_urls(cand) or []:
            if urlparse(u).netloc.endswith(urlparse(base).netloc) and u not in seen:
                if rp and not allow_url(rp, u):
                    continue
                seen.add(u); urls.append(u)
                if len(urls) >= limit: break
        if len(urls) >= limit: break

    # seeds
    seed_paths = MANUAL_SEEDS.get(base.rstrip("/"), [])
    for s in seed_paths:
        absu = s if s.startswith("http") else urljoin(base, s)
        if rp and not allow_url(rp, absu):
            continue
        if absu not in urls: urls.append(absu)

    # generic fallbacks
    if len(urls) < 10:
        for path in FALLBACK_PATHS:
            u = urljoin(base, path)
            try:
                if rp and not allow_url(rp, u): 
                    continue
                rr = fetch(u)
                if rr.status_code == 200 and "text/html" in (rr.headers.get("Content-Type","").lower()):
                    urls.append(u)
            except Exception:
                pass

    # mini-BFS if still thin
    if len([u for u in urls if good(u)]) < 25:
        crawled = bfs_crawl(base, seed_paths or ["/"], limit_pages=min(180, limit*2), max_depth=2, rp=rp)
        urls.extend(crawled)

    # filter, dedupe, cap, content-type guard
    host = urlparse(base).netloc
    urls = [u for u in urls if urlparse(u).netloc.endswith(host)]
    urls = [u for u in urls if good(u)]
    deduped, seen = [], set()
    for u in urls:
        if u not in seen:
            seen.add(u); deduped.append(u)
        if len(deduped) >= limit: break
    return deduped

def fetch_clean(url: str) -> tuple[str, str]:
    r = fetch(url); r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "text/html" not in ctype:
        raise RuntimeError(f"skip non-HTML: {ctype}")
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string if soup.title and soup.title.string else url).strip()
    for tag in soup(["script","style","noscript","svg"]): tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ").strip())
    return title, text

def chunk(text: str, max_tokens=500, overlap=80):
    # Ensure forward progress even if overlap >= max_tokens
    overlap = max(0, min(overlap, max_tokens - 1))
    toks = ENC.encode(text)
    i = 0
    while i < len(toks):
        j = min(i + max_tokens, len(toks))
        yield ENC.decode(toks[i:j])
        if j >= len(toks): break
        i = j - overlap if j - i > overlap else j

def embed_in_batches(client: OpenAI, texts: list[str], batch_size: int):
    """Yield embeddings for texts in small batches to keep RAM low."""
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        for item in resp.data:
            yield item.embedding

def ensure_outdir(slug: str) -> str:
    d = os.path.join(OUT_ROOT, slug)
    os.makedirs(d, exist_ok=True)
    return d

def build_for(name: str, base_url: str, limit_pages: int, max_chunks: int, batch: int, ignore_robots: bool):
    slug = slugify(name)
    outdir = ensure_outdir(slug)
    info_path = os.path.join(outdir, "info.json")
    meta_path = os.path.join(outdir, "meta.jsonl")
    faiss_path = os.path.join(outdir, "index.faiss")

    print(f"• {name} [{slug}]: discovering pages from {base_url}")
    urls = discover_urls(base_url, limit=limit_pages, ignore_robots=ignore_robots)
    if not urls:
        print("  (!) No URLs discovered — adjust seeds or patterns."); return
    print(f"  found {len(urls)} URLs; fetching & chunking… (limit {max_chunks} chunks)")

    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY is not set.")
        sys.exit(1)

    # Prepare index + outputs
    index = faiss.IndexFlatIP(DIM)
    client = OpenAI()
    meta_f = open(meta_path, "w", encoding="utf-8")
    added = 0

    docs_batch, metas_batch = [], []
    seen_chunks = set()  # md5 of text to reduce duplicates

    def flush_batch():
        nonlocal docs_batch, metas_batch, added
        if not docs_batch: return
        vecs = list(embed_in_batches(client, docs_batch, batch))
        X = np.asarray(vecs, dtype="float32")
        faiss.normalize_L2(X)
        index.add(X)
        for m in metas_batch:
            meta_f.write(json.dumps(m, ensure_ascii=False) + "\n")
        added += len(metas_batch)
        docs_batch, metas_batch = [], []

    # Stream pages → chunks → embed
    for u in urls:
        if added >= max_chunks: break
        try:
            title, text = fetch_clean(u)
            for ch in chunk(text):
                if added + len(metas_batch) >= max_chunks:
                    break
                h = hashlib.md5(ch.encode("utf-8")).hexdigest()
                if h in seen_chunks:
                    continue
                seen_chunks.add(h)
                docs_batch.append(ch)
                metas_batch.append({"text": ch, "url": u, "title": title, "council": slug})
                if len(docs_batch) >= batch:
                    flush_batch()
        except Exception as e:
            print("   skip", u, str(e)[:120])

    flush_batch()
    meta_f.close()
    faiss.write_index(index, faiss_path)

    # Write info for compatibility checks
    info = {
        "model": EMBED_MODEL,
        "dim": DIM,
        "created": int(time.time()),
        "params": {
            "limit_pages": limit_pages,
            "max_chunks": max_chunks,
            "batch": batch,
            "rate_limit": RATE_LIMIT
        },
        "source_base": base_url,
        "count_chunks": added,
    }
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"  ✓ Built {name} → {faiss_path}  ({added} chunks)")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated council names", default="")
    ap.add_argument("--limit-pages", type=int, default=80, help="Max URLs per council to fetch")
    ap.add_argument("--max-chunks", type=int, default=1200, help="Max chunks per council")
    ap.add_argument("--batch", type=int, default=12, help="Embedding batch size (lower = lower RAM)")
    ap.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt (NOT recommended)")
    args = ap.parse_args()

    try:
        data = json.load(open("councils.json", "r"))
    except Exception as e:
        print("❌ Missing or invalid councils.json. Expected: { 'Council Name': 'https://domain', ... }")
        sys.exit(1)

    names = list(data.keys())
    if args.only:
        pick = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in pick]

    t0 = time.time()
    for n in names:
        try:
            build_for(n, data[n], args.limit_pages, args.max_chunks, args.batch, args.ignore_robots)
        except Exception as e:
            print("ERROR", n, e)
    print(f"Done in {int(time.time()-t0)}s")
