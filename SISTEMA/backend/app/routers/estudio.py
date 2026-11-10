"""ESTUDIO DE CONTEUDO - a pauta de publicacoes do escritorio.

Objetivo: digitar um tema e receber o carrossel pronto, com calendario do que vai sair.

COMO FUNCIONA (e por que assim):
A tela NAO gera nada. Ela so enfileira: a peca nasce com estado 'fila' e quem
escreve e o SYNC/estudio_conteudo.py, um cron de 1 em 1 minuto. Dois motivos:

  1. quem escreve e o Claude Code da ASSINATURA, nao a API (que vive sem saldo).
     O token OAuth mora no crontab, nao no systemd do backend, e nao vou colocar
     credencial nova no servico so pra isso;
  2. gerar carrossel leva ~1 min. Endpoint que segura a conexao um minuto derruba
     a tela do usuario e prende um worker (o backend roda com 1 worker so).

A tela pergunta de tempos em tempos ate o estado virar 'pronto'.

PUBLICACAO: nao publica sozinho ainda. Falta a Meta liberar instagram_basic e
instagram_content_publish no token (ver o levantamento de 23/09). Enquanto isso,
'agendado' e 'publicado' sao marcados a mao por quem posta, e o calendario serve
de pauta. Quando o token vier, so o passo final muda.
"""
import json
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..database import Usuario
from ..deps import usuario_atual

router = APIRouter(prefix='/api/estudio', tags=['estudio'])

FORMATOS = ('carrossel', 'reels', 'story', 'legenda')
ESTADOS = ('fila', 'gerando', 'pronto', 'agendado', 'publicado', 'erro', 'arquivado')


def _db():
    caminho = str(settings.DATABASE_URL).replace('sqlite+aiosqlite:///', '').replace('sqlite:///', '')
    con = sqlite3.connect(caminho, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('pragma busy_timeout=8000')
    return con


def _garantir_tabela(con):
    con.executescript("""
    create table if not exists mkt_pauta (
      id integer primary key autoincrement,
      tema text not null,
      formato text not null default 'carrossel',
      publico text default '',
      observacao text default '',
      estado text not null default 'fila',
      titulo text default '',
      slides text default '',          -- json: [{"titulo","texto"}]
      legenda text default '',
      hashtags text default '',
      erro text default '',
      data_prevista text default '',   -- AAAA-MM-DD
      criado_por text default '',
      criado_em text default '',
      gerado_em text default '',
      publicado_em text default ''
    );
    create index if not exists ix_mkt_pauta_estado on mkt_pauta(estado);
    create index if not exists ix_mkt_pauta_data on mkt_pauta(data_prevista);
    """)
    con.commit()


def _linha(r):
    d = dict(r)
    try:
        d['slides'] = json.loads(d.get('slides') or '[]')
    except Exception:
        d['slides'] = []
    # as imagens 1080x1350 que o SYNC/render_carrossel.py desenhou
    try:
        d['imagens'] = json.loads(d.get('imagens') or 'null')
    except Exception:
        d['imagens'] = None
    return d


class NovaPauta(BaseModel):
    tema: str
    formato: str = 'carrossel'
    publico: str = ''
    observacao: str = ''
    data_prevista: str = ''


class AjustePauta(BaseModel):
    estado: Optional[str] = None
    data_prevista: Optional[str] = None
    titulo: Optional[str] = None
    legenda: Optional[str] = None


@router.get('/pauta')
async def listar(estado: Optional[str] = None, limite: int = Query(80, le=300),
                 user: Usuario = Depends(usuario_atual)):
    """A pauta inteira, da mais nova para a mais velha."""
    con = _db()
    _garantir_tabela(con)
    sql = 'select * from mkt_pauta'
    par = []
    if estado:
        sql += ' where estado = ?'
        par.append(estado)
    else:
        sql += " where estado <> 'arquivado'"
    sql += ' order by id desc limit ?'
    par.append(limite)
    itens = [_linha(r) for r in con.execute(sql, par)]
    contagem = {r[0]: r[1] for r in con.execute(
        'select estado, count(*) from mkt_pauta group by 1')}
    con.close()
    return {'itens': itens, 'contagem': contagem}


@router.post('/pauta')
async def criar(req: NovaPauta, user: Usuario = Depends(usuario_atual)):
    """Enfileira uma peca. Quem escreve e o cron; aqui so entra na fila."""
    tema = (req.tema or '').strip()
    if len(tema) < 4:
        raise HTTPException(400, 'Diga o tema da publicacao (pelo menos 4 letras)')
    formato = (req.formato or 'carrossel').strip().lower()
    if formato not in FORMATOS:
        raise HTTPException(400, 'Formato deve ser um destes: ' + ', '.join(FORMATOS))
    con = _db()
    _garantir_tabela(con)
    cur = con.execute(
        'insert into mkt_pauta (tema, formato, publico, observacao, estado, data_prevista, '
        'criado_por, criado_em) values (?,?,?,?,?,?,?,?)',
        (tema[:300], formato, (req.publico or '')[:200], (req.observacao or '')[:1000],
         'fila', (req.data_prevista or '')[:10],
         getattr(user, 'nome', '') or user.email, datetime.now().isoformat(sep=' ', timespec='seconds')))
    con.commit()
    novo = _linha(con.execute('select * from mkt_pauta where id=?', (cur.lastrowid,)).fetchone())
    con.close()
    return {'ok': True, 'item': novo,
            'aviso': 'Entrou na fila. O texto fica pronto em ate 2 minutos.'}


@router.patch('/pauta/{pauta_id}')
async def ajustar(pauta_id: int, req: AjustePauta, user: Usuario = Depends(usuario_atual)):
    """Move de estado, marca data ou corrige titulo/legenda a mao."""
    con = _db()
    _garantir_tabela(con)
    atual = con.execute('select * from mkt_pauta where id=?', (pauta_id,)).fetchone()
    if not atual:
        con.close()
        raise HTTPException(404, 'Peca nao encontrada')
    campos, valores = [], []
    if req.estado is not None:
        if req.estado not in ESTADOS:
            con.close()
            raise HTTPException(400, 'Estado invalido')
        campos.append('estado = ?')
        valores.append(req.estado)
        if req.estado == 'publicado':
            campos.append('publicado_em = ?')
            valores.append(datetime.now().isoformat(sep=' ', timespec='seconds'))
    for nome, valor in (('data_prevista', req.data_prevista), ('titulo', req.titulo),
                        ('legenda', req.legenda)):
        if valor is not None:
            campos.append('%s = ?' % nome)
            valores.append(valor)
    if not campos:
        con.close()
        raise HTTPException(400, 'Nada para mudar')
    valores.append(pauta_id)
    con.execute('update mkt_pauta set %s where id=?' % ', '.join(campos), valores)
    con.commit()
    item = _linha(con.execute('select * from mkt_pauta where id=?', (pauta_id,)).fetchone())
    con.close()
    return {'ok': True, 'item': item}


@router.post('/pauta/{pauta_id}/refazer')
async def refazer(pauta_id: int, user: Usuario = Depends(usuario_atual)):
    """Devolve para a fila: o cron escreve de novo."""
    con = _db()
    _garantir_tabela(con)
    con.execute("update mkt_pauta set estado='fila', erro='' where id=?", (pauta_id,))
    con.commit()
    con.close()
    return {'ok': True}
