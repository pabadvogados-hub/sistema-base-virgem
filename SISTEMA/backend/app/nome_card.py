# -*- coding: utf-8 -*-
"""O nome do cliente no card: quando o documento sabe mais que o WhatsApp.

07/11/2026, a partir do Galdino: "sempre que for recebido card e se tiver nome, a
gente ja incluiu o nome ou, pelo menos, depois que fechou, ja incluiu o nome do cliente,
porque a gente ja tem o nome dele".

E verdade: a esteira do GR le a CNH que o cliente manda e guarda nome, CPF, RG e endereco em
`qualificacao`. O card do CRM nunca era atualizado, entao o quadro mostrava "Jr", "empresa" ou
o proprio telefone enquanto o sistema sabia "BENICIO DE LIMA JUNIOR".

DECISOES DELA (07/11/2026)
- o nome fica em CAIXA ALTA, como vem da CNH;
- trocar NAO gera tarefa, so registro interno;
- corrigir os que ja existem e deixar o automatico ligado.

POR QUE EXISTE A REGRA DO "NOME FRACO"
Trocar nome de card as cegas e pior que nao trocar: o apelido no card ja travou a producao da
inicial uma vez, e o nome de uma EMPRESA no card de um processo nao pode virar o nome do
motorista so porque os dois tem "Silva". Entao o documento so vence quando o que esta la e
reconhecidamente fraco, ou quando e um pedaco do nome do documento. Nome forte e diferente
fica como esta: ali ha algo a entender, nao a sobrescrever.
"""
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

# Palavras que nao ajudam a identificar ninguem, e por isso nao contam na comparacao.
LIGACOES = ('de', 'da', 'do', 'das', 'dos', 'e')
SUFIXOS = ('junior', 'jr', 'filho', 'neto', 'sobrinho', 'segundo', 'terceiro')
# Nome que nao e nome: o card ficou com um rotulo generico em vez da pessoa.
GENERICOS = ('empresa', 'cliente', 'lead', 'contato', 'motorista', 'caminhoneiro',
             'desconhecido', 'sem nome', 'novo', 'teste', 'whatsapp', 'usuario')

ORIGENS = ('cnh', 'contrato', 'cadastro', 'conversa', 'manual')


class NomeInvalido(ValueError):
    """Candidato que nao serve como nome de pessoa."""


def _sa(s):
    return ''.join(c for c in unicodedata.normalize('NFD', (s or ''))
                   if unicodedata.category(c) != 'Mn')


def _agora():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _palavras(nome):
    """As palavras que identificam a pessoa: sem acento, sem ligacao, sem sufixo, sem lixo."""
    cru = _sa(nome or '').lower()
    cru = re.sub(r'[^a-z\s]', ' ', cru)             # tira emoji, numero, pontuacao
    fora = []
    for p in cru.split():
        if len(p) < 3 or p in LIGACOES or p in SUFIXOS:
            continue
        fora.append(p)
    return fora


def _so_letras(nome):
    return re.sub(r'[^A-Za-z]', '', _sa(nome or ''))


def nome_fraco(nome):
    """O que esta no card nao identifica ninguem?

    Fraco e: vazio, so numero, so emoji, ate tres letras ("Jr"), ou um rotulo generico
    ("empresa"). Com nome fraco, qualquer nome de documento e melhor.
    """
    n = (nome or '').strip()
    if not n:
        return True
    letras = _so_letras(n)
    if not letras:                                   # telefone, emoji, "✌🏼"
        return True
    if len(letras) <= 3:                             # "Jr", "Zé"
        return True
    if _sa(n).strip().lower() in GENERICOS:
        return True
    # apelido do WhatsApp com numero colado ("wellintonbarbosa1707", "zakasantos9"):
    # nome de gente nao tem digito no meio. Achado no ensaio de 07/11/2026, no card do
    # Wellinton, que a regra anterior deixava passar por ter mais de tres letras.
    if re.search(r'[A-Za-z]', n) and re.search(r'[0-9]', n):
        return True
    if not _palavras(n):                             # so ligacoes e sufixos
        return True
    return False


def nome_ilegivel(nome):
    """Mais estrito que `nome_fraco`: o card nao permite reconhecer NINGUEM.

    A diferenca existe por causa do fiscal (07/11/2026). `nome_fraco` trata "Cicero Perucci
    Cliente - Novo Junho 26" como fraco, porque tem digito, e esta certo para DECIDIR SE O
    DOCUMENTO VENCE: o nome da CNH e melhor que esse. Mas para APONTAR "cliente fechado sem
    nome" seria mentira, porque ali da para saber quem e a pessoa.

    Ilegivel e so: vazio, numero puro, emoji e nome com ate tres letras.
    """
    n = (nome or '').strip()
    if not n:
        return True
    letras = _so_letras(n)
    if not letras or len(letras) <= 3:
        return True
    return _sa(n).strip().lower() in GENERICOS


def e_pedaco_de(atual, candidato):
    """`atual` e um pedaco do nome do documento? ("Eli Lopes" dentro de "ELI RESENDE LOPES")

    Todas as palavras que identificam no nome atual precisam aparecer no candidato. Assim
    "Anselmo Júnior" cede para "FRANCISCO ANSELMO SILVA DORNELES" (Júnior e sufixo, nao conta),
    mas "Transportes Silva Ltda" NAO cede para "JOAO SILVA", porque "transportes" e "ltda" nao
    estao la: sao duas entidades diferentes, e trocar seria apagar informacao.
    """
    a, b = _palavras(atual), _palavras(candidato)
    if not a or not b:
        return False
    return all(p in b for p in a) and len(b) > len(a)


def arrumar(nome):
    """O nome como ele fica no card: CAIXA ALTA, sem espaco sobrando (07/11/2026)."""
    n = re.sub(r'\s+', ' ', (nome or '').strip())
    return n.upper()


def serve_como_nome(candidato):
    """O candidato e nome de gente? Duas palavras de verdade, nada de numero solto."""
    if not candidato or nome_fraco(candidato):
        return False
    return len(_palavras(candidato)) >= 2


def melhor_nome(atual, candidato):
    """O nome que o card deve mostrar, ou None quando nao se mexe.

    Troca quando o que esta la e fraco, ou quando e um pedaco do nome do documento. Nome forte
    e diferente do documento NAO e trocado: ali ha algo a entender (outro titular, empresa,
    responsavel), e a maquina nao decide isso sozinha.
    """
    if not serve_como_nome(candidato):
        return None
    novo = arrumar(candidato)
    if _so_letras(atual).lower() == _so_letras(novo).lower():
        return None                                  # mesmo nome, so mudaria a caixa
    if nome_fraco(atual) or e_pedaco_de(atual, candidato):
        return novo
    return None


# --------------------------------------------------------------------------- banco

def criar_esquema(con):
    """A tabela do historico do nome. Idempotente."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS wa_nome_hist (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conversa_id INTEGER NOT NULL,
          de_nome TEXT,
          para_nome TEXT NOT NULL,
          fonte TEXT NOT NULL,
          quem TEXT NOT NULL,
          criado_em TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_nome_hist_conversa
          ON wa_nome_hist (conversa_id, id DESC);
    """)
    con.commit()


def _nome_do_card(con, cid):
    r = con.execute("select coalesce(contato_nome,'') from wa_conversas where id=?",
                    (cid,)).fetchone()
    return r[0] if r else None


def ja_trocou_para(con, cid, novo):
    """Este card ja recebeu este mesmo nome antes? (para a passada ser idempotente)"""
    r = con.execute("select 1 from wa_nome_hist where conversa_id=? and para_nome=? limit 1",
                    (cid, novo)).fetchone()
    return bool(r)


def atualizar_nome(con, cid, candidato, fonte, quem='sistema', nota=True):
    """Poe o nome do documento no card, se ele for melhor. Devolve o nome novo ou None.

    Registro interno e so isso: uma linha em `wa_nome_hist` e uma nota na conversa, que e como
    o sistema ja anota troca de quadro. NAO abre tarefa (07/11/2026).
    """
    if fonte not in ORIGENS:
        raise NomeInvalido('fonte desconhecida: %r' % (fonte,))
    atual = _nome_do_card(con, cid)
    if atual is None:
        return None
    novo = melhor_nome(atual, candidato)
    if not novo or ja_trocou_para(con, cid, novo):
        return None
    try:
        con.execute('UPDATE wa_conversas SET contato_nome=? WHERE id=?', (novo, cid))
        con.execute('INSERT INTO wa_nome_hist (conversa_id, de_nome, para_nome, fonte, quem, '
                    'criado_em) VALUES (?,?,?,?,?,?)',
                    (cid, atual or None, novo, fonte, quem, _agora()))
        if nota:
            # midia_url e remote_id sao NOT NULL na tabela de verdade. Descoberto em
            # 07/11/2026 rodando contra o banco real: o teste usava um esquema reduzido e
            # deixou passar. O helper dos testes agora copia as colunas obrigatorias.
            con.execute(
                "INSERT INTO wa_mensagens (conversa_id, origem, autor, tipo, conteudo, "
                "midia_url, criado_em) VALUES (?, 'nota', 'sistema', 'texto', ?, '', ?)",
                (cid, 'Nome do card atualizado de "%s" para "%s", pelo nome que veio %s.'
                 % (atual or '(vazio)', novo, FONTE_POR_EXTENSO.get(fonte, fonte)), _agora()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return novo


FONTE_POR_EXTENSO = {
    'cnh': 'na CNH enviada pelo cliente',
    'contrato': 'no contrato assinado',
    'cadastro': 'do cadastro juridico',
    'conversa': 'da resposta do cliente na conversa',
    'manual': 'de quem digitou',
}


def aplicar_do_documento(cid, nome_doc, db_path, fonte='cnh', log=None):
    """A porta que as ESTEIRAS usam: abre o banco, garante o esquema e aplica.

    Existe para a regra viver num lugar so. Sem ela, a esteira do GR e a do trabalhista
    teriam cada uma a sua copia de "abre, garante, chama", e duas copias viram duas regras
    no dia em que alguem mexer em uma.

    NUNCA levanta: falhar aqui nao pode parar a esteira, porque o contrato do cliente vale
    mais do que o nome bonito no quadro. O que deu errado vai para o `log`.
    """
    avisar = log or (lambda _m: None)
    if not nome_doc:
        return None
    try:
        con = sqlite3.connect(db_path, timeout=60)
        con.execute('pragma busy_timeout=60000')
        try:
            criar_esquema(con)
            novo = atualizar_nome(con, int(cid), nome_doc, fonte)
        finally:
            con.close()
        if novo:
            avisar('      nome do card, pelo documento: %s' % novo)
        return novo
    except Exception as e:
        avisar('      (nao consegui por o nome do documento no card: %s: %s)'
               % (type(e).__name__, str(e)[:90]))
        return None


def historico(con, cid):
    return [{'id': r[0], 'de_nome': r[1], 'para_nome': r[2], 'fonte': r[3], 'quem': r[4],
             'criado_em': r[5]}
            for r in con.execute(
                'select id, de_nome, para_nome, fonte, quem, criado_em from wa_nome_hist '
                'where conversa_id=? order by id desc', (cid,))]
