import os
import re
import json
import hashlib
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


# ============================================================
# TBS GRÓJECKA 91 MONITOR v3
# ============================================================

STATE_FILE = "state.json"

TBS_DOMAIN = "www.tbswp.pl"

# ============================================================
# STRONY STARTOWE
# ============================================================

START_URLS = [
    # TBS
    "https://www.tbswp.pl/",
    "https://www.tbswp.pl/node/311",
    "https://www.tbswp.pl/aktualnosci",
    "https://www.tbswp.pl/projekty/projekty_inwestycyjne",

    # Oficjalne strony Warszawy
    "https://mieszkania.um.warszawa.pl/-/inwestycja-przy-ul-grojeckiej-91-nabor-wnioskow-1",
    "https://um.warszawa.pl/-/tbs-wydluza-termin-skladania-wnioskow-o-najem-lokali-przy-ul-grojeckiej-91",
]


# ============================================================
# SŁOWA KLUCZOWE
# ============================================================

# Bardzo mocne oznaki, że znaleźliśmy wyniki
KEYWORDS_RESULT = [
    "lista podstawowa",
    "lista rezerwowa",
    "wyniki naboru",
    "wyniki naborów",
    "wyniki naboru wniosków",
    "wyniki naboru na najem",
    "ostateczna lista",
    "lista najemców",
    "lista zakwalifikowanych",
    "osoby zakwalifikowane",
    "osoby zakwalifikowane do najmu",
    "wnioski zakwalifikowane",
    "lista wnioskodawców",
    "lista osób zakwalifikowanych",
    "lista osób zakwalifikowanych do zawarcia umowy",
]

# Słowa związane z procesem naboru
KEYWORDS_APPLICATION = [
    "nabór",
    "nabor",
    "wniosek",
    "wnioski",
    "wnioskodawca",
    "wnioskodawców",
    "kwalifikacja",
    "zakwalifikowani",
    "zakwalifikowanych",
    "punktacja",
    "punkty",
    "ocena punktowa",
    "weryfikacja wniosków",
    "kryteria",
    "kandydat",
    "kandydaci",
]

# Inwestycja
KEYWORDS_INVESTMENT = [
    "grójecka 91",
    "grojecka 91",
    "grójeckiej 91",
    "grojeckiej 91",
    "ul. grójecka",
    "ul grójecka",
    "grójecka",
    "grojecka",
    "banacha",
]

# Dokumenty / komunikaty
KEYWORDS_UPDATE = [
    "aktualizacja",
    "komunikat",
    "nowy komunikat",
    "ogłoszenie",
    "nowe ogłoszenie",
    "ważna informacja",
    "informacja dla wnioskodawców",
    "informacja",
]

# Nazwy plików, które same w sobie są mocnym sygnałem
KEYWORDS_FILENAME = [
    "lista podstawowa",
    "lista rezerwowa",
    "lista_podstawowa",
    "lista_rezerwowa",
    "wyniki",
    "wyniki naboru",
    "wyniki_naboru",
    "zakwalifikowani",
    "lista wnioskodawców",
    "lista_wnioskodawcow",
]


# ============================================================
# USTAWIENIA CRAWLERA
# ============================================================

MAX_PAGES = 60
MAX_PDFS = 100

# Nie chcemy odwiedzać całego internetu.
# Tylko domeny, które mają znaczenie dla tego naboru.
ALLOWED_DOMAINS = {
    "www.tbswp.pl",
    "tbswp.pl",
    "mieszkania.um.warszawa.pl",
    "um.warszawa.pl",
}


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36 "
        "TBS-Grojecka91-Monitor/3.0"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}


# ============================================================
# NORMALIZACJA
# ============================================================

def normalize(text):
    """
    Ujednolica tekst:
    - małe litery
    - polskie znaki -> bez znaków diakrytycznych
    - wielokrotne spacje -> jedna
    """

    if not text:
        return ""

    text = text.lower()

    translation = str.maketrans(
        "ąćęłńóśźż",
        "acelnoszz"
    )

    text = text.translate(translation)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# STATE
# ============================================================

def empty_state():
    return {
        "initialized": False,
        "pages": {},
        "pdfs": {},
        "alerts": {},
    }


def load_state():

    if not os.path.exists(STATE_FILE):
        return empty_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        state.setdefault(
            "initialized",
            False
        )

        state.setdefault(
            "pages",
            {}
        )

        state.setdefault(
            "pdfs",
            {}
        )

        state.setdefault(
            "alerts",
            {}
        )

        return state

    except Exception as error:

        print(
            f"⚠️ Nie można odczytać state.json: {error}"
        )

        return empty_state()


def save_state(state):

    temp_file = (
        STATE_FILE
        + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


# ============================================================
# HASH
# ============================================================

def sha256_bytes(data):

    return hashlib.sha256(
        data
    ).hexdigest()


def sha256_text(text):

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):

    token = os.environ[
        "TELEGRAM_TOKEN"
    ]

    chat_id = os.environ[
        "TELEGRAM_CHAT_ID"
    ]

    url = (
        "https://api.telegram.org/"
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
# HTTP
# ============================================================

def download(url, timeout=40):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )

    response.raise_for_status()

    return response


def get_page(url):

    response = download(
        url,
        timeout=40
    )

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    print(
        f"     HTTP {response.status_code}"
        f" | {content_type}"
    )

    return response.text


# ============================================================
# HTML
# ============================================================

def extract_page_text(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
        ]
    ):

        element.decompose()

    return normalize(
        soup.get_text(" ")
    )


def absolute_url(
    href,
    base_url
):

    if not href:
        return None

    href = href.strip()

    if href.startswith(
        (
            "mailto:",
            "tel:",
            "javascript:",
            "#",
        )
    ):
        return None

    return urljoin(
        base_url,
        href
    ).split("#")[0]


def is_allowed_domain(url):

    try:

        hostname = urlparse(
            url
        ).netloc.lower()

        return hostname in ALLOWED_DOMAINS

    except Exception:

        return False


def find_links(
    html,
    base_url
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []

    for tag in soup.find_all(
        "a",
        href=True
    ):

        url = absolute_url(
            tag["href"],
            base_url
        )

        if not url:
            continue

        if not is_allowed_domain(
            url
        ):
            continue

        title = tag.get_text(
            " ",
            strip=True
        )

        links.append({
            "url": url,
            "title": title or "",
        })

    unique = {}

    for item in links:

        unique[
            item["url"]
        ] = item

    return list(
        unique.values()
    )


def find_pdfs(
    html,
    base_url
):

    links = find_links(
        html,
        base_url
    )

    pdfs = {}

    for link in links:

        url = link["url"]

        if ".pdf" in url.lower():

            pdfs[url] = {
                "url": url,
                "title": (
                    link["title"]
                    or "Dokument PDF"
                ),
            }

    return list(
        pdfs.values()
    )


# ============================================================
# PDF
# ============================================================

def download_pdf(url):

    response = download(
        url,
        timeout=60
    )

    return response.content


def extract_pdf_text(data):

    try:

        reader = PdfReader(
            BytesIO(data)
        )

        pages = []

        for page in reader.pages:

            try:

                text = (
                    page.extract_text()
                    or ""
                )

                pages.append(
                    text
                )

            except Exception:
                pass

        return normalize(
            "\n".join(pages)
        )

    except Exception as error:

        print(
            f"     ❌ Błąd odczytu PDF: {error}"
        )

        return ""


# ============================================================
# KEYWORD SEARCH
# ============================================================

def matches(
    text,
    keywords
):

    return [
        keyword
        for keyword in keywords
        if normalize(keyword)
        in text
    ]


# ============================================================
# ANALIZA DOKUMENTU
# ============================================================

def analyse(
    text,
    url,
    title=""
):

    normalized_url = normalize(
        url
    )

    normalized_title = normalize(
        title
    )

    combined = normalize(
        f"{normalized_url} "
        f"{normalized_title} "
        f"{text}"
    )

    result_words = matches(
        combined,
        KEYWORDS_RESULT
    )

    application_words = matches(
        combined,
        KEYWORDS_APPLICATION
    )

    investment_words = matches(
        combined,
        KEYWORDS_INVESTMENT
    )

    update_words = matches(
        combined,
        KEYWORDS_UPDATE
    )

    filename_words = matches(
        normalized_url,
        KEYWORDS_FILENAME
    )

    # --------------------------------------------------------
    # BARDZO MOCNY SYGNAŁ
    # --------------------------------------------------------

    if result_words:

        return {
            "is_result": True,
            "confidence": "VERY HIGH",
            "reason": "fraza wynikowa",
            "result": result_words,
            "investment": investment_words,
            "application": application_words,
            "filename": filename_words,
            "update": update_words,
        }

    # --------------------------------------------------------
    # NAZWA PDF SAMA SUGERUJE WYNIKI
    # --------------------------------------------------------

    if filename_words:

        return {
            "is_result": True,
            "confidence": "VERY HIGH",
            "reason": "nazwa pliku sugeruje wyniki",
            "result": result_words,
            "investment": investment_words,
            "application": application_words,
            "filename": filename_words,
            "update": update_words,
        }

    # --------------------------------------------------------
    # LISTA + INWESTYCJA + NABÓR
    # --------------------------------------------------------

    has_list = (
        "lista" in combined
        or "zakwalifikowani" in combined
        or "zakwalifikowanych" in combined
    )

    if (
        has_list
        and investment_words
        and application_words
    ):

        return {
            "is_result": True,
            "confidence": "HIGH",
            "reason": (
                "lista + inwestycja + nabór"
            ),
            "result": result_words,
            "investment": investment_words,
            "application": application_words,
            "filename": filename_words,
            "update": update_words,
        }

    # --------------------------------------------------------
    # NODE/311 + WYNIKI
    # --------------------------------------------------------

    if (
        "/node/311" in normalized_url
        and (
            "wyniki" in combined
            or "lista podstawowa" in combined
            or "lista rezerwowa" in combined
            or "zakwalifikowani" in combined
        )
    ):

        return {
            "is_result": True,
            "confidence": "HIGH",
            "reason": (
                "node/311 + frazy wynikowe"
            ),
            "result": result_words,
            "investment": investment_words,
            "application": application_words,
            "filename": filename_words,
            "update": update_words,
        }

    # --------------------------------------------------------
    # NOWA INFORMACJA O NABORZE
    # --------------------------------------------------------

    if (
        investment_words
        and application_words
        and update_words
    ):

        return {
            "is_result": False,
            "confidence": "MEDIUM",
            "reason": (
                "nowa informacja dotycząca naboru"
            ),
            "result": result_words,
            "investment": investment_words,
            "application": application_words,
            "filename": filename_words,
            "update": update_words,
        }

    # --------------------------------------------------------
    # BRAK
    # --------------------------------------------------------

    return {
        "is_result": False,
        "confidence": "LOW",
        "reason": "brak oznak wyników",
        "result": result_words,
        "investment": investment_words,
        "application": application_words,
        "filename": filename_words,
        "update": update_words,
    }


# ============================================================
# FORMATOWANIE ALARMU
# ============================================================

def format_detection(
    detection
):

    analysis = detection[
        "analysis"
    ]

    icon = (
        "📄"
        if detection["type"] == "PDF"
        else "🌐"
    )

    message = (
        f"{icon} {detection['title']}\n"
        f"{detection['url']}\n\n"
    )

    if analysis["result"]:

        message += (
            "🔎 Wyniki: "
            + ", ".join(
                analysis["result"]
            )
            + "\n"
        )

    if analysis["filename"]:

        message += (
            "📁 Nazwa pliku: "
            + ", ".join(
                analysis["filename"]
            )
            + "\n"
        )

    if analysis["investment"]:

        message += (
            "🏢 Inwestycja: "
            + ", ".join(
                analysis["investment"]
            )
            + "\n"
        )

    message += (
        f"🎯 Pewność: "
        f"{analysis['confidence']}\n"
    )

    message += (
        f"💡 Powód: "
        f"{analysis['reason']}\n"
    )

    return message


# ============================================================
# GŁÓWNA FUNKCJA
# ============================================================

def main():

    state = load_state()

    first_run = not state[
        "initialized"
    ]

    detections = []

    pages_to_check = list(
        START_URLS
    )

    discovered_pages = set(
        START_URLS
    )

    discovered_pdfs = set()

    checked_pages = 0

    print()
    print(
        "=============================================="
    )
    print(
        "🚨 TBS GRÓJECKA 91 MONITOR v3"
    )
    print(
        "=============================================="
    )

    print(
        f"Pierwsze uruchomienie: "
        f"{first_run}"
    )

    # ========================================================
    # CRAWLER
    # ========================================================

    while (
        pages_to_check
        and checked_pages < MAX_PAGES
    ):

        page_url = pages_to_check.pop(
            0
        )

        checked_pages += 1

        print()
        print(
            f"[{checked_pages}/{MAX_PAGES}] "
            f"🌐 {page_url}"
        )

        try:

            html = get_page(
                page_url
            )

        except Exception as error:

            print(
                f"     ❌ Błąd strony: {error}"
            )

            continue

        # ----------------------------------------------------
        # TEKST STRONY
        # ----------------------------------------------------

        page_text = extract_page_text(
            html
        )

        page_hash = sha256_text(
            page_text
        )

        old_page_hash = state[
            "pages"
        ].get(page_url)

        if old_page_hash is None:

            print(
                "     🆕 Nowa strona — "
                "zapisuję stan."
            )

            state[
                "pages"
            ][page_url] = page_hash

        elif old_page_hash != page_hash:

            print(
                "     🔄 ZMIANA TREŚCI!"
            )

            state[
                "pages"
            ][page_url] = page_hash

            analysis = analyse(
                page_text,
                page_url
            )

            print(
                f"     Analiza: "
                f"{analysis['confidence']} "
                f"/ {analysis['reason']}"
            )

            if analysis[
                "is_result"
            ]:

                detections.append({
                    "type": "PAGE",
                    "url": page_url,
                    "title": (
                        "Nowa treść strony"
                    ),
                    "analysis": analysis,
                })

        else:

            print(
                "     ✓ Tekst bez zmian."
            )

        # ----------------------------------------------------
        # PDF
        # ----------------------------------------------------

        pdfs = find_pdfs(
            html,
            page_url
        )

        print(
            f"     📄 PDF-y znalezione: "
            f"{len(pdfs)}"
        )

        for pdf in pdfs:

            if len(
                discovered_pdfs
            ) >= MAX_PDFS:

                break

            pdf_url = pdf[
                "url"
            ]

            if pdf_url in discovered_pdfs:

                continue

            discovered_pdfs.add(
                pdf_url
            )

            print(
                f"     → PDF: "
                f"{pdf_url}"
            )

            try:

                data = download_pdf(
                    pdf_url
                )

            except Exception as error:

                print(
                    f"       ❌ Nie można pobrać: "
                    f"{error}"
                )

                continue

            current_hash = sha256_bytes(
                data
            )

            old_hash = state[
                "pdfs"
            ].get(pdf_url)

            # ------------------------------------------------
            # NOWY PDF
            # ------------------------------------------------

            if old_hash is None:

                print(
                    "       🆕 NOWY PDF"
                )

                state[
                    "pdfs"
                ][pdf_url] = current_hash

                # Pierwsze uruchomienie:
                # tylko zapisujemy stan.
                if first_run:

                    continue

                pdf_text = extract_pdf_text(
                    data
                )

                analysis = analyse(
                    pdf_text,
                    pdf_url,
                    pdf["title"]
                )

                print(
                    f"       Analiza: "
                    f"{analysis['confidence']} "
                    f"/ {analysis['reason']}"
                )

                if analysis[
                    "is_result"
                ]:

                    detections.append({
                        "type": "PDF",
                        "url": pdf_url,
                        "title": pdf["title"],
                        "analysis": analysis,
                    })

            # ------------------------------------------------
            # ZMIANA PDF
            # ------------------------------------------------

            elif old_hash != current_hash:

                print(
                    "       🔄 PDF ZMIENIONY"
                )

                state[
                    "pdfs"
                ][pdf_url] = current_hash

                pdf_text = extract_pdf_text(
                    data
                )

                analysis = analyse(
                    pdf_text,
                    pdf_url,
                    pdf["title"]
                )

                print(
                    f"       Analiza: "
                    f"{analysis['confidence']} "
                    f"/ {analysis['reason']}"
                )

                if analysis[
                    "is_result"
                ]:

                    detections.append({
                        "type": "PDF",
                        "url": pdf_url,
                        "title": (
                            pdf["title"]
                            + " — ZMIENIONO"
                        ),
                        "analysis": analysis,
                    })

            else:

                print(
                    "       ✓ PDF bez zmian."
                )

        # ----------------------------------------------------
        # ODKRYWANIE KOLEJNYCH STRON
        # ----------------------------------------------------

        links = find_links(
            html,
            page_url
        )

        for link in links:

            link_url = link[
                "url"
            ]

            if link_url in discovered_pages:
                continue

            if ".pdf" in link_url.lower():
                continue

            link_text = normalize(
                link["title"]
            )

            url_text = normalize(
                link_url
            )

            searchable = (
                link_text
                + " "
                + url_text
            )

            # Strona jest interesująca,
            # jeśli wygląda na związaną
            # z naborem / Grójecką.
            interesting = any(
                word in searchable
                for word in [
                    "grojecka",
                    "grójecka",
                    "nabor",
                    "nabór",
                    "wyniki",
                    "lista",
                    "wniosek",
                    "wnioski",
                    "mieszkania",
                    "najem",
                    "wnioskodawca",
                    "wnioskodawcy",
                    "komunikat",
                    "ogloszenie",
                    "ogłoszenie",
                ]
            )

            if interesting:

                discovered_pages.add(
                    link_url
                )

                pages_to_check.append(
                    link_url
                )

    # ========================================================
    # DIAGNOSTYKA
    # ========================================================

    print()
    print(
        "=============================================="
    )
    print(
        "📊 PODSUMOWANIE"
    )
    print(
        "=============================================="
    )

    print(
        f"🌐 Sprawdzone strony: "
        f"{checked_pages}"
    )

    print(
        f"📄 Unikalne PDF-y: "
        f"{len(discovered_pdfs)}"
    )

    print(
        f"🚨 Wykryte wyniki: "
        f"{len(detections)}"
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    if detections:

        message = (
            "🚨🚨🚨 TBS GRÓJECKA 91 🚨🚨🚨\n\n"
            "WYKRYTO MOŻLIWE WYNIKI NABORU!\n\n"
        )

        for detection in detections:

            message += format_detection(
                detection
            )

            message += "\n"

        message += (
            "👉 Sprawdź dokument/stronę "
            "bezpośrednio.\n"
        )

        # Hash alarmu
        alert_hash = sha256_text(
            message
        )

        if (
            alert_hash
            not in state["alerts"]
        ):

            try:

                telegram(
                    message
                )

                state[
                    "alerts"
                ][alert_hash] = True

                print(
                    "📱 🚨 TELEGRAM: WYSŁANO!"
                )

            except Exception as error:

                print(
                    f"📱 ❌ TELEGRAM ERROR: "
                    f"{error}"
                )

        else:

            print(
                "📱 ✓ Ten sam alarm "
                "został już wysłany."
            )

    else:

        print(
            "📱 Telegram: "
            "brak alarmu."
        )

    # ========================================================
    # ZAPIS
    # ========================================================

    state[
        "initialized"
    ] = True

    save_state(
        state
    )

    print(
        "💾 State zapisany."
    )

    print(
        "=============================================="
    )


if __name__ == "__main__":
    main()
