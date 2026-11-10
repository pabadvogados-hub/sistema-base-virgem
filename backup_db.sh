#!/usr/bin/env bash
# Backup diario do banco do Sistema Escritório Modelo (online backup do SQLite = seguro com o app rodando).
set -euo pipefail

DB=/opt/escritorio/SISTEMA/backend/sistema_esc.db
DIR=/opt/escritorio/backups
mkdir -p "$DIR"

TS=$(date +%Y%m%d_%H%M%S)
OUT="$DIR/sistema_esc_$TS.db"

# backup consistente via API do sqlite (nao copia arquivo pela metade)
/opt/escritorio/venv/bin/python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "$DB" "$OUT"

gzip -f "$OUT"

# mantem os ultimos 14 dias
find "$DIR" -name 'sistema_esc_*.db.gz' -mtime +14 -delete

echo "$(date '+%Y-%m-%d %H:%M:%S') backup ok -> $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"
