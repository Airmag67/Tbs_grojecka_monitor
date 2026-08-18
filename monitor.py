import os
import re
import json
import hashlib
from io import BytesIO
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


# ============================================================
# KONFIGURACJA
# ============================================================

URLS = [
    "https://www.tbswp.pl/node/311",
]

STATE_FILE = "state.json"

KEYWORDS_STRONG = [
    "lista podstawowa",
    "lista rezerwowa",
    "wyniki naboru",
    "wyniki naborów",
]

KEYWORDS_WEAK = [
    "grójecka 91",
    "grójecka",
    "nabor",
    "nabór",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}


# ============================================================
# POMOCNICZE
# ============================================================

def normalize(text):
    """Normalizuje tekst do łatwiejszego wyszukiwania."""

    text = text.lower()

    # zamiana polskich znaków na odpowiedniki bez znaków diakrytycznych
    translation = str.maketrans(
        "ąćęłńóśźż",
        "acelnoszz"
    )

    text = text.translate(translation)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def load_state():
    """Wczytuje zapamiętane dokumenty."""

    if not os.path.exists(STATE_FILE):
        return {
            "pages": {},
            "pdfs": {}
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return {
            "pages": {},
            "pdfs": {}
        }


def save_state(state):
    """Zapisuje stan."""

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


def sha256(data):
    """Liczy hash pliku."""

    return hashlib.sha256(data).hexdigest()


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):
    """Wysyła wiadomość Telegramem."""

    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )

    response.raise_for_status()


# ============================================================
# STRONA WWW
# ============================================================

def get_page(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.text


def extract_page_text(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # usuwamy elementy nieistotne
    for element in soup(
        ["script", "style", "noscript"]
    ):
        element.decompose()

    return normalize(
        soup.get_text(" ")
    )


def find_pdf_links(html, base_url):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    pdfs = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link["href"]

        if ".pdf" not in href.lower():
            continue

        pdf_url = urljoin(
            base_url,
            href
        )

        title = link.get_text(
            " ",
            strip=True
        )

        pdfs.append({
            "url": pdf_url,
            "title": title
        })

    # usuwamy duplikaty
    unique = {}

    for pdf in pdfs:
        unique[pdf["url"]] = pdf

    return list(unique.values())


# ============================================================
# PDF
# ============================================================

def download_pdf(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60
    )

    response.raise_for_status()

    return response.content


def extract_pdf_text(data):

    reader = PdfReader(
        BytesIO(data)
    )

    text = ""

    for page in reader.pages:

        try:
            text += "\n"
            text += page.extract_text() or ""

        except Exception:
            pass

    return normalize(text)


# ============================================================
# ANALIZA WYNIKÓW
# ============================================================

def analyse_text(text):

    strong = [
        keyword
        for keyword in KEYWORDS_STRONG
        if normalize(keyword) in text
    ]

    weak = [
        keyword
        for keyword in KEYWORDS_WEAK
        if normalize(keyword) in text
    ]

    # Najważniejsza zasada:
    #
    # alarmujemy jeżeli:
    #
    # 1. mamy mocną frazę
    #
    # LUB
    #
    # 2. mamy "Grójecka 91" + słowo związane z naborem

    is_result = False

    if strong:
        is_result = True

    elif (
        "grojecka 91" in text
        and (
            "nabor" in text
            or "lista" in text
        )
    ):
        is_result = True

    return {
        "is_result": is_result,
        "strong": strong,
        "weak": weak,
    }


# ============================================================
# GŁÓWNA LOGIKA
# ============================================================

def main():

    state = load_state()

    found_results = []

    print("================================")
    print("TBS GRÓJECKA 91 MONITOR")
    print("================================")

    for page_url in URLS:

        print(f"\nSprawdzam: {page_url}")

        try:

            html = get_page(
                page_url
            )

        except Exception as error:

            print(
                f"Błąd strony: {error}"
            )

            continue

        # ----------------------------------------------------
        # ANALIZA TEKSTU STRONY
        # ----------------------------------------------------

        page_text = extract_page_text(
            html
        )

        analysis = analyse_text(
            page_text
        )

        page_hash = hashlib.sha256(
            html.encode("utf-8")
        ).hexdigest()

        previous_hash = state[
            "pages"
        ].get(page_url)

        state[
            "pages"
        ][page_url] = page_hash

        if analysis["is_result"]:

            found_results.append({
                "type": "page",
                "url": page_url,
                "keywords": analysis["strong"]
            })

        # ----------------------------------------------------
        # SZUKANIE PDF
        # ----------------------------------------------------

        pdfs = find_pdf_links(
            html,
            page_url
        )

        print(
            f"Znaleziono PDF: {len(pdfs)}"
        )

        for pdf in pdfs:

            pdf_url = pdf["url"]

            print(
                f"  → {pdf_url}"
            )

            try:

                data = download_pdf(
                    pdf_url
                )

            except Exception as error:

                print(
                    f"     Nie można pobrać: {error}"
                )

                continue

            file_hash = sha256(
                data
            )

            previous = state[
                "pdfs"
            ].get(pdf_url)

            # ------------------------------------------------
            # JEŻELI PDF JUŻ ZNAMY
            # ------------------------------------------------

            if previous == file_hash:

                print(
                    "     PDF bez zmian."
                )

                continue

            # ------------------------------------------------
            # NOWY PDF LUB ZMIENIONY PDF
            # ------------------------------------------------

            print(
                "     NOWY / ZMIENIONY PDF!"
            )

            text = extract_pdf_text(
                data
            )

            analysis = analyse_text(
                text
            )

            state[
                "pdfs"
            ][pdf_url] = file_hash

            # ------------------------------------------------
            # WYNIKI
            # ------------------------------------------------

            if analysis["is_result"]:

                found_results.append({
                    "type": "pdf",
                    "url": pdf_url,
                    "title": pdf["title"],
                    "keywords": analysis["strong"]
                })

            else:

                print(
                    "     PDF nie wygląda na wyniki."
                )

    # ========================================================
    # TELEGRAM
    # ========================================================

    if found_results:

        message = (
            "🚨 TBS GRÓJECKA 91 — WYNIKI!\n\n"
            "Bot wykrył publikację dokumentu "
            "mogącego zawierać wyniki naboru.\n\n"
        )

        for result in found_results:

            if result["type"] == "pdf":

                message += (
                    "📄 PDF\n"
                    f"{result['url']}\n"
                )

                if result["title"]:
                    message += (
                        f"Nazwa: {result['title']}\n"
                    )

            else:

                message += (
                    "🌐 STRONA\n"
                    f"{result['url']}\n"
                )

            if result["keywords"]:

                message += (
                    "🔎 Wykryto: "
                    + ", ".join(
                        result["keywords"]
                    )
                    + "\n"
                )

            message += "\n"

        telegram(
            message
        )

        print(
            "\n🚨 WYSŁANO ALARM TELEGRAM!"
        )

    else:

        print(
            "\nBrak wyników."
        )

    save_state(
        state
    )

    print(
        "\nStan zapisany."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
