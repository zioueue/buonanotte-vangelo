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

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"
GROQ_MODELS_URL = f"{GROQ_BASE_URL}/models"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


def pulisci(valore):
    testo = html.unescape(valore or "")
    testo = re.sub(r"<[^>]+>", " ", testo)
    return re.sub(r"\s+", " ", testo).strip()


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
                if len(corpo) >= 150:
                    return titolo or "Vangelo del giorno", corpo
    return "", ""


def estrai_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    titolo_j, corpo_j = estrai_json_ld(soup)
    if len(corpo_j) >= 150:
        return titolo_j, corpo_j

    h1 = soup.find("h1")
    titolo = pulisci(h1.get_text()) if h1 and pulisci(h1.get_text()) else "Vangelo del giorno"

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    righe = []
    for el in soup.find_all(["p", "h2", "h3", "blockquote"]):
        testo = pulisci(el.get_text())
        if len(testo) >= 15 and testo not in righe:
            righe.append(testo)

    testo_unito = "\n\n".join(righe).strip()
    if len(testo_unito) >= 120:
        return titolo, testo_unito
    return titolo, ""


def trova_link(base_url, html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    trovati = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip()).split("#")[0]
        low = full.lower()
        txt = a.get_text(" ", strip=True).lower()
        if "vaticannews.va/it/" in low and low.endswith(".html"):
            if any(k in low or k in txt for k in ("vangelo", "parola-del-giorno", "liturgia")):
                if not any(b in low for b in ("/podcast/", "/audio/", "/video/")):
                    if full not in trovati:
                        trovati.append(full)
    return trovati


def recupera_vatican():
    candidati = []
    visti = set()
    for url in PAGINE_VATICAN:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 150 and "vangelo" in (titolo + " " + testo[:400]).lower():
                return titolo, testo[:4000]
            for link in trova_link(url, r.text):
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
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 150:
                return titolo, testo[:4000]
        except Exception:
            continue

    raise ValueError("Nessun testo trovato su Vatican News.")


def recupera_riserva():
    for feed_url in FEED_RISERVA:
        try:
            r = requests.get(feed_url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            for voce in parsed.entries[:5]:
                titolo = pulisci(voce.get("title") or "Vangelo del giorno")
                grezzo = (
                    voce.get("summary")
                    or voce.get("description")
                    or (voce.get("content", [{}])[0].get("value", ""))
                    or ""
                )
                testo = pulisci(grezzo)
                if len(testo) >= 120:
                    return titolo, testo[:4000]
        except Exception:
            continue
    raise ValueError("Nessun feed di riserva disponibile.")


def recupera_vangelo():
    try:
        return recupera_vatican()
    except Exception:
        return recupera_riserva()


def scegli_modello_groq(groq_key):
    headers = {"Authorization": f"Bearer {groq_key}"}
    resp = requests.get(GROQ_MODELS_URL, headers=headers, timeout=25)
    if not resp.ok:
        raise RuntimeError(f"Errore lettura modelli Groq ({resp.status_code}): {resp.text[:300]}")

    dati = resp.json().get("data", [])
    ids_disponibili = [m.get("id") for m in dati if isinstance(m, dict) and m.get("id")]

    modelli_chat = [
        mid for mid in ids_disponibili
        if not any(escluso in mid.lower() for escluso in ("whisper", "guard", "tts", "embed"))
    ]

    preferiti = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "gemma2-9b-it",
    ]
    for pref in preferiti:
        if pref in modelli_chat:
            return pref

    if modelli_chat:
        return modelli_chat[0]

    raise RuntimeError(f"Nessun modello chat trovato su Groq. Disponibili: {ids_disponibili[:10]}")


def genera_storia(titolo, vangelo, groq_key):
    modello = scegli_modello_groq(groq_key)

    prompt_sistema = (
        "Scrivi una dolce storia della buonanotte per bambini (6-8 anni) "
        "ispirata al Vangelo del giorno. Italiano semplice e sereno, senza "
        "prediche o elementi paurosi, circa 200-250 parole, con finale calmo."
    )
    prompt_utente = f"Titolo: {titolo}\n\nVangelo:\n{vangelo}"
    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": modello,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_utente},
        ],
        "temperature": 0.7,
        "max_tokens": 700,
    }
    resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=45)
    if not resp.ok:
        raise RuntimeError(f"Errore Groq con modello '{modello}' ({resp.status_code}): {resp.text[:300]}")
    storia = resp.json()["choices"][0]["message"]["content"].strip()
    if not storia:
        raise ValueError("Risposta Groq vuota.")
    return f"🌙 *Buonanotte dal Vangelo*\n_{titolo}_\n\n{storia}"


def invia_singolo_messaggio(pezzo, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": pezzo, "parse_mode": "Markdown"}
    resp = requests.post(url, json=payload, timeout=30)
    if not resp.ok:
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Errore Telegram ({resp.status_code}): {resp.text[:300]}")


def invia_telegram(testo, token, chat_id):
    chat_pulito = str(chat_id).strip()
    if not chat_pulito:
        raise ValueError("TELEGRAM_CHAT_ID è vuoto.")

    for i in range(0, len(testo), 3800):
        invia_singolo_messaggio(testo[i:i + 3800], token, chat_pulito)


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    groq_key = os.environ["GROQ_API_KEY"].strip()

    titolo, vangelo = recupera_vangelo()
    storia = genera_storia(titolo, vangelo, groq_key)
    invia_telegram(storia, token, chat_id)


if __name__ == "__main__":
    main()
