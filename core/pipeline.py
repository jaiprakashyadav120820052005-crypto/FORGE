import datetime as dt, itertools, json, os, re, time
from . import pagerules, resume as resume_mod, tf
from .data import CATALOG, EXTRAS, NONE_SENSOR, PROJECTS, QTY, RULES
from .rules import evaluate

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]  # tried in order if one is unavailable or out of quota
_G = None

def _gemini(key):
    """Keep ONE Gemini client alive. google-genai closes a client when Python discards it,
    so chaining genai.Client(...).models.generate_content(...) fails with 'client has been closed'."""
    global _G
    if _G is None:
        from google import genai
        _G = genai.Client(api_key=key)
    return _G

GONE = ("404", "NOT_FOUND")
BUSY = ("503", "UNAVAILABLE", "500", "INTERNAL", "429", "RESOURCE_EXHAUSTED")

def _generate(prompt, cfg, key):
    """Try the configured model, then fallbacks. Busy/quota errors get one retry after a pause; missing models move on at once."""
    last = None
    for m in dict.fromkeys([os.getenv("GEMINI_MODEL") or MODELS[0], *MODELS]):
        for attempt in range(2):
            try:
                return _gemini(key).models.generate_content(model=m, contents=prompt, config=cfg)
            except Exception as e:
                last, msg = e, str(e)
                if any(g in msg for g in GONE): break
                if any(x in msg for x in BUSY):
                    if attempt == 0: time.sleep(2); continue
                    break
                raise
    raise last

_QUOTA_UNTIL = 0.0  # time.time() until which Gemini is skipped after a 429 / quota error

def quota_off(): return time.time() < _QUOTA_UNTIL

def _gemini_offer(page_text, part_name):
    """Gemini reads the page; a price is accepted ONLY if its quote appears verbatim in the page and contains the number."""
    key = os.getenv("GEMINI_API_KEY")
    if not key: return None
    from google.genai import types
    prompt = (f'From this product page, find the price in INR of "{part_name}" (the main product, not related items). '
              'Reply JSON: {"product_name":"exact product title copied from the page","price_inr":number|null,"quote":"exact text copied from the page showing the price"}. '
              'Use null if unsure.\n\nPAGE:\n' + pagerules.price_window(page_text))
    r = _generate(prompt, types.GenerateContentConfig(response_mime_type="application/json"), key)
    try: d = json.loads(r.text)
    except Exception: return None
    q, p = d.get("quote") or "", d.get("price_inr")
    if not q or q not in page_text or not isinstance(p, (int, float)) or not pagerules.quote_has_amount(q, p): return None
    n = (d.get("product_name") or "").strip()
    name = n[:90] if n and n.lower() in page_text.lower() else None  # verbatim on the page, or not shown
    return dict(price=float(p), in_stock=None, quote=q, name=name)  # stock is never read: page text cannot say reliably

def extract_offer(page_text, part_name, use_rules=True, part=None, budget=None):
    """Read one shop page. 1) page rules (free, no LLM) 2) Gemini only as a backup, if `budget` has calls left.
    Either way the price needs a quote found verbatim on the page. A page that sells the original/genuine version
    of a generic part gets its price withheld with a note (clone-vs-original trap).
    budget: {"left": n} shared Gemini-call allowance, or None for unlimited (used by tests and direct calls)."""
    global _QUOTA_UNTIL
    part = part or dict(name=part_name, query=part_name)
    problem = pagerules.variant_problem(part, page_text) if use_rules else None
    offer = None
    if use_rules:
        hit = pagerules.rule_price(page_text, anchor=part.get("query", part_name))
        if hit and pagerules.quote_on_page(hit[1], page_text):
            offer = dict(price=hit[0], in_stock=None, quote=hit[1],
                         name=pagerules.page_name(page_text, part.get("query", part_name)), method="page rules")
    if offer is None and problem is None and not quota_off() and (budget is None or budget["left"] > 0):
        if budget is not None and os.getenv("GEMINI_API_KEY"): budget["left"] -= 1
        try: offer = _gemini_offer(page_text, part_name)
        except Exception as e:
            if any(x in str(e) for x in ("429", "RESOURCE_EXHAUSTED")): _QUOTA_UNTIL = time.time() + 60  # stop calling Gemini for a minute
            raise
        if offer: offer["method"] = "Gemini + quote check"
    if offer is not None and pagerules.bad_price_quote(offer.get("quote"), offer.get("price")):
        offer = dict(offer, price=None, in_stock=None, note="Price not used: it looks like an EMI or instalment figure, not the product price")
    if offer is None: return None
    offer["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if problem: offer = dict(offer, price=None, in_stock=None, note=f"Price not used: {problem}")
    return offer

BLOCKED = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "pinterest.", "reddit.com", "quora.com", "wikipedia.org", "github.com", "cirkitdesigner.com")

def usable(url):
    """Skip videos, social posts, simulators and category listings; keep likely product pages."""
    return not any(b in url for b in BLOCKED) and "/product-category/" not in url

def source_part(part, log=print, gemini_max=None):
    """Search -> Fetch -> verified read for one part. Returns offers (price None if unverified).
    Prices are read from the page by rules first (free); Gemini is only a backup, at most `gemini_max` pages per part
    (env GEMINI_FALLBACK_MAX, default 1; 0 = never use Gemini)."""
    if part.get("owned"):
        log(f"Skipping {part['name']}: this item is marked as already owned.")
        return []
    budget = dict(left=int(os.getenv("GEMINI_FALLBACK_MAX", "1")) if gemini_max is None else gemini_max)
    log(f"Searching for {part['name']}...")
    hits = tf.search(part["query"] + " buy India price")[:8]
    urls = [h["url"] for h in hits if h["url"] and usable(h["url"])][:4]
    log(f"Search: {len(hits)} results for {part['name']} ({len(urls)} look like shop pages)")
    pages = tf.fetch(urls) if urls else {}
    offers = []
    for u in urls:
        ck = f"x3:{u}|{part['name']}"  # x3: older cached reads may contain EMI figures read as prices, so they are ignored
        o = tf.cache_get(ck)  # only readable pages are cached, so failures are retried next time
        if o is None and pages.get(u):
            try: o = extract_offer(pages[u], part["name"], part=part, budget=budget)
            except Exception as e:
                log(("Gemini quota reached; using page rules only for now. " if quota_off() else "Gemini error: ") + str(e)[:120])
            if o: tf.cache_set(ck, o)
        offers.append(dict(url=u, **(o or dict(price=None, in_stock=None, quote=None, name=None))))
        if o and pagerules.trusted_price(o): log(f"Verified ₹{o['price']:,.0f} at {u} ({o.get('method', 'cached')})")
        else: log((o or {}).get("note") or "No verifiable price at " + u)
    for o in offers:   # flagged here, after all pages are read, so every consumer (BOM, bill, ledger, UI) sees the same flag
        if o["url"] in pagerules.suspect_low(part, offers):
            o["suspect"] = True
            o["suspect_note"] = "Unusually low (far below the team's estimate or the other listings). It may be an accessory or a different item, so it is never chosen by default. Tick it yourself only if you checked the page."
    return offers

def spread_warning(offers, ratio=3.0):
    """Warn when verified prices for one part differ a lot: usually different products or variants (e.g. clone vs original)."""
    v = [o["price"] for o in offers if pagerules.trusted_price(o)]
    if len(v) >= 2 and max(v) / min(v) > ratio:
        return f"Verified prices range ₹{min(v):,.0f} to ₹{max(v):,.0f}. These are likely different products or variants; check the names. The BOM uses the cheapest."

def price_of(part, live):
    pid = (part or {}).get("id")
    # Owned equipment has zero marginal BOM cost; don't revive an old market quote for it.
    if part and part.get("owned"):
        for key in ("seed_price", "price_inr", "price"):
            if part.get(key) is not None:
                return (part[key], "owned")
    vals = [o["price"] for o in (live or {}).get(pid, []) if pagerules.trusted_price(o) and not o.get("suspect")]
    if vals:
        return (min(vals), "live")
    if part and "seed_price" in part and part["seed_price"] is not None:
        return (part["seed_price"], "seed")
    if part and "price_inr" in part and part["price_inr"] is not None:
        return (part["price_inr"], "seed")
    if part and "price" in part and part["price"] is not None:
        return (part["price"], "seed")
    try:
        from . import profiles
        prof = profiles.get(pid) or profiles.get_by_name((part or {}).get("name"), (part or {}).get("role"))
        if prof and prof.get("price_inr") is not None:
            return (prof["price_inr"], "seed")
    except Exception:
        pass
    return (0.0, "seed")

def candidate_parts(robot):
    """Every catalog part this build could use (all controllers, drivers, motors, batteries, the right sensors) plus the extras.
    Pricing all of them, not only today's winner, keeps the cost comparison fair."""
    kind = PROJECTS[robot]["sensor"]
    return [p for p in CATALOG if not p.get("owned") and (p["role"] != "sensor" or (kind is not None and p["kind"] == kind))] + list(EXTRAS)

def select(robot, budget, needs_bt=False, live=None):
    live = live or {}
    by = {r: [p for p in CATALOG if p["role"] == r and not p.get("owned")] for r in ("controller", "driver", "motor", "battery", "sensor")}
    kind = PROJECTS[robot]["sensor"]
    by["sensor"] = [NONE_SENSOR] if kind is None else [p for p in by["sensor"] if p["kind"] == kind]
    rows = []
    for combo in itertools.product(*by.values()):
        parts = {**dict(zip(by.keys(), combo)), **{e["role"]: e for e in EXTRAS}}
        cost = sum(price_of(p, live)[0] * QTY.get(r, 1) for r, p in parts.items())
        checks = evaluate(parts, RULES, needs_bt)
        rows.append(dict(parts=parts, cost=cost, checks=checks, fails=sum(c["status"] == "FAIL" for c in checks), unknown=sum(c["status"] == "UNKNOWN" for c in checks)))
    ok = sorted([r for r in rows if r["fails"] == 0 and r["cost"] <= budget], key=lambda r: (r["unknown"], r["cost"]))
    return dict(best=ok[0] if ok else None, alternatives=ok[1:4], evaluated=len(rows), rejected=sum(r["fails"] > 0 for r in rows))


def _keyword_idea(text):
    """Free, deterministic first pass: map a short idea to a BOM-supported build by keywords."""
    idea = str(text or "").lower()
    rules = (("line_follower", ("line follower", "follows a line", "follow the line", "black path", "black line")),
             ("obstacle_avoider", ("obstacle", "avoid objects", "avoid collision")),
             ("bluetooth_car", ("bluetooth", "phone controlled", "phone control")))
    for project, phrases in rules:
        if any(phrase in idea for phrase in phrases):
            return dict(project=project, reason="Matched by a deterministic keyword rule to a supported build.")
    return None

def map_idea(text):
    """Map a free-text idea onto a supported project type: keyword rules first (no AI), then Gemini, or say honestly that it can't."""
    hit = _keyword_idea(text)
    if hit: return hit
    key = os.getenv("GEMINI_API_KEY")
    if not key: return dict(project=None, reason="No supported BOM project clearly matches this idea. Add a Gemini key to try a looser match.")
    from google.genai import types
    opts = "\n".join(f"- {k}: {v['label']} ({v['desc']})" for k, v in PROJECTS.items())
    prompt = ("A student describes a robot project idea. Pick the supported type whose parts and compatibility rules would fit it "
              "(wheeled robot with DC motors, controller, driver, battery, optional line or distance sensor). If none fits, e.g. a drone, "
              "walking robot or robot arm, answer null.\nSupported:\n" + opts +
              '\nReply JSON: {"project": key or null, "reason": "one short sentence"}\nIDEA: ' + text[:500])
    try: d = json.loads(_generate(prompt, types.GenerateContentConfig(response_mime_type="application/json"), key).text)
    except Exception as e: return dict(project=None, reason=f"Could not match: {str(e)[:100]}")
    return dict(project=d.get("project") if d.get("project") in PROJECTS else None, reason=str(d.get("reason", ""))[:200])


def extract_resume_factors(text):
    """Use Gemini only to structure resume evidence; unsupported claims are removed locally."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("Add GEMINI_API_KEY to analyze a resume. You can still get recommendations from the survey alone.")
    from google.genai import types
    prompt = (
        "Extract only explicit facts from this student's resume. Do not infer proficiency. Return JSON with these arrays: "
        '"skills":[{"id":"allowed_id","name":"short label","quote":"exact verbatim evidence"}], '
        '"languages":[{"id":"c_cpp|python","name":"language","quote":"exact verbatim evidence"}], '
        '"tools":[{"id":"soldering_iron|multimeter|3d_printer|college_lab|laptop","name":"tool","quote":"exact verbatim evidence"}], '
        '"projects":[{"name":"project name","domain":"embedded|robotics|vision|iot|mechanics|other",'
        '"skills":["skill"],"quote":"exact verbatim evidence"}]. '
        "Allowed skill IDs: sensor_read, soldering, motor_driver, breadboard, raspberry_pi, trained_model. "
        "Every item MUST include a short exact quote copied character-for-character from the resume. Omit items without evidence. "
        "Return empty arrays when there are no supported facts.\n\nRESUME TEXT:\n" + text[:resume_mod.MAX_CHARS]
    )
    try:
        response = _generate(prompt, types.GenerateContentConfig(response_mime_type="application/json"), key)
        extracted = json.loads(response.text or "{}")
    except Exception as error:
        raise ValueError(f"Gemini could not structure this resume ({type(error).__name__}).") from error
    return resume_mod.validate_factors(text, extracted)


def research_career_target(target):
    """Search and fetch public job/career pages; return source excerpts and deterministic domain signals."""
    target = str(target or "").strip()[:160]
    if not target:
        return dict(query="", signals=[], sources=[])
    query = f'"{target}" job skills requirements robotics embedded India'
    hits = tf.search(query, location="IN")[:5]
    urls, seen = [], set()
    for hit in hits:
        url = hit.get("url", "")
        if url.startswith(("https://", "http://")) and url not in seen:
            urls.append(url)
            seen.add(url)
        if len(urls) == 3:
            break
    pages = tf.fetch(urls) if urls else {}
    sources, searchable = [], [target.lower()]
    for hit in hits:
        url = hit.get("url", "")
        content = " ".join(str(pages.get(url, "") or hit.get("snippet", "")).split())[:1400]
        if not url or not content:
            continue
        sources.append(dict(title=str(hit.get("title", "Career source"))[:160], url=url, excerpt=content))
        searchable.append((str(hit.get("title", "")) + " " + content).lower())
    combined = " ".join(searchable)
    terms = {
        "embedded": ("embedded", "firmware", "microcontroller", "rtos", "stm32"),
        "robotics": ("robotics", "robot", "autonomous", "control systems"),
        "vision": ("computer vision", "opencv", "image processing", "machine learning", "deep learning"),
        "iot": ("iot", "wireless", "mqtt", "bluetooth", "connectivity"),
        "mechanics": ("mechanical", "cad", "fabrication", "solidworks", "3d printing"),
    }
    signals = [tag for tag, phrases in terms.items() if any(phrase in combined for phrase in phrases)]
    return dict(query=query, signals=signals, sources=sources)
