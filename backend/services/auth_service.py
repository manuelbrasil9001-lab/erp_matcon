"""
auth_service.py — JWT, hashing de senha, verificação de permissões
"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from ..auth_models import Usuario, SessaoToken, ROLES_PADRAO

# Configurações JWT
import os as _os
SECRET_KEY = _os.getenv(
    "JWT_SECRET",
    "erp-matcon-curitiba-pr-2025-chave-secreta-troque-em-producao"
)
ALGORITHM  = "HS256"
EXPIRACAO_HORAS = 8

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── SENHA ────────────────────────────────────────────────────────────────────

def hash_senha(senha: str) -> str:
    return pwd_ctx.hash(senha)

def verificar_senha(senha: str, hash_: str) -> bool:
    return pwd_ctx.verify(senha, hash_)


# ── JWT ──────────────────────────────────────────────────────────────────────

def criar_token(usuario: Usuario) -> str:
    expira = datetime.now(timezone.utc) + timedelta(hours=EXPIRACAO_HORAS)
    payload = {
        "sub":   usuario.id,
        "email": usuario.email,
        "nome":  usuario.nome,
        "role":  usuario.role,
        "exp":   expira,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decodificar_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── LOGIN ────────────────────────────────────────────────────────────────────

def autenticar(db: Session, email: str, senha: str) -> Optional[Usuario]:
    usuario = db.query(Usuario).filter(
        Usuario.email == email,
        Usuario.ativo == True,
    ).first()
    if not usuario:
        return None
    if not verificar_senha(senha, usuario.senha_hash):
        return None
    # Atualiza último acesso
    usuario.ultimo_acesso = datetime.utcnow()
    db.commit()
    return usuario


# ── PERMISSÕES ───────────────────────────────────────────────────────────────

def obter_permissoes(usuario: Usuario) -> dict:
    """
    Retorna as permissões efetivas do usuário.
    Permissões customizadas (permissoes_json) sobrescrevem as do role.
    """
    base = ROLES_PADRAO.get(usuario.role, ROLES_PADRAO["vendedor"])["permissoes"].copy()
    if usuario.permissoes_json:
        for modulo, perms in usuario.permissoes_json.items():
            if modulo in base:
                base[modulo].update(perms)
            else:
                base[modulo] = perms
    return base

def pode(usuario: Usuario, modulo: str, acao: str = "visualizar") -> bool:
    """
    Verifica se o usuário tem permissão para uma ação em um módulo.
    Ações: visualizar, criar, editar, excluir
    """
    perms = obter_permissoes(usuario)
    return perms.get(modulo, {}).get(acao, False)


# ── CRUD DE USUÁRIOS ─────────────────────────────────────────────────────────

def criar_usuario(
    db: Session,
    nome: str,
    email: str,
    senha: str,
    role: str,
    criado_por_id: str,
    nome_operador: str = None,
    permissoes_custom: dict = None,
) -> Usuario:
    if db.query(Usuario).filter(Usuario.email == email).first():
        raise ValueError(f"E-mail '{email}' já está em uso.")
    u = Usuario(
        nome=nome,
        email=email,
        senha_hash=hash_senha(senha),
        role=role,
        nome_operador=nome_operador or nome.split()[0],
        criado_por=criado_por_id,
        permissoes_json=permissoes_custom,
        primeiro_login=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u

def trocar_senha(db: Session, usuario: Usuario, senha_atual: str, nova_senha: str) -> bool:
    if not verificar_senha(senha_atual, usuario.senha_hash):
        return False
    usuario.senha_hash = hash_senha(nova_senha)
    usuario.primeiro_login = False
    db.commit()
    return True

def trocar_senha_admin(db: Session, usuario: Usuario, nova_senha: str) -> None:
    """Admin pode resetar senha sem precisar da senha atual."""
    usuario.senha_hash = hash_senha(nova_senha)
    usuario.primeiro_login = True   # força troca no próximo login
    db.commit()
