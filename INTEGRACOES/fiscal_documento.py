# -*- coding: utf-8 -*-
"""Fiscal de documento: barra o que nao pode sair do escritorio.

Pedido da Advogada em 09/11/2026: "nao sei como que a gente consegue cadastrar
fiscais para que isso nao aconteca mais. E o mesmo problema que deu no
passado, que a gente teve que reconstruir varios contratos".

O PROBLEMA QUE ESTE MODULO RESOLVE
----------------------------------
Ja existe trava anti-modelo em `google_integration.documentos_prontos_para_
assinatura`, mas ela tem tres limites que deixaram o furo aberto:

  1. So le GOOGLE DOC pelo id no Drive. Documento montado a mao (.docx, .pdf)
     nao passa por ela.
  2. So conhece os `{{campos}}` do modelo. Nao ve "[CONFIRMAR: ...]", "a
     confirmar", "nao informado", XXX e companhia.
  3. **Nao fica no caminho do envio.** Quem chama, chama; quem esquece, envia.
     Foi assim no CT 100/2026 do Heitor Valença, assinado em 22/09/2026 com
     os `{{...}}` visiveis.

Aqui o fiscal le o ARQUIVO que vai ser enviado, seja ele qual for, e fica no
funil: `zapsign_integration.criar_documento`. Qualquer caminho que leve ao
cliente passa por ele, inclusive script de producao e inclusive eu.

Ele NAO conserta nada e NAO fala com cliente: aponta e barra, igual ao
[[fiscal-da-operacao]].

REVISAO LINHA A LINHA, 09/11/2026 (a Advogada pediu: "verifica tudo todas as linhas
de codigo"). O que mudou da v1 para ca:
  - pdftotext com codigo de erro deixava o texto vazio e o documento era
    barrado como "sem texto". Agora erro de leitura e erro de leitura, com o
    motivo no log, e nao se confunde com PDF de imagem.
  - "XX" tambem e numeral romano: "CLAUSULA XX" e legitimo, "dia XX de XX" nao
    e. Agora a regra olha a palavra que vem antes.
  - "a confirmar" era aviso leve, e aviso leve deixa sair. Num contrato, "data
    a confirmar" nao pode ir ao cliente: virou GRAVE (medido, sem falso
    positivo nos 69 documentos reais).
  - nome de arquivo vazio dizia "documento sem texto", que confundia quem lia o
    log. Agora diz que o documento ia sem nome.
  - se o proprio fiscal falhar (disco cheio, por exemplo), ele devolve
    reprovado com o motivo em vez de estourar excecao no meio do envio.
  - entidades XML do .docx (&quot;, &apos;, numericas) ficavam no texto e podiam
    partir uma marca no meio.

NAO ADICIONE regra de underscore ("____"): a procuracao tem linha de
assinatura, e barrar isso reprovaria todas as procuracoes do escritorio.

Uso:
    from fiscal_documento import conferir
    ok, achados = conferir('/caminho/CONTRATO.pdf')
    if not ok:
        ...  # nao envia

    python3 fiscal_documento.py arquivo1.pdf arquivo2.docx
"""
import os
import re
import subprocess
import sys
import zipfile

# ---------------------------------------------------------------- as marcas
# Cada regra e (regex, o_que) ou (regex, o_que, filtro). O filtro recebe o
# texto imediatamente ANTES do achado e devolve True para descartar.
#
# GRAVE = nao sai do escritorio em hipotese nenhuma.
#
# ATENCAO ao IGNORECASE: a primeira versao usava `\bTODO\b` sem distinguir
# caixa e barrou 15 documentos BONS de uma vez, porque "todo" e palavra comum
# em portugues ("todo o conteudo", "em todo caso"). Regra que depende de
# CAIXA ALTA entra na lista MARCAS_GRAVES_CAIXA, conferida sem IGNORECASE.
MARCAS_GRAVES = [
    (r"\{\{[^}]{0,80}\}\}", "campo do modelo em aberto"),
    (r"\[\s*CONFIRMAR[^\]]{0,80}\]", "campo marcado para confirmar"),
    (r"\[\s*PREENCHER[^\]]{0,80}\]", "campo marcado para preencher"),
    (r"\[\s*INSERIR[^\]]{0,80}\]", "campo marcado para inserir"),
    (r"\[\s*A\s+DEFINIR[^\]]{0,80}\]", "campo a definir"),
    (r"\[\s*NOME[^\]]{0,60}\]", "nome entre colchetes"),
    (r"\bLorem ipsum\b", "texto de rascunho"),
    (r"n[ao]o\s+informad[oa]\s+na\s+transcri[cç][ao]o", "recado da automacao"),
    (r"n[ao]o\s+identificad[oa]", "recado da automacao"),
    (r"grafia\s+incerta", "recado da automacao"),
    (r"necess[aá]rio\s+confirmar", "recado da automacao"),
    (r"\(sobrenome[^)]{0,60}\)", "sobrenome nao resolvido"),
    (r"R\$\s*0,00", "valor zerado"),
    (r"R\$\s*_+", "valor em branco"),
    # 09/11/2026: era LEVE, e leve deixa sair. Contrato com "data a confirmar"
    # nao pode chegar ao cliente. Medido nos 69 documentos reais: nenhum bom
    # usa esta expressao.
    (r"\ba\s+confirmar\b", "diz 'a confirmar'"),
    # Pontuacao orfa de campo que sumiu: "sob n° ," / "CPF: ." / "CEP: ,".
    # O lookahead e obrigatorio: sem ele isto barrava "CT. N°. 325/2026",
    # "n°. 47.672.389/0001-85" e o "CEP. 09750-730" do proprio rodape, que
    # sao legitimos. Ruido ensina a equipe a ignorar o fiscal inteiro
    # ([[fiscal-da-operacao]], regra de calibragem 1).
    (r"n[º°]\s*[,.;](?!\s*\d)", "numero vazio, campo sumiu"),
    (r"CPF\s*(sob\s*)?[nº°]*\s*[,.;](?!\s*\d)", "CPF vazio"),
    (r"CEP\s*:?\s*[,.;](?!\s*\d)", "CEP vazio"),
]

# "XX" tambem e numeral romano. "CLAUSULA XX" e "Capitulo XX" sao legitimos;
# "dia XX de XX" e campo em branco. Entao o XX so conta quando a palavra antes
# dele nao e de numeracao.
_ROMANO_ANTES = re.compile(
    r"(cl[aá]usul[ao]|cap[ií]tulo|t[ií]tulo|se[cç][aã]o|anexo|s[eé]culo|"
    r"inciso|item|parte|livro|tomo|volume|artigo|art\.?|n\.?[º°]?)\s*$",
    re.IGNORECASE)


def _eh_numeral_romano(antes, _m):
    return bool(_ROMANO_ANTES.search(antes or ""))


# Conferidas SEM IGNORECASE: so valem em caixa alta.
MARCAS_GRAVES_CAIXA = [
    (r"\bTODO:", "TODO esquecido no texto"),
    (r"\bXX+\b", "XX de preenchimento", _eh_numeral_romano),
    (r"\bPREENCHER\b", "diz PREENCHER"),
]

# LEVE = sai, mas eu aviso. Serve para o humano decidir.
MARCAS_LEVES = [
    (r"\bsob\s+consulta\b", "diz 'sob consulta'"),
]


# ------------------------------------------------------------ ler o arquivo
_ENTIDADES = (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
              ("&quot;", '"'), ("&apos;", "'"), ("&#13;", " "),
              ("&#10;", " "), ("&#9;", " "))


def _soltar_entidades(t):
    """Entidade XML que fica no texto pode partir uma marca no meio e esconder
    o campo em aberto justamente de quem procura por ele."""
    for de, para in _ENTIDADES:
        t = t.replace(de, para)

    def _num(m):
        try:
            return chr(int(m.group(1)))
        except (ValueError, OverflowError):
            return " "

    return re.sub(r"&#(\d{1,7});", _num, t)


def _texto_docx(caminho):
    z = zipfile.ZipFile(caminho)
    partes = []
    for nome in z.namelist():
        if not (nome.startswith("word/") and nome.endswith(".xml")):
            continue
        if "/theme" in nome or "settings" in nome or "fontTable" in nome:
            continue
        xml = z.read(nome).decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        partes.append(re.sub(r"<[^>]+>", "", xml))
    return _soltar_entidades("\n".join(partes))


def _texto_pdf(caminho):
    """Texto do PDF. Erro de LEITURA e diferente de PDF sem texto: na v1 o
    pdftotext podia falhar, devolver stdout vazio, e o documento era barrado
    como "sem texto", mandando quem lesse o log procurar o problema no lugar
    errado."""
    try:
        r = subprocess.run(["pdftotext", "-layout", caminho, "-"],
                           capture_output=True, timeout=120)
    except Exception as e:
        raise RuntimeError("nao consegui rodar o pdftotext (%s)" % e)
    if r.returncode != 0:
        raise RuntimeError("pdftotext falhou, codigo %s: %s"
                           % (r.returncode,
                              r.stderr.decode("utf-8", "replace").strip()[:120]))
    return r.stdout.decode("utf-8", "replace")


def extrair_texto(caminho):
    ext = os.path.splitext(caminho)[1].lower()
    if ext == ".docx":
        return _texto_docx(caminho)
    if ext == ".pdf":
        return _texto_pdf(caminho)
    if ext in (".txt", ".md"):
        with open(caminho, encoding="utf-8", errors="replace") as f:
            return f.read()
    raise RuntimeError("nao sei ler %s" % ext)


# ------------------------------------------------------------------ o fiscal
def conferir(caminho, texto=None):
    """Devolve (aprovado, achados).

    achados = [{'gravidade','marca','trecho','o_que'}]. Na duvida, barra:
    se nao conseguir LER o arquivo, devolve reprovado, porque documento que
    o fiscal nao leu e documento que ninguem conferiu.
    """
    achados = []
    if texto is None:
        try:
            texto = extrair_texto(caminho)
        except Exception as e:
            return False, [{"gravidade": "GRAVE", "marca": "ilegivel",
                            "trecho": str(e)[:160],
                            "o_que": "o fiscal nao conseguiu ler o arquivo"}]
    if not (texto or "").strip():
        return False, [{"gravidade": "GRAVE", "marca": "vazio", "trecho": "",
                        "o_que": "documento sem texto"}]

    def varrer(regras, gravidade, flags):
        for item in regras:
            regra, o_que = item[0], item[1]
            filtro = item[2] if len(item) > 2 else None
            for m in re.finditer(regra, texto, flags):
                ini = max(0, m.start() - 60)
                if filtro and filtro(texto[max(0, m.start() - 40):m.start()], m):
                    continue
                achados.append({
                    "gravidade": gravidade, "marca": m.group(0)[:60],
                    "trecho": " ".join(texto[ini:m.end() + 60].split())[:150],
                    "o_que": o_que})

    varrer(MARCAS_GRAVES, "GRAVE", re.IGNORECASE)
    varrer(MARCAS_GRAVES_CAIXA, "GRAVE", 0)
    varrer(MARCAS_LEVES, "LEVE", re.IGNORECASE)

    graves = [a for a in achados if a["gravidade"] == "GRAVE"]
    return (not graves), achados


def conferir_bytes(pdf_bytes, nome="documento"):
    """Mesma conferencia, nos bytes do PDF que vao subir ao ZapSign.

    E por aqui que o fiscal entra no portao: dentro de
    `zapsign_integration.criar_documento`, que e o funil por onde TUDO passa.

    **O nome tambem e conferido**, e nao e detalhe: no incidente de 22/09 o
    arquivo se chamava "Heitor (sobrenome nao informado na transcricao) -
    Contrato". O nome aparece para o cliente na tela do ZapSign e no e-mail,
    entao recado de automacao no nome e tao grave quanto no corpo.
    """
    import tempfile
    achados = []

    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        achados.append({"gravidade": "GRAVE", "marca": "", "trecho": "",
                        "o_que": "o documento ia sem nome"})
    else:
        _ok, achados_nome = conferir(None, texto=nome_limpo)
        for a in achados_nome:
            a["o_que"] = "%s (no NOME do arquivo)" % a["o_que"]
        achados += achados_nome

    # Se o PROPRIO fiscal falhar (disco cheio, /tmp sem permissao), ele devolve
    # reprovado com o motivo. Excecao solta aqui estouraria dentro do envio.
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(pdf_bytes or b"")
            tmp.close()
            _ok_corpo, achados_corpo = conferir(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        achados += achados_corpo
    except Exception as e:
        achados.append({"gravidade": "GRAVE", "marca": "fiscal falhou",
                        "trecho": str(e)[:160],
                        "o_que": "o fiscal nao conseguiu conferir o arquivo"})

    graves = [a for a in achados if a["gravidade"] == "GRAVE"]
    return (not graves), achados


LOG_BARRADO = "/var/log/escritorio-fiscal-barrou.log"


def registrar(nome, achados, origem=""):
    """Grava o barramento em log proprio, para nao morrer em print.

    Sem isto, barrar vira documento que simplesmente nao chega e ninguem fica
    sabendo, que e o cenario "some calado". O fiscal diario le este arquivo e
    transforma em tarefa para a Advogada.
    """
    import datetime as dt
    try:
        linha = "%s\t%s\t%s\t%s\n" % (
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            origem or "?", (nome or "?").replace("\t", " "),
            " | ".join(resumo_curto(achados)))
        with open(LOG_BARRADO, "a", encoding="utf-8") as f:
            f.write(linha)
    except Exception:
        pass  # log nao pode derrubar envio nenhum


def resumo_curto(achados):
    """Uma linha por problema, para log e para a mensagem da equipe."""
    vistos, saida = set(), []
    for a in achados:
        if a["gravidade"] != "GRAVE" or a["o_que"] in vistos:
            continue
        vistos.add(a["o_que"])
        saida.append("%s (%s)" % (a["o_que"], a["marca"][:40]))
    return saida


def relatorio(caminho):
    ok, achados = conferir(caminho)
    nome = os.path.basename(caminho)
    graves = [a for a in achados if a["gravidade"] == "GRAVE"]
    leves = [a for a in achados if a["gravidade"] == "LEVE"]
    print("%s  %s" % ("APROVADO " if ok else "BARRADO  ", nome[:66]))
    vistos = set()
    for a in graves:
        chave = (a["o_que"], a["marca"])
        if chave in vistos:
            continue
        vistos.add(chave)
        print("     GRAVE  %-34s %s" % (a["o_que"][:34], a["marca"][:40]))
        print("            ...%s" % a["trecho"][:110])
    if leves and not graves:
        for a in leves[:3]:
            print("     aviso  %-34s %s" % (a["o_que"][:34], a["marca"][:30]))
    return ok


if __name__ == "__main__":
    alvos = sys.argv[1:]
    if not alvos:
        print(__doc__)
        raise SystemExit(0)
    todos_ok = True
    for a in alvos:
        todos_ok = relatorio(a) and todos_ok
        print()
    raise SystemExit(0 if todos_ok else 1)
