import copy
from typing import List, Optional
from fastapi import APIRouter, Cookie, HTTPException

from core import auto_fix, component_catalog as cat, compatibility_engine, optimizer as opt, pipeline
from core.data import PROJECTS
from server.schemas import OptimizerFixRequest, OptimizerSwapRequest
from server.state import store

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])


def _active_parts(session):
    parts = session.get_active_parts()
    if not parts:
        raise HTTPException(status_code=400, detail="Generate a BOM before using the Project Optimizer")
    return parts


def _candidates(parts, role):
    current = parts.get(role)
    if not isinstance(current, dict) or current.get("none"):
        raise HTTPException(status_code=404, detail=f"Component role '{role}' not found in the active BOM")
    sensor_kind = current.get("kind") if current.get("role") == "sensor" else None
    return [candidate for candidate in opt.candidates(parts, current.get("role", role), sensor_kind)
            if candidate.get("id") != "pow_power_bank"]


def _find_candidate(parts, role, part_id):
    candidate = next((part for part in _candidates(parts, role) if part["id"] == part_id), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"Part '{part_id}' is not a valid replacement for '{role}'")
    return candidate


def _evaluate(session, parts, role, part_id, staged_fix_ids: Optional[List[str]] = None):
    candidate = _find_candidate(parts, role, part_id)
    proposed = opt.replace(parts, role, candidate)
    changed_roles = {role}
    staged_fix_ids = staged_fix_ids or []

    for f_id in staged_fix_ids:
        fix = auto_fix.AUTO_FIX_CATALOG.get(f_id)
        if not fix:
            continue
        f_part = copy.deepcopy(fix["part"])
        if fix.get("action") == "swap":
            swap_role = fix.get("swap_role", f_part.get("role"))
            proposed[swap_role] = f_part
            changed_roles.add(swap_role)
        else:
            role_key = f_part.get("role", "mod_" + f_id)
            if role_key in proposed:
                role_key = f"{role_key}_{f_id}"
            f_part["role"] = role_key
            proposed[role_key] = f_part
            changed_roles.add(role_key)

    records = cat.load()
    needs_bt = session.bluetooth or PROJECTS.get(session.project, {}).get("bt", False)
    evaluation = opt.compare_builds(
        original=parts,
        proposed=proposed,
        changed=changed_roles,
        records=records,
        live=session.live,
        needs_bt=needs_bt,
    )

    # Evaluate un-staged candidate to know what was originally failing
    proposed_raw = opt.replace(parts, role, candidate)
    raw_eval = opt.compare_builds(
        original=parts,
        proposed=proposed_raw,
        changed={role},
        records=records,
        live=session.live,
        needs_bt=needs_bt,
    )

    # Attach auto-fixes for current rules
    for rule in evaluation.get("compat_proposed", []):
        r_id = rule.get("rule_id") or rule.get("id", "")
        fixes = auto_fix.get_fixes_for_rule(r_id, proposed)
        if not fixes:
            raw_fixes = auto_fix.get_fixes_for_rule(r_id, proposed_raw)
            fixes = [f for f in raw_fixes if f["id"] in staged_fix_ids]
        rule["auto_fixes"] = [
            {
                "id": f["id"],
                "label": f["label"],
                "icon": f.get("icon", "🛠️"),
                "description": f.get("description", ""),
                "action": f.get("action", "insert"),
                "staged": f["id"] in staged_fix_ids,
            }
            for f in fixes
        ]

    # Check for rules resolved by staged fixes
    raw_failures = [r for r in raw_eval.get("compat_proposed", []) if r.get("status") == "FAIL"]
    resolved_failures = []
    for rf in raw_failures:
        r_id = rf.get("rule_id") or rf.get("id", "")
        current_rule = next((r for r in evaluation.get("compat_proposed", []) if (r.get("rule_id") or r.get("id")) == r_id), None)
        if current_rule and current_rule.get("status") == "PASS":
            applicable_fixes = auto_fix.get_fixes_for_rule(r_id, proposed_raw)
            resolved_failures.append({
                "rule_id": r_id,
                "title": rf.get("title", ""),
                "components": rf.get("components", ""),
                "explanation": rf.get("explanation", ""),
                "action": rf.get("action", ""),
                "status": "RESOLVED",
                "auto_fixes": [
                    {
                        "id": f["id"],
                        "label": f["label"],
                        "icon": f.get("icon", "🛠️"),
                        "description": f.get("description", ""),
                        "action": f.get("action", "insert"),
                        "staged": f["id"] in staged_fix_ids,
                    }
                    for f in applicable_fixes
                ],
            })
    evaluation["resolved_failures"] = resolved_failures

    # Format catalog corrections
    catalog_corrections = []
    for item in evaluation.get("conflicts", []):
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            side, r_, nm, c = item[0], item[1], item[2], item[3]
            if isinstance(c, (list, tuple)) and len(c) == 3:
                k, seed, ver = c
                catalog_corrections.append({
                    "side": side,
                    "role": r_,
                    "part_name": nm,
                    "field": k,
                    "seed": seed,
                    "verified": ver,
                    "message": f"Catalog correction ({side}): {nm} {k} is {seed} in FORGE's team seed, but official documentation says {ver}. The verified value is used.",
                })
    evaluation["catalog_corrections"] = catalog_corrections
    evaluation["plain_explanation"] = opt.plain_explanation(evaluation)

    return candidate, evaluation


@router.get("/candidates")
def get_swap_candidates(role: str, forge_session: Optional[str] = Cookie(None)):
    """Return catalog candidates for a given role."""
    session = store.get_or_create(forge_session)
    parts = _active_parts(session)
    candidates = _candidates(parts, role)
    return [{"id": c["id"], "name": c["name"], "price": c.get("seed_price")} for c in candidates]


@router.post("/evaluate")
def evaluate_swap(req: OptimizerSwapRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = _active_parts(session)
    _, eval_res = _evaluate(session, parts, req.role, req.swap_part_id, req.staged_fixes)
    summary = eval_res["summary_proposed"]
    return {"evaluation": eval_res, "summary": summary}


@router.post("/apply")
def apply_swap(req: OptimizerSwapRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = _active_parts(session)
    candidate, evaluation = _evaluate(session, parts, req.role, req.swap_part_id, req.staged_fixes)
    summary = evaluation["summary_proposed"]
    if not summary["ready"]:
        raise HTTPException(status_code=409, detail="Replacement fails compatibility checks and cannot be applied")
    session.opt_swaps[req.role] = candidate

    if req.staged_fixes:
        for f_id in req.staged_fixes:
            fix = auto_fix.AUTO_FIX_CATALOG.get(f_id)
            if not fix:
                continue
            f_part = copy.deepcopy(fix["part"])
            if fix.get("action") == "swap":
                swap_role = fix.get("swap_role", f_part.get("role"))
                session.opt_swaps[swap_role] = f_part
            else:
                existing_key = next((k for k, p in session.opt_swaps.items() if isinstance(p, dict) and p.get("id") == f_part.get("id")), None)
                if existing_key:
                    session.opt_swaps[existing_key] = f_part
                else:
                    role_key = f_part.get("role", "mod_" + f_id)
                    if role_key in session.opt_swaps and session.opt_swaps[role_key].get("id") != f_part.get("id"):
                        role_key = f"{role_key}_{f_id}"
                    f_part["role"] = role_key
                    session.opt_swaps[role_key] = f_part

    # TinyFish sourcing if not yet sourced
    parts_to_source = [candidate]
    if req.staged_fixes:
        for f_id in req.staged_fixes:
            if f_id in auto_fix.AUTO_FIX_CATALOG:
                parts_to_source.append(auto_fix.AUTO_FIX_CATALOG[f_id]["part"])

    for p in parts_to_source:
        pid = p.get("id")
        if p.get("owned"):
            continue
        if pid and pid not in session.live:
            try:
                p_copy = copy.deepcopy(p)
                p_copy.setdefault("query", p_copy.get("name", ""))
                session.live[pid] = pipeline.source_part(p_copy)
            except Exception:
                pass

    return {"status": "ok", "swaps": {role: part.get("id") for role, part in session.opt_swaps.items()}}


@router.post("/apply-fix")
def apply_compatibility_fix(req: OptimizerFixRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = _active_parts(session)
    checks = compatibility_engine.check(
        opt.effective(parts, cat.load()),
        needs_bt=session.bluetooth or PROJECTS.get(session.project, {}).get("bt", False),
    )
    failed_ids = {row.get("rule_id") for row in checks if row.get("status") == "FAIL"}
    fix = auto_fix.AUTO_FIX_CATALOG.get(req.fix_id)
    offered = [option for rule_id in failed_ids for option in auto_fix.get_fixes_for_rule(rule_id, parts)]
    if not fix or fix not in offered:
        raise HTTPException(status_code=409, detail="This fix is not available for a current compatibility failure")

    proposed = auto_fix.apply_auto_fix(parts, req.fix_id)
    proposed_checks = compatibility_engine.check(
        opt.effective(proposed, cat.load()),
        needs_bt=session.bluetooth or PROJECTS.get(session.project, {}).get("bt", False),
    )
    unresolved = [row for row in proposed_checks if row.get("rule_id") in fix.get("rule_ids", []) and row.get("status") == "FAIL"]
    if unresolved:
        raise HTTPException(status_code=409, detail="The fix did not resolve the compatibility failure")

    for role, part in proposed.items():
        if role not in parts or parts[role].get("id") != part.get("id"):
            session.opt_swaps[role] = part
    pid = fix["part"].get("id")
    if pid and pid not in session.live and not fix["part"].get("owned"):
        try:
            source = copy.deepcopy(fix["part"])
            source.setdefault("query", source.get("name", ""))
            session.live[pid] = pipeline.source_part(source)
        except Exception:
            pass
    return {"status": "ok", "swaps": {role: part.get("id") for role, part in session.opt_swaps.items()}}


@router.post("/reset")
def reset_swaps(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    session.opt_swaps.clear()
    return {"status": "ok"}

