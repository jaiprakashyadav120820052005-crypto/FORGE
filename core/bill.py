"""Procurement bill: per-part vendor choice (cheapest verified listing by default), totals, on-screen invoice and PDF.

Pure functions only, so everything here is testable offline.
Prices come from verified live offers (quote found on the page) or, failing that, the seed estimate. Nothing is invented:
no tax, shipping or discount is added, because shop pages decide those at checkout.
"""
import hashlib, html, pathlib, re, datetime as dt
from io import BytesIO
from urllib.parse import urlparse
from . import pagerules
from .data import QTY

MAX_OFFERS = 4  # source_part() reads at most 4 pages per part
FONT_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "fonts"
NOTE = ("Estimate only. Prices were read from vendor pages when you searched; stock, shipping, GST and discounts are decided "
        "by each shop at checkout. Lines marked Estimate use a rough seed price that has not been verified on a shop page.")


# ---------- small helpers ----------
def inr(x, sym="₹"):
    """Indian digit grouping with 2 decimals: 123456.5 -> ₹1,23,456.50"""
    whole, dec = f"{abs(x):.2f}".split(".")
    if len(whole) > 3: whole = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", whole[:-3]) + "," + whole[-3:]
    return f"{'-' if x < 0 else ''}{sym}{whole}.{dec}"

def money(x, sym="₹"):
    """inr() that shows a dash for a missing price instead of crashing."""
    return "—" if x is None else inr(x, sym)

def prices_text(tot):
    """'3 verified, 2 estimated' (+ ', 1 not found' when a part has no price at all)."""
    return f"{tot['n_live']} verified, {tot['n_seed']} estimated" + (f", {tot['n_manual']} entered by you" if tot.get("n_manual") else "") + (f", {tot['n_missing']} not found" if tot.get("n_missing") else "")

def vendor(url):
    return (urlparse(url or "").netloc or "").removeprefix("www.") or "unknown shop"

def safe_url(url):
    """Only http(s) links are ever rendered as links (URLs come from the web)."""
    return url if urlparse(url or "").scheme in ("http", "https") else ""

def md_escape(t):
    """Escape Markdown so text read from a shop page cannot inject links or formatting into the UI."""
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~$])", r"\\\1", str(t or ""))


# ---------- offers and choice ----------
def offers_for(part_id, live):
    """Verified offers (price found with a quote), cheapest first. Ties keep search order, same as pipeline.price_of."""
    vs = [o for o in live.get(part_id, []) if pagerules.trusted_price(o)]
    return sorted(vs, key=lambda o: (bool(o.get("suspect")), o["price"]))[:MAX_OFFERS]   # suspicious-low listings last, so they are never the default

def unverified_for(part_id, live):
    """Listings without a usable price. A price whose quote shows an EMI/instalment figure counts as unverified, with the reason."""
    out = []
    for o in live.get(part_id, []):
        if not o.get("price"): out.append(o)
        elif o.get("in_stock") is False: out.append(dict(o, price=None, note="Sold out at this shop, so it is not used for pricing"))
        elif not pagerules.trusted_price(o):
            out.append(dict(o, price=None, note=o.get("note") or f"Price not used: ₹{o['price']:,.0f} looks like an EMI or instalment figure, not the product price"))
    return out

def chosen_url(part_id, live, choice):
    """The user's pick if it is still a verified offer, otherwise the cheapest one. None when there is no verified offer."""
    offers = offers_for(part_id, live)
    if not offers: return None
    urls = [o["url"] for o in offers]
    return choice.get(part_id) if choice.get(part_id) in urls else urls[0]


def manual_pick(part_id, manual):
    """The user's own pick of an UNVERIFIED listing with a price they typed, or None. Never chosen by default, never called 'lowest'.
    Kept apart from `live` so no verified-price logic (cheapest, spread warning, selector) can ever see it."""
    m = (manual or {}).get(part_id)
    try: price = float(m.get("price") or 0) if m else 0
    except (TypeError, ValueError): price = 0
    return dict(m, price=price) if m and safe_url(m.get("url")) and 0 < price < 1e8 else None


# ---------- bill ----------
def build_lines(parts, live, choice, manual=None):
    """One bill line per part (skips 'no sensor needed'). Each line knows if it is the cheapest option and what a swap costs."""
    lines = []
    for key, p in parts.items():
        if p.get("none"): continue
        role = p.get("role", key)  # custom parts are keyed by id and carry their own role and quantity
        qty, offers = p.get("qty") or QTY.get(role, 1), offers_for(p["id"], live)
        base = dict(part_id=p["id"], role=role, name=p["name"], qty=qty)
        man = manual_pick(p["id"], manual)
        if man:  # the user chose an unverified listing and typed the price they saw
            lines.append(dict(base, unit=man["price"], total=man["price"] * qty, cheapest_total=offers[0]["price"] * qty if offers else man["price"] * qty,
                              source="manual", url=man["url"], vendor=vendor(man["url"]), listing=man.get("name"), quote=None,
                              in_stock=None, is_cheapest=False, n_options=len(offers)))
        elif offers:
            pick = next(o for o in offers if o["url"] == chosen_url(p["id"], live, choice))
            lines.append(dict(base, unit=pick["price"], total=pick["price"] * qty, cheapest_total=offers[0]["price"] * qty,
                              source="live", url=pick["url"], vendor=vendor(pick["url"]), listing=pick.get("name"), quote=pick.get("quote"),
                              in_stock=pick.get("in_stock"), is_cheapest=pick["price"] == offers[0]["price"], n_options=len(offers)))
        elif p.get("seed_price") is None:  # nothing verified and no estimate: shown as missing, left out of the total
            lines.append(dict(base, unit=None, total=0, cheapest_total=0, source="missing", url="", vendor="", listing=None, quote=None,
                              in_stock=None, is_cheapest=True, n_options=0))
        else:
            lines.append(dict(base, unit=p["seed_price"], total=p["seed_price"] * qty, cheapest_total=p["seed_price"] * qty,
                              source="seed", url="", vendor="", listing=None, quote=None, in_stock=None, is_cheapest=True, n_options=0))
    return lines

def totals(lines, budget):
    sub = sum(l["total"] for l in lines); cheapest = sum(l["cheapest_total"] for l in lines)
    return dict(subtotal=sub, cheapest=cheapest, premium=sub - cheapest, budget=budget, left=budget - sub,
                n_live=sum(l["source"] == "live" for l in lines), n_seed=sum(l["source"] == "seed" for l in lines), n_missing=sum(l["source"] == "missing" for l in lines), n_manual=sum(l["source"] == "manual" for l in lines), n_items=sum(l["qty"] for l in lines))

def bill_ref(project_label, lines, day):
    h = hashlib.sha1((project_label + "|".join(f"{l['part_id']}:{l['url']}:{l['unit']}" for l in lines)).encode()).hexdigest()[:5].upper()
    return f"FG-{day:%Y%m%d}-{h}"

def status_of(l):
    """(kind, text) shown on a bill line."""
    if l["source"] == "missing": return "est", "No verified price yet, not in total"
    if l["source"] == "manual": return "pick", "Your pick, price entered by you (not verified)"
    if l["source"] == "seed": return "est", "Estimate, not verified"
    if l["is_cheapest"]: return "best", "Cheapest verified"
    return "pick", f"Your pick, +{inr(l['total'] - l['cheapest_total'])}"


# ---------- on-screen invoice (HTML for st.markdown) ----------
CSS = """<style>
.fg-sheet{background:#F3F6F9;color:#0D1524;border-radius:18px;padding:34px 40px 28px;font-family:'DM Sans',sans-serif;box-shadow:0 18px 48px rgba(0,0,0,.35);margin:6px 0 14px}
.fg-sheet *{box-sizing:border-box}
.fg-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;padding-bottom:18px;border-bottom:3px solid #3CC4CF}
.fg-brand{font:700 26px Sora,sans-serif;letter-spacing:.5px;color:#0D1524}.fg-sub{font-size:14px;color:#4B5A73;margin-top:2px}
.fg-ref{text-align:right;font-size:13px;color:#4B5A73;line-height:1.55}.fg-ref b{color:#0D1524;font-weight:500}
.fg-meta{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:18px 0;border-bottom:1px solid #D9E2EC}
.fg-meta span{display:block;font-size:12px;color:#6A7890}.fg-meta b{font-weight:500;font-size:15px}
.fg-wrap{overflow-x:auto}.fg-t{width:100%;border-collapse:collapse;min-width:640px}
.fg-t th{font-size:12px;font-weight:500;color:#6A7890;text-align:left;padding:14px 8px 8px}
.fg-t td{padding:13px 8px;border-bottom:1px solid #E2E9F1;vertical-align:top;font-size:14px}
.fg-t .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.fg-t .nm{font-weight:500}.fg-t .rl{font-size:12.5px;color:#6A7890;margin-top:2px}
.fg-t a{color:#0B7A84;text-decoration:none;font-weight:500}.fg-t a:hover{text-decoration:underline}
.fg-t .amt{font-weight:700;font-family:Sora,sans-serif;font-size:14px}
.fg-c{display:inline-block;margin-top:5px;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:500}
.fg-c.best{background:#DDF3E8;color:#1B7A4F}.fg-c.pick{background:#FBEFD0;color:#7D5A08}.fg-c.est{background:#E6EBF2;color:#4B5A73}.fg-c.oos{background:#FADDE0;color:#A32B36;margin-left:5px}
.fg-tot{display:flex;justify-content:flex-end;margin-top:18px}.fg-tot>div{width:min(360px,100%)}
.fg-row{display:flex;justify-content:space-between;padding:6px 0;font-size:14px;color:#4B5A73;font-variant-numeric:tabular-nums}.fg-row b{font-weight:500;color:#0D1524}
.fg-row.ok b{color:#1B7A4F}.fg-row.bad b{color:#C2303D}
.fg-grand{display:flex;justify-content:space-between;align-items:baseline;margin-top:8px;padding:14px 18px;border-radius:12px;background:#0D1524;color:#fff}
.fg-grand span{font-size:14px;color:#B8C6DA}.fg-grand b{font:700 28px Sora,sans-serif;font-variant-numeric:tabular-nums}
.fg-note{margin-top:18px;font-size:12px;line-height:1.55;color:#6A7890}
@media(max-width:640px){.fg-sheet{padding:22px 18px}.fg-meta{grid-template-columns:repeat(2,1fr)}.fg-ref{text-align:left}}
</style>"""

def invoice_html(lines, tot, project_label, ref, day):
    """Self-contained invoice markup. Every value that came from the web is html-escaped. No blank lines or indentation (Markdown would treat them as code)."""
    e, rows = html.escape, []
    for l in lines:
        kind, text = status_of(l)
        if l["source"] in ("live", "manual"):
            u = safe_url(l["url"]); shop = f'<a href="{e(u, quote=True)}" target="_blank" rel="noopener noreferrer">{e(l["vendor"])}</a>' if u else e(l["vendor"])
            listing = f'<div class="rl">{e(l["listing"][:70])}</div>' if l.get("listing") else ""
        else: shop, listing = '<span class="rl">No shop page verified</span>', ""
        rows.append(f'<tr><td><div class="nm">{e(l["name"])}</div><div class="rl">{e(l["role"].capitalize())}</div></td>'
                    f'<td>{shop}{listing}<div><span class="fg-c {kind}">{e(text)}</span></div></td>'
                    f'<td class="n">{l["qty"]}</td><td class="n">{money(l["unit"])}</td><td class="n amt">{money(l["unit"] and l["total"])}</td></tr>')
    extra = ""
    if tot["premium"] > 0.005:
        extra = (f'<div class="fg-row"><span>Cheapest possible total</span><b>{inr(tot["cheapest"])}</b></div>'
                 f'<div class="fg-row"><span>Extra for your picks</span><b>+{inr(tot["premium"])}</b></div>')
    if tot.get("n_missing"):
        extra += f'<div class="fg-row"><span>Parts without a verified price</span><b>{tot["n_missing"]} not in total</b></div>'
    over = tot["left"] < -0.005
    meta = [("Project", project_label), ("Prepared on", f"{day:%d %b %Y}"), ("Budget", inr(tot["budget"])),
            ("Prices", prices_text(tot))]
    return (CSS + '<div class="fg-sheet">'
            '<div class="fg-head"><div><div class="fg-brand">FORGE</div><div class="fg-sub">Estimated bill of materials</div></div>'
            f'<div class="fg-ref">Reference <b>{e(ref)}</b><br>{tot["n_items"]} items across {len(lines)} parts</div></div>'
            '<div class="fg-meta">' + "".join(f"<div><span>{e(k)}</span><b>{e(v)}</b></div>" for k, v in meta) + '</div>'
            '<div class="fg-wrap"><table class="fg-t"><thead><tr><th>Part</th><th>Shop</th><th class="n">Qty</th><th class="n">Unit price</th><th class="n">Amount</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
            '<div class="fg-tot"><div>' + extra +
            f'<div class="fg-row {"bad" if over else "ok"}"><span>{"Over budget by" if over else "Under budget by"}</span><b>{inr(abs(tot["left"]))}</b></div>'
            f'<div class="fg-grand"><span>Estimated total</span><b>{inr(tot["subtotal"])}</b></div></div></div>'
            f'<div class="fg-note">{e(NOTE)}</div></div>')


# ---------- PDF ----------
def _fonts():
    """Register the bundled DejaVu fonts (they contain the rupee sign). Falls back to Helvetica with 'Rs.' if they are missing."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    reg, bold = FONT_DIR / "DejaVuSans.ttf", FONT_DIR / "DejaVuSans-Bold.ttf"
    if reg.exists() and bold.exists():
        if "FG" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("FG", str(reg))); pdfmetrics.registerFont(TTFont("FG-Bold", str(bold)))
            pdfmetrics.registerFontFamily("FG", normal="FG", bold="FG-Bold", italic="FG", boldItalic="FG-Bold")
        return "FG", "FG-Bold", "₹"
    return "Helvetica", "Helvetica-Bold", "Rs. "

def pdf_bytes(lines, tot, project_label, ref, day):
    """A4 PDF of the same bill. Vendor names are clickable links to the chosen listing."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    F, FB, sym = _fonts()
    NAVY, TEAL, MUTE, RULE, PAPER = colors.HexColor("#0D1524"), colors.HexColor("#3CC4CF"), colors.HexColor("#6A7890"), colors.HexColor("#D9E2EC"), colors.HexColor("#F3F6F9")
    GREEN, AMBER, RED = "#1B7A4F", "#7D5A08", "#C2303D"
    S = lambda n, **k: ParagraphStyle(n, fontName=k.pop("fontName", F), fontSize=k.pop("fontSize", 9), leading=k.pop("leading", 12), textColor=k.pop("textColor", NAVY), **k)
    body, small, num, numb = S("b"), S("s", fontSize=7.5, leading=10, textColor=MUTE), S("n", alignment=2), S("nb", alignment=2, fontName=FB)
    head = S("h", fontSize=8, textColor=MUTE); headr = S("hr", fontSize=8, textColor=MUTE, alignment=2)
    esc = lambda t: html.escape(str(t or ""), quote=True)
    m = lambda x: inr(x, sym)

    def draw(c, d):
        w, h = A4
        c.saveState(); c.setFillColor(NAVY); c.rect(0, h - 26 * mm, w, 26 * mm, stroke=0, fill=1)
        c.setFillColor(TEAL); c.rect(0, h - 27 * mm, w, 1 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white); c.setFont(FB, 20); c.drawString(16 * mm, h - 14 * mm, "FORGE")
        c.setFillColor(colors.HexColor("#B8C6DA")); c.setFont(F, 9); c.drawString(16 * mm, h - 20 * mm, "Estimated bill of materials")
        c.setFont(F, 8.5); c.drawRightString(w - 16 * mm, h - 14 * mm, f"Reference {ref}"); c.drawRightString(w - 16 * mm, h - 19.5 * mm, f"{day:%d %b %Y}")
        c.setStrokeColor(RULE); c.line(16 * mm, 13 * mm, w - 16 * mm, 13 * mm)
        c.setFillColor(MUTE); c.setFont(F, 7.5); c.drawString(16 * mm, 9 * mm, "Generated by FORGE. Estimate only; verify price and stock at checkout.")
        c.drawRightString(w - 16 * mm, 9 * mm, f"Page {d.page}"); c.restoreState()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=36 * mm, bottomMargin=20 * mm,
                            title=f"FORGE bill: {project_label}", author="FORGE")
    W = A4[0] - 32 * mm
    story = []
    # meta strip
    meta = [("Project", project_label), ("Budget", m(tot["budget"])), ("Items", f"{tot['n_items']} across {len(lines)} parts"),
            ("Prices", prices_text(tot))]
    mt = Table([[Paragraph(k, small) for k, _ in meta], [Paragraph(f"<b>{esc(v)}</b>", body) for _, v in meta]], colWidths=[W * .30, W * .20, W * .25, W * .25])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                            ("LINEBELOW", (0, 1), (-1, 1), .6, RULE), ("BOTTOMPADDING", (0, 1), (-1, 1), 10)]))
    story += [mt, Spacer(1, 8)]
    # items
    data = [[Paragraph("Part", head), Paragraph("Shop", head), Paragraph("Qty", headr), Paragraph("Unit price", headr), Paragraph("Amount", headr)]]
    for l in lines:
        kind, text = status_of(l)
        colr = {"best": GREEN, "pick": AMBER, "est": "#4B5A73"}[kind]
        part = f"<b>{esc(l['name'])}</b><br/><font size=7.5 color='#6A7890'>{esc(l['role'].capitalize())}</font>"
        if l["source"] in ("live", "manual"):
            u = safe_url(l["url"]); name = f"<link href=\"{esc(u)}\" color='#0B7A84'><u>{esc(l['vendor'])}</u></link>" if u else esc(l["vendor"])
            shop = f"{name}" + (f"<br/><font size=7.5 color='#6A7890'>{esc((l['listing'] or '')[:60])}</font>" if l.get("listing") else "")
        else: shop = "<font color='#6A7890'>No shop page verified</font>"
        shop += f"<br/><font size=7.5 color='{colr}'>{esc(text)}</font>"
        data.append([Paragraph(part, body), Paragraph(shop, body), Paragraph(str(l["qty"]), num), Paragraph(money(l["unit"], sym), num), Paragraph(money(l["unit"] and l["total"], sym), numb)])
    it = Table(data, colWidths=[W * .31, W * .31, W * .07, W * .15, W * .16], repeatRows=1)
    it.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                            ("LINEBELOW", (0, 0), (-1, -1), .5, RULE)]))
    story += [it, Spacer(1, 12)]
    # totals
    rows = []
    if tot["premium"] > 0.005:
        rows += [[Paragraph("Cheapest possible total", body), Paragraph(m(tot["cheapest"]), num)], [Paragraph("Extra for your picks", body), Paragraph("+" + m(tot["premium"]), num)]]
    if tot.get("n_missing"):
        rows.append([Paragraph("Parts without a verified price", body), Paragraph(f"{tot['n_missing']} not in total", num)])
    over = tot["left"] < -0.005
    rows.append([Paragraph("Over budget by" if over else "Under budget by", body), Paragraph(f"<font color='{RED if over else GREEN}'><b>{m(abs(tot['left']))}</b></font>", num)])
    rows.append([Paragraph("<font color='#B8C6DA'>Estimated total</font>", body), Paragraph(f"<font color='white' size=14><b>{m(tot['subtotal'])}</b></font>", S("g", alignment=2, leading=18))])
    tt = Table(rows, colWidths=[W * .30, W * .24], hAlign="RIGHT")
    tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("BACKGROUND", (0, -1), (-1, -1), NAVY), ("TOPPADDING", (0, -1), (-1, -1), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
                            ("LEFTPADDING", (0, -1), (-1, -1), 10), ("RIGHTPADDING", (0, -1), (-1, -1), 10)]))
    story += [tt, Spacer(1, 16), Paragraph(esc(NOTE), small)]
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
    return buf.getvalue()


def make(parts, live, choice, budget, project_label, day=None, manual=None):
    """Everything the UI needs in one call: lines, totals, reference, html, pdf."""
    day = day or dt.date.today()
    lines = build_lines(parts, live, choice, manual); tot = totals(lines, budget); ref = bill_ref(project_label, lines, day)
    return dict(lines=lines, tot=tot, ref=ref, html=invoice_html(lines, tot, project_label, ref, day), pdf=pdf_bytes(lines, tot, project_label, ref, day))
