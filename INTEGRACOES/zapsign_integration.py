"""
Integracao unificada com ZapSign - assinatura digital.

Funcoes disponiveis:
  Baixo nivel:
    get_zapsign_headers()
    exportar_google_doc_pdf(drive, doc_id)
    pdf_bytes_para_base64(pdf_bytes)
    normalizar_telefone(tel)
    criar_signatario(nome, email, telefone, auth_mode)

  Alto nivel - criacao:
    criar_documento(nome, pdf_base64, signers, folder_path, external_id) -> dict
    enviar_documento(drive, doc_id, signers, folder_path) -> dict
    enviar_para_zapsign(drive, nome_cliente, docs_gerados, dados_cliente) -> list[link]
      (compat INTAKE/COMERCIAL)

  Alto nivel - consulta/download:
    listar_documentos(status='signed', page=1) -> (docs, has_next)
    buscar_documento(token) -> dict
    listar_signatarios_documento(doc_token) -> list
    baixar_pdf_assinado(url_signed_file) -> bytes

  Sincronizacao:
    sincronizar_assinados_para_drive(drive, log_path, buscar_pasta_cliente_fn)
      -> dict com resumo (novos, ja_sincronizados, erros)

Endpoints ZapSign:
  POST /api/v1/docs/            criar doc
  GET  /api/v1/docs/?page=N     listar docs
  GET  /api/v1/docs/{token}/    buscar doc

Auth: Bearer token via ZAPSIGN_API_TOKEN (.env).
"""
import os
import sys
import json
import time
import base64
import logging
import requests

log = logging.getLogger('integracoes.zapsign')

ZAPSIGN_BASE = 'https://api.zapsign.com.br/api/v1'


# ============================================================
# BAIXO NIVEL
# ============================================================

def get_zapsign_headers():
    """Retorna headers Bearer. Fails hard se o token nao estiver setado."""
    token = os.getenv('ZAPSIGN_API_TOKEN')
    if not token:
        print('ERRO: ZAPSIGN_API_TOKEN nao encontrado no .env')
        sys.exit(1)
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }


def exportar_google_doc_pdf(drive_service, doc_id):
    """Exporta um Google Doc como PDF e retorna os bytes."""
    return drive_service.files().export(
        fileId=doc_id, mimeType='application/pdf'
    ).execute()


def pdf_bytes_para_base64(pdf_bytes):
    """Converte bytes de PDF em string base64 pronta pra API."""
    return base64.b64encode(pdf_bytes).decode('utf-8')


def normalizar_telefone(tel):
    """Retorna so digitos do telefone, removendo DDI 55 inicial."""
    if not tel:
        return ''
    d = ''.join(c for c in str(tel) if c.isdigit())
    if d.startswith('55') and len(d) > 11:
        d = d[2:]
    return d


# PADRAO DA ASSINATURA (Advogada, 23/10/2026): o cliente desenha na tela e confirma com o codigo que
# chega no WHATSAPP, nao mais por SMS. Nasceu do caso Ivo Pacheco Neri, que recebeu seis SMS,
# abriu o contrato doze vezes e nao conseguiu assinar. A ZapSign documenta que o codigo por
# WhatsApp chega com mais confianca que o SMS, e o nosso publico le WhatsApp. Vale para o que
# nascer daqui para frente: documento que ja esta no ZapSign segue como esta.
MODO_ASSINATURA = 'assinaturaTela-tokenWhatsapp'


def criar_signatario(nome, email=None, telefone=None, auth_mode=None,
                     send_email=None, send_whatsapp=None):
    """
    Monta um dict de signatario para a API do ZapSign.

    auth_mode: None usa MODO_ASSINATURA (desenho na tela + codigo por WhatsApp). A API tambem
    aceita 'tokenWhatsapp' (so o codigo, sem desenho: e o plano B de quem trava na tela),
    'assinaturaTela', 'tokenSms', 'assinaturaTela-tokenSms' e 'certificadoDigital'.
    send_email / send_whatsapp: None = autodetecta pelos dados; True/False sobrescreve.
    """
    auth_mode = auth_mode or os.getenv('ZAPSIGN_AUTH_MODE') or MODO_ASSINATURA
    tel = normalizar_telefone(telefone) or None
    # ZapSign exige codigo ao assinar na tela: sem e-mail no signatario ele vai por SMS (sem nenhum dos dois, trava)
    em = None if tel else (email or None)
    signatario = {
        'name': nome,
        'email': em,
        'phone_country': '55',
        'phone_number': tel,
        'auth_mode': auth_mode,
        'send_automatic_email': bool(em) and send_email is not False,
        'send_automatic_whatsapp': bool(tel) if send_whatsapp is None else send_whatsapp,
    }
    if tel:
        signatario['blank_email'] = True
    return signatario


def signatario_advogada_api():
    """
    Signatario da Dra. Advogada preparado para assinatura AUTOMATICA via API.

    Requisito do endpoint /sign/: o email do signatario deve ser VAZIO ou IGUAL ao
    do usuario ZapSign que assina. Duas formas de operar:
      - Assinar "pelo email": defina ZAPSIGN_ADVOGADA_EMAIL (.env) com o MESMO email
        do usuario ZapSign da Advogada. A assinatura fica vinculada ao email dela.
      - Sem email: deixe ZAPSIGN_ADVOGADA_EMAIL vazio -> signatario sem email.
    Nos dois casos as notificacoes ficam desligadas: ela nao recebe link, a
    assinatura e colocada automaticamente pelo fluxo.
    """
    em = os.getenv('ZAPSIGN_ADVOGADA_EMAIL') or None
    return {
        # fica em 'assinaturaTela' de proposito: ela assina pela API, nao recebe link nem codigo
        'name': os.getenv('ZAPSIGN_ADVOGADA_NOME') or 'Advogada Responsável',
        'email': em,
        'phone_country': '55',
        'phone_number': None,
        'auth_mode': 'assinaturaTela',
        'send_automatic_email': False,
        'send_automatic_whatsapp': False,
    }


def assinar_via_api(signer_tokens, user_token=None):
    """
    Assina documento(s) em nome do usuario dono do user_token (assinatura em lote).
    POST /api/v1/sign/  body: {user_token, signer_tokens:[...]}.

    signer_tokens: str (um) ou list de signer tokens dos signatarios a assinar.
    user_token: token de 'assinatura via API' do perfil ZapSign da Advogada.
                Se None, le de ZAPSIGN_USER_TOKEN (.env).
    Retorna True/False.
    """
    if user_token is None:
        user_token = os.getenv('ZAPSIGN_USER_TOKEN')
    if not user_token:
        log.warning('ZAPSIGN_USER_TOKEN ausente - assinatura automatica via API pulada')
        return False
    if isinstance(signer_tokens, str):
        signer_tokens = [signer_tokens]
    signer_tokens = [t for t in signer_tokens if t]
    if not signer_tokens:
        return False
    headers = get_zapsign_headers()
    payload = {'user_token': user_token, 'signer_tokens': signer_tokens}
    try:
        r = requests.post(f'{ZAPSIGN_BASE}/sign/', headers=headers, json=payload, timeout=60)
    except Exception as e:
        log.warning(f'assinar_via_api erro: {e}')
        return False
    if r.status_code in (200, 201):
        return True
    log.warning(f'assinar_via_api {r.status_code}: {r.text[:300]}')
    return False


# ============================================================
# ALTO NIVEL - CRIACAO
# ============================================================

def criar_documento(nome, pdf_base64, signers, folder_path='/', external_id='',
                    lang='pt-br'):
    """
    Cria documento no ZapSign a partir de PDF base64.

    signers: lista de dicts no formato criar_signatario()
    Retorna dict com:
        token       - token unico do doc
        doc_url     - link pro painel do ZapSign
        sign_urls   - list[{'signatario','link','token'}]
        raw         - resposta completa
    """
    # ------------------------------------------------------------------
    # FISCAL DE DOCUMENTO (Advogada, 09/11/2026). Ultimo portao antes do cliente.
    #
    # Fica AQUI, e nao em enviar_documento(), porque este e o funil: o
    # gr_contratacao, o gr_producao e os termos de imagem chamam
    # criar_documento() direto, sem passar por enviar_documento(). Posto la,
    # o fiscal deixaria o Desbloqueio GR inteiro de fora.
    #
    # Nasceu do CT 100/2026 (Heitor Valença), assinado em 22/09/2026 com os
    # {{campos}} a mostra. Medido em 71 documentos reais antes de subir: 65
    # aprovados, 6 barrados, e os 6 sao os do incidente.
    #
    # Quem monta varios documentos de uma vez deve conferir ANTES de criar o
    # primeiro (ver gr_contratacao.mandar_para_zapsign): barrar o segundo com
    # o primeiro ja criado deixa o cliente com meia contratacao.
    # ------------------------------------------------------------------
    try:
        from fiscal_documento import conferir_bytes, resumo_curto, registrar
    except ImportError:
        import os as _os
        import sys as _sys
        _aqui = _os.path.dirname(_os.path.abspath(__file__))
        if _aqui not in _sys.path:
            _sys.path.insert(0, _aqui)
        try:
            from fiscal_documento import conferir_bytes, resumo_curto, registrar
        except ImportError:
            raise RuntimeError(
                'fiscal_documento nao encontrado ao lado do zapsign_integration: '
                'nao crio documento no ZapSign sem conferir antes')
    import base64 as _b64
    try:
        _pdf = _b64.b64decode(pdf_base64)
    except Exception as _e:
        raise ValueError('PDF ilegivel para o fiscal (%s)' % _e)
    _ok, _achados = conferir_bytes(_pdf, nome)
    if not _ok:
        _lista = resumo_curto(_achados)
        registrar(nome, _achados, origem='criar_documento')
        print('   FISCAL BARROU %s:' % nome)
        for _p in _lista:
            print('      - %s' % _p)
        raise ValueError(
            'FISCAL BARROU o documento "%s" antes do envio: %s'
            % (nome, '; '.join(_lista)))

    headers = get_zapsign_headers()
    payload = {
        'name': nome,
        'url_pdf': '',
        'base64_pdf': pdf_base64,
        'external_id': external_id,
        'signers': signers,
        'folder_path': folder_path,
        'lang': lang,
    }
    r = requests.post(f'{ZAPSIGN_BASE}/docs/', headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    result = r.json()

    sign_urls = []
    for s in result.get('signers') or []:
        sign_urls.append({
            'signatario': s.get('name'),
            'link': s.get('sign_url'),
            'token': s.get('token'),
            'email': s.get('email'),
            'phone_number': s.get('phone_number'),
        })

    token = result.get('token', '')
    return {
        'token': token,
        'doc_url': f'https://app.zapsign.com.br/cli/docs/{token}' if token else None,
        'sign_urls': sign_urls,
        'raw': result,
    }


def enviar_documento(drive_service, doc_id, signers, folder_path='/', external_id=''):
    """
    Atalho: exporta um Google Doc (ou baixa um PDF do Drive) e cria no ZapSign.
    Retorna o mesmo dict de criar_documento() + nome_doc.
    """
    meta = drive_service.files().get(
        fileId=doc_id, fields='name,mimeType', supportsAllDrives=True
    ).execute()
    nome = meta['name']
    mime = meta['mimeType']

    if mime == 'application/vnd.google-apps.document':
        pdf_bytes = exportar_google_doc_pdf(drive_service, doc_id)
    else:
        import io
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        req = drive_service.files().get_media(fileId=doc_id, supportsAllDrives=True)
        dl = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        pdf_bytes = buf.getvalue()

    out = criar_documento(
        nome=nome,
        pdf_base64=pdf_bytes_para_base64(pdf_bytes),
        signers=signers,
        folder_path=folder_path,
        external_id=external_id,
    )
    out['nome_doc'] = nome
    return out


def enviar_para_zapsign(drive_service, nome_cliente, docs_gerados, dados_cliente):
    """
    COMPAT: assinatura original usada por INTAKE/main.py e COMERCIAL/main.py.

    Envia Contrato (2 signers: cliente + Advogada), Procuracao e Declaracao
    (1 signer: cliente). Retorna list[{'documento','signatario','link'}].
    """
    nome = dados_cliente.get('nome', nome_cliente)
    email = dados_cliente.get('email', '')
    telefone = dados_cliente.get('telefone', '')

    print(f'\n8. Enviando documentos para ZapSign...')
    links = []

    for doc in docs_gerados:
        doc_nome = doc['nome']
        doc_id = doc['id']
        up = doc_nome.upper()

        is_contrato = 'CONTRATO' in up
        is_procuracao = 'PROCURAC' in up
        is_declaracao = 'DECLARAC' in up
        if not (is_contrato or is_procuracao or is_declaracao):
            continue

        print(f'   Enviando: {doc_nome}...')
        # telefone SEMPRE no cadastro do signatario (no Benicio, 12/08, ele ia
        # vazio e o documento ficava sem dono identificavel), mas quem fala com
        # o cliente e a nossa regua pela Z-API: WhatsApp do ZapSign desligado
        # para nao chegarem duas mensagens diferentes do mesmo contrato.
        signatarios = [criar_signatario(nome, email, telefone, send_whatsapp=False)]
        if is_contrato:
            # Signatario da Advogada: preparado para assinatura automatica via API
            signatarios.append(signatario_advogada_api())

        try:
            out = enviar_documento(
                drive_service=drive_service,
                doc_id=doc_id,
                signers=signatarios,
                folder_path=f'/{nome_cliente.upper()}/',
            )
            for s in out['sign_urls']:
                if s['link']:
                    links.append({
                        'documento': doc_nome,
                        'signatario': s['signatario'],
                        'link': s['link'],
                    })
            print('   Enviado com sucesso!')

            # Assinatura automatica da Dra. Advogada (parte dela) direto no envio
            if is_contrato and os.getenv('ZAPSIGN_USER_TOKEN'):
                pam_token = None
                for s in out['sign_urls']:
                    nm = (s.get('signatario') or '').lower()
                    if nm == signatario_advogada_api()['name'].lower():
                        pam_token = s.get('token')
                        break
                if pam_token:
                    ok_sign = assinar_via_api(pam_token)
                    print('   Assinatura automatica Dra. Advogada: '
                          + ('OK' if ok_sign else 'FALHOU (verifique ZAPSIGN_USER_TOKEN)'))
        except Exception as e:
            print(f'   ERRO ao enviar {doc_nome}: {e}')

        time.sleep(1)

    return links


# ============================================================
# ALTO NIVEL - CONSULTA / DOWNLOAD
# ============================================================

def listar_documentos(status=None, page=1):
    """
    Lista documentos do ZapSign (paginado).

    status: se informado ('signed', 'pending', 'refused'), filtra em memoria.
    Retorna (list_docs, has_next).
    """
    headers = get_zapsign_headers()
    r = requests.get(f'{ZAPSIGN_BASE}/docs/?page={page}', headers=headers, timeout=30)
    if r.status_code != 200:
        log.warning(f'listar_documentos HTTP {r.status_code}: {r.text[:200]}')
        return [], False
    data = r.json()
    docs = data.get('results') or []
    if status:
        docs = [d for d in docs if d.get('status') == status]
    has_next = bool(data.get('next'))
    return docs, has_next


def buscar_documento(token):
    """GET /docs/{token}/ - retorna o doc completo."""
    headers = get_zapsign_headers()
    r = requests.get(f'{ZAPSIGN_BASE}/docs/{token}/', headers=headers, timeout=30)
    if r.status_code != 200:
        log.warning(f'buscar_documento {token} HTTP {r.status_code}: {r.text[:200]}')
        return None
    return r.json()


def listar_signatarios_documento(doc_token):
    """Retorna lista de signatarios com status de assinatura."""
    doc = buscar_documento(doc_token)
    if not doc:
        return []
    return doc.get('signers') or []


def baixar_pdf_assinado(signed_file_url):
    """Baixa o PDF final assinado. Retorna bytes ou None."""
    try:
        r = requests.get(signed_file_url, timeout=120)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        log.warning(f'falha ao baixar pdf assinado: {e}')
    return None


# ============================================================
# SINCRONIZACAO ZAPSIGN -> DRIVE
# ============================================================

def sincronizar_assinados_para_drive(drive_service, log_path, buscar_pasta_cliente_fn,
                                     max_paginas=20):
    """
    Percorre documentos assinados no ZapSign e salva o PDF na pasta do cliente no Drive.

    drive_service: drive autenticado (do google_integration.autenticar_google)
    log_path: caminho do JSON que rastreia tokens ja sincronizados
    buscar_pasta_cliente_fn: callable(drive, nome_cliente) -> pasta_id (ou None)
    max_paginas: limite de paginas do ZapSign a percorrer

    Retorna dict:
        {'novos': int, 'ja_sincronizados': int, 'ignorados': int, 'erros': list, 'itens_novos': list}
    """
    from googleapiclient.http import MediaInMemoryUpload

    # carregar / iniciar log
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            try:
                sync_log = json.load(f)
            except Exception:
                sync_log = {'sincronizados': []}
    else:
        sync_log = {'sincronizados': []}
    sincronizados = set(sync_log.get('sincronizados') or [])

    resumo = {'novos': 0, 'ja_sincronizados': 0, 'ignorados': 0,
              'erros': [], 'itens_novos': []}

    for page in range(1, max_paginas + 1):
        docs, has_next = listar_documentos(status='signed', page=page)
        if not docs:
            break

        for doc in docs:
            token = doc.get('token', '')
            nome_doc = doc.get('name', '')
            signed_file = doc.get('signed_file', '')
            folder_path = doc.get('folder_path', '') or ''

            if token in sincronizados:
                resumo['ja_sincronizados'] += 1
                continue
            if not signed_file:
                resumo['ignorados'] += 1
                continue

            nome_cliente = folder_path.strip('/').strip()
            if not nome_cliente and ' - ' in nome_doc:
                nome_cliente = nome_doc.split(' - ')[0].strip()
            if not nome_cliente:
                resumo['ignorados'] += 1
                resumo['erros'].append(f'{nome_doc}: sem cliente identificavel')
                continue

            pasta_id = None
            try:
                pasta_id = buscar_pasta_cliente_fn(drive_service, nome_cliente)
            except Exception as e:
                resumo['erros'].append(f'{nome_doc}: erro pasta cliente: {e}')
            if not pasta_id:
                resumo['ignorados'] += 1
                resumo['erros'].append(f'{nome_doc}: pasta de "{nome_cliente}" nao encontrada')
                continue

            pdf_bytes = baixar_pdf_assinado(signed_file)
            if not pdf_bytes:
                resumo['erros'].append(f'{nome_doc}: falha download PDF')
                continue

            try:
                media = MediaInMemoryUpload(pdf_bytes, mimetype='application/pdf')
                drive_service.files().create(
                    body={'name': f'{nome_doc} (ASSINADO).pdf', 'parents': [pasta_id]},
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
                sincronizados.add(token)
                resumo['novos'] += 1
                resumo['itens_novos'].append({
                    'nome_doc': nome_doc,
                    'nome_cliente': nome_cliente,
                    'pasta_id': pasta_id,
                    'token': token,
                })
            except Exception as e:
                resumo['erros'].append(f'{nome_doc}: erro upload Drive: {e}')

        if not has_next:
            break

    # salvar log
    try:
        sync_log['sincronizados'] = sorted(sincronizados)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(sync_log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        resumo['erros'].append(f'erro ao salvar log: {e}')

    return resumo


def atualizar_signatario(signer_token, **campos):
    """Muda o cadastro de um signatario que AINDA NAO ASSINOU (POST /signers/{token}/).

    Serve para o que a gente aprendeu em 23/10/2026: trocar o auth_mode de quem trava na tela de
    desenho (para 'tokenWhatsapp', so o codigo) e preencher o telefone de documento que nasceu
    sem ele, caso em que o cliente nunca teve como ser avisado (Ivo Pacheco Neri e os documentos
    corrigidos do Valdo Sarmento).

    Campos aceitos pela API: name, email, phone_country, phone_number, auth_mode, lock_name,
    lock_email, lock_phone, qualification, external_id, send_automatic_whatsapp,
    send_automatic_email, selfie_validation_type, require_document_photo, require_selfie_photo,
    require_cpf, cpf, validate_cpf, require_rg, rg, signature_placement, rubrica_placement,
    redirect_link.

    Devolve (ok, resposta).
    """
    url = f'{ZAPSIGN_BASE}/signers/{signer_token}/'
    r = requests.post(url, headers=get_zapsign_headers(), json=campos, timeout=60)
    ok = r.status_code in (200, 201)
    if not ok:
        log.warning('atualizar_signatario %s: %s %s', signer_token, r.status_code, r.text[:200])
    try:
        return ok, r.json()
    except Exception:
        return ok, {'status_code': r.status_code, 'texto': r.text[:300]}


def reenviar_notificacoes(doc_token):
    """Reenvia o aviso de assinatura a TODOS os signatarios pendentes do documento.

    POST /docs/{token}/resend-notifications-bulk/. Quem ja assinou nao recebe nada. A API devolve
    429 com 'cooldown_period' quando o reenvio foi recente: isso NAO e erro nosso, e so esperar.

    Devolve (ok, resposta), onde resposta traz sent_count e failed_signers.
    """
    url = f'{ZAPSIGN_BASE}/docs/{doc_token}/resend-notifications-bulk/'
    r = requests.post(url, headers=get_zapsign_headers(), json={}, timeout=60)
    ok = r.status_code in (200, 201)
    if not ok:
        log.warning('reenviar_notificacoes %s: %s %s', doc_token, r.status_code, r.text[:200])
    try:
        return ok, r.json()
    except Exception:
        return ok, {'status_code': r.status_code, 'texto': r.text[:300]}
