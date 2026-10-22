"""Read a shop page WITHOUT an LLM: price, stock, product name and a clone-vs-original check.

Pure functions (no network, no Gemini), so they cost nothing and are fully testable offline.
Every value returned comes with a quote that is a verbatim slice of the page text, so it passes the same
"quote must be on the page" check as a Gemini answer. Gemini (pipeline.extract_offer) is only a backup.
"""
import re

_PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]{1,9}(?:\.\d{1,2})?)", re.I)
# words right before a number that mean it is NOT the product price (shipping thresholds, EMI, ranges, ...)
_NOT_A_PRICE = re.compile(r"(?:over|above|between|minimum|min\.?|orders?|shipping|free|cashback|save|saving|off|emi|"
                          r"per month|/month|cod|coupon|discount|upto|up to|worth|from|\d\s*(?:to|-|–|and))\W*$", re.I)
_PRICE_WORDS = re.compile(r"(?:price|sale|offer|now|our|buy for|only for|at|special)\W*$", re.I)
_MRP_WORDS = re.compile(r"(?:mrp|m\.r\.p|regular|list price|was)\W*$", re.I)
# monthly instalment wording ("EMI starts at ₹86 per month") is never the product price
_EMI_AROUND = re.compile(r"\bemi\b|per month|/\s*month|\bmonthly\b|no[- ]cost|instal?ments?", re.I)
# a delivery fee is never the product price; a price followed by "- ₹450" / "to ₹450" is a range, not a price
_SHIP_BEFORE = re.compile(r"(?:shipping|delivery|handling|courier|postage|packing)\b[^\d₹]{0,24}$", re.I)
_RANGE_AFTER = re.compile(r"^\s*(?:-|–|—|to)\s*(?:₹|Rs\.?|INR)\s*\d", re.I)
_ORIGINAL = re.compile(r"\b(original|genuine|official)\b", re.I)
_CLONE = re.compile(r"\b(compatible|clone|equivalent|generic)\b", re.I)
_IGNORE_TOKENS = {"buy", "india", "price", "online", "module", "board", "pack"}


def norm(s):
    """Same normalisation for quote and page, so a quote is 'on the page' if it matches after this."""
    return re.sub(r"[^a-z0-9.]+", " ", str(s).lower()).strip()


def quote_on_page(quote, page):
    return isinstance(quote, str) and len(quote.strip()) >= 3 and norm(quote) in norm(page)


def clean_quote(text, a, b):
    """Verbatim slice of the page, trimmed so it neither starts nor ends in the middle of a word."""
    if a > 0 and text[a - 1].isalnum():
        a = (text.find(" ", a) + 1) or a
    if b < len(text) and text[b].isalnum():
        cut = text.rfind(" ", a, b)
        b = cut if cut > a else b
    return text[a:b].replace("\n", " ").strip()


def _anchor_pos(text, anchor):
    """Where the product name first appears on the page (-1 if it does not). Prices above it are usually banners or other products."""
    toks = [t for t in re.findall(r"[a-z0-9]{3,}", (anchor or "").lower()) if t not in _IGNORE_TOKENS][:2]
    low = text.lower(); found = [low.find(t) for t in toks if low.find(t) >= 0]
    return min(found) if found else -1


def quote_has_amount(quote, price):
    """True only if a number written in the quote equals the price (so 5 does not match 'Rs 1,599.00', and 1599.5 does not match 1,599.00)."""
    try: target = float(price)
    except (TypeError, ValueError): return False
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", str(quote or "")):
        try:
            if abs(float(raw.replace(",", "")) - target) < 0.005: return True
        except ValueError: continue
    return False


def suspect_low(part, offers, ratio=3.0, seed_ratio=4.0):
    """URLs of verified offers whose price is implausibly low for this part. Two independent signals:
    (a) far below the team's own estimate for the part (below seed/4), which needs no other listings; or
    (b) far below the median of 3+ listings AND the listing name does not look like the part.
    A cheap listing that IS named like the part (a genuine clone at 1/7 of the original's price) is not flagged by (b).
    Flagged offers stay selectable but are never the default."""
    priced = [o for o in offers if trusted_price(o)]
    out = set()
    seed = part.get("seed_price")
    if seed:
        out |= {o["url"] for o in priced if o["price"] < seed / seed_ratio}
    if len(priced) >= 3:
        prices = sorted(o["price"] for o in priced)
        med = prices[len(prices) // 2] if len(prices) % 2 else (prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / 2
        toks = [t for t in re.findall(r"[a-z0-9]{3,}", str(part.get("query", part.get("name", ""))).lower()) if t not in _IGNORE_TOKENS][:2]
        out |= {o["url"] for o in priced if o["price"] < med / ratio and not any(t in (o.get("name") or "").lower() for t in toks)}
    return out


def rule_price(text, lo=10.0, hi=200000.0, anchor=None):
    """Best price on the page as (value, verbatim quote), or None. Skips shipping thresholds, EMI, ranges, MRP."""
    cands = []
    for m in _PRICE_RE.finditer(text):
        try: v = float(m.group(1).replace(",", ""))
        except ValueError: continue
        if not lo <= v <= hi: continue
        before = text[max(0, m.start() - 28):m.start()]
        if _NOT_A_PRICE.search(before) or _SHIP_BEFORE.search(before) or _RANGE_AFTER.match(text[m.end():m.end() + 16]) \
                or _EMI_AROUND.search(before[-16:] + text[m.end():m.end() + 14]): continue
        score = (2 if _PRICE_WORDS.search(before) else 0) - (2 if _MRP_WORDS.search(before) else 0)
        quote = clean_quote(text, max(0, m.start() - 24), min(len(text), m.end() + 12))
        cands.append((score, -m.start(), v, quote, m.start()))
    pos = _anchor_pos(text, anchor)
    after = [c for c in cands if c[4] >= pos] if pos >= 0 else []
    cands = after or cands   # prefer prices at or after the product name; fall back to any price
    best = max(cands, key=lambda c: c[:2]) if cands else None
    return (best[2], best[3]) if best else None


def bad_price_quote(quote, price=None):
    """True if the figure in this quote is an EMI / instalment / shipping / range figure, not the product price.
    Looks at every figure equal to `price` (else the first one); the quote is bad only if ALL of them are bad,
    because a quote can include a range or banner printed just before the real price."""
    q = quote or ""
    ms = list(_PRICE_RE.finditer(q))
    if not ms: return False
    def val(m):
        try: return float(m.group(1).replace(",", ""))
        except ValueError: return None
    same = [x for x in ms if price is not None and val(x) == float(price)] or ms[:1]
    def bad(m):
        before = q[max(0, m.start() - 28):m.start()]
        return bool(_NOT_A_PRICE.search(before) or _SHIP_BEFORE.search(before) or _RANGE_AFTER.match(q[m.end():m.end() + 16])
                    or _EMI_AROUND.search(before[-16:] + q[m.end():m.end() + 14]))
    return all(bad(m) for m in same)


def trusted_price(offer):
    """An offer's price may be used only if it exists and its own quote does not show an EMI/instalment-type figure.
    One rule for the BOM, the cards, the ledger and the cheapest-price logic."""
    return bool(offer.get("price")) and offer.get("in_stock") is not False and not bad_price_quote(offer.get("quote"), offer.get("price"))



def page_name(text, part_query):
    """Product title copied from the page: first '# heading', else the first short line that mentions the part. None if unsure."""
    for line in text.splitlines()[:40]:
        m = re.match(r"\s*#{1,2}\s+(.{4,120})$", line)
        if m: return m.group(1).strip()
    toks = [t for t in re.findall(r"[a-z0-9]{3,}", part_query.lower()) if t not in _IGNORE_TOKENS][:2]
    for line in text.splitlines()[:25]:
        s = line.strip()
        if 6 <= len(s) <= 120 and toks and all(t in s.lower() for t in toks): return s
    return None


def variant_problem(part, text):
    """None if the page looks like the part we mean; otherwise a short reason.
    Catches the clone-vs-original trap (README: Arduino Nano listings from ₹229 to ₹2,755)."""
    low = text.lower()
    toks = [t for t in re.findall(r"[a-z0-9]{2,}", part.get("query", part["name"]).lower()) if t not in _IGNORE_TOKENS]
    if toks and sum(t in low for t in toks) / len(toks) < 0.6:
        return "page does not look like this part"
    wants_original = bool(_ORIGINAL.search(part["name"]))
    if not wants_original and _ORIGINAL.search(text) and not _CLONE.search(text):
        return "page sells the original/genuine version, not the generic part"
    return None


def price_window(text, limit=6000):
    """Short slice of a page for the LLM: the top of the page plus the area around the first price."""
    if len(text) <= limit: return text
    m = _PRICE_RE.search(text)
    start = max(0, (m.start() if m else 0) - 1500)
    return text[:2000] + "\n...\n" + text[start:start + (limit - 2000)]
