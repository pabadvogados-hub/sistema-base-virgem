""
import re

PERGUNTA_DE_TEMPO = re.compile(
    r'(quanto tempo|quando (vai|sai|termina|acaba|resolve|julga|paga)|'
    r'vai demorar|demora muito|ta demorando|esta demorando|demorar mais|'
    r'previs(ao|áo)|prazo (para|pra|de) (terminar|acabar|sair|julgar)|'
    r'falta muito|ainda vai levar|data (para|pra) (terminar|sair))', re.I)

RESPOSTA_TEMPO = (
    'Os processos correm sob a condução da Justiça, e o tempo de cada etapa '
    'depende do cartório, da vara e do juiz que cuidam do seu caso. Por isso '
    'não dá para cravar uma data, e desconfie de quem cravar. '
    'O que posso te dizer com segurança é que a nossa equipe acompanha o seu '
    'processo de perto e cuida de cada prazo que é nosso. '
    'Esperamos que logo esteja tudo resolvido, e assim que houver novidade '
    'você fica sabendo por aqui. Se quiser ver tudo o que já aconteceu, é só '
    'abrir "Ver processo completo".'
)

ESCALAR = [
    (re.compile(r'(voces|vocês|o escritorio|a doutora|a advogada|o advogado)\s*'
                r'(ja|já)?\s*(vao|vão|foi|fez|fizeram|mandou|mandaram|protocol|'
                r'entrou|entraram|respond|peticion)', re.I),
     'cobrou um ato do escritorio'),

    (re.compile(r'(quero|posso|gostaria de|preciso) falar com|falar com (a|o) '
                r'(doutora|advogad|dr)|atendimento|me liga|liga pra mim|'
                r'quero conversar', re.I),
     'pediu para falar com a equipe'),

    (re.compile(r'(nao recebi|não recebi|meu dinheiro|meu pagamento|quando (eu )?recebo|'
                r'honorario|honorário|boleto|cobranca|cobrança|valor a pagar|'
                r'alvara|alvará|levantamento)', re.I),
     'assunto de dinheiro'),

    (re.compile(r'(preciso (mandar|enviar|assinar)|que documento|quais documento|'
                r'mandei o|enviei o|assinar (o|a) )', re.I),
     'assunto de documento'),

    (re.compile(r'(mudar meu|atualizar meu|meu endereco|meu endereço|meu telefone|'
                r'meu email|meu e-mail|trocar meu|corrigir meu)', re.I),
     'quer atualizar cadastro'),

    (re.compile(r'(nao concordo|não concordo|reclamar|reclamacao|reclamação|'
                r'insatisfeit|absurdo|nao esta certo|não está certo|erro no meu|'
                r'ta errado|tá errado|esta errado)', re.I),
     'demonstrou insatisfacao ou apontou erro'),

    (re.compile(r'(desistir|encerrar o processo|acordo|proposta|fazer acordo|'
                r'aceitar (a|o) )', re.I),
     'assunto de acordo ou desistencia'),
]

CONFUSAO = re.compile(
    r'(nao entendi|não entendi|nao entendo|não entendo|nao ficou claro|'
    r'nao sei o que|não sei o que|to perdid|tô perdid|estou perdid|'
    r'explica de novo|explicar melhor)', re.I)


def resposta_pronta(pergunta: str):
    ""
    if PERGUNTA_DE_TEMPO.search(pergunta or ''):
        return RESPOSTA_TEMPO
    return None


def precisa_humano(pergunta: str, resposta: str = ''):
    ""
    p = pergunta or ''
    for rx, motivo in ESCALAR:
        if rx.search(p):
            return True, motivo
    if CONFUSAO.search(p):
        return True, 'nao entendeu a explicacao'
    return False, ''
