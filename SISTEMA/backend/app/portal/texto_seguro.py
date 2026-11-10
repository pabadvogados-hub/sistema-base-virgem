""
import re

MARCAS_DE_INSTRUCAO = re.compile(
    r'escreva apenas|#+\s*resposta\s*:|responda em at[eé]\s+\d|'
    r'explica[cç][aã]o profissional|pergunta do cliente\s*:|quem vai ler\s*:|'
    r'dados do processo\s*:|trecho da pe[cç]a que abriu|aviso do sistema|'
    r'^\s*regras\s*:|voc[eê] [eé] (um|uma) (assistente|modelo)|\bsystem prompt\b|'
    r'</?(instru|context|resposta|pergunta)[a-z_]*>',
    re.I | re.M)

LETRAS_SOLTAS = re.compile(r'(?:(?<!\S)\S{1,2}[ \t]{2,}){5,}\S{0,2}')


def limpar_entrada(txt: str) -> str:
    ""
    if not txt:
        return ''
    linhas = [ln for ln in txt.splitlines() if not MARCAS_DE_INSTRUCAO.search(ln)]
    t = '\n'.join(linhas)
    t = LETRAS_SOLTAS.sub(' ', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'^\s*\.{3}\s*$', '', t, flags=re.M)
    return re.sub(r'\n{3,}', '\n\n', t).strip()


def aproveitar(txt: str) -> str:
    ""
    if not txt:
        return txt
    m = list(re.finditer(r'#+\s*resposta\s*:', txt, re.I))
    if m:
        txt = txt[m[-1].end():]
    return txt.strip()


def problema(txt: str):
    ""
    if not txt or len(txt.strip()) < 40:
        return 'veio vazia ou curta demais'
    t = txt.strip()
    if MARCAS_DE_INSTRUCAO.search(t):
        return 'veio com instrucao interna copiada junto (por exemplo "Escreva apenas" ou "# Resposta")'
    if LETRAS_SOLTAS.search(t):
        return 'veio com letras soltas copiadas de um documento mal lido'
    if re.match(r'^(\.{2,}|[a-z])', t):
        return 'comeca quebrada, no meio de uma frase'
    if re.search(r'\n\s*\.{3}\s*\n', t):
        return 'tem reticencias soltas, sinal de trecho de documento copiado'
    return None


PALAVRAS_DIFICEIS = re.compile(
    r'\b(autos|m[eé]rito|exordial|manifesta[çc][ãa]o|deferiment\w*|indeferi\w*|procediment\w*|'
    r'ato ordinat[óo]rio|mero expediente|conclusos|tr[âa]nsito em julgado|polo (ativo|passivo)|'
    r'peti[çc][ãa]o inicial|intima[çc][ãa]o|intimad[oa]s?|impugna[çc][ãa]o|ju[ií]zo|exequente|'
    r'executad[oa]|reclamante|reclamad[oa]|lit[ií]gio|certid[ãa]o|juntad[oa])\b', re.I)

TROCAS_SUGERIDAS = ('intimacao = aviso oficial da Justica; merito = o pedido principal; certidao = registro '
                    'da secretaria; peticao inicial = o pedido que abriu o processo; impugnacao = contestacao; '
                    'ato ordinatorio = andamento de rotina da secretaria; procedimento = passo')


def palavras_dificeis(txt: str):
    return sorted({m.group(0).lower() for m in PALAVRAS_DIFICEIS.finditer(txt or '')})


def pedido_de_palavras_simples(palavras) -> str:
    return ('\n\n' + '=' * 40 + '\nAVISO: a resposta anterior usou palavra que o cliente nao entende: '
            + ', '.join(palavras[:5]) + '. Escreva de novo o mesmo conteudo SEM essas palavras, trocando por '
            'palavras do dia a dia (' + TROCAS_SUGERIDAS + '). Devolva apenas o texto novo.')


def pedido_de_correcao(motivo: str) -> str:
    return ('\n\n' + '=' * 40 + '\nAVISO: a resposta anterior foi RECUSADA porque '
            + motivo + '. Escreva de novo somente o texto para o cliente, em frases '
            'completas, sem repetir nenhuma instrucao e sem copiar trecho do documento.')
