#!/usr/bin/env python3
"""Vigia das tarefas "atrasadas que nao estao" (29/09/2026). Cron: 10 7,13 * * 1-5.

So faz sentido com sistema juridico externo espelhado (compara as tarefas de la com as daqui).
Com SISTEMA_JURIDICO=nenhum (padrao do escritorio) sai sem fazer nada. Logica em app/tarefas_conferir.py.
Uso: python3 tarefas_conferir.py [--ensaio]   (ensaio: roda numa copia em memoria e mostra)
"""
import datetime as dt
import os
import sqlite3
import sys

if (os.getenv('SISTEMA_JURIDICO') or 'nenhum').strip().lower() == 'nenhum':
    print('tarefas_conferir: sem sistema juridico externo (SISTEMA_JURIDICO=nenhum); nada a comparar.')
    sys.exit(0)

sys.path.insert(0, "/opt/escritorio/SYNC")
sys.path.insert(0, "/opt/escritorio/SISTEMA/backend")
import tarefas_espelho_advbox as E  # noqa: E402
from app import tarefas_conferir as TC  # noqa: E402

DB = "/opt/escritorio/SISTEMA/backend/sistema_esc.db"
ENSAIO = "--ensaio" in sys.argv


def main():
    desde = (dt.date.today() - dt.timedelta(days=75)).isoformat()
    feitas = list(E._concluidas_desde(desde).values())
    pendentes, completo = E._pendentes()
    print(dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "| ADVBOX: %d concluidas desde %s, %d pendentes%s%s"
          % (len(feitas), desde, len(pendentes), "" if completo else " (lista incompleta)", " | ENSAIO" if ENSAIO else ""))
    if not feitas and not pendentes:
        print("ADVBOX nao respondeu; nada foi marcado.")
        return
    real = sqlite3.connect(DB, timeout=30)
    if ENSAIO:
        c = sqlite3.connect(":memory:")
        real.backup(c)
        real.close()
    else:
        c = real
    c.row_factory = sqlite3.Row
    try:
        novas, atual = TC.varrer(c, feitas, pendentes)
        n = c.execute("SELECT nivel, COUNT(*) FROM tarefa_conferir WHERE status='suspeita' GROUP BY nivel").fetchall()
        print("  novas para conferir: %d | atualizadas: %d | esperando resposta: %s"
              % (novas, atual, ", ".join("%s %d" % (r[0], r[1]) for r in n) or "nenhuma"))
        if ENSAIO:
            for x in c.execute("SELECT k.tarefa_id, k.nivel, t.tipo, t.processo_numero, k.evidencia, "
                               "(SELECT GROUP_CONCAT(nome) FROM tarefa_responsaveis r WHERE r.tarefa_id=t.id "
                               "AND r.concluido_em IS NULL) resp FROM tarefa_conferir k JOIN tarefas t ON t.id=k.tarefa_id "
                               "WHERE k.status='suspeita' ORDER BY k.nivel, k.tarefa_id"):
                print("   #%s %-6s %-28s %-26s %-10s %s" % (x[0], x[1], (x[2] or "")[:28], x[3], (x[5] or "")[:10], x[4][:150]))
    finally:
        c.close()


if __name__ == "__main__":
    main()
