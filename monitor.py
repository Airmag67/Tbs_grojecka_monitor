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
# KONFIGURACJA
# ============================================================

TARGET_URL = "https://www.tbswp.pl/node/311"
MAIN_URL = "https://www.tbswp.pl/"

# Strony, które sprawdzamy bezpośrednio
URLS = [
    MAIN_URL,
    TARGET_URL,
]

STATE_FILE = "state.json"

# Maksymalna liczba dodatkowych stron TBS, które odwiedzimy
MAX_DISCOVERED_PAGES = 30


# ============================================================
# SŁOWNIKI
# ============================================================

# Frazy bardzo mocno wskazujące na wyniki
KEYWORDS_CRITICAL = [
    "lista podstawowa",
    "lista rezerwowa",
    "wyniki naboru",
    "wyniki naborów",
    "wyniki naboru wniosków",
    "ostateczna lista",
    "lista najemców",
    "przydział mieszkań",
    "przydział lokali",
    "osoby zakwalifikowane",
    "osoby zakwalifikowane do najmu",
    "wnioski zakwalifikowane",
    "lista zakwalifikowanych",
    "lista wnioskodawców",
]

# Frazy związane z procesem naboru
KEYWORDS_APPLICATION = [
    "nabór",
    "nabor",
    "wniosek",
    "wnioski",
    "wnioskodawca",
    "wnioskodawców",
    "kwalifikacja",
    "zakwalifikowani",
    "punktacja",
    "punkty",
    "ocena punktowa",
    "weryfikacja wniosków",
]

# Frazy identyfikujące inwestycję
KEYWORDS_INVESTMENT = [
    "grójecka 91",
    "grojecka 91",
    "grójeckiej 91",
    "grojeckiej 91",
    "ul. grójecka",
    "ul grójecka",
    "grójecka",
    "grojecka",
]

# Frazy sugerujące zwykłą aktualizację
KEYWORDS_UPDATE = [
    "aktualizacja",
    "nowy komunikat",
    "nowe ogłoszenie",
    "ważna informacja",
    "informacja dla wnioskodawców",
    "komunikat",
    "ogłoszenie",
]


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36 "
        "TBS-Grojecka91-Monitor/2.0"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}


# ============================================================
# POMOCNICZE
# ============================================================

def normalize(text):
    """
    Normalizuje tekst:
    - małe litery
    - usuwa polskie znaki diakrytyczne
    - usuwa nadmiar spacji
    """

    if not text:
        return ""

    text = text.lower()

    translation = str.maketrans(
        "ąćęłńóśźż",
        "acelnoszz"
    )

    text = text.translate(translation)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def load_state():

    if not os.path.exists(STATE_FILE):
        return {
            "initialized": False,
            "pages": {},
            "pdfs": {},
            "alerts": {},
        }

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            state = json.load(file)

        # zabezpieczenie przed starszym formatem
        state.setdefault("initialized", False)
        state.setdefault("pages", {})
        state.setdefault("pdfs", {})
        state.setdefault("alerts", {})

        return state

    except Exception as error:

        print(
            f"Nie można odczytać state.json: {error}"
        )

        return {
            "initialized": False,
            "pages": {},
            "pdfs": {},
            "alerts": {},
        }


def save_state(state):

    temporary_file = STATE_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temporary_file,
        STATE_FILE
    )


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
# STRONY WWW
# ============================================================

def get_page(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=40,
        allow_redirects=True,
    )

    response.raise_for_status()

    return response.text


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


def find_links(html, base_url):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []

    for tag in soup.find_all(
        "a",
        href=True
    ):

        href = tag.get("href", "").strip()

        if not href:
            continue

        absolute = urljoin(
            base_url,
            href
        )

        # tylko tbswp.pl
        parsed = urlparse(absolute)

        if parsed.netloc.lower() != "www.tbswp.pl":
            continue

        # usuwamy fragment
        absolute = absolute.split("#")[0]

        title = tag.get_text(
            " ",
            strip=True
        )

        links.append({
            "url": absolute,
            "title": title or "",
        })

    unique = {}

    for link in links:
        unique[link["url"]] = link

    return list(
        unique.values()
    )


def find_pdf_links(
    html,
    base_url
):

    links = find_links(
        html,
        base_url
    )

    pdfs = []

    for link in links:

        url = link["url"]

        if ".pdf" in url.lower():

            pdfs.append({
                "url": url,
                "title": link["title"] or "Dokument PDF",
            })

    return pdfs


# ============================================================
# PDF
# ============================================================

def download_pdf(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60,
        allow_redirects=True,
    )

    response.raise_for_status()

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    # Nie ufamy wyłącznie rozszerzeniu URL
    if (
        "pdf" not in content_type
        and not url.lower().endswith(".pdf")
    ):

        print(
            f"     Uwaga: serwer zwrócił "
            f"Content-Type: {content_type}"
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

                pages.append(text)

            except Exception:
                pass

        return normalize(
            "\n".join(pages)
        )

    except Exception as error:

        print(
            f"     Błąd odczytu PDF: {error}"
        )

        return ""


# ============================================================
# ANALIZA
# ============================================================

def find_matches(
    text,
    keywords
):

    return [
        keyword
        for keyword in keywords
        if normalize(keyword) in text
    ]


def analyse_document(
    text,
    source_url,
    title=""
):

    combined = normalize(
        f"{title} {source_url} {text}"
    )

    critical = find_matches(
        combined,
        KEYWORDS_CRITICAL
    )

    investment = find_matches(
        combined,
        KEYWORDS_INVESTMENT
    )

    application = find_matches(
        combined,
        KEYWORDS_APPLICATION
    )

    update = find_matches(
        combined,
        KEYWORDS_UPDATE
    )

    # --------------------------------------------------------
    # 1. MOCNY SYGNAŁ
    # --------------------------------------------------------
    #
    # Jeżeli występuje "lista podstawowa" / "lista rezerwowa"
    # itp., praktycznie mamy wyniki.
    #

    if critical:

        return {
            "is_result": True,
            "confidence": "HIGH",
            "reason": "critical",
            "critical": critical,
            "investment": investment,
            "application": application,
            "update": update,
        }

    # --------------------------------------------------------
    # 2. LISTA + INWESTYCJA
    # --------------------------------------------------------

    has_list = (
        "lista" in combined
        or "zakwalifikowani" in combined
        or "zakwalifikowanych" in combined
    )

    has_investment = bool(
        investment
    )

    has_application = bool(
        application
    )

    if (
        has_list
        and has_investment
        and has_application
    ):

        return {
            "is_result": True,
            "confidence": "HIGH",
            "reason": "lista + inwestycja + nabor",
            "critical": critical,
            "investment": investment,
            "application": application,
            "update": update,
        }

    # --------------------------------------------------------
    # 3. WYNIKI NA STRONIE NODE/311
    # --------------------------------------------------------
    #
    # Sama zmiana strony NIE wystarcza.
    # Muszą pojawić się konkretne słowa.
    #

    if (
        "node/311" in source_url
        and (
            has_list
            or "wyniki" in combined
            or "zakwalifikowani" in combined
        )
    ):

        return {
            "is_result": True,
            "confidence": "HIGH",
            "reason": "node/311 + frazy wynikowe",
            "critical": critical,
            "investment": investment,
            "application": application,
            "update": update,
        }

    # --------------------------------------------------------
    # 4. SŁABSZY SYGNAŁ
    # --------------------------------------------------------
    #
    # Nie alarmujemy, ale zapisujemy w logach.
    #

    if (
        has_investment
        and (
            has_application
            or update
        )
    ):

        return {
            "is_result": False,
            "confidence": "MEDIUM",
            "reason": "aktualizacja dotycząca naboru",
            "critical": critical,
            "investment": investment,
            "application": application,
            "update": update,
        }

    # --------------------------------------------------------
    # 5. BRAK ISTOTNEGO SYGNAŁU
    # --------------------------------------------------------

    return {
        "is_result": False,
        "confidence": "LOW",
        "reason": "brak wystarczających oznak wyników",
        "critical": critical,
        "investment": investment,
        "application": application,
        "update": update,
    }


# ============================================================
# FORMATOWANIE WYNIKU
# ============================================================

def format_detection(
    result,
    source_type,
    url,
    title=""
):

    if source_type == "PDF":

        icon = "📄"

    else:

        icon = "🌐"

    message = (
        f"{icon} {title or source_type}\n"
        f"{url}\n"
    )

    if result["critical"]:

        message += (
            "🔎 Kluczowe frazy: "
            + ", ".join(
                result["critical"]
            )
            + "\n"
        )

    if result["investment"]:

        message += (
            "🏢 Inwestycja: "
            + ", ".join(
                result["investment"]
            )
            + "\n"
        )

    message += (
        f"🎯 Pewność: {result['confidence']}\n"
    )

    return message


# ============================================================
# GŁÓWNA FUNKCJA
# ============================================================

def main():

    state = load_state()

    first_run = not state["initialized"]

    detections = []

    print(
        "========================================"
    )
    print(
        "TBS GRÓJECKA 91 MONITOR 2.0"
    )
    print(
        "========================================"
    )

    if first_run:

        print(
            "PIERWSZE URUCHOMIENIE — "
            "tworzę stan początkowy."
        )

    # ========================================================
    # LISTA STRON DO SPRAWDZENIA
    # ========================================================

    pages_to_check = list(
        URLS
    )

    discovered_pages = set(
        pages_to_check
    )

    # ========================================================
    # SPRAWDZANIE STRON
    # ========================================================

    checked = 0

    while (
        pages_to_check
        and checked < MAX_DISCOVERED_PAGES
    ):

        page_url = pages_to_check.pop(
            0
        )

        checked += 1

        print(
            f"\n[{checked}] Sprawdzam:"
        )

        print(
            page_url
        )

        try:

            html = get_page(
                page_url
            )

        except Exception as error:

            print(
                f"❌ Błąd pobierania: {error}"
            )

            continue

        page_text = extract_page_text(
            html
        )

        page_hash = sha256_text(
            page_text
        )

        old_hash = state[
            "pages"
        ].get(page_url)

        # ----------------------------------------------------
        # ANALIZA STRONY
        # ----------------------------------------------------

        if old_hash is None:

            print(
                "🆕 Nowa strona — "
                "zapisuję stan."
            )

            state[
                "pages"
            ][page_url] = page_hash

        elif old_hash != page_hash:

            print(
                "🔄 ZMIANA TREŚCI STRONY"
            )

            state[
                "pages"
            ][page_url] = page_hash

            analysis = analyse_document(
                page_text,
                page_url
            )

            if analysis["is_result"]:

                detections.append({
                    "type": "PAGE",
                    "url": page_url,
                    "title": "Nowa treść strony",
                    "analysis": analysis,
                })

                print(
                    "🚨 STRONA ZAWIERA WYNIKI!"
                )

            else:

                print(
                    "ℹ️ Zmiana nie wygląda "
                    "na wyniki."
                )

        else:

            print(
                "✓ Strona bez zmian."
            )

        # ----------------------------------------------------
        # WYKRYWANIE LINKÓW DO PDF
        # ----------------------------------------------------

        pdfs = find_pdf_links(
            html,
            page_url
        )

        print(
            f"📄 PDF-ów: {len(pdfs)}"
        )

        # ----------------------------------------------------
        # PDF
        # ----------------------------------------------------

        for pdf in pdfs:

            pdf_url = pdf["url"]

            try:

                data = download_pdf(
                    pdf_url
                )

            except Exception as error:

                print(
                    f"❌ PDF niedostępny: "
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
                    f"🆕 NOWY PDF: {pdf_url}"
                )

                state[
                    "pdfs"
                ][pdf_url] = current_hash

                # Pierwszy przebieg = tylko zapis
                if first_run:

                    continue

                pdf_text = extract_pdf_text(
                    data
                )

                analysis = analyse_document(
                    pdf_text,
                    pdf_url,
                    pdf["title"]
                )

                if analysis["is_result"]:

                    detections.append({
                        "type": "PDF",
                        "url": pdf_url,
                        "title": pdf["title"],
                        "analysis": analysis,
                    })

            # ------------------------------------------------
            # ZMIENIONY PDF
            # ------------------------------------------------

            elif old_hash != current_hash:

                print(
                    f"🔄 ZMIENIONY PDF: "
                    f"{pdf_url}"
                )

                state[
                    "pdfs"
                ][pdf_url] = current_hash

                pdf_text = extract_pdf_text(
                    data
                )

                analysis = analyse_document(
                    pdf_text,
                    pdf_url,
                    pdf["title"]
                )

                if analysis["is_result"]:

                    detections.append({
                        "type": "PDF",
                        "url": pdf_url,
                        "title": (
                            pdf["title"]
                            + " — ZAKTUALIZOWANO"
                        ),
                        "analysis": analysis,
                    })

            else:

                print(
                    "✓ PDF bez zmian."
                )

        # ----------------------------------------------------
        # ODKRYWANIE KOLEJNYCH STRON TBS
        # ----------------------------------------------------

        links = find_links(
            html,
            page_url
        )

        for link in links:

            link_url = link["url"]

            if (
                link_url
                in discovered_pages
            ):
                continue

            # Nie crawlujemy PDF-ów jako stron
            if ".pdf" in link_url.lower():
                continue

            # Interesują nas głównie strony,
            # które mogą dotyczyć naboru.
            link_text = normalize(
                link["title"]
            )

            if (
                any(
                    word in link_text
                    for word in [
                        "grójecka",
                        "grojecka",
                        "nabór",
                        "nabor",
                        "mieszkania",
                        "wyniki",
                        "lista",
                        "ogłoszenie",
                        "komunikat",
                    ]
                )
            ):

                discovered_pages.add(
                    link_url
                )

                pages_to_check.append(
                    link_url
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
                detection["analysis"],
                detection["type"],
                detection["url"],
                detection["title"]
            )

            message += "\n"

        message += (
            "⚠️ Bot wykrył konkretne frazy "
            "związane z wynikami. "
            "Sprawdź dokument przed podjęciem decyzji."
        )

        # ----------------------------------------------------
        # HASH ALARMU
        # ----------------------------------------------------

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
                    "\n🚨 ALARM TELEGRAM WYSŁANY!"
                )

            except Exception as error:

                print(
                    "\n❌ Błąd Telegram:"
                    f" {error}"
                )

        else:

            print(
                "\n✓ Ten alarm był już wysłany."
            )

    else:

        print(
            "\n✓ Brak wyników."
        )

    # ========================================================
    # KONIEC
    # ========================================================

    state["initialized"] = True

    save_state(
        state
    )

    print(
        "\n✓ Stan zapisany."
    )

    print(
        "========================================"
    )


if __name__ == "__main__":
    main()
