# -*- coding: utf-8 -*-
"""Mensagem de retomada escrita UMA A UMA, a partir da conversa de cada lead.

Humana e personalizada, "como se estivessemos pensando unicamente nele". A IA le o que
o proprio trabalhador contou e escreve
uma mensagem so para ele, terminando com a pergunta: ainda esta na empresa ou
ja saiu? Nao pode inventar fato, prometer resultado nem usar travessao.

  --amostra N   escreve N mensagens para leads reais variados e mostra (nao envia)
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
CLAUDE = os.getenv('ESC_CLAUDE_BIN', os.path.expanduser('~/.local/bin/claude'))
AGORA_BR = datetime.utcnow() - timedelta(hours=3)

REGRAS = """Voce escreve mensagens de WhatsApp em nome do escritorio Escritório Modelo Advogados, que defende trabalhadores na Justica do Trabalho.

TAREFA: retomar o contato com um trabalhador que procurou o escritorio ha algumas semanas e perguntar se ele ainda esta na mesma empresa ou se ja saiu.

COMO ESCREVER:
- Como uma pessoa de verdade do escritorio que lembrou DELE especificamente. Ele tem que sentir que a mensagem foi escrita so para ele.
- Cite um ou dois detalhes que ELE MESMO contou (funcao, rotina, escala, o que incomodava, como era tratado), usando o jeito dele de falar. Nunca invente nada que nao esteja na conversa. Se ele nao contou detalhe nenhum, nao invente: diga que ele procurou a gente querendo saber sobre os seus direitos trabalhistas.
- Traga SEMPRE uma frase curta com a ideia de justica para o trabalhador: que o trabalho dele merece respeito e que o escritorio existe para que a justica chegue a quem trabalha. Escreva com suas palavras, varie, nao copie esta frase. Sem exagero, sem dramatizar.
- Trechos marcados com (audio) foram transcritos automaticamente e costumam ter palavras erradas: use so a ideia geral deles, NUNCA cite palavras ou expressoes desses trechos. Se qualquer outro trecho parecer confuso ou sem sentido, ignore.
- Ele ainda NAO e cliente. Nunca diga que o escritorio esta acompanhando, cuidando, analisando ou de olho no caso dele.
- Antes de responder, releia cada frase e pergunte: um brasileiro falaria assim no WhatsApp? Reescreva qualquer frase estranha, forcada ou sem sentido.
- Varie a abertura. Nao comece com "Lembramos de voce" e nao use "Passando pra saber".
- Separe em 2 ou 3 paragrafos curtos, como alguem digitando no WhatsApp.
- Termine com UMA pergunta direta e simples: se ele ainda esta na mesma empresa ou ja saiu.
- Comece com "{saudacao}" e o primeiro nome dele, se houver nome. Diga que e do escritorio Escritório Modelo Advogados.
- Portugues simples, frases curtas, tom de conversa de WhatsApp. De 3 a 5 linhas curtas. No maximo um emoji.
- Use "voce conversou com a gente", nunca "voce conversou comigo".

PROIBIDO:
- Travessao ou hifen como pausa. Use ponto ou virgula.
- Prometer resultado, valor, advogada, prazo ou dizer que ele "tem direito a receber" algo (regra da OAB).
- A palavra "garantir" em qualquer forma. Advogado nao garante nada.
- Citar o nome da empresa dele de forma negativa, acusar a empresa ou pressionar a entrar com processo.
- Citar doenca, problema de saude, familia ou qualquer assunto delicado.
- Mencionar IA, robo, sistema, automacao ou classificacao.
- Formalidade ("Prezado", "Sr.", "atenciosamente").

SE NAO FOR LEAD TRABALHISTA: se pela conversa ficar claro que a pessoa nao e trabalhador procurando seus direitos (e empresa, propaganda, cobranca, banco, numero errado, alguem vendendo algo), responda apenas a palavra PULAR.

Devolva SOMENTE o texto da mensagem, sem aspas e sem explicacao."""

PROIBIDAS = re.compile(r'(—|–| - |garant|com certeza vai|r\$|tem direito a receber|vai receber|vai ganhar|'
                       r'indeniza|processar a|robo|inteligencia artificial|sistema|prezad|atenciosamente|'
                       r'de olho no seu caso|acompanhando o seu caso|cuidando do seu caso)', re.I)
SIGLA_IA = re.compile(r'\bIA\b')   # so a sigla maiuscula: "ia" minusculo e o verbo ir


def primeiro_nome(n):
    n = ''.join(c for c in (n or '') if c.isalpha() or c == ' ')
    p = (n.split() or [''])[0]
    ruins = {'regra', 'lead', 'dr', 'sem', 'nome', 'cliente', 'trabalhador'}
    if len(p) < 3 or len(p) > 14 or p.lower() in ruins:
        return ''
    return p.capitalize()


def contexto(con, cid):
    nome, criado = con.execute('select contato_nome, criado_em from wa_conversas where id=?', (cid,)).fetchone()
    t = con.execute('select temperatura, situacao, violacoes, resumo from lead_termometro where conversa_id=?',
                    (cid,)).fetchone() or ('', '', '[]', '')
    falas = [(o, ('(audio) ' if tp == 'audio' else '') + ' '.join((c or '').split())) for o, tp, c in con.execute(
        "select origem, tipo, conteudo from wa_mensagens where conversa_id=? and origem in ('contato','atendente','ia') "
        "and length(conteudo)>1 order by id", (cid,)) if c]
    dele = [c for o, c in falas if o == 'contato'][-25:]
    semanas = max(1, int((datetime.utcnow() - datetime.strptime(str(criado).split('.')[0].replace('T', ' '),
                                                                  '%Y-%m-%d %H:%M:%S')).days / 7))
    return {'id': cid, 'nome': primeiro_nome(nome), 'nome_bruto': nome, 'semanas': semanas,
            'temp': t[0], 'situacao': t[1], 'violacoes': t[2], 'resumo': t[3],
            'dele': ' | '.join(dele)[-2600:]}


def saudacao():
    h = AGORA_BR.hour
    return 'Bom dia' if h < 12 else 'Boa tarde' if h < 18 else 'Boa noite'


MODELO_ESCRITA = os.getenv('FLUXO_MODELO', 'claude-opus-4-5')
MODELO_LEITURA = os.getenv('FLUXO_MODELO_LEITURA', 'claude-haiku-4-5')


def chamar(prompt, modelo=None, max_tokens=700):
    """Primeiro a API com a chave do escritorio, depois o Claude do servidor.

    O login do CLI expira sozinho e sem aviso (ja aconteceu no meio
    do trabalho), entao ele e o plano B, nao o principal.
    """
    from dotenv import load_dotenv
    load_dotenv('/opt/escritorio/config/.env')
    chave = os.getenv('ANTHROPIC_API_KEY')
    if chave:
        try:
            import anthropic
            r = anthropic.Anthropic(api_key=chave).messages.create(
                model=modelo or MODELO_ESCRITA, max_tokens=max_tokens,
                messages=[{'role': 'user', 'content': prompt}])
            return r.content[0].text.strip()
        except Exception as e:
            print('API falhou (%s), tentando o Claude do servidor' % str(e)[:120])
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)
    try:
        r = subprocess.run([CLAUDE, '-p', prompt, '--model', 'opus', '--tools', '', '--strict-mcp-config'],
                           capture_output=True, text=True, env=env, timeout=180)
        saida = (r.stdout or '').strip()
        return '' if 'Failed to authenticate' in saida else saida
    except Exception:
        return ''


# quem falou pouco recebe mensagem parecida com a do vizinho, e texto repetido e
# sinal de spam para o WhatsApp: cada mensagem sorteia um jeito de comecar
ANGULOS = [
    'Comece perguntando como ele esta, se a rotina anda mais tranquila.',
    'Comece dizendo que passou pelo contato dele hoje e lembrou dele.',
    'Comece falando do tempo que passou desde a conversa que ele teve com a gente.',
    'Comece direto, sem rodeio, dizendo a que veio.',
    'Comece reconhecendo que a gente ficou de retomar e esta retomando agora.',
    'Comece com uma saudacao curta e va logo ao ponto, com leveza.',
]


def escrever(ctx, tentativa=1):
    import random
    regras = REGRAS.replace('{saudacao}', saudacao())
    regras += '\n\nJEITO DE ABRIR ESTA MENSAGEM: %s Escreva diferente de qualquer modelo pronto.' \
              % random.choice(ANGULOS)
    if ctx.get('revisita'):
        # revisita de 60 dias: ele ja respondeu que ainda trabalhava
        r = ctx['revisita']
        regras += ('\n\nESTA MENSAGEM E UMA REVISITA. Em %s ele respondeu que ainda estava na mesma empresa '
                   '(o que ele escreveu: "%s"). Retome lembrando, com leveza, que da ultima vez ele contou que '
                   'ainda estava la, e pergunte se continua na mesma empresa ou se ja saiu. Nao copie a resposta '
                   'dele palavra por palavra, nao cobre e nao pressione.'
                   % (r.get('quando') or 'algumas semanas atras', (r.get('resposta') or '')[:200]))
    pedido = ("%s\n\nDADOS DO TRABALHADOR\nPrimeiro nome: %s\nProcurou o escritorio ha cerca de %d semana(s).\n"
              "Resumo do caso feito pelo escritorio: %s\nO QUE ELE MESMO ESCREVEU NA CONVERSA (em ordem):\n%s\n"
              % (regras, ctx['nome'] or '(sem nome, nao use nome)', ctx['semanas'], ctx['resumo'] or '-',
                 ctx['dele'] or '(quase nada)'))
    msg = chamar(pedido).strip().strip('"').strip()
    if not msg:
        return '', 'llm_indisponivel'
    if msg.upper().strip(' .') == 'PULAR':
        return '', 'nao_e_lead'
    problema = conferir(msg)
    if problema and tentativa < 2:
        return escrever(ctx, tentativa + 1)
    return msg, problema


def conferir(msg):
    if not msg:
        return 'vazia'
    if PROIBIDAS.search(msg):
        return 'palavra proibida: %s' % PROIBIDAS.search(msg).group(0)
    if SIGLA_IA.search(msg):
        return 'mencionou IA'
    # pode terminar com emoji depois da pergunta
    if not re.sub(r'[^\w?]+$', '', msg.strip()).endswith('?'):
        return 'nao termina com a pergunta'
    if 'Escritório Modelo' not in msg:
        return 'nao diz que e do escritorio'
    if len(msg) > 650:
        return 'longa demais (%d)' % len(msg)
    return ''


def amostra(n):
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=20)
    perfis = [
        ("com caso contado, quente", "t.temperatura='quente' and t.violacoes not in ('','[]')"),
        ("com caso contado, ainda trabalhando", "t.situacao='trabalhando' and t.violacoes not in ('','[]')"),
        ("morno, nao disse se saiu", "t.temperatura='morno' and t.situacao='nao_informado'"),
        ("frio, falou pouco", "t.temperatura='frio' and t.situacao='nao_informado' and t.violacoes in ('','[]')"),
        ("frio com algum relato", "t.temperatura='frio' and t.violacoes not in ('','[]')"),
        ("contato mais antigo", "t.situacao in ('nao_informado','trabalhando') and v.criado_em < date('now','-60 day')"),
    ][:n]
    for rotulo, cond in perfis:
        cid = con.execute(
            "select v.id from wa_conversas v join lead_termometro t on t.conversa_id=v.id "
            " where v.pipeline_id=10 and t.situacao<>'saiu' and t.temperatura<>'sem_caso' "
            " and v.etapa not in ('Perdido','Perdido - Sem resposta','Perdido - Sem interesse',"
            "'Lead Desqualificado','Novo Cliente','Contrato / Assinatura') "
            " and (select count(*) from wa_mensagens m where m.conversa_id=v.id and m.origem='contato') >= 3 "
            " and %s order by random() limit 1" % cond).fetchone()
        if not cid:
            continue
        ctx = contexto(con, cid[0])
        msg, prob = escrever(ctx)
        print('=' * 72)
        print('PERFIL: %s | lead #%d | %s | %s' % (rotulo, ctx['id'], ctx['nome_bruto'], ctx['situacao']))
        print('o que ele contou (trecho): %s' % ctx['dele'][:260])
        print('-' * 72)
        print(msg)
        print('-' * 72)
        print('conferencia: %s' % (prob or 'OK'))
    con.close()


def por_ids(ids):
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=20)
    for cid in ids:
        ctx = contexto(con, cid)
        msg, prob = escrever(ctx)
        print('=' * 72)
        print('lead #%d | %s' % (ctx['id'], ctx['nome_bruto']))
        print('-' * 72)
        print(msg)
        print('-' * 72)
        print('conferencia: %s' % (prob or 'OK'))
    con.close()


if __name__ == '__main__':
    if '--ids' in sys.argv:
        por_ids([int(x) for x in sys.argv[sys.argv.index('--ids') + 1].split(',')])
    elif '--amostra' in sys.argv:
        amostra(int(sys.argv[sys.argv.index('--amostra') + 1]))
