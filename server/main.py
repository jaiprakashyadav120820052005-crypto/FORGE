import os
import uuid
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv(override=True)

from server.routes import (
    bom,
    competition,
    delivery,
    idea,
    optimizer,
    projects,
    research,
    resume,
    stars,
    survey,
    wiring,
)
from server.state import store

app = FastAPI(
    title="FORGE API — Robot Planning & Compatibility Copilot",
    description="FastAPI interface layer for FORGE hardware architecture, sourcing, and verification.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from starlette.datastructures import MutableHeaders


@app.middleware("http")
async def session_cookie_middleware(request: Request, call_next):
    session_id = request.cookies.get("forge_session")
    new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        new_session = True
        headers = MutableHeaders(scope=request.scope)
        existing_cookie = headers.get("cookie", "")
        headers["cookie"] = f"forge_session={session_id}; {existing_cookie}" if existing_cookie else f"forge_session={session_id}"

    # Ensure session exists in store
    store.get_or_create(session_id)

    response = await call_next(request)
    if new_session:
        response.set_cookie(
            key="forge_session",
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,
        )
    return response


# Include API Routers
app.include_router(survey.router)
app.include_router(resume.router)
app.include_router(idea.router)
app.include_router(competition.router)
app.include_router(bom.router)
app.include_router(delivery.router)
app.include_router(optimizer.router)
app.include_router(wiring.router)
app.include_router(research.router)
app.include_router(stars.router)
app.include_router(projects.router)


@app.get("/api/state")
def get_current_state(request: Request):
    session_id = request.cookies.get("forge_session")
    session = store.get_or_create(session_id)
    return {
        "session_id": session.session_id,
        "stage": session.stage,
        "survey_step": session.survey_step,
        "survey_answers": session.survey_answers,
        "project": session.project,
        "budget": session.budget,
        "bluetooth": session.bluetooth,
        "bom_origin": session.bom_origin,
        "has_custom": bool(session.custom),
        "has_res": bool(session.res),
        "has_resume": bool(session.resume),
        "label": session.get_build_label(),
        "tinyfish_enabled": bool(os.getenv("TINYFISH_API_KEY")),
        "gemini_enabled": bool(os.getenv("GEMINI_API_KEY")),
    }


# Mount Static Front End Files
web_dir = Path(__file__).resolve().parent.parent / "web"
if not web_dir.exists():
    web_dir.mkdir(parents=True, exist_ok=True)

app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="static")
