import os
import re
import json
import hashlib
from io import BytesIO
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


# ============================================================
# TBS GRÓJECKA 91 MONITOR v4
# ============================================================

STATE_FILE = "state.json"

BASE_DOMAIN = "tbswp.pl"

# ------------------------------------------------------------
# STRONY, KTÓRE MAJĄ BYĆ MONITOROWANE
# ------------------------------------------------------------

MONITOR_URLS = [
    # TBS
    "https://www.tbswp.pl/",
    "https://www.tbswp.pl/node/311",
    "https://www.tbswp.pl/aktualnosci",
    "https://www.tbswp.pl/projekty/projekty_inwestycyjne",

    # Strony TBS związane z wynajmem
    "https://www.tbswp.pl/wynajem",
    "https://www.tbswp.pl/wynajem/lokale_mieszkalne",

    # Oficjalne strony Warszawy
    "https://mieszkania.um.warszawa.pl/-/inwestycja-przy-ul-grojeckiej-91-nabor-wnioskow-1",
    "https://um.warszawa.pl/-/tbs-wydluza-termin-skladania-wnioskow-o-najem-lokali-przy-ul-grojeckiej-91",
]


# ------------------------------------------------------------
# SŁOWA KLUCZOWE
# ------------------------------------------------------------

KEYWORDS_RESULT = [
    "lista podstawowa",
    "lista rezerwowa",
    "wyniki naboru",
    "wyniki naborów",
    "wynik naboru",
    "lista rankingowa",
    "lista rankingowa wniosków",
    "lista osób zakwalifikowanych",
    "osoby zakwalifikowane",
    "zakwalifikowani",
    "zakwalifikowane",
    "lista najemców",
    "przydział mieszkań",
    "przydział lokali",
    "ocena punktowa",
    "lista punktowa",
    "punktacja",
    "weryfikacja wniosków",
    "rozstrzygnięcie naboru",
    "rozstrzygnięcie",
    "kwalifikacja wniosków",
    "ostateczna lista",
]

KEYWORDS_INVESTMENT = [
    "grójecka 91",
    "grójeckiej 91",
    "grojecka 91",
    "grojeckiej 91",
]

KEYWORDS_UPDATE = [
    "nabór",
    "naboru",
    "wniosków",
    "wnioskodawców",
    "wyniki",
    "lista",
    "kwalifikacja",
    "punktacja",
]

# Nazwy plików, które mogą być wynikami
RESULT_FILE_KEYWORDS = [
    "grójecka",
    "grojecka",
    "wynik",
    "wyniki",
    "nabór",
    "nabor",
    "lista",
    "ranking",
    "punkt",
    "kwalifik",
    "rezerw",
    "najem",
]


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# NORMALIZACJA
# ============================================================

def normalize(text):
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


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_str(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {
            "pages": {},
            "pdfs": {},
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return {
            "pages": {},
            "pdfs": {},
        }


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ Brak TELEGRAM_TOKEN lub TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

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

    return True


# ============================================================
# HTTP
# ============================================================

def get_url(url):

    try:

        response = SESSION.get(
            url,
            timeout=30,
            allow_redirects=True
        )

        return response

    except Exception as error:

        print(
            f"     ❌ Błąd HTTP: {error}"
        )

        return None


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
            "header",
            "footer",
            "nav",
        ]
    ):

        element.decompose()

    return normalize(
        soup.get_text(" ")
    )


# ============================================================
# PDF
# ============================================================

def extract_pdf_text(data):

    try:

        reader = PdfReader(
            BytesIO(data)
        )

        text = ""

        for page in reader.pages:

            text += "\n"
            text += page.extract_text() or ""

        return normalize(text)

    except Exception as error:

        print(
            f"        ⚠️ Nie udało się odczytać PDF: {error}"
        )

        return ""


def download_pdf(url):

    response = SESSION.get(
        url,
        timeout=60
    )

    response.raise_for_status()

    return response.content


# ============================================================
# LINKI PDF
# ============================================================

def find_pdf_links(html, base_url):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    pdfs = {}

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

        title = (
            link.get_text(
                " ",
                strip=True
            )
            or unquote(
                pdf_url.split("/")[-1]
            )
        )

        pdfs[pdf_url] = {
            "url": pdf_url,
            "title": title,
        }

    return list(
        pdfs.values()
    )


# ============================================================
# CZY PDF JEST INTERESUJĄCY?
# ============================================================

def pdf_looks_relevant(title, url, text):

    combined = normalize(
        f"{title} {url} {text}"
    )

    # Najpierw szukamy inwestycji
    has_investment = any(
        normalize(keyword)
        in combined
        for keyword in KEYWORDS_INVESTMENT
    )

    # Następnie wyników
    has_result = any(
        normalize(keyword)
        in combined
        for keyword in KEYWORDS_RESULT
    )

    # Albo charakterystycznej nazwy pliku
    filename = normalize(
        unquote(
            urlparse(url).path
        )
    )

    has_result_filename = any(
        normalize(keyword)
        in filename
        for keyword in RESULT_FILE_KEYWORDS
    )

    return (
        (has_investment and has_result)
        or
        (has_investment and has_result_filename)
    )


# ============================================================
# ANALIZA STRONY
# ============================================================

def analyse_page(text, url):

    normalized_url = normalize(url)

    critical = [
        keyword
        for keyword in KEYWORDS_RESULT
        if normalize(keyword) in text
    ]

    investment = [
        keyword
        for keyword in KEYWORDS_INVESTMENT
        if normalize(keyword) in text
    ]

    update = [
        keyword
        for keyword in KEYWORDS_UPDATE
        if normalize(keyword) in text
    ]

    # Dedykowana strona inwestycji
    if "node/311" in normalized_url:

        if critical:

            return True, critical

        # Jeśli node/311 przestanie być pusty,
        # również chcemy o tym wiedzieć.
        if investment and update:

            return True, list(
                set(investment + update)
            )

    # Pozostałe strony
    if critical and investment:

        return True, list(
            set(critical + investment)
        )

    return False, []


# ============================================================
# ZMIANA STATUSU STRONY
# ============================================================

def check_page(
    url,
    state,
    first_run,
    alerts
):

    print(
        f"\n🌐 {url}"
    )

    response = get_url(url)

    if response is None:
        return

    status = response.status_code

    print(
        f"     HTTP {status} | "
        f"{response.headers.get('content-type', '')}"
    )

    previous = state["pages"].get(
        url,
        {}
    )

    # --------------------------------------------------------
    # PIERWSZE URUCHOMIENIE
    # --------------------------------------------------------

    if not previous:

        print(
            "     🆕 Pierwsze sprawdzenie — zapisuję stan."
        )

        state["pages"][url] = {
            "status": status,
            "hash": None,
        }

        if status == 200:

            content_type = response.headers.get(
                "content-type",
                ""
            ).lower()

            if "text/html" in content_type:

                text = extract_page_text(
                    response.text
                )

                state["pages"][url]["hash"] = (
                    sha256_str(text)
                )

        return

    # --------------------------------------------------------
    # ZMIANA STATUSU
    # --------------------------------------------------------

    previous_status = previous.get(
        "status"
    )

    if previous_status != status:

        print(
            f"     🔄 ZMIANA STATUSU: "
            f"{previous_status} → {status}"
        )

        # 404 -> 200 jest dla nas SUPER WAŻNE
        if (
            previous_status == 404
            and status == 200
        ):

            alerts.append({
                "type": "status",
                "title": "Strona wyników została uruchomiona!",
                "url": url,
                "keywords": [
                    "404 → 200",
                    "strona ponownie dostępna"
                ],
            })

    # --------------------------------------------------------
    # STRONA 200
    # --------------------------------------------------------

    if status == 200:

        content_type = response.headers.get(
            "content-type",
            ""
        ).lower()

        if "text/html" in content_type:

            text = extract_page_text(
                response.text
            )

            current_hash = sha256_str(
                text
            )

            previous_hash = previous.get(
                "hash"
            )

            if (
                previous_hash
                and previous_hash != current_hash
            ):

                print(
                    "     🔄 ZMIANA TREŚCI"
                )

                is_result, keywords = analyse_page(
                    text,
                    url
                )

                if is_result:

                    alerts.append({
                        "type": "page",
                        "title": "Nowa informacja dotycząca naboru",
                        "url": url,
                        "keywords": keywords,
                    })

            state["pages"][url] = {
                "status": status,
                "hash": current_hash,
            }

    else:

        state["pages"][url] = {
            "status": status,
            "hash": previous.get(
                "hash"
            ),
        }


# ============================================================
# PDF
# ============================================================

def check_pdfs(
    page_url,
    html,
    state,
    first_run,
    alerts
):

    pdfs = find_pdf_links(
        html,
        page_url
    )

    print(
        f"     📄 PDF-y: {len(pdfs)}"
    )

    for pdf in pdfs:

        pdf_url = pdf["url"]
        title = pdf["title"]

        try:

            data = download_pdf(
                pdf_url
            )

        except Exception as error:

            print(
                f"        ❌ PDF: {error}"
            )

            continue

        current_hash = sha256_bytes(
            data
        )

        previous_hash = state["pdfs"].get(
            pdf_url
        )

        text = ""

        # ----------------------------------------------------
        # NOWY PDF
        # ----------------------------------------------------

        if previous_hash is None:

            print(
                f"        🆕 NOWY PDF: {title}"
            )

            text = extract_pdf_text(
                data
            )

            relevant = pdf_looks_relevant(
                title,
                pdf_url,
                text
            )

            if (
                not first_run
                and relevant
            ):

                alerts.append({
                    "type": "pdf",
                    "title": title,
                    "url": pdf_url,
                    "keywords": [
                        "nowy PDF",
                        "wyniki / nabór"
                    ],
                })

            state["pdfs"][pdf_url] = (
                current_hash
            )

        # ----------------------------------------------------
        # ZMIANA PDF
        # ----------------------------------------------------

        elif previous_hash != current_hash:

            print(
                f"        🔄 ZMIANA PDF: {title}"
            )

            text = extract_pdf_text(
                data
            )

            relevant = pdf_looks_relevant(
                title,
                pdf_url,
                text
            )

            if relevant:

                alerts.append({
                    "type": "pdf",
                    "title": title + " — ZMIANA",
                    "url": pdf_url,
                    "keywords": [
                        "zmieniona zawartość PDF"
                    ],
                })

            state["pdfs"][pdf_url] = (
                current_hash
            )


# ============================================================
# GŁÓWNA FUNKCJA
# ============================================================

def main():

    print()
    print("=" * 55)
    print("🚨 TBS GRÓJECKA 91 MONITOR v4")
    print("=" * 55)

    state = load_state()

    first_run = (
        not state["pages"]
        and not state["pdfs"]
    )

    print(
        f"Pierwsze uruchomienie: {first_run}"
    )

    alerts = []

    # --------------------------------------------------------
    # MONITOROWANIE STRON
    # --------------------------------------------------------

    for url in MONITOR_URLS:

        check_page(
            url,
            state,
            first_run,
            alerts
        )

        # Jeśli strona jest TBS i działa,
        # sprawdzamy znajdujące się na niej PDF-y.
        response = None

        try:

            response = SESSION.get(
                url,
                timeout=30
            )

            if (
                response.status_code == 200
                and "text/html"
                in response.headers.get(
                    "content-type",
                    ""
                ).lower()
            ):

                check_pdfs(
                    url,
                    response.text,
                    state,
                    first_run,
                    alerts
                )

        except Exception:
            pass

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    if alerts:

        print()
        print(
            f"🚨 LICZBA ALARMÓW: {len(alerts)}"
        )

        message = (
            "🚨 TBS GRÓJECKA 91\n"
            "NOWA INFORMACJA!\n\n"
        )

        for alert in alerts:

            if alert["type"] == "status":
                icon = "🚨"

            elif alert["type"] == "pdf":
                icon = "📄"

            else:
                icon = "🌐"

            message += (
                f"{icon} "
                f"{alert['title']}\n"
            )

            message += (
                f"{alert['url']}\n"
            )

            if alert.get("keywords"):

                message += (
                    "🔎 "
                    + ", ".join(
                        alert["keywords"]
                    )
                    + "\n"
                )

            message += "\n"

        try:

            telegram(message)

            print(
                "📨 ALARM WYSŁANY DO TELEGRAMA"
            )

        except Exception as error:

            print(
                f"❌ BŁĄD TELEGRAM: {error}"
            )

    else:

        print()
        print(
            "✅ Brak nowych wyników."
        )
        print(
            "Telegram: brak alarmu."
        )

    save_state(state)

    print()
    print(
        "💾 Stan zapisany."
    )
    print("=" * 55)


if __name__ == "__main__":
    main()
