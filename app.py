"""Run FORGE's FastAPI backend and serve its HTML, CSS, and JavaScript frontend."""

import os

import uvicorn

from server.main import app


DEFAULT_PORT = int(os.getenv("PORT", os.getenv("FORGE_PORT", "8001")))
DEFAULT_HOST = os.getenv("HOST", "0.0.0.0")


if __name__ == "__main__":
    uvicorn.run("server.main:app", host=DEFAULT_HOST, port=DEFAULT_PORT, reload=False)
