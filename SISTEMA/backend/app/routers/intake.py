"""Modulo Intake - fase de contratacao (roda juridico/intake_escritorio.run_intake).

ATENCAO: e uma operacao REAL - cria a pasta do cliente no Drive, sobe ficha, contrato e
procuracao e grava cliente e processo no sistema. Assinatura (ZapSign) so com ESC_INTAKE_ZAPSIGN=1.
Para ensaiar, use modo_teste (nada sai da maquina).
"""
import sys
import uuid
import asyncio
import io
import contextlib
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual
from ..database import get_db, IntakeJob, Usuario

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent.parent
BASE_DIR = Path(__file__).parent.parent.parent  # SISTEMA/backend
UPLOADS_DIR = BASE_DIR / 'uploads'
UPLOADS_DIR.mkdir(exist_ok=True)

router = APIRouter(prefix='/api/intake', tags=['intake'])

_run_intake = None


def _carregar_run_intake():
    """Intake do escritorio (juridico/intake_escritorio.py): pasta no Drive + ficha, contrato e procuracao."""
    global _run_intake
    if _run_intake is not None:
        return _run_intake
    sp = str(PROJETO_ROOT / 'INTEGRACOES')
    if sp not in sys.path:
        sys.path.insert(0, sp)
    from ..juridico.intake_escritorio import run_intake
    _run_intake = run_intake
    return _run_intake


class IniciarIntakeRequest(BaseModel):
    transcricao_path: str
    transcricao_nome: Optional[str] = ''
    docs_extras: Optional[list] = None  # lista de paths
    modo_teste: bool = False            # True = so gera os documentos, sem Drive, banco ou ZapSign


@router.post('/upload')
async def upload(arquivo: UploadFile = File(...), user: Usuario = Depends(usuario_atual)):
    ext = Path(arquivo.filename).suffix
    dest = UPLOADS_DIR / f'{uuid.uuid4()}{ext}'
    dest.write_bytes(await arquivo.read())
    return {'nome': arquivo.filename, 'path': str(dest)}


async def _processar_intake(job_id: str, transcricao_path: str, docs_extras: list, modo_teste: bool = False):
    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        job = await db.get(IntakeJob, job_id)
        if not job:
            return
        job.status = 'running'
        job.progresso_msg = ('MODO TESTE — gerando so os documentos...' if modo_teste
                             else 'Rodando intake (ficha, pasta no Drive, contrato e procuracao)...')
        await db.commit()

        def _run():
            buf = io.StringIO()
            run_intake = _carregar_run_intake()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    run_intake(transcricao_path, docs_extras or None, modo_teste=modo_teste)
                return True, buf.getvalue()
            except BaseException as e:
                return False, buf.getvalue() + f'\n\nERRO: {type(e).__name__}: {e}'

        ok, log = await asyncio.to_thread(_run)
        job.log = log[-40000:]
        # tenta achar o nome do cliente no log
        for linha in log.splitlines():
            if 'Cliente Identificado' in linha:
                job.cliente_nome = linha.split(':', 1)[-1].strip()[:200]
                break
        if ok:
            job.status = 'done'
            job.progresso_msg = 'Intake concluido'
        else:
            job.status = 'error'
            job.progresso_msg = 'Falhou'
            job.erro = log.splitlines()[-1][:500] if log else 'erro'
        job.finalizado_em = datetime.utcnow()
        await db.commit()


@router.post('/iniciar')
async def iniciar(
    payload: IniciarIntakeRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(usuario_atual),
):
    if not Path(payload.transcricao_path).exists():
        raise HTTPException(400, 'Transcricao nao encontrada (faca o upload primeiro)')
    job = IntakeJob(
        id=str(uuid.uuid4()),
        usuario_email=user.email,
        transcricao_nome=payload.transcricao_nome or Path(payload.transcricao_path).name,
        status='pending',
        progresso_msg='Enfileirado',
    )
    db.add(job)
    await db.commit()
    background.add_task(_processar_intake, job.id, payload.transcricao_path, payload.docs_extras or [], payload.modo_teste)
    return {'job_id': job.id, 'status': job.status}


@router.get('/jobs/{job_id}')
async def status_job(job_id: str, db: AsyncSession = Depends(get_db), user: Usuario = Depends(usuario_atual)):
    job = await db.get(IntakeJob, job_id)
    if not job:
        raise HTTPException(404, 'Job nao encontrado')
    if job.usuario_email != user.email and user.papel != 'admin':
        raise HTTPException(403, 'Acesso negado')
    return {
        'id': job.id,
        'cliente_nome': job.cliente_nome,
        'transcricao_nome': job.transcricao_nome,
        'status': job.status,
        'progresso_msg': job.progresso_msg,
        'log': job.log,
        'erro': job.erro,
        'criado_em': job.criado_em.isoformat() if job.criado_em else None,
    }


@router.get('/historico')
async def historico(limit: int = 20, db: AsyncSession = Depends(get_db), user: Usuario = Depends(usuario_atual)):
    if user.papel == 'admin':
        q = select(IntakeJob).order_by(IntakeJob.criado_em.desc()).limit(limit)
    else:
        q = select(IntakeJob).where(IntakeJob.usuario_email == user.email).order_by(IntakeJob.criado_em.desc()).limit(limit)
    res = await db.execute(q)
    return {'items': [{
        'id': j.id, 'cliente_nome': j.cliente_nome, 'transcricao_nome': j.transcricao_nome,
        'status': j.status, 'criado_em': j.criado_em.isoformat() if j.criado_em else None,
        'usuario_email': j.usuario_email,
    } for j in res.scalars().all()]}
