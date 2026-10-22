"""Project Optimizer: replace a part, recompute everything that can be computed, report the rest honestly.
Data flow: part profiles (core.profiles) -> pair dataset (core.swap_dataset) -> system metrics (core.impact).
Missing profiles are filled by core.profile_fill (silent) and stored, so each new swap is answered from the dataset next time.
Pure Python. The original build is never mutated: every function copies."""
import copy, hashlib, json, os
from . import component_catalog as cat, compatibility_engine as ce, engineering_calculations as calc, impact, profile_fill, profiles, swap_dataset
from .data import CATALOG, QTY
from .pipeline import price_of

ROLES = ("controller", "driver", "motor", "battery", "sensor", "power", "actuator", "display", "comms", "input", "camera", "converter")
GOALS = dict(lowest_cost="Lowest cost", fewest_issues="Fewest compatibility issues", connectivity="Better connectivity",
             longer_runtime="Longer battery life", higher_speed="Higher speed", higher_torque="Higher torque",
             lower_weight="Lower weight", processing="Better processing capability", balanced="Balanced")

def snapshot(parts): return copy.deepcopy(parts)

def merge_specs(part, record):
    """Return a copy of `part` whose specs use VERIFIED catalog values where the catalog has them (seed values otherwise).
    Adds _provenance {spec: 'verified'|'seed'} and _conflicts [(spec, seed, verified)]."""
    p = copy.deepcopy(part); p["_provenance"] = {k: "seed" for k in p.get("specs", {})}; p["_conflicts"] = []; p["_verified"] = {}
    if not record: return p
    ver = cat.verified_fields(record)
    for ck, v in ((k, cat.spec_value(record, k)) for k in ver):
        if v is None: continue
        p["_verified"][ck] = v
        lk = cat.KEYMAP.get(ck)
        if lk:
            if lk in p.get("specs", {}) and p["specs"][lk] != v: p["_conflicts"].append((lk, p["specs"][lk], v))
            p.setdefault("specs", {})[lk] = v; p["_provenance"][lk] = "verified"
    p["_record"] = record["id"]
    return p

def effective(parts, records):
    by = cat.by_legacy(records)
    return {r: merge_specs(p, by.get(p.get("id"))) for r, p in parts.items()}

def replace(parts, role, new_part):
    """New build with one role swapped. Original dict untouched."""
    if role not in ROLES and role not in parts:
        raise ValueError(f"Unknown or unsupported role: {role}")
    p_role = new_part.get("role")
    current = parts.get(role, {})
    current_role = current.get("role", role)
    eff_role = profiles.ROLE_ALIASES.get(role, profiles.ROLE_ALIASES.get(current_role, current_role))
    eff_p_role = profiles.ROLE_ALIASES.get(p_role, p_role) if p_role else None
    if p_role != role and eff_p_role != eff_role and not new_part.get("none"):
        raise ValueError(f"Cannot replace {role} with part of role {p_role}")
    out = snapshot(parts)
    replacement = copy.deepcopy(new_part)
    if not replacement.get("qty") and isinstance(current, dict) and current.get("qty"):
        replacement["qty"] = current["qty"]
    out[role] = replacement
    return out


# ------------------------------------------------------------------ candidates
KIND_OK = dict(controller={"mcu"}, driver={"dc"}, motor={"dc_gear"}, battery=None)       # kinds that fit a wheeled DC-motor robot

def _known_ids():
    ids = set()
    for p in CATALOG:
        ids.add(p["id"]); r = profiles.get(p["id"])
        if r: ids.update(x for x in (r["id"], r.get("component_id")) if x)
    return ids

def candidates(parts, role, sensor_kind=None):
    """Same-role parts: the app catalog plus every profile in the dataset (including ones added at run time). Current part first."""
    eff_role = profiles.ROLE_ALIASES.get(role, role)
    pool = [p for p in CATALOG if p["role"] in (role, eff_role) and (role != "sensor" or sensor_kind is None or p.get("kind") == sensor_kind)]
    known = _known_ids(); ok = KIND_OK.get(role) or KIND_OK.get(eff_role)
    all_profs = profiles.for_role(role)
    if eff_role != role:
        all_profs += profiles.for_role(eff_role)
    for pr in all_profs:
        if pr["id"] in known or pr.get("component_id") in known: continue
        if role == "sensor" and sensor_kind is not None and pr.get("kind") != sensor_kind: continue
        if ok is not None and pr.get("kind") not in ok: continue
        pool.append(profiles.as_part(pr))
    cur = parts.get(role, {}).get("id") if isinstance(parts.get(role), dict) else None
    # Deduplicate pool by id
    seen, dedup = set(), []
    for p in pool:
        if p["id"] not in seen:
            seen.add(p["id"])
            dedup.append(p)
    return sorted(dedup, key=lambda p: (p["id"] != cur, p["name"]))

def custom_part(role, text):
    """A part the user typed that is not listed. Returns a part dict, or None if no data could be produced right now."""
    pr = profile_fill.fill(role, text)
    return profiles.as_part(pr) if pr else None

def profile_for(part, allow_fill=True):
    """Profile for a part dict; fills it (silently) when missing and allowed. None for 'no sensor' or if unavailable."""
    if not isinstance(part, dict) or part.get("none"): return None
    pr = profiles.get(part.get("id"))
    if pr: return pr
    name = part.get("name", "")
    pr = profiles.get_by_name(name, part.get("role"))
    if pr: return pr
    if allow_fill and name:
        pr = profile_fill.fill(part.get("role", "part"), name, part.get("id"))
    return pr

def _profiles(parts, allow_fill): return {r: profile_for(p, allow_fill) for r, p in parts.items()}

def _price(parts, live):
    lines, srcs = [], []
    for r, p in parts.items():
        if not isinstance(p, dict) or p.get("none"): continue
        pr, s = price_of(p, live); lines.append((pr, QTY.get(r, 1))); srcs.append(s)
    q = ("verified live prices" if srcs and all(s == "live" for s in srcs)
         else "owned equipment at ₹0 plus seed estimates" if "owned" in srcs and any(s == "seed" for s in srcs)
         else "owned equipment at ₹0" if srcs and all(s == "owned" for s in srcs)
         else "estimates only" if all(s == "seed" for s in srcs)
         else "mixed: some live, some estimates")
    return calc.bom_total(lines), q

def _row(key, name, unit, orig, prop, higher, quality, reason, evidence=None):
    c = calc.compare(orig, prop, higher)
    return dict(key=key, metric=name, unit=unit, original=orig, proposed=prop, diff=c["diff"], pct=c["pct"], status=c["status"], reason=reason, quality=quality, evidence=evidence or [])

def compare_builds(original, proposed, changed, records, live=None, needs_bt=False, allow_fill=True, wheel_d_m=None):
    """original/proposed: role -> part. changed: roles that differ. Returns rows + 'what changes' chips + compatibility results."""
    live = live or {}
    eo, ep = effective(original, records), effective(proposed, records)
    co, cp = ce.check(eo, needs_bt=needs_bt), ce.check(ep, needs_bt=needs_bt)
    so, sp = ce.summary(co), ce.summary(cp)
    to, qo = _price(original, live); tp, qp = _price(proposed, live)
    rows = [_row("cost", "Total BOM cost", "INR", to.value, tp.value, False, qp, f"Sum of quantity x unit price ({qp}). Original: {qo}.")]
    rank = dict(compatible=0, caution=1, incompatible=2)
    cs = calc.compare(rank[so["state"]], rank[sp["state"]], False)
    rows.append(dict(key="compat", metric="Compatibility", unit="", original=so["state"], proposed=sp["state"], diff=None, pct=None,
                     status="incompatible" if sp["state"] == "incompatible" else cs["status"],
                     reason=f"Proposed: {sp['fail']} fail, {sp['warn']} warning, {sp['unknown']} unknown, {sp['passed']} pass.", quality="rules", evidence=[]))
    po, pp = _profiles(original, allow_fill), _profiles(proposed, allow_fill)
    n_motors = QTY.get("motor", 1)
    imp = impact.rows(original, proposed, set(changed), po, pp, n_motors, wheel_d_m or impact.WHEEL_D_M)
    rows += imp["rows"]
    chips, how = [], {}
    for r in changed:
        if po.get(r) and pp.get(r):
            part_role = original.get(r, {}).get("role", r)
            rec, src = swap_dataset.lookup(part_role, po[r], pp[r]); chips += rec["chips"]; how[r] = src
    chips += impact.system_chips(imp["d0"], imp["d1"])
    missing = [original[r]["name"] if po.get(r) is None else proposed[r]["name"] for r in changed if r in original and r in proposed and (po.get(r) is None or pp.get(r) is None)]
    return dict(rows=rows, chips=chips, data_gaps=missing, compat_original=co, compat_proposed=cp, summary_original=so, summary_proposed=sp, lookup=how,
                conflicts=[(side, role, p["name"], c) for side, parts in (("original", eo), ("proposed", ep)) for role, p in parts.items() for c in p["_conflicts"]])

def _rec(part, records): return cat.by_legacy(records).get(part["id"])

# ------------------------------------------------------------------ goals / ranking
GOAL_METRIC = dict(higher_speed=("speed", True), higher_torque=("torque", True), longer_runtime=("runtime", True), lower_weight=("weight", False), processing=("processing", True))
def rank_candidates(parts, role, goal, records, live=None, needs_bt=False, sensor_kind=None):
    """Evaluate every same-role candidate against the SAME baseline. Candidates the data cannot rank for the goal go last, flagged, never scored."""
    out = []
    for c in candidates(parts, role, sensor_kind):
        new = replace(parts, role, c)
        res = compare_builds(parts, new, {role}, records, live, needs_bt, allow_fill=False)
        rows = {r["key"]: r for r in res["rows"]}
        out.append(dict(part=c, state=res["summary_proposed"]["state"], cost=rows["cost"]["proposed"], rows=rows, result=res))
    ok = [o for o in out if o["state"] != "incompatible"]
    sp = lambda o: o["result"]["summary_proposed"]
    keyfun, metric = None, None
    if goal == "lowest_cost": keyfun = lambda o: (o["cost"] is None, o["cost"] or 0)
    elif goal in ("fewest_issues", "balanced"): keyfun = lambda o: (sp(o)["fail"], sp(o)["warn"], sp(o)["unknown"], o["cost"] or 0)
    elif goal == "connectivity" and role == "controller": keyfun = lambda o: -(2 * (o["rows"]["wifi"]["proposed"] or 0) + (o["rows"]["bluetooth"]["proposed"] or 0))
    elif goal in GOAL_METRIC:
        metric, hib = GOAL_METRIC[goal]
        val = lambda o: o["rows"].get(metric, {}).get("proposed")
        if goal == "processing" and role != "controller": keyfun = None
        elif sum(val(o) is not None for o in ok) >= 2: keyfun = lambda o: (val(o) is None, -(val(o) or 0) if hib else (val(o) or 0))
    if keyfun:
        ok.sort(key=keyfun)
        return dict(goal=goal, rankable=True, ranked=ok, incompatible=[o for o in out if o["state"] == "incompatible"], note="")
    return dict(goal=goal, rankable=False, ranked=ok, incompatible=[o for o in out if o["state"] == "incompatible"],
                note=f"'{GOALS.get(goal, goal)}' does not apply to this part type, or there is not enough data to rank it. Showing candidates unranked.")

# ------------------------------------------------------------------ explanation (deterministic; every sentence comes from a computed row)
def plain_explanation(res):
    lines = []
    for c in res.get("chips", []): lines.append(f"- {c['text']}")
    for r in res["rows"]:
        if r["status"] == "not_affected" or r["key"] in ("cost", "compat"): continue
        if r["status"] == "unknown": lines.append(f"- {r['metric']}: not available. {r['reason']}")
    cost = next(r for r in res["rows"] if r["key"] == "cost")
    if cost["diff"] is not None and cost["diff"] != 0: lines.append(f"- Cost {'rises' if cost['diff'] > 0 else 'falls'} by {abs(cost['diff']):,.0f} INR.")
    sp = res["summary_proposed"]
    if sp["fail"]: lines.append(f"- Not buildable as is: {sp['fail']} compatibility rule(s) fail.")
    return "\n".join(lines) or "Nothing changed."
