import os
import re
import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invoice-api")

app = FastAPI()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.0-flash"  # change if you want a different Gemini model
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

CURRENCY_MAP = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD", "us dollar": "USD", "us dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "pound sterling": "GBP", "pounds sterling": "GBP", "£": "GBP",
    "inr": "INR", "rupee": "INR", "rupees": "INR", "₹": "INR", "rs": "INR", "rs.": "INR",
    "jpy": "JPY", "yen": "JPY", "¥": "JPY",
}

WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lac": 100000, "million": 1000000, "crore": 10000000}


def words_to_number(text: str):
    """Convert a spelled-out number phrase (with optional scale words) to an int."""
    text = text.lower().replace("-", " ")
    text = re.sub(r"\band\b", " ", text)
    tokens = [t for t in re.split(r"[\s,]+", text) if t]
    total = 0
    current = 0
    found = False
    for tok in tokens:
        if tok in WORD_NUMS:
            current += WORD_NUMS[tok]
            found = True
        elif tok in SCALES:
            scale = SCALES[tok]
            if scale >= 1000:
                total += (current if current else 1) * scale
                current = 0
            else:
                current = (current if current else 1) * scale
            found = True
    total += current
    return total if found else None


def parse_amount(raw: str):
    """Parse an amount string that may use words, K/M suffix, or grouped digits (Indian/Western) into an int."""
    if raw is None:
        return None
    s = str(raw).strip()

    # Strip currency symbols/words
    s_clean = re.sub(r"[₹$€£]", "", s)
    s_clean = re.sub(
        r"\b(usd|eur|gbp|inr|jpy|rs\.?|rupees?|dollars?|euros?|pounds?( sterling)?|yen)\b",
        "", s_clean, flags=re.IGNORECASE
    ).strip()

    # K/M suffix e.g. 12K, 1.5M
    m = re.fullmatch(r"([\d,.]+)\s*([kKmM])", s_clean)
    if m:
        num = float(m.group(1).replace(",", ""))
        mult = 1000 if m.group(2).lower() == "k" else 1_000_000
        return int(round(num * mult))

    # Plain/grouped digits (strip all commas, works for both Western 12,480 and Indian 1,24,800 grouping)
    digits_only = re.sub(r"[^\d.]", "", s_clean)
    if digits_only and re.fullmatch(r"\d+(\.\d+)?", digits_only) and any(c.isdigit() for c in s_clean):
        try:
            return int(round(float(digits_only)))
        except ValueError:
            pass

    # Spelled out in words
    w = words_to_number(s_clean)
    if w is not None:
        return w

    return None


def find_total_amount(text: str):
    # Look near "total", "amount due", "grand total", "balance due"
    patterns = [
        r"(?:total amount|grand total|amount due|balance due|total)\s*[:\-]?\s*(?:is|of)?\s*([₹$€£]?\s?[\d,]+(?:\.\d+)?\s*[kKmM]?)",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            val = parse_amount(m.group(1))
            if val is not None:
                return val

    # Word-based total, e.g. "total of twelve thousand four hundred eighty"
    m = re.search(
        r"(?:total|amount due|balance due)[^.\n]{0,20}?\b((?:[a-zA-Z]+[\s-]+){1,8}(?:hundred|thousand|lakh|crore|million)?[a-zA-Z\s-]*)",
        text, flags=re.IGNORECASE
    )
    if m:
        val = words_to_number(m.group(1))
        if val is not None:
            return val

    return None


def find_currency(text: str):
    for sym, code in {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}.items():
        if sym in text:
            return code
    lowered = text.lower()
    for word, code in CURRENCY_MAP.items():
        if len(word) > 1 and re.search(r"\b" + re.escape(word) + r"\b", lowered):
            return code
    return None


def find_due_in_days(text: str):
    m = re.search(r"\bnet\s+(\d+)\b", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:payable|due)\s+within\s+(\d+)\s+days?", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"due in (\d+)\s+days?", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"due in ([a-zA-Z\s-]+?)\s+weeks?", text, flags=re.IGNORECASE)
    if m:
        n = words_to_number(m.group(1)) or (1 if m.group(1).strip().lower() == "a" else None)
        if n is not None:
            return n * 7
    m = re.search(r"due in ([a-zA-Z\s-]+?)\s+days?", text, flags=re.IGNORECASE)
    if m:
        n = words_to_number(m.group(1))
        if n is not None:
            return n
    return None


def find_contact_email(text: str):
    m = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    if m:
        return m.group(0).lower()
    return None


def find_is_paid(text: str):
    lowered = text.lower()
    paid_signals = ["paid in full", "payment received", "already paid", "paid on"]
    unpaid_signals = ["awaiting payment", "unpaid", "outstanding", "pending payment", "not yet paid", "due upon"]
    for s in unpaid_signals:
        if s in lowered:
            return False
    for s in paid_signals:
        if s in lowered:
            return True
    return None


def find_priority(text: str):
    lowered = text.lower()
    if "urgent" in lowered:
        return "urgent"
    if "high priority" in lowered or re.search(r"\bhigh\b", lowered):
        return "high"
    if "low priority" in lowered or re.search(r"\blow\b", lowered):
        return "low"
    if "normal" in lowered:
        return "normal"
    return None


def normalize_date(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    from datetime import datetime
    fmts = [
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%d/%m/%Y",
        "%Y/%m/%d", "%B %d %Y", "%d-%m-%Y", "%m-%d-%Y",
    ]
    for f in fmts:
        try:
            return datetime.strptime(raw, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def sanitize_schema_for_gemini(schema):
    """Gemini's responseSchema only supports a subset of OpenAPI/JSON-Schema keywords.
    Recursively strip unsupported keys and uppercase 'type' values."""
    if not isinstance(schema, dict):
        return schema

    UNSUPPORTED_KEYS = {"additionalProperties", "$schema", "title", "examples", "default", "const"}
    TYPE_MAP = {
        "object": "OBJECT", "string": "STRING", "number": "NUMBER",
        "integer": "INTEGER", "boolean": "BOOLEAN", "array": "ARRAY",
    }

    out = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: sanitize_schema_for_gemini(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitize_schema_for_gemini(value)
        else:
            out[key] = value
    return out


async def call_llm(document_text: str, schema: dict):
    system_prompt = """You are an invoice data extraction engine. Extract structured data from the invoice text EXACTLY per the JSON schema provided. Follow these rules precisely:

- vendor: the biller's proper name, exactly as written in the text.
- currency: ISO 4217 code (USD, EUR, GBP, INR, JPY, etc), inferred from symbols or words like "euros", "pounds sterling", "₹".
- total_amount: integer in the main unit, no separators or symbols. Text may spell it out ("twelve thousand four hundred eighty"), use grouped digits (12,480 or Indian grouping 1,24,800), or a K/M suffix (12K = 12000).
- invoice_date: normalize to YYYY-MM-DD.
- due_in_days: integer parsed from phrases like "Net 30", "payable within 45 days", "due in two weeks" (=14).
- is_paid: boolean inferred from wording ("paid in full" = true, "awaiting payment" = false).
- priority: one of low, normal, high, urgent.
- contact_email: lowercased.
- line_items: array of {sku, quantity, unit_price} objects in the order they appear in the text. unit_price is an integer.
- item_count: number of line items.

Return ONLY the JSON object matching the schema exactly - no extra keys, no missing keys, no markdown fences."""

    gemini_schema = sanitize_schema_for_gemini(schema)

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {"role": "user", "parts": [{"text": f"Invoice text:\n\n{document_text}"}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not parse Gemini response: {e} | raw={data}")


def postprocess(extracted: dict, text: str, schema: dict) -> dict:
    """Normalize fields and fill in gaps using regex fallbacks, then trim to exact schema keys."""
    result = dict(extracted) if isinstance(extracted, dict) else {}

    # total_amount
    ta = result.get("total_amount")
    parsed = parse_amount(ta) if ta is not None else None
    if parsed is None:
        parsed = find_total_amount(text)
    if parsed is not None:
        result["total_amount"] = int(parsed)

    # currency
    cur = result.get("currency")
    if not cur or not re.fullmatch(r"[A-Z]{3}", str(cur)):
        fallback = find_currency(text)
        if fallback:
            result["currency"] = fallback
    elif cur:
        result["currency"] = cur.upper()

    # invoice_date
    date_val = normalize_date(result.get("invoice_date"))
    if date_val:
        result["invoice_date"] = date_val

    # due_in_days
    if result.get("due_in_days") is None:
        d = find_due_in_days(text)
        if d is not None:
            result["due_in_days"] = d
    if isinstance(result.get("due_in_days"), str):
        try:
            result["due_in_days"] = int(result["due_in_days"])
        except ValueError:
            pass

    # is_paid
    if result.get("is_paid") is None:
        ip = find_is_paid(text)
        if ip is not None:
            result["is_paid"] = ip

    # priority
    pr = result.get("priority")
    if not pr or pr.lower() not in {"low", "normal", "high", "urgent"}:
        fallback = find_priority(text)
        if fallback:
            result["priority"] = fallback
    else:
        result["priority"] = pr.lower()

    # contact_email
    email = result.get("contact_email")
    if email:
        result["contact_email"] = email.lower()
    else:
        fallback = find_contact_email(text)
        if fallback:
            result["contact_email"] = fallback

    # line_items unit_price / quantity as ints
    items = result.get("line_items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                if "unit_price" in item:
                    v = parse_amount(item["unit_price"])
                    if v is not None:
                        item["unit_price"] = int(v)
                if "quantity" in item and isinstance(item["quantity"], str):
                    try:
                        item["quantity"] = int(item["quantity"])
                    except ValueError:
                        pass
        result["item_count"] = len(items)

    # Trim/align to exact schema keys (no more, no less)
    props = schema.get("properties", {})
    if props:
        aligned = {}
        for key in props:
            if key in result:
                aligned[key] = result[key]
        result = aligned

    return result


@app.post("/extract")
async def extract(request: Request):
    body = await request.json()
    document_id = body.get("document_id")
    text = body.get("text", "")
    schema = body.get("schema", {})

    try:
        raw_extracted = await call_llm(text, schema)
    except Exception as e:
        log.exception("LLM call failed for document %s", document_id)
        return JSONResponse(status_code=500, content={"error": str(e)})

    final = postprocess(raw_extracted, text, schema)
    return JSONResponse(content=final)


@app.get("/")
async def health():
    return {"status": "ok"}
