import re
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


class ExtractRequest(BaseModel):
    invoice_text: str


def clean_number(raw: str):
    if raw is None:
        return None
    raw = raw.strip()
    raw = raw.replace(",", "")
    raw = re.sub(r"[^\d.]", "", raw)
    if raw in ("", "."):
        return None
    try:
        return round(float(raw), 2)
    except:
        return None

def parse_date(raw: str):
    if not raw:
        return None
    raw = raw.strip()

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", raw)
    if m:
        d, mon_name, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mo = MONTHS.get(mon_name)
        if mo:
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None

    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", raw)
    if m:
        mon_name, d, y = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        mo = MONTHS.get(mon_name)
        if mo:
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None

    return None


def find_first(text: str, patterns):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip()
            if val:
                return val
    return None


def extract_invoice_no(text: str):
    patterns = [
        r"(?:invoice|inv)\s*(?:no\.?|number|num|id|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-_.]{2,})",
        r"(?:invoice|inv)\s*[:#\-]\s*([A-Za-z0-9][A-Za-z0-9/\-_.]{2,})",
        r"ref(?:erence)?\s*(?:no\.?|number|#)?\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-_.]{2,})",
        r"(?:bill|doc(?:ument)?|receipt|order|transaction|txn)\s*(?:no\.?|number|id|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-_.]{2,})",
    ]
    result = find_first(text, patterns)
    if result:
        return result.strip().rstrip(".,")

    m = re.search(r"\b([A-Z]{1,6}[-/][A-Za-z0-9\-/]{2,10})\b", text)
    if m:
        return m.group(1)
    return None


def extract_date(text: str):
    patterns = [
        r"(?:invoice\s*date|date|issued|dated)\s*[:\-]\s*([0-9A-Za-z,\s/\-]+?)(?:\n|$)",
    ]
    raw = find_first(text, patterns)
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return None


def extract_vendor(text: str):
    patterns = [
        r"(?:vendor(?:\s*name)?|seller|from|bill\s*from|supplier)\s*[:\-]\s*([^\n]+)",
    ]
    v = find_first(text, patterns)
    if v:
        return v.strip()

    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if lines:
        first = lines[0]
        first = re.sub(r"\s*[-–—]\s*Tax\s*Invoice.*$", "", first, flags=re.IGNORECASE)
        if first.upper() != "INVOICE" and not re.search(r"invoice", first, re.IGNORECASE):
            return first.strip()
        elif re.search(r"invoice", first, re.IGNORECASE) and first.upper() != "INVOICE":
            cleaned = re.sub(r"invoice", "", first, flags=re.IGNORECASE).strip(" -–—")
            if cleaned:
                return cleaned
    return None


def extract_amount(text: str):
    patterns = [
        r"(?:Sub\s*Total|Subtotal)\s*[:\-]?\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
        r"(?:Amount\s*Before\s*Tax)\s*[:\-]?\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
        r"(?:Net\s*Amount)\s*[:\-]?\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return clean_number(m.group(1))
    return None

def extract_tax(text: str):
    patterns = [
        r"(?:GST|IGST|CGST|SGST|VAT|Tax)\s*\(\s*\d+(?:\.\d+)?%\s*\)\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
        r"(?:GST|IGST|CGST|SGST|VAT|Tax)\s*[:\-]\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
        r"Tax\s*Amount\s*[:\-]?\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
        r"GST\s*Amount\s*[:\-]?\s*(?:Rs\.?|INR|₹|\$|USD|EUR|€|GBP|£)?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return clean_number(m.group(1))
    return None

def extract_currency(text: str):
    patterns = [
        r"currency\s*[:\-]\s*([A-Za-z]{3})",
    ]
    raw = find_first(text, patterns)
    if raw:
        return raw.upper()

    if re.search(r"Rs\.?|₹|INR", text, re.IGNORECASE):
        return "INR"
    if re.search(r"\$|USD", text):
        return "USD"
    if re.search(r"€|EUR", text):
        return "EUR"
    if re.search(r"£|GBP", text):
        return "GBP"
    return None


@app.post("/extract")
def extract(req: ExtractRequest):
    text = req.invoice_text or ""
    return {
        "invoice_no": extract_invoice_no(text),
        "date": extract_date(text),
        "vendor": extract_vendor(text),
        "amount": extract_amount(text),
        "tax": extract_tax(text),
        "currency": extract_currency(text),
    }


@app.get("/")
def root():
    return {"status": "ok"}
