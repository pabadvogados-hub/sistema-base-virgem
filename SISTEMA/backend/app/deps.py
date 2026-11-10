"""Dependencies de autenticacao e autorizacao."""
from datetime import datetime
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, Usuario
from .security import decodificar_token

security = HTTPBearer(auto_error=False)


async def usuario_atual(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Usuario:
    """Valida o JWT do sistema e devolve o Usuario (ativo)."""
    if credentials is None:
        raise HTTPException(401, 'Authorization: Bearer <token> obrigatorio')
    try:
        payload = decodificar_token(credentials.credentials)
    except PyJWTError as e:
        raise HTTPException(401, f'Token invalido ou expirado: {e}')

    # Token do Portal do Cliente nunca vale no sistema interno.
    if payload.get('tipo') == 'cliente':
        raise HTTPException(401, 'Token do portal nao vale aqui')

    email = (payload.get('sub') or '').lower().strip()
    res = await db.execute(select(Usuario).where(Usuario.email == email))
    user = res.scalar_one_or_none()
    if user is None or not user.ativo:
        raise HTTPException(401, 'Usuario inexistente ou inativo')
    return user


async def requer_admin(user: Usuario = Depends(usuario_atual)) -> Usuario:
    if user.papel != 'admin':
        raise HTTPException(403, 'Acao restrita a administradores')
    return user


async def requer_financeiro(user: Usuario = Depends(usuario_atual)) -> Usuario:
    if user.papel != 'admin' and not user.acesso_financeiro:
        raise HTTPException(403, 'Sem acesso ao modulo Financeiro')
    return user
