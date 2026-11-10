# -*- coding: utf-8 -*-
"""O produto de um card: TRABALHISTA (reclamante, linha fixa) ou EMPRESARIAL (linha do Dr. Titular).

POR QUE ESTE MODULO EXISTE SEPARADO
1. `produto_do_fechamento` e a fonte unica: painel, comercial e esteiras perguntam aqui,
   nunca leem a coluna direto.
2. a regra fica separada do efeito: `trocar_produto` DECIDE e devolve um plano (aviso ao time,
   freio da esteira). Quem manda mensagem e a esteira/uma pessoa, com o plano na mao.
3. produto decide meta e comissao, entao tem endpoint proprio com permissao, validacao,
   auditoria e controle de versao (PATCH /conversas/{id}/produto).
4. a classificacao por FRASE e por NOME DE CAMPANHA mora aqui, para o painel de midia e o
   card nunca discordarem.
"""
import os
import sqlite3
import unicodedata
from datetime import datetime, timezone

TRABALHISTA = 'trabalhista'
EMPRESARIAL = 'empresarial'
OUTROS = 'outros'                       # so para o lado do Meta; nunca entra na coluna
PRODUTOS = (TRABALHISTA, EMPRESARIAL)

# Valor padrao do card por produto (0 = definido no contrato). Ajustavel por .env.
VALOR_TRABALHISTA = float(os.getenv('PRODUTO_VALOR_TRABALHISTA', '0') or 0)
VALOR_EMPRESARIAL = float(os.getenv('PRODUTO_VALOR_EMPRESARIAL', '0') or 0)

# Quem pode trocar o produto alem dos administradores (e-mails separados por virgula).
VENDEDORAS_PRODUTO = tuple(
    x.strip().lower() for x in os.getenv('PRODUTO_CARD_VENDEDORAS', '').split(',') if x.strip())

# IDs fixos dos quadros (ver whatsapp/funis.py)
QUADROS_TRABALHISTA = (10, 11, 18, 20, 31)
QUADROS_EMPRESARIAL = (30,)
QUADROS = QUADROS_TRABALHISTA + QUADROS_EMPRESARIAL
QUADRO_ENTRADA = 10                     # caixa de entrada: nao decide produto sozinho

ROTULO = {TRABALHISTA: 'TRABALHISTA (RECLAMANTE)', EMPRESARIAL: 'EMPRESARIAL', OUTROS: 'OUTROS'}

# A frase do anuncio identifica o produto do lado do CRM; o nome da campanha, do lado do Meta.
FRASES = {
    EMPRESARIAL: ('sou empresa', 'para minha empresa', 'assessoria empresarial', 'defesa da empresa',
                  'recebi uma reclamacao trabalhista', 'fui notificado', 'citacao'),
    TRABALHISTA: ('quero saber meus direitos', 'trabalho em fast food', 'trabalhei em fast food',
                  'trabalho em supermercado', 'trabalhei em supermercado', 'acidente de trabalho',
                  'rescisao indireta', 'palestra', 'estimativa:'),
}
NOMES = {
    EMPRESARIAL: ('empresa', 'empresarial', 'b2b', 'corporativo'),
    TRABALHISTA: ('reclamante', 'trabalhista', 'fast', 'supermercado', 'acidente', 'rescisao',
                  'palestra', 'geral'),
}

SERVICO = {EMPRESARIAL: 'a assessoria juridica da sua empresa',
           TRABALHISTA: 'a acao dos seus direitos trabalhistas'}

MARCAS_KIT = ('boas-vindas', 'boas vindas', 'seja bem-vindo', 'seja bem vindo')
MARCAS_CONTRATO = ('zapsign', 'contrato')
REF_CONFIRMA = 'confirma-produto:%s'


class ProdutoInvalido(ValueError):
    """Valor fora de PRODUTOS."""


class SemPermissao(PermissionError):
    """Quem nao e admin nem esta em PRODUTO_CARD_VENDEDORAS tentou trocar o produto."""


class ConflitoDeVersao(RuntimeError):
    """O produto no banco mudou depois de a tela ler: outra pessoa trocou no meio."""


# --------------------------------------------------------------------------- ajudantes

def _sa(s):
    """Minusculas e sem acento."""
    return ''.join(c for c in unicodedata.normalize('NFD', (s or '').lower())
                   if unicodedata.category(c) != 'Mn')


def _agora():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _tem_coluna(con, tabela, coluna):
    return any(c[1] == coluna for c in con.execute('pragma table_info(%s)' % tabela))


def tem_coluna_produto(con):
    try:
        return _tem_coluna(con, 'wa_conversas', 'produto')
    except sqlite3.Error:
        return False


def _card(con, cid):
    r = con.execute("select coalesce(produto,''), coalesce(contato_nome,''), "
                    "coalesce(atendente_email,''), coalesce(etapa,''), "
                    "coalesce(pipeline_id,0) from wa_conversas where id=?", (cid,)).fetchone()
    if not r:
        return None
    return {'produto': r[0] or None, 'nome': r[1], 'dona': r[2], 'etapa': r[3], 'quadro': r[4]}


# --------------------------------------------------------------------------- 1. esquema

def criar_esquema(con):
    """Coluna `produto`, tabela `wa_produto_hist` e gatilhos de valor valido. Idempotente.
    Os gatilhos valem tambem para escrita feita por fora da API (script, SQL na mao)."""
    if not _tem_coluna(con, 'wa_conversas', 'produto'):
        con.execute('ALTER TABLE wa_conversas ADD COLUMN produto TEXT')
    con.executescript("""
        CREATE TABLE IF NOT EXISTS wa_produto_hist (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conversa_id INTEGER NOT NULL,
          de_produto TEXT,
          para_produto TEXT NOT NULL,
          quem TEXT NOT NULL,
          origem TEXT,
          dona_da_venda TEXT,
          bloqueado INTEGER DEFAULT 0,
          motivo TEXT,
          criado_em TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_produto_hist_conversa
          ON wa_produto_hist (conversa_id, id DESC);
        CREATE INDEX IF NOT EXISTS ix_conversas_produto
          ON wa_conversas (produto);

        CREATE TRIGGER IF NOT EXISTS tg_produto_valido_ins
        BEFORE INSERT ON wa_conversas
        WHEN NEW.produto IS NOT NULL AND NEW.produto NOT IN ('trabalhista', 'empresarial')
        BEGIN
          SELECT RAISE(ABORT, 'produto invalido: so trabalhista ou empresarial');
        END;

        CREATE TRIGGER IF NOT EXISTS tg_produto_valido_upd
        BEFORE UPDATE OF produto ON wa_conversas
        WHEN NEW.produto IS NOT NULL AND NEW.produto NOT IN ('trabalhista', 'empresarial')
        BEGIN
          SELECT RAISE(ABORT, 'produto invalido: so trabalhista ou empresarial');
        END;

        CREATE TRIGGER IF NOT EXISTS tg_produto_hist_valido
        BEFORE INSERT ON wa_produto_hist
        WHEN NEW.para_produto NOT IN ('trabalhista', 'empresarial')
        BEGIN
          SELECT RAISE(ABORT, 'produto invalido no historico: so trabalhista ou empresarial');
        END;
    """)
    con.commit()


# --------------------------------------------------------------------------- 2. permissao

def pode_trocar_produto(user):
    """True para papel admin ou e-mail listado em PRODUTO_CARD_VENDEDORAS."""
    if user is None:
        return False
    if (getattr(user, 'papel', '') or '').strip().lower() == 'admin':
        return True
    return (getattr(user, 'email', '') or '').strip().lower() in VENDEDORAS_PRODUTO


# --------------------------------------------------------------------------- 3. regras puras

def produto_do_quadro(pipeline_id, etapa=''):
    """O produto que o quadro implica: Empresas -> empresarial; o resto -> trabalhista."""
    try:
        q = int(pipeline_id or 0)
    except (TypeError, ValueError):
        q = 0
    return EMPRESARIAL if q in QUADROS_EMPRESARIAL else TRABALHISTA


def valor_do_produto(produto):
    if produto == EMPRESARIAL:
        return VALOR_EMPRESARIAL
    if produto == TRABALHISTA:
        return VALOR_TRABALHISTA
    raise ProdutoInvalido('produto desconhecido: %r' % (produto,))


def produto_da_campanha(nome):
    """O produto pelo NOME da campanha do Meta. Devolve OUTROS quando nao reconhece."""
    n = _sa(nome)
    for p in (EMPRESARIAL, TRABALHISTA):
        if any(x in n for x in NOMES[p]):
            return p
    return OUTROS


def produto_da_frase(texto):
    """O produto pela FRASE com que o lead chegou. None quando nao reconhece."""
    t = _sa(texto)
    for p in (EMPRESARIAL, TRABALHISTA):
        if any(_sa(f) in t for f in FRASES[p]):
            return p
    return None


def produto_do_lead(origem, primeira_fala, agente=''):
    """De que produto e este lead: pela linha (Empresas), pela frase, e por fim pela campanha.
    None quando nada identifica."""
    if (agente or '') == 'empresas':
        return EMPRESARIAL
    p = produto_da_frase(primeira_fala)
    if p:
        return p
    if (origem or '').startswith('Campanha'):
        p = produto_da_campanha(origem)
        return None if p == OUTROS else p
    return None


def produto_no_nascimento(origem, primeira_fala, agente=''):
    """Sem sinal, nasce trabalhista (produto principal do escritorio)."""
    return produto_do_lead(origem, primeira_fala, agente) or TRABALHISTA


# --------------------------------------------------------------------------- 4. fonte unica

def _nascer(con, cid, produto, motivo):
    c = _card(con, cid)
    if not c or c['produto'] or produto not in PRODUTOS:
        return None
    try:
        con.execute('UPDATE wa_conversas SET produto=? WHERE id=?', (produto, cid))
        con.execute(
            'INSERT INTO wa_produto_hist (conversa_id, de_produto, para_produto, quem, '
            'origem, dona_da_venda, bloqueado, motivo, criado_em) VALUES (?,?,?,?,?,?,0,?,?)',
            (cid, None, produto, 'sistema', 'nascimento', c['dona'] or None, motivo, _agora()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return produto


def definir_no_nascimento(con, cid, origem, primeira_fala, agente=''):
    """O card recebe o produto da campanha/linha. NAO chuta: sem sinal, fica vazio."""
    sinal = produto_do_lead(origem, primeira_fala, agente)
    if not sinal:
        return None
    return _nascer(con, cid, sinal, 'nasceu pela campanha/frase: %s'
                   % (origem or (primeira_fala or '')[:40] or 'sem origem'))


def definir_pelo_quadro(con, cid):
    """Para quem nasceu sem sinal: o quadro onde o card foi parar diz o produto."""
    c = _card(con, cid)
    if not c or c['produto']:
        return None
    q = c['quadro']
    if q == QUADRO_ENTRADA or q not in QUADROS:
        return None
    return _nascer(con, cid, produto_do_quadro(q, c['etapa']),
                   'nasceu sem sinal; o quadro %s definiu' % q)


def produto_do_fechamento(con, cid):
    """FONTE UNICA do produto de um card."""
    c = _card(con, cid)
    return c['produto'] if c else None


def produto_para_o_painel(con, cid):
    """Produto para CONTAGEM: o campo vence; vazio responde pela posicao do card."""
    c = _card(con, cid)
    if not c:
        return None
    return c['produto'] or produto_do_quadro(c['quadro'], c['etapa'])


def kit_ja_saiu(con, cid):
    for (texto,) in con.execute(
            "select coalesce(conteudo,'') from wa_mensagens where conversa_id=? "
            "and origem<>'contato'", (cid,)):
        if any(m in _sa(texto) for m in MARCAS_KIT):
            return True
    return False


def contrato_ja_existe(con, cid):
    c = _card(con, cid)
    if c and any(m in _sa(c['etapa']) for m in MARCAS_CONTRATO):
        return True
    for (texto,) in con.execute(
            "select coalesce(conteudo,'') from wa_mensagens where conversa_id=? "
            "and origem<>'contato'", (cid,)):
        if 'zapsign' in _sa(texto):
            return True
    return False


def _trocas(con, cid, so_reais=True):
    sql = ("select count(*) from wa_produto_hist where conversa_id=? "
           "and coalesce(bloqueado,0)=0")
    if so_reais:
        sql += " and coalesce(origem,'')<>'nascimento' and coalesce(origem,'')<>'migracao'"
    return con.execute(sql, (cid,)).fetchone()[0]


# --------------------------------------------------------------------------- 5. a troca

def _aviso_ao_time(cid, nome, de_produto, para_produto, quem, kit, contrato, trocas_antes):
    partes = ['%s (card #%s) trocou de %s para %s, por %s.'
              % (nome or 'cliente sem nome', cid, de_produto or 'sem produto', para_produto, quem)]
    if contrato:
        partes.append('JA EXISTE CONTRATO deste card: confira o contrato no ZapSign e qualquer '
                      'cobranca antes de seguir. A esteira esta freada.')
    elif kit:
        partes.append('O cliente ja recebeu mensagens do produto antigo. Confira antes de mandar mais nada.')
    if trocas_antes:
        partes.append('ATENCAO: esta e a %da troca de produto deste card.' % (trocas_antes + 1))
    return ' '.join(partes)


def _registrar(con, cid, de_produto, para_produto, user, origem, dona, bloqueado=0, motivo=''):
    con.execute(
        'INSERT INTO wa_produto_hist (conversa_id, de_produto, para_produto, quem, origem, '
        'dona_da_venda, bloqueado, motivo, criado_em) VALUES (?,?,?,?,?,?,?,?,?)',
        (cid, de_produto, para_produto, (getattr(user, 'email', '') or '?'), origem, dona,
         1 if bloqueado else 0, motivo or None, _agora()))


def trocar_produto(con, cid, novo, de_produto, user, origem='ui', motivo=''):
    """Troca o produto do card e devolve o PLANO. Decide, grava e registra; nao envia nada.
    Levanta SemPermissao (403), ProdutoInvalido (422) e ConflitoDeVersao (409)."""
    if novo not in PRODUTOS:
        raise ProdutoInvalido('produto invalido: %r. Use um de %s' % (novo, list(PRODUTOS)))
    c = _card(con, cid)
    if not c:
        raise ProdutoInvalido('card %r nao existe' % (cid,))
    if not pode_trocar_produto(user):
        _registrar(con, cid, de_produto, novo, user, origem, c['dona'], bloqueado=1,
                   motivo=motivo or 'sem permissao')
        con.commit()
        raise SemPermissao('%s nao pode trocar o produto deste card'
                           % (getattr(user, 'email', '') or 'usuario'))
    atual = c['produto']
    if (atual or None) != (de_produto or None):
        raise ConflitoDeVersao(
            'o produto deste card agora e %r, e nao %r: outra pessoa trocou no meio. '
            'Recarregue o card e tente de novo.' % (atual, de_produto))
    vazio = {'cid': cid, 'de': atual, 'para': novo, 'trocou': False, 'freio': False,
             'desculpa': False, 'texto_desculpa': '', 'reiniciar': False, 'reiniciar_desde': 1,
             'urgente': False, 'avisar_time': ''}
    if atual == novo:
        return vazio
    kit = kit_ja_saiu(con, cid)
    contrato = contrato_ja_existe(con, cid)
    trocas_antes = _trocas(con, cid)
    try:
        con.execute('UPDATE wa_conversas SET produto=?, valor=? WHERE id=?',
                    (novo, valor_do_produto(novo), cid))
        _registrar(con, cid, de_produto, novo, user, origem, c['dona'], motivo=motivo)
        con.commit()
    except Exception:
        con.rollback()
        raise
    plano = dict(vazio, trocou=True, quem=(getattr(user, 'email', '') or '?'),
                 dona_da_venda=c['dona'], valor=valor_do_produto(novo),
                 freio=bool(kit or contrato), urgente=bool(contrato))
    if kit or contrato or trocas_antes:
        plano['avisar_time'] = _aviso_ao_time(cid, c['nome'], atual, novo, plano['quem'],
                                              kit, contrato, trocas_antes)
    return plano


def historico(con, cid):
    linhas = con.execute(
        'select id, de_produto, para_produto, quem, origem, dona_da_venda, '
        'coalesce(bloqueado,0), motivo, criado_em from wa_produto_hist '
        'where conversa_id=? order by id desc', (cid,)).fetchall()
    return [{'id': r[0], 'de_produto': r[1], 'para_produto': r[2], 'quem': r[3],
             'origem': r[4], 'dona_da_venda': r[5], 'bloqueado': bool(r[6]),
             'motivo': r[7], 'criado_em': r[8]} for r in linhas]


# --------------------------------------------------------------------------- 6. alarme

def _tarefa_confirma(con, cid):
    try:
        r = con.execute("select coalesce(status,'') from tarefas where origem_ref=? "
                        "order by id desc limit 1", (REF_CONFIRMA % cid,)).fetchone()
    except sqlite3.Error:
        return ''
    return r[0] if r else ''


def precisa_perguntar_produto(con, cid):
    c = _card(con, cid)
    if not c or not c['produto']:
        return False
    if c['produto'] == produto_do_quadro(c['quadro'], c['etapa']):
        return False
    if _trocas(con, cid):
        return False
    return _tarefa_confirma(con, cid) != 'concluida'


def produto_confirmado(con, cid):
    if precisa_perguntar_produto(con, cid):
        return False
    if _trocas(con, cid):
        return True
    return _tarefa_confirma(con, cid) != 'aberta'


def cards_com_produto_divergente(con, dias=7):
    """Alarme: card recente cujo produto discorda do quadro, sem troca registrada. Avisa, nao corrige."""
    fora = []
    for r in con.execute(
            "select id, coalesce(contato_nome,''), coalesce(produto,''), "
            "coalesce(pipeline_id,0), coalesce(etapa,''), coalesce(criado_em,'') "
            "from wa_conversas where coalesce(produto,'')<>'' "
            "and pipeline_id in (%s) "
            "and coalesce(replace(criado_em,'T',' '), datetime('now')) "
            ">= datetime('now', ?)" % ','.join(map(str, QUADROS)), ('-%d day' % int(dias),)):
        cid, nome, produto, quadro, etapa, criado = r
        if quadro == QUADRO_ENTRADA:
            continue
        esperado = produto_do_quadro(quadro, etapa)
        if produto == esperado or _trocas(con, cid):
            continue
        fora.append({'conversa_id': cid, 'nome': nome, 'produto': produto,
                     'produto_do_quadro': esperado, 'pipeline_id': quadro, 'etapa': etapa,
                     'criado_em': criado})
    return fora
