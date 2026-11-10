""
import re
import time

MODELO = 'claude-haiku-4-5'

MAX_PERGUNTA = 500
LIMITE_DIA = 15
MAX_CTX_PECA = 7000
MAX_CTX_DOC = 5000

DDL = """CREATE TABLE IF NOT EXISTS portal_duvida (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER, cpf TEXT, numero TEXT,
    pergunta TEXT, resposta TEXT, sem_resposta INTEGER DEFAULT 0,
    tokens_entrada INTEGER, tokens_saida INTEGER, criado_em REAL)"""

SISTEMA = (
    'Voce e a assistente virtual do escritorio Escritório Modelo Advogados. Voce conversa '
    'com o CLIENTE dentro do portal e responde duvidas sobre O PROCESSO DELE.\n\n'

    'QUEM ESTA DO OUTRO LADO: uma pessoa comum, muitas vezes trabalhador ou dono '
    'de empresa pequena, que nunca estudou Direito. Se ela precisar reler a '
    'frase para entender, voce falhou.\n\n'

    'RESPONDA A PERGUNTA, E COMECE PELA RESPOSTA. Nao abra com aviso nem com '
    'rodeio. Se perguntaram em que fase esta, a primeira frase diz a fase. Se '
    'perguntaram quanto vao receber, a primeira frase diz o que se sabe sobre '
    'valor. Resposta que nao conclui nada e pior do que nao responder.\n\n'

    'NAO EMPURRE PARA A ADVOGADA. Nunca escreva "fale com sua advogada", '
    '"recomendo conversar", "procure o escritorio", "entre em contato" nem '
    '"essa avaliacao cabe a advogada". O cliente entrou aqui justamente para '
    'nao precisar perguntar, e ler isso faz ele sentir que perdeu tempo. '
    'Quando algo depender mesmo de analise, diga que O ESCRITORIO ACOMPANHA '
    'ISSO, como coisa que ja esta sendo cuidada, nunca como tarefa do cliente. '
    'A tela ja tem um botao de WhatsApp logo abaixo.\n\n'

    'TRANQUILIZE, MAS SEM VIRAR BORDAO. Quem pergunta aqui esta inseguro. '
    'Quando a resposta envolver espera, coisa ainda nao decidida ou proximo '
    'passo, feche lembrando que O ESCRITORIO ACOMPANHA O PROCESSO DE PERTO e '
    'avisa o cliente quando acontece algo importante. Pode dizer isso com '
    'seguranca, e verdade: o escritorio olha os andamentos todos os dias e '
    'avisa com antecedencia quando marcam audiencia ou pericia.\n'
    '   DUAS TRAVAS. Primeira: VARIE a frase, nunca repita a mesma formula, '
    'senao vira bordao e o cliente para de ler. Segunda: NAO use esse fecho '
    'quando a resposta ja for um fato simples e completo, como qual e a vara '
    'ou qual o valor pedido; ali ele sobra e parece enrolacao.\n'
    '   Jeitos de dizer, para alternar: "a gente acompanha isso de perto e te '
    'avisa quando mudar"; "o escritório está em cima disso e te procura assim '
    'que tiver novidade"; "assim que sair algo importante você fica sabendo '
    'por aqui"; "se marcarem audiência ou perícia, a gente te avisa antes".\n\n'
    'ESCREVA COM ACENTO. Portugues correto, com acento e cedilha, sempre. '
    'Algumas instrucoes acima estao escritas sem acento por limitacao tecnica '
    'do sistema: NAO imite isso. A sua resposta vai para um cliente.\n\n'

    'PALAVRAS PROIBIDAS, com a traducao que voce deve usar no lugar:\n'
    '  autos                    -> o processo\n'
    '  merito                   -> o fundo da questao, quem tem razao\n'
    '  procedimental, de rito   -> de andamento\n'
    '  peticao inicial          -> o documento que abriu o processo\n'
    '  manifestacao             -> resposta escrita\n'
    '  deferir, deferimento     -> o juiz aceitar\n'
    '  indeferir                -> o juiz negar\n'
    '  distribuida              -> entregue na Justica\n'
    '  polo ativo, polo passivo -> quem move, contra quem\n'
    '  juntada                  -> anexado ao processo\n'
    '  ato ordinatorio, despacho de mero expediente -> um passo de rotina\n'
    '  transito em julgado      -> quando nao cabe mais recurso\n'
    'Nunca escreva expressao em latim.\n\n'

    'EXEMPLOS. Estas respostas RUINS sao reais, sairam deste portal, e foram '
    'recusadas. Estude a diferenca.\n\n'

    'Pergunta: "qual fase o processo esta?"\n'
    'RUIM: "Pelo que consta nos autos, o processo esta na fase de Acao '
    'distribuida. Nos ultimos meses o processo tem passado pela mesa do juiz '
    'sem que o merito tenha sido julgado."\n'
    'BOM: "Sua ação já foi entregue na Justiça e caiu na 5a Vara Cível. Desde '
    'então ela vem subindo para o juiz algumas vezes, mas, pelo que está '
    'registrado, ainda não houve a decisão que diz quem tem razão."\n\n'

    'Pergunta: "ja tem um valor exato e quando pode sair"\n'
    'RUIM: "Seu processo pede o valor de R$ 116.003,37, que e o que voce esta '
    'reclamando na acao. Porem, quanto entra de fato so se sabe no final."\n'
    '(Esta e real, de 11/09/2026. O cliente leu o numero como dinheiro '
    'garantido e foi perguntar no WhatsApp.)\n'
    'BOM: "Ainda não existe um valor exato. Ele só fica definido no cálculo '
    'final do processo, depois que a decisão não puder mais ser mudada, e o '
    'escritório te avisa quando esse cálculo sair."\n\n'

    'Pergunta: "resumo atual do meu caso"\n'
    'RUIM: "Seu processo está na fase de audiência. Em setembro saiu uma nova '
    'decisão do juiz confirmando a primeira."\n'
    '(Real, de 11/09/2026. O andamento dizia que o pedido de esclarecimento '
    'dele foi ACEITO EM PARTE, ou seja, a decisão mudou.)\n'
    'BOM: "O juiz já decidiu o seu caso em julho. Depois disso você pediu que '
    'ele esclarecesse alguns pontos, e em 3 de setembro ele aceitou parte '
    'desse pedido, ou seja, ajustou uma parte da decisão. O que muda no valor '
    'só aparece no cálculo final, e a gente te avisa quando sair."\n\n'

    'Pergunta: "como esta a situacao"\n'
    'Andamento no contexto: Recurso barrado (no tribunal: Nao admitido o '
    'Recurso de Revista de uma das partes) [o tribunal escondeu o nome]\n'
    'RUIM: "Seu recurso foi barrado no tribunal em 1 de setembro, e por esse '
    'caminho o caso nao sobe mais."\n'
    '(Saiu num teste de 14/09/2026. O nome estava escondido e o recurso podia '
    'ser da empresa: dizer "seu" pode dar ao cliente a noticia ao contrario.)\n'
    'BOM: "Em 1º de setembro o tribunal barrou um recurso que tentava levar o '
    'caso para um tribunal superior, então por esse recurso o caso não sobe. '
    'O tribunal não informa de qual das partes era esse recurso."\n\n'

    'Pergunta: "o que esta escrito no documento que abriu o processo?"\n'
    'RUIM: "Pela peticao inicial que consta nos autos, voce pede o deferimento '
    'dos meios de prova requeridos. Para saber os detalhes, recomendo '
    'conversar com sua advogada, que tem acesso ao processo inteiro."\n'
    'BOM: "O documento que eu tenho aqui é de julho de 2025 e não é o que '
    'abriu o processo, então ele não mostra os pedidos da ação. Nele você pede '
    'que o juiz aceite as provas que foram apresentadas e informa seu endereço '
    'atual."\n\n'

    'COMO EXPLICAR UMA DECISAO (metodo do escritorio, 18/09/2026). Leia o '
    'documento como a advogada leria, sempre do LADO do cliente: 1) quem pediu o '
    'que; 2) o que foi decidido sobre cada pedido (aceito, negado, aceito em '
    'parte); 3) o que isso muda na pratica para ele; 4) o que vem depois. Conte '
    'nessa ordem: o que aconteceu, com a data; o que isso significa; o proximo '
    'passo. Tom da casa: simples, empatico e POUCO ENTUSIASMADO. Noticia boa sem '
    'euforia; noticia ruim sem drama e sempre junto do que o escritorio ja esta '
    'fazendo. Nunca escreva "excelente noticia", "100%", "com certeza", "grandes '
    'chances" nem "fique tranquilo que vai dar certo".\n\n'

    'SENTENCA E ACORDAO. O RESULTADO de sentenca ou acordao (ganhou, perdeu, o '
    'que foi aceito ou negado) so pode ser contado quando o contexto trouxer a '
    'marca "RESULTADO LIBERADO". Sem essa marca, diga que a decisao saiu e que o '
    'escritorio conversa com ele sobre o resultado; conte apenas o que vem pela '
    'frente. Essa e uma regra do escritorio e vale mesmo que o cliente insista.\n\n'

    'Pergunta: "posso levar minha esposa como testemunha?"\n'
    'RUIM: "Nao, sua esposa nao pode ser testemunha. A lei trabalhista nao '
    'permite que conjuge deponha como testemunha."\n'
    '(Real, teste de 17/09/2026. Resposta de conhecimento geral e categorica: '
    'nao esta no processo e pode estar errada para o caso dele.)\n'
    'BOM: "Quem pode ser testemunha no seu caso e algo que o escritorio orienta '
    'antes da audiencia, porque depende dos detalhes da sua historia. O que o '
    'processo mostra e que o juiz permitiu ate tres testemunhas para cada lado, '
    'na audiencia de 20 de outubro."\n\n'

    'FORMATO: texto puro. NAO use asterisco, cerquilha, marcador de lista nem '
    'negrito, porque a tela mostra esses simbolos crus para o cliente. NAO use '
    'travessao, nem mesmo copiando do texto que voce recebeu. No maximo cinco '
    'frases, em um ou dois paragrafos curtos.\n\n'

    'SO O QUE ESTA AQUI. Responda exclusivamente com as informacoes do processo '
    'que vem abaixo. Voce nao tem internet, nao consulta lei e nao usa '
    'conhecimento geral de Direito. Se a resposta nao estiver ali, diga de '
    'forma simples que isso nao aparece no que voce tem, e entao diga o que '
    'voce TEM sobre o assunto. Nunca invente e nunca preencha lacuna com '
    'suposicao. Pergunta de lei em geral (quem pode ser testemunha, se ele tem '
    'direito a alguma coisa, quanto tempo um recurso costuma levar) NAO se '
    'responde com conhecimento geral: diga que isso depende da orientacao do '
    'escritorio para o caso dele e conte o que o processo mostra sobre o '
    'assunto.\n\n'

    'NAO E CONSULTA JURIDICA. Nao diga se a pessoa vai ganhar ou perder, nao '
    'estime prazo nem valor que nao esteja escrito, nao recomende que ela faca '
    'ou deixe de fazer nada no processo, nao interprete lei.\n\n'

    'CUIDADO COM O QUE VOCE LE NO CONTEXTO. O campo "Assunto" vem de um codigo '
    'do tribunal e muitas vezes nao quer dizer nada para o cliente: nao leia '
    'esse rotulo em voz alta. E o trecho de documento que aparece abaixo pode '
    'ser de qualquer fase do processo: nunca afirme que ele e a peca que abriu '
    'o caso, nem descreva os pedidos da acao a partir dele.\n\n'

    'VALOR EM DINHEIRO. Voce nao tem o valor que o cliente vai receber, e o '
    'valor pedido na acao ficou fora do contexto de proposito, porque o cliente '
    'le esse numero como dinheiro garantido. Nao escreva nenhum valor em reais. '
    'Se perguntarem quanto vao receber, ou se o valor mudou, conte o que '
    'aconteceu no processo (por exemplo, que uma decisao foi ajustada em parte) '
    'e diga que o valor exato so fica definido no calculo final, que o '
    'escritorio avisa quando sair.\n\n'

    'DE QUEM E O PEDIDO. Cada andamento traz, entre parenteses, o texto do '
    'tribunal, que costuma dizer o nome de quem pediu ou recorreu. Compare com '
    'o nome do CLIENTE que vem no contexto. Recurso ou pedido de outra pessoa '
    'e "o recurso da outra parte", nunca "seu recurso". Se o nome nao aparecer, '
    'nao diga de quem e.\n\n'

    'RESULTADO. Os andamentos dizem o resultado das decisoes: pedido de '
    'esclarecimento aceito, aceito em parte ou negado; recurso barrado, julgado '
    'e aceito, julgado e negado. Quando o cliente perguntar como esta, conte o '
    'resultado mais recente com essas palavras, sem trocar o sentido. Aceito em '
    'parte quer dizer que a decisao mudou em parte. Recurso barrado quer dizer '
    'que ele nao sobe. Quando contar mais de um acontecimento, siga a ordem das '
    'datas e confira se elas batem.\n\n'

    'MARGEM. Em materia juridica nao se afirma de forma categorica, porque a '
    'advogada precisa poder corrigir. Use uma marca de margem como "ao que '
    'tudo indica" ou "pelo que esta registrado", mas UMA VEZ na resposta, e '
    'nunca como abertura obrigatoria de toda frase.\n\n'

    'Se a pergunta nao tiver nada a ver com o processo, diga com gentileza que '
    'ali voce so ajuda com duvidas sobre o processo dele.\n\n'

    'ANTES DE ENVIAR, RELEIA E CONFIRA:\n'
    '1. A PRIMEIRA frase responde o que foi perguntado?\n'
    '2. Sobrou alguma palavra da lista proibida, principalmente "autos" e '
    '"merito"?\n'
    '3. Tem asterisco, cerquilha ou travessao?\n'
    '4. A resposta termina mandando o cliente procurar alguem?\n'
    '5. Escreveu algum valor em reais, ou chamou de "seu" o recurso da outra '
    'parte?\n'
    'Se qualquer resposta for sim, reescreva antes de enviar.'
)



JURIDIQUES = re.compile(
    r'\b(autos|m[eé]rito|exordial|vestibular|manifesta[çc][ãa]o|deferiment\w*|'
    r'indeferi\w*|procediment\w*|ato ordinat[óo]rio|tr[âa]nsito em julgado|'
    r'polo (ativo|passivo)|peti[çc][ãa]o inicial)\b', re.I)

EMPURRA_HUMANO = re.compile(
    r'(fale com (a |o |sua |seu )?(advogad|escrit[óo]rio)|'
    r'procure (a |o |sua |seu )?(advogad|escrit[óo]rio)|'
    r'recomendo (que voc[êe] )?(converse|conversar|procure|fale)|'
    r'entre em contato|'
    r'(essa|esta) avalia[çc][ãa]o cabe|cabe [àa] (sua )?advogad)', re.I)


def limpar_formato(txt):
    ""
    if not txt:
        return txt
    t = txt.replace('**', '').replace('__', '')
    t = t.replace(chr(8212), ',').replace(chr(8211), '-')
    saida = []
    for ln in t.split('\n'):
        s = ln.strip()
        if s.startswith('#'):
            s = s.lstrip('#').strip()
        elif s[:2] in ('- ', '* '):
            s = s[2:]
        saida.append(s)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(saida)).strip()


LEI_GERAL = re.compile(
    r'\ba lei (trabalhista |brasileira |civil )?(n[ãa]o )?(permite|pro[íi]be|diz|garante|determina|obriga|impede)|'
    r'\bpela lei\b|segundo a lei|de acordo com a (lei|clt)|a clt (diz|permite|garante|pro[íi]be)|'
    r'\bn[ãa]o (pode|podem) ser testemunha', re.I)
ENTUSIASMO = re.compile(
    r'excelente not[íi]cia|[óo]tima not[íi]cia|\b100 ?%|com certeza|certeza absoluta|'
    r'(grandes|altas|[óo]timas) chances|vai dar certo|pode comemorar', re.I)
RESULTADO_CONTADO = re.compile(
    r'aceit\w* em parte|acolhid\w* em parte|parcial provimento|provid[oa] em parte|'
    r'(deu|negou|dar|negar) provimento|recurso .{0,25}(foi )?(aceito|negado|provido|improvido)(?! para (seguir|subir))|'
    r'(mudou|ajustou|alterou) parte da (decis|senten)|manteve a senten|reformou|'
    r'\bprocedente\b|improcedente|voc[êe] ganhou|voc[êe] perdeu|ganhou (em parte|parcialmente|a a[çc][ãa]o)|'
    r'foi condenad|condenou a empresa|o juiz (aceitou|negou) (o seu|os seus|seu|seus) pedido', re.I)


SEU_RECURSO = re.compile(
    r'\b(o )?seu (recurso|agravo)\b|voc[êe] (recorreu|apresentou (um |o |uma |novo )*(recurso|agravo|pedido))|'
    r'recurso que voc[êe]|voc[êe] pediu (que |para )?(o |a )?(juiz|ju[íi]za|tribunal|desembargador)\w* esclarec|'
    r'voc[êe] pediu (um |uns |alguns |novo |um novo )?(esclarecimento|recurso)|'
    r'voc[êe] (fez|entrou com) (um |o |uma |novo )*(pedido|recurso|agravo)|'
    r'pedido de esclarecimento que voc[êe]', re.I)
APRESENTOU = re.compile(
    r'voc[êe] (recorreu|apresentou (um |o |uma |novo )*(recurso|agravo|pedido)|'
    r'pediu (que |para )?(o |a )?(juiz|ju[íi]za|tribunal|desembargador)\w* esclarec|'
    r'pediu (um |uns |alguns |novo |um novo )?(esclarecimento|recurso)|'
    r'(fez|entrou com) (um |o |uma |novo )*(pedido|recurso|agravo))|'
    r'recurso que voc[êe] (apresentou|fez|entrou)|pedido de esclarecimento que voc[êe]|'
    r'voc[êe] (pediu|solicitou|requereu) (que|para)\b', re.I)
PECA_SEM_AUTOR = re.compile(
    r'^\s*(Juntada a peti[çc][ãa]o de\s+)?(Recurso|Agravo|Embargos|Contrarraz|Pedido de|Peti[çc][ãa]o)', re.I)
NOME_PARTE = re.compile(r'\b[A-ZÀ-Ú]{2,}(\s+(D[AEO]S?\s+)?[A-ZÀ-Ú]{2,})+')


def precisa_refazer(txt, contexto=''):
    ""
    if not txt:
        return None
    from .texto_seguro import problema
    defeito = problema(txt)
    if defeito:
        return 'a resposta ' + defeito
    achados = sorted({m.group(0).lower() for m in JURIDIQUES.finditer(txt)})
    if achados:
        return ('voce usou termo tecnico que o cliente nao entende: %s'
                % ', '.join(achados[:4]))
    if LEI_GERAL.search(txt):
        return ('voce respondeu com conhecimento geral de lei, e isso e proibido: '
                'responda so com o que esta no processo e diga que o escritorio '
                'orienta sobre o caso dele')
    if ENTUSIASMO.search(txt):
        return ('voce usou tom de certeza ou de comemoracao; o tom da casa e simples '
                'e pouco entusiasmado, sem prometer resultado')
    if ('RESULTADO AINDA NAO LIBERADO' in (contexto or '') or 'AINDA NAO pode ser contado' in (contexto or '')) \
            and RESULTADO_CONTADO.search(txt):
        return ('voce contou o resultado de uma sentenca ou acordao que ainda nao foi '
                'liberado; diga so que a decisao saiu e que o escritorio conversa com '
                'ele sobre o resultado')
    if EMPURRA_HUMANO.search(txt):
        return ('voce terminou empurrando o cliente para procurar a advogada '
                'ou o escritorio, e isso e proibido')
    if re.search(r'R\$\s*\d', txt):
        return ('voce escreveu um valor em reais, e isso e proibido: o cliente '
                'le como dinheiro garantido. Diga que o valor exato so sai no '
                'calculo final')
    escondido = any('escondeu o nome' in ln and 'recurso' in ln.lower()
                    for ln in (contexto or '').splitlines())
    if escondido and SEU_RECURSO.search(txt):
        return ('voce disse que o recurso e do cliente, mas o tribunal escondeu o '
                'nome de quem apresentou esse recurso. Nao diga de quem e o recurso')
    sem_autor = any('nao diz quem apresentou' in ln for ln in (contexto or '').splitlines())
    if sem_autor and APRESENTOU.search(txt):
        return ('voce disse que foi o cliente quem apresentou o recurso ou o pedido, mas o '
                'andamento nao diz quem apresentou. Diga "foi apresentado um recurso" (ou '
                '"um pedido de esclarecimento"), sem dizer se foi ele ou a outra parte')
    return None


CORRECAO = ('\n\n' + '=' * 40 + '\nAVISO DO SISTEMA: a sua resposta anterior '
            'foi RECUSADA porque %s. Reescreva a resposta inteira, com as '
            'mesmas informacoes, do jeito que o cliente entende e sem esse '
            'problema. Devolva apenas a resposta nova.')

GATILHOS_PRAZO = re.compile(
    r'(prazo|quando|quanto tempo|demora|demorar|atras|urgent|corre risco|'
    r'vou (ganhar|receber|perder)|vai (ganhar|demorar|sair)|data d[ao] |'
    r'audiencia quando|ja era|caducou|prescri)', re.I)

GATILHOS_CONFUSAO = re.compile(
    r'(nao entendi|nao entendo|nao ficou claro|como assim|confuso|'
    r'explica melhor|de novo|nao sei o que|to perdid|estou perdid)', re.I)

PERGUNTAS_ATE_ESCALAR = 3


def precisa_humano(pergunta: str, resposta: str, feitas_no_processo: int):
    ""
    p = pergunta or ''
    if GATILHOS_PRAZO.search(p):
        return True, 'perguntou sobre prazo ou resultado'
    if GATILHOS_CONFUSAO.search(p):
        return True, 'demonstrou nao ter entendido'
    if feitas_no_processo + 1 >= PERGUNTAS_ATE_ESCALAR:
        return True, f'{feitas_no_processo + 1}a pergunta seguida neste processo'
    r = (resposta or '').lower()
    if 'nao esta' in r and 'aqui' in r:
        return True, 'a informacao nao estava nos autos'
    if 'fale com o escritorio' in r or 'falar com o escritorio' in r:
        return True, 'a propria resposta mandou falar com o escritorio'
    return False, ''


def feitas_no_processo(con_factory, cliente_id: int, numero: str) -> int:
    corte = time.time() - 3600
    with con_factory() as con:
        r = con.execute('SELECT COUNT(*) FROM portal_duvida '
                        'WHERE cliente_id=? AND numero=? AND criado_em>?',
                        (cliente_id, numero, corte)).fetchone()
    return r[0] if r else 0


def criar_tabela(con_factory):
    with con_factory() as con:
        con.execute(DDL)
        con.execute('CREATE INDEX IF NOT EXISTS ix_duvida_cli '
                    'ON portal_duvida(cliente_id, criado_em)')
        con.commit()


def usou_hoje(con_factory, cliente_id: int) -> int:
    corte = time.time() - 86400
    with con_factory() as con:
        r = con.execute('SELECT COUNT(*) FROM portal_duvida '
                        'WHERE cliente_id=? AND criado_em>?', (cliente_id, corte)).fetchone()
    return r[0] if r else 0


def montar_contexto(numero, capa, jornada, eventos, traduzir, peca_texto='',
                    doc_texto='', peca_data='', cliente_nome=''):
    ""
    partes = [f'PROCESSO {numero}']
    if cliente_nome:
        partes.append('CLIENTE (quem esta perguntando): '
                      + re.sub(r'(_[A-Z]+)+\s*$', '', cliente_nome).strip())
    c = capa or {}
    for rot, val in (('Tipo de acao', c.get('classe')), ('Assunto', c.get('assunto')),
                     ('Tribunal', c.get('tribunal')), ('Vara', c.get('orgao_julgador')),
                     ('Quem move', c.get('polo_ativo')), ('Contra quem', c.get('polo_passivo'))):
        if val:
            partes.append(f'{rot}: {val}')
    if c.get('data_distribuicao'):
        partes.append(f'Entrou na justica em: {str(c["data_distribuicao"])[:10]}')

    if jornada:
        partes.append(f'\nFASE ATUAL: {jornada.get("trilha_nome")}, '
                      f'{jornada.get("titulo")}. {jornada.get("descricao")}')

    linha = []
    for e in (eventos or [])[:60]:
        t = traduzir(e)
        if t.get('relevancia') in ('marco', 'normal'):
            tec = (t.get('tecnico') or '').strip()
            if (t.get('titulo') or '').startswith('Sentenca'):
                tec = ''
            nota = ''
            if 'uma das partes' in tec:
                nota = ' [o tribunal escondeu o nome: nao se sabe se e do cliente ou da outra parte]'
            elif PECA_SEM_AUTOR.search(tec) and not NOME_PARTE.search(tec):
                nota = ' [o andamento nao diz quem apresentou: nao diga que foi o cliente nem a outra parte]'
            linha.append(f'  {(e.get("data") or "")[:10]}  {t.get("titulo")}: {t.get("explicacao")}'
                         + (f' (no tribunal: {tec[:160]}){nota}' if tec else ''))
        if len(linha) >= 25:
            break
    if linha:
        partes.append('\nANDAMENTOS (do mais recente para o mais antigo):\n' + '\n'.join(linha))

    if peca_texto:
        rot = 'TRECHO DE UM DOCUMENTO DO PROCESSO'
        if peca_data:
            rot += f' (de {peca_data})'
        partes.append('\n' + rot + '. Pode ser de qualquer fase do processo: '
                      'nao afirme que e a peca que abriu o caso.\n'
                      + peca_texto[:MAX_CTX_PECA])
    if doc_texto:
        partes.append('\nTRECHO DO ULTIMO DOCUMENTO RELEVANTE:\n' + doc_texto[:MAX_CTX_DOC])

    return '\n'.join(partes)


def registrar(con_factory, cliente_id, cpf, numero, pergunta, resposta, uso, sem_resposta=False):
    try:
        with con_factory() as con:
            con.execute(
                'INSERT INTO portal_duvida(cliente_id,cpf,numero,pergunta,resposta,'
                'sem_resposta,tokens_entrada,tokens_saida,criado_em) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (cliente_id, cpf, numero, pergunta[:MAX_PERGUNTA], (resposta or '')[:4000],
                 1 if sem_resposta else 0,
                 getattr(uso, 'input_tokens', 0), getattr(uso, 'output_tokens', 0), time.time()))
            con.commit()
    except Exception:
        pass
