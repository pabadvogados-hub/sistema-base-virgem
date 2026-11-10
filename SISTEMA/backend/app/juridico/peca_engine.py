# -*- coding: utf-8 -*-
"""Motor de peca do escritorio: texto pela IA com o DNA + formatacao no TIMBRADO.docx.

Usado por routers/peca.py (Gerar Peca) e pelas skills (.claude/skills/timbrado/montar_peca.py
chama formatar_no_timbrado). Sem dependencia de pasta externa.

- gerar_texto_peca(): monta o prompt com juridico/DNA_ESCRITORIO.md (formatacao, abertura,
  fecho, teses, assinatura) e chama o Claude; se a Anthropic recusar, cai na IA de reserva.
- formatar_no_timbrado(): abre juridico/modelos/TIMBRADO.docx (imagem no cabecalho) e escreve o
  corpo em Century Gothic 12, justificado, recuo de 3 cm na primeira linha; titulos em caixa alta
  e negrito sem recuo; enderecamento, fecho e assinatura sem recuo.
"""
import os
import re
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DNA = AQUI / 'DNA_ESCRITORIO.md'
TIMBRADO = Path(os.getenv('ESC_TIMBRADO_DOCX') or (AQUI / 'modelos' / 'TIMBRADO.docx'))
MODELO = os.getenv('ESC_MODELO_PECA', 'claude-opus-5-5')
FONTE = os.getenv('ESC_FONTE_PECA', 'Century Gothic')
TAMANHO = float(os.getenv('ESC_TAMANHO_PECA', '12'))
RECUO_CM = float(os.getenv('ESC_RECUO_PECA_CM', '3'))


def ler_dna():
    return DNA.read_text(encoding='utf-8') if DNA.exists() else ''


SISTEMA = """Voce e a advogada redatora do escritorio. Escreva a peca COMPLETA, pronta para revisao,
seguindo a risca o DNA abaixo (enderecamento, abertura "por suas advogadas", topicos DA/DO em
caixa alta, citacao de dispositivo em paragrafo proprio, pedidos em alineas, fecho "Termos em que,
pede deferimento.", cidade e data, bloco de assinatura).

Regras inegociaveis:
- nunca invente jurisprudencia: sem precedente que esteja no DNA ou no bloco de jurisprudencia
  verificada, escreva [CONFERIR PRECEDENTE];
- nunca invente dado do cliente ou do processo: use [PREENCHER: campo];
- valores sempre com "R$";
- devolva SO o texto da peca, um paragrafo por linha, sem markdown (sem #, sem **), sem comentarios.

===== DNA DO ESCRITORIO =====
{dna}
"""


def _chave_anthropic():
    try:
        from dotenv import dotenv_values
        from ..config import ENV_CENTRAL
        v = dotenv_values(str(ENV_CENTRAL)) if ENV_CENTRAL.exists() else {}
    except Exception:
        v = {}
    return (v.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_API_KEY') or '').strip()


def montar_prompt(dados_caso, tipo_peca, esqueleto='', jurisprudencia=''):
    partes = ['TIPO DE PECA: %s' % (tipo_peca or '').upper(), '', 'DADOS DO CASO E DOS AUTOS:', dados_caso or '']
    if esqueleto:
        partes += ['', 'ESQUELETO / ORIENTACAO DA ADVOGADA (siga a ordem dos topicos):', esqueleto]
    if jurisprudencia:
        partes += ['', 'JURISPRUDENCIA VERIFICADA (use so esta):', jurisprudencia]
    return '\n'.join(partes)


def gerar_texto_peca(dados_caso, tipo_peca, esqueleto='', jurisprudencia='', incluir_amostras=True,
                     max_tokens=16000):
    """Devolve o texto puro da peca (um paragrafo por linha)."""
    sistema = SISTEMA.format(dna=ler_dna() if incluir_amostras else '')
    msg = montar_prompt(dados_caso, tipo_peca, esqueleto, jurisprudencia)
    chave = _chave_anthropic()
    erro = None
    modo = (os.getenv('ESC_IA_PECA') or ('api' if chave else 'cli')).lower()
    if modo == 'cli':  # Claude Code da assinatura do escritorio (sem credito de API)
        try:
            return limpar(_via_cli(sistema, msg))
        except Exception as e:
            erro = e
    if chave and modo != 'cli':
        try:
            import anthropic
            cli = anthropic.Anthropic(api_key=chave)
            r = cli.messages.create(model=MODELO, max_tokens=max_tokens, system=sistema,
                                    messages=[{'role': 'user', 'content': msg}])
            return limpar(''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text'))
        except Exception as e:  # sem credito, fora do ar: tenta a reserva
            erro = e
    try:
        from .. import ia_reserva
        if ia_reserva.disponivel():
            return limpar(ia_reserva.texto(sistema, [{'role': 'user', 'content': msg}], max_tokens=max_tokens,
                                           modelo=ia_reserva.MODELO_PRO))
    except Exception as e:
        erro = erro or e
    raise RuntimeError('Nenhuma IA disponivel para gerar a peca (%s)' % (erro or 'sem ANTHROPIC_API_KEY/GEMINI_API_KEY'))


def _via_cli(sistema, msg, timeout=None):
    """claude -p com o prompt de sistema; usa a assinatura logada na maquina (CLAUDE_CODE_OAUTH_TOKEN no servidor)."""
    import shutil
    import subprocess
    exe = os.getenv('ESC_CLAUDE_BIN') or shutil.which('claude') or '/usr/local/bin/claude'
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)
    r = subprocess.run([exe, '-p', '--model', os.getenv('ESC_MODELO_PECA_CLI', 'opus'), '--append-system-prompt',
                        sistema], input=msg, capture_output=True, text=True, encoding='utf-8',
                       env=env, timeout=timeout or int(os.getenv('ESC_TIMEOUT_PECA', '900')))
    if r.returncode or not r.stdout.strip():
        raise RuntimeError('claude -p falhou (%s): %s' % (r.returncode, (r.stderr or r.stdout)[-400:]))
    return r.stdout


def limpar(texto):
    t = (texto or '').replace('\r\n', '\n')
    t = re.sub(r'^\s*#+\s*', '', t, flags=re.M)
    t = t.replace('**', '')
    return re.sub(r'\n{3,}', '\n\n', t).strip()


# ---------------------------------------------------------------- formatacao
_SEM_RECUO = re.compile(
    r'^(AO JU[IÍ]ZO|EXCELENT|EGR[EÉ]GI|PROCESSO|RECORRENTE|RECORRID|ORIGEM|RAZ[OÕ]ES DE|EGR[EÉ]GIA TURMA|'
    r'TERMOS EM QUE|OUTORGANTE|OUTORGADO|EM FACE DE)', re.I)
_ASSINATURA = re.compile(r'OAB/[A-Z]{2}\s*[\d.]+\s*$')
_DATA_LOCAL = re.compile(r'^[A-ZÀ-Ú][\wÀ-ú ]+, \d{1,2} de [a-zç]+ de \d{4}\.?$')


def _eh_titulo(p):
    s = p.strip()
    letras = re.sub(r'[^A-Za-zÀ-ú]', '', s)
    return 3 < len(s) <= 140 and letras and s.upper() == s and not s.endswith((';', ',')) \
        and not _ASSINATURA.search(s)


def formatar_no_timbrado(texto, out_path, modelo=None):
    """Grava `texto` no timbrado do escritorio, no padrao do DNA. Devolve o caminho."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    from docx.oxml.ns import qn

    base = Path(modelo or TIMBRADO)
    doc = Document(str(base)) if base.exists() else Document()
    corpo = doc.element.body
    for el in list(corpo):  # mantem cabecalho/rodape (sectPr), limpa o corpo do modelo
        if el.tag != qn('w:sectPr'):
            corpo.remove(el)

    def par(txt, negrito=False, recuo=True, alin=WD_ALIGN_PARAGRAPH.JUSTIFY):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.alignment = alin
        pf.first_line_indent = Cm(RECUO_CM) if recuo else Cm(0)
        pf.line_spacing = 1.5
        pf.space_after = Pt(6)
        r = p.add_run(txt)
        r.bold = negrito
        r.font.name = FONTE
        r.font.size = Pt(TAMANHO)
        rpr = r._element.get_or_add_rPr()
        rf = rpr.find(qn('w:rFonts'))
        if rf is None:
            rf = rpr.makeelement(qn('w:rFonts'), {})
            rpr.append(rf)
        for k in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
            rf.set(qn(k), FONTE)
        return p

    for linha in limpar(texto).split('\n'):
        s = linha.strip()
        if not s:
            continue
        if _ASSINATURA.search(s):
            par(s, negrito=True, recuo=False, alin=WD_ALIGN_PARAGRAPH.CENTER)
        elif _DATA_LOCAL.match(s):
            par(s, recuo=False, alin=WD_ALIGN_PARAGRAPH.RIGHT)
        elif _SEM_RECUO.match(s):
            par(s, negrito=_eh_titulo(s), recuo=False)
        elif _eh_titulo(s):
            par(s, negrito=True, recuo=False)
        else:
            par(s)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return str(out)


if __name__ == '__main__':  # teste offline: so formatacao
    import tempfile
    exemplo = """AO JUÍZO DA 1ª VARA DO TRABALHO DE CIDADE – SP
Processo nº 0000000-00.2026.5.02.0491
FULANO DE TAL, nos autos da reclamação trabalhista que move em face de EMPRESA X LTDA., por suas advogadas, vem respeitosamente perante Vossa Excelência, apresentar sua RÉPLICA À CONTESTAÇÃO, pelos motivos de fato e de direito a seguir expostos:
DO INTERVALO INTRAJORNADA
A Reclamada sustenta que o intervalo era integralmente usufruído.
Ao contrário.
DOS PEDIDOS
a) a rejeição das teses defensivas;
b) a procedência integral dos pedidos.
Termos em que, pede deferimento.
Cidade, 10 de novembro de 2026.
NOME DA ADVOGADA – OAB/UF 000.000"""
    out = formatar_no_timbrado(exemplo, Path(tempfile.gettempdir()) / 'teste_timbrado_esc.docx')
    from docx import Document
    d = Document(out)
    ps = [p for p in d.paragraphs if p.text.strip()]
    assert ps[0].runs[0].bold and ps[0].paragraph_format.first_line_indent == 0
    assert ps[4].paragraph_format.first_line_indent.cm > 2.9
    assert ps[3].runs[0].bold and ps[-1].runs[0].bold
    assert ps[4].runs[0].font.name == FONTE
    assert d.sections[0].header.part is not None
    print('ok', out, len(ps), 'paragrafos')
