"""Modulo Atendimento - WhatsApp (Z-API/D-API) + IA (SDR de reclamantes e recepcao de empresas) + CRM/inbox.

- POST /api/atendimento/webhook/{numero_id}  -> recebe Z-API (publico), grava e a IA responde
- GET  /api/atendimento/conversas             -> inbox (equipe)
- GET  /api/atendimento/conversas/{id}/mensagens
- POST /api/atendimento/conversas/{id}/responder  -> resposta humana (assume a conversa)
- POST /api/atendimento/conversas/{id}/etapa      -> move no funil
- POST /api/atendimento/conversas/{id}/atribuir   -> atribui a um atendente
- GET/POST /api/atendimento/numeros               -> gerencia os numeros (multi-numero)
"""
import asyncio
import datetime as _dt
import re
import secrets
import sqlite3 as _sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select, desc, or_, func, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual, requer_admin
from ..database import get_db, AsyncSessionLocal, Usuario
from ..config import settings
from .. import produto_card          # o produto do card
from .. import busca_contato        # achar pelo telefone com ou sem o 9 (07/11)
from .. import trava_conversa      # um card por contato (07/11)
from ..whatsapp.models import (NumeroWhatsapp, Conversa, Mensagem, Pipeline, Stage,
                              Figurinha, Fluxo, LidMap, MoveBoard)
from ..whatsapp import zapi, sdr_reclamante, recepcao_empresas, transcricao, ctwa, classificacao_contato, coluna_lead
from ..whatsapp import funis

# Assinatura do remetente na 1a linha de cada mensagem. As pessoas assinam com o nome do cadastro.
# True = a SDR (que se apresenta pelo nome) assina com o proprio nome; False = assinatura padrao da automacao.
ASSINA_ROBO_PELO_NOME = True

router = APIRouter(prefix='/api/atendimento', tags=['atendimento'])

# Cores padrao pras colunas
CORES = ['#64748b', '#2563eb', '#4f46e5', '#9333ea', '#b8860b', '#1e8e5a', '#0d9488', '#c0392b']

# Quadros padrao: nomes, IDs fixos e colunas ficam em whatsapp/funis.py (fonte unica).
PIPELINES_PADRAO = [(funis.NOME[pid], funis.COLUNAS[pid]) for pid in funis.ORDEM]

# Etapa de destino quando o cliente termina de mandar os documentos
PIPE_PRODUCAO = funis.PIPE_PRODUCAO
ETAPA_PRODUCAO_INICIAL = 'Producao da Inicial'
# Quando a SDR encaminha, o lead vai para o quadro de Reclamantes (a coluna e coluna_lead.py quem escolhe)
PIPE_LEADS = funis.PIPE_LEADS
# Quando o card cai em "Contrato / Assinatura" (contrato fechado), dispara sozinho
# o fluxo que pede os documentos do cliente.
ETAPA_CONTRATO = funis.CONTRATO
FLUXO_CONTRATACAO = 'contratacao'

# Agentes de IA disponiveis por numero (linha)
AGENTES = {'reclamante': sdr_reclamante, 'empresas': recepcao_empresas}
AGENTE_PADRAO = 'reclamante'

# Marcador da nota interna de briefing (evita gerar duas vezes na mesma conversa)
MARK_BRIEFING = '[BRIEFING DE FECHAMENTO]'

# Teto de respostas automaticas da IA numa mesma conversa sem encaminhar (loop/trote/spam)
LIMITE_RESPOSTAS_IA = 12
MARK_LIMITE = '[LIMITE IA]'

# Lead de formulario (campanha com qualificacao previa)
CAMPANHA_FORMULARIO = 'Campanha Formulario'

# Saudacao automatica do WhatsApp Business do celular (nao e gente respondendo). Ajuste no .env.
import os as _os
SAUDACAO_WHATSAPP_BUSINESS = (_os.getenv('SAUDACAO_WHATSAPP_BUSINESS') or 'Bem vindo ao Escritório Modelo Advogados').strip()


def _engine(numero):
    return AGENTES.get((numero.agente or AGENTE_PADRAO), sdr_reclamante)


# ============================================================
# TRAVA DO BOARD
# ============================================================
# O board de uma conversa e decisao humana e vale para o escritorio inteiro.
#
#   R1  Automacao NUNCA traz card de outro board de volta para o quadro de Reclamantes.
#   R2  Card com o board fixado por gente so muda de board por gente. A unica excecao e o
#       AVANCO da esteira do caso: Reclamantes -> Contratacao -> Producao.
#
# Trocar de COLUNA dentro do mesmo board continua livre para a automacao.
# Toda tentativa, aceita ou recusada, entra em wa_board_hist.
ESTEIRA_BOARDS = list(funis.ESTEIRA)


async def _id_pipeline(db, nome):
    return (await db.execute(select(Pipeline.id).where(Pipeline.nome == nome))).scalar_one_or_none()


async def _produto_pelo_quadro(db, conv, para_pid):
    """O lead que nasceu sem sinal ganha o produto quando chega num quadro que o diz.

    Ex.: quem escreveu so "Bom dia" e foi parar no quadro de Empresas ganha o produto empresarial.
    O quadro de Reclamantes e a caixa de entrada e nao decide nada sozinho, e card que JA tem
    produto nao e tocado: se uma pessoa escolheu, a maquina nao desfaz.
    """
    try:
        if (getattr(conv, 'produto', None) or '').strip():
            return
        if not para_pid or para_pid == funis.RECLAMANTES or para_pid not in produto_card.QUADROS:
            return
        novo = produto_card.produto_do_quadro(para_pid, '')
        conv.produto = novo
        await db.execute(sa_text(
            'INSERT INTO wa_produto_hist (conversa_id, de_produto, para_produto, quem, '
            "origem, bloqueado, motivo, criado_em) "
            "VALUES (:c, NULL, :p, 'sistema', 'nascimento', 0, :m, :q)"),
            {'c': conv.id, 'p': novo, 'q': datetime.utcnow().isoformat(sep=' '),
             'm': 'nasceu sem sinal; o quadro %s definiu' % para_pid})
    except Exception as e:
        print('[produto-quadro] conv=%s falhou: %s' % (getattr(conv, 'id', '?'), e), flush=True)


def _registrar_move(db, conv, para_pid, para_etapa, quem, origem, bloqueado=False):
    """Anota a troca (ou a tentativa recusada) na trilha. Nao commita."""
    db.add(MoveBoard(conversa_id=conv.id, de_pipeline=conv.pipeline_id or 0,
                     de_etapa=conv.etapa or '', para_pipeline=para_pid or 0,
                     para_etapa=para_etapa or '', quem=quem, origem=origem,
                     bloqueado=1 if bloqueado else 0,
                     criado_em=datetime.utcnow().isoformat(sep=' ')))


async def _automacao_pode_mover(db, conv, para_pid):
    """Regras R1 e R2 acima. True = a automacao pode levar o card pra la."""
    if not para_pid or para_pid == conv.pipeline_id:
        return True                      # trocar de coluna no mesmo board e livre
    leads_id = await _id_pipeline(db, PIPE_LEADS)
    if leads_id and para_pid == leads_id:
        return False                     # R1
    if not getattr(conv, 'board_fixado', 0):
        return True
    ids = [i for i in [await _id_pipeline(db, n) for n in ESTEIRA_BOARDS] if i]
    if (conv.pipeline_id in ids and para_pid in ids
            and ids.index(para_pid) > ids.index(conv.pipeline_id)):
        return True                      # R2: avanco da esteira passa
    return False


async def _coluna_existe(db, pid, etapa):
    """A coluna existe nesse board? Card mandado pra coluna que nao existe some
    do quadro do CRM (foi o que aconteceu com 'Primeiro Contato', apagada do
    Reclamantes numa reorganizacao das colunas)."""
    cols = (await db.execute(select(Stage.nome).where(Stage.pipeline_id == pid))).scalars().all()
    return (not cols) or (etapa in cols)


async def _mover_automatico(db, conv, para_pid, para_etapa, quem, origem, nota=None):
    """Unico caminho pelo qual a automacao move um card. Devolve True se moveu."""
    if conv.pipeline_id == para_pid and conv.etapa == para_etapa:
        return False                     # ja esta la (idempotente)
    if not await _coluna_existe(db, para_pid, para_etapa):
        _registrar_move(db, conv, para_pid, para_etapa, quem, origem, bloqueado=True)
        print(f"[trava-board] conv={conv.id} recusado {origem}: coluna "
              f"'{para_etapa}' nao existe no board {para_pid}", flush=True)
        return False
    if not await _automacao_pode_mover(db, conv, para_pid):
        _registrar_move(db, conv, para_pid, para_etapa, quem, origem, bloqueado=True)
        print(f"[trava-board] conv={conv.id} recusado {origem}: "
              f"board {conv.pipeline_id} fixado por {conv.board_fixado_por or 'humano'}", flush=True)
        return False
    _registrar_move(db, conv, para_pid, para_etapa, quem, origem)
    conv.pipeline_id = para_pid
    conv.etapa = para_etapa
    conv.atualizado_em = datetime.utcnow()
    try:
        await classificacao_contato.lembrar(db, conv, quem, origem)
    except Exception as e:
        print('[classificacao-contato] falhou ao gravar (auto): %s' % e, flush=True)
    if nota:
        db.add(Mensagem(conversa_id=conv.id, origem='nota', autor='sistema',
                        tipo='texto', conteudo=nota))
    return True


async def _mover_para_producao(db, conv):
    """Move a conversa pra fila 'Producao da Inicial' (pipeline Produção).
    Idempotente: se ja esta la, nao faz nada nem duplica a nota."""
    pipe = (await db.execute(select(Pipeline).where(Pipeline.nome == PIPE_PRODUCAO))).scalar_one_or_none()
    if not pipe:
        return
    await _mover_automatico(
        db, conv, pipe.id, ETAPA_PRODUCAO_INICIAL, 'auto:documentos', 'esteira:producao',
        nota='[PRODUCAO] Cliente enviou documentos — movido para "Producao da Inicial". '
             'Confira os documentos recebidos na conversa e siga para a producao da peca.')


async def _mover_lead_qualificado(db, conv, historico=None):
    """A SDR qualificou -> larga o lead na coluna certa do quadro de Reclamantes. Idempotente.

    Qualificou e viu requisitos: Lead Quente ou Lead Qualificado conforme a temperatura;
    nao entendeu do que se trata: fica na entrada para gente olhar.
    """
    pipe = (await db.execute(select(Pipeline).where(Pipeline.nome == PIPE_LEADS))).scalar_one_or_none()
    if not pipe:
        return
    etapa = await coluna_lead.escolher(db, conv, historico)
    if not etapa:
        return                       # sem caso identificado -> deixa na Caixa de Entrada
    # R1: se o card ja saiu de Reclamantes (virou cliente, foi para Empresas), a SDR NAO o traz de volta.
    await _mover_automatico(db, conv, pipe.id, etapa, 'auto:sdr', 'ia:encaminhou')


async def _iniciar_fluxo(db, conv, tipo, enviar_ja=True):
    """Liga um fluxo de cadencia na conversa (idempotente) e, se enviar_ja, ja dispara
    o 1o passo na hora — pra nao esperar ate 12h pelo cron. Nao faz commit (o caller commita)."""
    if tipo not in FLUXOS:
        return False
    fl = (await db.execute(select(Fluxo).where(
        Fluxo.conversa_id == conv.id, Fluxo.tipo == tipo))).scalar_one_or_none()
    if fl and fl.ativo:
        return False  # ja rodando -> nao duplica
    agora = datetime.utcnow()
    if fl:  # reativa do zero
        fl.ativo = True; fl.iniciado_em = agora; fl.etapas_enviadas = ''
        fl.concluido_em = None; fl.criado_por = 'auto:contrato-fechado'
    else:
        fl = Fluxo(conversa_id=conv.id, tipo=tipo, criado_por='auto:contrato-fechado')
        db.add(fl)
    if enviar_ja:
        passo = next((p for p in FLUXOS[tipo]['passos'] if p['ordem'] == 1), None)
        numero = (await db.execute(select(NumeroWhatsapp).where(
            NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
        if passo and numero:
            texto = passo['texto'].format(nome=_primeiro_nome(conv))
            ok, info = await asyncio.to_thread(zapi.enviar_texto, numero, conv.contato_telefone, texto)
            if ok:
                db.add(Mensagem(conversa_id=conv.id, origem='atendente', autor='fluxo:' + tipo,
                                tipo='texto', conteudo=texto, remote_id=(info or '')))
                conv.ultima_mensagem = texto[:300]; conv.atualizado_em = agora
                fl.etapas_enviadas = '1'
    return True


def _ver_conv(d, user):
    """Esconde campos financeiros de quem nao tem acesso financeiro."""
    if not getattr(user, 'acesso_financeiro', False):
        d = {**d, 'valor': None}
    return d


def _norm_tel(raw):
    """So digitos, com DDI 55 (10/11 digitos -> +55)."""
    d = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if len(d) in (10, 11):
        d = '55' + d
    return d


async def _conversa_por_numero(db, numero_id, telefone_raw):
    """ANTI-DUPLICATA (fonte unica): retorna a conversa existente desse numero, casando
    pelos ULTIMOS 8 digitos (colapsa variacoes com/sem 55 e com/sem o 9). None se nao existir.
    Garante 1 contato por numero — usada em todo ponto que cria/cadastra conversa.
    Se ja houver duplicata (corrida antiga), devolve a mais recente em vez de estourar."""
    d = _norm_tel(telefone_raw)
    if len(d) < 8:
        return None
    ult8 = d[-8:]
    return (await db.execute(select(Conversa).where(
        Conversa.numero_id == numero_id,
        Conversa.contato_telefone.like(f'%{ult8}')).order_by(Conversa.atualizado_em.desc(), Conversa.id).limit(1))).scalars().first()


async def seed_pipelines():
    """Cria os quadros/colunas padrao se ainda nao existirem (idempotente).
    Cada quadro nasce com o ID FIXO de funis.py: os robos e as telas filtram por esses numeros."""
    # esquema do produto do card (coluna + historico + gatilhos): idempotente, roda sempre
    try:
        _c = _sqlite3.connect(_BANCO_PRODUTO, timeout=60)
        try:
            produto_card.criar_esquema(_c)
        finally:
            _c.close()
    except Exception as e:
        print('[seed] esquema do produto falhou: %s' % e, flush=True)
    async with AsyncSessionLocal() as db:
        existe = (await db.execute(select(Pipeline.id).limit(1))).first()
        if existe:
            await db.commit()
            await seed_numeros()
            return
        for pi, pid in enumerate(funis.ORDEM):
            db.add(Pipeline(id=pid, nome=funis.NOME[pid], ordem=pi))
            await db.flush()
            for si, sname in enumerate(funis.COLUNAS[pid]):
                db.add(Stage(pipeline_id=pid, nome=sname, cor=CORES[si % len(CORES)], ordem=si))
        await db.commit()
    await seed_numeros()


async def seed_numeros():
    """Cadastra as duas linhas a partir do .env, se ainda nao existirem (idempotente).
    WA_RECLAMANTES_* = linha fixa do escritorio (SDR de IA); WA_EMPRESAS_* = numero do Dr. Titular.
    Credenciais ficam so no .env/banco; nada de numero ou token no codigo."""
    import os
    linhas = [
        ('RECLAMANTES', 'reclamante', 'Reclamantes', True),
        ('EMPRESAS', 'empresas', 'Empresas', os.getenv('WA_EMPRESAS_IA', '0') == '1'),
    ]
    async with AsyncSessionLocal() as db:
        for chave, agente, nome_padrao, ia in linhas:
            tel = ''.join(ch for ch in (os.getenv(f'WA_{chave}_TELEFONE') or '') if ch.isdigit())
            inst = (os.getenv(f'WA_{chave}_INSTANCIA') or '').strip()
            if not tel and not inst:
                continue
            ja = (await db.execute(select(NumeroWhatsapp.id).where(
                NumeroWhatsapp.agente == agente).limit(1))).first()
            if ja:
                continue
            db.add(NumeroWhatsapp(
                nome=(os.getenv(f'WA_{chave}_NOME') or nome_padrao), telefone=tel,
                zapi_instance=inst, zapi_token=(os.getenv(f'WA_{chave}_TOKEN') or ''),
                zapi_client_token=(os.getenv(f'WA_{chave}_CLIENT_TOKEN') or ''),
                atende_ia=ia, agente=agente, so_reclamante=False, ativo=True))
        await db.commit()


async def _pipeline_padrao(db, numero=None):
    """Quadro e coluna de entrada de um lead novo. A linha de Empresas cai no quadro de Empresas;
    a de Reclamantes (e qualquer outra) cai em Reclamantes."""
    pid = funis.QUADRO_DO_AGENTE.get(getattr(numero, 'agente', None) or AGENTE_PADRAO, funis.RECLAMANTES)
    p = (await db.execute(select(Pipeline).where(Pipeline.id == pid))).scalar_one_or_none()
    if not p:
        p = (await db.execute(select(Pipeline).order_by(Pipeline.ordem).limit(1))).scalar_one_or_none()
    if not p:
        return 0, 'Novo Lead'
    s = (await db.execute(select(Stage).where(Stage.pipeline_id == p.id)
                          .order_by(Stage.ordem).limit(1))).scalar_one_or_none()
    return p.id, (s.nome if s else 'Novo Lead')


# ============================================================
# WEBHOOK (Z-API -> nosso sistema) — PUBLICO
# ============================================================

@router.post('/webhook/{numero_id}/{secret}')
async def webhook_zapi(numero_id: int, secret: str, request: Request, bg: BackgroundTasks,
                       db: AsyncSession = Depends(get_db)):
    """Webhook oficial da Z-API — protegido por segredo no path (WEBHOOK_SECRET)."""
    esperado = getattr(settings, 'WEBHOOK_SECRET', '') or ''
    if not esperado or secret != esperado:
        raise HTTPException(403, 'forbidden')
    return await _processar_webhook(numero_id, request, bg, db)


async def _processar_webhook(numero_id: int, request: Request, bg: BackgroundTasks,
                             db: AsyncSession):
    payload = await request.json()
    dados = zapi.parse_webhook(payload)
    if not dados:
        return {'ok': True, 'ignorado': True}

    # Lead de formulario: a mensagem pre-preenchida ja vem no formato estruturado ("Estimativa:", "CLT:"...).
    veio_do_formulario = (dados['direcao'] == 'in'
                          and sdr_reclamante.eh_lead_formulario(dados.get('conteudo') or ''))

    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == numero_id))).scalar_one_or_none()
    if not numero or not numero.ativo:
        return {'ok': True, 'ignorado': 'numero inativo'}

    # WhatsApp as vezes manda o contato como @lid (anonimo) -> resolve pro telefone real.
    # 1o tenta o mapa lid->numero (preenchido pela reconciliacao numero->lid, que a Z-API
    # responde mesmo quando lid->numero falha). Evita as conversas-fantasma.
    if '@' in dados['telefone']:
        lid_orig = dados['telefone']
        lrow = (await db.execute(select(LidMap).where(LidMap.lid == lid_orig))).scalar_one_or_none()
        if lrow and lrow.telefone:
            dados['telefone'] = lrow.telefone
        else:
            phone_real, nome_real, foto_real = await asyncio.to_thread(zapi.resolver_chat, numero, lid_orig)
            if phone_real:
                dados['telefone'] = phone_real
                if nome_real and not dados['nome']:
                    dados['nome'] = nome_real
                if foto_real and not dados.get('foto'):
                    dados['foto'] = foto_real
                await db.merge(LidMap(lid=lid_orig,
                                      telefone=''.join(ch for ch in phone_real if ch.isdigit()),
                                      atualizado_em=datetime.utcnow()))

    # acha/cria conversa. Se ainda e @lid (nao resolveu), mantem o @lid SEM inventar numero.
    # Se o contato ficou com 2 conversas (2 webhooks juntos na 1a mensagem), pega a mais
    # recente em vez de estourar MultipleResultsFound, que fazia TODA mensagem nova dele se perder.
    # UM CARD POR CONTATO. Dois webhooks do mesmo cliente chegando juntos no primeiro contato
    # faziam dois cards; a trava fecha a causa. Regra: app/trava_conversa.py.
    _nasceu_agora = False
    async with trava_conversa.travar(numero_id, dados['telefone']):
        eh_lid = '@' in dados['telefone']
        dig = ''.join(ch for ch in dados['telefone'] if ch.isdigit())
        if eh_lid:
            tel_store = dados['telefone']
            conv = (await db.execute(select(Conversa).where(
                Conversa.numero_id == numero_id,
                Conversa.contato_telefone == dados['telefone']).order_by(Conversa.atualizado_em.desc(), Conversa.id).limit(1))).scalars().first()
        elif len(dig) >= 8:
            ult8 = dig[-8:]
            tel_store = dig if dig.startswith('55') else ('55' + dig if len(dig) >= 10 else dig)
            conv = (await db.execute(select(Conversa).where(
                Conversa.numero_id == numero_id,
                Conversa.contato_telefone.like(f'%{ult8}')).order_by(Conversa.atualizado_em.desc(), Conversa.id).limit(1))).scalars().first()
        else:
            tel_store = dados['telefone']
            conv = (await db.execute(select(Conversa).where(
                Conversa.numero_id == numero_id,
                Conversa.contato_telefone == dados['telefone']).order_by(Conversa.atualizado_em.desc(), Conversa.id).limit(1))).scalars().first()
        if not conv:
            pid, primeira_col = await _pipeline_padrao(db, numero)
            conv = Conversa(numero_id=numero_id, contato_telefone=tel_store,
                            contato_nome=dados['nome'], pipeline_id=pid, etapa=primeira_col)
            db.add(conv)
            await db.flush()
            _nasceu_agora = True
            # A CLASSIFICACAO MORA NO CONTATO. Se esse telefone ja tinha
            # lugar no CRM, o card nasce la, com o dono de antes, em vez de voltar do zero
            # para a Caixa de Entrada. Coluna de perda nao herda: ver classificacao_contato.
            try:
                reg = await classificacao_contato.recuperar(db, tel_store)
                if reg:
                    herdou = (classificacao_contato.pode_herdar(reg)
                              and await _coluna_existe(db, reg['pipeline_id'], reg['etapa']))
                    if herdou:
                        _registrar_move(db, conv, reg['pipeline_id'], reg['etapa'],
                                        'auto:classificacao-do-contato', 'contato:conversa-nova')
                        conv.pipeline_id = reg['pipeline_id']
                        conv.etapa = reg['etapa']
                        await _produto_pelo_quadro(db, conv, reg['pipeline_id'])
                        conv.board_fixado = reg['board_fixado'] or 0
                        if reg.get('responsavel'):
                            conv.responsavel = reg['responsavel']
                        if reg.get('atendente_email'):
                            conv.atendente_email = reg['atendente_email']
                        if reg.get('tag') and not (conv.tag or '').strip():
                            conv.tag = reg['tag']
                    nome_board = (await db.execute(select(Pipeline.nome).where(
                        Pipeline.id == reg['pipeline_id']))).scalar_one_or_none()
                    db.add(Mensagem(conversa_id=conv.id, origem='nota', autor='sistema', tipo='texto',
                                    conteudo=classificacao_contato.nota_de_volta(reg, nome_board, herdou)))
            except Exception as e:
                print('[classificacao-contato] falhou ao recuperar: %s' % e, flush=True)
        elif not eh_lid and dig.startswith('55') and conv.contato_telefone != dig:
            conv.contato_telefone = dig  # canoniza pro formato com 55
        if dados['nome'] and dados['direcao'] == 'in' and not conv.contato_nome and '@lid' not in dados['nome']:
            conv.contato_nome = dados['nome']
        # fecha a criacao DENTRO da trava: enquanto a transacao nao commita, a proxima
        # requisicao deste mesmo contato nao enxerga o card e a corrida continua aberta.
        if _nasceu_agora:
            await db.commit()

    # foto de perfil do contato (cacheia local pois o link do WhatsApp expira)
    if dados.get('foto') and MIDIA_MARCA not in (conv.foto_url or ''):
        conv.foto_url = dados['foto']
        bg.add_task(_cache_avatar, conv.id, dados['foto'])

    # ===== Mensagem que SAIU (fromMe): enviada por nos ou digitada no celular =====
    if dados['direcao'] == 'out':
        # dedup: se ja temos esse messageId (eco), ignora SEM mexer no horario (nao sobe pro topo)
        if dados['remote_id']:
            existe = (await db.execute(select(Mensagem.id).where(
                Mensagem.remote_id == dados['remote_id']).limit(1))).first()
            if existe:
                return {'ok': True, 'dedup': True}
        conv.ultima_mensagem = (dados['conteudo'] or f"[{dados['tipo']}]")[:300]
        conv.atualizado_em = datetime.utcnow()
        # A saudacao automatica do WhatsApp Business do celular sai sozinha no 1o contato.
        # Nao e gente respondendo: entra como fluxo e NAO desliga a IA.
        saudacao = (dados['conteudo'] or '').lstrip().startswith(SAUDACAO_WHATSAPP_BUSINESS)
        db.add(Mensagem(conversa_id=conv.id, origem='atendente', tipo=dados['tipo'],
                        **({'autor': 'fluxo:saudacao-whatsapp'} if saudacao else {}),
                        conteudo=dados['conteudo'], midia_url=dados['midia_url'],
                        remote_id=dados['remote_id']))
        if saudacao:
            await db.commit()
            return {'ok': True, 'out': True, 'saudacao': True}
        conv.ia_ativa = False   # respondeu pelo celular -> IA sai de cena
        await db.commit()
        return {'ok': True, 'out': True}

    # ===== Mensagem RECEBIDA do contato =====
    conv.nao_lidas = (conv.nao_lidas or 0) + 1
    db.add(Mensagem(conversa_id=conv.id, origem='contato', tipo=dados['tipo'],
                    conteudo=dados['conteudo'], midia_url=dados['midia_url'],
                    remote_id=dados['remote_id']))

    # Lead respondeu -> pausa qualquer fluxo automatico ativo (humano assume, nao cobra mais)
    fluxos_ativos = (await db.execute(select(Fluxo).where(
        Fluxo.conversa_id == conv.id, Fluxo.ativo == True))).scalars().all()
    tinha_fluxo_docs = any(f.tipo in ('documentos', 'contratacao') for f in fluxos_ativos)
    for fl in fluxos_ativos:
        fl.ativo = False
        fl.concluido_em = datetime.utcnow()

    # PRODUCAO: cliente estava no fluxo de DOCUMENTOS e mandou um documento/foto
    # -> move o card pra "Producao da Inicial" pra entrar na esteira de producao.
    if tinha_fluxo_docs and dados['tipo'] in ('imagem', 'doc'):
        await _mover_para_producao(db, conv)

    # ===== Qualificacao automatica da ficha (so mensagem RECEBIDA) =====
    if dados['direcao'] == 'in' and dados['conteudo']:
        low = dados['conteudo'].lower()
        # origem pela frase pre-preenchida do anuncio (click-to-WhatsApp). Frases em whatsapp/funis.py.
        campanha = None
        if veio_do_formulario:
            campanha = CAMPANHA_FORMULARIO  # formato proprio do formulario -> ja chega pre-qualificado
            conv.e_reclamante = True
        else:
            campanha = funis.origem_pela_frase(low)
            if campanha and getattr(numero, 'agente', AGENTE_PADRAO) != 'empresas':
                conv.e_reclamante = True     # anuncio nosso: a SDR atende na hora
        if campanha and not (conv.origem or '').strip():
            conv.origem = campanha
            # lead de campanha que acabou de nascer vai para a coluna da campanha
            col = funis.coluna_da_origem(campanha)
            if col and conv.pipeline_id == funis.RECLAMANTES and conv.etapa == funis.CAIXA:
                await _mover_automatico(db, conv, funis.RECLAMANTES, col, 'auto:campanha', 'campanha:entrada')
        # O PRODUTO DO CARD NASCE AQUI, pela campanha/frase ou pela linha (Empresas -> empresarial).
        # NAO CHUTA: sem sinal, fica vazio e e resolvido na troca de quadro.
        try:
            if not (getattr(conv, 'produto', None) or '').strip():
                _sinal = produto_card.produto_do_lead(conv.origem, low, getattr(numero, 'agente', ''))
                if _sinal:
                    conv.produto = _sinal
                    await db.execute(sa_text(
                        'INSERT INTO wa_produto_hist (conversa_id, de_produto, para_produto, '
                        'quem, origem, bloqueado, motivo, criado_em) '
                        "VALUES (:c, NULL, :p, 'sistema', 'nascimento', 0, :m, :q)"),
                        {'c': conv.id, 'p': _sinal, 'q': datetime.utcnow().isoformat(sep=' '),
                         'm': 'nasceu pela campanha/frase: %s' % (conv.origem or '')[:60]})
        except Exception as e:
            print('[produto-nascimento] conv=%s falhou: %s' % (conv.id, e), flush=True)
        # De qual ANUNCIO e de qual CLIQUE veio este lead. Em melhor esforco:
        # se falhar, a mensagem segue o caminho normal. Ver app/whatsapp/ctwa.py.
        try:
            achado = ctwa.achar(payload)
            if achado or campanha:
                await ctwa.guardar(db, conv.id, dados['telefone'], achado, None if achado else payload)
        except Exception:
            pass
        # deteccao por palavras (outras mensagens) — so na linha de reclamantes
        if (getattr(numero, 'agente', AGENTE_PADRAO) != 'empresas' and not conv.e_reclamante
                and sdr_reclamante.eh_reclamante([{'role': 'user', 'content': low}])):
            conv.e_reclamante = True
        if not (conv.tag or '').strip():
            if getattr(numero, 'agente', '') == 'empresas':
                conv.tag = 'EMPRESA'
            elif conv.e_reclamante:
                conv.tag = 'RECLAMANTE'

    await db.commit()

    # Agenda o processamento em background.
    # AUDIO sempre processa (baixa/salva o arquivo + transcreve), mesmo com IA off.
    # TEXTO so agenda se a IA estiver no comando.
    tem_texto = dados['tipo'] == 'texto' and dados['conteudo']
    tem_audio = dados['tipo'] == 'audio' and dados['midia_url']
    pode_ia = numero.atende_ia and conv.ia_ativa
    if tem_audio or (tem_texto and pode_ia):
        bg.add_task(_responder_ia, conv.id)
    return {'ok': True, 'ia': bool(tem_texto and pode_ia)}


MEDIA_DIR = _os.getenv('MEDIA_DIR') or '/opt/escritorio/media'
# endereco publico das midias (o nginx serve MEDIA_DIR em /media)
PUBLIC_BASE_URL = (_os.getenv('PUBLIC_BASE_URL') or 'https://sistema.escritorio.adv.br').rstrip('/')
MIDIA_URL = PUBLIC_BASE_URL + '/media'
MIDIA_MARCA = MIDIA_URL.split('://', 1)[-1]
TETO_VIDEO = 16 * 1024 * 1024      # o WhatsApp toca video ate uns 16 MB (o nginx aceita 25)


async def _cache_avatar(conversa_id: int, url: str):
    """Baixa a foto de perfil e salva local (o link do WhatsApp expira)."""
    import os
    data = await asyncio.to_thread(transcricao.baixar, url)
    if not data:
        return
    try:
        d = f"{MEDIA_DIR}/avatars"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/{conversa_id}.jpg", 'wb') as f:
            f.write(data)
    except Exception:
        return
    async with AsyncSessionLocal() as db:
        conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
        if conv:
            conv.foto_url = f"{MIDIA_URL}/avatars/{conversa_id}.jpg"
            await db.commit()


def _tempo_de_digitacao(texto: str) -> float:
    """Quanto tempo a SDR segura a resposta antes de mandar.

    Motivo: responder no mesmo segundo entrega que e maquina. A conta imita alguem que le a mensagem
    (~1,5 s) e digita a ~22 caracteres por segundo.

    A calibragem importa: o prompt limita a resposta a 3 linhas, ou seja quase
    sempre entre 120 e 260 caracteres. Com a conta antiga (13 c/s, teto de 14 s)
    praticamente TODA mensagem batia no teto e saia com o mesmo atraso, que e tao
    robotico quanto responder na hora. Assim a faixa real cai entre ~7 e ~13 s,
    com variacao de verdade entre uma mensagem e outra.
    """
    import random
    segundos = 1.5 + len(texto or "") / 22.0
    segundos *= random.uniform(0.85, 1.15)      # sem cadencia de metronomo
    return max(2.5, min(segundos, 13.0))


async def _responder_ia(conversa_id: int, forcar: bool = False):
    """Roda em background: baixa/salva+transcreve os audios (SEMPRE), e — se a IA
    estiver no comando — monta o historico, aplica o filtro e responde via Z-API.
    forcar=True (disparo manual): ignora o filtro de assunto trabalhista."""
    import os, asyncio
    async with AsyncSessionLocal() as db:
        conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
        if not conv:
            return
        numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
        if not numero:
            return
        msgs = (await db.execute(select(Mensagem).where(Mensagem.conversa_id == conversa_id)
                                 .order_by(Mensagem.criado_em.desc()).limit(40))).scalars().all()
        msgs = list(reversed(msgs))  # volta pra ordem cronologica (query pegou as 40 MAIS RECENTES)

        # AUDIO: baixa, salva local (o link do Z-API expira) e transcreve — sempre
        houve_transcricao = False
        for m in msgs:
            if m.origem == 'contato' and m.tipo == 'audio' and m.midia_url and MIDIA_MARCA not in m.midia_url:
                data = await asyncio.to_thread(transcricao.baixar, m.midia_url)
                if data:
                    try:
                        os.makedirs(MEDIA_DIR, exist_ok=True)
                        # nome imprevisivel (token) p/ evitar enumeracao de /media/{id}.ogg
                        fname = f"{m.id}_{secrets.token_hex(6)}.ogg"
                        with open(f"{MEDIA_DIR}/{fname}", 'wb') as f:
                            f.write(data)
                        m.midia_url = f"{MIDIA_URL}/{fname}"
                    except Exception:
                        pass
                    if not (m.conteudo or '').strip():
                        texto = await asyncio.to_thread(transcricao.transcrever_bytes, data)
                        if texto:
                            m.conteudo = texto
                            houve_transcricao = True
        if houve_transcricao:
            ultima = next((m for m in reversed(msgs) if m.origem == 'contato' and (m.conteudo or '').strip()), None)
            if ultima:
                conv.ultima_mensagem = ultima.conteudo[:300]
        await db.commit()

        # daqui pra baixo so roda se a IA estiver no comando
        if not conv.ia_ativa:
            return

        raw = []
        for m in msgs:
            if m.origem == 'nota':          # nota interna nao entra no historico da IA
                continue
            if not (m.conteudo or '').strip():
                continue
            role = 'user' if m.origem == 'contato' else 'assistant'
            raw.append({'role': role, 'content': m.conteudo})
        # funde mensagens consecutivas do mesmo papel (Claude exige alternancia)
        hist = []
        for msg in raw:
            if hist and hist[-1]['role'] == msg['role']:
                hist[-1]['content'] += '\n' + msg['content']
            else:
                hist.append(dict(msg))
        # comeca e termina numa mensagem do contato
        while hist and hist[0]['role'] != 'user':
            hist.pop(0)
        while hist and hist[-1]['role'] != 'user':
            hist.pop()
        if not hist:
            return

        agente = numero.agente or AGENTE_PADRAO
        # detecta assunto trabalhista no historico ja com o texto transcrito
        if agente != 'empresas' and not conv.e_reclamante and sdr_reclamante.eh_reclamante(hist):
            conv.e_reclamante = True
        if conv.e_reclamante and not (conv.tag or '').strip():
            conv.tag = 'RECLAMANTE'   # preenche a ficha
        await db.commit()

        # RESPEITA O HUMANO: se um atendente ja falou nessa conversa, a IA nao entra sozinha.
        # (disparo manual com forcar=True passa por cima disso — voce mandou, ele responde)
        # As mensagens automaticas de FLUXO (autor 'fluxo:...') NAO contam como humano —
        # senao a SDR fica silenciada em todo lead que entrou num fluxo.
        if not forcar and any(m.origem == 'atendente' and not (m.autor or '').startswith('fluxo:') for m in msgs):
            return

        # filtro por agente: se a linha esta em "so reclamante", a SDR so responde quem fala de trabalho
        # (disparo manual com forcar=True ignora esse filtro). Regra da casa: na duvida, atende.
        if not forcar and agente == 'reclamante' and numero.so_reclamante and not conv.e_reclamante:
            return

        # TETO DE SEGURANCA: se a IA ja mandou muita resposta nessa conversa e ainda nao
        # encaminhou (sem briefing gerado), ela para sozinha e pede humano — em vez de ficar
        # girando em loop com trote/spam/caso confuso. Ver LIMITE_RESPOSTAS_IA.
        if not forcar:
            n_ia_enviadas = sum(1 for m in msgs if m.origem == 'ia')
            ja_encaminhado = any(m.origem == 'nota' and (m.conteudo or '').startswith(MARK_BRIEFING) for m in msgs)
            if n_ia_enviadas >= LIMITE_RESPOSTAS_IA and not ja_encaminhado:
                conv.ia_ativa = False
                db.add(Mensagem(conversa_id=conversa_id, origem='nota', autor='sistema', tipo='texto',
                                conteudo=f'{MARK_LIMITE} A IA atingiu {n_ia_enviadas} respostas automaticas '
                                         'sem encaminhar o caso — IA pausada, assumir manualmente.'))
                await db.commit()
                return

        try:
            pre_qualificado = (conv.origem == CAMPANHA_FORMULARIO)
            dados_calc = sdr_reclamante.parse_formulario(hist[0]['content']) if pre_qualificado and hist else None
            motor = _engine(numero)
            resposta = await asyncio.to_thread(motor.gerar_resposta, hist, pre_qualificado, dados_calc)
        except Exception as e:
            print(f"[IA] ERRO gerar_resposta conv={conversa_id}: {type(e).__name__}: {e}", flush=True)
            return
        if not resposta:
            print(f"[IA] resposta vazia conv={conversa_id} (forcar={forcar})", flush=True)
            return
        # RITMO HUMANO: resposta instantanea entrega o robo. Antes de mandar, marca a
        # mensagem como lida, acende o "digitando..." no aparelho do contato e espera
        # o tempo que alguem levaria pra escrever aquilo. Nada disso pode segurar a
        # resposta se falhar, entao vai tudo em melhor esforco.
        espera = _tempo_de_digitacao(resposta)
        try:
            await asyncio.to_thread(zapi.marcar_lido, numero, conv.contato_telefone)
            await asyncio.to_thread(zapi.digitando, numero, conv.contato_telefone, int(espera * 1000))
        except Exception:
            pass
        await asyncio.sleep(espera)

        # robo que se apresenta pelo nome assina com o proprio nome; o resto assina com a assinatura padrao
        nome_robo = zapi.ASSINATURA_AUTOMACAO
        if ASSINA_ROBO_PELO_NOME and motor is sdr_reclamante:
            nome_robo = sdr_reclamante.NOME
        ok, msg_id = await asyncio.to_thread(zapi.enviar_texto, numero, conv.contato_telefone, resposta, nome_robo)
        if not ok:
            print(f"[IA] envio falhou conv={conversa_id}: {msg_id}", flush=True)
        # QUEM FALOU fica guardado na mensagem (a tela mostra o robo da conversa).
        db.add(Mensagem(conversa_id=conversa_id, origem='ia', tipo='texto', conteudo=resposta,
                        autor=('ia:' + agente),
                        remote_id=(msg_id if ok else '')))
        conv.ultima_mensagem = resposta[:300]
        conv.atualizado_em = datetime.utcnow()
        await db.commit()

        # HANDOFF -> FECHAMENTO: se a SDR encaminhou (lead qualificado), gera UMA VEZ o
        # BRIEFING DE FECHAMENTO como nota interna para quem vai fechar.
        try:
            if agente == 'reclamante':
                ja = any(m.origem == 'nota' and (m.conteudo or '').startswith(MARK_BRIEFING) for m in msgs)
                n_contato = sum(1 for m in msgs if m.origem == 'contato')
                # formulario ja chega com vinculo+tempo na 1a mensagem -> pode encaminhar de cara
                n_min = 1 if pre_qualificado else 2
                if not ja and n_contato >= n_min:
                    hist_full = hist + [{'role': 'assistant', 'content': resposta}]
                    if await asyncio.to_thread(sdr_reclamante.foi_encaminhado, hist_full):
                        # qualificou -> larga o lead na coluna certa do quadro de Reclamantes
                        await _mover_lead_qualificado(db, conv, hist_full)
                        nome = (conv.contato_nome or 'o cliente').split()[0]
                        briefing = await asyncio.to_thread(
                            sdr_reclamante.gerar_briefing_fechamento, hist_full, nome, dados_calc)
                        if briefing:
                            db.add(Mensagem(conversa_id=conversa_id, origem='nota', autor='briefing',
                                            tipo='texto', conteudo=f'{MARK_BRIEFING}\n{briefing}'))
                            conv.ultima_mensagem = 'Briefing de fechamento gerado'
                            conv.atualizado_em = datetime.utcnow()
                            await db.commit()
                            print(f"[Briefing] gerado conv={conversa_id}", flush=True)
        except Exception as e:
            print(f"[Briefing] erro conv={conversa_id}: {type(e).__name__}: {e}", flush=True)


# ============================================================
# INBOX (equipe) — PROTEGIDO
# ============================================================

def _pode_trocar_board(user) -> bool:
    """Quem pode arrastar um card de um BOARD para outro.

    Admin, ou quem tem a permissao propria usuarios.pode_trocar_board (ex.: o vendedor
    que organiza o funil sem ser administrador do sistema).

    Isto NAO abre board pessoal de ninguem: quem tem a permissao consegue MANDAR
    um card para o board pessoal de alguem e, dali em diante, nao ve mais nem o
    card nem as mensagens. O filtro de leitura continua sendo
    _pipelines_bloqueados, pelo dono_email do pipeline.
    """
    return (user.papel or '') == 'admin' or bool(getattr(user, 'pode_trocar_board', False))


async def _pipelines_bloqueados(db, user):
    """IDs de pipelines PESSOAIS de outra pessoa — o usuario nao pode ver nem no chat."""
    return (await db.execute(select(Pipeline.id).where(
        Pipeline.dono_email.isnot(None),
        Pipeline.dono_email != '',
        Pipeline.dono_email != user.email))).scalars().all()


@router.get('/conversas')
async def listar_conversas(numero_id: Optional[int] = None, status: str = 'aberto',
                           etapa: Optional[str] = None, pipeline_id: Optional[int] = None,
                           meus: bool = False,
                           user: Usuario = Depends(usuario_atual),
                           db: AsyncSession = Depends(get_db)):
    q = select(Conversa).where(Conversa.status == status)
    # esconde conversas que estao em pipeline pessoal de OUTRO usuario
    bloq = await _pipelines_bloqueados(db, user)
    if bloq:
        q = q.where(~Conversa.pipeline_id.in_(bloq))
    if numero_id:
        q = q.where(Conversa.numero_id == numero_id)
    if pipeline_id is not None:
        q = q.where(Conversa.pipeline_id == pipeline_id)
    if etapa:
        q = q.where(Conversa.etapa == etapa)
    if meus:
        q = q.where(Conversa.atendente_email == user.email)
    q = q.order_by(desc(Conversa.atualizado_em)).limit(2000 if pipeline_id is not None else 200)
    rows = (await db.execute(q)).scalars().all()
    # CAPA DO CARD: contatos feitos + alerta de "respondeu e ninguem voltou".
    # Se der qualquer problema no calculo, a lista sai como sempre saiu — a capa
    # e informacao a mais, nunca motivo para a tela quebrar.
    try:
        extras = await _resumo_contatos_em_lote(db, rows)
    except Exception as e:
        print(f'[capa] resumo de contatos falhou: {type(e).__name__}: {e}', flush=True)
        extras = {}
    return [_ver_conv({**c.to_dict(), **extras.get(c.id, {})}, user) for c in rows]


@router.get('/conversas/{conversa_id}/mensagens')
async def mensagens(conversa_id: int, user: Usuario = Depends(usuario_atual),
                    db: AsyncSession = Depends(get_db)):
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    if conv.pipeline_id in (await _pipelines_bloqueados(db, user)):
        raise HTTPException(403, 'Conversa de um pipeline pessoal de outro usuario')
    conv.nao_lidas = 0
    await db.commit()
    rows = (await db.execute(select(Mensagem).where(Mensagem.conversa_id == conversa_id)
                             .order_by(Mensagem.criado_em))).scalars().all()
    return {'conversa': _ver_conv(conv.to_dict(), user), 'mensagens': [m.to_dict() for m in rows]}


@router.get('/contatos')
async def buscar_contatos(q: str = '', limit: int = 60,
                          user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Busca em TODAS as conversas por nome OU numero — nada fica escondido.
    Resolve o problema de contato que 'some' da lista do chat (que e filtrada)."""
    termo = (q or '').strip()
    query = select(Conversa)
    if termo:
        dig = re.sub(r'\D', '', termo)
        conds = [Conversa.contato_nome.ilike(f'%{termo}%')]
        # TELEFONE COM E SEM O NONO DIGITO.
        # Antes procurava so o numero inteiro que a pessoa digitou, e o WhatsApp grava numero
        # brasileiro antigo SEM o 9: quem digitava o numero com o 9 nao achava o gravado sem o 9 e
        # concluia que o cliente nao existia. O recebimento de mensagem ja era tolerante a
        # isso (linhas 339 e 435, pelos ultimos 8 digitos); so a busca nao era.
        # `variantes` gera as formas daquele numero, entao NAO traz cliente de outro DDD.
        # Regra e testes: app/busca_contato.py e tests/test_busca_contato.py.
        for alvo in busca_contato.padroes_like(dig):
            conds.append(Conversa.contato_telefone.like(alvo))
        query = query.where(or_(*conds))
    lim = max(1, min(limit, 200))
    query = query.order_by(desc(Conversa.atualizado_em)).limit(lim)
    rows = list((await db.execute(query)).scalars().all())
    # NOME DITO NA CONVERSA: o card fica com o nome do perfil do
    # WhatsApp (apelido ou o proprio numero) e o lead se apresenta com outro nome. Depois dos
    # acertos por nome/numero, procura tambem no briefing da SDR e nas mensagens curtas do
    # proprio lead (onde ele diz o nome). So para termo de 4+ letras sem numero; se der erro, a
    # busca sai como sempre saiu.
    if termo and len(termo) >= 4 and not re.search(r'\d', termo) and len(rows) < lim:
        try:
            # briefing: so vale a linha "- Nome:" (o resto cita a Dra., a empresa etc.)
            briefs = (await db.execute(select(Mensagem.conversa_id, Mensagem.conteudo).where(
                Mensagem.origem == 'nota', Mensagem.conteudo.like('%' + MARK_BRIEFING + '%'),
                Mensagem.conteudo.ilike(f'%{termo}%')).limit(500))).all()
            t_min = termo.lower()
            por_briefing = {cid for cid, txt in briefs
                            if any(t_min in re.split(r'\s+-\s+', linha)[0].lower()
                                   for linha in re.findall(r'(?m)^- Nome:([^\n]*)', txt or ''))}
            curtas = select(Mensagem.conversa_id).where(
                Mensagem.origem == 'contato', func.length(Mensagem.conteudo) <= 60,
                Mensagem.conteudo.ilike(f'%{termo}%'))
            q2 = select(Conversa).where(or_(Conversa.id.in_(curtas), Conversa.id.in_(por_briefing)))
            ja = [r.id for r in rows]
            if ja:
                q2 = q2.where(~Conversa.id.in_(ja))
            rows += (await db.execute(q2.order_by(desc(Conversa.atualizado_em)).limit(lim - len(rows)))).scalars().all()
        except Exception as e:
            print(f'[busca] nome dito na conversa falhou: {type(e).__name__}: {e}', flush=True)
    bloq = await _pipelines_bloqueados(db, user)
    itens = [_ver_conv(r.to_dict(), user) for r in rows if r.pipeline_id not in bloq]
    return {'items': itens, 'total': len(itens)}


class ResponderReq(BaseModel):
    texto: str


@router.post('/conversas/{conversa_id}/responder')
async def responder(conversa_id: int, req: ResponderReq,
                    user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
    # Chega ao cliente assinada com o nome de quem mandou; aqui fica o texto como foi digitado
    ok, detalhe = await asyncio.to_thread(zapi.enviar_texto, numero, conv.contato_telefone, req.texto,
                                          zapi.nome_assinatura(user))
    if not ok:
        raise HTTPException(502, f'Falha ao enviar pelo Z-API: {detalhe}')
    db.add(Mensagem(conversa_id=conversa_id, origem='atendente', autor=user.email,
                    tipo='texto', conteudo=req.texto, remote_id=(detalhe or '')))
    conv.ia_ativa = False              # humano assumiu: IA sai de cena
    conv.atendente_email = conv.atendente_email or user.email
    conv.ultima_mensagem = req.texto[:300]
    conv.atualizado_em = datetime.utcnow()
    await db.commit()
    return {'ok': True}


# ===== EDITAR MENSAGEM =====
# So acrescenta: nenhuma rota existente muda. A D-API repassa a edicao ao WhatsApp e responde sucesso ate
# para id inexistente, entao as regras ficam aqui: so texto mandado pela equipe, so quem mandou (ou
# administrador), so ate 15 min depois (o WhatsApp ignora edicao mais velha), so numero da D-API.
EDITAR_JANELA_MIN = 15


class EditarMsgReq(BaseModel):
    texto: str


def _criado_utc(v):
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace('T', ' ').replace('Z', '')[:26])


async def _historico_edicao(db, m, conv_id, antes, depois, por):
    from sqlalchemy import text as _sql
    await db.execute(_sql("CREATE TABLE IF NOT EXISTS wa_mensagens_edicoes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "mensagem_id INTEGER, conversa_id INTEGER, texto_antes TEXT, texto_depois TEXT, por TEXT, em TEXT)"))
    await db.execute(_sql("INSERT INTO wa_mensagens_edicoes (mensagem_id, conversa_id, texto_antes, texto_depois, por, em) "
                          "VALUES (:m, :c, :a, :d, :p, :e)"),
                     {'m': m, 'c': conv_id, 'a': antes, 'd': depois, 'p': por, 'e': datetime.utcnow().isoformat()})


async def _absorver_eco_edicao(conversa_id: int, mensagem_id: int, texto: str, desde: datetime):
    """O aviso da edicao pode voltar pelo webhook com OUTRO id e virar um balao novo (o webhook so descarta id
    repetido). Por 2 min, apaga esse eco: mesma conversa, depois da mensagem editada, atendente sem autor
    (o webhook grava assim) e exatamente o texto novo. Nao mexe no webhook."""
    from datetime import timedelta
    for _ in range(12):
        await asyncio.sleep(10)
        try:
            async with AsyncSessionLocal() as db:
                ecos = (await db.execute(select(Mensagem).where(
                    Mensagem.conversa_id == conversa_id, Mensagem.id > mensagem_id, Mensagem.origem == 'atendente',
                    or_(Mensagem.autor.is_(None), Mensagem.autor == ''), Mensagem.conteudo == texto,
                    Mensagem.criado_em >= desde - timedelta(seconds=5)))).scalars().all()
                if ecos:
                    for e in ecos:
                        await db.delete(e)
                    await db.commit()
                    print(f'[editar] eco da edicao absorvido na conversa {conversa_id}: {len(ecos)}', flush=True)
                    return
        except Exception as e:
            print(f'[editar] absorver eco falhou: {type(e).__name__}: {e}', flush=True)
            return


@router.post('/mensagens/{mensagem_id}/editar')
async def editar_mensagem(mensagem_id: int, req: EditarMsgReq, bg: BackgroundTasks,
                          user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Edita no WhatsApp do cliente uma mensagem de texto que a equipe mandou."""
    texto = (req.texto or '').strip()
    if not texto:
        raise HTTPException(400, 'Escreva o texto novo')
    m = (await db.execute(select(Mensagem).where(Mensagem.id == mensagem_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, 'Mensagem nao encontrada')
    if m.origem != 'atendente' or m.tipo != 'texto' or not m.remote_id:
        raise HTTPException(400, 'So da para editar mensagem de texto enviada pela equipe')
    if (m.autor or '').lower() != (user.email or '').lower() and user.papel != 'admin':
        raise HTTPException(403, 'So quem mandou a mensagem pode editar')
    if (datetime.utcnow() - _criado_utc(m.criado_em)).total_seconds() > EDITAR_JANELA_MIN * 60:
        raise HTTPException(400, 'O WhatsApp so deixa editar ate 15 minutos depois do envio')
    if texto == (m.conteudo or '').strip():
        return {'ok': True, 'igual': True}
    conv = (await db.execute(select(Conversa).where(Conversa.id == m.conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    if conv.pipeline_id in (await _pipelines_bloqueados(db, user)):
        raise HTTPException(403, 'Conversa de um pipeline pessoal de outro usuario')
    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
    from ..whatsapp import dapi
    if not numero or not dapi.eh_dapi(numero):
        raise HTTPException(400, 'Editar mensagem so funciona no WhatsApp conectado pela D-API')
    # O texto editado volta ao cliente com a assinatura de quem mandou a mensagem
    autor_u = (await db.execute(select(Usuario).where(Usuario.email == (m.autor or '')))).scalar_one_or_none()
    texto_envio = zapi.assinar(texto, zapi.nome_assinatura(autor_u or user))
    ok, detalhe = await asyncio.to_thread(dapi.editar_texto, numero, conv.contato_telefone, m.remote_id, texto_envio)
    if not ok:
        raise HTTPException(502, f'O WhatsApp recusou a edicao: {detalhe}')
    antes = m.conteudo or ''
    m.conteudo = texto
    if (conv.ultima_mensagem or '') == antes[:300]:
        conv.ultima_mensagem = texto[:300]
    await _historico_edicao(db, m.id, conv.id, antes, texto, user.email)
    await db.commit()
    bg.add_task(_absorver_eco_edicao, conv.id, m.id, texto_envio, datetime.utcnow())   # o eco volta assinado
    return {'ok': True, 'mensagem': m.to_dict()}


@router.get('/conversas/{conversa_id}/editadas')
async def mensagens_editadas(conversa_id: int, user: Usuario = Depends(usuario_atual),
                             db: AsyncSession = Depends(get_db)):
    """Ids das mensagens editadas dessa conversa (a tela marca 'editada')."""
    from sqlalchemy import text as _sql
    try:
        rows = (await db.execute(_sql("SELECT DISTINCT mensagem_id FROM wa_mensagens_edicoes WHERE conversa_id = :c"),
                                 {'c': conversa_id})).all()
    except Exception:
        return {'ids': []}
    return {'ids': [r[0] for r in rows]}


class NotaReq(BaseModel):
    texto: str


@router.post('/conversas/{conversa_id}/nota')
async def nota_interna(conversa_id: int, req: NotaReq,
                       user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Nota interna: so a equipe ve, NAO vai pro cliente e a IA ignora."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    db.add(Mensagem(conversa_id=conversa_id, origem='nota', autor=user.email,
                    tipo='texto', conteudo=req.texto))
    await db.commit()
    return {'ok': True}


class SubirDriveReq(BaseModel):
    message_ids: Optional[list[int]] = None   # None = todos os arquivos ainda nao subidos


@router.post('/conversas/{conversa_id}/subir-drive')
async def subir_drive(conversa_id: int, req: SubirDriveReq,
                      user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Sobe os arquivos que o lead mandou (imagem/doc/audio) pra pasta dele no Google Drive."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    if conv.pipeline_id in (await _pipelines_bloqueados(db, user)):
        raise HTTPException(403, 'Conversa de um pipeline pessoal de outro usuario')

    # quais mensagens subir
    cond = [Mensagem.conversa_id == conversa_id, Mensagem.origem == 'contato',
            Mensagem.tipo.in_(['imagem', 'doc', 'audio', 'video']), Mensagem.midia_url != '']
    if req.message_ids:
        cond.append(Mensagem.id.in_(req.message_ids))
    else:
        cond.append((Mensagem.midia_drive == '') | (Mensagem.midia_drive.is_(None)))
    msgs = (await db.execute(select(Mensagem).where(*cond).order_by(Mensagem.criado_em))).scalars().all()
    if not msgs:
        return {'ok': False, 'erro': 'Nenhum arquivo do lead para subir', 'itens': []}

    itens = [{'id': m.id, 'tipo': m.tipo, 'midia_url': m.midia_url, 'conteudo': m.conteudo} for m in msgs]
    nome_lead = conv.contato_nome or conv.contato_telefone
    data_str = datetime.utcnow().strftime('%Y-%m-%d')

    from ..whatsapp import drive_leads
    res = await asyncio.to_thread(drive_leads.subir_arquivos, nome_lead,
                                  conv.contato_telefone, itens, conv.drive_pasta_id or '', data_str)
    if not res.get('ok') and not res.get('pasta_id'):
        raise HTTPException(502, res.get('erro') or 'Falha ao subir para o Drive')

    # cache da pasta na conversa
    if res.get('pasta_id'):
        conv.drive_pasta_id = res['pasta_id']
        conv.drive_pasta_url = res.get('pasta_url', '')
    # marca cada mensagem que subiu
    por_id = {m.id: m for m in msgs}
    n_ok = 0
    for r in res.get('resultados', []):
        if r.get('ok') and por_id.get(r['id']):
            por_id[r['id']].midia_drive = r.get('url', '')
            n_ok += 1
    # nota interna de auditoria: diz ONDE caiu, senao ninguem sabe se foi pra pasta
    # do cliente ou pra de leads (regra da casa)
    if n_ok:
        caminho = res.get('caminho', '')
        onde = ('pasta do cliente' if res.get('destino') == 'cliente' else 'pasta de leads')
        db.add(Mensagem(conversa_id=conversa_id, origem='nota', autor=user.email, tipo='texto',
                        conteudo=f'[DRIVE] {n_ok} arquivo(s) salvos na {onde}'
                                 + (f' ({caminho})' if caminho else '')
                                 + f': {res.get("pasta_url","")}'))
    await db.commit()
    return {'ok': n_ok > 0, 'n_ok': n_ok, 'total': len(itens),
            'pasta_url': res.get('pasta_url', ''), 'destino': res.get('destino', ''),
            'caminho': res.get('caminho', ''), 'itens': res.get('resultados', []),
            'erro': res.get('erro', '') if n_ok == 0 else ''}


@router.post('/conversas/{conversa_id}/audio')
async def enviar_audio_conv(conversa_id: int, arquivo: UploadFile = File(...),
                            user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Recebe o audio gravado, converte pra ogg/opus e envia como voz pelo WhatsApp."""
    import os, uuid, subprocess
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
    raw = await arquivo.read()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    base = 'snd_' + uuid.uuid4().hex[:12]
    tmp = f'{MEDIA_DIR}/{base}.in'
    ogg = f'{MEDIA_DIR}/{base}.ogg'
    with open(tmp, 'wb') as fp:
        fp.write(raw)
    ffmpeg_bin = '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else 'ffmpeg'
    try:
        await asyncio.to_thread(subprocess.run, [ffmpeg_bin, '-y', '-i', tmp, '-c:a', 'libopus', '-b:a', '32k', ogg],
                                capture_output=True, timeout=60)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    if not os.path.exists(ogg):
        raise HTTPException(500, 'Falha ao converter o audio (ffmpeg)')
    url = f'{MIDIA_URL}/{base}.ogg'
    ok, info = await asyncio.to_thread(zapi.enviar_audio, numero, conv.contato_telefone, url)
    if not ok:
        raise HTTPException(502, f'Falha ao enviar audio pelo Z-API: {info}')
    # transcreve o proprio audio enviado (pra aparecer o texto no chat, igual aos do cliente)
    texto_audio = ''
    try:
        with open(ogg, 'rb') as fp:
            dados_ogg = fp.read()
        texto_audio = (await asyncio.to_thread(transcricao.transcrever_bytes, dados_ogg)) or ''
    except Exception:
        texto_audio = ''
    db.add(Mensagem(conversa_id=conversa_id, origem='atendente', autor=user.email,
                    tipo='audio', conteudo=texto_audio, midia_url=url, remote_id=(info or '')))
    conv.ia_ativa = False
    conv.ultima_mensagem = '[audio]'
    conv.atualizado_em = datetime.utcnow()
    await db.commit()
    return {'ok': True}


@router.post('/conversas/{conversa_id}/midia')
async def enviar_midia(conversa_id: int, arquivo: UploadFile = File(...), caption: str = Form(''),
                       user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Envia imagem ou documento pelo WhatsApp (botao +)."""
    import os, uuid
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
    raw = await arquivo.read()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    nome_orig = arquivo.filename or 'arquivo'
    ext = (nome_orig.rsplit('.', 1)[-1].lower() if '.' in nome_orig else 'bin')[:8]
    base = 'md_' + uuid.uuid4().hex[:12]
    fname = f'{base}.{ext}'
    with open(f'{MEDIA_DIR}/{fname}', 'wb') as fp:
        fp.write(raw)
    url = f'{MIDIA_URL}/{fname}'
    ctype = (arquivo.content_type or '')
    nome_baixo = nome_orig.lower()
    eh_video = ctype.startswith('video') or nome_baixo.endswith(('.mp4', '.mov', '.3gp', '.m4v'))
    if eh_video and len(raw) > TETO_VIDEO:
        # o WhatsApp nao toca video grande: recusar com recado e melhor que falhar calado
        raise HTTPException(413, 'Video de %.1f MB: o WhatsApp toca ate %d MB. Corte o video ou '
                                 'mande o link.' % (len(raw) / 1048576.0, TETO_VIDEO // 1048576))
    if eh_video:
        ok, info = await asyncio.to_thread(zapi.enviar_video, numero, conv.contato_telefone, url,
                                           caption, zapi.nome_assinatura(user))
        tipo, resumo = 'video', (caption or '[video]')
    elif ctype.startswith('image'):
        ok, info = await asyncio.to_thread(zapi.enviar_imagem, numero, conv.contato_telefone, url, caption,
                                           zapi.nome_assinatura(user))   # 08/10: legenda assinada
        tipo, resumo = 'imagem', (caption or '[imagem]')
    else:
        ok, info = await asyncio.to_thread(zapi.enviar_documento, numero, conv.contato_telefone, url, nome_orig, ext)
        tipo, resumo = 'doc', f'[documento] {nome_orig}'
    if not ok:
        raise HTTPException(502, f'Falha ao enviar pelo Z-API: {info}')
    db.add(Mensagem(conversa_id=conversa_id, origem='atendente', autor=user.email,
                    tipo=tipo, conteudo=caption, midia_url=url, remote_id=(info or '')))
    conv.ia_ativa = False
    conv.ultima_mensagem = resumo[:300]
    conv.atualizado_em = datetime.utcnow()
    await db.commit()
    return {'ok': True}


# ===================== FLUXOS AUTOMATICOS (cadencia de mensagens) =====================
# Publico: trabalhador (reclamacao trabalhista). Nunca cobra pagamento por aqui e nao fala de
# honorarios: condicoes ficam no contrato, com a advogada. Linguagem do escritorio: didatica com formal.
# Motor roda 1x/dia: para cada fluxo ativo, calcula os dias desde o inicio e manda a
# mensagem do dia devido que ainda nao foi enviada. Para quando conclui ou o lead responde.
FLUXOS = {
    'assinatura': {
        'nome': 'Assinatura de documentos',
        'descricao': 'Lembra o cliente de assinar o contrato e a procuracao para o escritorio dar entrada na acao.',
        'passos': [
            {'ordem': 1, 'apos_horas': 0, 'texto': (
                "{nome}, o seu contrato e a procuracao ja estao prontos para assinatura. A assinatura e feita pelo "
                "celular, leva poucos minutos e e o que permite ao escritorio dar entrada na sua acao. "
                "Consegue assinar hoje?")},
            {'ordem': 2, 'apos_horas': 6, 'texto': (
                "{nome}, explicando o proximo passo: depois da sua assinatura, o escritorio pede os seus documentos, "
                "prepara a peticao inicial e entra com a acao na Justica do Trabalho. Tudo comeca pela assinatura.")},
            {'ordem': 3, 'apos_horas': 24, 'texto': (
                "{nome}, se ficou alguma duvida sobre o contrato, pode me perguntar aqui. Prefiro que voce assine "
                "tranquilo, entendendo cada ponto.")},
            {'ordem': 4, 'apos_horas': 48, 'texto': (
                "{nome}, quem cuida do seu caso e a Dra. Advogada, advogada do escritorio. Assim que voce assinar, "
                "ela ja comeca a preparar a sua acao.")},
            {'ordem': 5, 'apos_horas': 96, 'texto': (
                "{nome}, um lembrete importante: os direitos trabalhistas tem prazo. A acao precisa ser proposta em "
                "ate dois anos depois da saida da empresa. Por isso vale nao deixar a assinatura para depois.")},
            {'ordem': 6, 'apos_horas': 168, 'texto': (
                "{nome}, o que esta impedindo a assinatura? Se for duvida sobre como funciona ou sobre o que voce "
                "tem direito, me conte que eu explico ou peco para a advogada falar com voce.")},
            {'ordem': 7, 'apos_horas': 288, 'texto': (
                "{nome}, o seu caso continua reservado aqui. Se quiser seguir, me responda com um sim que eu "
                "reenvio o link da assinatura.")},
            {'ordem': 8, 'apos_horas': 480, 'texto': (
                "{nome}, este e o meu ultimo contato sobre a assinatura. Se quiser seguir com a acao, e so responder "
                "aqui. Se decidiu nao seguir, me avise para eu encerrar o seu atendimento. Fico a disposicao.")},
        ],
    },
    'documentos': {
        'nome': 'Envio de documentos',
        'descricao': 'Depois de assinar, pede os documentos para montar o processo.',
        'passos': [
            {'ordem': 1, 'apos_horas': 0, 'texto': (
                "{nome}, assinatura recebida, obrigado. Agora, para preparar a sua acao, preciso de: documento com "
                "foto (RG ou CNH), carteira de trabalho (fisica ou a digital), comprovante de endereco e, se tiver, "
                "holerites, termo de rescisao e qualquer prova do que aconteceu (mensagens, fotos, atestados). "
                "Pode enviar foto aqui mesmo.")},
            {'ordem': 2, 'apos_horas': 24, 'texto': (
                "{nome}, estou aguardando os seus documentos para a advogada comecar a peticao: documento com foto, "
                "carteira de trabalho e comprovante de endereco. Se tiver dificuldade para achar algum, me avise "
                "que eu explico como conseguir.")},
            {'ordem': 3, 'apos_horas': 72, 'texto': (
                "{nome}, sem os documentos o processo nao avanca. Me envie hoje o que tiver em maos; o que faltar, "
                "a gente resolve depois.")},
        ],
    },
    'contratacao': {
        'nome': 'Contratacao — documentos do cliente',
        'descricao': 'Dispara sozinho quando o card cai em "Contrato / Assinatura": pede documento '
                     'pessoal + comprovante de residencia (ou o endereco escrito) para preparar o contrato.',
        'passos': [
            {'ordem': 1, 'apos_horas': 0, 'texto': (
                "{nome}, que bom ter voce com a gente. Para preparar o seu contrato, preciso de duas coisas:\n\n"
                "1) Uma foto do seu RG ou CNH\n"
                "2) Um comprovante de residencia (conta de luz, agua, telefone)\n\n"
                "Se nao tiver o comprovante agora, pode me mandar o endereco completo escrito (rua, numero, bairro, "
                "cidade, estado e CEP).")},
            {'ordem': 2, 'apos_horas': 6, 'texto': (
                "{nome}, estou aguardando o documento com foto e o comprovante de residencia (ou o endereco escrito) "
                "para finalizar o seu contrato.")},
            {'ordem': 3, 'apos_horas': 24, 'texto': (
                "{nome}, o seu contrato esta quase pronto, falta so o documento com foto e o endereco. Se tiver "
                "dificuldade para fotografar, me avise que eu explico o passo a passo.")},
            {'ordem': 4, 'apos_horas': 72, 'texto': (
                "{nome}, para nao perder prazo, me envie hoje o documento com foto e o comprovante de residencia "
                "(ou o endereco escrito). Com eles eu finalizo o contrato no mesmo dia.")},
        ],
    },
    'indicacao': {
        'nome': 'Indicacao',
        'descricao': 'Primeiro contato com quem foi indicado por um cliente ou parceiro do escritorio.',
        'passos': [
            {'ordem': 1, 'apos_horas': 0, 'texto': (
                "Ola, {nome}, tudo bem? Aqui e do Escritório Modelo Advogados. Recebemos o seu contato por indicacao "
                "e estamos a disposicao. Me conte, por favor, o que esta acontecendo para eu direcionar ao "
                "atendimento certo.")},
            {'ordem': 2, 'apos_horas': 24, 'texto': (
                "Ola, {nome}. Passando para nao deixar voce sem retorno. Quando puder, me conte o que precisa.")},
            {'ordem': 3, 'apos_horas': 96, 'texto': (
                "{nome}, seguimos a disposicao. E so chamar aqui quando precisar. Equipe Escritório Modelo Advogados.")},
        ],
    },
    # Reaquecimento: lead com caso que disse "agora nao, mais para frente". Tres lembretes no ano.
    # Inscricao automatica pelo termometro (criado_por='auto:adiar') ou manual. Desliga se o lead responder.
    'reaquecimento_bimestral': {
        'nome': 'Reaquecimento anual (não quer agora ou quer depois)',
        'descricao': 'Lead com caso que disse que não quer agora ou pediu para falar mais pra frente. Três lembretes de direitos no ano, aos 4, 8 e 12 meses; para sozinho se ele responder.',
        'passos': [
            {'ordem': 1, 'apos_horas': 2880, 'texto': (
                "Ola, {nome}, aqui e do Escritório Modelo Advogados. Voce comentou que preferia deixar para mais "
                "para frente, e esta tudo certo. So lembrando: os direitos trabalhistas podem ser cobrados em ate "
                "dois anos depois da saida da empresa. Quando quiser conversar sobre o seu caso, e so responder aqui.")},
            {'ordem': 2, 'apos_horas': 5760, 'texto': (
                "{nome}, tudo bem? Muita gente descobre tarde que tinha direito a horas extras, intervalo, adicional "
                "ou verbas da rescisao que nao foram pagas. Se quiser, a gente conversa sobre o seu caso. E so "
                "mandar um oi.")},
            {'ordem': 3, 'apos_horas': 8640, 'texto': (
                "{nome}, este e o meu ultimo lembrete. Se um dia decidir olhar o que a empresa ficou devendo, o "
                "Escritório Modelo Advogados estara a disposicao. Guarde o nosso contato.")},
        ],
    },
}


def _primeiro_nome(conv):
    nome = (conv.contato_nome or '').strip()
    if not nome or '@lid' in nome:
        return 'tudo bem'
    return nome.split()[0]


class FluxoReq(BaseModel):
    tipo: str
    ativo: bool = True


@router.get('/fluxos/tipos')
async def fluxos_tipos(user: Usuario = Depends(usuario_atual)):
    """Catalogo dos fluxos disponiveis + a cadencia de cada um (pra UI)."""
    return {'items': [{'tipo': k, 'nome': v['nome'], 'descricao': v['descricao'],
                       'passos': len(v['passos']),
                       'apos_horas': [p['apos_horas'] for p in v['passos']]} for k, v in FLUXOS.items()]}


@router.post('/conversas/{conversa_id}/fluxo')
async def set_fluxo(conversa_id: int, req: FluxoReq,
                    user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Liga/desliga um fluxo de cadencia numa conversa."""
    if req.tipo not in FLUXOS:
        raise HTTPException(400, 'Tipo de fluxo invalido')
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    fl = (await db.execute(select(Fluxo).where(
        Fluxo.conversa_id == conversa_id, Fluxo.tipo == req.tipo))).scalar_one_or_none()
    if req.ativo:
        if fl and fl.ativo:
            return {'ok': True, 'fluxo': fl.to_dict()}  # ja ativo
        if fl:  # reativa do zero (recomeca a cadencia)
            fl.ativo = True; fl.iniciado_em = datetime.utcnow(); fl.etapas_enviadas = ''
            fl.concluido_em = None; fl.criado_por = user.email
        else:
            fl = Fluxo(conversa_id=conversa_id, tipo=req.tipo, criado_por=user.email)
            db.add(fl)
        await db.commit(); await db.refresh(fl)
        return {'ok': True, 'fluxo': fl.to_dict()}
    else:
        if fl and fl.ativo:
            fl.ativo = False; fl.concluido_em = datetime.utcnow()
            await db.commit()
        return {'ok': True, 'desativado': True}


@router.get('/conversas/{conversa_id}/fluxos')
async def fluxos_da_conversa(conversa_id: int, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Fluxo).where(Fluxo.conversa_id == conversa_id))
    return {'items': [f.to_dict() for f in res.scalars().all()]}


@router.post('/conversas/{conversa_id}/briefing')
async def gerar_briefing_conv(conversa_id: int, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Gera (ou regera) o BRIEFING DE FECHAMENTO da conversa como nota interna, sob demanda."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    msgs = (await db.execute(select(Mensagem).where(Mensagem.conversa_id == conversa_id)
                             .order_by(Mensagem.criado_em.desc()).limit(60))).scalars().all()
    msgs = list(reversed(msgs))  # volta pra ordem cronologica (query pegou as 60 MAIS RECENTES)
    # monta o historico (contato=user, ia/atendente=assistant; ignora nota e vazio; funde e apara)
    raw = [{'role': 'user' if m.origem == 'contato' else 'assistant', 'content': m.conteudo}
           for m in msgs if m.origem != 'nota' and (m.conteudo or '').strip()
           and not (m.conteudo or '').startswith(MARK_BRIEFING)]
    hist = []
    for msg in raw:
        if hist and hist[-1]['role'] == msg['role']:
            hist[-1]['content'] += '\n' + msg['content']
        else:
            hist.append(dict(msg))
    while hist and hist[0]['role'] != 'user':
        hist.pop(0)
    while hist and hist[-1]['role'] != 'user':
        hist.pop()
    if not hist:
        raise HTTPException(400, 'Sem conversa suficiente para gerar o briefing')
    nome = (conv.contato_nome or 'o cliente').split()[0]
    dados_calc = sdr_reclamante.parse_formulario(hist[0]['content']) if conv.origem == CAMPANHA_FORMULARIO else None
    briefing = await asyncio.to_thread(sdr_reclamante.gerar_briefing_fechamento, hist, nome, dados_calc)
    if not briefing:
        raise HTTPException(502, 'Falha ao gerar o briefing')
    db.add(Mensagem(conversa_id=conversa_id, origem='nota', autor='briefing',
                    tipo='texto', conteudo=f'{MARK_BRIEFING}\n{briefing}'))
    conv.ultima_mensagem = 'Briefing de fechamento gerado'
    conv.atualizado_em = datetime.utcnow()
    await db.commit()
    return {'ok': True, 'briefing': briefing}


class ProcessarFluxosReq(BaseModel):
    token: Optional[str] = ''
    dry_run: bool = False


@router.post('/fluxos/processar')
async def processar_fluxos(req: ProcessarFluxosReq, request: Request, db: AsyncSession = Depends(get_db)):
    """Motor diario: manda as mensagens devidas de cada fluxo ativo. Chamado por cron.
    Protegido por token (settings.cron_token) — nao exige login pra rodar via cron."""
    from ..config import settings
    esperado = getattr(settings, 'CRON_TOKEN', None) or ''
    if esperado and req.token != esperado:
        raise HTTPException(403, 'Token invalido')

    agora = datetime.utcnow()
    res = await db.execute(select(Fluxo).where(Fluxo.ativo == True))
    ativos = res.scalars().all()
    enviados, concluidos, pulados = [], 0, 0
    LIMITE_RODADA = 20  # trava anti-spam: no maximo 20 envios reais por rodada (protege o numero)
    for fl in ativos:
        if not req.dry_run and len(enviados) >= LIMITE_RODADA:
            break  # atingiu o limite da rodada; o resto sai na proxima
        tpl = FLUXOS.get(fl.tipo)
        if not tpl:
            continue
        horas = (agora - fl.iniciado_em).total_seconds() / 3600 if fl.iniciado_em else 0
        enviadas = set(int(x) for x in fl.etapas_enviadas.split(',') if x.strip().isdigit())
        conv = (await db.execute(select(Conversa).where(Conversa.id == fl.conversa_id))).scalar_one_or_none()
        if not conv:
            fl.ativo = False; fl.concluido_em = agora; continue
        numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
        # manda SO a primeira etapa devida ainda nao enviada (1 msg por rodada -> 2x/dia com 2 crons)
        passo = next((p for p in tpl['passos']
                      if p['apos_horas'] <= horas and p['ordem'] not in enviadas), None)
        if passo:
            texto = passo['texto'].format(nome=_primeiro_nome(conv))
            if req.dry_run:
                enviados.append({'conversa_id': conv.id, 'tipo': fl.tipo, 'ordem': passo['ordem'], 'dry_run': True})
            else:
                ok, info = await asyncio.to_thread(zapi.enviar_texto, numero, conv.contato_telefone, texto) if numero else (False, 'sem numero')
                if ok:
                    db.add(Mensagem(conversa_id=conv.id, origem='atendente', autor='fluxo:' + fl.tipo,
                                    tipo='texto', conteudo=texto, remote_id=(info or '')))
                    conv.ultima_mensagem = texto[:300]; conv.atualizado_em = agora
                    enviadas.add(passo['ordem'])
                    fl.etapas_enviadas = ','.join(str(x) for x in sorted(enviadas))
                    enviados.append({'conversa_id': conv.id, 'tipo': fl.tipo, 'ordem': passo['ordem']})
                else:
                    pulados += 1
        # concluiu se ja enviou a ultima etapa
        ultima_ordem = tpl['passos'][-1]['ordem']
        if ultima_ordem in enviadas and not req.dry_run:
            fl.ativo = False; fl.concluido_em = agora; concluidos += 1
    if not req.dry_run:
        await db.commit()
    return {'ok': True, 'ativos': len(ativos), 'enviados': enviados,
            'total_enviados': len(enviados), 'concluidos': concluidos, 'pulados': pulados}


# ===================== FIGURINHAS (stickers) =====================

@router.get('/figurinhas')
async def listar_figurinhas(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Galeria de figurinhas favoritas do escritorio."""
    q = select(Figurinha).where(Figurinha.ativo == True).order_by(Figurinha.ordem, Figurinha.id.desc())
    res = await db.execute(q)
    return {'items': [f.to_dict() for f in res.scalars().all()]}


def _bytes_para_webp(raw, nome_arquivo=''):
    """Salva bytes como .webp 512x512 (padrao sticker) em MEDIA_DIR. Retorna o filename."""
    import os, uuid, io
    os.makedirs(MEDIA_DIR, exist_ok=True)
    base = 'fig_' + uuid.uuid4().hex[:12]
    fname = f'{base}.webp'
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert('RGBA')
        # encaixa em 512x512 mantendo proporcao, fundo transparente
        img.thumbnail((512, 512), Image.LANCZOS)
        canvas = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
        canvas.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2), img)
        canvas.save(f'{MEDIA_DIR}/{fname}', 'WEBP')
    except Exception:
        # se nao der pra converter, salva o arquivo original
        ext = (nome_arquivo or 'x.webp').rsplit('.', 1)[-1].lower()[:5] or 'webp'
        fname = f'{base}.{ext}'
        with open(f'{MEDIA_DIR}/{fname}', 'wb') as fp:
            fp.write(raw)
    return fname


@router.post('/figurinhas')
async def add_figurinha(arquivo: UploadFile = File(...), nome: str = Form(''),
                        user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Sobe uma figurinha pra galeria. Converte pra .webp 512x512 (padrao sticker)."""
    raw = await arquivo.read()
    fname = _bytes_para_webp(raw, arquivo.filename or '')
    url = f'{MIDIA_URL}/{fname}'
    fig = Figurinha(nome=(nome or arquivo.filename or '')[:120], url=url)
    db.add(fig)
    await db.commit()
    await db.refresh(fig)
    return {'ok': True, 'figurinha': fig.to_dict()}


class SalvarFigUrlReq(BaseModel):
    url: str
    nome: Optional[str] = ''


@router.post('/figurinhas/salvar')
async def salvar_figurinha_url(req: SalvarFigUrlReq,
                               user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Salva na galeria uma figurinha RECEBIDA (baixa a imagem, pois o link do WhatsApp expira)."""
    if not req.url:
        raise HTTPException(400, 'URL vazia')
    import requests as _rq
    try:
        r = await asyncio.to_thread(lambda: _rq.get(req.url, timeout=30))
        if r.status_code != 200 or not r.content:
            raise HTTPException(502, f'Nao consegui baixar a figurinha (HTTP {r.status_code})')
        raw = r.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Falha ao baixar a figurinha: {e}')
    fname = _bytes_para_webp(raw)
    url = f'{MIDIA_URL}/{fname}'
    fig = Figurinha(nome=(req.nome or '')[:120], url=url)
    db.add(fig)
    await db.commit()
    await db.refresh(fig)
    return {'ok': True, 'figurinha': fig.to_dict()}


@router.delete('/figurinhas/{fig_id}')
async def del_figurinha(fig_id: int, user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    fig = (await db.execute(select(Figurinha).where(Figurinha.id == fig_id))).scalar_one_or_none()
    if not fig:
        raise HTTPException(404, 'Figurinha nao encontrada')
    import os
    if fig.url and MIDIA_MARCA + '/' in fig.url:
        try:
            os.remove(f"{MEDIA_DIR}/{fig.url.split('/media/')[-1]}")
        except Exception:
            pass
    fig.ativo = False
    await db.commit()
    return {'ok': True}


class FigurinhaEnvioReq(BaseModel):
    figurinha_id: int


@router.post('/conversas/{conversa_id}/figurinha')
async def enviar_figurinha_conv(conversa_id: int, req: FigurinhaEnvioReq,
                                user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Envia uma figurinha da galeria pelo WhatsApp."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    fig = (await db.execute(select(Figurinha).where(Figurinha.id == req.figurinha_id))).scalar_one_or_none()
    if not fig:
        raise HTTPException(404, 'Figurinha nao encontrada')
    numero = (await db.execute(select(NumeroWhatsapp).where(NumeroWhatsapp.id == conv.numero_id))).scalar_one_or_none()
    ok, info = await asyncio.to_thread(zapi.enviar_figurinha, numero, conv.contato_telefone, fig.url)
    if not ok:
        raise HTTPException(502, f'Falha ao enviar pelo Z-API: {info}')
    db.add(Mensagem(conversa_id=conversa_id, origem='atendente', autor=user.email,
                    tipo='figurinha', conteudo='', midia_url=fig.url, remote_id=(info or '')))
    conv.ia_ativa = False
    conv.ultima_mensagem = '[figurinha]'
    conv.atualizado_em = datetime.utcnow()
    await db.commit()
    return {'ok': True}


class IAReq(BaseModel):
    ativa: bool


@router.post('/conversas/{conversa_id}/ia')
async def toggle_ia(conversa_id: int, req: IAReq, bg: BackgroundTasks,
                    user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Liga/desliga a IA na conversa. Ao LIGAR, ela ja responde a ultima mensagem."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    conv.ia_ativa = req.ativa
    if req.ativa:
        conv.e_reclamante = True   # ligar manual = IA cuida dessa conversa (bypassa o filtro dali pra frente)
    await db.commit()
    if req.ativa:
        bg.add_task(_responder_ia, conv.id, True)   # forcar=True: dispara mesmo sem assunto trabalhista
    return {'ok': True, 'ia_ativa': conv.ia_ativa}


class EtapaReq(BaseModel):
    etapa: str
    pipeline_id: Optional[int] = None   # se vier, tambem move de pipeline


@router.post('/conversas/{conversa_id}/etapa')
async def mover_etapa(conversa_id: int, req: EtapaReq,
                      user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    pid = req.pipeline_id if req.pipeline_id is not None else conv.pipeline_id
    # valida que a coluna existe nesse pipeline
    cols = (await db.execute(select(Stage.nome).where(Stage.pipeline_id == pid))).scalars().all()
    if cols and req.etapa not in cols:
        raise HTTPException(400, f'Coluna invalida nesse pipeline. Use: {cols}')
    entrou_no_contrato = (req.etapa == ETAPA_CONTRATO and conv.etapa != ETAPA_CONTRATO)
    # Troca de BOARD feita por gente arma a trava: daqui pra frente nenhuma
    # automacao tira este card daqui (ver TRAVA DO BOARD). Trocar so de coluna
    # dentro do mesmo board nao arma nada — senao os watchers parariam.
    trocou_board = (pid != conv.pipeline_id)
    # Regra da casa: mudar de BOARD e so de administrador. Arrastar
    # o card entre COLUNAS do mesmo board continua liberado pra equipe toda.
    if trocou_board and not _pode_trocar_board(user):
        _registrar_move(db, conv, pid, req.etapa, user.email, 'ui', bloqueado=True)
        await db.commit()
        raise HTTPException(403, 'Voce nao tem permissao para trocar o card de board. '
                                 'Voce pode mover o card entre as colunas deste board.')
    _registrar_move(db, conv, pid, req.etapa, user.email, 'ui')
    conv.pipeline_id = pid
    conv.etapa = req.etapa
    if trocou_board:
        conv.board_fixado = 1
        conv.board_fixado_por = user.email
        conv.board_fixado_em = datetime.utcnow().isoformat(sep=' ')
    # a classificacao feita por gente fica gravada no CONTATO, nao so nesta conversa
    try:
        await classificacao_contato.lembrar(db, conv, user.email, 'ui')
    except Exception as e:
        print('[classificacao-contato] falhou ao gravar (ui): %s' % e, flush=True)
    # Contrato fechado -> pede sozinho os documentos do cliente (CNH + comprovante/endereco)
    fluxo_disparado = False
    if entrou_no_contrato:
        fluxo_disparado = await _iniciar_fluxo(db, conv, FLUXO_CONTRATACAO)
    await db.commit()
    return {'ok': True, 'pipeline_id': conv.pipeline_id, 'etapa': conv.etapa,
            'board_fixado': bool(conv.board_fixado), 'fluxo_documentos': fluxo_disparado}


@router.get('/conversas/{conversa_id}/board-historico')
async def board_historico(conversa_id: int, user: Usuario = Depends(usuario_atual),
                          db: AsyncSession = Depends(get_db)):
    """Quem moveu este card de board, quando e por que — inclusive as tentativas
    que a trava recusou. E a resposta para 'meu card voltou pro Reclamantes sozinho'."""
    nomes = {p.id: p.nome for p in (await db.execute(select(Pipeline))).scalars().all()}
    rows = (await db.execute(select(MoveBoard).where(MoveBoard.conversa_id == conversa_id)
                             .order_by(desc(MoveBoard.id)).limit(100))).scalars().all()
    itens = []
    for r in rows:
        d = r.to_dict()
        d['de_board'] = nomes.get(r.de_pipeline, str(r.de_pipeline))
        d['para_board'] = nomes.get(r.para_pipeline, str(r.para_pipeline))
        itens.append(d)
    return {'items': itens}


class BoardTravaReq(BaseModel):
    fixado: bool


@router.post('/conversas/{conversa_id}/board-trava')
async def board_trava(conversa_id: int, req: BoardTravaReq,
                      user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Liga/desliga a trava na mao. Destravar devolve o card para as automacoes."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    conv.board_fixado = 1 if req.fixado else 0
    conv.board_fixado_por = user.email if req.fixado else ''
    conv.board_fixado_em = datetime.utcnow().isoformat(sep=' ') if req.fixado else ''
    _registrar_move(db, conv, conv.pipeline_id, conv.etapa, user.email,
                    'ui:trava-on' if req.fixado else 'ui:trava-off')
    await db.commit()
    return {'ok': True, 'board_fixado': bool(conv.board_fixado)}


# ============================================================
# REGUA DE CONTATOS
# ============================================================
# "Dentro de Lead Quente eu preciso saber se ele ja foi contatado, e qual foi o
#  1o, 2o, 3o, 4o contato."
#
# A regua e DERIVADA das mensagens, nao digitada. Um CONTATO (toque) e uma
# investida nossa: as mensagens que saem juntas, sem resposta do lead no meio e
# sem passar de JANELA_TOQUE_H horas entre elas, contam como UM contato — que e
# como a mesa conta ("liguei tres vezes"), nao como o banco conta (17 mensagens).
#
# Conta como contato: mensagem/audio da equipe, disparo de fluxo, resposta da IA
# e ligacao pelo ramal. Nao conta: nota interna (ninguem falou com o lead).
JANELA_TOQUE_H = 6
ORIGENS_NOSSAS = ('atendente', 'ia', 'ligacao')


def _rotulo_robo(autor: str) -> str:
    """Rotulo legivel do robo pelo autor gravado na mensagem ('ia:reclamante' / 'ia:empresas')."""
    a = (autor or '').lower()
    if a.startswith('ia:empresas'):
        return 'Recepcao Empresas (IA)'
    if a.startswith('ia:'):
        return sdr_reclamante.NOME + ' (IA)'
    return autor


def _quem_falou(m):
    """Devolve (rotulo, especie) do autor de um toque."""
    autor = (m.autor or '').strip()
    if m.origem == 'ligacao':
        return (f'ligacao (ramal {autor})' if autor else 'ligacao', 'ligacao')
    if m.origem == 'ia':
        return (_rotulo_robo(autor) if autor else sdr_reclamante.NOME + ' (IA)', 'ia')
    if autor.startswith('fluxo:'):
        return (f'automacao ({autor[6:]})', 'automacao')
    if autor:
        return (autor, 'humano')
    return ('equipe (pelo celular)', 'humano')


def _horas(a, b):
    try:
        return abs((a - b).total_seconds()) / 3600.0
    except Exception:
        return 999.0


def _montar_regua(msgs):
    """msgs em ordem cronologica -> (toques, resumo). Nao toca no banco."""
    toques, atual, respondeu_desde = [], None, False
    for m in msgs:
        if m.origem == 'nota':
            continue                              # nota interna nao e contato
        if m.origem == 'contato':
            if atual and not atual['respondeu']:
                atual['respondeu'] = True
                atual['respondeu_em'] = m.criado_em
                atual['resposta_em_horas'] = round(_horas(m.criado_em, atual['quando']), 1)
            respondeu_desde = True
            continue
        if m.origem not in ORIGENS_NOSSAS:
            continue
        novo = (atual is None or respondeu_desde
                or _horas(m.criado_em, atual['ultima_em']) > JANELA_TOQUE_H)
        if novo:
            rotulo, especie = _quem_falou(m)
            atual = {'numero': len(toques) + 1, 'quando': m.criado_em,
                     'ultima_em': m.criado_em, 'quem': rotulo, 'especie': especie,
                     'canais': [m.tipo], 'mensagens': 1,
                     'respondeu': False, 'respondeu_em': None, 'resposta_em_horas': None,
                     'previa': (getattr(m, 'conteudo', '') or '')[:160]}
            toques.append(atual)
            respondeu_desde = False
        else:
            atual['ultima_em'] = m.criado_em
            atual['mensagens'] += 1
            if m.tipo not in atual['canais']:
                atual['canais'].append(m.tipo)
    agora = datetime.utcnow()
    ult = toques[-1] if toques else None
    resumo = {
        'total_contatos': len(toques),
        'ultimo_numero': ult['numero'] if ult else 0,
        'ultimo_em': ult['quando'].isoformat() + 'Z' if ult else None,
        'ultimo_quem': ult['quem'] if ult else '',
        'ultimo_respondeu': bool(ult['respondeu']) if ult else False,
        'dias_desde_ultimo': round(_horas(agora, ult['quando']) / 24.0, 1) if ult else None,
        'contatos_sem_resposta_no_fim': 0,
        'primeiro_em': toques[0]['quando'].isoformat() + 'Z' if toques else None,
    }
    for t in reversed(toques):                    # quantos toques seguidos no vacuo
        if t['respondeu']:
            break
        resumo['contatos_sem_resposta_no_fim'] += 1
    saida = []
    for t in toques:
        saida.append({'numero': t['numero'], 'quando': t['quando'].isoformat() + 'Z',
                      'quem': t['quem'], 'especie': t['especie'],
                      'canais': t['canais'], 'mensagens': t['mensagens'],
                      'respondeu': t['respondeu'],
                      'respondeu_em': t['respondeu_em'].isoformat() + 'Z' if t['respondeu_em'] else None,
                      'resposta_em_horas': t['resposta_em_horas'],
                      'previa': t['previa']})
    return saida, resumo


async def _resumo_contatos_em_lote(db, convs):
    """Resumo da regua para varios cards de uma vez, com consulta enxuta (so as
    colunas que a regua usa). E o que alimenta a CAPA do card no quadro."""
    if not convs:
        return {}
    ids = [c.id for c in convs]
    linhas = (await db.execute(
        select(Mensagem.conversa_id, Mensagem.origem, Mensagem.criado_em,
               Mensagem.tipo, Mensagem.autor)
        .where(Mensagem.conversa_id.in_(ids), Mensagem.origem != 'nota')
        .order_by(Mensagem.conversa_id, Mensagem.criado_em, Mensagem.id))).all()
    por_conv = {}
    for l in linhas:
        por_conv.setdefault(l.conversa_id, []).append(l)
    nomes_board = {p.id: p.nome for p in
                   (await db.execute(select(Pipeline))).scalars().all()}
    agora = datetime.utcnow()
    saida = {}
    for c in convs:
        msgs = por_conv.get(c.id, [])
        _, r = _montar_regua(msgs)
        # a bola esta com a gente? = a ultima palavra foi do lead
        ultima = msgs[-1] if msgs else None
        esperando = bool(ultima is not None and ultima.origem == 'contato')
        # qual robo atende este card, para a tela poder dizer (ultimo autor de IA gravado)
        _autores_ia = [getattr(m, 'autor', '') for m in msgs if getattr(m, 'origem', '') == 'ia']
        _robo = _rotulo_robo(_autores_ia[-1]) if _autores_ia else ""
        saida[c.id] = {
            'pipeline_nome': nomes_board.get(c.pipeline_id, ''),
            'robo_ia': _robo,
            'contatos_total': r['total_contatos'],
            'aguardando_retorno': esperando,
            'horas_aguardando': round(_horas(agora, ultima.criado_em), 1) if esperando else None,
            'sem_resposta_seguidos': r['contatos_sem_resposta_no_fim'],
        }
    return saida


@router.get('/conversas/{conversa_id}/regua-contatos')
async def regua_contatos(conversa_id: int, user: Usuario = Depends(usuario_atual),
                         db: AsyncSession = Depends(get_db)):
    """A regua de UM lead: 1o, 2o, 3o contato, quem fez, por onde e se respondeu."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    if conv.pipeline_id in (await _pipelines_bloqueados(db, user)):
        raise HTTPException(403, 'Conversa de um pipeline pessoal de outro usuario')
    msgs = (await db.execute(select(Mensagem).where(Mensagem.conversa_id == conversa_id)
                             .order_by(Mensagem.criado_em, Mensagem.id))).scalars().all()
    toques, resumo = _montar_regua(msgs)
    return {'conversa_id': conversa_id, 'contato_nome': conv.contato_nome,
            'etapa': conv.etapa, 'pipeline_id': conv.pipeline_id,
            'resumo': resumo, 'toques': toques}


@router.get('/regua-contatos')
async def regua_da_coluna(pipeline_id: int, etapa: Optional[str] = None,
                          limite: int = 200, user: Usuario = Depends(usuario_atual),
                          db: AsyncSession = Depends(get_db)):
    """A regua da COLUNA inteira (ex.: Lead Quente): quem esta no 1o contato, quem
    ja levou quatro e nao respondeu. Ordena pelo mais parado primeiro, que e a
    fila que a mesa precisa atacar."""
    if pipeline_id in (await _pipelines_bloqueados(db, user)):
        raise HTTPException(403, 'Pipeline pessoal de outro usuario')
    q = select(Conversa).where(Conversa.pipeline_id == pipeline_id,
                               Conversa.status == 'aberto')
    if etapa:
        q = q.where(Conversa.etapa == etapa)
    convs = (await db.execute(q.limit(max(1, min(limite, 500))))).scalars().all()
    if not convs:
        return {'items': [], 'total': 0}
    ids = [c.id for c in convs]
    msgs = (await db.execute(select(Mensagem).where(Mensagem.conversa_id.in_(ids))
                             .order_by(Mensagem.criado_em, Mensagem.id))).scalars().all()
    por_conv = {}
    for m in msgs:
        por_conv.setdefault(m.conversa_id, []).append(m)
    itens = []
    for c in convs:
        _, resumo = _montar_regua(por_conv.get(c.id, []))
        itens.append({'conversa_id': c.id, 'contato_nome': c.contato_nome,
                      'contato_telefone': c.contato_telefone, 'etapa': c.etapa,
                      'tag': c.tag or '', 'origem': c.origem or '',
                      'atendente_email': c.atendente_email or '', **resumo})
    itens.sort(key=lambda i: (-(i['contatos_sem_resposta_no_fim'] or 0),
                              -(i['dias_desde_ultimo'] or 0)))
    return {'items': itens, 'total': len(itens)}


class DadosReq(BaseModel):
    # NAO acrescente `produto` aqui. O endpoint abaixo grava qualquer campo deste modelo sem
    # checar papel, sem validar e sem registrar quem mudou, e produto decide esteira, meta e
    # comissao. Ele tem endpoint proprio: PATCH /conversas/{id}/produto.
    contato_nome: Optional[str] = None
    tag: Optional[str] = None
    origem: Optional[str] = None
    responsavel: Optional[str] = None
    email: Optional[str] = None
    handle: Optional[str] = None
    valor: Optional[float] = None
    notas: Optional[str] = None


@router.patch('/conversas/{conversa_id}/dados')
async def editar_dados(conversa_id: int, req: DadosReq,
                       user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Edita a ficha do cliente/lead (tag, origem, responsavel, valor, etc.)."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    for campo, val in req.model_dump(exclude_none=True).items():
        setattr(conv, campo, val)
    await db.commit()
    return {'ok': True, 'conversa': conv.to_dict()}


# ============================================================
# O PRODUTO DO CARD
# ============================================================
# Por que nao entrou no `editar_dados` acima: aquele endpoint grava qualquer campo do modelo
# sem checar papel, sem validar valor, sem registrar quem mudou e sem controle de versao.
# Produto decide de que esteira o cliente recebe mensagem, qual meta a venda alimenta e quanto
# vale o card, entao ganhou endpoint proprio, com permissao, validacao, auditoria e versao.
#
# Regra: app/produto_card.py.
#
# NADA SAI PARA O CLIENTE AQUI. `trocar_produto` devolve um PLANO (freiar a esteira, pedir
# desculpa, reiniciar o kit, avisar o time) e este endpoint transforma o plano numa TAREFA
# para uma pessoa concluir. Motor sugere, humano conclui.

def _caminho_banco():
    """Arquivo SQLite do DATABASE_URL (sqlite+aiosqlite:///./x.db), relativo ao backend."""
    url = settings.DATABASE_URL or ''
    arq = url.split(':///', 1)[1] if ':///' in url else 'sistema_esc.db'
    pth = Path(arq)
    return str(pth if pth.is_absolute() else (Path(__file__).parent.parent.parent / pth))


_BANCO_PRODUTO = _caminho_banco()


def _con_produto():
    con = _sqlite3.connect(_BANCO_PRODUTO, timeout=60)
    con.execute('pragma busy_timeout=60000')
    return con


class ProdutoReq(BaseModel):
    produto: str                          # 'trabalhista' ou 'empresarial'
    de_produto: Optional[str] = None      # o que a tela estava mostrando, para pegar conflito
    motivo: str = ''


def _usuario_id_por_email(con, email):
    if not email:
        return None, ''
    r = con.execute("select id, coalesce(nome,'') from usuarios where lower(email)=? limit 1",
                    ((email or '').strip().lower(),)).fetchone()
    return (r[0], r[1]) if r else (None, '')


def _abrir_tarefa_da_troca(con, plano, nome, telefone):
    """O aviso do plano vira tarefa para uma PESSOA. Sem isto, o freio nao avisa ninguem.

    Responsavel: a dona da venda, que e quem fala com o cliente. Quando ha contrato no mundo
    (`urgente`), a advogada responsavel (RESPONSAVEL_REVISAO_EMAIL) entra tambem, porque
    cancelar contrato e decisao dela. Idempotente pelo `origem_ref`, que carrega o id da linha do historico.
    """
    if not plano.get('avisar_time'):
        return None
    hist = produto_card.historico(con, plano['cid'])
    ref = 'troca-produto:%s:%s' % (plano['cid'], hist[0]['id'] if hist else 0)
    if con.execute('select id from tarefas where origem=? and origem_ref=? limit 1',
                   ('produto-card', ref)).fetchone():
        return None

    corpo = [plano['avisar_time']]
    if plano.get('freio'):
        corpo.append('')
        corpo.append('A ESTEIRA ESTA FREADA para este card: a sequencia automatica nao vai '
                     'sair enquanto esta tarefa estiver aberta.')
    if plano.get('texto_desculpa'):
        corpo.append('')
        corpo.append('TEXTO SUGERIDO para o cliente (NAO foi enviado; precisa do aval da '
                     'advogada responsavel antes de sair):')
        corpo.append('')
        corpo.append(plano['texto_desculpa'])
    if plano.get('reiniciar'):
        corpo.append('')
        corpo.append('Depois da desculpa, a recepcao do produto novo recomeca no passo %d, '
                     'sem repetir o que ele ja ouviu, e o video vai sempre.'
                     % plano.get('reiniciar_desde', 3))
    corpo.append('')
    corpo.append('Trocado por %s. Historico completo na ficha do card.' % plano.get('quem', '?'))

    agora = _dt.datetime.utcnow().isoformat(sep=' ')
    cur = con.execute(
        "insert into tarefas (tipo, titulo, descricao, status, origem, origem_ref, "
        "prioridade, data, conversa_id, contato_nome, contato_telefone, criado_por, "
        "criado_em, atualizado_em) values (?,?,?,'aberta','produto-card',?,?,?,?,?,?,?,?,?)",
        ('ACOMPANHAMENTO',
         'TROCA DE PRODUTO: %s passou de %s para %s%s'
         % (nome or 'cliente sem nome', plano.get('de') or 'sem produto', plano['para'],
            ' · URGENTE, ha contrato' if plano.get('urgente') else ''),
         '\n'.join(corpo), ref,
         'alta' if plano.get('urgente') else 'normal',
         _dt.date.today().isoformat(), plano['cid'], nome or '', telefone or '',
         plano.get('quem', ''), agora, agora))
    tid = cur.lastrowid

    donos = [plano.get('dona_da_venda')]
    if plano.get('urgente'):
        donos.append((_os.getenv('REVISORA_EMAIL') or _os.getenv('RESPONSAVEL_REVISAO_EMAIL') or '').strip().lower())
    for email in donos:
        uid, unome = _usuario_id_por_email(con, email)
        if uid and not con.execute('select id from tarefa_responsaveis where tarefa_id=? '
                                   'and usuario_id=?', (tid, uid)).fetchone():
            con.execute('insert into tarefa_responsaveis (tarefa_id, usuario_id, nome, '
                        'importante, urgente) values (?,?,?,?,?)',
                        (tid, uid, unome, 1, 1 if plano.get('urgente') else 0))
    con.commit()
    return tid


@router.get('/conversas/{conversa_id}/produto')
async def ver_produto(conversa_id: int, user: Usuario = Depends(usuario_atual)):
    """O produto do card, quem trocou, quando, e se ESTE usuario pode trocar.

    A tela usa `pode_trocar` para mostrar o campo como editavel ou como rotulo, e
    `de_produto` para mandar de volta no PATCH e o servidor pegar troca simultanea.
    """
    def trabalho():
        con = _con_produto()
        try:
            if not con.execute('select 1 from wa_conversas where id=?',
                               (conversa_id,)).fetchone():
                return None
            atual = produto_card.produto_do_fechamento(con, conversa_id)
            return {
                'produto': atual,
                'de_produto': atual,
                'opcoes': [{'valor': produto_card.TRABALHISTA,
                            'rotulo': 'Trabalhista (reclamante)'},
                           {'valor': produto_card.EMPRESARIAL, 'rotulo': 'Empresarial'}],
                'pode_trocar': produto_card.pode_trocar_produto(user),
                'valor_do_produto': (produto_card.valor_do_produto(atual) if atual else None),
                'historico': produto_card.historico(con, conversa_id),
            }
        finally:
            con.close()

    dados = await run_in_threadpool(trabalho)
    if dados is None:
        raise HTTPException(404, 'Conversa nao encontrada')
    return dados


@router.patch('/conversas/{conversa_id}/produto')
async def trocar_produto_do_card(conversa_id: int, req: ProdutoReq,
                                 user: Usuario = Depends(usuario_atual)):
    """Troca o produto do card. 403 sem permissao, 409 se outra pessoa trocou no meio.

    O que sai daqui: o card gravado, a linha no historico e, quando o cliente ja recebeu algo
    do produto antigo, uma TAREFA para uma pessoa resolver. Mensagem para o cliente, nenhuma.
    """
    def trabalho():
        con = _con_produto()
        try:
            r = con.execute("select coalesce(contato_nome,''), coalesce(contato_telefone,'') "
                            "from wa_conversas where id=?", (conversa_id,)).fetchone()
            if not r:
                return {'erro': 404, 'msg': 'Conversa nao encontrada'}
            nome, telefone = r[0], r[1]
            try:
                plano = produto_card.trocar_produto(con, conversa_id, req.produto,
                                                    req.de_produto, user, origem='ui',
                                                    motivo=req.motivo)
            except produto_card.SemPermissao as e:
                return {'erro': 403, 'msg': str(e)}
            except produto_card.ConflitoDeVersao as e:
                return {'erro': 409, 'msg': str(e)}
            except produto_card.ProdutoInvalido as e:
                return {'erro': 422, 'msg': str(e)}
            tarefa_id = _abrir_tarefa_da_troca(con, plano, nome, telefone)
            return {'ok': True, 'plano': plano, 'tarefa_id': tarefa_id,
                    'historico': produto_card.historico(con, conversa_id)}
        finally:
            con.close()

    out = await run_in_threadpool(trabalho)
    if out.get('erro'):
        raise HTTPException(out['erro'], out['msg'])
    return out


class NovoClienteReq(BaseModel):
    numero_id: int
    contato_nome: str
    contato_telefone: str = ''
    pipeline_id: Optional[int] = None
    etapa: Optional[str] = None
    tag: str = ''
    origem: str = ''
    responsavel: str = ''
    email: str = ''
    handle: str = ''
    valor: float = 0.0
    notas: str = ''


@router.post('/conversas')
async def criar_cliente(req: NovoClienteReq, user: Usuario = Depends(usuario_atual),
                        db: AsyncSession = Depends(get_db)):
    """Cadastro manual de cliente/lead (aba Cadastro)."""
    tel = ''.join(ch for ch in req.contato_telefone if ch.isdigit())
    # 10 ou 11 digitos = DDD + numero (sem o pais) -> completa com 55.
    # 12/13 digitos ja vem com o 55. Assim "55 9xxxx" (DDD 55) tambem funciona.
    if len(tel) in (10, 11):
        tel = '55' + tel
    # DEDUP (NAO permite dois contatos com o mesmo numero): casa pelos ULTIMOS 8 digitos,
    # entao variacoes (com/sem 55, com/sem o 9) caem no mesmo contato. Reusa em vez de duplicar.
    existe = await _conversa_por_numero(db, req.numero_id, tel)
    if existe:
        if req.contato_nome.strip():
            existe.contato_nome = req.contato_nome.strip()
        for campo in ('tag', 'origem', 'responsavel', 'email', 'handle', 'notas'):
            val = (getattr(req, campo, '') or '')
            if val:
                setattr(existe, campo, val)
        if req.pipeline_id:
            if req.pipeline_id != existe.pipeline_id:
                if not _pode_trocar_board(user):
                    _registrar_move(db, existe, req.pipeline_id, req.etapa or existe.etapa,
                                    user.email, 'ui:cadastro', bloqueado=True)
                    await db.commit()
                    raise HTTPException(403, 'Este contato ja existe em outro board. '
                                             'Voce nao tem permissao para troca-lo de board.')
                _registrar_move(db, existe, req.pipeline_id, req.etapa or existe.etapa,
                                user.email, 'ui:cadastro')
                existe.board_fixado = 1
                existe.board_fixado_por = user.email
                existe.board_fixado_em = datetime.utcnow().isoformat(sep=' ')
            existe.pipeline_id = req.pipeline_id
        if req.etapa:
            existe.etapa = req.etapa
        existe.atualizado_em = datetime.utcnow()
        await db.commit()
        await db.refresh(existe)
        return {'ok': True, 'id': existe.id, 'conversa': existe.to_dict(), 'reusado': True}
    pid = req.pipeline_id
    etapa = req.etapa
    if not pid:
        pid, etapa = await _pipeline_padrao(db)
    if not etapa:
        st = (await db.execute(select(Stage).where(Stage.pipeline_id == pid).order_by(Stage.ordem).limit(1))).scalar_one_or_none()
        etapa = st.nome if st else 'Novo'
    conv = Conversa(numero_id=req.numero_id, contato_telefone=tel, contato_nome=req.contato_nome.strip(),
                    pipeline_id=pid, etapa=etapa, ia_ativa=False,
                    tag=req.tag, origem=req.origem, responsavel=req.responsavel,
                    email=req.email, handle=req.handle, valor=req.valor, notas=req.notas)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return {'ok': True, 'id': conv.id, 'conversa': conv.to_dict()}


# ============================================================
# PIPELINES + COLUNAS (CRM)
# ============================================================

@router.get('/pipelines')
async def listar_pipelines(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    pls = (await db.execute(select(Pipeline).order_by(Pipeline.ordem))).scalars().all()
    stages = (await db.execute(select(Stage).order_by(Stage.pipeline_id, Stage.ordem))).scalars().all()
    out = []
    for p in pls:
        # privado (com dono): so o dono ve. Sem dono: compartilhado (todos veem).
        if p.dono_email and p.dono_email != user.email:
            continue
        cols = [s.to_dict() for s in stages if s.pipeline_id == p.id]
        out.append({**p.to_dict(), 'stages': cols})
    return out


class PipelineReq(BaseModel):
    nome: str
    pessoal: bool = False   # True = privado (so o dono ve)


@router.post('/pipelines')
async def criar_pipeline(req: PipelineReq, user: Usuario = Depends(usuario_atual),
                         db: AsyncSession = Depends(get_db)):
    n = (await db.execute(select(Pipeline))).scalars().all()
    p = Pipeline(nome=req.nome.strip(), ordem=len(n), dono_email=(user.email if req.pessoal else ''))
    db.add(p)
    await db.flush()
    # ja cria uma coluna inicial pra nao ficar vazio
    db.add(Stage(pipeline_id=p.id, nome='Novo', cor=CORES[0], ordem=0))
    await db.commit()
    await db.refresh(p)
    return {'ok': True, 'id': p.id}


class PipelineRenameReq(BaseModel):
    nome: str


@router.patch('/pipelines/{pipeline_id}')
async def renomear_pipeline(pipeline_id: int, req: PipelineRenameReq,
                            user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, 'Pipeline nao encontrado')
    p.nome = req.nome.strip()
    await db.commit()
    return {'ok': True, 'nome': p.nome}


@router.delete('/pipelines/{pipeline_id}')
async def excluir_pipeline(pipeline_id: int, user: Usuario = Depends(requer_admin),
                           db: AsyncSession = Depends(get_db)):
    usados = (await db.execute(select(Conversa.id).where(Conversa.pipeline_id == pipeline_id).limit(1))).first()
    if usados:
        raise HTTPException(400, 'Pipeline tem conversas. Mova-as antes de excluir.')
    for s in (await db.execute(select(Stage).where(Stage.pipeline_id == pipeline_id))).scalars().all():
        await db.delete(s)
    p = (await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))).scalar_one_or_none()
    if p:
        await db.delete(p)
    await db.commit()
    return {'ok': True}


class StageReq(BaseModel):
    nome: str
    cor: Optional[str] = None


@router.post('/pipelines/{pipeline_id}/stages')
async def criar_stage(pipeline_id: int, req: StageReq, user: Usuario = Depends(usuario_atual),
                      db: AsyncSession = Depends(get_db)):
    atuais = (await db.execute(select(Stage).where(Stage.pipeline_id == pipeline_id))).scalars().all()
    s = Stage(pipeline_id=pipeline_id, nome=req.nome.strip(),
              cor=req.cor or CORES[len(atuais) % len(CORES)], ordem=len(atuais))
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {'ok': True, 'id': s.id, 'nome': s.nome}


class StageRenameReq(BaseModel):
    nome: str


@router.patch('/stages/{stage_id}')
async def renomear_stage(stage_id: int, req: StageRenameReq, user: Usuario = Depends(usuario_atual),
                         db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(Stage).where(Stage.id == stage_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, 'Coluna nao encontrada')
    antigo = s.nome
    s.nome = req.nome.strip()
    # atualiza as conversas que estavam nessa coluna
    convs = (await db.execute(select(Conversa).where(
        Conversa.pipeline_id == s.pipeline_id, Conversa.etapa == antigo))).scalars().all()
    for c in convs:
        c.etapa = s.nome
    await db.commit()
    return {'ok': True, 'nome': s.nome}


@router.delete('/stages/{stage_id}')
async def excluir_stage(stage_id: int, user: Usuario = Depends(usuario_atual),
                        db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(Stage).where(Stage.id == stage_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, 'Coluna nao encontrada')
    usados = (await db.execute(select(Conversa.id).where(
        Conversa.pipeline_id == s.pipeline_id, Conversa.etapa == s.nome).limit(1))).first()
    if usados:
        raise HTTPException(400, 'Coluna tem conversas. Mova-as antes de excluir.')
    await db.delete(s)
    await db.commit()
    return {'ok': True}


class AtribuirReq(BaseModel):
    atendente_email: str
    assumir_ia: bool = True   # ao atribuir, humano assume (IA para)


@router.post('/conversas/{conversa_id}/atribuir')
async def atribuir(conversa_id: int, req: AtribuirReq,
                   user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    conv.atendente_email = req.atendente_email
    if req.assumir_ia:
        conv.ia_ativa = False
    await db.commit()
    return {'ok': True}


class AceitarReq(BaseModel):
    aceita: bool = True


@router.post('/conversas/{conversa_id}/aceitar')
async def aceitar_conversa(conversa_id: int, req: AceitarReq,
                           user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Aceita a conversa (triagem): sai da fila 'Nao lidas'."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    conv.aceita = req.aceita
    if req.aceita:
        conv.recusada = False
    await db.commit()
    return {'ok': True, 'aceita': bool(conv.aceita)}


@router.post('/conversas/{conversa_id}/recusar')
async def recusar_conversa(conversa_id: int, req: AceitarReq,
                           user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    """Recusa a conversa (triagem): sai da fila 'Nao lidas', marcada como recusada."""
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    conv.recusada = req.aceita   # reusa o mesmo campo do body (True = recusar)
    if req.aceita:
        conv.aceita = False
    await db.commit()
    return {'ok': True, 'recusada': bool(conv.recusada)}


@router.delete('/conversas/{conversa_id}')
async def excluir_conversa(conversa_id: int, user: Usuario = Depends(requer_admin),
                           db: AsyncSession = Depends(get_db)):
    """Exclui a conversa + mensagens + arquivos de midia (destrutivo, so admin)."""
    import os
    conv = (await db.execute(select(Conversa).where(Conversa.id == conversa_id))).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, 'Conversa nao encontrada')
    msgs = (await db.execute(select(Mensagem).where(Mensagem.conversa_id == conversa_id))).scalars().all()
    for m in msgs:
        if m.midia_url and MIDIA_MARCA + '/' in m.midia_url:
            try:
                os.remove(f"{MEDIA_DIR}/{m.midia_url.split('/media/')[-1]}")
            except Exception:
                pass
        await db.delete(m)
    await db.delete(conv)
    await db.commit()
    return {'ok': True}


# ============================================================
# NUMEROS (multi-numero) — config
# ============================================================

@router.get('/numeros')
async def listar_numeros(user: Usuario = Depends(usuario_atual), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(NumeroWhatsapp))).scalars().all()
    return [n.to_dict() for n in rows]


class NumeroReq(BaseModel):
    nome: str
    telefone: str = ''
    zapi_instance: str = ''
    zapi_token: str = ''
    zapi_client_token: str = ''
    atende_ia: bool = True
    agente: str = 'reclamante'   # 'reclamante' (SDR trabalhista) ou 'empresas' (recepcao B2B)
    so_reclamante: bool = False


@router.post('/numeros')
async def criar_numero(req: NumeroReq, user: Usuario = Depends(requer_admin),
                       db: AsyncSession = Depends(get_db)):
    n = NumeroWhatsapp(**req.model_dump())
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return {'ok': True, 'id': n.id, 'webhook': f'/api/atendimento/webhook/{n.id}'}
