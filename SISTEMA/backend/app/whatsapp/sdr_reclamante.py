"""SDR de IA da linha de RECLAMANTES (trabalhador) do Escritório Modelo Advogados.

Independente de canal: recebe o histórico da conversa e devolve a resposta.
Nome configurável por .env (SDR_RECLAMANTE_NOME, padrão "Helena").
Regra da casa: NUNCA recusar trabalhador. Na dúvida, atende e encaminha.
"""
import os
import re
import anthropic
from ..config import settings

NOME = (os.getenv('SDR_RECLAMANTE_NOME') or 'Helena').strip()
ESCRITORIO = (os.getenv('ESCRITORIO_NOME') or 'Escritório Modelo Advogados').strip()
ADVOGADA = (os.getenv('SDR_ADVOGADA_RESPONSAVEL') or 'Dra. Advogada').strip()
RESP_EMPRESAS = (os.getenv('RESPONSAVEL_EMPRESAS') or 'Dr. Titular').strip()

MODELO = os.getenv('SDR_MODELO') or 'claude-haiku-5-5'
MODELO_RESERVA = os.getenv('SDR_MODELO_RESERVA') or 'claude-haiku-4-5'
MODELO_BRIEFING = os.getenv('SDR_MODELO_BRIEFING') or 'claude-sonnet-5'
_CLIENTE = None


def _cliente():
    global _CLIENTE
    if _CLIENTE is None:
        _CLIENTE = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _CLIENTE


SYSTEM_PROMPT = f"""\
Você é a {NOME}, do primeiro atendimento do escritório {ESCRITORIO}, em Cidade/UF, que defende
trabalhadores na Justiça do Trabalho. Você NÃO é advogada. Seu papel é ouvir a história, criar
confiança, explicar com clareza como funciona o caminho trabalhista e levar a pessoa até a equipe
jurídica, liderada pela {ADVOGADA}. Quem analisa e decide o caso é a advogada; você abre a porta.

LINGUAGEM (definida pelo escritório): misto, didático com formal. Educada e respeitosa, sem ser fria.
Trata a pessoa pelo nome, com "você". Explica em linguagem simples o que vai acontecer em cada
passo (conversa inicial, análise da advogada, contrato, documentos, ação, audiência), sem
juridiquês e sem prometer nada.

COMO ESCREVE:
- Português correto, com acentos. Mensagens curtas: no máximo 3 linhas, uma ideia por vez.
- Sem emojis e sem travessão. Sem aspas. Use ponto, vírgula ou dois pontos.
- UMA pergunta por vez, sempre puxando o gancho do que a pessoa acabou de contar.
  Nada de formulário nem de interrogatório. Se ela já contou, não pergunte de novo.

ABERTURA (só na primeira mensagem):
Olá, tudo bem? Aqui é a {NOME}, do {ESCRITORIO}. Vou te ajudar a entender os seus direitos.
Para começar: você ainda está trabalhando na empresa ou já saiu?
POR QUE ASSIM: quem já saiu tem prazo correndo (dois anos para entrar com a ação), então essa é a
primeira coisa a saber. Na segunda mensagem, se já saiu, pergunte QUANDO saiu (mês e ano). Nos dois
casos, pegue o NOME e peça para a pessoa contar o que aconteceu.

TODO TRABALHADOR É ATENDIDO (regra da casa, não negociável):
Se quem chegou é trabalhador, ou foi, com qualquer problema do trabalho, o escritório ATENDE.
Não existe "não é o nosso foco", "isso é com outro setor" nem "procure outro advogado".
Se veio por um anúncio nosso, o assunto do anúncio É causa nossa.
Se você não souber se cabe, não decide: acolhe, ouve e encaminha para a advogada.

ASSUNTOS EM QUE O ESCRITÓRIO MAIS ATUA (a lista não é fechada):
- RESCISÃO INDIRETA: quando a empresa descumpre o contrato (atraso de salário, FGTS não depositado,
  assédio, desvio de função, jornada abusiva, risco à saúde) e o trabalhador pode "sair com todos os
  direitos", como se tivesse sido mandado embora. Se a pessoa ainda está na empresa e está sofrendo,
  esse é o assunto. NÃO oriente a pessoa a pedir demissão nem a parar de ir trabalhar: isso é decisão
  da advogada, depois de analisar.
- ACIDENTE DE TRABALHO e doença ocupacional: pergunte com cuidado se houve afastamento, se a empresa
  abriu CAT e se houve auxílio do INSS. Pode haver estabilidade e indenização, e o escritório trabalha
  em conjunto com a área previdenciária. Acolha primeiro; saúde vem antes do processo.
- FAST-FOOD e SUPERMERCADO: hora extra e banco de horas, intervalo cortado, acúmulo de função (caixa,
  repositor, limpeza), trabalho em domingo e feriado, câmara fria, metas e cobrança abusiva,
  escala 6x1, desconto de quebra de caixa, uniforme.
- TRABALHISTA EM GERAL: carteira não assinada (vínculo), salário por fora, verbas da rescisão não
  pagas, FGTS, adicional noturno, insalubridade e periculosidade, assédio moral, demissão de gestante,
  equiparação salarial.

O QUE PRECISA ENTENDER (no fio da conversa, aos poucos):
1. NOME (e passe a chamar a pessoa pelo nome).
2. SITUAÇÃO: ainda trabalha, quer sair, ou já saiu. Se já saiu: quando (mês/ano) e como
   (pediu as contas, foi mandada embora, acordo, justa causa).
3. VÍNCULO: carteira assinada, sem registro, MEI/PJ. Sem carteira NÃO é problema: pode ser um caso
   forte de reconhecimento de vínculo.
4. TEMPO DE EMPRESA e FUNÇÃO.
5. O PROBLEMA PRINCIPAL, com as palavras dela.
6. PROCESSO ANTERIOR contra essa mesma empresa. Se já existe, diga com tranquilidade que a equipe
   vai verificar antes.
Com nome, situação, vínculo, tempo e o problema: ENCAMINHE. Não fique catando detalhe a mais.

EXPLICAR O CAMINHO (didático): quando for encaminhar, explique em duas ou três mensagens curtas o
que vem agora: a {ADVOGADA} analisa o caso; se tiver viabilidade, o escritório apresenta o contrato,
que é assinado pelo celular; depois a pessoa envia os documentos (RG ou CNH, carteira de trabalho,
comprovante de endereço e o que tiver do trabalho); o escritório prepara e entra com a ação.
Não fale de valores, de prazo de processo nem de honorários: isso é com a advogada.

NOMEAR A DOR: depois de entender, diga com respeito o que a pessoa viveu, em uma frase curta, e
reforce que ela não está sozinha.

QUANDO NÃO SE APLICA (exceção estreita):
Só quando claramente NÃO é trabalhador nem assunto de trabalho: número errado, vendedor, propaganda,
assunto de outra área sem relação com trabalho (família, criminal, consumidor), ou um "oi" solto.
Se for EMPRESA querendo assessoria ou defesa, diga que vai passar para o {RESP_EMPRESAS}, responsável pela
área empresarial do escritório, e pare.
Nos outros casos responda UMA vez, curto e educado, que um atendente do escritório já assume, e
pare. Nunca indique outro advogado ou escritório.
Atenção: dúvida do trabalhador sobre um direito NÃO entra aqui. Caso que parece fraco NÃO entra aqui.
Na dúvida, ATENDE.

LIMITES ÉTICOS (Provimento 205/2021 da OAB, não negociáveis):
Não dá orientação jurídica conclusiva, não cita artigo de lei, não estima valor, prazo ou chance,
não promete resultado, não fala de honorários, não exige documento nesta fase e não manda procurar
outro advogado.
"""

PALAVRAS_TRABALHADOR = (
    "trabalh", "empresa", "patrao", "patrão", "chefe", "gerente", "carteira", "registro", "clt",
    "demiti", "demissao", "demissão", "mandado embora", "mandaram embora", "justa causa", "rescis",
    "aviso previo", "aviso prévio", "fgts", "salario", "salário", "hora extra", "horas extras",
    "banco de horas", "intervalo", "folga", "domingo", "feriado", "escala", "6x1", "acidente",
    "afastad", "inss", "cat ", "doenca", "doença", "assedio", "assédio", "humilh", "acumulo de funcao",
    "acúmulo de função", "desvio de funcao", "desvio de função", "insalubr", "periculos",
    "fast food", "fast-food", "lanchonete", "supermercado", "caixa", "repositor", "atendente",
    "gestante", "gravida", "grávida", "direitos", "processo trabalhista", "advogad",
)


def eh_reclamante(historico):
    """True se a pessoa fala de trabalho. Na dúvida o atendimento acontece igual (regra da casa)."""
    txt = " ".join(h.get("content", "").lower() for h in historico if h.get("role") == "user")
    return any(k in txt for k in PALAVRAS_TRABALHADOR)


# ============================================================
# LEAD DE FORMULÁRIO (campanha com qualificação prévia)
# ============================================================
# Mensagem estruturada do formulário/calculadora de direitos, por exemplo:
#   Olá! Fiz a avaliação dos meus direitos trabalhistas.
#   Estimativa: R$ 18.000
#   CLT: Sim
#   Ainda na empresa: Não
#   Tempo de empresa: 3 ano(s)
#   Horas extras/dia: 2
#   Área: Supermercado
_CAMPOS_FORMULARIO = [
    ('estimativa', r'Estimativa:\s*(.+)'),
    ('clt', r'CLT:\s*(Sim|N[aã]o)'),
    ('ainda_na_empresa', r'Ainda na empresa:\s*(Sim|N[aã]o)'),
    ('tempo_empresa', r'Tempo de empresa:\s*([^\n·]+)'),
    ('horas_extra_dia', r'Horas extras/dia:\s*([\d.,]+)'),
    ('area', r'[ÁA]rea:\s*([^\n·]+)'),
]
_ROTULOS_FORMULARIO = {
    'estimativa': 'Estimativa de valor', 'clt': 'Carteira assinada (CLT)',
    'ainda_na_empresa': 'Ainda na empresa', 'tempo_empresa': 'Tempo de empresa',
    'horas_extra_dia': 'Horas extras/dia', 'area': 'Área/segmento',
}


def eh_lead_formulario(texto):
    low = (texto or '').lower()
    return 'avaliação dos meus direitos' in low or ('avaliacao dos meus direitos' in low) or (
        'estimativa' in low and 'tempo de empresa' in low)


def parse_formulario(texto):
    """Extrai os campos estruturados da mensagem do formulário (dict vazio se não achar)."""
    dados = {}
    for chave, padrao in _CAMPOS_FORMULARIO:
        m = re.search(padrao, texto or '', re.IGNORECASE)
        if m:
            dados[chave] = m.group(1).strip()
    return dados


def _resumo_formulario(dados):
    return "\n".join(f"- {_ROTULOS_FORMULARIO.get(k, k)}: {v}" for k, v in dados.items())


def _extra_pre_qualificado(dados_formulario=None):
    texto = """

LEAD PRÉ-QUALIFICADO PELO FORMULÁRIO (importante):
Esta pessoa já respondeu um formulário com parte da qualificação. NÃO use a abertura padrão nem
pergunte de novo o que ela já informou. Comece confirmando, em uma frase, o que ela trouxe, e pegue
o NOME. Falte o que faltar (se já saiu e quando, como saiu, processo anterior contra a mesma
empresa), pergunte uma coisa por vez. Com isso, nomeie a dor, explique o caminho e encaminhe."""
    if dados_formulario:
        texto += ("\n\nDADOS JÁ CONFIRMADOS PELO FORMULÁRIO (use exatamente estes valores, não "
                  "precisa confirmar de novo):\n" + _resumo_formulario(dados_formulario))
    return texto


def gerar_resposta(historico, pre_qualificado=False, dados_calculadora=None):
    """historico: [{'role': 'user'|'assistant', 'content': '...'}].
    pre_qualificado=True: lead veio do formulário, já chega com parte da qualificação.
    dados_calculadora: dict de parse_formulario(), se houver (nome mantido por compatibilidade)."""
    client = _cliente()
    extra = _extra_pre_qualificado(dados_calculadora) if (pre_qualificado or dados_calculadora) else ''
    try:
        blocos = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
        if extra:
            blocos.append({"type": "text", "text": extra})
        resp = client.messages.create(
            model=MODELO, max_tokens=1200, system=blocos, messages=historico,
            output_config={"effort": "low"},
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if txt and resp.stop_reason in ("end_turn", "stop_sequence"):
            return txt
        print(f"[SDR] motor principal sem resposta útil (stop={resp.stop_reason}), usando reserva", flush=True)
    except Exception as e:
        print(f"[SDR] motor principal falhou ({type(e).__name__}: {e}), usando reserva", flush=True)
    resp = client.messages.create(
        model=MODELO_RESERVA, max_tokens=500, system=SYSTEM_PROMPT + extra, messages=historico,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


# ============================================================
# BRIEFING DE FECHAMENTO (handoff SDR -> quem fecha)
# ============================================================
_CLASSIFIER_PROMPT = f"""\
Você analisa uma conversa entre a {NOME} (primeiro atendimento do {ESCRITORIO}, trabalhista do
trabalhador) e um lead. Diga se o lead JÁ ESTÁ PRONTO PARA O FECHAMENTO, ou seja, se ficou claro
(1) a SITUAÇÃO (ainda trabalha / já saiu), (2) o VÍNCULO, (3) o TEMPO DE EMPRESA e (4) o problema
principal, e a {NOME} já encaminhou ou vai encaminhar para a equipe. Responda APENAS SIM ou NAO."""

_BRIEFING_PROMPT = f"""\
Você prepara um BRIEFING DE FECHAMENTO para a equipe do {ESCRITORIO} (quem vai conduzir a ligação ou
a reunião de fechamento com o trabalhador). Precisa ser tão claro que ATÉ UMA PESSOA SEM EXPERIÊNCIA
consiga conduzir a conversa lendo ele.

CÁLCULO DE TEMPO: você recebe a DATA DE HOJE. Calcule o tempo de empresa e, se a pessoa já saiu, há
quanto tempo saiu (o prazo para entrar com a ação é de 2 anos da saída: se passar de 18 meses, marque
URGENTE). Contrato de experiência dura até 90 dias. Datas confusas: escreva "CONFIRMAR NA LIGAÇÃO".

CONTEXTO DO NEGÓCIO:
- Público: trabalhador (reclamante). Serviço: reclamação trabalhista.
- Focos do escritório: rescisão indireta, acidente de trabalho (com parceria previdenciária),
  fast-food e supermercado, trabalhista em geral.
- Honorários: tratados pela advogada no contrato. NÃO estime valores no roteiro.
- Objetivo: a pessoa entender o caminho e ASSINAR o contrato e a procuração.
- A advogada responsável é a {ADVOGADA}.
- Linguagem: didática com formal, acolhedor, sem juridiquês, sem emojis.
- Ética (Provimento 205/2021 OAB): não prometer resultado, não garantir valor, prazo ou chance.

Na seção 1 use BULLETS CURTOS. As seções 3 (ROTEIRO) e 4 (OBJEÇÕES) devem SEMPRE aparecer completas.

Estrutura (texto puro):

BRIEFING DE FECHAMENTO, {{nome}}

1) RESUMO DO QUE O LEAD CONTOU
- Nome:
- Empresa e segmento (fast-food, supermercado, outro):
- Função:
- Vínculo: (carteira assinada / sem registro / MEI-PJ)
- Tempo de empresa:
- Situação: (ainda na empresa / quer sair / já saiu), se saiu QUANDO e COMO
- Tema principal: (rescisão indireta / acidente de trabalho / horas extras / vínculo / outro)
- Acidente ou doença: (CAT, afastamento, INSS), se houver
- Processo anterior contra essa empresa:
- Temperatura do lead: (frio / morno / quente) e por quê

2) PONTOS FORTES DO CASO
- (2 a 4 ganchos concretos que apareceram na conversa)

3) ROTEIRO DA CONVERSA DE FECHAMENTO (siga na ordem)
1. Abertura e conexão
2. Confirmar a história
3. Nomear a dor, com respeito
4. Apresentar o escritório e a {ADVOGADA}
5. Explicar o caminho trabalhista, passo a passo, de forma didática
6. Conduzir para a assinatura do contrato e da procuração
7. Próximos passos: documentos (RG ou CNH, carteira de trabalho, comprovante de endereço,
   holerites, provas que tiver)

4) OBJEÇÕES PROVÁVEIS + COMO RESPONDER
- (2 a 3, com resposta pronta)

5) FRASES PRONTAS
- (3 a 5 frases curtas, incluindo uma de fechamento)

Regras: use SÓ o que apareceu na conversa; o que faltar, marque "CONFIRMAR NA LIGAÇÃO". Não invente."""


def _transcricao(historico):
    linhas = []
    for h in historico:
        quem = 'LEAD' if h.get('role') == 'user' else NOME.upper()
        linhas.append(f"{quem}: {h.get('content', '')}")
    return "\n".join(linhas)


def foi_encaminhado(historico):
    """True se o lead já está qualificado e pronto para o fechamento."""
    try:
        conteudo = "Analise esta conversa e responda apenas SIM ou NAO:\n\n" + _transcricao(historico)
        resp = _cliente().messages.create(
            model=MODELO_RESERVA, max_tokens=5, system=_CLASSIFIER_PROMPT,
            messages=[{"role": "user", "content": conteudo}],
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip().upper()
        return txt.startswith("SIM")
    except Exception:
        return False


def gerar_briefing_fechamento(historico, nome="o cliente", dados_calculadora=None):
    """Briefing de fechamento (resumo + roteiro) para quem vai fechar.
    A conversa vai como TRANSCRIÇÃO num pedido explícito, para o modelo não 'responder' a conversa."""
    import datetime as _dt
    hoje = _dt.date.today().strftime('%d/%m/%Y')
    extra = ''
    if dados_calculadora:
        extra = ("\n\nDADOS CONFIRMADOS PELO FORMULÁRIO (use estes valores exatos na seção 1):\n"
                 + _resumo_formulario(dados_calculadora) + "\n")
    conteudo = (f"DATA DE HOJE: {hoje}." + extra + "\n\n"
                f"Aqui está a conversa entre a {NOME} (atendimento) e o lead. NÃO responda a conversa: "
                "ANALISE e gere o BRIEFING DE FECHAMENTO COMPLETO, com as 5 seções.\n\n"
                "=== CONVERSA ===\n" + _transcricao(historico) + "\n=== FIM ===\n\n"
                "Gere agora o BRIEFING DE FECHAMENTO completo.")
    msgs = [{"role": "user", "content": conteudo}]
    melhor = ""
    for _ in range(2):
        resp = _cliente().messages.create(
            model=MODELO_BRIEFING, max_tokens=3000,
            system=_BRIEFING_PROMPT.replace('{nome}', nome), messages=msgs,
        )
        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if len(txt) > len(melhor):
            melhor = txt
        if len(txt) >= 600 and 'ROTEIRO' in txt.upper():
            return txt
    return melhor
