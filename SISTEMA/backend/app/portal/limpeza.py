""
import re

_FONTES = re.compile(r'(?:;\s*){4,}')
_CHAVE_VALOR = re.compile(r'^[a-z][a-z0-9_]{3,40}\s+\S')
_HEX = re.compile(r'^[0-9a-f]{80,}$', re.I)
_ESTILO = re.compile(r'^[^.!?:]{1,60};$')
_MERGEFIELD = re.compile(r'\\?\*\s*MERGEFIELD\s+"[^"]*"\s*')
_WPTOOLS = re.compile(r'\\?\*\s*(WPTools|ultimo_auto_salvamento)\S*.*$', re.I)
_IMAGEM = re.compile(r'\\?\*\s*INCLUDEPICTURE\s+"[^"]*"\s*MERGEFORMAT[\\\s]*|'
                     r'\\?\*\s*brasoes[^\s]*\.bmp\s*', re.I)


def _e_entulho(linha: str) -> bool:
    l = linha.strip()
    if not l:
        return True
    if _FONTES.search(l) or _HEX.match(l):
        return True
    if _CHAVE_VALOR.match(l) and ' ' not in l.split(' ', 1)[0]:
        return True
    if _ESTILO.match(l):
        return True
    return False


def _parece_texto(linha: str) -> bool:
    ""
    l = _IMAGEM.sub('', _MERGEFIELD.sub('', linha)).strip()
    if len(l) < 12:
        return False
    if _e_entulho(l):
        return False
    if l.endswith(';'):
        return False
    letras = sum(1 for c in l if c.isalpha())
    return letras >= 8 and letras / len(l) > 0.5


def limpar_documento(texto: str) -> str:
    ""
    if not texto:
        return texto

    linhas = texto.split('\n')

    inicio = 0
    for i, l in enumerate(linhas):
        if _parece_texto(l):
            inicio = i
            break
    else:
        return texto.strip()

    limpo = linhas[inicio:]

    limpo = [_WPTOOLS.sub('', _IMAGEM.sub('', _MERGEFIELD.sub('', l))) for l in limpo]
    limpo = [l for l in limpo if not _HEX.match(l.strip())]

    saida, vazias = [], 0
    for l in limpo:
        if l.strip():
            vazias = 0
            saida.append(l.rstrip())
        else:
            vazias += 1
            if vazias == 1:
                saida.append('')
    return '\n'.join(saida).strip()
