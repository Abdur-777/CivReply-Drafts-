# ingest.py — build FAISS indexes for VIC councils from councils.json
import os, json, re, time, faiss, requests, tiktoken
import numpy as np
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"  # 1536 dims
DIM = 1536
ENC = tiktoken.get_encoding("cl100k_base")
IDX_DIR = "indexes"
os.makedirs(IDX_DIR, exist_ok=True)

# Keywords we care about (waste, permits, rates, hours, etc.)
KEEP_PATTERNS = [
    "waste", "recycling", "bin", "hard-rubbish", "hardrubbish", "green-waste",
    "parking", "permit", "permits", "rates", "fees", "payments",
    "contact", "customer", "libraries", "library",
    "animal", "pets", "dogs", "cats",
    "building", "planning", "roads", "footpath",
    "events", "opening-hours", "hours", "collection", "book", "booking", "transfer-station", "tip"
]

# Fallback guesses if sitemap is thin
FALLBACK_PATHS = [
    "/services", "/contact-us", "/contact", "/rates", "/waste-recycling", "/waste-and-recycling",
    "/parking-permits", "/parking/permits", "/libraries", "/building-permits", "/planning-building"
]

def good(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in KEEP_PATTERNS)

def fetch_sitemap_urls(base: str, limit=120) -> list[str]:
    urls = []
    tried = set()
    candidates = [f"{base.rstrip('/')}/sitemap.xml", f"{base.rstrip('/')}/sitemap_index.xml"]
    for sm in candidates:
        if sm in tried: continue
        tried.add(sm)
        try:
            r = requests.get(sm, timeout=20)
            if r.status_code != 200 or not r.text.strip():
                continue
            root = ET.fromstring(r.text)
            # sitemapindex or urlset
            if root.tag.endswith("sitemapindex"):
                for node in root.iter():
                    if node.tag.endswith("loc"):
                        loc = node.text.strip()
                        if loc and loc not in tried:
                            tried.add(loc)
                            try:
                                rr = requests.get(loc, timeout=20)
                                if rr.status_code == 200:
                                    rs = ET.fromstring(rr.text)
                                    for u in rs.iter():
                                        if u.tag.endswith("loc"):
                                            urls.append(u.text.strip())
                            except Exception:
                                pass
            else:
                for node in root.iter():
                    if node.tag.endswith("loc"):
                        urls.append(node.text.strip())
        except Exception:
            continue
    # fallback guesses
    if not urls:
        for path in FALLBACK_PATHS:
            url = base.rstrip("/") + path
            try:
                rr = requests.get(url, timeout=10)
                if rr.status_code == 200:
                    urls.append(url)
            except Exception:
                continue
    # same host only & filter by patterns
    host = urlparse(base).netloc
    urls = [u for u in urls if urlparse(u).netloc.endswith(host)]
    urls = [u for u in urls if good(u)]
    # dedupe + cap
    seen, deduped = set(), []
    for u in urls:
        if u in seen: continue
        seen.add(u); deduped.append(u)
        if len(deduped) >= limit: break
    return deduped

def fetch_clean(url: str) -> tuple[str, str]:
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string if soup.title else url).strip()
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
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
    urls = fetch_sitemap_urls(base_url)
    if not urls:
        print("  (!) No URLs discovered — check domain.")
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

    os.makedirs(IDX_DIR, exist_ok=True)
    faiss.write_index(index, f"{IDX_DIR}/{name}.faiss")
    with open(f"{IDX_DIR}/{name}.jsonl", "w") as f:
        for d, m in zip(docs, metas):
            f.write(json.dumps({"text": d, **m}) + "\n")
    print(f"  ✓ Built {name}: {len(docs)} chunks, {len(urls)} pages")

if __name__ == "__main__":
    data = json.load(open("councils.json"))
    t0 = time.time()
    for name, base in data.items():
        try:
            build_for(name, base)
        except Exception as e:
            print("ERROR", name, e)
    print(f"Done in {int(time.time()-t0)}s")
