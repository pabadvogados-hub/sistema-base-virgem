# -*- coding: utf-8 -*-
"""
PERSONALIZAR A BASE VIRGEM PARA UM ESCRITORIO NOVO.

A base sai com a marca de mentira "Escritório Modelo Advogados", cidade "Cidade/UF",
equipe "Titular" (admin) e "Advogada" (assina, revisa e e dona das tarefas) e logos neutras
"EM". Este script troca a MARCA e a CIDADE em todos os arquivos de texto do repositorio.

O resto e configuracao, nao codigo:
  - pessoas e papeis ........ SISTEMA/backend/app/juridico/equipe.json
  - chaves, e-mails, linhas . config/.env (copie de config/.env.example)
  - quem e admin na tela .... SISTEMA/frontend/dist/escritorio-config.js
  - estilo das pecas ........ SISTEMA/backend/app/juridico/DNA_ESCRITORIO.md (reescrever com pecas reais)
  - timbrado/contrato/procuracao .docx ... SISTEMA/backend/app/juridico/modelos/ (fora do git)
  - logos ................... SISTEMA/frontend/dist/logo-esc-*.{svg,png}, favicon.svg,
                              SISTEMA/frontend/portal/logo-esc*.png, icone*.png, icone.svg

Uso (rode numa COPIA da base, nunca na base):
    python deploy/personalizar.py --nome "Silva & Souza Advogados" --curto "Silva & Souza" \
        --cidade "Campinas" --uf SP
    python deploy/personalizar.py ... --simular      (so mostra quantos arquivos mudariam)
"""
import argparse
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BIN = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".docx", ".xlsx", ".woff", ".woff2",
       ".ttf", ".otf", ".mp3", ".wav", ".ogg", ".mp4", ".zip", ".db", ".sqlite"}
SEM_ACENTO = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeiooouc" + "AAAAEEIOOOUC")


def pares(a):
    nome, curto = a.nome.strip(), (a.curto or a.nome).strip()
    cidade, uf = a.cidade.strip(), a.uf.strip().upper()
    nome_sa, curto_sa = nome.translate(SEM_ACENTO), curto.translate(SEM_ACENTO)
    return [
        ("Escritório Modelo Advogados", nome), ("ESCRITÓRIO MODELO ADVOGADOS", nome.upper()),
        ("Escritorio Modelo Advogados", nome_sa), ("ESCRITORIO MODELO ADVOGADOS", nome_sa.upper()),
        ("Escritório Modelo", curto), ("ESCRITÓRIO MODELO", curto.upper()),
        ("Escritorio Modelo", curto_sa), ("ESCRITORIO_MODELO", curto_sa.upper().replace(" ", "_")),
        ("Cidade/UF", "%s/%s" % (cidade, uf)),
        ("VARA DO TRABALHO DE CIDADE", "VARA DO TRABALHO DE " + cidade.upper()),
        ("ESCRITORIO_CIDADE=\n", "ESCRITORIO_CIDADE=%s\n" % cidade),
        ("ESCRITORIO_CIDADE=\r\n", "ESCRITORIO_CIDADE=%s\r\n" % cidade),
    ]


def main():
    ap = argparse.ArgumentParser(description="Troca a marca da base virgem pela do escritorio.")
    ap.add_argument("--nome", required=True, help='nome completo, ex.: "Silva & Souza Advogados"')
    ap.add_argument("--curto", help='nome curto, ex.: "Silva & Souza" (padrao: o nome completo)')
    ap.add_argument("--cidade", required=True)
    ap.add_argument("--uf", required=True)
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()

    trocas = pares(a)
    saida = subprocess.run(["git", "-C", str(RAIZ), "ls-files", "-z"], capture_output=True).stdout
    mudou = 0
    for rel in filter(None, saida.decode("utf-8").split("\0")):
        p = RAIZ / rel
        if p.suffix.lower() in BIN or not p.is_file() or p.name == "personalizar.py":
            continue
        try:
            t = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        o = t
        for de, para in trocas:
            t = t.replace(de, para)
        if t != o:
            mudou += 1
            if not a.simular:
                p.write_bytes(t.encode("utf-8"))
    print("%s %d arquivo(s)." % ("Mudariam" if a.simular else "Alterados", mudou))
    if not a.simular:
        print("Proximos passos: equipe.json, config/.env, escritorio-config.js, DNA_ESCRITORIO.md, "
              "modelos .docx e logos (veja o topo deste arquivo).")


if __name__ == "__main__":
    sys.exit(main())
