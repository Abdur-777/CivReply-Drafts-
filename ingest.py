# ingest.py — robust sitemap discovery + FAISS indexing for VIC councils
import os, json, re, time, gzip, io, argparse, faiss, requests, tiktoken, numpy as np
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"  # 1536 dims
DIM = 1536
ENC = tiktoken.get_encoding("cl100k_base")
IDX_DIR = "indexes"
os.makedirs(IDX_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# pages we care about
KEEP_PATTERNS = [
    "waste", "recycling", "bin", "hard-rubbish", "hardrubbish", "green-waste",
    "parking", "permit", "permits", "rates", "fees", "payments",
    "contact", "customer", "libraries", "library",
    "animal", "pets", "dogs", "cats",
    "building", "planning", "roads", "footpath",
    "events", "opening-hours", "hours", "collection", "book", "booking",
    "transfer-station", "tip"
]

# generic fallbacks if sitemaps are tricky
FALLBACK_PATHS = [
    "/services", "/contact-us", "/contact",
    "/rates", "/waste-recycling", "/waste-and-recycling",
    "/parking-permits", "/parking/permits", "/libraries",
    "/building-permits", "/planning-building"
]

def good(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in KEEP_PATTERNS)

def fetch(url: str):
    return requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)

def sitemap_candidates(base: str):
    """Yield likely sitemap URLs (handles www + robots.txt)."""
    base = base.rstrip("/")
    parsed = urlparse(base)
    schemes_hosts = {f"{parsed.scheme}://{parsed.netloc}"}
    if not parsed.netloc.startswith("www."):
        schemes_hosts.add(f"{parsed.scheme}://www.{parsed.netloc}")

    paths = [
        "sitemap.xml", "sitemap_index.xml", "sitemapindex.xml",
        "sitemap/sitemap.xml", "sitemap/sitemap_index.xml",
        "sitemaps/sitemap.xml", "sitemaps/sitemap_index.xml",
        "sitemap-1.xml"
    ]
    for sh in schemes_hosts:
        for p in paths:
            yield f"{sh}/{p}"

    # robots.txt → Sitemap: lines
    for sh in schemes_hosts:
        try:
            r = fetch(f"{sh}/robots.txt")
            if r.status_code == 200:
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        url = line.split(":", 1)[1].strip()
                        if url:
                            yield url
        except Exception:
            pass

def iter_sitemap_urls(sm_url: str):
    """Read a (possibly gzipped) sitemap or sitemap index and yield <loc> URLs."""
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

def discover_urls(base: str, limit=120) -> list[str]:
    """Find candidate content URLs via sitemaps; fall back to guessed paths."""
    urls = []

    # 1) sitemaps (incl. robots.txt, gz, www)
    seen = set()
    for cand in sitemap_candidates(base):
        for u in iter_sitemap_urls(cand):
            if u not in seen:
                seen.add(u)
                urls.append(u)
                if len(urls) >= limit:
                    break
        if len(urls) >= limit:
            break

    # 2) fallbacks if sitemap didn’t yield anything useful
    if not urls:
        for path in FALLBACK_PATHS:
            u = base.rstrip("/") + path
            try:
                rr = fetch(u)
                if rr.status_code == 200:
                    urls.append(u)
            except Exception:
                pass

    # keep same host + pattern filter + dedupe
    host = urlparse(base).netloc
    urls = [u for u in urls if urlparse(u).netloc.endswith(host)]
    urls = [u for u in urls if good(u)]
    deduped = []
    seen = set()
    for u in urls:
        if u in seen: continue
        seen.add(u); deduped.append(u)
        if len(deduped) >= limit: break
    return deduped

def fetch_clean(url: str) -> tuple[str, str]:
    r = fetch(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string if soup.title else url).strip()
    for tag in soup(["script", "style", "noscript", "svg"]): tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(separator=" ").strip())
    return title, text

def chunk(text: str, max_tokens=500, overlap=80):
    toks = ENC.encode(text)
    i = 0
    while i < len(toks):
        j = min(i + max_tokens, len(toks))
        yield ENC.decode(toks[i:j])
        i = j - overlap

def embed_texts(client: OpenAI, chunks: list[str]):
    resp = client.embeddings.create(model=EMBED_MODEL, input=chunks)
    return [v.embedding for v in resp.data]

def build_for(name: str, base_url: str):
    print(f"• {name}: discovering pages from {base_url}")
    urls = discover_urls(base_url)
    if not urls:
        print("  (!) No URLs discovered — check domain or adjust KEEP_PATTERNS.")
        return
    print(f"  found {len(urls)} URLs; fetching & chunking…")

    docs, metas = [], []
    for u in urls:
        try:
            title, text = fetch_clean(u)
            for ch in chunk(text):
                docs.append(ch); metas.append({"url": u, "title": title})
        except Exception as e:
            print("   skip", u, str(e)[:80])

    if not docs:
        print("  (!) No content scraped.")
        return

    client = OpenAI()
    vecs = embed_texts(client, docs)
    X = np.array(vecs, dtype="float32")
    faiss.normalize_L2(X)
    index = faiss.IndexFlatIP(DIM)
    index.add(X)

    faiss.write_index(index, f"{IDX_DIR}/{name}.faiss")
    with open(f"{IDX_DIR}/{name}.jsonl", "w") as f:
        for d, m in zip(docs, metas):
            f.write(json.dumps({"text": d, **m}) + "\n")
    print(f"  ✓ Built {name}: {len(docs)} chunks from {len(urls)} pages")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated council names to build", default="")
    args = ap.parse_args()

    data = json.load(open("councils.json"))
    names = list(data.keys())
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        names = [n for n in names if n in wanted]

    t0 = time.time()
    for name in names:
        try:
            build_for(name, data[name])
        except Exception as e:
            print("ERROR", name, e)
    print(f"Done in {int(time.time()-t0)}s")
