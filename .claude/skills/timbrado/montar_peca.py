#!/usr/bin/env python3
"""Coloca um texto (um paragrafo por linha) no timbrado do escritorio. Uso:
    python montar_peca.py --texto PECA.txt --saida PECA.docx
"""
import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ / 'SISTEMA' / 'backend'))
from app.juridico.peca_engine import formatar_no_timbrado  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--texto', required=True)
ap.add_argument('--saida', required=True)
a = ap.parse_args()
print(formatar_no_timbrado(Path(a.texto).read_text(encoding='utf-8'), a.saida))
