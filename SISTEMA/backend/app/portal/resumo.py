""
import hashlib
import json
import logging
import os
import re
import time

from . import dados

log = logging.getLogger('portal.resumo')

MODELO = 'claude-opus-5'
MAX_TEXTO_DOC = 12000
CACHE_DDL = """CREATE TABLE IF NOT EXISTS portal_resumo_ia (
    chave TEXT PRIMARY KEY, numero TEXT, tipo TEXT, texto TEXT,
    modelo TEXT, tokens_entrada INTEGER, tokens_saida INTEGER, criado_em REAL)"""


def criar_tabela():
    with dados._con() as con:
        con.execute(CACHE_DDL)
        con.commit()


def _cache_ler(chave: str):
    try:
        with dados._con() as con:
            r = con.execute('SELECT texto FROM portal_resumo_ia WHERE chave=?', (chave,)).fetchone()
        return r['texto'] if r else None
    except Exception:
        return None


def _cache_gravar(chave, numero, tipo, texto, uso):
    try:
        with dados._con() as con:
            con.execute(
                'INSERT OR REPLACE INTO portal_resumo_ia'
                '(chave,numero,tipo,texto,modelo,tokens_entrada,tokens_saida,criado_em)'
                ' VALUES(?,?,?,?,?,?,?,?)',
                (chave, numero, tipo, texto, MODELO,
                 getattr(uso, 'input_tokens', 0), getattr(uso, 'output_tokens', 0), time.time()))
            con.commit()
    except Exception as e:
        log.warning(f'nao gravou cache do resumo: {e}')


def _conferido(system: str, pergunta: str, texto, uso, max_tokens: int, numero: str, tipo: str):
    ""
    from . import texto_seguro
    texto = texto_seguro.aproveitar(texto)
    defeito = texto_seguro.problema(texto) if texto else None
    if texto and not defeito:
        dificeis = texto_seguro.palavras_dificeis(texto)
        if dificeis:
            t2, u2 = _chamar_ia(system, pergunta + texto_seguro.pedido_de_palavras_simples(dificeis),
                                max_tokens=max_tokens)
            t2 = texto_seguro.aproveitar(t2)
            if (t2 and not texto_seguro.problema(t2)
                    and len(texto_seguro.palavras_dificeis(t2)) < len(dificeis)):
                return t2, u2
        return texto, uso
    if not texto:
        return None, None
    log.warning(f'{numero} {tipo}: texto reprovado ({defeito}); pedindo de novo')
    t2, u2 = _chamar_ia(system, pergunta + texto_seguro.pedido_de_correcao(defeito), max_tokens=max_tokens)
    t2 = texto_seguro.aproveitar(t2)
    if t2 and not texto_seguro.problema(t2):
        return t2, u2
    log.warning(f'{numero} {tipo}: reprovado de novo; nada vai para a tela nem para o cache')
    return None, None


def _chamar_ia(system: str, pergunta: str, max_tokens: int = 700, modelo: str = None):
    ""
    try:
        from dotenv import load_dotenv
        load_dotenv('/opt/escritorio/config/.env')
        if not os.getenv('ANTHROPIC_API_KEY'):
            return None, None
        import anthropic
        cli = anthropic.Anthropic()
        r = cli.messages.create(model=(modelo or MODELO), max_tokens=max_tokens,
                                system=system, messages=[{'role': 'user', 'content': pergunta}])
        if getattr(r, 'stop_reason', '') == 'refusal':
            log.warning('IA recusou o resumo; caindo no texto deterministico')
            return None, None
        texto = ''.join(b.text for b in r.content if b.type == 'text').strip()
        return (texto or None), r.usage
    except Exception as e:
        log.warning(f'IA indisponivel para resumo: {type(e).__name__}: {str(e)[:120]}')
        return None, None


def _norm_data(v):
    return (v or '')[:19].replace('T', ' ')


def espelho(numero: str, monitorado: bool) -> dict:
    ""
    capa, eventos, fonte = {}, [], 'nenhuma'

    if monitorado:
        try:
            d = dados.sync_get(f'/processos/{numero}/autos', {'itens': 300})
            capa = d.get('capa') or {}
            for it in (d.get('autos') or []):
                eventos.append({
                    'tipo': it.get('tipo'),
                    'data': it.get('data'),
                    'grau': it.get('grau') or '',
                    'descricao': it.get('descricao') or '',
                    'nome': it.get('nome') or '',
                    'tipo_documento': it.get('tipo_documento') or '',
                    'documento_id': it.get('id'),
                    'tem_arquivo': bool(it.get('tem_arquivo')) and it.get('conteudo_status') == 'disponivel',
                    'tem_texto': bool(it.get('tem_markdown')) and it.get('conteudo_status') == 'disponivel',
                })
            if eventos:
                fonte = 'autos'
                return {'capa': capa, 'eventos': eventos, 'fonte': fonte,
                        'total': d.get('total') or len(eventos)}
            log.warning(f'autos vazios em {numero}: caindo para a consulta publica')
        except Exception as e:
            log.warning(f'autos falharam em {numero}: {str(e)[:100]}')

    try:
        p = dados.sync_get(f'/preview/{numero}')
        if p.get('segredo_justica') and not p.get('classe'):
            return {'capa': {'numero': numero, 'segredo_justica': True}, 'eventos': [],
                    'fonte': 'segredo', 'total': 0}
        capa = {
            'numero': p.get('numero'), 'tribunal': p.get('tribunal'), 'classe': p.get('classe'),
            'assunto': p.get('assunto'), 'orgao_julgador': p.get('orgao_julgador'),
            'valor_causa': p.get('valor_causa'), 'data_distribuicao': p.get('data_distribuicao'),
            'polo_ativo': p.get('polo_ativo'), 'polo_passivo': p.get('polo_passivo'),
            'advogados_ativo': p.get('advogados_ativo'), 'advogados_passivo': p.get('advogados_passivo'),
            'segredo_justica': bool(p.get('segredo')),
        }
        for m in (p.get('movimentos') or []):
            eventos.append({'tipo': 'movimento', 'data': m.get('data'),
                            'descricao': m.get('descricao') or '', 'nome': '',
                            'tipo_documento': '', 'documento_id': None,
                            'tem_arquivo': False, 'tem_texto': False})
        for doc in (p.get('documentos') or []):
            eventos.append({'tipo': 'documento', 'data': doc.get('data_juntada'),
                            'descricao': '', 'nome': doc.get('nome') or '',
                            'tipo_documento': doc.get('tipo') or '', 'documento_id': None,
                            'tem_arquivo': False, 'tem_texto': False})
        eventos.sort(key=lambda e: e['data'] or '', reverse=True)
        fonte = 'preview'
    except Exception as e:
        log.warning(f'preview falhou em {numero}: {str(e)[:100]}')

    return {'capa': capa, 'eventos': eventos, 'fonte': fonte, 'total': len(eventos)}


SYS_CASO = (
    'Voce escreve para o cliente de um escritorio de advocacia brasileiro, uma pessoa '
    'leiga em direito. Explique o processo dela em portugues simples e direto.\n'
    'Regras: no maximo 3 frases curtas. Sem juridiques. Sem travessao. Nao invente fato '
    'que nao esteja nos dados. Nao prometa resultado, nao estime prazo, nao de conselho '
    'juridico. Fale com o cliente na segunda pessoa (voce, seu, sua). '
    'Comece dizendo do que o processo trata.'
)


def _ler_doc(documento_id):
    ""
    bruto = dados.sync_get(f'/documentos/{documento_id}/markdown', texto=True)
    if isinstance(bruto, dict):
        bruto = bruto.get('texto') or bruto.get('markdown') or ''
    return bruto or ''


def resumo_caso(numero: str, capa: dict, etapa: str, eventos: list,
                polo: str = '', nome_cliente: str = '',
                so_cache: bool = False) -> str | None:
    ""
    from . import resumo_v2
    capa = capa or {}

    marco = ''
    for e in (eventos or [])[:40]:
        if e.get('descricao'):
            marco = e['descricao']
            break
    ultima = _norm_data((eventos or [{}])[0].get('data'))
    assinatura = f'{numero}|{etapa}|{ultima}|{polo}'
    chave = 'caso2:' + hashlib.sha256(assinatura.encode()).hexdigest()[:32]
    guardado = _cache_ler(chave)
    if guardado:
        return guardado
    if so_cache:
        return None

    doc, texto_peca = resumo_v2.achar_peca_inicial(eventos or [], _ler_doc)
    from . import texto_seguro
    texto_peca = texto_seguro.limpar_entrada(texto_peca)
    if doc:
        log.info(f'{numero}: peca inicial = doc {doc.get("documento_id")} '
                 f'({len(texto_peca)} chars)')

    if not (capa.get('classe') or texto_peca):
        return None

    pergunta = resumo_v2.pergunta_caso(numero, capa, etapa, polo,
                                       nome_cliente or 'o cliente', texto_peca, marco)
    texto, uso = _chamar_ia(resumo_v2.SYS_CASO, pergunta, max_tokens=800)
    texto, uso = _conferido(resumo_v2.SYS_CASO, pergunta, texto, uso, 800, numero, 'caso')
    if texto:
        _cache_gravar(chave, numero, 'caso', texto, uso)
    return texto


SYS_MOV = (
    'Voce explica ao cliente de um escritorio de advocacia brasileiro, pessoa leiga, '
    'o que acabou de acontecer no processo dele.\n'
    'Regras: no maximo 3 frases curtas. Portugues simples, sem juridiques, sem travessao. '
    'Diga o que aconteceu e o que isso significa na pratica para ele. Nao invente nada '
    'alem do texto fornecido. Nao prometa resultado nem estime prazo. Se o texto for '
    'burocratico e sem efeito pratico, diga que e um andamento de rotina.'
)


def _limpar_doc(txt: str) -> str:
    txt = re.sub(r'\n{3,}', '\n\n', txt or '')
    return txt.strip()[:MAX_TEXTO_DOC]


def resumo_movimentacao(numero: str, evento: dict, eventos: list = None,
                        polo: str = '', so_cache: bool = False) -> str | None:
    ""
    from . import resumo_v2
    from . import traducao

    lista = eventos if eventos is not None else ([evento] if evento else [])
    from . import lado
    alvo, traduzido, rotina_depois = resumo_v2.escolher_movimento_relevante(
        lista, lado.tradutor(traducao.traduzir_evento, polo))

    if not alvo:
        return ('Nao houve novidade importante no seu processo nos ultimos registros. '
                'Ele segue andando normalmente e nos acompanhamos de perto.')

    base = (alvo.get('descricao') or alvo.get('nome') or '').strip()
    assinatura = f'{numero}|{_norm_data(alvo.get("data"))}|{base}|{polo}|{rotina_depois}'
    chave = 'mov2:' + hashlib.sha256(assinatura.encode()).hexdigest()[:32]
    guardado = _cache_ler(chave)
    if guardado:
        return guardado
    if so_cache:
        return None

    texto_doc = ''
    if alvo.get('documento_id') and alvo.get('tem_texto'):
        try:
            texto_doc = resumo_v2._corpo(_ler_doc(alvo['documento_id']))
        except Exception as e:
            log.warning(f'markdown do doc {alvo["documento_id"]} falhou: {str(e)[:80]}')
    from . import texto_seguro
    texto_doc = texto_seguro.limpar_entrada(texto_doc)

    pergunta = resumo_v2.pergunta_movimento(alvo, traduzido, texto_doc, rotina_depois, polo)
    texto, uso = _chamar_ia(resumo_v2.SYS_MOV, pergunta, max_tokens=600)
    texto, uso = _conferido(resumo_v2.SYS_MOV, pergunta, texto, uso, 600, numero, 'movimentacao')
    if texto:
        _cache_gravar(chave, numero, 'movimentacao', texto, uso)
    return texto


def custo_ate_agora() -> dict:
    ""
    with dados._con() as con:
        r = con.execute('SELECT COUNT(*) n, COALESCE(SUM(tokens_entrada),0) ent, '
                        'COALESCE(SUM(tokens_saida),0) sai FROM portal_resumo_ia').fetchone()
    ent, sai = r['ent'], r['sai']
    usd = ent / 1_000_000 * 5.0 + sai / 1_000_000 * 25.0
    return {'resumos': r['n'], 'tokens_entrada': ent, 'tokens_saida': sai,
            'custo_usd': round(usd, 4), 'modelo': MODELO}
