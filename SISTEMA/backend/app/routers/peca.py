"""Modulo Gerar Peca - producao de peca juridica no timbrado do escritorio (IA + DNA_ESCRITORIO.md)."""
import sys
import uuid
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual
from ..database import get_db, Job, Usuario
from ..juridico.jurisprudencia import bloco_verificado

PROJETO_ROOT = Path(__file__).parent.parent.parent.parent.parent
from ..juridico.peca_engine import gerar_texto_peca, formatar_no_timbrado  # noqa: E402

BASE_DIR = Path(__file__).parent.parent.parent  # SISTEMA/backend
UPLOADS_DIR = BASE_DIR / 'uploads'
UPLOADS_DIR.mkdir(exist_ok=True)
PECAS_DIR = BASE_DIR / 'pecas_geradas'
PECAS_DIR.mkdir(exist_ok=True)

router = APIRouter(prefix='/api/peca', tags=['peca'])

TIPOS_PECA = [
    'PETICAO INICIAL', 'CONTESTACAO', 'REPLICA', 'RAZOES FINAIS',
    'RECURSO ORDINARIO', 'RECURSO DE REVISTA', 'CONTRARRAZOES',
    'EMBARGOS DE DECLARACAO', 'AGRAVO DE INSTRUMENTO', 'MANIFESTACAO',
]


class GerarPecaRequest(BaseModel):
    tipo_peca: str
    cliente_nome: str
    cliente_advbox_id: Optional[str] = ''
    drive_link: Optional[str] = ''   # link da pasta do Drive com os documentos do cliente
    dados_caso: Optional[str] = ''
    jurisprudencia: Optional[str] = ''
    esqueleto: Optional[str] = ''
    anexos: Optional[list] = None  # [{nome, path}]


@router.get('/tipos')
async def tipos(user: Usuario = Depends(usuario_atual)):
    return {'tipos': TIPOS_PECA}


async def _processar_job(job_id: str):
    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job:
            return
        try:
            job.status = 'running'
            await db.commit()

            # 1) Le a pasta do Google Drive (documentos do cliente), se informada
            drive_texto = ''
            if job.drive_link:
                job.progresso_msg = 'Lendo documentos da pasta do Drive...'
                await db.commit()
                from ..drive_reader import ler_pasta
                res = await asyncio.to_thread(ler_pasta, job.drive_link)
                if res['ok']:
                    drive_texto = (
                        f'DOCUMENTOS DO CLIENTE (lidos da pasta do Google Drive — '
                        f'{res["qtd"]} arquivo(s): {", ".join(res["nomes"])}):\n\n' + res['texto']
                    )
                else:
                    job.progresso_msg = f'Aviso Drive: {res["erro"]} — seguindo com os dados digitados'
                    await db.commit()

            # 2) Anexos enviados manualmente
            anexos_texto = ''
            for ax in (job.anexos or []):
                p = Path(ax.get('path', ''))
                if p.exists():
                    try:
                        anexos_texto += f'\n\n=== {ax.get("nome")} ===\n' + p.read_text(encoding='utf-8', errors='ignore')[:10000]
                    except Exception:
                        pass

            # 3) Jurisprudencia REAL (Jurisprudencias.ai) a partir do que foi digitado no campo
            # jurisprudencia - nunca deixa o Opus inventar processo/ementa/tribunal
            jurisprudencia_final = job.jurisprudencia or ''
            if jurisprudencia_final.strip():
                job.progresso_msg = 'Buscando jurisprudencia verificada (Jurisprudencias.ai)...'
                await db.commit()
                bloco = await asyncio.to_thread(bloco_verificado, jurisprudencia_final)
                if bloco:
                    jurisprudencia_final = bloco + '\n\nObservacoes adicionais informadas:\n' + job.jurisprudencia

            job.progresso_msg = 'Chamando Opus...'
            await db.commit()
            partes = [x for x in (drive_texto, job.dados_caso, anexos_texto) if x and x.strip()]
            dados_completo = '\n\n'.join(partes)

            texto = await asyncio.to_thread(
                gerar_texto_peca,
                dados_caso=dados_completo,
                tipo_peca=job.tipo_peca,
                esqueleto=job.esqueleto or '',
                jurisprudencia=jurisprudencia_final,
                incluir_amostras=True,
            )
            job.peca_texto = texto
            job.progresso_msg = 'Formatando no timbrado Escritório Modelo...'
            await db.commit()

            nome_arq = f'{job.cliente_nome} - {job.tipo_peca.title()}.docx'
            out_path = PECAS_DIR / f'{job.id}_{nome_arq}'
            await asyncio.to_thread(formatar_no_timbrado, texto, out_path)
            job.docx_path = str(out_path)
            await db.commit()

            # Salva na pasta do cliente no Drive (subpasta PECAS SISTEMA OPERACIONAL)
            if job.drive_link:
                job.progresso_msg = 'Salvando na pasta do cliente no Drive...'
                await db.commit()
                from ..drive_writer import salvar_peca_na_pasta
                up = await asyncio.to_thread(salvar_peca_na_pasta, job.drive_link, str(out_path), nome_arq)
                if up['ok']:
                    job.drive_url = up['url']
                else:
                    job.progresso_msg = f'Aviso: peca gerada, mas nao subiu ao Drive ({up["erro"]})'
                    await db.commit()

            job.progresso_msg = 'Concluida'
            job.status = 'done'
            job.finalizado_em = datetime.utcnow()
            await db.commit()
        except Exception as e:
            job.status = 'error'
            job.erro = f'{type(e).__name__}: {e}'
            job.finalizado_em = datetime.utcnow()
            await db.commit()


@router.post('/gerar')
async def gerar(
    payload: GerarPecaRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(usuario_atual),
):
    if payload.tipo_peca not in TIPOS_PECA:
        raise HTTPException(400, f'Tipo de peca invalido: {payload.tipo_peca}')
    job = Job(
        id=str(uuid.uuid4()),
        usuario_email=user.email,
        tipo_peca=payload.tipo_peca,
        cliente_nome=payload.cliente_nome,
        cliente_advbox_id=payload.cliente_advbox_id or '',
        drive_link=payload.drive_link or '',
        dados_caso=payload.dados_caso or '',
        jurisprudencia=payload.jurisprudencia or '',
        esqueleto=payload.esqueleto or '',
        anexos=payload.anexos or [],
        status='pending',
        progresso_msg='Enfileirada',
    )
    db.add(job)
    await db.commit()
    background.add_task(_processar_job, job.id)
    return {'job_id': job.id, 'status': job.status}


@router.get('/jobs/{job_id}')
async def status_job(job_id: str, db: AsyncSession = Depends(get_db), user: Usuario = Depends(usuario_atual)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, 'Job nao encontrado')
    if job.usuario_email != user.email and user.papel != 'admin':
        raise HTTPException(403, 'Acesso negado')
    return {
        'id': job.id,
        'tipo_peca': job.tipo_peca,
        'cliente_nome': job.cliente_nome,
        'status': job.status,
        'progresso_msg': job.progresso_msg,
        'erro': job.erro,
        'criado_em': job.criado_em.isoformat() if job.criado_em else None,
        'finalizado_em': job.finalizado_em.isoformat() if job.finalizado_em else None,
        'tem_docx': bool(job.docx_path),
        'drive_url': job.drive_url or '',
    }


@router.get('/jobs/{job_id}/download')
async def download_job(job_id: str, db: AsyncSession = Depends(get_db), user: Usuario = Depends(usuario_atual)):
    job = await db.get(Job, job_id)
    if not job or not job.docx_path:
        raise HTTPException(404, 'Arquivo nao disponivel')
    if job.usuario_email != user.email and user.papel != 'admin':
        raise HTTPException(403, 'Acesso negado')
    p = Path(job.docx_path)
    if not p.exists():
        raise HTTPException(404, 'Arquivo removido do servidor')
    return FileResponse(
        p,
        filename=f'{job.cliente_nome} - {job.tipo_peca.title()}.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@router.get('/historico')
async def historico(limit: int = 30, db: AsyncSession = Depends(get_db), user: Usuario = Depends(usuario_atual)):
    if user.papel == 'admin':
        q = select(Job).order_by(Job.criado_em.desc()).limit(limit)
    else:
        q = select(Job).where(Job.usuario_email == user.email).order_by(Job.criado_em.desc()).limit(limit)
    res = await db.execute(q)
    jobs = res.scalars().all()
    return {
        'items': [{
            'id': j.id,
            'tipo_peca': j.tipo_peca,
            'cliente_nome': j.cliente_nome,
            'status': j.status,
            'criado_em': j.criado_em.isoformat() if j.criado_em else None,
            'tem_docx': bool(j.docx_path),
            'drive_url': j.drive_url or '',
            'usuario_email': j.usuario_email,
        } for j in jobs]
    }


@router.post('/upload')
async def upload_anexo(arquivo: UploadFile = File(...), user: Usuario = Depends(usuario_atual)):
    ext = Path(arquivo.filename).suffix
    dest = UPLOADS_DIR / f'{uuid.uuid4()}{ext}'
    content = await arquivo.read()
    dest.write_bytes(content)

    extracted = ''
    if ext.lower() == '.pdf':
        try:
            from ..juridico.intake_escritorio import extrair_texto
            extracted = extrair_texto(str(dest))[:8000]
        except Exception as e:
            extracted = f'(erro ao extrair: {e})'
    elif ext.lower() in ('.txt', '.md'):
        try:
            extracted = dest.read_text(encoding='utf-8', errors='ignore')[:8000]
        except Exception:
            pass

    return {
        'nome': arquivo.filename,
        'path': str(dest),
        'tamanho': len(content),
        'preview': extracted[:500],
        'texto_extraido': extracted,
    }
