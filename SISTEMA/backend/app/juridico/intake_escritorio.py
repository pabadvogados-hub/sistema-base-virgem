# -*- coding: utf-8 -*-
"""INTAKE DO ESCRITORIO - atendimento -> pasta do cliente pronta (prioridade dos 30 dias).

run_intake(transcricao, docs_extras, modo_teste, dados_crm, qualificacao) faz, em ordem:
  1. le a transcricao/ficha da reuniao e os documentos (pdf, docx, txt; imagem por OCR se houver);
  2. IA monta a FICHA DO CLIENTE (JSON): qualificacao, reclamada, resumo, direitos, pontos de
     atencao e documentos que faltam. Documento vence conversa: `qualificacao` (lida da CNH/RG/CTPS)
     sobrescreve o que a IA ouviu;
  3. cria no Drive RECLAMANTE > NOME DO CLIENTE com as subpastas padrao
     (ESC_SUBPASTAS_CLIENTE, por padrao ATOS INTERNOS, DOCUMENTOS DO CLIENTE, PASTA DO CLIENTE);
  4. gera Ficha (docx), Contrato de Honorarios e Procuracao a partir de juridico/modelos/ e sobe
     em ATOS INTERNOS; os documentos do cliente vao para DOCUMENTOS DO CLIENTE;
  5. grava cliente + processo em FASE DE CONTRATACAO no banco do sistema;
  6. so envia para assinatura (ZapSign) com ESC_INTAKE_ZAPSIGN=1 E sem nenhum [PREENCHER] aberto.

Tudo o que acontece sai em print (o router mostra o log na tela). modo_teste=True nao toca no
Drive nem no banco: grava os arquivos numa pasta local e devolve o resumo.
"""
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
MODELOS = AQUI / 'modelos'
DB = Path(__file__).resolve().parents[2] / 'sistema_esc.db'
MODELO_IA = os.getenv('ESC_MODELO_INTAKE', 'claude-opus-5-5')
CIDADE = os.getenv('ESC_CIDADE_ESCRITORIO', 'Cidade')
RESPONSAVEL = os.getenv('ESC_RESPONSAVEL_PROCESSO', 'ADVOGADA KAREN BARBOSA DOS ESCRITÓRIO MODELO')
SUBPASTAS = [s.strip() for s in os.getenv('ESC_SUBPASTAS_CLIENTE',
                                         'ATOS INTERNOS,DOCUMENTOS DO CLIENTE,PASTA DO CLIENTE').split(',') if s.strip()]
PREENCHER = '[PREENCHER: %s]'
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro']

CAMPOS = ['nome', 'cpf', 'rg', 'nacionalidade', 'estado_civil', 'profissao', 'data_nascimento', 'telefone', 'email',
          'endereco', 'bairro', 'cidade', 'estado', 'cep', 'reclamada', 'cnpj_reclamada', 'funcao', 'admissao',
          'demissao', 'motivo_saida', 'ultimo_salario', 'tipo_acao', 'resumo_caso', 'direitos', 'pontos_atencao',
          'documentos_faltantes', 'origem', 'indicante']

PROMPT = """Voce e a advogada de triagem do escritorio (trabalhista, lado do reclamante, TRT-2).
Leia a transcricao/ficha da reuniao e os documentos e devolva SO um JSON com as chaves:
%s
Regras: nunca invente; o que nao estiver no material fica "" (string vazia). "direitos",
"pontos_atencao" e "documentos_faltantes" sao listas de frases curtas. Em "direitos" liste o que o
caso sustenta (horas extras, intervalo, verbas rescisorias, FGTS, insalubridade, periculosidade,
dano moral, rescisao indireta, acidente de trabalho, vinculo etc.), cada um com o fato que o apoia.
CPF no formato 000.000.000-00; datas DD/MM/AAAA.""" % ', '.join(CAMPOS)


# ------------------------------------------------------------------ leitura
def extrair_texto(caminho):
    p = Path(caminho)
    ext = p.suffix.lower()
    try:
        if ext == '.pdf':
            try:
                from pypdf import PdfReader
                return '\n'.join((pg.extract_text() or '') for pg in PdfReader(str(p)).pages)
            except Exception:
                import subprocess
                return subprocess.run(['pdftotext', '-layout', str(p), '-'], capture_output=True,
                                      text=True, timeout=120).stdout
        if ext == '.docx':
            from docx import Document
            return '\n'.join(par.text for par in Document(str(p)).paragraphs)
        if ext in ('.jpg', '.jpeg', '.png', '.webp'):
            try:
                import pytesseract
                from PIL import Image
                return pytesseract.image_to_string(Image.open(str(p)), lang='por')
            except Exception:
                return '(imagem sem OCR disponivel: %s)' % p.name
        return p.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        return '(erro lendo %s: %s)' % (p.name, e)


# ------------------------------------------------------------------ IA
def analisar(texto, docs_texto):
    from .peca_engine import _chave_anthropic
    msg = 'TRANSCRICAO/FICHA:\n%s\n\nDOCUMENTOS:\n%s' % (texto[:60000], docs_texto[:40000])
    bruto = ''
    chave = _chave_anthropic()
    if chave:
        import anthropic
        r = anthropic.Anthropic(api_key=chave).messages.create(
            model=MODELO_IA, max_tokens=8000, system=PROMPT, messages=[{'role': 'user', 'content': msg}])
        bruto = ''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text')
    else:
        from .. import ia_reserva
        bruto = ia_reserva.texto(PROMPT, [{'role': 'user', 'content': msg}], max_tokens=8000)
    return ler_json(bruto)


def ler_json(bruto):
    m = re.search(r'\{.*\}', bruto or '', re.S)
    dados = json.loads(m.group(0)) if m else {}
    for c in CAMPOS:
        dados.setdefault(c, [] if c in ('direitos', 'pontos_atencao', 'documentos_faltantes') else '')
    return dados


# ------------------------------------------------------------------ documentos
def _data_extenso(d=None):
    d = d or datetime.now()
    return '%d de %s de %d' % (d.day, MESES[d.month - 1], d.year)


def qualificacao_texto(d):
    def v(c, rot=None):
        return (d.get(c) or '').strip() or PREENCHER % (rot or c)
    end = ', '.join(x for x in [(d.get('endereco') or '').strip(), (d.get('bairro') or '').strip()] if x) \
        or PREENCHER % 'endereco'
    cid = '/'.join(x for x in [(d.get('cidade') or '').strip(), (d.get('estado') or '').strip()] if x) \
        or PREENCHER % 'cidade/UF'
    return ('%s, %s, %s, %s, portador(a) do RG nº %s e inscrito(a) no CPF sob o nº %s, residente e domiciliado(a) '
            'na %s, %s, CEP %s' % (v('nome').upper(), v('nacionalidade'), v('estado_civil'), v('profissao'),
                                   v('rg'), v('cpf'), end, cid, v('cep')))


def _trocar_no_docx(origem, destino, trocas):
    """Troca texto nos paragrafos (inclusive tabelas) preservando o estilo do primeiro run."""
    from docx import Document
    doc = Document(str(origem))

    def pars(d):
        for p in d.paragraphs:
            yield p
        for t in d.tables:
            for row in t.rows:
                for cell in row.cells:
                    yield from pars(cell)

    for p in pars(doc):
        txt = p.text
        novo = txt
        for a, b in trocas:
            novo = novo.replace(a, b)
        if novo != txt and p.runs:
            p.runs[0].text = novo
            for r in p.runs[1:]:
                r.text = ''
    doc.save(str(destino))
    return str(destino)


def gerar_documentos(dados, pasta_local):
    """Contrato e procuracao a partir dos modelos do escritorio + ficha do cliente. Devolve [(rotulo, caminho)]."""
    nome = (dados.get('nome') or 'CLIENTE').strip()
    qual = qualificacao_texto(dados)
    data = '%s, %s.' % (CIDADE, _data_extenso())
    trocas = [('DADOS DO CLIENTE', qual), ('NOME DO CLIENTE', nome.upper()),
              ('%s, data.' % CIDADE, data), ('Cidade, data.', data)]
    saida = []
    for arq, rot in (('CONTRATO_HONORARIOS_RECLAMANTE.docx', 'Contrato de Honorarios'),
                     ('PROCURACAO.docx', 'Procuracao')):
        if (MODELOS / arq).exists():
            dst = Path(pasta_local) / ('%s - %s.docx' % (nome, rot))
            saida.append((rot, _trocar_no_docx(MODELOS / arq, dst, trocas)))
        else:
            print('   Aviso: modelo %s nao encontrado em juridico/modelos/' % arq)
    saida.append(('Ficha Cliente', gerar_ficha(dados, Path(pasta_local) / (
        '%s - Ficha Cliente - %s.docx' % (nome, datetime.now().strftime('%d-%m-%Y'))))))
    return saida


def gerar_ficha(dados, destino):
    linhas = ['FICHA DO CLIENTE', '', 'QUALIFICAÇÃO', qualificacao_texto(dados), '',
              'CONTATO', 'Telefone: %s | E-mail: %s' % (dados.get('telefone') or '-', dados.get('email') or '-'), '',
              'RECLAMADA', '%s %s' % (dados.get('reclamada') or PREENCHER % 'reclamada',
                                     ('(CNPJ %s)' % dados['cnpj_reclamada']) if dados.get('cnpj_reclamada') else ''), '',
              'CONTRATO DE TRABALHO',
              'Função: %s | Admissão: %s | Saída: %s (%s) | Último salário: %s' % (
                  dados.get('funcao') or '-', dados.get('admissao') or '-', dados.get('demissao') or '-',
                  dados.get('motivo_saida') or '-', dados.get('ultimo_salario') or '-'), '',
              'RESUMO DO CASO', dados.get('resumo_caso') or '-', '', 'DIREITOS A VALIDAR']
    linhas += ['- %s' % x for x in (dados.get('direitos') or ['-'])]
    linhas += ['', 'PONTOS DE ATENÇÃO'] + ['- %s' % x for x in (dados.get('pontos_atencao') or ['-'])]
    linhas += ['', 'DOCUMENTOS QUE FALTAM'] + ['- %s' % x for x in (dados.get('documentos_faltantes') or ['-'])]
    linhas += ['', 'Origem: %s | Indicação: %s' % (dados.get('origem') or '-', dados.get('indicante') or '-')]
    from .peca_engine import formatar_no_timbrado
    return formatar_no_timbrado('\n'.join(l for l in linhas if l is not None), destino)


def pendencias(docs):
    """Lista os [PREENCHER: ...] ainda abertos em cada documento (trava da assinatura)."""
    from docx import Document
    out = {}
    for rot, cam in docs:
        txt = '\n'.join(p.text for p in Document(cam).paragraphs)
        achados = sorted(set(re.findall(r'\[PREENCHER: ([^\]]+)\]', txt)))
        if achados:
            out[rot] = achados
    return out


# ------------------------------------------------------------------ Drive e banco
def subir_no_drive(drive, pasta_id, caminho):
    from googleapiclient.http import MediaFileUpload
    meta = {'name': Path(caminho).name, 'parents': [pasta_id]}
    media = MediaFileUpload(str(caminho), resumable=False)
    return drive.files().create(body=meta, media_body=media, fields='id,webViewLink',
                                supportsAllDrives=True).execute()


def _subpasta(drive, pai, nome):
    q = "'%s' in parents and name='%s' and mimeType='application/vnd.google-apps.folder' and trashed=false" % (
        pai, nome.replace("'", "\\'"))
    r = drive.files().list(q=q, fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    return (r.get('files') or [{}])[0].get('id')


def gravar_no_sistema(dados, pasta_link, origem='', db=DB):
    """Cliente (por CPF) + processo em FASE DE CONTRATACAO. Rodar de novo para a mesma pasta nao duplica."""
    if not Path(db).exists():
        print('   (banco do sistema nao encontrado: %s)' % db)
        return None
    agora = datetime.now().isoformat(sep=' ', timespec='microseconds')
    con = sqlite3.connect(str(db), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        cpf = re.sub(r'\D', '', dados.get('cpf') or '')
        cli = None
        if cpf:
            for r in con.execute('SELECT * FROM jur_clientes WHERE documento IS NOT NULL'):
                if re.sub(r'\D', '', r['documento'] or '') == cpf:
                    cli = r
                    break
        if cli:
            cid = cli['id']
        else:
            doc = ('%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])) if len(cpf) == 11 else (dados.get('cpf') or '')
            cur = con.execute(
                'INSERT INTO jur_clientes (nome, documento, email, telefone, celular, origem, nascimento, estado_civil, '
                'profissao, logradouro, cidade, estado, cep, notas, criado_em, atualizado_em) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ((dados.get('nome') or '').upper()[:250], doc[:40], (dados.get('email') or '')[:200], '',
                 re.sub(r'\D', '', dados.get('telefone') or '')[:40], (origem or '')[:80],
                 (dados.get('data_nascimento') or '')[:20], (dados.get('estado_civil') or '')[:60],
                 (dados.get('profissao') or '')[:120], (dados.get('endereco') or '')[:250],
                 (dados.get('cidade') or '')[:120], (dados.get('estado') or '')[:40], (dados.get('cep') or '')[:20],
                 '', agora, agora))
            cid = cur.lastrowid
        ja = con.execute('SELECT id FROM jur_processos WHERE notas LIKE ?', ('%' + pasta_link + '%',)).fetchone()
        if ja:
            pid = ja['id']
        else:
            notas = 'PASTA DO CASO: %s' % pasta_link + (('\nORIGEM DO LEAD: %s' % origem) if origem else '')
            cur = con.execute(
                'INSERT INTO jur_processos (numero, protocolo, area, tipo, fase, etapa, responsavel, encerrado, '
                'status_encerramento, data_processo, pasta, valor_expectativa, valor_honorarios, contingencia, notas, '
                'criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('', '', 'TRABALHISTA RECLAMANTE', (dados.get('tipo_acao') or 'TRABALHISTA')[:120],
                 'FASE DE CONTRATAÇÃO', 'NEGOCIAÇÃO', RESPONSAVEL, 0, '', '', '', 0.0, 0.0, 0.0, notas, agora, agora))
            pid = cur.lastrowid
            cl = con.execute('SELECT nome, documento FROM jur_clientes WHERE id=?', (cid,)).fetchone()
            con.execute('INSERT INTO jur_processo_partes (processo_id, cliente_id, nome, documento, papel) '
                        'VALUES (?,?,?,?,?)', (pid, cid, cl['nome'], cl['documento'], (origem or '')[:80]))
        con.commit()
        return {'cliente_id': cid, 'processo_id': pid}
    finally:
        con.close()


# ------------------------------------------------------------------ orquestracao
def run_intake(pdf_path, docs_extras=None, modo_teste=False, dados_crm=None, qualificacao=None, numero_contrato=None,
               dados_prontos=None):
    print('\n--- INICIANDO INTAKE DO ESCRITORIO ---')
    print('1. Lendo a transcricao/ficha: %s' % pdf_path)
    texto = extrair_texto(pdf_path) if pdf_path else ''
    docs_texto = ''
    for d in docs_extras or []:
        print('   Lendo documento: %s' % Path(d).name)
        docs_texto += '\n--- DOCUMENTO: %s ---\n%s' % (Path(d).name, extrair_texto(d))

    print('\n2. Ficha do cliente pela IA...')
    dados = dict(dados_prontos) if dados_prontos else analisar(texto, docs_texto)
    for c in CAMPOS:
        dados.setdefault(c, [] if c in ('direitos', 'pontos_atencao', 'documentos_faltantes') else '')
    for c in ('telefone', 'email', 'origem', 'indicante'):
        if not dados.get(c) and (dados_crm or {}).get(c):
            dados[c] = dados_crm[c]
    if qualificacao:  # documento vence conversa
        trocados = [c for c, v in qualificacao.items() if v and c in CAMPOS and v != dados.get(c)]
        for c in trocados:
            dados[c] = qualificacao[c]
        if trocados:
            print('   Qualificacao vinda dos DOCUMENTOS: %s' % ', '.join(trocados))
    nome = (dados.get('nome') or '').strip() or 'CLIENTE SEM NOME'
    print('   Cliente: %s | Reclamada: %s' % (nome, dados.get('reclamada') or '(nao identificada)'))

    pasta_local = Path(tempfile.mkdtemp(prefix='intake_'))
    print('\n3. Gerando Ficha, Contrato e Procuracao...')
    docs = gerar_documentos(dados, pasta_local)
    abertos = pendencias(docs)
    for rot, campos in abertos.items():
        print('   %s com campo em aberto: %s' % (rot, ', '.join(campos)))

    resultado = {'nome': nome, 'dados': dados, 'documentos': [c for _, c in docs], 'pendencias': abertos,
                 'pasta_local': str(pasta_local)}
    if modo_teste:
        print('\n[MODO TESTE] Nada foi enviado ao Drive nem ao banco. Arquivos em: %s' % pasta_local)
        return resultado

    print('\n4. Criando a pasta do cliente no Google Drive...')
    import sys
    raiz = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(raiz / 'INTEGRACOES'))
    from google_integration import autenticar_google, criar_pasta_cliente
    drive, _docs = autenticar_google()
    pasta_id = criar_pasta_cliente(drive, nome)
    link = 'https://drive.google.com/drive/folders/%s' % pasta_id
    atos = _subpasta(drive, pasta_id, 'ATOS INTERNOS') or pasta_id
    docs_cli = _subpasta(drive, pasta_id, 'DOCUMENTOS DO CLIENTE') or pasta_id
    for rot, cam in docs:
        f = subir_no_drive(drive, atos, cam)
        print('   %s: %s' % (rot, f.get('webViewLink')))
    for d in docs_extras or []:
        subir_no_drive(drive, docs_cli, d)
    resultado.update(pasta_id=pasta_id, pasta_link=link)

    print('\n5. Gravando cliente e processo no sistema...')
    try:
        r = gravar_no_sistema(dados, link, dados.get('origem') or (dados_crm or {}).get('origem') or '')
        if r:
            print('   cliente #%(cliente_id)s / processo #%(processo_id)s' % r)
            resultado.update(r)
    except Exception as e:
        print('   Aviso: falha ao gravar no sistema: %s' % e)

    if os.getenv('ESC_INTAKE_ZAPSIGN', '0') == '1':
        if abertos:
            print('\n*** ASSINATURA BARRADA: ha campo em aberto. Corrija na pasta do cliente e envie a mao. ***')
        else:
            print('\n6. Enviando para assinatura (ZapSign)...')
            try:
                import base64
                import subprocess
                from zapsign_integration import criar_documento, criar_signatario
                signers = [criar_signatario(nome, dados.get('email') or None, dados.get('telefone') or None)]
                for rot, cam in docs:
                    if rot == 'Ficha Cliente':
                        continue
                    subprocess.run(['soffice', '--headless', '--convert-to', 'pdf', '--outdir', str(pasta_local), cam],
                                   capture_output=True, timeout=180)
                    pdf = Path(cam).with_suffix('.pdf')
                    if not pdf.exists():
                        print('   %s: sem PDF (instale o LibreOffice no servidor); envie a mao' % rot)
                        continue
                    r = criar_documento('%s - %s' % (nome, rot), base64.b64encode(pdf.read_bytes()).decode(), signers)
                    print('   %s: %s' % (rot, ((r or {}).get('signers') or [{}])[0].get('sign_url') or r))
            except Exception as e:
                print('   Aviso: ZapSign nao enviou: %s' % e)
    print('\n--- INTAKE FINALIZADO ---\nPasta do cliente: RECLAMANTE > %s\n%s' % (nome.upper(), link))
    return resultado


if __name__ == '__main__':  # teste offline, sem IA, sem Drive, sem banco
    exemplo = {'nome': 'Fulano de Tal', 'cpf': '000.000.000-00', 'rg': '00.000.000-0', 'nacionalidade': 'brasileiro',
               'estado_civil': 'solteiro', 'profissao': 'operador de caixa', 'endereco': 'Rua Exemplo, 100',
               'bairro': 'Centro', 'cidade': 'Cidade', 'estado': 'SP', 'cep': '08600-000', 'reclamada': 'MERCADO X LTDA',
               'direitos': ['horas extras: fechava o caixa depois de bater o ponto'], 'documentos_faltantes': ['CTPS']}
    r = run_intake('', modo_teste=True, dados_prontos=exemplo)
    assert len(r['documentos']) == 3 and not r['pendencias'], r['pendencias']
    sem_cpf = dict(exemplo, cpf='')
    r2 = run_intake('', modo_teste=True, dados_prontos=sem_cpf)
    assert 'cpf' in ' '.join(sum(r2['pendencias'].values(), [])), r2['pendencias']
    print('ok')
