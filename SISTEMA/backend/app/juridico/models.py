"""Nucleo juridico PROPRIO do escritorio — clientes, processos e partes.

O banco do sistema e a fonte da verdade. A coluna `advbox_id` ficou do esquema-base e serve
para guardar o id de um sistema juridico externo, se um dia houver (ver
INTEGRACOES/sistema_juridico.py); em operacao normal fica vazia.
"""
from datetime import datetime
from sqlalchemy import String, DateTime, Integer, Text, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Cliente(Base):
    """Pessoa/empresa: pode ser cliente do escritorio ou parte contraria."""
    __tablename__ = 'jur_clientes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advbox_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)  # id externo (opcional)
    nome: Mapped[str] = mapped_column(String(250), index=True)
    documento: Mapped[str] = mapped_column(String(40), default='', index=True)  # CPF/CNPJ
    email: Mapped[str] = mapped_column(String(200), default='')
    telefone: Mapped[str] = mapped_column(String(40), default='')
    celular: Mapped[str] = mapped_column(String(40), default='')
    # ESCRITORIO (cliente nosso) | PARTE CONTRARIA | INDICACAO ...
    origem: Mapped[str] = mapped_column(String(80), default='', index=True)
    nascimento: Mapped[str] = mapped_column(String(20), default='')
    estado_civil: Mapped[str] = mapped_column(String(60), default='')
    profissao: Mapped[str] = mapped_column(String(120), default='')
    # endereco
    logradouro: Mapped[str] = mapped_column(String(250), default='')
    cidade: Mapped[str] = mapped_column(String(120), default='')
    estado: Mapped[str] = mapped_column(String(40), default='')
    cep: Mapped[str] = mapped_column(String(20), default='')
    notas: Mapped[str] = mapped_column(Text, default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'advbox_id': self.advbox_id, 'nome': self.nome,
                'documento': self.documento, 'email': self.email,
                'telefone': self.telefone or self.celular, 'origem': self.origem,
                'nascimento': self.nascimento, 'estado_civil': self.estado_civil,
                'profissao': self.profissao, 'cidade': self.cidade, 'estado': self.estado,
                'logradouro': self.logradouro, 'cep': self.cep, 'notas': self.notas}


class Processo(Base):
    __tablename__ = 'jur_processos'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advbox_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    numero: Mapped[str] = mapped_column(String(60), default='', index=True)      # process_number
    protocolo: Mapped[str] = mapped_column(String(60), default='')               # protocol_number
    area: Mapped[str] = mapped_column(String(120), default='', index=True)       # group (nucleo)
    tipo: Mapped[str] = mapped_column(String(250), default='')                   # type
    fase: Mapped[str] = mapped_column(String(120), default='')                   # stage
    etapa: Mapped[str] = mapped_column(String(120), default='')                  # step
    responsavel: Mapped[str] = mapped_column(String(150), default='', index=True)
    responsavel_advbox_id: Mapped[int] = mapped_column(Integer, nullable=True)
    encerrado: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status_encerramento: Mapped[str] = mapped_column(String(120), default='')
    data_processo: Mapped[str] = mapped_column(String(20), default='')
    pasta: Mapped[str] = mapped_column(String(120), default='')                  # folder
    valor_expectativa: Mapped[float] = mapped_column(Float, default=0.0)         # fees_expec
    valor_honorarios: Mapped[float] = mapped_column(Float, default=0.0)          # fees_money
    contingencia: Mapped[float] = mapped_column(Float, default=0.0)
    notas: Mapped[str] = mapped_column(Text, default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'advbox_id': self.advbox_id, 'numero': self.numero,
                'protocolo': self.protocolo, 'area': self.area, 'tipo': self.tipo,
                'fase': self.fase, 'etapa': self.etapa, 'responsavel': self.responsavel,
                'encerrado': bool(self.encerrado), 'status_encerramento': self.status_encerramento,
                'data_processo': self.data_processo, 'pasta': self.pasta,
                'valor_expectativa': self.valor_expectativa, 'valor_honorarios': self.valor_honorarios,
                'contingencia': self.contingencia, 'notas': self.notas}


class ProcessoParte(Base):
    """Quem esta no processo e em que papel (nosso cliente x parte contraria)."""
    __tablename__ = 'jur_processo_partes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processo_id: Mapped[int] = mapped_column(Integer, index=True)
    cliente_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    advbox_customer_id: Mapped[int] = mapped_column(Integer, nullable=True)
    nome: Mapped[str] = mapped_column(String(250), default='')
    documento: Mapped[str] = mapped_column(String(40), default='')
    papel: Mapped[str] = mapped_column(String(80), default='')  # ESCRITORIO | PARTE CONTRARIA

    def to_dict(self):
        return {'id': self.id, 'cliente_id': self.cliente_id, 'nome': self.nome,
                'documento': self.documento, 'papel': self.papel}
