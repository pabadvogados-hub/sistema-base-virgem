import os
import sys
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    # so 'drive': o token atual e emitido so com esse escopo e ele BASTA para a
    # Docs API (batchUpdate aceita — confirmado 07/08/2026); pedir escopo a mais
    # derruba o refresh com invalid_scope.
    'https://www.googleapis.com/auth/drive',
]

def autenticar_google():
    """
    Autentica com OAuth2 (conta pessoal do usuario) se oauth_credentials.json existir.
    Caso contrario, usa Service Account como fallback.
    """
    # Buscar credenciais na pasta config/ (raiz do projeto)
    project_root = os.path.join(os.path.dirname(__file__), '..')
    config_dir = os.path.join(project_root, 'config')
    oauth_creds_path = os.getenv("GOOGLE_OAUTH_CREDENTIALS", os.path.join(config_dir, "oauth_credentials.json"))
    token_path = os.path.join(config_dir, "token.json")
    sa_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.join(config_dir, "credentials.json"))

    creds = None

    # Tenta OAuth2 primeiro (conta pessoal - usa cota do usuario)
    if os.path.exists(oauth_creds_path):
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("Renovando token de acesso...")
                creds.refresh(Request())
            else:
                print("Abrindo navegador para login com sua conta Google...")
                print("(isso so precisa ser feito uma vez)")
                flow = InstalledAppFlow.from_client_secrets_file(oauth_creds_path, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(token_path, 'w') as token:
                token.write(creds.to_json())

        print("Autenticado com sua conta Google pessoal!")

    # Fallback: Service Account
    elif os.path.exists(sa_creds_path):
        print("AVISO: Usando Service Account (cota limitada).")
        print("Para usar sua conta pessoal, configure oauth_credentials.json")
        creds = service_account.Credentials.from_service_account_file(
            sa_creds_path, scopes=SCOPES)
    else:
        print(f"ERRO: Nenhum arquivo de credenciais encontrado.")
        print("Coloque 'oauth_credentials.json' ou 'credentials.json' na pasta do projeto.")
        sys.exit(1)

    try:
        drive_service = build('drive', 'v3', credentials=creds)
        docs_service = build('docs', 'v1', credentials=creds)
        return drive_service, docs_service
    except Exception as e:
        print(f"ERRO DE AUTENTICACAO: {str(e)}")
        sys.exit(1)

def duplicar_template(drive_service, template_id: str, nome_cliente: str, data: str, pasta_destino_id: str = None) -> str:
    """
    Duplica o arquivo de template e o renomeia, mantendo na mesma pasta original 
    ou enviando para uma pasta destino específica.
    """
    print("Gerando cópia do Template Ficha no Google Drive...")
    
    try:
        nome_arquivo = f"{nome_cliente} - Ficha Cliente - {data}"
        body = {
            'name': nome_arquivo,
            'mimeType': 'application/vnd.google-apps.document'
        }

        if pasta_destino_id:
            body['parents'] = [pasta_destino_id]
        else:
            arquivo_origem = drive_service.files().get(fileId=template_id, fields='parents').execute()
            if 'parents' in arquivo_origem:
                body['parents'] = arquivo_origem['parents']

        copia = drive_service.files().copy(
            fileId=template_id, body=body).execute()

        return copia.get('id')
        
    except HttpError as error:
        print(f"ERRO DE API DRIVE: Falha ao duplicar o arquivo.")
        print("Você compartilhou a pasta destino (ou o template original) com o email robô?")
        print(f"Detalhes técnicos: {error}")
        sys.exit(1)


def criar_pasta_cliente(drive_service, nome_cliente: str) -> str:
    """
    Cria a pasta do cliente dentro de RECLAMANTE com as 3 subpastas padrao:
    - ATOS INTERNOS
    - DOCUMENTOS DO CLIENTE
    - PASTA DO CLIENTE
    Retorna o ID da pasta principal criada.
    """
    pasta_reclamante_id = os.getenv("GOOGLE_PASTA_RECLAMANTE", "")
    if not pasta_reclamante_id:
        raise RuntimeError("GOOGLE_PASTA_RECLAMANTE nao configurado no config/.env (id da pasta RECLAMANTE no Drive)")

    nome_alvo = nome_cliente.upper().strip()
    MIME_DIR = 'application/vnd.google-apps.folder'

    try:
        # IDEMPOTENTE desde 21/10/2026 (Advogada). Antes esta funcao criava as cegas, e o intake do
        # Francisco Roberio, que falhou tres vezes em 14/10, deixou QUATRO pastas com o mesmo
        # nome em RECLAMANTE. Com nome repetido o arquivador do WhatsApp se recusa a escolher
        # (trava de sigilo) e o documento do cliente cai na pasta de leads.
        escapado = nome_alvo.replace("'", "\\'")
        achadas = drive_service.files().list(
            q=(f"'{pasta_reclamante_id}' in parents and mimeType='{MIME_DIR}' "
               f"and trashed=false and name='{escapado}'"),
            fields='files(id, name, webViewLink)', pageSize=50).execute().get('files', [])
        if len(achadas) > 1:
            # caso ja sujo: fica com a que tem mais conteudo e NAO cria uma quinta
            def _tamanho(fid):
                try:
                    return len(drive_service.files().list(
                        q=f"'{fid}' in parents and trashed=false",
                        fields='files(id)', pageSize=200).execute().get('files', []))
                except HttpError:
                    return 0
            achadas.sort(key=lambda f: _tamanho(f['id']), reverse=True)
            print(f"ATENCAO: {len(achadas)} pastas com o nome {nome_alvo} em RECLAMANTE. "
                  f"Reuso a de id {achadas[0]['id']} (a com mais conteudo) e NAO crio outra. "
                  "Limpar as duplicadas a mao.")
        if achadas:
            pasta_id = achadas[0]['id']
            pasta_link = achadas[0].get('webViewLink', '')
            print(f"Pasta do cliente JA EXISTIA, reusando: {nome_alvo} ({pasta_id})")
        else:
            print(f"Criando pasta do cliente: {nome_cliente}...")
            pasta_cliente = drive_service.files().create(body={
                'name': nome_alvo,
                'mimeType': MIME_DIR,
                'parents': [pasta_reclamante_id]
            }, fields='id, webViewLink').execute()
            pasta_id = pasta_cliente['id']
            pasta_link = pasta_cliente.get('webViewLink', '')

        # Criar as 3 subpastas que faltarem (idem: nunca duplicar subpasta)
        existentes = {f['name'].strip().upper() for f in drive_service.files().list(
            q=f"'{pasta_id}' in parents and mimeType='{MIME_DIR}' and trashed=false",
            fields='files(id, name)', pageSize=100).execute().get('files', [])}
        subpastas = ['ATOS INTERNOS', 'DOCUMENTOS DO CLIENTE', 'PASTA DO CLIENTE']
        for sub in subpastas:
            if sub in existentes:
                print(f"   Subpasta ja existia: {sub}")
                continue
            drive_service.files().create(body={
                'name': sub,
                'mimeType': MIME_DIR,
                'parents': [pasta_id]
            }).execute()
            print(f"   Subpasta criada: {sub}")

        print(f"Pasta do cliente pronta!")
        print(f"Link: {pasta_link}")
        return pasta_id

    except HttpError as error:
        print(f"ERRO ao criar pasta do cliente: {error}")
        return None


def buscar_proximo_contrato(drive_service, nome_cliente="", empresa="", area="TRABALHISTA"):
    """
    Busca o proximo numero sequencial de contrato na planilha de controle
    e registra o novo contrato na proxima linha.
    """
    import openpyxl
    import io
    from datetime import datetime
    from googleapiclient.http import MediaInMemoryUpload

    planilha_id = os.getenv("GOOGLE_SHEETS_CONTRATOS_ID", "")

    try:
        content = drive_service.files().get_media(fileId=planilha_id).execute()
        wb = openpyxl.load_workbook(io.BytesIO(content))
        ws = wb['Contratos 2026']

        # Encontrar ultimo numero e ultima linha preenchida
        ultimo = 0
        ultima_linha = 4
        for row in range(5, ws.max_row + 1):
            val = str(ws.cell(row=row, column=8).value or '')
            if '/2026' in val:
                try:
                    num = int(val.split('/')[0])
                    if num > ultimo:
                        ultimo = num
                        ultima_linha = row
                except:
                    pass

        proximo = ultimo + 1
        nova_linha = ultima_linha + 1
        numero_contrato = f"{proximo}/2026"

        # Preencher nova linha no padrao
        ws.cell(row=nova_linha, column=4, value=nome_cliente.upper())  # D: Nome
        ws.cell(row=nova_linha, column=6, value=datetime.now())  # F: Data cadastro
        ws.cell(row=nova_linha, column=8, value=numero_contrato)  # H: CT.
        ws.cell(row=nova_linha, column=9, value=empresa.upper())  # I: Parte contraria
        ws.cell(row=nova_linha, column=10, value=area)  # J: Area

        # Salvar e fazer upload de volta
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        media = MediaInMemoryUpload(
            buffer.read(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        drive_service.files().update(
            fileId=planilha_id,
            media_body=media
        ).execute()

        print(f"   Planilha atualizada: linha {nova_linha} - {nome_cliente.upper()} - {numero_contrato}")
        return f"CT. {numero_contrato}"

    except Exception as e:
        print(f"   Aviso: Nao conseguiu atualizar planilha: {e}")
        return ""


def gerar_documentos_cliente(drive_service, docs_service, nome_cliente: str,
                             dados: dict, numero_contrato: str = None):
    """
    Gera Contrato, Procuracao e Declaracao de Hipossuficiencia
    a partir dos templates, preenchendo com os dados do cliente.
    """
    from datetime import datetime

    templates = {
        'Contrato de Honorarios': {
            'template_id': os.getenv('GOOGLE_TEMPLATE_CONTRATO', ''),
            'pasta_id': os.getenv('GOOGLE_PASTA_MODELOS_CONTRATO', ''),
        },
        'Procuracao': {
            'template_id': os.getenv('GOOGLE_TEMPLATE_PROCURACAO', ''),
            'pasta_id': os.getenv('GOOGLE_PASTA_MODELOS_PROCURACAO', ''),
        },
        'Declaracao de Hipossuficiencia': {
            'template_id': os.getenv('GOOGLE_TEMPLATE_DECLARACAO', ''),
            'pasta_id': os.getenv('GOOGLE_PASTA_MODELOS_DECLARACAO', ''),
        },
    }

    hoje = datetime.now().strftime('%d de %B de %Y').replace(
        'January', 'Janeiro').replace('February', 'Fevereiro').replace(
        'March', 'Marco').replace('April', 'Abril').replace(
        'May', 'Maio').replace('June', 'Junho').replace(
        'July', 'Julho').replace('August', 'Agosto').replace(
        'September', 'Setembro').replace('October', 'Outubro').replace(
        'November', 'Novembro').replace('December', 'Dezembro')

    data_local = f"Cidade, {hoje}"

    # Buscar numero sequencial do contrato
    empresa = dados.get('qual_empresa', dados.get('empresa', ''))
    if numero_contrato:
        # REFAZER um contrato nao pode queimar numero novo na planilha nem
        # trocar o numero que o cliente ja viu: reaproveita o original
        # (Advogada, 24/09/2026, correcao dos contratos com o modelo em aberto).
        print(f"   Numero do contrato (mantido do original): {numero_contrato}")
    else:
        numero_contrato = buscar_proximo_contrato(drive_service, nome_cliente, empresa)
        print(f"   Numero do contrato: {numero_contrato}")

    # Tratar endereco: remover prefixo de logradouro (Rua, Av, Avenida, etc.)
    # pois o template ja contem "Rua {{Endereco}}" / "residente ... a Rua {{Endereco}}"
    import re
    endereco_raw = dados.get('endereco', '')
    endereco_limpo = re.sub(r'^(Rua|Av\.?|Avenida|Alameda|Travessa|Estrada|Rodovia|R\.)\s+', '', endereco_raw, flags=re.IGNORECASE).strip()
    # o contrato ja tem "Rua" no modelo e usa o endereco SEM prefixo;
    # a procuracao nao tem, e usa este aqui, inteiro
    endereco_completo = (endereco_raw or '').strip() or endereco_limpo

    # Mapeamento das chaves dos templates para os dados extraidos
    mapa_chaves = {
        '{{Nome do cliente}}': dados.get('nome', ''),
        '{{Nome completo}}': dados.get('nome', ''),
        '{{CPF do cliente}}': dados.get('cpf', ''),
        '{{CPF}}': dados.get('cpf', ''),
        '{{RG do cliente}}': dados.get('rg', ''),
        '{{RG}}': dados.get('rg', ''),
        '{{Nacionalidade do cliente}}': dados.get('nacionalidade', ''),
        '{{Nacionalidade}}': dados.get('nacionalidade', ''),
        '{{Estado civil do cliente}}': dados.get('estado_civil', ''),
        '{{Estado Civil}}': dados.get('estado_civil', ''),
        '{{Profissão do cliente}}': dados.get('profissao', 'Motorista'),
        '{{Profissão}}': dados.get('profissao', 'Motorista'),
        '{{ENDEREÇO DO CLIENTE}}': endereco_limpo,
        # a PROCURACAO nao tem a palavra "Rua" antes do campo, entao aqui vai
        # o endereco COMPLETO; sem isso sai "domiciliado a dos Coqueirais"
        '{{Endereço}}': endereco_completo,
        '{{Bairro}}': dados.get('bairro', ''),
        '{{Cidade Estado}}': dados.get('cidade_estado', ''),
        '{{CEP}}': dados.get('cep', ''),
        '{{telefone do cliente}}': dados.get('telefone', ''),
        '{{e-mail do cliente}}': dados.get('email', ''),
        '{{NOME DA AÇÃO}}': dados.get('tipo_acao', 'Reclamatoria Trabalhista'),
        # TODO contrato sai sem o nome da reclamada (Advogada, 15/10/2026): os
        # dois campos entram SEMPRE vazios e o CAMPOS_COM_PADRAO poe "em
        # desfavor do seu empregador" no contrato e limpa o campo solto da
        # procuracao. A decisao dela: "a gente vai ter documentos de procuracao
        # que vao dar essa substancia para o contrato. Ele nao vai ficar vago".
        #
        # Veio do caso Francisco Roberio, cujo contrato saiu "em desfavor de
        # JAQUELINE RODRIGUES CORREIA", nome do emprego REGISTRADO dele de 2023
        # a 2025, quando o caso era o trabalho sem carteira no CEASA em 2026.
        # Em acao de reconhecimento de vinculo a reclamada nunca esta na CTPS.
        #
        # `qual_empresa` continua valendo FORA dos documentos: a planilha de
        # contratos e o ADVBOX seguem recebendo a parte contraria.
        '{{NOME DA EMPRESA}}': '',
        '{{nome da empresa}}': '',
        '{{Data e local de hoje}}': data_local,
        '{{data e cidade}}': data_local,
        '{{ Número sequencial de contrato  EX: CT. N°.182/2024}}': numero_contrato,
    }

    docs_gerados = []

    for doc_nome, config in templates.items():
        print(f"   Gerando: {nome_cliente} - {doc_nome}...")

        try:
            nome_arquivo = f"{nome_cliente} - {doc_nome}"
            copia = drive_service.files().copy(
                fileId=config['template_id'],
                body={
                    'name': nome_arquivo,
                    'mimeType': 'application/vnd.google-apps.document',
                    'parents': [config['pasta_id']]
                }
            ).execute()

            doc_id = copia['id']

            # Preencher as chaves.
            # ATENCAO (24/09/2026): era aqui que o contrato saia com os
            # {{campos}} do modelo. O `if valor` pulava o campo vazio e o
            # marcador FICAVA no documento, que ia assinado para o cliente
            # (CT. 100/2026, Heitor Valença). Agora campo vazio tambem gera
            # pedido: o OPCIONAL some limpo e o ESSENCIAL e barrado na
            # conferencia logo abaixo.
            # Cidade sem divisao por bairros (Advogada, 01/10/2026): com a marca
            # posta pela leitura dos Correios, {{Bairro}} deixa de ser essencial
            # NESTA geracao e sai do texto junto com o rotulo.
            opcionais = set(CAMPOS_OPCIONAIS)
            if dados.get('_bairro_nao_existe'):
                opcionais.add('{{Bairro}}')

            requests = []
            para_log_padrao = []
            for chave, valor in mapa_chaves.items():
                if valor:
                    requests.append({
                        'replaceAllText': {
                            'containsText': {'text': chave, 'matchCase': False},
                            'replaceText': valor
                        }
                    })
                elif chave in CAMPOS_COM_PADRAO:
                    # falta o dado, mas o documento segue com a mencao padrao
                    for de, para in CAMPOS_COM_PADRAO[chave]:
                        requests.append({'replaceAllText': {
                            'containsText': {'text': de, 'matchCase': False},
                            'replaceText': para}})
                    if para_log_padrao is not None:
                        para_log_padrao.append(chave)
                elif chave in opcionais:
                    requests.extend(_apagar_campo_opcional(chave))

            if requests:
                docs_service.documents().batchUpdate(
                    documentId=doc_id, body={'requests': requests}).execute()

            if para_log_padrao:
                print("   %s: sem o nome da reclamada, saiu a mencao padrao "
                      "(em desfavor do seu empregador)" % nome_arquivo)
            faltando = conferir_preenchimento(drive_service, doc_id)
            faltando += conferir_recados(drive_service, doc_id)
            if dados.get('_bairro_nao_existe'):
                faltando = [c for c in faltando if c != '{{Bairro}}']
            doc = {'nome': nome_arquivo, 'id': doc_id, 'faltando': faltando}
            if faltando:
                doc['bloqueado'] = any(c in CAMPOS_ESSENCIAIS for c in faltando)
                print(f"   ATENCAO: {nome_arquivo} ficou com campo do modelo em "
                      f"aberto: {', '.join(faltando)}")
            docs_gerados.append(doc)
            print(f"   Pronto: {nome_arquivo}")

        except HttpError as error:
            print(f"   ERRO ao gerar {doc_nome}: {error}")

    return docs_gerados


def preencher_documento(docs_service, document_id: str, dados: dict):
    """
    Edita um documento Google via Requests de BatchUpdate substituindo os placeholders 
    {{CHAVE}} com os valores recém extraídos pela IA no dicionário.
    """
    print("Inserindo Fatos e Detalhes no novo documento gerado...")
    
    requests = []
    
    # Mapeamento do dicionário do Claude com as Tags reais
    # Note que se a IA botou "FATOS" e "NOME_CLIENTE", vamos buscar "{{FATOS}}" no doc.
    for chave, valor in dados.items():
        if not isinstance(valor, str):
            # Se vier uma lista/dicionário maluco, converte
            valor = str(valor)
            
        requests.append({
            'replaceAllText': {
                'containsText': {
                    'text': f"{{{{{chave}}}}}",
                    'matchCase': True
                },
                'replaceText': valor
            }
        })
        
    try:
        resultado = docs_service.documents().batchUpdate(
            documentId=document_id, body={'requests': requests}).execute()
        print("Documento finalizado com Sucesso!!")
        
    except HttpError as error:
        print(f"ERRO DE API DOCS: Houve um erro ao formatar o texto do arquivo.")
        print(f"Detalhes: {error}")
        sys.exit(1)
     

# ============================================================
# GOOGLE CALENDAR — convites de prazo (intake / operacional)
# ============================================================

def _calendar_service():
    """Servico do Calendar reusando o token OAuth (mesma conta do Drive)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    token_path = os.path.join(config_dir, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    return build('calendar', 'v3', credentials=creds)


def criar_evento_prazo(titulo, data_ymd, descricao='', convidados=None, hora_inicio='09:00', duracao_min=60):
    """Cria um evento no Google Agenda e envia convite pros e-mails da equipe.

    data_ymd: 'YYYY-MM-DD'
    convidados: lista de e-mails (recebem convite; sendUpdates=all)
    Retorna o link do evento, ou None se falhar.
    """
    try:
        cal = _calendar_service()
        h, m = (hora_inicio.split(':') + ['00'])[:2]
        fim_min = int(h) * 60 + int(m) + duracao_min
        fim_h, fim_m = divmod(fim_min, 60)
        body = {
            'summary': titulo,
            'description': descricao,
            'start': {'dateTime': f'{data_ymd}T{int(h):02d}:{int(m):02d}:00', 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': f'{data_ymd}T{fim_h:02d}:{fim_m:02d}:00', 'timeZone': 'America/Sao_Paulo'},
            'attendees': [{'email': e} for e in (convidados or [])],
            'reminders': {'useDefault': False, 'overrides': [
                {'method': 'popup', 'minutes': 60}, {'method': 'email', 'minutes': 1440}]},
        }
        ev = cal.events().insert(calendarId='primary', body=body, sendUpdates='all').execute()
        print(f'   Evento na agenda criado: {titulo} ({data_ymd}) -> {ev.get("htmlLink")}')
        return ev.get('htmlLink')
    except Exception as e:
        print(f'   Aviso: falha ao criar evento na agenda: {e}')
        return None


# ---------------------------------------------------------------------------
# TRAVA ANTI-MODELO (regra da Advogada, 24/09/2026)
#
#   "jamais podemos mandar contratos, procuracoes, declaracoes de hipo sem o
#    preambulo preenchido pois nao fica um titulo executavel"
#
# Campo ESSENCIAL em aberto = documento NAO vai para assinatura, em hipotese
# nenhuma; o time e avisado e resolve a mao.
# Campo OPCIONAL em aberto = o documento sai, mas o marcador some do texto
# junto com a pontuacao orfa, para o preambulo continuar legivel.
# ---------------------------------------------------------------------------

CAMPOS_ESSENCIAIS = {
    '{{Nome do cliente}}', '{{Nome completo}}',
    '{{CPF do cliente}}', '{{CPF}}',
    '{{RG do cliente}}', '{{RG}}',
    '{{ENDERECO DO CLIENTE}}'.replace('ENDERECO', 'ENDERE\u00c7O'), '{{Endere\u00e7o}}',
    '{{Bairro}}', '{{Cidade Estado}}', '{{CEP}}',
    '{{telefone do cliente}}',
    '{{NOME DA A\u00c7\u00c3O}}',
    '{{NOME DA EMPRESA}}', '{{nome da empresa}}',
    '{{Nacionalidade do cliente}}', '{{Nacionalidade}}',
    '{{Profiss\u00e3o do cliente}}', '{{Profiss\u00e3o}}',
    '{{Data e local de hoje}}', '{{data e cidade}}',
    '{{ N\u00famero sequencial de contrato  EX: CT. N\u00b0.182/2024}}',
}

# So estes dois podem faltar sem barrar o documento (Advogada, 24/09: "caso nao
# encontre pode rodar mesmo assim"). Nao e escolha de estilo: sao os unicos
# que, no modelo, vivem ISOLADOS ENTRE VIRGULAS, entao da para tirar sem
# quebrar a frase. Campo com texto-guia antes dele (o RG vem depois de
# "portador da carteira de identidade de n\u00b0") NAO entra aqui: apagar so o
# marcador costura a frase errada, do tipo "n\u00b0brasileiro". Esses barram.
CAMPOS_OPCIONAIS = {
    '{{Estado civil do cliente}}', '{{Estado Civil}}',
    '{{e-mail do cliente}}',
}


CAMPOS_COM_PADRAO = {
    # Campo que, faltando, NAO barra: o texto do modelo e trocado por uma
    # mencao padrao e o documento segue (Advogada, 14/10/2026: "pode entao fazer em
    # desfavor do seu empregador mesmo... e a gente deixa isso como padrao,
    # porque dai voce nao fica travando por causa disso").
    #
    # Troca o TRECHO INTEIRO, nao o campo: o modelo diz "em desfavor de
    # {{NOME DA EMPRESA}}", e por "do seu empregador" no campo daria "em
    # desfavor de do seu empregador".
    #
    # Em acao de reconhecimento de vinculo a CTPS nao traz a reclamada e nunca
    # vai trazer, entao esse e o caso NORMAL do escritorio, nao a excecao.
    '{{NOME DA EMPRESA}}': [
        ('em desfavor de {{NOME DA EMPRESA}}', 'em desfavor do seu empregador'),
    ],
    # Na PROCURACAO o campo aparece solto entre pontos ("...iguais poderes.
    # {{nome da empresa}}. {{data e cidade}}"). Ali a mencao nao cabe, entao o
    # campo sai limpo, levando o ponto que sobraria.
    '{{nome da empresa}}': [
        ('{{nome da empresa}}. ', ''),
        ('{{nome da empresa}}.', ''),
        ('{{nome da empresa}}', ''),
    ],
}


_ROTULO_DO_CAMPO = {
    # trecho removido JUNTO com o campo, para nao sobrar rotulo orfao no texto
    # assinado ("Bairro: , Cidade: Eldorado/MS")
    '{{Bairro}}': ['Bairro: {{Bairro}}, ', 'Bairro: {{Bairro}},', 'Bairro: {{Bairro}}'],
}


def _apagar_campo_opcional(chave):
    """Pedidos que tiram o marcador E a pontuacao que sobraria em volta.

    O preambulo e uma frase com virgulas ("brasileiro, {{Estado civil}},
    Motorista"). Trocar so o marcador por vazio deixa ", ," no meio do texto
    assinado. A ordem importa: as variantes com pontuacao vao primeiro.
    """
    pedidos = []
    for trecho in _ROTULO_DO_CAMPO.get(chave, []):
        pedidos.append({'replaceAllText': {
            'containsText': {'text': trecho, 'matchCase': False},
            'replaceText': ''}})
    return pedidos + [
        {'replaceAllText': {'containsText': {'text': ', %s,' % chave, 'matchCase': False},
                            'replaceText': ','}},
        {'replaceAllText': {'containsText': {'text': ', %s' % chave, 'matchCase': False},
                            'replaceText': ''}},
        {'replaceAllText': {'containsText': {'text': '%s, ' % chave, 'matchCase': False},
                            'replaceText': ''}},
        {'replaceAllText': {'containsText': {'text': chave, 'matchCase': False},
                            'replaceText': ''}},
    ]


_RECADO_NO_DOCUMENTO = (
    # recado da automacao que NUNCA pode aparecer no documento do cliente.
    # Em 02/10/2026 o contrato CT. 101/2026 (Clóvis) foi assinado com
    # "em desfavor de Nao identificada - Reclamante relatou ... (nome e
    # CPF/CNPJ nao informados na transcricao); necessario confirmar". Nao havia
    # {{marcador}} sobrando: havia TEXTO, e a conferencia so olhava marcador.
    "nao informado na transcricao", "não informado na transcrição",
    "nao informados na transcricao", "não informados na transcrição",
    "nao informada na transcricao", "não informada na transcrição",
    "nao identificada", "não identificada",
    "nao identificado", "não identificado",
    "necessario confirmar", "necessário confirmar",
    "a confirmar com o cliente", "verificar com o cliente",
    "grafia incerta", "ilegivel", "ilegível",
    "sobrenome nao informado", "sobrenome não informado",
)


def conferir_recados(drive_service, doc_id):
    """Recado da automacao dentro do documento pronto.

    Separada da conferencia de {{campo}} porque e outro defeito: o marcador foi
    substituido, mas por um texto que era para a equipe ler, nao o cliente
    assinar. Devolve a lista do que achou, em formato de achado de texto.
    """
    import re as _re
    try:
        texto = drive_service.files().export(
            fileId=doc_id, mimeType='text/plain').execute()
        if isinstance(texto, bytes):
            texto = texto.decode('utf-8', 'replace')
    except Exception as e:
        print("   Aviso: nao consegui conferir os recados em %s: %s" % (doc_id, e))
        return []
    baixo = texto.lower()
    achados = []
    for marca in _RECADO_NO_DOCUMENTO:
        i = baixo.find(marca)
        if i >= 0:
            trecho = " ".join(texto[max(0, i - 60):i + len(marca) + 40].split())
            achados.append('[recado da automacao no documento: "%s"]' % trecho)
    return list(dict.fromkeys(achados))


def conferir_preenchimento(drive_service, doc_id):
    """Le o documento PRONTO e devolve os marcadores que sobraram.

    Le o texto de verdade, exportado do Drive, em vez de confiar na lista de
    campos que a gente mandou preencher: modelo alterado a mao pelo escritorio
    pode ter ganhado campo novo que o codigo nao conhece.
    """
    import re as _re
    try:
        texto = drive_service.files().export(
            fileId=doc_id, mimeType='text/plain').execute()
        if isinstance(texto, bytes):
            texto = texto.decode('utf-8', 'replace')
    except Exception as e:
        print("   Aviso: nao consegui conferir o documento %s: %s" % (doc_id, e))
        return []
    sobraram = list(dict.fromkeys(_re.findall(r'\{\{[^}]{0,120}\}\}', texto)))

    # FRASE COSTURADA ERRADO: o marcador sumiu mas a frase que o apresentava
    # ficou pendurada, e o texto assinado vira "carteira de identidade de
    # n\u00b0brasileiro" ou "CPF sob n\u00ba ,". Isso nao aparece na busca por
    # {{ }}, entao vai numa passada propria. Pego no teste do proprio gate.
    for costura in _re.findall(
            r'(?:de n[º°o]\.?|CPF sob n[º°o]\.?|RG\.?|Cep\.?|CEP:?|'
            r'Bairro:?|Cidade:?)\s*[,;]', texto):
        if not _re.search(r'\d', costura):
            sobraram.append('[frase sem o dado: "%s"]' % costura.strip())
            break
    return sobraram


def documentos_prontos_para_assinatura(docs_gerados):
    """(liberados, barrados). Barrado = campo essencial em aberto OU achado de
    texto no documento.

    Achado de texto e o que vem entre colchetes: "[frase sem o dado: ...]" e
    "[recado da automacao no documento: ...]". Os dois sao defeito no documento
    que o cliente assina, nao aviso. Antes de 14/10/2026 a frase costurada era
    detectada e deixava passar, que e o mesmo furo do recado: a conferencia
    enxergava e nao barrava.
    """
    liberados, barrados = [], []
    for d in docs_gerados:
        graves = [c for c in d.get('faltando', [])
                  if c in CAMPOS_ESSENCIAIS or str(c).startswith('[')]
        (barrados if graves else liberados).append(d)
    return liberados, barrados
