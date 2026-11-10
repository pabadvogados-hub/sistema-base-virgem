"""SQLite async (SQLAlchemy) - usuarios e jobs de peca."""
from datetime import datetime
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, Integer, Text, JSON, Boolean, event
from .config import settings

# timeout=30s: se o banco estiver escrevendo, espera ao inves de estourar "database is locked"
engine = create_async_engine(settings.DATABASE_URL, echo=False, connect_args={"timeout": 30})

# SQLite: WAL deixa leitura e escrita rodarem juntas; busy_timeout segura sob concorrencia
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = 'usuarios'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(200))
    senha_hash: Mapped[str] = mapped_column(String(300), default='')
    # papel: admin (tudo) | advogado | usuario
    papel: Mapped[str] = mapped_column(String(50), default='usuario')
    # acesso ao modulo Financeiro/Asaas (o admin libera por pessoa)
    acesso_financeiro: Mapped[bool] = mapped_column(Boolean, default=False)
    # Arrastar card de um BOARD para outro no CRM sem ser admin.
    # ATENCAO: nao da acesso de LEITURA a board pessoal de outra pessoa. Quem tem
    # esta permissao consegue MANDAR um card para o board pessoal de alguem, e depois
    # nao ve mais esse card nem as mensagens dele: quem filtra isso e
    # _pipelines_bloqueados, pelo dono_email do pipeline, e continua valendo.
    pode_trocar_board: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ultimo_login: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'email': self.email,
            'nome': self.nome,
            'papel': self.papel,
            'acesso_financeiro': bool(self.acesso_financeiro),
            'pode_trocar_board': bool(self.pode_trocar_board),
            'ativo': bool(self.ativo),
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'ultimo_login': self.ultimo_login.isoformat() if self.ultimo_login else None,
        }


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    usuario_email: Mapped[str] = mapped_column(String(200), index=True)
    tipo_peca: Mapped[str] = mapped_column(String(60))
    cliente_nome: Mapped[str] = mapped_column(String(200), default='')
    cliente_advbox_id: Mapped[str] = mapped_column(String(60), default='')
    drive_link: Mapped[str] = mapped_column(String(600), default='')
    status: Mapped[str] = mapped_column(String(30), default='pending')  # pending/running/done/error
    progresso_msg: Mapped[str] = mapped_column(String(500), default='')

    # Input
    dados_caso: Mapped[str] = mapped_column(Text, default='')
    jurisprudencia: Mapped[str] = mapped_column(Text, default='')
    esqueleto: Mapped[str] = mapped_column(Text, default='')
    anexos: Mapped[dict] = mapped_column(JSON, default=list)

    # Output
    peca_texto: Mapped[str] = mapped_column(Text, default='')
    docx_path: Mapped[str] = mapped_column(String(500), default='')
    drive_url: Mapped[str] = mapped_column(String(500), default='')
    erro: Mapped[str] = mapped_column(Text, default='')

    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finalizado_em: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class IntakeJob(Base):
    __tablename__ = 'intake_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usuario_email: Mapped[str] = mapped_column(String(200), index=True)
    cliente_nome: Mapped[str] = mapped_column(String(200), default='')
    transcricao_nome: Mapped[str] = mapped_column(String(300), default='')
    status: Mapped[str] = mapped_column(String(30), default='pending')  # pending/running/done/error
    progresso_msg: Mapped[str] = mapped_column(String(500), default='')
    log: Mapped[str] = mapped_column(Text, default='')
    erro: Mapped[str] = mapped_column(Text, default='')
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finalizado_em: Mapped[datetime] = mapped_column(DateTime, nullable=True)


async def init_db():
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migracao leve: adiciona colunas novas em tabelas ja existentes
        res = await conn.execute(text("PRAGMA table_info(jobs)"))
        cols = {row[1] for row in res.fetchall()}
        if 'drive_link' not in cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN drive_link VARCHAR(600) DEFAULT ''"))
        # wa_conversas: coluna pipeline_id (multi-pipeline)
        res = await conn.execute(text("PRAGMA table_info(wa_conversas)"))
        wcols = {row[1] for row in res.fetchall()}
        if wcols and 'pipeline_id' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN pipeline_id INTEGER DEFAULT 0"))
        # wa_mensagens: coluna remote_id (dedup de mensagens do celular)
        res = await conn.execute(text("PRAGMA table_info(wa_mensagens)"))
        mcols = {row[1] for row in res.fetchall()}
        if mcols and 'remote_id' not in mcols:
            await conn.execute(text("ALTER TABLE wa_mensagens ADD COLUMN remote_id VARCHAR(80) DEFAULT ''"))
        # wa_conversas: coluna foto_url (foto de perfil do contato)
        if wcols and 'foto_url' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN foto_url VARCHAR(600) DEFAULT ''"))
        # wa_conversas: ficha do cliente (CRM)
        _add = {
            'tag': "VARCHAR(80) DEFAULT ''", 'origem': "VARCHAR(80) DEFAULT ''",
            'responsavel': "VARCHAR(120) DEFAULT ''", 'email': "VARCHAR(200) DEFAULT ''",
            'handle': "VARCHAR(120) DEFAULT ''", 'valor': "FLOAT DEFAULT 0", 'notas': "TEXT DEFAULT ''",
        }
        for col, ddl in _add.items():
            if wcols and col not in wcols:
                await conn.execute(text(f"ALTER TABLE wa_conversas ADD COLUMN {col} {ddl}"))
        # wa_stages: coluna descricao (texto de ajuda da coluna do CRM)
        res = await conn.execute(text("PRAGMA table_info(wa_stages)"))
        scols = {row[1] for row in res.fetchall()}
        if scols and 'descricao' not in scols:
            await conn.execute(text("ALTER TABLE wa_stages ADD COLUMN descricao TEXT DEFAULT ''"))
        # wa_conversas: colunas de triagem da fila "Nao lidas"
        if wcols and 'aceita' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN aceita BOOLEAN DEFAULT 0"))
        if wcols and 'recusada' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN recusada BOOLEAN DEFAULT 0"))
        # wa_pipelines: coluna dono_email (pipeline pessoal/privado)
        res = await conn.execute(text("PRAGMA table_info(wa_pipelines)"))
        pcols = {row[1] for row in res.fetchall()}
        if pcols and 'dono_email' not in pcols:
            await conn.execute(text("ALTER TABLE wa_pipelines ADD COLUMN dono_email VARCHAR(200) DEFAULT ''"))
        # wa_conversas: pasta do lead no Google Drive (subir arquivos do chat)
        if wcols and 'drive_pasta_id' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN drive_pasta_id VARCHAR(120) DEFAULT ''"))
        if wcols and 'drive_pasta_url' not in wcols:
            await conn.execute(text("ALTER TABLE wa_conversas ADD COLUMN drive_pasta_url VARCHAR(600) DEFAULT ''"))
        # wa_mensagens: link do arquivo depois de subir pro Drive
        res = await conn.execute(text("PRAGMA table_info(wa_mensagens)"))
        mcols = {row[1] for row in res.fetchall()}
        if mcols and 'midia_drive' not in mcols:
            await conn.execute(text("ALTER TABLE wa_mensagens ADD COLUMN midia_drive VARCHAR(600) DEFAULT ''"))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
