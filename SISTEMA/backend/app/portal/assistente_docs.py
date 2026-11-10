""
import datetime as _dt
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import time

from . import texto_seguro

log = logging.getLogger('portal.assistente_docs')

MESES = {'janeiro': 1, 'fevereiro': 2, 'marco': 3, 'março': 3, 'abril': 4, 'maio': 5, 'junho': 6,
         'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12}
CNJ = re.compile(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}')
QUER_DECISAO = re.compile(r'decis|decid|ac[oó]rd|despach|julg|liminar|agravo|juiz|juiza|desembarg|tribunal', re.I)
ROTULO_DECISAO = re.compile(r'DECIS|AC[OÓ]RD|DESPACH|JULGAMENTO|LIMINAR|AGRAVO|EMBARGOS', re.I)
SENTENCA = re.compile(r'SENTEN|AC[OÓ]RD', re.I)
NOME_NA_PASTA = re.compile(r'decis|ac[oó]rd|agravo|despach|liminar|julgamento|embargos', re.I)
NOME_DECISAO = re.compile(r'decis|ac[oó]rd|despach|liminar|julgamento', re.I)
PETICAO_DE_PARTE = re.compile(r'^\s*(Juntada a peti[çc][ãa]o de\s+)?(Recurso|Agravo|Embargos|Contrarraz|Peti[çc])', re.I)
NOME_PECA_NOSSA = re.compile(r'contrarraz|contra-raz|raz[oõ]es|peti[cç]|manifesta|impugna|minuta|procura', re.I)

MAX_DOCS_AUTOS = 3
MAX_DOCS_PASTA = 2
MAX_CHARS_DOC = 6000
MAX_BYTES_ARQUIVO = 20 * 1024 * 1024
ORCAMENTO_PASTA_SEG = 14
CACHE = os.environ.get('ESC_CACHE_PASTA_TEXTO', '/opt/escritorio/portal_audio_cache/textos_pasta')


def datas_citadas(pergunta: str):
    ""
    t = CNJ.sub(' ', pergunta or '')
    achadas = []
    for m in re.finditer(r'(?<!\d)(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?:\s*[/.\-]\s*(\d{2,4}))?(?!\d)', t):
        achadas.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    for m in re.finditer(r'(?<!\d)(\d{1,2})\s+do\s+(\d{1,2})(?!\d)', t, re.I):
        achadas.append((int(m.group(1)), int(m.group(2)), None))
    for m in re.finditer(r'(?<!\d)(\d{1,2})\s+de\s+(' + '|'.join(MESES) + r')(?:\s+de\s+(\d{4}))?', t, re.I):
        achadas.append((int(m.group(1)), MESES[m.group(2).lower()], m.group(3)))
    saida = []
    for d, mes, ano in achadas:
        if 1 <= d <= 31 and 1 <= mes <= 12:
            a = int(ano) if ano else None
            if a and a < 100:
                a += 2000
            if (d, mes, a) not in saida:
                saida.append((d, mes, a))
    return saida[:3]


def _dia_local(iso):
    ""
    if not iso:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(_dt.timezone(_dt.timedelta(hours=-3)))
        return dt.date()
    except Exception:
        try:
            return _dt.date.fromisoformat(str(iso)[:10])
        except Exception:
            return None


def _rotulo(e):
    return ' '.join(x for x in (e.get('tipo_documento'), e.get('nome'), e.get('descricao')) if x)


def _eh_sentenca(e, traduzir):
    try:
        tit = traduzir(e).get('titulo') or ''
        if tit.startswith('Sentenca') or tit.startswith('Recurso julgado') or tit.startswith('Tribunal julgou'):
            return True
    except Exception:
        pass
    return bool(SENTENCA.search(_rotulo(e)))


def escolher(pergunta, eventos, traduzir):
    ""
    datas = datas_citadas(pergunta)
    alvos, sem_registro, proximos = [], [], []
    for d, mes, ano in datas:
        do_dia = [e for e in eventos
                  if (lambda x: x and x.day == d and x.month == mes and (ano is None or x.year == ano))(_dia_local(e.get('data')))]
        if do_dia:
            anos = sorted({_dia_local(e.get('data')).year for e in do_dia}, reverse=True)
            alvos += [e for e in do_dia if _dia_local(e.get('data')).year == anos[0]]
        else:
            sem_registro.append((d, mes, ano))
            ref = _dt.date(ano or _dt.date.today().year, mes, min(d, 28))
            perto = sorted((e for e in eventos if _dia_local(e.get('data'))
                            and abs((_dia_local(e.get('data')) - ref).days) <= 20),
                           key=lambda e: abs((_dia_local(e.get('data')) - ref).days))
            proximos += perto[:4]
    quer_decisao = bool(QUER_DECISAO.search(pergunta or ''))
    if quer_decisao and not alvos:
        decisoes = [e for e in eventos if ROTULO_DECISAO.search(_rotulo(e)) and not _eh_sentenca(e, traduzir)
                    and not PETICAO_DE_PARTE.search(_rotulo(e))]
        alvos += decisoes[:4]
        if sem_registro:
            alvos += [e for e in proximos if ROTULO_DECISAO.search(_rotulo(e))][:2]
    return {'datas': datas, 'alvos': alvos, 'sem_registro': sem_registro,
            'proximos': proximos, 'quer_decisao': quer_decisao}


def _extrair_texto_arquivo(svc, arq):
    ""
    chave = hashlib.sha1((arq['id'] + '|' + (arq.get('quando') or '')).encode()).hexdigest()[:24]
    caminho = os.path.join(CACHE, chave + '.txt')
    if os.path.exists(caminho):
        with open(caminho, encoding='utf-8') as f:
            return f.read()
    mime = arq.get('mime') or ''
    texto = ''
    if mime == 'application/vnd.google-apps.document':
        texto = svc.files().export(fileId=arq['id'], mimeType='text/plain').execute().decode('utf-8', 'ignore')
    elif mime == 'application/pdf' and (arq.get('tamanho') or 0) <= MAX_BYTES_ARQUIVO:
        conteudo = svc.files().get_media(fileId=arq['id']).execute()
        with tempfile.TemporaryDirectory() as pasta:
            pdf = os.path.join(pasta, 'doc.pdf')
            with open(pdf, 'wb') as f:
                f.write(conteudo)
            r = subprocess.run(['pdftotext', '-q', '-l', '40', '-enc', 'UTF-8', pdf, '-'],
                               capture_output=True, timeout=30)
            texto = r.stdout.decode('utf-8', 'ignore')
    texto = texto_seguro.limpar_entrada(texto)
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(caminho + '.tmp', 'w', encoding='utf-8') as f:
            f.write(texto)
        os.replace(caminho + '.tmp', caminho)
    except Exception:
        pass
    return texto


def textos_da_pasta(pasta_drive, escolha):
    ""
    if not pasta_drive:
        return []
    from . import dados, pasta_portal
    inicio = time.time()
    try:
        svc = dados._drive_service()
        sub, _como = pasta_portal.achar_pasta_do_cliente(svc, pasta_drive)
        if not sub:
            return []
        arquivos = pasta_portal.listar(svc, sub['id'])
    except Exception as e:
        log.warning(f'pasta indisponivel para a assistente: {str(e)[:100]}')
        return []

    def nota(a):
        nome = a.get('nome') or ''
        if SENTENCA.search(nome):
            return -1
        n = 0
        if NOME_DECISAO.search(nome):
            n += 3
        elif NOME_NA_PASTA.search(nome):
            n += 1
        if NOME_PECA_NOSSA.search(nome):
            n -= 3
        for d, mes, ano in escolha['datas']:
            padroes = ['%02d.%02d' % (d, mes), '%02d-%02d' % (d, mes), '%02d_%02d' % (d, mes),
                       '%02d%02d' % (d, mes), '%d.%d' % (d, mes)]
            if any(p in nome for p in padroes):
                n += 3
            try:
                q = _dt.date.fromisoformat(a.get('quando') or '')
                ref = _dt.date(ano or q.year, mes, min(d, 28))
                if abs((q - ref).days) <= 15:
                    n += 1
            except Exception:
                pass
        return n

    candidatos = sorted((a for a in arquivos if nota(a) >= 2), key=lambda a: a.get('quando') or '', reverse=True)
    candidatos.sort(key=lambda a: -nota(a))
    saida = []
    for a in candidatos:
        if len(saida) >= MAX_DOCS_PASTA or time.time() - inicio > ORCAMENTO_PASTA_SEG:
            break
        try:
            texto = _extrair_texto_arquivo(svc, a)
        except Exception as e:
            log.warning(f'nao li {a.get("nome")}: {str(e)[:80]}')
            continue
        if len(texto) >= 200:
            saida.append((f'"{a.get("nome")}" (arquivo da pasta do cliente, de {a.get("quando") or "data nao informada"})',
                          texto[:MAX_CHARS_DOC]))
    return saida


def bloco_para_contexto(pergunta, eventos, traduzir, ler_doc, pasta_drive):
    ""
    from . import resumo_v2
    escolha = escolher(pergunta, eventos or [], traduzir)
    if not (escolha['datas'] or escolha['quer_decisao']):
        return ''

    partes = []
    for d, mes, ano in escolha['sem_registro']:
        rot = '%02d/%02d' % (d, mes) + ('/%d' % ano if ano else '')
        perto = '; '.join('%s %s' % (str(_dia_local(e.get('data')) or '')[8:10] + '/' + str(_dia_local(e.get('data')) or '')[5:7],
                                     (traduzir(e).get('titulo') or _rotulo(e))[:80])
                          for e in escolha['proximos'][:4])
        partes.append(f'O CLIENTE CITOU {rot}: NAO HA REGISTRO NESTA DATA NESTE PROCESSO. Diga isso com clareza. '
                      + (f'Registros mais proximos: {perto}.' if perto else 'Nao ha registro perto dessa data.'))

    lidos, sem_texto = 0, []
    vistos = set()
    for e in escolha['alvos']:
        if lidos >= MAX_DOCS_AUTOS:
            break
        doc_id = e.get('documento_id')
        if not doc_id or doc_id in vistos:
            continue
        vistos.add(doc_id)
        dia = _dia_local(e.get('data'))
        titulo = (traduzir(e).get('titulo') or _rotulo(e))[:80]
        if _eh_sentenca(e, traduzir):
            continue
        if not e.get('tem_texto'):
            sem_texto.append(f'{dia:%d/%m/%Y} {titulo}' if dia else titulo)
            continue
        try:
            texto = texto_seguro.limpar_entrada(resumo_v2._corpo(ler_doc(doc_id)))
        except Exception:
            texto = ''
        if len(texto) < 200:
            sem_texto.append(f'{dia:%d/%m/%Y} {titulo}' if dia else titulo)
            continue
        partes.append(f'DOCUMENTO DO PROCESSO de {dia:%d/%m/%Y} ({titulo}), texto do tribunal:\n'
                      + texto[:MAX_CHARS_DOC])
        lidos += 1

    if sem_texto:
        partes.append('Estes documentos existem no tribunal, mas o texto deles nao veio: ' + '; '.join(sem_texto[:5]) + '.')
    if lidos == 0:
        for rotulo, texto in textos_da_pasta(pasta_drive, escolha):
            partes.append(f'DOCUMENTO {rotulo}:\n' + texto)
            lidos += 1

    if not partes:
        return ''
    return ('\n' + '=' * 40 + '\nDOCUMENTOS LIDOS PARA ESTA PERGUNTA. Use o texto deles para explicar, em '
            'portugues simples, o que foi decidido e o que isso muda para o cliente. Diga a data do documento. '
            'Se o documento nao responder, diga que nao encontrou.\n\n' + '\n\n'.join(partes))
