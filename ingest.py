# ingest.py — robust discovery (sitemaps + manual seeds + BFS crawl) for VIC councils
import os, json, re, time, gzip, argparse, faiss, requests, tiktoken, numpy as np
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"  # 1536
DIM = 1536
ENC = tiktoken.get_encoding("cl100k_base")
IDX_DIR = "indexes"
os.makedirs(IDX_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Keep pages relevant to inbox FAQs
KEEP_PATTERNS = [
    "waste", "recycling", "bin", "hard-waste", "hardwaste", "hard-rubbish", "green-waste",
    "parking", "permit", "permits", "rates", "fees", "payments", "pay",
    "contact", "customer", "libraries", "library", "opening-hours", "hours",
    "animal", "pets", "dogs", "cats",
    "building", "planning", "roads", "footpath", "maintenance", "graffiti",
    "events", "collection", "book", "booking", "transfer-station", "tip", "landfill"
]

# Fallback guesses if sitemaps are thin
FALLBACK_PATHS = [
    "/services", "/contact-us", "/contact",
    "/rates", "/waste-recycling", "/waste-and-recycling",
    "/parking-permits", "/parking/permits", "/libraries",
    "/building-permits", "/planning-building"
]

# Manual high-signal seeds for tricky domains (adjust/expand over time)
MANUAL_SEEDS = {
    "https://www.melbourne.vic.gov.au": [
        "/residents/waste-and-recycling/kerbside-collections",
        "/residents/waste-and-recycling/hard-waste-collection",
        "/parking-and-transport/permits/resident-parking-permits",
        "/residents/rates",
        "/libraries",
        "/building-and-development/building-permits",
        "/contact-us"
    ],
    "https://www.yarracity.vic.gov.au": [
        "/services/waste-and-recycling",
        "/services/parking-permits",
        "/planning-building/building",
        "/about-us/contact-us",
        "/services/rates"
    ],
}

def good(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in KEEP_PATTERNS)

def fetch(url: str):
    return requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)

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
        if sm_url.endswith(".gz"):
            data = gzip.decompress(data)
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

def bfs_crawl(base: str, starts: list[str], limit_pages=120, max_depth=2):
    """Lightweight same-site crawl starting from seed paths/URLs."""
    host = urlparse(base).netloc
    q = []
    seen = set()
    urls = []

    # normalise starts to absolute
    for s in starts:
        absu = s if s.startswith("http") else urljoin(base, s)
        q.append((absu, 0))
        seen.add(absu)

    while q and len(urls) < limit_pages:
        url, depth = q.pop(0)
        try:
            r = fetch(url)
            if r.status_code != 200 or not r.text:
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
            if not p.scheme.startswith("http"):
                continue
            if not p.netloc.endswith(host):
                continue
            if href in seen:
                continue
            seen.add(href)
            q.append((href, depth + 1))
            if len(urls) >= limit_pages:
                break
    # dedupe while preserving order
    seen2, deduped = set(), []
    for u in urls:
        if u not in seen2:
            seen2.add(u)
            deduped.append(u)
    return deduped

def discover_urls(base: str, limit=150) -> list[str]:
    urls = []
    # 1) sitemaps
    seen = set()
    for cand in sitemap_candidates(base):
        for u in iter_sitemap_urls(cand) or []:
            if urlparse(u).netloc.endswith(urlparse(base).netloc) and u not in seen:
                seen.add(u); urls.append(u)
                if len(urls) >= limit: break
        if len(urls) >= limit: break

    # 2) add manual seeds
    seed_paths = MANUAL_SEEDS.get(base.rstrip("/"), [])
    for s in seed_paths:
        absu = s if s.startswith("http") else urljoin(base, s)
        if absu not in urls:
            urls.append(absu)

    # 3) fallbacks if still thin
    if len(urls) < 10:
        for path in FALLBACK_PATHS:
            u = urljoin(base, path)
            try:
                rr = fetch(u)
                if rr.status_code == 200:
                    urls.append(u)
            except Exception:
                pass

    # 4) mini-crawl if still < threshold
    if len([u for u in urls if good(u)]) < 25:
        crawl_starts = seed_paths or ["/"]
        crawled = bfs_crawl(base, crawl_starts, limit_pages=180, max_depth=2)
        urls.extend(crawled)

    # same-host + pattern + dedupe + limit
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
    r = fetch(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string if soup.title else url).strip()
    for tag in soup(["script","style","noscript","svg"]): tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ").strip())
    return title, text

def chunk(text: str, max_tokens=500, overlap=80):
    toks = ENC.encode(text); i = 0
    while i < len(toks):
        j = min(i + max_tokens, len(toks))
        yield ENC.decode(toks[i:j]); i = j - overlap

def embed_texts(client: OpenAI, chunks: list[str]):
    resp = client.embeddings.create(model=EMBED_MODEL, input=chunks)
    return [v.embedding for v in resp.data]

def build_for(name: str, base_url: str):
    print(f"• {name}: discovering pages from {base_url}")
    urls = discover_urls(base_url)
    if not urls:
        print("  (!) No URLs discovered — adjust MANUAL_SEEDS or KEEP_PATTERNS.")
        return
    print(f"  found {len(urls)} URLs; fetching & chunking…")

    docs, metas = [], []
    for u in urls:
        try:
            title, text = fetch_clean(u)
            for ch in chunk(text):
                docs.append(ch); metas.append({"url": u, "title": title})
        except Exception as e:
            print("   skip", u, str(e)[:100])

    if not docs:
        print("  (!) No content scraped."); return

    client = OpenAI()
    vecs = embed_texts(client, docs)
    X = np.array(vecs, dtype="float32")
    faiss.normalize_L2(X)
    index = faiss.IndexFlatIP(DIM); index.add(X)

    faiss.write_index(index, f"{IDX_DIR}/{name}.faiss")
    with open(f"{IDX_DIR}/{name}.jsonl","w") as f:
        for d,m in zip(docs, metas):
            f.write(json.dumps({"text": d, **m}) + "\n")
    print(f"  ✓ Built {name}: {len(docs)} chunks from {len(urls)} pages")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated council names", default="")
    args = ap.parse_args()
    data = json.load(open("councils.json"))
    names = list(data.keys())
    if args.only:
        pick = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in pick]
    t0 = time.time()
    for n in names:
        try:
            build_for(n, data[n])
        except Exception as e:
            print("ERROR", n, e)
    print(f"Done in {int(time.time()-t0)}s")
