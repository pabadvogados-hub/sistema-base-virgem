"""Seguranca: hash de senha (PBKDF2, stdlib) + emissao/validacao de JWT.

Sem dependencias nativas (bcrypt exige compilacao) - usa hashlib.pbkdf2_hmac.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt import PyJWTError

from .config import settings

_PBKDF2_ROUNDS = 200_000
_ALGO = 'sha256'


def hash_senha(senha: str) -> str:
    """Gera hash pbkdf2 no formato: pbkdf2_sha256$rounds$salt_hex$hash_hex."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, senha.encode('utf-8'), salt, _PBKDF2_ROUNDS)
    return f'pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}'


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = hash_armazenado.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(_ALGO, senha.encode('utf-8'), salt, int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def criar_token(email: str, papel: str, nome: str) -> str:
    agora = datetime.now(timezone.utc)
    payload = {
        'sub': email,
        'papel': papel,
        'nome': nome,
        'iat': agora,
        'exp': agora + timedelta(hours=settings.JWT_EXPIRE_HORAS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm='HS256')


def decodificar_token(token: str) -> dict:
    """Retorna o payload ou levanta PyJWTError."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=['HS256'])
