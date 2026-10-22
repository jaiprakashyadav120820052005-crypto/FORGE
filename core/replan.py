"""Pivot A re-plan: a part sold out AND the budget dropped. Re-plan without silently breaking anything, and say what changed and why.

Design rules (each one is what "silently breaking" would look like if it were missing):
  1. DRY RUN. plan() never mutates the build, the session or the live offers. The user accepts or discards the proposal.
  2. NO RULE GETS WORSE. A move is allowed only if, for every compatibility rule, the new status is not more severe than the old one
     (PASS < UNKNOWN < WARN < FAIL). Missing data is never treated as a pass.
  3. NO PERFORMANCE CLIFF. A move is rejected if a headline metric (speed, torque, runtime) falls more than DEGRADE_LIMIT, or goes from known to unknown.
  4. EVERY CHANGE HAS A REASON. 'sold_out' or 'budget', with the numbers behind it, the price source (live / seed) and the impact chips.
  5. HONEST FAILURE. If nothing safe fits, the result says so (status 'over_budget' or 'blocked') and keeps the closest safe plan.
Pure functions: no network, no LLM. Everything is testable offline.
"""
import copy

from . import bill, compatibility_engine as ce, component_catalog as cat, optimizer as opt, wiring
from .data import QTY
from .pipeline import price_of

SEVERITY = {"PASS": 0, "UNKNOWN": 1, "WARN": 2, "FAIL": 3}
HEADLINE = ("speed", "torque", "runtime")      # metrics a user would notice if they silently got much worse
DEGRADE_LIMIT = 0.25                           # a headline metric may fall at most 25 % in a budget move
MAX_MOVES = 12                                 # hard stop for the greedy loop


# ------------------------------------------------------------------ small helpers
def mark_sold_out(live, part_id, urls=None):
    """Copy of `live` where the part's offers are flagged in_stock=False. urls=None means every listing for the part."""
    out = copy.deepcopy(live or {})
    for o in out.get(part_id, []):
        if urls is None or o.get("url") in urls:
            o["in_stock"] = False
    return out


def _sev(rows):
    return {r["rule_id"]: SEVERITY.get(r["status"], 3) for r in rows}


def _regressions(before_rows, after_rows):
    """Rules that got MORE severe. A rule that appears only after the move counts from PASS (a new rule can only add risk)."""
    b, a = _sev(before_rows), _sev(after_rows)
    names = {r["rule_id"]: r["title"] for r in after_rows + before_rows}
    status = {r["rule_id"]: r["status"] for r in after_rows}
    was = {r["rule_id"]: r["status"] for r in before_rows}
    return [dict(rule_id=k, title=names.get(k, k), was=was.get(k, "PASS"), now=status[k])
            for k in a if a[k] > b.get(k, 0)]


def _line_total(part, live, role):
    unit, src = price_of(part, live)
    qty = part.get("qty") or QTY.get(part.get("role", role), 1)
    return unit, src, qty


def _total(parts, live):
    """(subtotal, n_unpriced). A part with no price at all is reported, never silently counted as zero cost."""
    total, unpriced = 0.0, []
    for role, p in parts.items():
        if not isinstance(p, dict) or p.get("none"):
            continue
        unit, src, qty = _line_total(p, live, role)
        if not unit:
            unpriced.append(p.get("name", role))
        total += unit * qty
    return total, unpriced


def _part_view(part, live, role):
    if not part:
        return None
    unit, src, qty = _line_total(part, live, role)
    return dict(id=part.get("id"), name=part.get("name"), unit=unit, qty=qty, price_source=src)


def _headline_ok(rows):
    """True if no headline metric fell more than DEGRADE_LIMIT or became unknown. Returns (ok, reason)."""
    for r in rows:
        if r["key"] not in HEADLINE:
            continue
        if r["status"] == "not_affected":
            continue
        o, p = r.get("original"), r.get("proposed")
        if o is not None and p is None:
            return False, f"{r['metric']} can no longer be computed for this part"
        if o and p is not None and (o - p) / abs(o) > DEGRADE_LIMIT:
            return False, f"{r['metric']} would fall by {100 * (o - p) / abs(o):.0f}% (limit {int(DEGRADE_LIMIT * 100)}%)"
    return True, ""



def _wiring_problem(parts, new_parts):
    """None if the proposed build can still be wired by FORGE, else why not.
    core/wiring.py only knows the Nano, Uno and ESP32 pin maps and falls back to the Nano map for ANY other controller,
    so a swap to e.g. a Digispark would draw a wiring diagram and firmware for the wrong board without any warning."""
    ctrl = next((p for p in new_parts.values() if isinstance(p, dict) and p.get("role") == "controller"), None)
    if ctrl:
        fam, _ = wiring._detect_controller({"controller": ctrl})
        text = f"{ctrl.get('id', '')} {ctrl.get('name', '')}".lower()
        if fam not in text:
            return f"FORGE has no pin map for {ctrl.get('name')} yet; wiring and firmware would silently use the Arduino Nano pinout"
    try:
        data = wiring.generate_wiring(new_parts, project_label="replan-check")
    except Exception as e:                                   # a swap must never leave the Wiring Studio crashing
        return f"wiring generation fails for this build ({type(e).__name__})"
    def clashes(conns):
        used, bad = {}, 0
        for c in conns:
            if c.get("type") in ("PWM", "DIGITAL", "ANALOG"):
                k = (c.get("to_part"), c.get("to_pin"))
                bad += k in used
                used[k] = True
        return bad
    try:
        before = clashes(wiring.generate_wiring(parts, project_label="replan-check")["connections"])
    except Exception:
        before = 0
    if clashes(data["connections"]) > before:
        return "two signals would share one controller pin"
    return None

def _evaluate_move(parts, role, cand, records, live, needs_bt, base_rows):
    """Evaluate ONE swap against the current build. Returns dict(ok, why, ...) so a rejection always has a reason."""
    new = opt.replace(parts, role, cand)
    res = opt.compare_builds(parts, new, {role}, records, live, needs_bt, allow_fill=False)
    if res["data_gaps"]:
        return dict(ok=False, why="not enough data on this part to check the swap", cand=cand)
    reg = _regressions(base_rows, res["compat_proposed"])
    if reg:
        return dict(ok=False, why="would worsen: " + ", ".join(r["title"] for r in reg), cand=cand, regressions=reg)
    good, why = _headline_ok(res["rows"])
    if not good:
        return dict(ok=False, why=why, cand=cand)
    wprob = _wiring_problem(parts, new)
    if wprob:
        return dict(ok=False, why=wprob, cand=cand)
    cost_row = next(r for r in res["rows"] if r["key"] == "cost")
    return dict(ok=True, cand=cand, parts=new, res=res, cost=cost_row["proposed"], why="")


def _worse_rows(res):
    return sum(r["status"] == "reduced" for r in res["rows"] if r["key"] not in ("cost", "compat"))


def _chips(res):
    return [c["text"] for c in res.get("chips", [])][:6]


# ------------------------------------------------------------------ the plan
def plan(parts, live, *, budget_old, budget_new=None, cut_pct=None, sold_out=None, needs_bt=False, sold_out_urls=None):
    """Return a proposal. `parts` role->part, `live` {part_id: [offers]}, `sold_out` iterable of part ids,
    `sold_out_urls` {part_id: [urls]} for listing-level sold-out (the part itself may be fine at another shop)."""
    if budget_new is None:
        if cut_pct is None:
            raise ValueError("give budget_new or cut_pct")
        budget_new = round(budget_old * (1 - cut_pct / 100.0))
    sold_out = set(sold_out or [])
    sold_out_urls = sold_out_urls or {}
    records = cat.load()

    work_live = copy.deepcopy(live or {})
    for pid, urls in sold_out_urls.items():
        work_live = mark_sold_out(work_live, pid, set(urls))

    original = opt.snapshot(parts)
    base_check = ce.check(opt.effective(original, records), needs_bt=needs_bt)
    before_total, before_unpriced = _total(original, live or {})
    current, changes, unresolved = opt.snapshot(original), [], []

    # ---- phase 1: availability (sold out)
    for role, part in list(original.items()):
        if not isinstance(part, dict) or part.get("none"):
            continue
        pid = part.get("id")
        urls_gone = set(sold_out_urls.get(pid, []))
        part_gone = pid in sold_out
        if not part_gone and not urls_gone:
            continue
        if part_gone:
            work_live = mark_sold_out(work_live, pid)
        else:
            # listing-level: if another verified, in-stock listing exists the part itself is unchanged, only the vendor moves
            from .pagerules import trusted_price
            left = [o for o in work_live.get(pid, []) if trusted_price(o) and o.get("in_stock") is not False]
            was = [o for o in (live or {}).get(pid, []) if trusted_price(o) and o.get("url") in urls_gone]
            if left:
                best = min(left, key=lambda o: o["price"])
                changes.append(dict(
                    role=role, kind="vendor_switch", from_part=_part_view(part, live or {}, role), to_part=_part_view(part, work_live, role),
                    reasons=[dict(code="sold_out", text=f"The listing you picked is sold out. Same part, next cheapest in-stock listing: {bill.vendor(best['url'])}.")],
                    cost_delta=(best["price"] - (min((o["price"] for o in was), default=best["price"]))) * (part.get("qty") or QTY.get(part.get("role", role), 1)),
                    compat=dict(regressions=[]), impact=[], source_url=best["url"]))
                continue
        cands = [c for c in opt.candidates(current, part.get("role", role), part.get("kind") if part.get("role") == "sensor" else None)
                 if c["id"] != pid and c["id"] not in sold_out]
        tried, ok = [], []
        for c in cands:
            ev = _evaluate_move(current, role, c, records, work_live, needs_bt, base_check)
            (ok if ev["ok"] else tried).append(ev)
        if not ok:
            unresolved.append(dict(role=role, part=part.get("name"), code="sold_out",
                                   text=f"{part.get('name')} is sold out and no safe replacement was found ({len(tried)} candidates rejected). "
                                        "The build is left unchanged for this line so you can decide.",
                                   rejected=[dict(name=t["cand"]["name"], why=t["why"]) for t in tried[:5]]))
            continue
        orig_unit = price_of(part, live or {})[0]
        # closest equivalent first: fewest metrics that get worse, then nearest price
        ok.sort(key=lambda e: (_worse_rows(e["res"]), abs(price_of(e["cand"], work_live)[0] - orig_unit)))
        pick = ok[0]
        changes.append(_change(role, part, pick, live or {}, work_live, "sold_out",
                               f"{part.get('name')} is sold out. Closest safe equivalent: fewest performance trade-offs, nearest price."))
        current = pick["parts"]

    # ---- phase 2: budget
    total, unpriced = _total(current, work_live)
    guard_notes = []
    for _ in range(MAX_MOVES):
        if total <= budget_new:
            break
        base_now = ce.check(opt.effective(current, records), needs_bt=needs_bt)
        best = None
        for role, part in current.items():
            if not isinstance(part, dict) or part.get("none"):
                continue
            cur_unit = price_of(part, work_live)[0]
            if not cur_unit:
                continue
            kind = part.get("kind") if part.get("role") == "sensor" else None
            for c in opt.candidates(current, part.get("role", role), kind):
                if c["id"] == part.get("id") or c["id"] in sold_out:
                    continue
                c_unit = price_of(c, work_live)[0]
                if not c_unit or c_unit >= cur_unit:     # unpriced candidates are never 'cheaper': 0 means unknown, not free
                    continue
                ev = _evaluate_move(current, role, c, records, work_live, needs_bt, base_now)
                if not ev["ok"]:
                    continue
                saving = total - ev["cost"]
                if saving <= 0:
                    continue
                key = (-saving, _worse_rows(ev["res"]))
                if best is None or key < best[0]:
                    best = (key, role, ev)
        if best is None:
            break
        _, role, ev = best
        part = current[role]
        changes.append(_change(role, part, ev, work_live, work_live, "budget",
                               f"Budget fell from ₹{budget_old:,.0f} to ₹{budget_new:,.0f}. This is the largest safe saving available; nothing it touches got worse than the checks allow."))
        current = ev["parts"]
        total, unpriced = _total(current, work_live)

    # ---- merge repeat moves on one role into one line (A -> B -> C reads as A -> C)
    changes = _collapse(changes)

    after_check = ce.check(opt.effective(current, records), needs_bt=needs_bt)
    regs = _regressions(base_check, after_check)
    sb, sa = ce.summary(base_check), ce.summary(after_check)
    fits = total <= budget_new
    status = "blocked" if unresolved and not changes else "fits" if fits and not unresolved else "over_budget" if not fits else "partial"
    return dict(
        status=status,
        budget=dict(old=budget_old, new=budget_new, cut_pct=round(100 * (budget_old - budget_new) / budget_old, 1) if budget_old else None,
                    before_total=before_total, after_total=total, left=budget_new - total, shortfall=max(0.0, total - budget_new)),
        changes=changes,
        unchanged=[r for r, p in current.items() if isinstance(p, dict) and not p.get("none") and original.get(r, {}).get("id") == p.get("id")],
        unresolved=unresolved,
        guards=dict(compat_before=sb, compat_after=sa, regressions=regs, no_rule_worse=not regs,
                    unpriced_before=before_unpriced, unpriced_after=unpriced,
                    stale=["wiring", "firmware", "bill", "ledger"] if changes else []),
        swaps={c["role"]: c["_part"] for c in changes if c["kind"] != "vendor_switch"},
        sold_out=sorted(sold_out), sold_out_urls={k: list(v) for k, v in sold_out_urls.items()},
    )


def _change(role, old_part, ev, live_before, live_after, code, text):
    cand, res = ev["cand"], ev["res"]
    old, new = _part_view(old_part, live_before, role), _part_view(cand, live_after, role)
    return dict(
        role=role, kind="part_swap", from_part=old, to_part=new, _part=cand,
        reasons=[dict(code=code, text=text)],
        cost_delta=(new["unit"] * new["qty"]) - (old["unit"] * old["qty"]),
        compat=dict(before=res["summary_original"]["state"], after=res["summary_proposed"]["state"], regressions=[]),
        impact=_chips(res),
        data_quality=dict(old=old["price_source"], new=new["price_source"]),
    )


def _collapse(changes):
    out = {}
    for c in changes:
        if c["role"] in out and c["kind"] == "part_swap" and out[c["role"]]["kind"] == "part_swap":
            first = out[c["role"]]
            first["to_part"], first["_part"], first["impact"] = c["to_part"], c["_part"], c["impact"]
            first["compat"], first["data_quality"] = c["compat"], c["data_quality"]
            first["cost_delta"] = (first["to_part"]["unit"] * first["to_part"]["qty"]) - (first["from_part"]["unit"] * first["from_part"]["qty"])
            first["reasons"] += c["reasons"]
        else:
            out[c["role"]] = c
    return list(out.values())


def public(proposal):
    """JSON-safe proposal: the internal part dicts used to apply swaps are removed from the changes list."""
    p = copy.deepcopy(proposal)
    for c in p["changes"]:
        c.pop("_part", None)
    p["swaps"] = {r: dict(id=x["id"], name=x["name"]) for r, x in proposal["swaps"].items()}
    return p


def reapply_sold_out(live, sold_out_ids, sold_out_urls):
    """Fresh sourcing creates new offers with in_stock=None. Call this afterwards so a sold-out listing never comes back as buyable."""
    out = copy.deepcopy(live or {})
    for pid in sold_out_ids or []:
        out = mark_sold_out(out, pid)
    for pid, urls in (sold_out_urls or {}).items():
        out = mark_sold_out(out, pid, set(urls))
    return out
