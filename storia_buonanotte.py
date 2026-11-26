from datetime import datetime
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
    "https://www.vaticannews.va/it/rss.xml",
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
    {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Accept-Language": "it-IT,it;q=0.9",
    },
]


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
                if len(corpo) >= 100:
                    return titolo or "Vangelo del giorno", corpo
    return "", ""


def estrai_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    titolo_j, corpo_j = estrai_json_ld(soup)
    if len(corpo_j) >= 100:
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
    if len(testo_unito) >= 100:
        return titolo, testo_unito
    return titolo, ""


def trova_link_vangelo(base_url, html_content):
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
                if len(testo) >= 50:
                    return titolo, testo[:4500]
        except Exception:
            continue
    return "", ""


def recupera_da_vatican():
    candidati = []
    visti = set()
    for url in PAGINE_VATICAN:
        try:
            r = scarica_url(url, timeout=25)
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 120:
                return titolo, testo[:4500]
            for link in trova_link_vangelo(url, r.text):
                if link not in candidati:
                    candidati.append(link)
        except Exception:
            continue

    for link in candidati[:12]:
        if link in visti:
            continue
        visti.add(link)
        try:
            r = scarica_url(link, timeout=25)
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 120:
                return titolo, testo[:4500]
        except Exception:
            continue

    return "", ""


def recupera_vangelo():
    titolo, testo = recupera_da_feed()
    if testo:
        return titolo, testo

    titolo, testo = recupera_da_vatican()
    if testo:
        return titolo, testo

    oggi = datetime.now().strftime("%d/%m/%Y")
    return (
        f"Vangelo del {oggi}",
        f"Brano del Vangelo del giorno per la data {oggi} (rilevato in modalità di riserva).",
    )


def modelli_candidati_groq(groq_key):
    headers = {"Authorization": f"Bearer {groq_key}"}
    resp = requests.get(GROQ_MODELS_URL, headers=headers, timeout=25)
    if not resp.ok:
        raise RuntimeError(f"Errore lettura modelli Groq ({resp.status_code}): {resp.text[:300]}")

    dati = resp.json().get("data", [])
    ids_disponibili = [m.get("id") for m in dati if isinstance(m, dict) and m.get("id")]

    esclusi = ("whisper", "guard", "tts", "embed", "orpheus", "playai")
    modelli_chat = [
        mid for mid in ids_disponibili
        if not any(bad in mid.lower() for bad in esclusi)
    ]

    preferiti = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "gemma2-9b-it",
    ]
    ordinati = [m for m in preferiti if m in modelli_chat]
    for m in modelli_chat:
        if m not in ordinati:
            ordinati.append(m)

    if not ordinati:
        raise RuntimeError(f"Nessun modello testo trovato su Groq. Disponibili: {ids_disponibili[:10]}")
    return ordinati


def genera_storia(titolo, vangelo, groq_key):
    candidati = modelli_candidati_groq(groq_key)

    prompt_sistema = (
        "Sei un narratore dolce e sereno che scrive storie della buonanotte "
        "per bambini di 7-8 anni partendo fedelmente dal racconto del Vangelo del giorno. "
        "Regole fondamentali:\n"
        "1) Racconta l'episodio, la parabola o il messaggio evangelico del Vangelo "
        "del giorno (se è indicata solo la data o un testo breve, usa il Vangelo del "
        "giorno corrispondente a quella data), adattato con dolcezza per i bambini, "
        "senza inventare corsi, convegni o vicende estranee al brano.\n"
        "2) Usa un linguaggio semplice, caldo e rassicurante, senza toni da predica "
        "e senza scene spaventose.\n"
        "3) Scrivi una storia completa di circa 220-280 parole.\n"
        "4) Concludi sempre con una frase completa e tranquilla di buonanotte, senza "
        "mai troncare una parola o una frase a metà."
    )
    prompt_utente = (
        f"Riferimento del Vangelo: {titolo}\n\n"
        f"Testo o indicazione del Vangelo del giorno:\n{vangelo}\n\n"
        "Scrivi ora la storia della buonanotte completa per stasera."
    )

    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json",
    }

    errori = []
    for modello in candidati[:6]:
        payload = {
            "model": modello,
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_utente},
            ],
            "temperature": 0.65,
            "max_tokens": 1100,
        }
        resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=45)
        if resp.ok:
            storia = resp.json()["choices"][0]["message"]["content"].strip()
            if storia:
                return f"🌙 *Buonanotte dal Vangelo*\n_{titolo}_\n\n{storia}"
            errori.append(f"{modello}: risposta vuota")
        else:
            errori.append(f"{modello} ({resp.status_code}): {resp.text[:160]}")

    raise RuntimeError("Nessun modello Groq ha completato la richiesta -> " + " | ".join(errori))


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
