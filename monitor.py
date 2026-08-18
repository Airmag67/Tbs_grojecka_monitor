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
    "https://www.tbswp.pl/",          # Strona główna portalu
    "https://www.tbswp.pl/node/311",  # Dedykowana strona inwestycji
]

STATE_FILE = "state.json"

KEYWORDS_CRITICAL = [
    "lista podstawowa",
    "lista rezerwowa",
    "wyniki naboru",
    "wyniki naborów",
]

KEYWORDS_INVESTMENT = [
    "grójecka",
    "grójeckiej",
    "grojecka",
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
    text = text.lower()
    translation = str.maketrans("ąćęłńóśźż", "acelnoszz")
    text = text.translate(translation)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"pages": {}, "pdfs": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"pages": {}, "pdfs": {}}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha256_str(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
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


# ============================================================
# STRONA WWW I PDF
# ============================================================

def get_page(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text

def extract_page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "header", "footer"]):
        element.decompose()
    return normalize(soup.get_text(" "))

def find_pdf_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    pdfs = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if ".pdf" not in href.lower():
            continue
        pdf_url = urljoin(base_url, href)
        title = link.get_text(" ", strip=True) or "Dokument PDF"
        pdfs[pdf_url] = {"url": pdf_url, "title": title}
    return list(pdfs.values())

def download_pdf(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response.content

def extract_pdf_text(data):
    try:
        reader = PdfReader(BytesIO(data))
        text = "\n".join([page.extract_text() or "" for page in reader.pages])
        return normalize(text)
    except Exception:
        return ""


# ============================================================
# ANALIZA WYNIKÓW
# ============================================================

def analyse_text(text, source_url):
    critical = [k for k in KEYWORDS_CRITICAL if normalize(k) in text]
    investment = [k for k in KEYWORDS_INVESTMENT if normalize(k) in text]

    is_result = False

    # 1. Strona dedykowana: Każda zmiana jest ważna
    if "node/311" in source_url:
        is_result = True
        
    # 2. Główna strona/PDF: Słowo krytyczne + dotyczy inwestycji
    elif critical and investment:
        is_result = True
        
    # 3. Główna strona/PDF: Słowo inwestycji + słowo o naborze
    elif investment and ("nabor" in text or "wniosek" in text or "lista" in text):
        is_result = True

    return {
        "is_result": is_result,
        "critical": critical,
    }


# ============================================================
# GŁÓWNA LOGIKA
# ============================================================

def main():
    state = load_state()
    is_first_run = not state["pages"] and not state["pdfs"]
    found_results = []

    print("================================")
    print("TBS GRÓJECKA 91 MONITOR")
    print("================================")

    # Wysłanie testowej wiadomości przy pierwszym uruchomieniu
    if is_first_run:
        print("Pierwsze uruchomienie bota - próba wysłania wiadomości testowej...")
        try:
            telegram("✅ Monitor TBS Grójecka 91 został pomyślnie uruchomiony! Bot rozpoczął sprawdzanie stron.")
            print("Wiadomość testowa wysłana pomyślnie.")
        except Exception as e:
            print(f"BŁĄD TELEGRAMA: Nie udało się wysłać wiadomości testowej! Sprawdź Token i Chat ID. Szczegóły: {e}")

    for page_url in URLS:
        print(f"\nSprawdzam: {page_url}")
        
        try:
            html = get_page(page_url)
        except Exception as error:
            print(f"Błąd strony: {error}")
            continue

        page_text = extract_page_text(html)
        page_hash = sha256_str(page_text)
        previous_hash = state["pages"].get(page_url)

        # SPRAWDZANIE ZMIAN NA STRONIE
        if previous_hash is None:
            print("     Zapamiętano stan początkowy strony.")
            state["pages"][page_url] = page_hash
        elif previous_hash != page_hash:
            print("     WYKRYTO ZMIANĘ TEKSTU NA STRONIE!")
            state["pages"][page_url] = page_hash
            
            analysis = analyse_text(page_text, page_url)
            if analysis["is_result"]:
                found_results.append({
                    "type": "page",
                    "url": page_url,
                    "title": "Aktualizacja strony",
                    "keywords": analysis["critical"]
                })
        else:
            print("     Strona bez zmian (tekst identyczny).")

        # SPRAWDZANIE PDF
        pdfs = find_pdf_links(html, page_url)
        print(f"Znaleziono plików PDF: {len(pdfs)}")

        for pdf in pdfs:
            pdf_url = pdf["url"]
            try:
                data = download_pdf(pdf_url)
            except Exception as error:
                print(f"     Nie można pobrać PDF {pdf_url}: {error}")
                continue

            file_hash = sha256_bytes(data)
            previous = state["pdfs"].get(pdf_url)

            if previous is None:
                # Jeśli to pierwsze uruchomienie skryptu, nie alarmuj o wszystkich istniejących PDFach
                if is_first_run:
                    state["pdfs"][pdf_url] = file_hash
                    continue
                    
                print(f"     NOWY PDF: {pdf_url}")
                text = extract_pdf_text(data)
                analysis = analyse_text(text, pdf_url)
                
                if analysis["is_result"]:
                    found_results.append({
                        "type": "pdf",
                        "url": pdf_url,
                        "title": pdf["title"],
                        "keywords": analysis["critical"]
                    })
                state["pdfs"][pdf_url] = file_hash
                
            elif previous != file_hash:
                print(f"     ZAKTUALIZOWANY PDF: {pdf_url}")
                text = extract_pdf_text(data)
                analysis = analyse_text(text, pdf_url)
                
                if analysis["is_result"]:
                    found_results.append({
                        "type": "pdf",
                        "url": pdf_url,
                        "title": pdf["title"] + " (Zaktualizowano)",
                        "keywords": analysis["critical"]
                    })
                state["pdfs"][pdf_url] = file_hash

    # ========================================================
    # WYSYŁKA POWIADOMIEŃ
    # ========================================================
    if found_results:
        message = "🚨 TBS GRÓJECKA 91 — NOWA AKTUALIZACJA!\n\n"
        
        for result in found_results:
            ikona = "📄" if result["type"] == "pdf" else "🌐"
            message += f"{ikona} {result['title']}\n{result['url']}\n"
            
            if result["keywords"]:
                message += "🔎 Kluczowe słowa: " + ", ".join(result["keywords"]) + "\n"
            message += "\n"

        try:
            telegram(message)
            print("\n🚨 WYSŁANO ALARM TELEGRAM O NOWOŚCIACH!")
        except Exception as e:
            print(f"\n🚨 BŁĄD PODCZAS WYSYŁANIA ALARMU: {e}")
    else:
        print("\nBrak nowych wyników do zaraportowania.")

    save_state(state)
    print("Stan zapisany.")


if __name__ == "__main__":
    main()
