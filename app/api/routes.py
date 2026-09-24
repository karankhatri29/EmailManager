from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    """Serves the app shell (the page itself handles login state)."""
    return templates.TemplateResponse(request, "index.html")


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy(request: Request):
    """What the app reads, stores and shares. Public: it is linked from the sign-in screen and OAuth consent."""
    return templates.TemplateResponse(request, "privacy.html")


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
