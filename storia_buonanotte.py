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

    raise ValueError("Vatican News non ha restituito un testo completo via HTTP statico.")


def recupera_da_riserva():
    for feed_url in FEED_RISERVA:
        try:
            r = requests.get(feed_url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            for voce in parsed.entries[:5]:
                titolo = pulisci_stringa(voce.get("title") or "Vangelo del giorno")
                grezzo = (
                    voce.get("summary")
                    or voce.get("description")
                    or (voce.get("content", [{}])[0].get("value", ""))
                    or ""
                )
                testo = pulisci_stringa(grezzo)
                if len(testo) >= 120:
                    return titolo, testo[:4000]
        except Exception:
            continue

    raise ValueError("Nessuna fonte disponibile al momento per il Vangelo del giorno.")


def recupera_vangelo():
    try:
        return recupera_da_vatican()
    except Exception:
        return recupera_da_riserva()


def genera_storia(titolo, vangelo, groq_key):
    system_prompt = (
        "Sei un narratore dolce e sereno che scrive storie della buonanotte "
        "per bambini (circa 6-8 anni) ispirate al Vangelo del giorno. "
        "Scrivi in italiano semplice, caldo e immaginativo, senza toni da predica "
        "e senza elementi che facciano paura. La storia deve durare circa 2 minuti "
        "di lettura (circa 200-280 parole), avere un piccolo messaggio positivo "
        "legato al brano e concludersi con un finale calmo che inviti al riposo."
    )
    user_prompt = (
        f"Titolo/riferimento del giorno: {titolo}\n\n"
        f"Testo del Vangelo del giorno:\n{vangelo}\n\n"
        "Scrivi ora la storia della buonanotte per stasera."
    )

    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 700,
    }
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    storia = data["choices"][0]["message"]["content"].strip()
    if not storia:
        raise ValueError("La risposta di Groq è vuota.")
    return f"🌙 *Buonanotte dal Vangelo*\n_{titolo}_\n\n{storia}"


def invia_telegram(testo, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": testo,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=30)
    if not resp.ok:
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    groq_key = os.environ["GROQ_API_KEY"]

    titolo, vangelo = recupera_vangelo()
    storia = genera_storia(titolo, vangelo, groq_key)
    invia_telegram(storia, token, chat_id)


if __name__ == "__main__":
    main()
