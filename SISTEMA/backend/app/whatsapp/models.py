"""Modelos do Atendimento (WhatsApp/CRM). Reusa o Base do sistema."""
from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class NumeroWhatsapp(Base):
    """Cada numero conectado (multi-numero): linha fixa de Reclamantes + numero de Empresas."""
    __tablename__ = 'wa_numeros'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))                 # "Reclamantes" / "Empresas"
    telefone: Mapped[str] = mapped_column(String(30), default='')  # 55DDDNUMERO
    # Credenciais Z-API deste numero
    zapi_instance: Mapped[str] = mapped_column(String(120), default='')
    zapi_token: Mapped[str] = mapped_column(String(200), default='')
    zapi_client_token: Mapped[str] = mapped_column(String(200), default='')  # Account Security Token
    # A IA responde neste numero?
    atende_ia: Mapped[bool] = mapped_column(Boolean, default=True)
    # Qual agente atende: 'reclamante' (SDR trabalhista, linha fixa) ou 'empresas' (recepcao B2B)
    agente: Mapped[str] = mapped_column(String(30), default='reclamante')
    # A IA so responde quem fala de trabalho neste numero? (vale so p/ a SDR de reclamantes)
    so_reclamante: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'nome': self.nome, 'telefone': self.telefone,
                'atende_ia': bool(self.atende_ia), 'agente': self.agente,
                'so_reclamante': bool(self.so_reclamante), 'ativo': bool(self.ativo)}


class Pipeline(Base):
    """Um funil/quadro do CRM (ex.: Reclamantes, Empresas, Palestra Rescisao Indireta). IDs fixos em funis.py."""
    __tablename__ = 'wa_pipelines'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    # dono do pipeline: vazio = compartilhado (todos veem); preenchido = privado (so o dono ve)
    dono_email: Mapped[str] = mapped_column(String(200), default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'nome': self.nome, 'ordem': self.ordem,
                'dono_email': self.dono_email or '', 'pessoal': bool(self.dono_email)}


class Stage(Base):
    """Coluna dentro de um pipeline (ex.: Primeiro Contato, Fechado)."""
    __tablename__ = 'wa_stages'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(Integer, ForeignKey('wa_pipelines.id'), index=True)
    nome: Mapped[str] = mapped_column(String(80))
    cor: Mapped[str] = mapped_column(String(20), default='#64748b')
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    descricao: Mapped[str] = mapped_column(Text, default='')  # texto de ajuda da coluna

    def to_dict(self):
        return {'id': self.id, 'pipeline_id': self.pipeline_id, 'nome': self.nome,
                'cor': self.cor, 'ordem': self.ordem, 'descricao': self.descricao or ''}


class Conversa(Base):
    __tablename__ = 'wa_conversas'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero_id: Mapped[int] = mapped_column(Integer, ForeignKey('wa_numeros.id'), index=True)
    # pipeline/funil e a coluna (stage) dentro dele
    pipeline_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    contato_telefone: Mapped[str] = mapped_column(String(30), index=True)
    contato_nome: Mapped[str] = mapped_column(String(200), default='')
    foto_url: Mapped[str] = mapped_column(String(600), default='')
    # ===== Ficha do cliente/lead =====
    tag: Mapped[str] = mapped_column(String(80), default='')          # etiqueta (ex.: Inbound, Quente)
    origem: Mapped[str] = mapped_column(String(80), default='')       # Campanha / Indicacao / Conteudo / WhatsApp
    responsavel: Mapped[str] = mapped_column(String(120), default='') # quem cuida
    email: Mapped[str] = mapped_column(String(200), default='')
    handle: Mapped[str] = mapped_column(String(120), default='')      # @ do social
    valor: Mapped[float] = mapped_column(default=0.0)                 # valor de venda
    # De que produto e este card: 'trabalhista' (reclamante) ou 'empresarial'.
    # NAO se troca por aqui nem pelo PATCH /conversas/{id}/dados: so pelo
    # PATCH /conversas/{id}/produto, que checa papel, valida, versiona e registra quem mudou.
    # A regra inteira esta em app/produto_card.py. Gatilhos no banco barram valor inventado,
    # inclusive escrita feita por script ou SQL na mao.
    produto: Mapped[str] = mapped_column(String(20), nullable=True, default=None, index=True)
    notas: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='aberto')       # aberto/fechado
    # etapa/coluna do CRM (pipeline por dono+tipo). Novo lead cai em NOVOS.
    etapa: Mapped[str] = mapped_column(String(60), default='NOVOS')
    # quem atende: email do usuario responsavel (null = nao atribuido)
    atendente_email: Mapped[str] = mapped_column(String(200), default='', index=True)
    # IA no comando? (false quando um humano assume)
    ia_ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    e_reclamante: Mapped[bool] = mapped_column(Boolean, default=False)   # falou de trabalho: SDR atende
    aceita: Mapped[bool] = mapped_column(Boolean, default=False)    # triada (aceita): sai de "Nao lidas"
    recusada: Mapped[bool] = mapped_column(Boolean, default=False)  # triada (recusada): sai de "Nao lidas"
    nao_lidas: Mapped[int] = mapped_column(Integer, default=0)
    ultima_mensagem: Mapped[str] = mapped_column(String(300), default='')
    # pasta do lead no Google Drive (cache: subir arquivos direto do chat)
    drive_pasta_id: Mapped[str] = mapped_column(String(120), default='')
    drive_pasta_url: Mapped[str] = mapped_column(String(600), default='')
    # ===== Trava do board =====
    # Board escolhido por gente e decisao final: automacao nao troca mais.
    board_fixado: Mapped[int] = mapped_column(Integer, default=0)
    board_fixado_por: Mapped[str] = mapped_column(String(200), default='')
    board_fixado_em: Mapped[str] = mapped_column(String(40), default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id, 'numero_id': self.numero_id, 'pipeline_id': self.pipeline_id,
            'contato_telefone': self.contato_telefone, 'contato_nome': self.contato_nome,
            'foto_url': self.foto_url,
            'tag': self.tag, 'origem': self.origem, 'responsavel': self.responsavel,
            'email': self.email, 'handle': self.handle, 'valor': self.valor, 'notas': self.notas,
            'produto': self.produto or None,
            'status': self.status, 'etapa': self.etapa, 'atendente_email': self.atendente_email,
            'ia_ativa': bool(self.ia_ativa), 'e_reclamante': bool(self.e_reclamante),
            'aceita': bool(self.aceita), 'recusada': bool(self.recusada),
            'nao_lidas': self.nao_lidas, 'ultima_mensagem': self.ultima_mensagem,
            'drive_pasta_url': self.drive_pasta_url or '',
            'board_fixado': bool(self.board_fixado),
            'board_fixado_por': self.board_fixado_por or '',
            'board_fixado_em': self.board_fixado_em or '',
            'atualizado_em': (self.atualizado_em.isoformat() + 'Z') if self.atualizado_em else None,
        }


class MoveBoard(Base):
    """Trilha de TODA troca de board/coluna — inclusive as que a trava recusou.
    Existe para responder 'quem tirou meu card daqui' sem depender de memoria."""
    __tablename__ = 'wa_board_hist'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversa_id: Mapped[int] = mapped_column(Integer, index=True)
    de_pipeline: Mapped[int] = mapped_column(Integer, default=0)
    de_etapa: Mapped[str] = mapped_column(String(60), default='')
    para_pipeline: Mapped[int] = mapped_column(Integer, default=0)
    para_etapa: Mapped[str] = mapped_column(String(60), default='')
    quem: Mapped[str] = mapped_column(String(200), default='')      # email do humano ou auto:<motivo>
    origem: Mapped[str] = mapped_column(String(60), default='')     # ui | ia | esteira | script:<nome>
    bloqueado: Mapped[int] = mapped_column(Integer, default=0)      # 1 = a trava recusou
    criado_em: Mapped[str] = mapped_column(String(40), default='')

    def to_dict(self):
        return {'id': self.id, 'conversa_id': self.conversa_id,
                'de_pipeline': self.de_pipeline, 'de_etapa': self.de_etapa,
                'para_pipeline': self.para_pipeline, 'para_etapa': self.para_etapa,
                'quem': self.quem, 'origem': self.origem,
                'bloqueado': bool(self.bloqueado), 'criado_em': self.criado_em}


class Mensagem(Base):
    __tablename__ = 'wa_mensagens'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversa_id: Mapped[int] = mapped_column(Integer, ForeignKey('wa_conversas.id'), index=True)
    origem: Mapped[str] = mapped_column(String(20))       # contato | ia | atendente
    autor: Mapped[str] = mapped_column(String(200), default='')  # email do atendente, se humano
    tipo: Mapped[str] = mapped_column(String(20), default='texto')  # texto | audio | imagem | doc | figurinha
    conteudo: Mapped[str] = mapped_column(Text, default='')
    midia_url: Mapped[str] = mapped_column(String(600), default='')
    midia_drive: Mapped[str] = mapped_column(String(600), default='')  # link no Drive depois de subir
    remote_id: Mapped[str] = mapped_column(String(80), default='', index=True)  # messageId Z-API (dedup)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'conversa_id': self.conversa_id, 'origem': self.origem,
                'autor': self.autor, 'tipo': self.tipo, 'conteudo': self.conteudo,
                'midia_url': self.midia_url, 'midia_drive': self.midia_drive or '',
                'criado_em': (self.criado_em.isoformat() + 'Z') if self.criado_em else None}


class Fluxo(Base):
    """Fluxo automatico de mensagens por cadencia (ex.: assinatura D+1/3/7/14).
    Uma conversa entra num fluxo; o motor diario manda a mensagem do dia devido.
    Fluxos de assinatura e documentos; nunca cobra pagamento do trabalhador."""
    __tablename__ = 'wa_fluxos'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversa_id: Mapped[int] = mapped_column(Integer, ForeignKey('wa_conversas.id'), index=True)
    tipo: Mapped[str] = mapped_column(String(30))            # 'assinatura' | 'documentos'
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    etapas_enviadas: Mapped[str] = mapped_column(String(120), default='')  # CSV dos dias ja enviados: "1,3"
    concluido_em: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    criado_por: Mapped[str] = mapped_column(String(200), default='')

    def to_dict(self):
        enviadas = [int(x) for x in self.etapas_enviadas.split(',') if x.strip().isdigit()]
        return {'id': self.id, 'conversa_id': self.conversa_id, 'tipo': self.tipo,
                'ativo': bool(self.ativo), 'iniciado_em': self.iniciado_em.isoformat() if self.iniciado_em else None,
                'etapas_enviadas': enviadas,
                'concluido_em': self.concluido_em.isoformat() if self.concluido_em else None}


class Figurinha(Base):
    """Galeria de figurinhas favoritas do escritorio (enviadas pelo chat)."""
    __tablename__ = 'wa_figurinhas'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), default='')
    url: Mapped[str] = mapped_column(String(600), default='')   # URL publica do .webp
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'nome': self.nome, 'url': self.url,
                'ordem': self.ordem, 'ativo': bool(self.ativo)}


class LidMap(Base):
    """Mapa @lid <-> numero real. Resolve o bug das conversas-fantasma do WhatsApp:
    o WhatsApp migra contatos pra @lid; guardamos aqui o numero real (obtido via
    numero->lid na Z-API) pra rotear as mensagens pra conversa certa."""
    __tablename__ = 'wa_lid_map'
    lid: Mapped[str] = mapped_column(String(80), primary_key=True)
    telefone: Mapped[str] = mapped_column(String(30), default='', index=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
