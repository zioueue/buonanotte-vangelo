import html
import os
import re
from bs4 import BeautifulSoup
import requests

VATICAN_NEWS_URLS = [
    "https://www.vaticannews.va/it/vangelo-del-giorno.html",
    "https://www.vaticannews.va/it/vangelo-del-giorno.rss.xml",
]
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


def pulisci_testo(raw_text: str) -> str:
    soup = BeautifulSoup(html.unescape(raw_text), "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    testo = soup.get_text(separator="\n")
    righe = [re.sub(r"[ \t]+", " ", riga).strip() for riga in testo.splitlines()]
    righe = [r for r in righe if r]
    return "\n".join(righe)


def estrai_da_pagina_html(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")

    # Cerca il titolo principale della pagina
    h1 = soup.find("h1")
    titolo = h1.get_text(" ", strip=True) if h1 else "Vangelo del giorno — Vatican News"
    titolo = re.sub(r"\s+", " ", titolo).strip()

    # Cerca il contenitore del testo dell'articolo/vangelo
    contenitore = (
        soup.find("section", class_=re.compile(r"editorial|body|text", re.I))
        or soup.find("div", class_=re.compile(r"editorial|body|text|content", re.I))
        or soup.find("article")
        or soup.body
    )

    if not contenitore:
        return titolo, ""

    for tag in contenitore(["script", "style", "nav", "header", "footer", "aside", "button"]):
        tag.decompose()

    paragrafi = []
    for p in contenitore.find_all(["p", "h2", "h3"]):
        riga = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(riga) >= 20 and riga not in paragrafi:
            paragrafi.append(riga)

    testo = "\n\n".join(paragrafi).strip()
    if not testo:
        testo = pulisci_testo(str(contenitore))

    return titolo, testo


def recupera_vangelo_vatican_news() -> tuple[str, str]:
    errori = []

    for url in VATICAN_NEWS_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=25)
            resp.raise_for_status()

            if url.endswith(".xml"):
                soup = BeautifulSoup(resp.content, "xml")
                item = soup.find("item")
                if not item:
                    errori.append(f"{url}: nessun <item> trovato")
                    continue
                titolo_tag = item.find("title")
                desc_tag = item.find("description")
                titolo = titolo_tag.get_text(" ", strip=True) if titolo_tag else "Vangelo del giorno"
                testo = pulisci_testo(desc_tag.get_text() if desc_tag else "")
                if len(testo) > 80:
                    return titolo, testo[:4000]
                errori.append(f"{url}: testo troppo corto")
            else:
                titolo, testo = estrai_da_pagina_html(resp.text)
                if len(testo) > 80:
                    return titolo, testo[:4000]
                errori.append(f"{url}: testo non estratto dalla pagina")

        except Exception as exc:
            errori.append(f"{url}: {exc}")

    raise ValueError("Impossibile leggere il Vangelo da Vatican News -> " + " | ".join(errori))


def genera_storia(titolo: str, vangelo: str, groq_key: str) -> str:
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
        f"Testo del Vangelo del giorno (da Vatican News):\n{vangelo}\n\n"
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


def invia_telegram(testo: str, token: str, chat_id: str) -> None:
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


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    groq_key = os.environ["GROQ_API_KEY"]

    titolo, vangelo = recupera_vangelo_vatican_news()
    storia = genera_storia(titolo, vangelo, groq_key)
    invia_telegram(storia, token, chat_id)


if __name__ == "__main__":
    main()
    
