# ingest.py — robust & low-memory index builder for VIC councils
# - Uses sitemaps + manual seeds + light BFS crawl
# - Embeds incrementally in small batches (low RAM)
#
# Examples:
#   python3 ingest.py --only "Wyndham City Council" --limit-pages 40 --max-chunks 600 --batch 12
#   python3 ingest.py  # build all councils from councils.json with safe defaults

import os, json, re, time, gzip, argparse, requests, faiss, numpy as np, tiktoken
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET
from openai import OpenAI

EMBED_MODEL = "text-embedding-3-small"  # 1536 dims
DIM = 1536
ENC = tiktoken.get_encoding("cl100k_base")
IDX_DIR = "indexes"
os.makedirs(IDX_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Pages we care about (adjust as you learn what residents ask)
KEEP_PATTERNS = [
    "waste","recycling","bin","hard-waste","hardwaste","hard-rubbish","green-waste",
    "parking","permit","permits","rates","fees","payments","pay","fines",
    "contact","customer","libraries","library","opening-hours","hours","holiday",
    "animal","pets","dogs","cats","registration",
    "building","planning","building-permit","building-permits","roads","footpath","maintenance","graffiti",
    "events","collection","book","booking","transfer-station","tip","landfill","garbage"
]

# Generic fallbacks if sitemaps are thin
FALLBACK_PATHS = [
    "/services","/contact-us","/contact","/contact-us/","/contact/","/contactus",
    "/rates","/rates-and-valuation","/waste-recycling","/waste-and-recycling",
    "/parking-permits","/parking/permits","/parking",
    "/libraries","/library",
    "/building-permits","/planning-building","/planning-and-building","/building","/planning"
]

# --------- MANUAL SEEDS (major VIC councils) ----------
# Use some domain-specific paths for tricky sites, otherwise generic service hubs.
# Seeds can be relative (we'll urljoin) or absolute. BFS will expand from them.
MANUAL_SEEDS = {
    # Inner Melbourne
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
    "https://www.portphillip.vic.gov.au": [
        "/residents/waste-and-recycling",
        "/residents/parking-permits",
        "/planning-and-building/building",
        "/residents/rates",
        "/council/about-the-council/contact-us"
    ],
    "https://www.stonnington.vic.gov.au": [
        "/residents-and-families/waste-and-recycling",
        "/residents-and-families/parking/parking-permits",
        "/planning-and-building/building-and-permits",
        "/council/rates-and-valuations",
        "/libraries",
        "/council/contact-us"
    ],
    "https://www.gleneira.vic.gov.au": [
        "/services/rubbish-and-recycling",
        "/services/parking/permits",
        "/services/planning-and-building/building",
        "/services/rates-and-valuations",
        "/libraries",
        "/about-council/contact-us"
    ],
    "https://www.boroondara.vic.gov.au": [
        "/services/rubbish-waste-and-recycling",
        "/services/parking/permits",
        "/building-and-development/building-permits",
        "/about-council/contact-us",
        "/services/rates"
    ],

    # SE Metro
    "https://
