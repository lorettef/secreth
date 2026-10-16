"""Uvicorn entry point: ``uvicorn app.http:app``."""

from app.api.app import create_app

app = create_app()
