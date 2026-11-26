import html
import json
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import feedparser
import requests

PAGINE_VATICAN = [
    "https://www.vaticannews.va/it/vangelo-del-giorno-e-parola-del-giorno.html",
    "https://www.vaticannews.va/it/chiesa/vangelo-del-giorno.html",
    "https://www.vaticannews.va/it.html",
]

FEED_RISERVA = [
    "https://vangelodelgiorno.org/IT/rss",
    "https://www.chiesacattolica.it/feed/",
]

GROQ_API_URL = "https://api.groq.com/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


def pulisci_stringa(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def estrai_testo_da_json_ld(soup: BeautifulSoup) -> tuple[str, str]:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            dati = json.loads(raw)
        except Exception:
            continue

        oggetti = dati if isinstance(dati, list) else [dati]
        for obj in oggetti:
            if not isinstance(obj, dict):
                continue
            titolo = pulisci_stringa(obj.get("headline") or obj.get("name") or "")
            corpo = pulisci_stringa(obj.get("articleBody") or obj.get("description") or "")
            if len(corpo) >= 150:
                return titolo or "Vangelo del giorno", corpo
    return "", ""


def estrai_da_html_vatican(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")

    titolo_json, corpo_json = estrai_testo_da_json_ld(soup)
    if len(corpo_json) >= 150:
        return titolo_json, corpo_json

    h1 = soup.find("h1")
    og_title = soup.find("meta", property
