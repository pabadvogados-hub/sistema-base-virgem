""
import argparse
import os
import datetime as dt
import re
import sqlite3
import sys
import time

sys.path.insert(0, '/opt/escritorio/SISTEMA/backend')
sys.path.insert(0, '/opt/escritorio')
from app.portal import dados, processo_info
from app.whatsapp import dapi, zapi

DB = '/opt/escritorio/SISTEMA/backend/sistema_esc.db'
LINK = 'https://sistema.escritorio.adv.br/portal'
V = os.getenv('PORTAL_VIDEOS_URL', '').rstrip('/')
VIDEO = {k: (V + '/' + arq if V else None) for k, arq in {
    'portal_pf': 'portal_pessoa_fisica.mp4',
    'portal_pj': 'portal_empresa.mp4',
    'painel_empresa': 'portal_empresa_painel.mp4',
    'custo_empresa': 'custo_empresa.mp4',
    'assistente': 'assistente_como_perguntar.mp4',
    'documentos': 'documentos.mp4',
    'ouvir': 'ouvir.mp4',
    'avisos': 'avisos.mp4',
}.items()}
CASA = re.sub(r'\D', '', os.getenv('WHATSAPP_ESCRITORIO', ''))[-11:]
EQUIPE = re.sub(r'\D', '', os.getenv('AVISO_WHATSAPP_EQUIPE', ''))
INTERVALO = 20
PAUSA_LOTE = 60
LOTE = 25
DIAS_SUMIU = 10
JANELA = (9, 18)

DDL = """CREATE TABLE IF NOT EXISTS portal_regua (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trilha TEXT, passo TEXT, cliente_id INTEGER,
    contato TEXT, celular TEXT, enviado_em REAL, ok INTEGER, info TEXT)"""

GENERICOS = {'contato', 'cliente', 'empresa', 'financeiro', 'rh', 'dp', 'responsavel', 'responsável'}


def t_a1(nome, emp):
    if emp:
        return (f'Oi, {nome}! Aqui é do Escritório Modelo Advogados. A gente montou um lugar onde vocês acompanham os processos '
                f'da {emp} na hora que quiserem, sem precisar perguntar para ninguém: o Portal do Cliente. Neste '
                'vídeo de 1 minuto a gente mostra como entrar. É só o CNPJ e um código que chega aqui no WhatsApp, '
                f'sem senha para guardar. {LINK}')
    return (f'Oi, {nome}! Aqui é do Escritório Modelo Advogados. A gente montou um lugar onde o senhor acompanha o seu processo '
            'sozinho, na hora que quiser, sem precisar perguntar para ninguém: o Portal do Cliente. Neste vídeo de '
            '1 minuto a gente mostra como entrar. É só o seu CPF e um código que chega aqui no WhatsApp, sem senha '
            f'para guardar. {LINK}')


def t_a2(nome, emp):
    if emp:
        return (f'{nome}, no portal vocês também baixam as cópias dos processos, as mesmas que a gente usa aqui. '
                f'Se travar em algum ponto, é só me chamar que eu ajudo. {LINK}')
    return (f'{nome}, no portal o senhor também baixa as cópias do seu processo, as mesmas que a gente usa aqui. '
            f'Fica tudo no seu celular. Se travar em algum ponto, me chama que eu ajudo. {LINK}')


def t_b1(nome, emp):
    if emp:
        return (f'Oi, {nome}! Vocês entraram no portal e de lá para cá os processos andaram. Tem uma novidade: '
                'dentro de cada processo agora tem a Cláudia, que responde dúvidas na hora, a qualquer horário. '
                f'Veja no vídeo como perguntar para ela. {LINK}')
    return (f'Oi, {nome}! O senhor entrou no portal e de lá para cá o seu processo andou. Tem uma novidade: dentro '
            'do seu processo agora tem a Cláudia, que responde dúvidas na hora, a qualquer horário. Veja no vídeo '
            f'como perguntar para ela. {LINK}')


def t_b2(nome, emp):
    quem = 'vocês precisarem' if emp else 'o senhor precisar'
    seu = 'dos processos' if emp else 'do seu processo'
    return (f'{nome}, se {quem} de alguma peça {seu}, ela está no portal, na tela do processo completo. Dá para ler '
            'na hora ou baixar o arquivo original. O vídeo mostra onde fica.')


def t_c1(nome, emp):
    de_voces = 'de vocês' if emp else 'do seu caso'
    return (f'Oi, {nome}! Uma dica rápida do portal: dentro do processo tem a Cláudia, nossa assistente de '
            f'inteligência artificial. Ela responde dúvidas {de_voces} na hora, inclusive de madrugada. Neste vídeo '
            'a gente mostra como perguntar do jeito que traz a melhor resposta.')


def t_c1b(nome, emp):
    return (f'{nome}, se quiser experimentar a Cláudia, essas perguntas funcionam bem:\n'
            '"O que significa o último andamento?"\n'
            '"Quando é a minha audiência?"\n'
            '"O que a empresa alegou na defesa?"\n'
            '"O que o juiz decidiu na última decisão?"\n'
            'É só abrir o processo no portal e tocar em tirar uma dúvida.')


def t_c2(nome, emp):
    return (f'{nome}, o assunto desta semana no portal são os documentos. Todas as peças do processo ficam lá, em '
            'acessar seu processo: dá para ler na hora ou baixar o arquivo original do tribunal. O vídeo mostra onde '
            'fica, em 40 segundos.')


def t_c3(nome, emp):
    return (f'{nome}, dica da semana: se preferir ouvir em vez de ler, o portal lê a explicação do processo em voz '
            'alta, é só tocar em ouvir. E logo abaixo tem o caminho do processo, que mostra em que pé ele está.')


def t_c4(nome, emp):
    return (f'{nome}, quando o juiz marca audiência ou perícia, a data aparece logo na primeira tela do processo, no '
            'portal, junto da explicação do que acontece naquele dia. O escritório continua avisando antes por aqui.')



def _oi(nome):
    ""
    return f'Oi, {nome}! ' if nome and nome != 'tudo bem' else 'Oi! '


def _voc(nome):
    return f'{nome}, ' if nome and nome != 'tudo bem' else ''


def t_e1(nome, emp):
    ""
    quem = f'a {emp}' if emp else 'a empresa'
    return (f'{_oi(nome)}Aqui é do Escritório Modelo Advogados. A gente reuniu num lugar só tudo o que {quem} tem com o '
            'escritório: os processos e em que pé cada um está, as datas de audiência e perícia, quanto o '
            'contencioso custou, os boletos e as frentes da consultoria. Neste vídeo a gente mostra o que tem '
            'em cada tela. Para entrar é o CNPJ da empresa e um código que chega aqui no WhatsApp, sem senha '
            f'para guardar. {LINK}')


def t_e2(nome, emp):
    return (f'{_voc(nome)}tem um bloco novo no painel: o custo do contencioso. Ele mostra quanto cada R$ 100 '
            'pedidos na Justiça custaram de verdade para a empresa, somando o que foi pago no processo, as '
            'custas e perícias, o nosso honorário e as audiências, e dá para abrir ação por ação. Tem também '
            'informações que só vocês têm: elas ficam em destaque na tela esperando a resposta, é só digitar '
            'e tocar em informar que a conta se ajusta sozinha.')


def t_e3(nome, emp):
    return (f'{_voc(nome)}a primeira coisa que aparece no painel são os próximos compromissos: audiência e '
            'perícia, com a data, o lugar e o que precisa levar naquele dia. Logo abaixo ficam os processos, '
            'e dentro de cada um o que aconteceu, explicado sem juridiquês. O escritório continua avisando '
            'vocês antes por aqui; o portal é para quando quiserem olhar sozinhos.')


def t_e4(nome, emp):
    return (f'{_voc(nome)}dentro de cada processo tem a Cláudia, nossa assistente de inteligência artificial. '
            'Ela responde na hora, a qualquer horário, perguntas como "quem da empresa precisa ir na '
            'audiência?", "o que o reclamante está pedindo?" ou "o que a nossa defesa alegou?". O que ela não '
            'puder responder, ela encaminha para a nossa equipe.')


def t_e5(nome, emp):
    return (f'{_voc(nome)}para fechar: no painel também estão a economia já realizada (quanto foi pedido nas '
            'ações encerradas e quanto de fato saiu do caixa), as frentes da consultoria preventiva e os '
            'boletos, com a segunda via na hora. Se faltar alguma coisa aí dentro, me chama que eu ajusto.')


TRILHAS = {
    'C': [('C1', 'assistente', t_c1, 0, None),
          ('C1b', None, t_c1b, 3, 'sem_duvida'),
          ('C2', 'documentos', t_c2, 4, None),
          ('C3', 'ouvir', t_c3, 7, None),
          ('C4', 'avisos', t_c4, 7, None)],
    'A': [('A1', 'portal', t_a1, 0, None),
          ('A2', None, t_a2, 4, 'nao_entrou')],
    'B': [('B1', 'assistente', t_b1, 0, None),
          ('B2', 'documentos', t_b2, 7, None)],
    'E': [('E1', 'painel_empresa', t_e1, 0, None),
          ('E2', 'custo_empresa', t_e2, 3, None),
          ('E3', None, t_e3, 7, None),
          ('E4', 'assistente', t_e4, 7, None),
          ('E5', None, t_e5, 7, None)],
}

TRILHA_EMPRESA_LIGADA = False


def ler_lista(arq):
    try:
        return {re.sub(r'\D', '', l.split('#')[0]) for l in open(arq, encoding='utf-8')
                if re.sub(r'\D', '', l.split('#')[0])}
    except Exception:
        return set()


def com_ddd(tel):
    t = dados.so_digitos(tel or '')
    return ('55' + t) if len(t) in (10, 11) else t


def primeiro(nome):
    p = re.sub(r'\(.*?\)', '', str(nome or '')).split()
    if not p or p[0].lower() in GENERICOS:
        return ''
    return p[0].title()


def numero_wa(con):
    r = con.execute('SELECT * FROM wa_numeros WHERE ativo=1 ORDER BY id LIMIT 1').fetchone()

    class N:
        pass
    n = N()
    for k in r.keys():
        setattr(n, k, r[k])
    return n


def enviar(wa, telefone, video, texto):
    ""
    if video:
        if dapi.eh_dapi(wa):
            ok, info = dapi._post(wa, 'messages/send/video',
                                  {'to': dapi._destino(telefone), 'video': video}, timeout=90)
        else:
            ok, info = zapi._post_zapi(wa, 'send-video', {'phone': telefone, 'video': video}, timeout=90)
        if not ok:
            return ok, info
        time.sleep(4)
        ok2, info2 = zapi.enviar_texto(wa, telefone, texto)
        for _ in range(2):
            if ok2:
                break
            time.sleep(6)
            ok2, info2 = zapi.enviar_texto(wa, telefone, texto)
        if not ok2:
            try:
                EQUIPE and zapi.enviar_texto(wa, EQUIPE, '⚠️ O video foi entregue para ***%s mas o TEXTO que '
                                           'vai junto falhou (%s). Mandar o texto na mao: a pessoa '
                                           'recebeu o video sem explicacao.' % (str(telefone)[-4:],
                                                                                str(info2)[:60]))
            except Exception:
                pass
        return True, f'video={info} texto={info2}' + ('' if ok2 else ' TEXTO FALHOU')
    return zapi.enviar_texto(wa, telefone, texto)


def dia_util_agora():
    a = dt.datetime.now()
    return a.weekday() < 5 and JANELA[0] <= a.hour < JANELA[1]


def falou_recente(con, cid, celular, dias=7):
    ""
    limite = time.time() - dias * 86400
    for sql, par in (('SELECT 1 FROM portal_regua WHERE cliente_id=? AND ok=1 AND enviado_em>?', (cid, limite)),
                     ('SELECT 1 FROM portal_campanha WHERE cliente_id=? AND ok=1 AND enviado_em>?', (cid, limite)),
                     ('SELECT 1 FROM portal_convite WHERE cliente_id=? AND ok=1 AND enviado_em>?', (cid, limite)),
                     ('SELECT 1 FROM portal_nps_convite WHERE cliente_id=? AND ok=1 AND enviado_em>?', (cid, limite))):
        try:
            if con.execute(sql, par).fetchone():
                return True
        except sqlite3.OperationalError:
            pass
    return False


def passos_feitos(con, cid):
    return {r[0]: r[1] for r in con.execute(
        'SELECT passo, MAX(enviado_em) FROM portal_regua WHERE cliente_id=? AND ok=1 GROUP BY passo', (cid,))}


def proximo_passo(trilha, feitos, extra):
    ""
    anterior = None
    for pid, video, texto, espera, cond in TRILHAS[trilha]:
        if pid in feitos:
            anterior = feitos[pid]
            continue
        if anterior is not None and (time.time() - anterior) < espera * 86400:
            return None
        if cond == 'sem_duvida' and extra.get('perguntou'):
            anterior = time.time()
            continue
        if cond == 'nao_entrou' and extra.get('entrou'):
            return None
        return (pid, video, texto)
    return None


def fila(con, so_trilha=None):
    nao_conv = ler_lista('/opt/escritorio/FINANCEIRO/portal_nao_convidar.txt')
    nao_cobrar = ler_lista('/opt/escritorio/FINANCEIRO/clientes_nao_cobrar.txt')
    parceiro = {r[0] for r in con.execute(
        "SELECT DISTINCT cliente_id FROM processo_contato WHERE tipo='cliente_do_parceiro'")}
    com_duvida = {r[0] for r in con.execute('SELECT DISTINCT cliente_id FROM portal_duvida')}
    ultimo = {r[0]: r[1] for r in con.execute(
        "SELECT cliente_id, MAX(criado_em) FROM portal_log WHERE acao IN ('login_ok','abriu_processo','abriu_autos',"
        "'abriu_painel','leu_documento','baixou_documento','duvida','ouviu') GROUP BY cliente_id")}
    convidados = {r[0] for r in con.execute('SELECT DISTINCT cliente_id FROM portal_convite WHERE ok=1')}
    com_acesso = {r[0] for r in con.execute('SELECT DISTINCT cliente_id FROM portal_acesso WHERE ativo=1')}

    from app.portal import inadimplencia
    saida, fora, vistos = [], [], set()
    for cid in sorted(convidados | com_acesso):
        j = con.execute('SELECT nome, documento FROM jur_clientes WHERE id=?', (cid,)).fetchone()
        if not j:
            continue
        doc = dados.so_digitos(j['documento'] or '')
        nome_cli = j['nome'] or ''
        if cid in parceiro:
            fora.append((cid, nome_cli[:24], 'cliente de parceiro')); continue
        if doc in nao_conv:
            fora.append((cid, nome_cli[:24], 'nao convidar')); continue
        if doc in nao_cobrar or str(cid) in nao_cobrar:
            fora.append((cid, nome_cli[:24], 'nao cobrar')); continue
        cli = dados.resolver_cliente(doc)
        if not cli:
            fora.append((cid, nome_cli[:24], 'cadastro nao resolvido')); continue
        try:
            if inadimplencia.situacao(cli).get('inadimplente'):
                fora.append((cid, nome_cli[:24], 'inadimplente')); continue
        except Exception:
            pass
        try:
            if not dados.processos_do_cliente(cli):
                fora.append((cid, nome_cli[:24], 'sem processo')); continue
        except Exception:
            pass
        visto = ultimo.get(cid)
        entrou = visto is not None
        if not entrou:
            trilha = 'A'
        elif (time.time() - visto) > DIAS_SUMIU * 86400:
            trilha = 'B'
        else:
            trilha = 'C'
        feitos = passos_feitos(con, cid)
        extra = {'perguntou': cid in com_duvida, 'entrou': entrou}
        if len(doc) == 14 and (TRILHA_EMPRESA_LIGADA or so_trilha == 'E'):
            passo_e = proximo_passo('E', feitos, extra)
            if passo_e:
                trilha = 'E'
        if so_trilha and trilha != so_trilha:
            continue
        passo = proximo_passo(trilha, feitos, extra)
        if not passo:
            continue
        if falou_recente(con, cid, None):
            fora.append((cid, nome_cli[:24], 'ja recebeu mensagem nossa nesta semana')); continue
        empresa = processo_info.nome_limpo(cli['nome']).title() if len(doc) == 14 else None
        for c in dados.contatos_do_cliente(cli):
            tel = com_ddd(c.get('celular'))
            if len(tel) < 12 or (CASA and tel.endswith(CASA)) or tel in vistos:
                continue
            nome = primeiro(c.get('nome')) if (empresa or not c.get('titular', True)) else primeiro(cli['nome'])
            vistos.add(tel)
            saida.append({'cliente_id': cid, 'trilha': trilha, 'passo': passo[0],
                          'video': VIDEO[('portal_pj' if empresa else 'portal_pf') if passo[1] == 'portal' else passo[1]]
                                   if passo[1] else None,
                          'texto': passo[2](nome or 'tudo bem', empresa), 'nome': nome,
                          'contato': c.get('nome') or '', 'celular': tel, 'empresa': empresa})
            break
    ordem = {'E': 0, 'C': 1, 'B': 2, 'A': 3}
    saida.sort(key=lambda x: (ordem[x['trilha']], x['cliente_id']))
    return saida, fora


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--enviar', action='store_true')
    ap.add_argument('--teste', action='store_true')
    ap.add_argument('--trilha', default=None)
    ap.add_argument('--limite', type=int, default=LOTE)
    ap.add_argument('--agora', action='store_true')
    a = ap.parse_args()
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute(DDL)
    con.commit()
    wa = numero_wa(con)

    if a.teste:
        trilha = a.trilha or 'C'
        emp = 'Empresa Exemplo' if trilha == 'E' else None
        for pid, video, texto, _espera, _cond in TRILHAS[trilha][:2]:
            if not EQUIPE:
                print('defina AVISO_WHATSAPP_EQUIPE no .env para o teste'); return
            ok, info = enviar(wa, EQUIPE, VIDEO[video] if video else None, texto('Advogada', emp))
            print(f'  teste {pid}: {"ok" if ok else "FALHOU " + str(info)[:120]}')
            time.sleep(INTERVALO)
        return

    f, fora = fila(con, a.trilha)
    print(time.strftime('%d/%m %H:%M'), 'regua do portal |', len(f), 'mensagem(ns) na fila')
    for x in f:
        print(f"   {x['trilha']}{'':1}{x['passo']:<4} cliente {x['cliente_id']:<4} "
              f"{('empresa ' + (x['empresa'] or '')[:24]) if x['empresa'] else 'pessoa fisica':34} "
              f"para {x['nome'][:14]:14} ***{x['celular'][-4:]}  {'video' if x['video'] else 'texto'}")
    if fora:
        print('   fora:', fora[:12], '...' if len(fora) > 12 else '')
    f = f[:a.limite]
    if not a.enviar:
        print(f'(nada enviado. {len(f)} iriam agora, ~{len(f) * INTERVALO / 60:.0f} min. use --enviar)')
        return
    if not (a.agora or dia_util_agora()):
        print('fora da janela de envio (dia util, 9h as 18h). nada enviado.')
        return
    for i, x in enumerate(f, 1):
        ok, info = enviar(wa, x['celular'], x['video'], x['texto'])
        con.execute('INSERT INTO portal_regua(trilha,passo,cliente_id,contato,celular,enviado_em,ok,info) '
                    'VALUES(?,?,?,?,?,?,?,?)', (x['trilha'], x['passo'], x['cliente_id'], x['contato'],
                                                x['celular'], time.time(), 1 if ok else 0, str(info)[:200]))
        con.commit()
        print(f"  {i}/{len(f)} {x['passo']} cliente {x['cliente_id']} ***{x['celular'][-4:]}: "
              f"{'ok' if ok else 'FALHOU ' + str(info)[:80]}", flush=True)
        if i < len(f):
            time.sleep(INTERVALO + (PAUSA_LOTE if i % 8 == 0 else 0))


if __name__ == '__main__':
    main()
