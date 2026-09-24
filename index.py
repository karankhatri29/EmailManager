"""Entrypoint for Vercel: it looks for a FastAPI `app` in a root-level index.py."""

from app.main import app  # noqa: F401
