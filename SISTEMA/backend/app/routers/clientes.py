"""Busca/autocomplete de clientes no cadastro proprio do sistema (jur_clientes)."""
import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Usuario, get_db
from ..deps import usuario_atual
from ..juridico.models import Cliente

router = APIRouter(prefix='/api/clientes', tags=['clientes'])


@router.get('/buscar')
async def buscar(
    q: str = Query(..., min_length=2),
    cpf: str = Query(None),
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Cliente)
    if cpf:
        dig = re.sub(r'\D', '', cpf)
        stmt = stmt.where(or_(Cliente.documento == cpf, Cliente.documento.like('%' + dig[-6:] + '%')))
    else:
        stmt = stmt.where(Cliente.nome.ilike('%' + q.strip() + '%'))
    rows = (await db.execute(stmt.order_by(Cliente.nome).limit(15))).scalars().all()
    items = [{'id': c.id, 'nome': c.nome, 'cpf': c.documento, 'telefone': c.celular or c.telefone,
              'email': c.email} for c in rows]
    return {'items': items}
