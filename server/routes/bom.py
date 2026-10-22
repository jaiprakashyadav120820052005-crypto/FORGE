import asyncio
import copy
import json
import os
from fastapi import APIRouter, Cookie, HTTPException, Query, Response
from fastapi.responses import Response, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from typing import Optional

from core import replan as replan_core, bill, component_catalog, compatibility_engine, custom_bom, hybrid_bom, ledger, optimizer, part_specs, pipeline, project_store
from core.data import PROJECTS, QTY
from core.recommendation_catalog import STAR_PROJECTS
from server.schemas import BomChoiceRequest, BomGenerateRequest
from server.state import store

router = APIRouter(prefix="/api/bom", tags=["bom"])


def _normalize_project_key(key):
    if key is None:
        return "general:line_follower"
    key = str(key).strip()
    if not key:
        return "general:line_follower"
    if key.startswith(("general:", "competition:")):
        return key
    if key in PROJECTS:
        return f"general:{key}"
    dataset_key = project_store.key_for_id(key)
    if dataset_key:
        return dataset_key
    return key


def _capture_initial_bom_total(session):
    """Save the displayed estimate as this BOM's immutable re-plan baseline."""
    parts = session.get_active_parts()
    session.initial_bom_total = bill.make(
        parts, session.live, session.choice, session.budget, session.get_build_label(), manual=session.manual
    )["tot"]["subtotal"]


@router.get("/catalog")
def get_catalog():
    options = project_store.options()
    labels = {o["key"]: o["label"] for o in options}
    return {"options": options, "labels": labels, "total": len(options)}


@router.post("/generate")
def generate_bom(req: BomGenerateRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    session.budget = req.budget
    session.bluetooth = req.bluetooth
    tinyfish_enabled = bool(os.getenv("TINYFISH_API_KEY"))

    # Free-text new idea flow
    if req.custom_idea:
        session.custom, session.custom_error, session.res = None, None, None
        try:
            counter = session.gemini_counter
            out = hybrid_bom.resolve(req.custom_idea, req.budget, req.bluetooth, counter, skip_local=req.skip_local)
        except ValueError as e:
            session.custom_error = str(e)
            raise HTTPException(status_code=400, detail=str(e))

        if out["origin"] == "rule":
            session.opt_swaps.clear()
            session.custom, session.project, session.bom_origin = None, out["project"], "rule"
            needs_bt = req.bluetooth or PROJECTS[out["project"]]["bt"]
            session.res = pipeline.select(out["project"], req.budget, needs_bt, session.live)
            session.stage = "bom"
            _capture_initial_bom_total(session)
            return _build_response_payload(session)

        if out["plan"] is None:
            # Similar catalog projects suggested
            session.suggest = dict(idea=req.custom_idea, items=out["suggestions"])
            return {
                "status": "suggest",
                "suggestions": out["suggestions"],
                "idea": req.custom_idea,
            }

        plan = out["plan"]
        session.opt_swaps.clear()
        session.res = None
        session.custom_error, session.suggest = None, None
        session.custom = dict(
            idea=req.custom_idea,
            label=plan["label"],
            parts=plan["parts"],
            notes=plan.get("notes", []),
            origin=plan["origin"],
            meta=plan,
        )
        session.bom_origin = plan["origin"]
        session.stage = "bom"
        _capture_initial_bom_total(session)
        return _build_response_payload(session)

    # Catalog project selection flow
    key = _normalize_project_key(req.project or "general:line_follower")
    session.custom_error, session.suggest = None, None

    if key.startswith("general:") and key.split(":", 1)[1] in PROJECTS:
        project_id = key.split(":", 1)[1]
        session.opt_swaps.clear()
        session.custom, session.project, session.bom_origin = None, project_id, "rule"
        needs_bt = req.bluetooth or PROJECTS[project_id]["bt"]
        session.res = pipeline.select(project_id, req.budget, needs_bt, session.live)
        session.stage = "bom"
        _capture_initial_bom_total(session)
        return _build_response_payload(session)

    plan = project_store.get_plan(key)
    if not plan:
        raw_id = key.split(":", 1)[-1] if ":" in key else key
        star = next((p for p in STAR_PROJECTS if p["key"] in (key, raw_id)), None)
        if star:
            star_idea = f"{star['name']}: {star['description']}"
            res = hybrid_bom.resolve(star_idea, req.budget or star.get("cost_inr") or 3500, req.bluetooth, session.gemini_counter)
            plan = res.get("plan")
            if plan:
                session.opt_swaps.clear()
                session.res = None
                session.custom = dict(
                    idea=star_idea,
                    label=plan["label"],
                    parts=plan["parts"],
                    notes=plan.get("notes", []),
                    origin=plan["origin"],
                    meta=plan,
                )
                session.bom_origin = plan["origin"]
                session.project = key
                session.stage = "bom"
                _capture_initial_bom_total(session)
                return _build_response_payload(session)
        raise HTTPException(status_code=404, detail=f"Project '{key}' not found in catalog")

    session.opt_swaps.clear()
    session.res = None
    session.custom = dict(
        idea=None,
        label=plan["label"],
        parts=plan["parts"],
        notes=plan.get("notes", []),
        origin=plan["origin"],
        meta=plan,
    )
    session.bom_origin = plan["origin"]
    session.project = key
    session.stage = "bom"
    _capture_initial_bom_total(session)
    return _build_response_payload(session)


@router.get("")
@router.get("/")
def get_current_bom(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    return _build_response_payload(session)


import datetime as dt

def _build_response_payload(session):
    parts = session.get_active_parts()
    label = session.get_build_label()
    checks = [
        {
            "id": row["rule_id"],
            "status": row["status"],
            "title": row["title"],
            "msg": row["explanation"],
            "action": row["action"],
        }
        for row in compatibility_engine.check(
            optimizer.effective(parts, component_catalog.load()),
            needs_bt=session.bluetooth or PROJECTS.get(session.project, {}).get("bt", False),
        )
    ]
    alternatives = []
    if session.res and session.res.get("best"):
        alternatives = [
            {"cost": r["cost"], "parts": [p["name"] for p in r["parts"].values() if not p.get("none")]}
            for r in session.res.get("alternatives", [])
        ]

    b = bill.make(parts, session.live, session.choice, session.budget, label, manual=session.manual)
    tot = b["tot"]

    parts_offers = {}
    for pid in session.live:
        raw_v = bill.offers_for(pid, session.live)
        verified = []
        for o in raw_v:
            verified.append({
                "url": o.get("url"),
                "price": o.get("price"),
                "name": o.get("name"),
                "vendor": bill.vendor(o.get("url", "")),
                "quote": o.get("quote"),
                "checked_at": o.get("checked_at"),
                "method": o.get("method"),
                "note": o.get("note"),
                "suspect": o.get("suspect"),
            })
        raw_u = bill.unverified_for(pid, session.live)
        unverified = []
        for o in raw_u:
            unverified.append({
                "url": o.get("url"),
                "name": o.get("name"),
                "vendor": bill.vendor(o.get("url", "")),
                "note": o.get("note"),
            })
        parts_offers[pid] = {
            "verified": verified,
            "unverified": unverified,
            "warning": pipeline.spread_warning(session.live.get(pid, [])),
        }

    return {
        "status": "ok",
        "stage": session.stage,
        "label": label,
        "project": session.project,
        "budget": session.budget,
        "bluetooth": session.bluetooth,
        "origin": session.bom_origin or "rule",
        "parts_count": len([p for p in parts.values() if not p.get("none")]),
        "parts": parts,
        "checks": checks,
        "alternatives": alternatives,
        "parts_offers": parts_offers,
        "choice": session.choice,
        "manual": session.manual,
        "bill": {
            "ref": b["ref"],
            "date": str(dt.date.today()),
            "lines": b["lines"],
            "parts_subtotal": tot["subtotal"],
            "live_priced_count": tot["n_live"],
            "total_items": tot["n_items"],
            "grand_total": tot["subtotal"],
            "tot": tot,
            "html": b["html"],
        },
    }


@router.get("/sourcing/stream")
async def stream_sourcing(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    tinyfish_enabled = bool(os.getenv("TINYFISH_API_KEY"))

    async def event_generator():
        yield {"event": "start", "data": json.dumps({"message": "Starting live price sourcing..."})}
        if not tinyfish_enabled:
            yield {
                "event": "log",
                "data": json.dumps({"text": "TinyFish API key not configured. Using catalog estimates."}),
            }
            yield {"event": "complete", "data": json.dumps({"message": "Sourcing completed."})}
            return

        parts = session.get_active_parts()
        queue = asyncio.Queue()

        def log_callback(msg):
            msg_str = str(msg)
            session.research.setdefault("log", []).append(msg_str)
            queue.put_nowait(msg_str)

        # Run sourcing in executor
        loop = asyncio.get_running_loop()

        def do_sourcing():
            try:
                if session.bom_origin == "rule" and session.project in PROJECTS:
                    cand_parts = pipeline.candidate_parts(session.project)
                    for p in cand_parts:
                        if p.get("none"):
                            continue
                        try:
                            session.live[p["id"]] = pipeline.source_part(p, log=log_callback)
                        except Exception as e:
                            log_callback(f"{p['name']}: {e}")
                    session.res = pipeline.select(session.project, session.budget, session.bluetooth or PROJECTS[session.project]["bt"], session.live)
                else:
                    gem = None if session.bom_origin == "gemini" else int(os.getenv("FORGE_DATASET_GEMINI_FALLBACK", "0"))
                    part_list = [p for p in parts.values() if isinstance(p, dict) and not p.get("none")] if isinstance(parts, dict) else parts
                    custom_bom.source_all(part_list, session.live, log=log_callback, gemini_max=gem, skip_done=True)
            except Exception as e:
                import traceback
                traceback.print_exc()
                log_callback(f"Sourcing error: {e}")

        task = loop.run_in_executor(None, do_sourcing)

        while not task.done():
            while not queue.empty():
                line = await queue.get()
                yield {"event": "log", "data": json.dumps({"text": line})}
            await asyncio.sleep(0.15)

        try:
            await task
        except Exception as e:
            log_callback(f"Task error: {e}")

        # Fresh sourcing rebuilds offers with in_stock=None: restore what the user reported sold out (Pivot A)
        session.live = replan_core.reapply_sold_out(session.live, session.sold_out, session.sold_out_urls)

        # Flush any remaining logs
        while not queue.empty():
            line = await queue.get()
            yield {"event": "log", "data": json.dumps({"text": line})}

        yield {"event": "complete", "data": json.dumps({"message": "Live sourcing complete!"})}

    return EventSourceResponse(event_generator())


@router.post("/choice")
def set_choice(req: BomChoiceRequest, forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    if req.action == "reset":
        session.choice.clear()
        session.manual.clear()
    elif req.action == "pick":
        if not req.part_id or not req.url:
            raise HTTPException(status_code=400, detail="part_id and url required for pick")
        session.choice[req.part_id] = req.url
        session.manual.pop(req.part_id, None)
    elif req.action == "manual":
        if not req.part_id or not req.url:
            raise HTTPException(status_code=400, detail="part_id and url required for manual pick")
        session.manual[req.part_id] = {
            "url": req.url,
            "name": req.name or "",
            "price": float(req.price or 0.0),
        }
    return {"status": "ok", "choice": session.choice, "manual": session.manual}


@router.get("/bill")
def get_bill(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    label = session.get_build_label()
    b = bill.make(parts, session.live, session.choice, session.budget, label, manual=session.manual)
    tot = b["tot"]
    return {
        "ref": b["ref"],
        "date": str(dt.date.today()),
        "label": label,
        "budget": session.budget,
        "total": tot["subtotal"],
        "parts_subtotal": tot["subtotal"],
        "live_priced_count": tot["n_live"],
        "total_items": tot["n_items"],
        "lines": b["lines"],
        "tot": tot,
        "html": b["html"],
    }


@router.get("/bill.pdf")
def download_bill_pdf(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    label = session.get_build_label()
    b = bill.make(parts, session.live, session.choice, session.budget, label, manual=session.manual)
    pdf_bytes = b.get("pdf")
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
    filename = f"forge_bill_{session.project}_{b['ref'][3:11]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ledger")
def get_ledger(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    for p in parts.values():
        if not p.get("specs") and not p.get("none"):
            sp = part_specs.find_specs_in_dataset(p.get("name"))
            if sp:
                p["specs"] = sp
    rows = ledger.rows(parts, session.live, session.choice, session.manual)
    summary = ledger.summary(rows)
    return {"rows": rows, "summary": summary}


@router.get("/ledger.csv")
def download_ledger_csv(forge_session: Optional[str] = Cookie(None)):
    session = store.get_or_create(forge_session)
    parts = session.get_active_parts()
    for p in parts.values():
        if not p.get("specs") and not p.get("none"):
            sp = part_specs.find_specs_in_dataset(p.get("name"))
            if sp:
                p["specs"] = sp
    rows = ledger.rows(parts, session.live, session.choice, session.manual)
    csv_text = ledger.to_csv(rows)
    filename = f"forge_evidence_ledger_{session.project or 'build'}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

