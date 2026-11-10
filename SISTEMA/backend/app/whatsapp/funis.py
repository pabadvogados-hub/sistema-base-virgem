"""Funis (quadros) do CRM do Escritório Modelo Advogados — fonte única de nomes, IDs e colunas.

Os IDs são FIXOS de propósito: o seed cria cada quadro com o id abaixo, e os robôs (SYNC/*)
e as telas filtram por esses números. Num banco novo, sem id fixo, o primeiro quadro viraria
o 1 e todo filtro por id apontaria para o lugar errado.

Operação (kickoff 09/11/2026):
- Linha FIXA do escritório  -> RECLAMANTES (trabalhador), atendida pela SDR de IA.
- Número do Dr. Titular        -> EMPRESAS (assessoria corporativa e defesa do empregador).
- Palestra de 09/12/2026    -> funil próprio da Rescisão Indireta (grupo com aquecimento).
"""

# ---------------- IDs ----------------
RECLAMANTES = 10        # entrada e qualificação do trabalhador até o contrato
CLIENTES = 11           # cliente com processo/atendimento em curso
PRODUCAO = 18           # esteira da inicial: documentos -> produção -> revisão -> distribuição
CONTRATACAO = 20        # contrato, documentos e assinatura
EMPRESAS = 30           # linha B2B do Dr. Titular
PALESTRA = 31           # funil da palestra de Rescisão Indireta

# ---------------- Nomes ----------------
NOME = {
    RECLAMANTES: 'Reclamantes',
    CLIENTES: 'Clientes',
    PRODUCAO: 'Produção',
    CONTRATACAO: 'Contratação',
    EMPRESAS: 'Empresas',
    PALESTRA: 'Palestra Rescisão Indireta',
}
PIPE_LEADS = NOME[RECLAMANTES]
PIPE_CLIENTES = NOME[CLIENTES]
PIPE_PRODUCAO = NOME[PRODUCAO]
PIPE_CONTRATACAO = NOME[CONTRATACAO]
PIPE_EMPRESAS = NOME[EMPRESAS]
PIPE_PALESTRA = NOME[PALESTRA]

# ---------------- Colunas de entrada por campanha (Reclamantes) ----------------
# O lead de campanha cai direto na coluna da origem; os robôs tratam todas como entrada.
CAIXA = 'Caixa de Entrada'
CAMPANHA_FAST_FOOD = 'Campanha Fast-food'
CAMPANHA_SUPERMERCADO = 'Campanha Supermercado'
CAMPANHA_ACIDENTE = 'Campanha Acidente de Trabalho'
CAMPANHA_GERAL = 'Campanha Geral'
ETAPAS_ENTRADA = (CAIXA, CAMPANHA_FAST_FOOD, CAMPANHA_SUPERMERCADO, CAMPANHA_ACIDENTE, CAMPANHA_GERAL)

# Origem (campo conversa.origem) -> coluna de entrada.
ORIGEM_PARA_COLUNA = {
    'Campanha Fast-food': CAMPANHA_FAST_FOOD,
    'Campanha Supermercado': CAMPANHA_SUPERMERCADO,
    'Campanha Acidente de Trabalho': CAMPANHA_ACIDENTE,
    'Campanha Geral': CAMPANHA_GERAL,
}

# Frase exclusiva de cada anúncio (mensagem pré-preenchida do clique para o WhatsApp).
# Ajuste aqui quando as campanhas forem criadas; comparação sem acento e em minúsculas.
FRASES_CAMPANHA = {
    'Campanha Fast-food': ('trabalho em fast food', 'trabalhei em fast food', 'lanchonete'),
    'Campanha Supermercado': ('trabalho em supermercado', 'trabalhei em supermercado'),
    'Campanha Acidente de Trabalho': ('acidente de trabalho', 'me acidentei no trabalho', 'sofri um acidente'),
    'Campanha Geral': ('quero saber meus direitos trabalhistas',),
    'Campanha Rescisão Indireta': ('palestra', 'rescisao indireta', 'rescisão indireta'),
}

QUENTE = 'Lead Quente'
QUALIFICADO = 'Lead Qualificado'
DESQUALIFICADO = 'Lead Desqualificado'
CONTRATO = 'Contrato / Assinatura'

COLUNAS = {
    RECLAMANTES: [
        CAIXA, CAMPANHA_FAST_FOOD, CAMPANHA_SUPERMERCADO, CAMPANHA_ACIDENTE, CAMPANHA_GERAL,
        QUENTE, QUALIFICADO, 'Contatado', 'Pré fechamento [fluxo aquecimento]', 'Em Negociação',
        'Pós-Reunião de Fechamento', CONTRATO, 'Ganho', DESQUALIFICADO,
        'Perdido - Sem resposta', 'Perdido - Sem interesse', 'Perdido',
    ],
    CONTRATACAO: [
        'Fechou! Boas vindas e Documentos para contrato', 'Fase de Contratação', 'Contrato no ZapSign',
        'Assinado', 'Ligação: reaquecimento de documentos', 'Perdido',
    ],
    PRODUCAO: ['Aguardando Documentos', 'Producao da Inicial', 'Revisao', 'Distribuicao', 'Distribuida'],
    CLIENTES: ['Novo Cliente', 'Em Atendimento', 'Processo Ativo [Cliente ou funcionario]', 'Resolvido'],
    EMPRESAS: [
        CAIXA, 'Diagnóstico', 'Reunião Marcada', 'Proposta Enviada', 'Negociação', 'Cliente Ativo', 'Perdido',
    ],
    PALESTRA: [
        'Captação', 'No Grupo (aquecimento)', 'Confirmou Presença', 'Assistiu à Palestra', 'Não Compareceu',
        'Triagem', CONTRATO, 'Ganho', 'Perdido',
    ],
}

# Ordem em que aparecem no CRM. O primeiro é o padrão da linha de reclamantes.
ORDEM = [RECLAMANTES, CONTRATACAO, PRODUCAO, CLIENTES, EMPRESAS, PALESTRA]

# Esteira que a automação pode AVANÇAR mesmo com o quadro fixado por gente.
ESTEIRA = [PIPE_LEADS, PIPE_CONTRATACAO, PIPE_PRODUCAO]

# Quadro padrão de cada agente (linha). Conversa nova nasce aqui.
QUADRO_DO_AGENTE = {'reclamante': RECLAMANTES, 'empresas': EMPRESAS}


def coluna_da_origem(origem: str):
    """Coluna de entrada para a origem da campanha, ou None (cai na Caixa de Entrada)."""
    return ORIGEM_PARA_COLUNA.get((origem or '').strip())


def origem_pela_frase(texto: str):
    """Descobre a campanha pela frase do anúncio. None se nenhuma bater."""
    import unicodedata
    t = unicodedata.normalize('NFKD', (texto or '').lower()).encode('ascii', 'ignore').decode()
    for origem, frases in FRASES_CAMPANHA.items():
        for f in frases:
            f2 = unicodedata.normalize('NFKD', f.lower()).encode('ascii', 'ignore').decode()
            if f2 in t:
                return origem
    return None
