"""Configuracoes do Sistema Escritório Modelo Advogados.

Tudo que e do escritorio (nome, CNPJ, OAB, dominio, telefones, chaves) vem do
config/.env central (modelo em config/.env.example). Um .env local em
SISTEMA/backend/.env tem prioridade sobre o central (util para testes).

Nada aqui tem valor real de producao: os padroes sao neutros e vazios.
"""
import logging
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app -> backend -> SISTEMA -> raiz do projeto
PROJETO_ROOT = Path(__file__).parent.parent.parent.parent
ENV_CENTRAL = PROJETO_ROOT / 'config' / '.env'
ENV_LOCAL = Path(__file__).parent.parent / '.env'

_log = logging.getLogger('config')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ENV_CENTRAL), str(ENV_LOCAL)),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # ===== Ambiente =====
    # 'producao' exige JWT_SECRET definido; 'dev' gera um segredo temporario.
    AMBIENTE: str = 'dev'

    # ===== Escritorio (white-label) =====
    ESCRITORIO_NOME: str = 'Escritório Modelo Advogados'
    ESCRITORIO_NOME_CURTO: str = 'Escritório Modelo'
    ESCRITORIO_RAZAO_SOCIAL: str = ''
    ESCRITORIO_CNPJ: str = ''
    ESCRITORIO_OAB_SOCIEDADE: str = ''
    ESCRITORIO_ENDERECO: str = ''
    ESCRITORIO_CIDADE: str = 'Cidade'
    ESCRITORIO_UF: str = 'SP'
    ESCRITORIO_EMAIL: str = ''
    ESCRITORIO_DOMINIO: str = ''          # ex.: sistema.escritorio.adv.br (A CONFIRMAR)
    ADVOGADO_TITULAR_NOME: str = ''
    ADVOGADO_TITULAR_OAB: str = ''
    ADVOGADA_RESPONSAVEL_NOME: str = ''   # quem assina, revisa e e dona das tarefas
    ADVOGADA_RESPONSAVEL_OAB: str = ''
    ADVOGADA_RESPONSAVEL_TEL: str = ''    # WhatsApp que recebe alertas de prazo

    # ===== Auth self-hosted =====
    JWT_SECRET: str = ''
    JWT_EXPIRE_HORAS: int = 12
    # Token do cron (motor de fluxos roda sem login, protegido por este token)
    CRON_TOKEN: str = ''
    # Segredo do webhook do WhatsApp (/webhook/{id}/{WEBHOOK_SECRET})
    WEBHOOK_SECRET: str = ''

    # ===== IA =====
    ANTHROPIC_API_KEY: str = ''
    JURISPRUDENCIAS_AI_TOKEN: str = ''

    # ===== Sistema juridico =====
    # Astrea (so liga se o plano tiver API). ADVBOX fica opcional/legado.
    ASTREA_API_TOKEN: str = ''
    ADVBOX_API_TOKEN: str = ''

    # ===== Publicacoes / Controladoria (SNC ou SYNC) =====
    SYNC_API_TOKEN: str = ''
    SYNC_API_BASE: str = ''
    SYNC_CACHE_SEGUNDOS: int = 300
    CONTROLADORIA_ALERTA_ON: bool = True
    CONTROLADORIA_ALERTA_TEL: str = ''    # WhatsApp que recebe intimacao com prazo
    CONTROLADORIA_LEMBRETE_ON: bool = True
    CONTROLADORIA_LEMBRETE_DIAS: int = 3

    # ===== Transcricao de audio (STT) — Groq ou OpenAI =====
    STT_API_KEY: str = ''
    STT_BASE_URL: str = 'https://api.groq.com/openai/v1'
    STT_MODEL: str = 'whisper-large-v3-turbo'

    # ===== CORS =====
    CORS_ORIGINS: str = 'http://localhost:5173,http://localhost:3000'

    # ===== Banco =====
    DATABASE_URL: str = 'sqlite+aiosqlite:///./sistema_esc.db'

    @property
    def cors_origins_list(self) -> list[str]:
        origens = [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]
        if self.ESCRITORIO_DOMINIO:
            origens.append(f'https://{self.ESCRITORIO_DOMINIO.strip()}')
        return origens


settings = Settings()

if not settings.JWT_SECRET:
    if settings.AMBIENTE.lower() in ('producao', 'produção', 'prod'):
        raise RuntimeError(
            'JWT_SECRET nao definido em config/.env. Em producao ele e obrigatorio. '
            'Gere um com: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    # Dev: segredo temporario (os logins caem a cada reinicio)
    settings.JWT_SECRET = secrets.token_urlsafe(48)
    _log.warning('JWT_SECRET vazio: usando segredo temporario (so para desenvolvimento).')
