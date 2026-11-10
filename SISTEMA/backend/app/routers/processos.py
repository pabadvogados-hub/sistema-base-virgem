"""Processos — agora lidos do NOSSO banco (nucleo juridico proprio).

O banco do sistema e a fonte da verdade. O endpoint /sincronizar so tem efeito quando houver
sistema juridico externo configurado (SISTEMA_JURIDICO); por padrao devolve um aviso.
"""
import sys
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual, requer_admin
from ..database import Usuario, get_db
from ..juridico.models import Cliente, Processo, ProcessoParte

PROJ = Path(__file__).parent.parent.parent.parent.parent  # /opt/escritorio
router = APIRouter(prefix='/api/processos', tags=['processos'])

PAM = '%advogada%'   # so os processos da Advogada (advogada)


def _cliente_de(partes):
    """Nome do nosso cliente no processo (ignora parte contraria)."""
    for pt in partes:
        if 'CONTR' not in (pt.papel or '').upper():
            return pt.nome
    return partes[0].nome if partes else ''


@router.get('')
async def listar(q: str = '', grupo: str = '', ativos: bool = True,
                 limit: int = 50, offset: int = 0,
                 user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    cond = [Processo.responsavel.ilike(PAM)]
    if ativos:
        cond.append(Processo.encerrado == False)  # noqa: E712

    # areas disponiveis (sempre das ativas da Advogada) + contagem
    grupos = (await db.execute(
        select(Processo.area).where(Processo.responsavel.ilike(PAM),
                                    Processo.encerrado == False,  # noqa: E712
                                    Processo.area != '').distinct().order_by(Processo.area))).scalars().all()
    ativos_total = (await db.execute(
        select(func.count()).select_from(Processo).where(
            Processo.responsavel.ilike(PAM), Processo.encerrado == False))).scalar() or 0  # noqa: E712

    if grupo:
        cond.append(Processo.area == grupo)
    if q:
        like = f'%{q}%'
        sub = select(ProcessoParte.processo_id).where(ProcessoParte.nome.ilike(like))
        cond.append(or_(Processo.numero.ilike(like), Processo.tipo.ilike(like),
                        Processo.area.ilike(like), Processo.id.in_(sub)))

    total = (await db.execute(select(func.count()).select_from(Processo).where(*cond))).scalar() or 0
    rows = (await db.execute(select(Processo).where(*cond)
                             .order_by(Processo.data_processo.desc())
                             .limit(limit).offset(offset))).scalars().all()

    ids = [p.id for p in rows]
    partes = {}
    if ids:
        for pt in (await db.execute(select(ProcessoParte).where(ProcessoParte.processo_id.in_(ids)))).scalars().all():
            partes.setdefault(pt.processo_id, []).append(pt)

    itens = []
    for p in rows:
        d = p.to_dict()
        d['grupo'] = p.area                  # compat com a tela
        d['data'] = p.data_processo
        d['cliente'] = _cliente_de(partes.get(p.id, []))
        d['partes'] = [pt.to_dict() for pt in partes.get(p.id, [])]
        itens.append(d)
    return {'total': total, 'ativos_total': ativos_total, 'grupos': grupos, 'itens': itens}


@router.get('/{processo_id}')
async def detalhe(processo_id: int, user: Usuario = Depends(usuario_atual),
                  db: AsyncSession = Depends(get_db)):
    """Ficha completa: processo + partes com o cadastro completo de cada uma."""
    p = (await db.execute(select(Processo).where(Processo.id == processo_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, 'Processo nao encontrado')
    partes = (await db.execute(select(ProcessoParte).where(ProcessoParte.processo_id == p.id))).scalars().all()
    cids = [pt.cliente_id for pt in partes if pt.cliente_id]
    fichas = {}
    if cids:
        for c in (await db.execute(select(Cliente).where(Cliente.id.in_(cids)))).scalars().all():
            fichas[c.id] = c.to_dict()
    d = p.to_dict()
    d['grupo'] = p.area
    d['cliente'] = _cliente_de(partes)
    d['partes'] = [{**pt.to_dict(), 'ficha': fichas.get(pt.cliente_id)} for pt in partes]
    return d


@router.post('/sincronizar')
async def sincronizar(user: Usuario = Depends(requer_admin), db: AsyncSession = Depends(get_db)):
    """Reimporta do sistema juridico externo o que ainda nao temos (idempotente).
    Com SISTEMA_JURIDICO=nenhum (padrao do escritorio) nao ha de onde puxar: devolve aviso."""
    import os
    if (os.getenv('SISTEMA_JURIDICO') or 'nenhum').strip().lower() == 'nenhum':
        return {'ok': True, 'clientes_novos': 0, 'processos_novos': 0,
                'aviso': 'O escritorio nao usa sistema juridico externo: clientes e processos nascem aqui.'}
    def _puxar():
        from dotenv import load_dotenv
        load_dotenv(str(PROJ / 'config' / '.env'))
        p = str(PROJ / 'INTEGRACOES')
        if p not in sys.path:
            sys.path.insert(0, p)
        import advbox_integration as adv
        def todos(ep):
            out, off = [], 0
            while True:
                d = adv._request('GET', ep, params={'limit': 500, 'offset': off})
                regs = (d or {}).get('data', []) or []
                out.extend(regs)
                if len(out) >= (d or {}).get('totalCount', 0) or not regs:
                    break
                off += 500
            return out
        return todos('/customers'), todos('/lawsuits')

    custs, laws = await asyncio.to_thread(_puxar)

    def num(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    def enc(t):
        # NAO usar status_closure: vem com data em processo vivo e marcava 127
        # processos ativos como arquivados (conferido no ADVBOX em 01/09/2026).
        pal = ('ARQUIV', 'ENCERR', 'BAIX', 'EXTINT')
        s = (t.get('stage') or '').upper()
        g = (t.get('step') or '').upper()
        return any(w in s for w in pal) or any(w in g for w in pal)

    ja_c = {c.advbox_id for c in (await db.execute(select(Cliente))).scalars().all()}
    novos_c = 0
    for c in custs:
        if c.get('id') in ja_c:
            continue
        db.add(Cliente(advbox_id=c.get('id'), nome=(c.get('name') or '')[:250],
                       documento=(c.get('identification') or c.get('document') or '')[:40],
                       email=(c.get('email') or '')[:200], telefone=(c.get('phone') or '')[:40],
                       celular=(c.get('cellphone') or '')[:40], origem=(c.get('origin') or '')[:80],
                       nascimento=(c.get('birthdate') or '')[:20], estado_civil=(c.get('civil_status') or '')[:60],
                       profissao=(c.get('occupation') or '')[:120], logradouro=(c.get('street') or '')[:250],
                       cidade=(c.get('city') or '')[:120], estado=(c.get('state') or '')[:40],
                       cep=(c.get('postalcode') or '')[:20], notas=(c.get('notes') or '')))
        novos_c += 1
    await db.commit()

    mapa = {c.advbox_id: c.id for c in (await db.execute(select(Cliente))).scalars().all()}
    ja_p = {p.advbox_id for p in (await db.execute(select(Processo))).scalars().all()}
    novos_p = 0
    for t in laws:
        if t.get('id') in ja_p:
            continue
        p = Processo(advbox_id=t.get('id'), numero=(t.get('process_number') or '')[:60],
                     protocolo=(t.get('protocol_number') or '')[:60], area=(t.get('group') or '')[:120],
                     tipo=(t.get('type') or '')[:250], fase=(t.get('stage') or '')[:120],
                     etapa=(t.get('step') or '')[:120], responsavel=(t.get('responsible') or '')[:150],
                     responsavel_advbox_id=t.get('responsible_id'), encerrado=enc(t),
                     status_encerramento=str(t.get('status_closure') or '')[:120],
                     data_processo=(t.get('process_date') or '')[:20], pasta=str(t.get('folder') or '')[:120],
                     valor_expectativa=num(t.get('fees_expec')), valor_honorarios=num(t.get('fees_money')),
                     contingencia=num(t.get('contingency')), notas=(t.get('notes') or ''))
        db.add(p)
        await db.flush()
        novos_p += 1
        for c in (t.get('customers') or []):
            db.add(ProcessoParte(processo_id=p.id, cliente_id=mapa.get(c.get('customer_id')),
                                 advbox_customer_id=c.get('customer_id'), nome=(c.get('name') or '')[:250],
                                 documento=(c.get('identification') or '')[:40], papel=(c.get('origin') or '')[:80]))
    await db.commit()
    return {'ok': True, 'clientes_novos': novos_c, 'processos_novos': novos_p}
