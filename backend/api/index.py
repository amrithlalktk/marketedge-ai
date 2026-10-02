"""Vercel serverless entry point: Vercel's Python runtime serves the ASGI `app` below.
All paths are rewritten here by vercel.json; FastAPI does the routing."""
from app.main import app  # noqa: F401
