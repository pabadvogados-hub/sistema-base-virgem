""
import re

MIN_CORPO_PECA = 900


def _corpo(markdown: str) -> str:
    ""
    t = (markdown or '').strip()
    if t.startswith('---'):
        fim = t.find('---', 3)
        if fim > 0:
            t = t[fim + 3:]
    return re.sub(r'\n{3,}', '\n\n', t).strip()


def achar_peca_inicial(eventos, ler_documento):
    ""
    docs = [e for e in eventos
            if e.get('tipo') == 'documento' and e.get('tem_texto') and e.get('documento_id')]
    docs.sort(key=lambda d: d.get('data') or '')

    def rotulo(d):
        return ((d.get('tipo_documento') or '') + ' ' + (d.get('nome') or '')).upper()

    preferidos = [d for d in docs if 'INICIAL' in rotulo(d)]
    peticoes = [d for d in docs if 'PETI' in rotulo(d)]

    for grupo in (preferidos, peticoes):
        for d in grupo:
            try:
                texto = _corpo(ler_documento(d['documento_id']))
            except Exception:
                continue
            if len(texto) >= MIN_CORPO_PECA:
                return d, texto
    return None, ''


def escolher_movimento_relevante(eventos, traduzir):
    ""
    rotina_depois = 0
    for e in eventos:
        if not (e.get('descricao') or e.get('nome')):
            continue
        t = traduzir(e)
        if t.get('relevancia') in ('marco', 'normal'):
            return e, t, rotina_depois
        rotina_depois += 1
    return None, None, rotina_depois


SYS_CASO = (
    'Voce escreve para o cliente de um escritorio de advocacia brasileiro, pessoa '
    'leiga em direito, explicando o processo DELE.\n'
    'REGRAS:\n'
    '- No maximo 4 frases curtas.\n'
    '- Portugues simples. Sem juridiques, sem citar artigo de lei, sem travessao.\n'
    '- Escreva do ponto de vista de quem le. Diga o que ELE quer nesse processo e '
    'por que ele entrou com isso, ou do que ele esta se defendendo.\n'
    '- Comece pelo que esta em jogo para ele, nao pelo nome tecnico da acao.\n'
    '- NUNCA afirme de forma categorica. Em materia juridica nao se afirma. '
    'Use margem UMA vez: "ao que tudo indica" ou "pelo que esta registrado no '
    'processo". Isso protege o escritorio se a leitura automatica errar, e deixa '
    'espaco para a advogada corrigir.\n'
    '- NUNCA escreva "autos", "juizo", "manifestacao", "pleitear" nem "ajuizar": '
    'quem le e trabalhador ou dono de empresa pequena.\n'
    '- NAO cite valor em reais, nem o valor da causa: o cliente le esse numero '
    'como dinheiro garantido (15/09/2026).\n'
    '- Use so o que estiver nos dados e no texto da peca. Nao invente fato, valor '
    'nem data.\n'
    '- Nao prometa resultado, nao estime prazo, nao de conselho juridico.\n'
    '- Trate a pessoa por voce.'
)

SYS_MOV = (
    'Voce explica ao cliente de um escritorio de advocacia brasileiro, pessoa leiga, '
    'o que aconteceu de mais recente no processo dele.\n'
    'REGRAS:\n'
    '- No maximo 3 frases curtas. Portugues simples, sem juridiques, sem travessao.\n'
    '- Diga o que aconteceu e, principalmente, O QUE ISSO MUDA PARA ELE.\n'
    '- NUNCA afirme de forma categorica. Em materia juridica nao se afirma. '
    'Use margem UMA vez: "ao que tudo indica" ou "pelo que esta registrado no '
    'processo". Isso protege o escritorio se a leitura automatica errar, e deixa '
    'espaco para a advogada corrigir.\n'
    '- NUNCA escreva "autos", "juizo" nem "manifestacao". NAO cite valor em reais.\n'
    '- A PRIMEIRA frase diz o que aconteceu, curta: a tela mostra essa frase em '
    'destaque, como manchete (tela simples, 15/09/2026).\n'
    '- Se ele precisa fazer algo, diga com todas as letras. Se nao precisa fazer '
    'nada, diga isso claramente, porque a duvida e o que faz o cliente ligar.\n'
    '- NUNCA use as expressoes "ato ordinatorio", "mero expediente", "portal '
    'eletronico", "certidao de publicacao" nem "juntada". Traduza o efeito.\n'
    '- Nao invente. Nao prometa resultado nem estime prazo.\n'
    '- Trate a pessoa por voce.'
)


def pergunta_caso(numero, capa, etapa, polo, nome_cliente, texto_peca, ultimo_ato):
    ""
    linhas = [
        f'Processo: {numero}',
        f'Tipo de acao: {capa.get("classe") or "nao informado"}',
        f'Assunto: {capa.get("assunto") or "nao informado"}',
        f'Quem move: {capa.get("polo_ativo") or "nao informado"}',
        f'Contra quem: {capa.get("polo_passivo") or "nao informado"}',
        f'Tribunal: {capa.get("tribunal") or ""} {capa.get("orgao_julgador") or ""}'.strip(),
    ]
    if capa.get('data_distribuicao'):
        linhas.append(f'Entrou na justica em: {str(capa["data_distribuicao"])[:10]}')
    linhas.append(f'Etapa no escritorio: {etapa or "nao informada"}')
    if ultimo_ato:
        linhas.append(f'Ultimo ato relevante: {ultimo_ato}')

    quem = (f'QUEM VAI LER: {nome_cliente}, que neste processo e a parte '
            f'{polo or "envolvida"}.')

    corpo = 'Dados do processo:\n' + '\n'.join(linhas) + '\n\n' + quem
    if texto_peca:
        corpo += ('\n\nTrecho da peca que abriu o processo (use para entender o que '
                  'a pessoa pede e por que):\n' + texto_peca[:9000])
    return corpo


def pergunta_movimento(evento, traduzido, texto_doc, rotina_depois, polo):
    ""
    data = (evento.get('data') or '')[:10]
    bruto = (evento.get('descricao') or evento.get('nome') or '').strip()
    partes = [
        f'Registro do tribunal em {data}: {bruto}',
        f'Leitura tecnica: {traduzido.get("titulo")} — {traduzido.get("explicacao")}',
        f'A pessoa que vai ler e a parte {polo or "envolvida"} no processo.',
    ]
    if rotina_depois:
        partes.append(
            f'Depois disso houve {rotina_depois} registro(s) apenas burocraticos '
            '(publicacao, intimacao automatica, juntada). Nao vale mencionar essa '
            'burocracia; se nada de pratico aconteceu desde entao, diga que o '
            'processo segue andando normalmente.')
    if texto_doc:
        partes.append('Teor do documento:\n' + texto_doc[:8000])
    return '\n\n'.join(partes)
