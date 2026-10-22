"""Pivot A: a pick sold out and the budget dropped. Preview -> accept / discard -> undo. Nothing changes until the user accepts."""
import copy
import datetime as dt
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException

from core import replan
from core.data import PROJECTS
from server.routes.bom import _build_response_payload
from server.schemas import ReplanRequest
from server.state import store

router = APIRouter(prefix="/api/replan", tags=["replan"])


def _needs_bt(session):
    return session.bluetooth or PROJECTS.get(session.project, {}).get("bt", False)


@router.post("/preview")
def preview(req: ReplanRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    if not parts:
        raise HTTPException(status_code=400, detail="Generate a BOM before re-planning")
    if req.cut_pct is None and req.new_budget is None:
        raise HTTPException(status_code=400, detail="Give cut_pct or new_budget")
    if not req.sold_out_part_ids and not req.sold_out_urls and req.new_budget is None and req.cut_pct is None:
        raise HTTPException(status_code=400, detail="Nothing to re-plan")
    known = {p.get("id") for p in parts.values() if isinstance(p, dict)}
    unknown = [i for i in list(req.sold_out_part_ids) + list(req.sold_out_urls) if i not in known]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Not in the active build: {', '.join(unknown)}")
    # earlier sold-out reports still apply
    sold = set(session.sold_out) | set(req.sold_out_part_ids)
    urls = {k: list(v) for k, v in session.sold_out_urls.items()}
    for k, v in req.sold_out_urls.items():
        urls[k] = sorted(set(urls.get(k, [])) | set(v))
    proposal = replan.plan(parts, session.live, budget_old=session.budget, budget_new=req.new_budget, cut_pct=req.cut_pct,
                           sold_out=sold, sold_out_urls=urls, needs_bt=_needs_bt(session))
    session.pending_replan = proposal           # kept server-side; the client only gets the public view
    return replan.public(proposal)


@router.post("/accept")
def accept(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    p = session.pending_replan
    if not p:
        raise HTTPException(status_code=409, detail="No pending re-plan. Preview one first.")
    snapshot = dict(budget=session.budget, opt_swaps=copy.deepcopy(session.opt_swaps), live=copy.deepcopy(session.live),
                    sold_out=set(session.sold_out), sold_out_urls=copy.deepcopy(session.sold_out_urls))
    session.budget = p["budget"]["new"]
    session.sold_out |= set(p["sold_out"])
    session.sold_out_urls = {k: list(v) for k, v in p["sold_out_urls"].items()}
    session.live = replan.reapply_sold_out(session.live, session.sold_out, session.sold_out_urls)
    for role, part in p["swaps"].items():
        session.opt_swaps[role] = copy.deepcopy(part)
    session.choice = {pid: u for pid, u in session.choice.items() if u not in {x for us in session.sold_out_urls.values() for x in us}}
    session.replan_log.append(dict(at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), snapshot=snapshot, proposal=replan.public(p)))
    session.pending_replan = None
    return dict(status="ok", bom=_build_response_payload(session), needs_sourcing=[c["to_part"]["id"] for c in p["changes"] if c["kind"] == "part_swap"])


@router.post("/discard")
def discard(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    session.pending_replan = None
    return {"status": "ok"}


@router.post("/undo")
def undo(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    if not session.replan_log:
        raise HTTPException(status_code=409, detail="Nothing to undo")
    s = session.replan_log.pop()["snapshot"]
    session.budget, session.opt_swaps, session.live = s["budget"], s["opt_swaps"], s["live"]
    session.sold_out, session.sold_out_urls = s["sold_out"], s["sold_out_urls"]
    return dict(status="ok", bom=_build_response_payload(session))


@router.get("/log")
def log(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    return [dict(at=e["at"], status=e["proposal"]["status"], budget=e["proposal"]["budget"], changes=e["proposal"]["changes"]) for e in session.replan_log]
