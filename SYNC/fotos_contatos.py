# -*- coding: utf-8 -*-
"""Busca no WhatsApp (Z-API) a foto de perfil das conversas que estao sem foto.

Por que existe: a foto so chega quando o proprio webhook traz o campo "photo".
Muita conversa antiga (e cadastro manual) ficou so com as iniciais. Aqui a
gente pergunta ao Z-API (GET /profile-picture) e salva a imagem local, no mesmo
lugar e no mesmo formato do _cache_avatar do backend
(MEDIA_DIR/avatars/{id}.jpg -> PUBLIC_BASE_URL/media/avatars/{id}.jpg).

Contato que escondeu a foto responde "not-authorized": isso nao tem conserto,
entao a tentativa fica registrada e so volta a ser feita depois de 7 dias.

    python fotos_contatos.py            # ate 400 conversas
    python fotos_contatos.py --limite 50
    python fotos_contatos.py --simular  # so mostra o que faria
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import requests
sys.path.insert(0, "/opt/escritorio/SYNC")  # saida unica de WhatsApp (D-API)
import wa_dapi  # noqa: E402

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
MEDIA = "/opt/escritorio/media/avatars"
URL_PUBLICA = (os.getenv("PUBLIC_BASE_URL") or "https://sistema.escritorio.adv.br").rstrip("/") + "/media/avatars/%d.jpg"
ESTADO = "/opt/escritorio/SYNC/fotos_contatos_estado.json"
DIAS_ESPERA = 7
PAUSA = 1.2


def carregar_estado():
    try:
        return json.load(open(ESTADO, encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=400)
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()

    db = sqlite3.connect(DB, timeout=30)
    # foto de perfil pela D-API (wa_dapi.foto_perfil)

    estado = carregar_estado()
    agora = datetime.utcnow()
    rows = db.execute(
        "select id, contato_telefone from wa_conversas "
        "where (foto_url is null or foto_url = '') and contato_telefone not like '%@%' "
        "and length(contato_telefone) >= 10 order by atualizado_em desc").fetchall()

    salvas = escondidas = sem_foto = erros = puladas = 0
    feitas = 0
    os.makedirs(MEDIA, exist_ok=True)
    for cid, tel in rows:
        if feitas >= a.limite:
            break
        reg = estado.get(str(cid))
        if reg and datetime.fromisoformat(reg["em"]) > agora - timedelta(days=DIAS_ESPERA):
            puladas += 1
            continue
        feitas += 1
        if a.simular:
            print("buscaria", cid, tel[-4:])
            continue
        try:
            link = wa_dapi.foto_perfil(tel)
            j = {}
            if link and link != "null" and link.startswith("http"):
                img = requests.get(link, timeout=25)
                if img.ok and len(img.content) > 500 and "image" in img.headers.get("content-type", "image"):
                    with open(os.path.join(MEDIA, "%d.jpg" % cid), "wb") as f:
                        f.write(img.content)
                    db.execute("update wa_conversas set foto_url = ? where id = ? and (foto_url is null or foto_url = '')",
                               (URL_PUBLICA % cid, cid))
                    db.commit()
                    salvas += 1
                    estado.pop(str(cid), None)
                else:
                    erros += 1
                    estado[str(cid)] = {"em": agora.isoformat(), "res": "download-falhou"}
            else:
                motivo = (j or {}).get("errorMessage") or "sem-link"
                if motivo == "not-authorized":
                    escondidas += 1
                else:
                    sem_foto += 1
                estado[str(cid)] = {"em": agora.isoformat(), "res": motivo}
        except Exception as e:
            erros += 1
            estado[str(cid)] = {"em": agora.isoformat(), "res": "erro: %s" % str(e)[:80]}
        time.sleep(PAUSA)

    if not a.simular:
        json.dump(estado, open(ESTADO, "w", encoding="utf-8"))
    print("[%s] sem foto na base: %d | consultadas: %d | fotos salvas: %d | foto escondida: %d | sem foto: %d | erros: %d | aguardando 7 dias: %d"
          % (agora.strftime("%Y-%m-%d %H:%M"), len(rows), feitas, salvas, escondidas, sem_foto, erros, puladas))


if __name__ == "__main__":
    sys.exit(main())
