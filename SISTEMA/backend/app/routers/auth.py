"""Auth + gestao de usuarios (login e-mail/senha, JWT proprio)."""
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, Usuario
from ..security import hash_senha, verificar_senha, criar_token
from ..deps import usuario_atual, requer_admin

router = APIRouter(prefix='/api/auth', tags=['auth'])

PAPEIS_VALIDOS = {'admin', 'advogado', 'usuario'}

# ---- Rate limit de login (anti forca-bruta) — in-memory, por IP e por e-mail ----
# Limite por E-MAIL baixo (barra forca-bruta numa conta). Por IP generoso, pra nao
# travar o escritorio inteiro quando todos saem por um IP so (NAT).
_LOGIN_FALHAS: dict[str, list[float]] = {}
_LOGIN_LIMITE_EMAIL = 8
_LOGIN_LIMITE_IP = 30
_LOGIN_JANELA = 900        # 15 minutos


def _cliente_ip(request: Request) -> str:
    xff = request.headers.get('x-forwarded-for', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.client.host if request.client else 'desconhecido'


def _rate_contar(chave: str) -> int:
    agora = time.time()
    recentes = [t for t in _LOGIN_FALHAS.get(chave, []) if agora - t < _LOGIN_JANELA]
    if recentes:
        _LOGIN_FALHAS[chave] = recentes
    else:
        _LOGIN_FALHAS.pop(chave, None)
    return len(recentes)


def _rate_registrar_falha(*chaves: str) -> None:
    agora = time.time()
    for c in chaves:
        _LOGIN_FALHAS.setdefault(c, []).append(agora)


def _rate_limpar(*chaves: str) -> None:
    for c in chaves:
        _LOGIN_FALHAS.pop(c, None)


# ============================================================
# MODELS
# ============================================================
class LoginRequest(BaseModel):
    email: EmailStr
    senha: str


class NovoUsuario(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    papel: str = 'usuario'
    acesso_financeiro: bool = False


class EditarUsuario(BaseModel):
    nome: Optional[str] = None
    papel: Optional[str] = None
    acesso_financeiro: Optional[bool] = None
    ativo: Optional[bool] = None


class TrocarSenha(BaseModel):
    senha_atual: str
    nova_senha: str


class ResetSenha(BaseModel):
    nova_senha: str


# ============================================================
# LOGIN / PERFIL
# ============================================================
@router.post('/login')
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    email = payload.email.lower().strip()
    chave_ip = 'ip:' + _cliente_ip(request)
    chave_email = 'email:' + email
    # trava anti forca-bruta: bloqueia se estourou o limite por e-mail (conta) ou por IP
    if _rate_contar(chave_email) >= _LOGIN_LIMITE_EMAIL or _rate_contar(chave_ip) >= _LOGIN_LIMITE_IP:
        raise HTTPException(429, 'Muitas tentativas de login. Aguarde alguns minutos e tente de novo.',
                            headers={'Retry-After': str(_LOGIN_JANELA)})

    res = await db.execute(select(Usuario).where(Usuario.email == email))
    user = res.scalar_one_or_none()
    if user is None or not verificar_senha(payload.senha, user.senha_hash):
        _rate_registrar_falha(chave_ip, chave_email)
        raise HTTPException(401, 'E-mail ou senha invalidos')
    if not user.ativo:
        raise HTTPException(403, 'Usuario desativado. Fale com o administrador.')

    _rate_limpar(chave_ip, chave_email)   # login ok zera o contador
    user.ultimo_login = datetime.utcnow()
    await db.commit()

    token = criar_token(user.email, user.papel, user.nome)
    return {'token': token, 'usuario': user.to_dict()}


@router.get('/me')
async def me(user: Usuario = Depends(usuario_atual)):
    return user.to_dict()


@router.post('/trocar-senha')
async def trocar_senha(
    payload: TrocarSenha,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db),
):
    if not verificar_senha(payload.senha_atual, user.senha_hash):
        raise HTTPException(400, 'Senha atual incorreta')
    if len(payload.nova_senha) < 6:
        raise HTTPException(400, 'A nova senha deve ter ao menos 6 caracteres')
    user.senha_hash = hash_senha(payload.nova_senha)
    await db.commit()
    return {'ok': True}


# ============================================================
# GESTAO DE USUARIOS (somente admin)
# ============================================================
@router.get('/usuarios')
async def listar_usuarios(admin: Usuario = Depends(requer_admin), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Usuario).order_by(Usuario.nome))
    return {'items': [u.to_dict() for u in res.scalars().all()]}


@router.post('/usuarios')
async def criar_usuario(
    payload: NovoUsuario,
    admin: Usuario = Depends(requer_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.papel not in PAPEIS_VALIDOS:
        raise HTTPException(400, f'Papel invalido. Use: {", ".join(PAPEIS_VALIDOS)}')
    if len(payload.senha) < 6:
        raise HTTPException(400, 'A senha deve ter ao menos 6 caracteres')
    email = payload.email.lower().strip()
    existe = await db.execute(select(Usuario).where(Usuario.email == email))
    if existe.scalar_one_or_none():
        raise HTTPException(409, 'Ja existe usuario com esse e-mail')

    user = Usuario(
        nome=payload.nome.strip(),
        email=email,
        senha_hash=hash_senha(payload.senha),
        papel=payload.papel,
        acesso_financeiro=payload.acesso_financeiro or payload.papel == 'admin',
        ativo=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user.to_dict()


@router.patch('/usuarios/{user_id}')
async def editar_usuario(
    user_id: int,
    payload: EditarUsuario,
    admin: Usuario = Depends(requer_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(Usuario, user_id)
    if not user:
        raise HTTPException(404, 'Usuario nao encontrado')
    # Blindagem: nao deixa o admin se auto-desativar nem se rebaixar (evita travar o acesso)
    if user.id == admin.id:
        if payload.ativo is False:
            raise HTTPException(400, 'Voce nao pode desativar o seu proprio usuario')
        if payload.papel is not None and payload.papel != 'admin':
            raise HTTPException(400, 'Voce nao pode rebaixar o seu proprio usuario admin')
    if payload.nome is not None:
        user.nome = payload.nome.strip()
    if payload.papel is not None:
        if payload.papel not in PAPEIS_VALIDOS:
            raise HTTPException(400, 'Papel invalido')
        user.papel = payload.papel
    if payload.acesso_financeiro is not None:
        user.acesso_financeiro = payload.acesso_financeiro
    if payload.ativo is not None:
        user.ativo = payload.ativo
    # admin sempre tem financeiro
    if user.papel == 'admin':
        user.acesso_financeiro = True
    await db.commit()
    return user.to_dict()


@router.post('/usuarios/{user_id}/reset-senha')
async def reset_senha(
    user_id: int,
    payload: ResetSenha,
    admin: Usuario = Depends(requer_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(Usuario, user_id)
    if not user:
        raise HTTPException(404, 'Usuario nao encontrado')
    if len(payload.nova_senha) < 6:
        raise HTTPException(400, 'A senha deve ter ao menos 6 caracteres')
    user.senha_hash = hash_senha(payload.nova_senha)
    await db.commit()
    return {'ok': True}
