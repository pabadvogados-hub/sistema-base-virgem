# -*- coding: utf-8 -*-
"""Aba ENTREVISTA da ficha do cliente (Advogada, 02/09/2026).

O modal "Ficha do cliente" do chat ao vivo tem 7 campos genericos e um campo de
Anotacoes. A entrevista do reclamante nao cabe ali: sao ~60 perguntas. Esta
aba poe cada pergunta em um campo de verdade, para o juridico depois EXTRAIR
resposta por resposta em vez de garimpar texto solto.

DESENHO
- Tabela propria `ficha_entrevista`, chave/valor: (conversa_id, campo, valor).
  Acrescentar pergunta depois nao exige migracao nem repatch do frontend.
- O ESQUEMA das perguntas mora AQUI. O frontend so desenha o que este endpoint
  devolve, entao mudar pergunta e editar este arquivo.
- Router e tabela NOVOS. Nao encosta em `atendimento.py`, no modelo `Conversa`
  nem em nenhum fluxo existente.

TRANSCRICAO
O time pode anexar aqui o arquivo da transcricao. Ele vai para a mesma pasta do
lead no Drive que o `subir-drive` ja usa (`drive_leads`), com o nome do cliente
no arquivo, que e por onde o `transcricao_drive.py` procura.
Atencao de quem for mexer: ligacao feita PELO RAMAL ja e gravada e transcrita
sozinha pelo `transcrever_ligacoes.py` (cron 1/min) e anexada na conversa. Este
anexo e o plano B, para ligacao feita fora do ramal.
"""
import io
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import usuario_atual
from ..database import get_db, Usuario

router = APIRouter(prefix='/api/ficha-entrevista', tags=['ficha-entrevista'])

MAX_ANEXO = 20 * 1024 * 1024          # 20 MB
EXT_OK = ('.txt', '.md', '.docx', '.doc', '.pdf', '.rtf', '.odt')


def _c(cid, label, tipo='texto', obrig=False, opcoes=None):
    d = {'id': cid, 'label': label, 'tipo': tipo}
    if obrig:
        d['obrigatorio'] = True
    if opcoes:
        d['opcoes'] = opcoes
    return d


# --- O esquema. Cada bloco vira um titulo; cada campo, um input. -------------
# tipo: 'texto' (uma linha) | 'area' (varias linhas) | 'opcao' (select)
BLOCOS = [
    # Bloco do CLIENTE (Advogada, 04/09/2026): o endereco preenchido AQUI vale como
    # comprovante para a contratacao avancar (o vigia le o campo
    # 'endereco_cliente'; o arquivo do comprovante e cobrado depois, na
    # organizacao de documentos). Fluxo dela: fechou na ligacao -> pede so a
    # CNH -> preenche o endereco na entrevista -> contrato sai.
    {'id': 'cliente', 'titulo': 'O cliente',
     'ajuda': 'Endereco completo aqui libera o contrato sem esperar o arquivo do '
              'comprovante. Anote como ele falar: rua, numero, bairro, cidade e CEP.',
     'campos': [
         _c('endereco_cliente', 'Endereco completo do cliente (rua, numero, bairro, cidade e CEP)',
            'area', True),
     ]},
    {'id': 'empresa', 'titulo': 'A empresa: a porteira da convencao coletiva',
     'ajuda': 'A convencao sai da base territorial da SEDE. Jornada-base errada contamina '
              'o calculo inteiro. Confirme pelo CNPJ na hora, nao pela memoria dele.',
     'campos': [
         _c('razao_social', 'Razao social completa', obrig=True),
         _c('cnpj', 'CNPJ', obrig=True),
         _c('como_chama', 'Como ele chama a firma'),
         _c('endereco_sede', 'Endereco da SEDE, de onde o caminhao sai', 'area', True),
         _c('grupo_economico', 'Outras empresas do mesmo dono ou do mesmo galpao'),
         _c('porte_frota', 'Quantos caminhoes e quantos motoristas tem a firma'),
         _c('agenciador', 'Trabalha para agenciador ou embarcador? Descontam por isso?'),
         _c('tipo_carga_geral', 'Transporta o que, no geral'),
     ]},
    {'id': 'contrato', 'titulo': 'O contrato', 'campos': [
        _c('registrado_desde', 'E registrado? Desde quando', obrig=True),
        _c('antes_registro', 'Trabalhou antes de assinarem a carteira'),
        _c('contrato_ativo', 'O contrato ainda esta ativo', 'opcao', True,
           ['', 'Sim, ainda esta la', 'Ja saiu']),
        _c('como_saiu', 'Se saiu, como', 'opcao', True,
           ['', 'Dispensa', 'Pediu a conta', 'Acordo', 'Justa causa']),
        _c('data_saida', 'Data da saida'),
        _c('motivo_saida_literal', 'Se pediu a conta, por que? Nao aguentava mais o que (ANOTAR LITERAL)', 'area'),
        _c('pagamento_rescisao', 'Em que dia pagaram a rescisao? Quantos dias depois', obrig=True),
        _c('funcao_carteira', 'Funcao anotada na carteira', obrig=True),
        _c('funcao_real', 'O que ele fazia de verdade, no dia a dia', 'area', True),
        _c('baixa_carteira', 'Deram baixa na carteira fisica'),
    ]},
    {'id': 'remuneracao', 'titulo': 'Remuneracao (todo valor leva R$)', 'campos': [
        _c('salario_carteira', 'Registrado na carteira (R$)', obrig=True),
        _c('salario_real', 'Quanto tira por mes de verdade, na media (R$)', obrig=True),
        _c('forma_ganho', 'Ganha fixo, comissao, por viagem ou por quilometragem'),
        _c('por_fora_valor', 'Tem parte que cai por fora? Quanto (R$)', obrig=True),
        _c('por_fora_forma', 'Como cai o por fora', 'opcao', True,
           ['', 'PIX', 'Cheque', 'Envelope', 'Dinheiro', 'Outro']),
        _c('recibo_produtividade', 'Tem RECIBO desse pagamento (por tonelada, viagem, entrega)'),
        _c('diaria', 'Diaria (R$) e o que ela cobre'),
        _c('pernoite_valor', 'Pernoite separado da diaria (R$)', obrig=True),
        _c('vale_refeicao', 'Vale-refeicao (R$)'),
        _c('premio_bonus', 'Premio, bonus, ajuda de custo (R$)'),
        _c('base_13_ferias', '13o e ferias caem sobre a carteira ou sobre o real'),
        _c('descontos', 'Descontam algo? Combustivel, pedagio, avaria, multa, pousada (R$)', 'area', True),
    ]},
    {'id': 'jornada', 'titulo': 'Jornada', 'campos': [
        _c('horario_comum', 'Que horas comeca e termina num dia comum', obrig=True),
        _c('semana_descrita', 'Descreva uma semana sua, do comeco ao fim (deixe ele falar)', 'area'),
        _c('almoco', 'Consegue parar 1 hora pra almocar', 'opcao', False,
           ['', 'Sim', 'As vezes', 'Nao']),
        _c('interjornada', 'Entre parar a noite e sair de manha, da 11 horas? Quantas', obrig=True),
        _c('pausa_30min', 'Faz a pausa de 30 min a cada 5h30 rodando'),
        _c('domingo_feriado', 'Trabalha domingo e feriado? Recebe em dobro ou folga'),
        _c('setimo_dia', 'Ja trabalhou mais de 6 dias seguidos sem folga'),
        _c('noturno', 'Roda entre 22h e 5h? Quantas noites por semana'),
        _c('hora_extra_paga', 'Recebe hora extra? O holerite bate com o que faz'),
        _c('art_62_externo', 'A empresa alega que ele e EXTERNO, sem controle de horario'),
    ]},
    {'id': 'espera', 'titulo': 'Tempo de espera', 'campos': [
        _c('tempo_espera', 'Quanto tempo espera pra carregar e pra descarregar', obrig=True),
        _c('pode_sair', 'Nesse tempo pode ir embora ou tem que ficar no patio'),
        _c('dias_parado', 'Ja ficou dias parado esperando carga? Quantos'),
        _c('recebe_espera', 'Recebe alguma coisa por esse tempo (R$)'),
        _c('episodio_datado', 'Episodio com data (ex.: cheguei meia-noite, descarregaram no dia seguinte)', 'area'),
    ]},
    {'id': 'pernoite', 'titulo': 'Onde ele dorme', 'campos': [
        _c('onde_dorme', 'Onde dorme', 'opcao', True,
           ['', 'Cabine leito', 'Cabine simples', 'BAU', 'Carro', 'Pousada', 'Outro']),
        _c('estrutura_cabine', 'O que tem dentro (cama, colchao, coberta)'),
        _c('posto_indicado', 'A empresa indica o posto? Qual a estrutura'),
        _c('banheiro_comida', 'Tem banheiro pra banho, comida e seguranca no local'),
        _c('paga_pernoite', 'A empresa paga o pernoite mesmo dormindo no caminhao (R$)'),
        _c('alojamento_feminino', 'SE MULHER: como era o alojamento? Tinha banheiro feminino separado', 'area'),
    ]},
    {'id': 'controle', 'titulo': 'Controle de jornada: define o arquetipo', 'campos': [
        _c('como_marca', 'Como marca a jornada', 'opcao', True,
           ['', 'Diario de bordo', 'Papeleta', 'Aplicativo', 'Rastreador', 'Nada']),
        _c('sistema_rastreio', 'Nome do sistema de rastreio', obrig=True),
        _c('assina_ponto', 'Assina folha de ponto? Todo dia ou todo mes'),
        _c('folha_bate', 'A folha bate com o que ele faz de verdade', obrig=True),
        _c('mandaram_escrever', 'Ja mandaram escrever horario que nao era o real (ANOTAR LITERAL)', 'area'),
        _c('arquetipo', 'ARQUETIPO', 'opcao', True,
           ['', 'A: tem controle, mas nao bate. EXIGE TESTEMUNHA',
            'B: nao tem controle idoneo, so rastreador']),
    ]},
    {'id': 'provas', 'titulo': 'Provas e testemunhas',
     'ajuda': 'ANOTE AS NEGATIVAS. Se ele disser que NAO tem ou que NUNCA aconteceu, escreva. '
              'E o que impede a cobranca automatica de pedir o que nao existe.',
     'campos': [
         _c('testemunha_1', 'Testemunha 1: nome, telefone, e se ja saiu da firma', 'area', True),
         _c('testemunha_2', 'Testemunha 2: nome, telefone, e se ja saiu da firma', 'area'),
         _c('whatsapp_empresa', 'Tem conversa de WhatsApp com logistica, RH ou gerente'),
         _c('fotos', 'Tem foto ou video da cabine, do patio, de como dormia'),
         _c('documentos', 'Tem holerite, TRCT, extrato de FGTS, contrato, ordem de servico'),
         _c('extrato_bancario', 'Tem extrato bancario do periodo todo'),
         _c('recibos_por_fora', 'Tem os recibos do pagamento por fora? Quantos'),
         _c('negativas', 'NEGATIVAS expressas (nunca foi assaltado, nao tem holerite, etc.)', 'area', True),
     ]},
    {'id': 'risco', 'titulo': 'Situacao atual, risco e advogado anterior', 'campos': [
        _c('pretende_sair', 'Pretende sair? Ja tem outra proposta'),
        _c('medo_retaliacao', 'Tem medo de retaliacao? Do que exatamente'),
        _c('ciencia_contrato_vigor', 'SE O CONTRATO ESTA EM VIGOR: ciencia registrada por escrito em', 'texto'),
        _c('advogado_anterior', 'Ja teve advogado nesse caso', obrig=True),
        _c('acao_anterior', 'Chegou a entrar com acao? Numero'),
    ]},
    {'id': 'valor_causa', 'titulo': 'O que aumenta o valor da causa',
     'ajuda': 'Pode vir num segundo contato, em ate 48h, desde que o resto esteja fechado.',
     'campos': [
         _c('carga_descarga', 'Carrega e descarrega sozinho? Peso das pecas (kg)'),
         _c('epi', 'Recebeu EPI? Roupa termica, luva, bota'),
         _c('paga_ajudante', 'Paga ajudante do proprio bolso (R$)'),
         _c('camera_cabine', 'Tem CAMERA dentro da cabine? Aponta pra onde'),
         _c('km_pegar_caminhao', 'Quanto anda so pra pegar o caminhao (km / min)'),
         _c('produto_perigoso', 'Que carga puxa? E produto perigoso'),
         _c('frigorifico', 'Bau ou carreta frigorifica? Entra na camara fria'),
         _c('assalto', 'Ja foi assaltado ou sequestrado? Como foi', 'area'),
         _c('empresa_suporte', 'A empresa deu suporte? (ANOTAR A RESPOSTA LITERAL DELA)', 'area'),
         _c('bloqueio_gr', 'Ficou bloqueado no gerenciamento de risco? Quanto tempo'),
         _c('acidente_cat', 'Ja se acidentou? Teve CAT? Ficou afastado'),
         _c('tempo_fora_casa', 'Quanto tempo fica sem ir em casa'),
         _c('dano_existencial', 'Perdeu aniversario, formatura, casamento, velorio (ANOTAR LITERAL)', 'area'),
         _c('relacao_casa', 'Como esta a relacao em casa por causa do tempo fora', 'area'),
     ]},
]

# Todo bloco ganha um campo de observacao proprio, no fim.
for _b in BLOCOS:
    _b['campos'].append(_c(f"obs_{_b['id']}", 'Observacao', 'area'))

CAMPOS_VALIDOS = {c['id'] for b in BLOCOS for c in b['campos']}
OBRIGATORIOS = [c['id'] for b in BLOCOS for c in b['campos'] if c.get('obrigatorio')]

DDL = """
create table if not exists ficha_entrevista (
  id integer primary key autoincrement,
  conversa_id integer not null,
  campo varchar(60) not null,
  valor text,
  atualizado_em datetime,
  atualizado_por varchar(200)
);
create unique index if not exists ux_ficha_entrevista
  on ficha_entrevista (conversa_id, campo);
"""


async def _garantir_tabela(db: AsyncSession):
    for comando in DDL.strip().split(';'):
        if comando.strip():
            await db.execute(text(comando))


class RespostasReq(BaseModel):
    respostas: dict


@router.get('/campos')
async def campos(user: Usuario = Depends(usuario_atual)):
    """O esquema das perguntas. O frontend desenha a partir daqui."""
    return {'blocos': BLOCOS, 'obrigatorios': OBRIGATORIOS}


@router.get('/{conversa_id}')
async def ler(conversa_id: int, user: Usuario = Depends(usuario_atual),
              db: AsyncSession = Depends(get_db)):
    await _garantir_tabela(db)
    linhas = (await db.execute(
        text('select campo, valor from ficha_entrevista where conversa_id=:c'),
        {'c': conversa_id})).fetchall()
    respostas = {campo: valor for campo, valor in linhas}
    faltando = [c for c in OBRIGATORIOS if not (respostas.get(c) or '').strip()]
    return {'ok': True, 'conversa_id': conversa_id, 'respostas': respostas,
            'faltando': faltando, 'total_obrigatorios': len(OBRIGATORIOS)}


@router.put('/{conversa_id}')
async def gravar(conversa_id: int, req: RespostasReq,
                 user: Usuario = Depends(usuario_atual),
                 db: AsyncSession = Depends(get_db)):
    """Grava so os campos do esquema. Campo desconhecido e ignorado de proposito.

    NAO toca em wa_conversas: `atualizado_em` de la e lido pela fila da vendedora
    e pelos relatorios como 'ultima atividade do lead', e preencher ficha nao e
    atividade do lead.
    """
    await _garantir_tabela(db)
    agora = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    gravados = 0
    for campo, valor in (req.respostas or {}).items():
        if campo not in CAMPOS_VALIDOS:
            continue
        await db.execute(text(
            'insert into ficha_entrevista (conversa_id, campo, valor, atualizado_em, atualizado_por) '
            'values (:c, :k, :v, :t, :u) '
            'on conflict(conversa_id, campo) do update set '
            'valor=excluded.valor, atualizado_em=excluded.atualizado_em, '
            'atualizado_por=excluded.atualizado_por'),
            {'c': conversa_id, 'k': campo, 'v': (valor or ''), 't': agora, 'u': user.email})
        gravados += 1
    await db.commit()
    linhas = (await db.execute(
        text('select campo, valor from ficha_entrevista where conversa_id=:c'),
        {'c': conversa_id})).fetchall()
    respostas = {c: v for c, v in linhas}
    faltando = [c for c in OBRIGATORIOS if not (respostas.get(c) or '').strip()]
    return {'ok': True, 'gravados': gravados, 'faltando': faltando}


@router.post('/{conversa_id}/transcricao')
async def anexar_transcricao(conversa_id: int, arquivo: UploadFile = File(...),
                             user: Usuario = Depends(usuario_atual),
                             db: AsyncSession = Depends(get_db)):
    """Sobe o arquivo da transcricao para a pasta do lead no Google Drive.

    Nome do arquivo leva o NOME DO CLIENTE, porque e por ele que o
    `transcricao_drive.py` procura (busca por token do nome, nao por substring).
    """
    nome_arq = (arquivo.filename or 'transcricao.txt')
    if not nome_arq.lower().endswith(EXT_OK):
        raise HTTPException(400, f'Formato nao aceito. Use: {", ".join(EXT_OK)}')
    dados = await arquivo.read()
    if not dados:
        raise HTTPException(400, 'Arquivo vazio')
    if len(dados) > MAX_ANEXO:
        raise HTTPException(400, f'Arquivo maior que {MAX_ANEXO // (1024*1024)} MB')

    linha = (await db.execute(text(
        'select contato_nome, contato_telefone, drive_pasta_id from wa_conversas where id=:c'),
        {'c': conversa_id})).fetchone()
    if not linha:
        raise HTTPException(404, 'Conversa nao encontrada')
    nome_lead, telefone, pasta_id = linha[0] or '', linha[1] or '', linha[2] or ''
    if not nome_lead.strip():
        raise HTTPException(400, 'A conversa nao tem NOME do cliente. Preencha o nome na '
                                 'aba Ficha antes de anexar: a esteira procura a transcricao '
                                 'pelo nome do arquivo.')

    ext = '.' + nome_arq.rsplit('.', 1)[-1].lower()
    limpo = re.sub(r'[^\w\s-]', '', nome_lead).strip()[:60] or 'cliente'
    destino = f"Transcricao {limpo} {datetime.now().strftime('%Y-%m-%d')}{ext}"

    import sys
    sys.path.insert(0, '/opt/escritorio/INTEGRACOES')
    try:
        from google_integration import autenticar_google
        from googleapiclient.http import MediaIoBaseUpload
        from ..whatsapp import drive_leads
        drive, _ = autenticar_google()
        if pasta_id:
            pasta = {'id': pasta_id, 'url': ''}
        else:
            pasta = drive_leads.resolver_pasta_lead(drive, nome_lead, telefone)
        midia = MediaIoBaseUpload(io.BytesIO(dados), mimetype='application/octet-stream',
                                  resumable=False)
        criado = drive.files().create(
            body={'name': destino, 'parents': [pasta['id']]},
            media_body=midia, fields='id, webViewLink',
            supportsAllDrives=True).execute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Falha ao subir para o Drive: {e}')

    link = criado.get('webViewLink', '')
    await _garantir_tabela(db)
    agora = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    await db.execute(text(
        'insert into ficha_entrevista (conversa_id, campo, valor, atualizado_em, atualizado_por) '
        'values (:c, :k, :v, :t, :u) on conflict(conversa_id, campo) do update set '
        'valor=excluded.valor, atualizado_em=excluded.atualizado_em, '
        'atualizado_por=excluded.atualizado_por'),
        {'c': conversa_id, 'k': '_transcricao_link', 'v': link, 't': agora, 'u': user.email})
    if not pasta_id and pasta.get('id'):
        await db.execute(text('update wa_conversas set drive_pasta_id=:p where id=:c'),
                         {'p': pasta['id'], 'c': conversa_id})
    await db.commit()
    return {'ok': True, 'arquivo': destino, 'link': link}
