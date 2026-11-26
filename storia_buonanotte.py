import html
import os
import re
from bs4 import BeautifulSoup
import feedparser
import requests

RSS_URL = "https://vangelodelgiorno.org/IT/rss"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def pulisci_testo_html(raw_html: str) -> str:
    soup = BeautifulSoup(html.unescape(raw_html), "html.parser")
    testo = soup.get_text(separator="\n")
    righe = [re.sub(r"[ \t]+", " ", riga).strip() for riga in testo.splitlines()]
    righe = [r for r in righe if r]
    return "\n".join(righe)


def recupera_vangelo() -> tuple[str, str]:
    resp = requests.get(RSS_URL, timeout=25)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        raise ValueError("Nessuna voce trovata nel feed RSS del Vangelo.")
    voce = feed.entries[0]
    titolo = voce.get("title", "Vangelo del giorno").strip()
    grezzo = voce.get("summary") or voce.get("description") or ""
    testo = pulisci_testo_html(grezzo)
    if not testo:
        raise ValueError("Testo del Vangelo vuoto.")
    return titolo, testo[:4000]


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
        f"Brano del Vangelo:\n{vangelo}\n\n"
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
    # Se Markdown dà problemi con caratteri speciali, riprova in testo semplice
    if not resp.ok:
        payload.pop("parse_mode", None)
        resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    groq_key = os.environ["GROQ_API_KEY"]

    titolo, vangelo = recupera_vangelo()
    storia = genera_storia(titolo, vangelo, groq_key)
    invia_telegram(storia, token, chat_id)


if __name__ == "__main__":
    main()
  
