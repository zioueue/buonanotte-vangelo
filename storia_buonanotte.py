from datetime import datetime
import html
import json
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import feedparser
import requests

PAGINE_VANGELO = [
    "https://www.vaticannews.va/it/vangelo-del-giorno-e-parola-del-giorno.html",
    "https://www.vaticannews.va/it/chiesa/vangelo-del-giorno.html",
    "https://www.chiesacattolica.it/liturgia-del-giorno/",
]

FEED_VANGELO = [
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
    "in quel tempo",
    "secondo matteo",
    "secondo marco",
    "secondo luca",
    "secondo giovanni",
    "gesù",
    "discepoli",
)

RIFERIMENTO_BIBLICO_RE = re.compile(
    r"\b(?:Mt|Mc|Lc|Gv|Matteo|Marco|Luca|Giovanni)\s+\d{1,2}\s*,\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?",
    re.IGNORECASE,
)

ESCLUSIONI_ARTICOLO = (
    "corso di formazione",
    "generazioni in rete",
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
    if any(escl in insieme for escl in ESCLUSIONI_ARTICOLO):
        return False
    return any(k in insieme for k in INDIZI_VANGELO) or bool(RIFERIMENTO_BIBLICO_RE.search(insieme))


def estrai_riferimento(titolo, testo):
    m = RIFERIMENTO_BIBLICO_RE.search(f"{titolo} {testo}")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def isola_passo_vangelo(testo):
    """Cerca di isolare il brano evangelico partendo da 'In quel tempo' o dal Vangelo."""
    if not testo:
        return ""
    idx_tempo = testo.lower().find("in quel tempo")
    if idx_tempo != -1:
        porzione = testo[idx_tempo:]
        return porzione[:3200].strip()
    return testo[:3200].strip()


def corrisponde_a_oggi(testo_controllo, ora=None):
    rif = ora or datetime.now()
    giorno_num = str(rif.day)
    mese_pad = rif.strftime("%m")
    anno = rif.strftime("%Y")
    formati = (
        rif.strftime("%d/%m/%Y"),
        rif.strftime("%Y-%m-%d"),
        rif.strftime("%d-%m-%Y"),
        f"{giorno_num}/{mese_pad}/{anno}",
    )
    low = testo_controllo.lower()
    if any(f.lower() in low for f in formati):
        return True
    mesi_it = {
        1: "gennaio",
        2: "febbraio",
        3: "marzo",
        4: "aprile",
        5: "maggio",
        6: "giugno",
        7: "luglio",
        8: "agosto",
        9: "settembre",
        10: "ottobre",
        11: "novembre",
        12: "dicembre",
    }
    nome_mese = mesi_it[rif.month]
    return bool(
        re.search(rf"\b0?{rif.day}\s+{nome_mese}(?:\s+{anno})?\b", low)
    )


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
                    return titolo or "Vangelo del giorno", isola_passo_vangelo(corpo)
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
        return titolo, isola_passo_vangelo(testo_unito)
    return titolo, ""


def trova_link_vangelo(base_url, html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    prioritari = []
    secondari = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"].strip()).split("#")[0]
        low = full.lower()
        txt = a.get_text(" ", strip=True).lower()
        if "vaticannews.va/it/" in low or "chiesacattolica.it" in low:
            if any(k in low or k in txt for k in ("vangelo", "parola-del-giorno", "liturgia")):
                if not any(b in low for b in ("/podcast/", "/audio/", "/video/")):
                    if corrisponde_a_oggi(f"{full} {txt}"):
                        if full not in prioritari:
                            prioritari.append(full)
                    elif full not in secondari:
                        secondari.append(full)
    return prioritari + secondari


def recupera_da_feed():
    for feed_url in FEED_VANGELO:
        try:
            r = scarica_url(feed_url, timeout=25)
            parsed = feedparser.parse(r.content)
            voci_ordinate = sorted(
                parsed.entries[:15],
                key=lambda v: (
                    1
                    if corrisponde_a_oggi(
                        f"{v.get('title', '')} {v.get('link', '')} {v.get('published', '')}"
                    )
                    else 0
                ),
                reverse=True,
            )
            for voce in voci_ordinate:
                titolo = pulisci(voce.get("title") or "Vangelo del giorno")
                grezzo = (
                    voce.get("summary")
                    or voce.get("description")
                    or (voce.get("content", [{}])[0].get("value", ""))
                    or ""
                )
                testo = pulisci(grezzo)
                link_voce = pulisci(voce.get("link") or "")

                if len(testo) < 180 and link_voce:
                    try:
                        pagina_voce = scarica_url(link_voce, timeout=20)
                        tit_p, testo_p = estrai_html(pagina_voce.text)
                        if len(testo_p) > len(testo) and ha_indizio_vangelo(tit_p, testo_p):
                            testo = testo_p
                            if tit_p and tit_p != "Vangelo del giorno":
                                titolo = tit_p
                    except Exception:
                        pass

                passo = isola_passo_vangelo(testo)
                if len(passo) >= 80 and ha_indizio_vangelo(titolo, passo):
                    rif = estrai_riferimento(titolo, passo)
                    etichetta = f"{titolo} ({rif})" if rif and rif.lower() not in titolo.lower() else titolo
                    print(f"[INFO] Fonte Vangelo trovata su feed: {feed_url} | {etichetta}")
                    print(f"[INFO] Estratto Vangelo: {passo[:180]}...")
                    return etichetta, passo[:4500]
        except Exception as e:
            print(f"[WARN] Feed non disponibile ({feed_url}): {e}")
            continue
    return "", ""


def recupera_da_pagine():
    candidati = []
    visti = set()
    for url in PAGINE_VANGELO:
        try:
            r = scarica_url(url, timeout=25)
            titolo, testo = estrai_html(r.text)
            if len(testo) >= 150:
                rif = estrai_riferimento(titolo, testo)
                etichetta = f"{titolo} ({rif})" if rif and rif.lower() not in titolo.lower() else titolo
                print(f"[INFO] Fonte Vangelo trovata su pagina: {url} | {etichetta}")
                print(f"[INFO] Estratto Vangelo: {testo[:180]}...")
                return etichetta, testo[:4500]
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
                rif = estrai_riferimento(titolo, testo)
                etichetta = f"{titolo} ({rif})" if rif and rif.lower() not in titolo.lower() else titolo
                print(f"[INFO] Fonte Vangelo trovata su articolo: {link} | {etichetta}")
                print(f"[INFO] Estratto Vangelo: {testo[:180]}...")
                return etichetta, testo[:4500]
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
    raise RuntimeError(
        f"Impossibile recuperare un testo reale del Vangelo per il {oggi} dalle fonti configurate."
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
    oggi = datetime.now().strftime("%d/%m/%Y")

    prompt_sistema = (
        "Sei un narratore dolce e pacato che accompagna i bambini dai 6 ai 9 anni "
        "verso il riposo serale a partire dal Vangelo cattolico del giorno.\n"
        "Regole tassative:\n"
        "1) NON inventare mai personaggi, nomi, luoghi o storie di fantasia estranei "
        "al brano del Vangelo fornito.\n"
        "2) Apri con una citazione breve e fedele del passo del Vangelo del giorno "
        "(tra virgolette), poi racconta con parole semplici e rassicuranti ciò che "
        "dice il brano.\n"
        "3) Scrivi un testo breve e scorrevole di 120-160 parole, adatto a bambini "
        "di 6-9 anni, senza toni spaventosi o prediche.\n"
        "4) Concludi sempre con una frase completa di augurio di serenità e pace "
        "per la notte, senza mai troncare il testo."
    )
    prompt_utente = (
        f"Data: {oggi}\n"
        f"Riferimento del passo: {titolo}\n\n"
        f"Testo del Vangelo del giorno:\n{vangelo}\n\n"
        "Scrivi ora la breve storia della buonanotte con la citazione iniziale e fedele al passo."
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
            "temperature": 0.25,
            "max_tokens": 450,
        }
        resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=45)
        if resp.ok:
            storia = resp.json()["choices"][0]["message"]["content"].strip()
            if storia:
                print(f"[INFO] Storia generata con modello Groq: {modello}")
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
    print(f"[INFO] Telegram OK verso {chat_id} | message_id={resp.json().get('result', {}).get('message_id')}")


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
