from datetime import datetime
import html
import json
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import feedparser
import requests

QUERY_RIFERIMENTO = "citazione vangelo cattolico di oggi"

PAGINE_VATICAN = [
    "https://www.vaticannews.va/it/vangelo-del-giorno-e-parola-del-giorno.html",
    "https://www.vaticannews.va/it/chiesa/vangelo-del-giorno.html",
    "https://www.chiesacattolica.it/liturgia-del-giorno/",
]

FEED_RISERVA = [
    "https://vangelodelgiorno.org/IT/rss",
    "https://www.vaticannews.va/it/rss.xml",
    "https://www.chiesacattolica.it/feed/",
]

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"
GROQ_MODELS_URL = f"{GROQ_BASE_URL}/models"

PROFILES_HEADERS = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
    },
]

INDIZI_VANGELO = (
    "vangelo",
    "gesù",
    "discepoli",
    "signore",
    "in quel tempo",
    "secondo matteo",
    "secondo marco",
    "secondo luca",
    "secondo giovanni",
)


def pulisci(valore):
    testo = html.unescape(valore or "")
    testo = re.sub(r"<[^>]+>", " ", testo)
    return re.sub(r"\s+", " ", testo).strip()


def scarica_url(url, timeout=25):
    ultimo_errore = None
    for headers in PROFILES_HEADERS:
        try:
            r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code in (401, 403, 406, 429, 503):
                ultimo_errore = RuntimeError(f"HTTP {r.status_code} su {url}")
                continue
            r.raise_for_status()
            if r.content:
                return r
        except Exception as e:
            ultimo_errore = e
            continue
    if ultimo_errore:
        raise ultimo_errore
    raise RuntimeError(f"Impossibile scaricare {url}")


def ha_indizio_vangelo(titolo, testo):
    insieme = f"{titolo} {testo}".lower()
    return any(k in insieme for k in INDIZI_VANGELO)


def estrai_json_ld(soup):
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
            if isinstance(obj, dict):
                titolo = pulisci(obj.get("headline") or obj.get("name") or "")
                corpo = pulisci(obj.get("articleBody") or obj.get("description") or "")
                if len(corpo) >= 120 and ha_indizio_vangelo(titolo, corpo):
                    return titolo or "Vangelo del giorno", corpo
    return "", ""


def estrai_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    titolo_j, corpo_j = estrai_json_ld(soup)
    if len(corpo_j) >= 120:
        return titolo_j, corpo_j

    h1 = soup.find("h1")
    titolo = pulisci(h1.get_text()) if h1 and pulisci(h1.get_text()) else "Vangelo del giorno"

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    righe = []
    for el in soup.find_all(["p", "h2", "h3", "blockquote"]):
        testo = pulisci(el.get_text())
        if len(testo) >= 20 and testo not in righe:
            righe.append(testo)

    testo_unito = "\n\n".join(righe).strip()
    if len(testo_unito) >= 120 and ha_indizio_vangelo(titolo, testo_unito):
        return titolo, testo_unito
    return titolo, ""


def trova_link_vangelo(base_url, html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    trovati = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip()).split("#")[0]
        low = full.lower()
        txt = a.get_text(" ", strip=True).lower()
        if "vaticannews.va/it/" in low or "chiesacattolica.it" in low:
            if any(k in low or k in txt for k in ("vangelo", "parola-del-giorno", "liturgia")):
                if not any(b in low for b in ("/podcast/", "/audio/", "/video/")):
                    if full not in trovati:
                        trovati.append(full)
    return trovati


def recupera_da_feed():
    for feed_url in FEED_RISERVA:
        try:
            r = scarica_url(feed_url, timeout=25)
            parsed = feedparser.parse(r.content)
            for voce in parsed.entries[:10]:
                titolo = pulisci(voce.get("title") or "Vangelo del giorno")
                grezzo = (
                    voce.get("summary")
                    or voce.get("description")
                    or (voce.get("content", [{}])[0].get("value", ""))
                    or ""
                )
                testo = pulisci(grezzo)
                link_voce = pulisci(voce.get("link") or "")

                if len(testo) < 150 and link_voce:
                    try:
                        pagina_voce = scarica_url(link_voce, timeout=20)
                        tit_p, testo_p = estrai_html(pagina_voce.text)
                        if len(testo_p) > len(testo) and ha_indizio_vangelo(tit_p, testo_p):
                            testo = testo_p
                            if tit_p and tit_p != "Vangelo del giorno":
                                titolo = tit_p
                    except Exception:
                        pass

                if len(testo) >= 80 and ha_indizio_vangelo(titolo, testo):
                    print(f"[INFO] Fonte Vangelo trovata su feed: {feed_url} | {titolo}")
                    return titolo, testo[:4500]
        except Exception as e:
            print(f"[WARN] Feed non disponibile ({feed_url}): {e}")
            continue
    return "", ""


def recupera_da_pagine():
    candidati = []
    visti = set()
    for url in PAGINE_VATICAN:
        try:
            r = scarica_url(url, timeout=25)
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 150:
                print(f"[INFO] Fonte Vangelo trovata su pagina: {url} | {titolo}")
                return titolo, testo[:4500]
            for link in trova_link_vangelo(url, r.text):
                if link not in candidati:
                    candidati.append(link)
        except Exception as e:
            print(f"[WARN] Pagina non disponibile ({url}): {e}")
            continue

    for link in candidati[:12]:
        if link in visti:
            continue
        visti.add(link)
        try:
            r = scarica_url(link, timeout=25)
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 150:
                print(f"[INFO] Fonte Vangelo trovata su articolo: {link} | {titolo}")
                return titolo, testo[:4500]
        except Exception:
            continue

    return "", ""


def recupera_vangelo():
    titolo, testo = recupera_da_feed()
    if testo:
        return titolo, testo

    titolo, testo = recupera_da_pagine()
    if testo:
        return titolo, testo

    oggi = datetime.now().strftime("%d/%m/%Y")
    print(f"[WARN] Nessun testo web recuperato per {oggi}: uso fallback generico.")
    return (
        f"{QUERY_RIFERIMENTO} ({oggi})",
        f"{QUERY_RIFERIMENTO} per la data
