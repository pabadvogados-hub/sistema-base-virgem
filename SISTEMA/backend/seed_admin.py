"""Cria (ou atualiza) o primeiro usuario administrador.

Uso:
    python seed_admin.py "Nome Completo" email@dominio "SENHA_FORTE"
    python seed_admin.py "Nome Completo" email@dominio            (gera senha aleatoria)

Se ja existir, apenas reseta a senha e garante papel admin.
"""
import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal, Usuario, init_db  # noqa: E402
from app.security import hash_senha  # noqa: E402


async def criar_ou_atualizar(nome: str, email: str, senha: str, papel: str = 'admin',
                             financeiro: bool = True) -> str:
    await init_db()
    email = email.lower().strip()
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Usuario).where(Usuario.email == email))
        user = res.scalar_one_or_none()
        if user:
            user.nome = nome
            user.senha_hash = hash_senha(senha)
            user.papel = papel
            user.acesso_financeiro = financeiro
            user.ativo = True
            acao = 'atualizado'
        else:
            db.add(Usuario(nome=nome, email=email, senha_hash=hash_senha(senha),
                           papel=papel, acesso_financeiro=financeiro, ativo=True))
            acao = 'criado'
        await db.commit()
    return acao


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Uso: python seed_admin.py "Nome" email@dominio ["senha"]')
        sys.exit(1)
    nome, email = sys.argv[1], sys.argv[2]
    senha = sys.argv[3] if len(sys.argv) > 3 else secrets.token_urlsafe(12)
    acao = asyncio.run(criar_ou_atualizar(nome, email, senha))
    print(f'Admin {acao}: {nome} <{email}>')
    if len(sys.argv) <= 3:
        print(f'Senha gerada: {senha}   (anote e troque no primeiro acesso)')
