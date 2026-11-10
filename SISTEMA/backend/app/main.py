"""Entrypoint de desenvolvimento.

Usa exatamente o mesmo app da producao (app.main_vps), para nao existirem dois
sistemas diferentes. Rodar local:  uvicorn app.main:app --port 8000
"""
from .main_vps import app  # noqa: F401
