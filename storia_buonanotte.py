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

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
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


def pulisci_stringa(valore):
    testo = html.unescape(valore or "")
    testo = re.sub(r"<[^>]+>", " ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    return testo


def estrai_testo_da_json_ld(soup):
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


def estrai_da_html_vatican(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    titolo_json, corpo_json = estrai_testo_da_json_ld(soup)
    if len(corpo_json) >= 150:
        return titolo_json, corpo_json

    h1 = soup.find("h1")
    og_title = soup.find("meta", attrs={"property": "og:title"})
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc:
        meta_desc = soup.find("meta", attrs={"property": "og:description"})

    if h1 and pulisci_stringa(h1.get_text()):
        titolo = pulisci_stringa(h1.get_text())
    elif og_title and og_title.get("content"):
        titolo = pulisci_stringa(og_title.get("content"))
    else:
        titolo = "Vangelo del giorno"

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    righe = []
    for el in soup.find_all(["p", "h2", "h3", "blockquote"]):
        testo = pulisci_stringa(el.get_text())
        if len(testo) >= 15 and testo not in righe:
            righe.append(testo)

    testo_unito = "\n\n".join(righe).strip()
    if len(testo_unito) >= 120:
        return titolo, testo_unito

    if meta_desc and meta_desc.get("content"):
        desc = pulisci_stringa(meta_desc.get("content"))
        if len(desc) >= 60:
            return titolo, desc

    return titolo, ""


def trova_link_vatican(base_url, html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    trovati = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(base_url, href).split("#")[0]
        low = full.lower()
        txt = a.get_text(" ", strip=True).lower()
        if "vaticannews.va/it/" in low and low.endswith(".html"):
            if any(k in low or k in txt for k in ("vangelo", "parola-del-giorno", "liturgia")):
                if not any(bad in low for bad in ("/podcast/", "/audio/", "/video/")):
                    if full not in trovati:
                        trovati.append(full)
    return trovati


def recupera_da_vatican():
    candidati = []
    visti = set()

    for url in PAGINE_VATICAN:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            titolo, testo = estrai_da_html_vatican(r.text)
            if len(testo) >= 150 and "vangelo" in (titolo + " " + testo[:400]).lower():
                return titolo, testo[:4000]
            for link in trova_link_vatican(url, r.text):
                if link not in candidati:
                    candidati.append(link)
        except Exception:
            continue

    for link in candidati[:10]:
        if link in visti:
            continue
        visti.add(link)
        try:
            r = requests.get(link, headers=HEADERS, timeout=25)
            r.raise_for_status()
            titolo, testo = estrai_da_html_vatican(r.text)
            if len(testo) >= 150:
                return titolo, testo[:4000]
        except Exception:
            continue

    raise ValueError("V
