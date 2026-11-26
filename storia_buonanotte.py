import html
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests

PAGINE_PARTENZA = [
    "https://www.vaticannews.va/it/vangelo-del-giorno-e-parola-del-giorno.html",
    "https://www.vaticannews.va/it/vangelo-del-giorno.html",
    "https://www.vaticannews.va/it.html",
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

PAROLE_ESCLUSE_URL = ("/podcast/", "/audio/", "/video/", "/fotogallery/")


def estrai_articolo_da_html(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")

    h1 = soup.find("h1")
    titolo = (
        re.sub(r"\s+", " ", h1.get_text(" ", strip=True)).strip()
        if h1
        else "Vangelo del giorno"
    )

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "button"]):
        tag.decompose()

    candidati_blocchi = soup.find_all(["article", "main", "section", "div"])
    miglior_testo = ""

    for blocco in candidati_blocchi:
        testo_blocco = blocco.get_text("\n", strip=True)
        # Evita contenitori giganteschi dell'intera pagina se ci sono blocchi più mirati
        if 250 <= len(testo_blocco) <= 15000:
            righe = [
                re.sub(r"[ \t]+", " ", html.unescape(r)).strip()
                for r in testo_blocco.splitlines()
            ]
            righe = [r for r in righe if len(r) >= 12]
            testo_pulito = "\n\n".join(righe)
            if len(testo_pulito) > len(miglior_testo):
                miglior_testo = testo_pulito

    if not miglior_testo:
        paragrafi = []
        for p in soup.find_all(["p", "h2", "h3"]):
            riga = re.sub(r"\s+", " ", html.unescape(p.get_text(" ", strip=True))).strip()
            if len(riga) >= 12 and riga not in paragrafi:
                paragrafi.append(riga)
        miglior_testo = "\n\n".join(paragrafi)

    return titolo, miglior_testo.strip()


def trova_link_vangelo_in_pagina(base_url: str, html_content: str) -> list[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    prioritari = []
    secondari = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urljoin(base_url, href).split("#")[0]
        url_lower = full_url.lower()
        testo_link = a.get_text(" ", strip=True).lower()

        if "vaticannews.va/it/" not in url_lower or not url_lower.endswith(".html"):
            continue
        if any(esclusa in url_lower for esclusa in PAROLE_ESCLUSE_URL):
            continue

        if "vangelo-del-giorno" in url_lower or "parola-del-giorno" in url_lower:
            if full_url not in prioritari:
                prioritari.append(full_url)
        elif "vangelo" in url_lower or "vangelo" in testo_link:
            if full_url not in secondari:
                secondari.append(full_url)

    ordinati = []
    for u in prioritari + secondari:
        if u not in ordinati:
            ordinati.append(u)
    return ordinati


def recupera_vangelo_vatican_news() -> tuple[str, str]:
    errori = []
    url_da_visitare = []
    url_gia_visti = set()

    for start_url in PAGINE_PARTENZA:
        try:
            resp = requests.get(start_url, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            titolo, testo = estrai_articolo_da_html(resp.text)
            if "vangelo" in (titolo + " " + testo[:500]).lower() and len(testo) >= 250:
                return titolo, testo[:4000]

            for link in trova_link_vangelo_in_pagina(start_url, resp.text):
                if link not in url_gia_visti and link not in url_da_visitare:
                    url_da_visitare.append(link)
        except Exception as exc:
            errori.append(f"{start_url}: {exc}")

    for link in url_da_visitare[:12]:
        url_gia_visti.add(link)
        try:
            resp = requests.get(link, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            titolo, testo = estrai_articolo_da_html(resp.text)
            if len(testo) >= 250:
                return titolo, testo[:4000]
            errori.append(f"{link}: testo troppo corto ({len(testo)} car.)")
        except Exception as exc:
            errori.append(f"{link}: {exc}")

    raise ValueError(
        "Impossibile recuperare un testo completo del Vangelo da Vatican News -> "
        + " | ".join(errori[:6])
    )


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
