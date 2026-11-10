# -*- coding: utf-8 -*-
"""Ponte para INTEGRACOES/escritorio_config.py (contatos do escritorio vindos do .env, sem padrao)."""
import importlib.util
from pathlib import Path

_ARQ = Path(__file__).resolve().parents[4] / 'INTEGRACOES' / 'escritorio_config.py'
_spec = importlib.util.spec_from_file_location('escritorio_config', str(_ARQ))
CFG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CFG)
