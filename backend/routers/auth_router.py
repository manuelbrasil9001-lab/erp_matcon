"""
auth_router.py — Endpoints de autenticação e gestão de usuários
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

from ..database import get_db
from ..auth_models import Usuario, ROLES_PADRAO, MODULOS_SISTEMA
from ..services.auth_service import (
    autenticar, criar_token, decodificar_token,
    obter_permissoes, pode, criar_usuario,
    trocar_senha, trocar_senha_admin, hash_senha,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


# ── DEPENDÊNCIA: usuário logado ───────────────────────────────────────────────

def usuario_atual(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token não informado.")
    payload = decodificar_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado.")
    user = db.query(Usuario).filter(
        Usuario.id == payload["sub"],
        Usuario.ativo == True,
    ).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não encontrado.")
    return user

def requer_admin(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    if usuario.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso restrito a administradores.")
    return usuario

def requer_permissao(modulo: str, acao: str = "visualizar"):
    def dep(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
        if not pode(usuario, modulo, acao):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Sem permissão para '{acao}' em '{modulo}'."
            )
        return usuario
    return dep


# ── SCHEMAS ───────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    email: str
    senha: str

class CriarUsuarioIn(BaseModel):
    nome: str
    email: str
    senha: str
    role: str = "vendedor"
    nome_operador: Optional[str] = None
    permissoes_custom: Optional[dict] = None

class EditarUsuarioIn(BaseModel):
    nome: Optional[str] = None
    role: Optional[str] = None
    nome_operador: Optional[str] = None
    ativo: Optional[bool] = None
    permissoes_custom: Optional[dict] = None

class TrocarSenhaIn(BaseModel):
    senha_atual: str
    nova_senha: str

class ResetSenhaAdminIn(BaseModel):
    nova_senha: str


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    usuario = autenticar(db, data.email, data.senha)
    if not usuario:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha incorretos.")
    token = criar_token(usuario)
    permissoes = obter_permissoes(usuario)
    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "id":             usuario.id,
            "nome":           usuario.nome,
            "email":          usuario.email,
            "role":           usuario.role,
            "nome_operador":  usuario.nome_operador,
            "primeiro_login": usuario.primeiro_login,
            "permissoes":     permissoes,
        }
    }

@router.get("/me")
def me(usuario: Usuario = Depends(usuario_atual)):
    return {
        "id":             usuario.id,
        "nome":           usuario.nome,
        "email":          usuario.email,
        "role":           usuario.role,
        "nome_operador":  usuario.nome_operador,
        "primeiro_login": usuario.primeiro_login,
        "ultimo_acesso":  usuario.ultimo_acesso.isoformat() if usuario.ultimo_acesso else None,
        "permissoes":     obter_permissoes(usuario),
    }

@router.post("/trocar-senha")
def trocar_senha_endpoint(
    data: TrocarSenhaIn,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
):
    if len(data.nova_senha) < 6:
        raise HTTPException(400, "A nova senha deve ter pelo menos 6 caracteres.")
    ok = trocar_senha(db, usuario, data.senha_atual, data.nova_senha)
    if not ok:
        raise HTTPException(400, "Senha atual incorreta.")
    return {"mensagem": "Senha alterada com sucesso."}

@router.get("/roles")
def listar_roles(usuario: Usuario = Depends(usuario_atual)):
    return [
        {"id": k, "descricao": v["descricao"], "permissoes": v["permissoes"]}
        for k, v in ROLES_PADRAO.items()
    ]

@router.get("/modulos")
def listar_modulos(usuario: Usuario = Depends(usuario_atual)):
    return MODULOS_SISTEMA


# ── GESTÃO DE USUÁRIOS (admin) ────────────────────────────────────────────────

@router.get("/usuarios")
def listar_usuarios(
    admin: Usuario = Depends(requer_admin),
    db: Session = Depends(get_db),
):
    usuarios = db.query(Usuario).order_by(Usuario.nome).all()
    return [
        {
            "id":            u.id,
            "nome":          u.nome,
            "email":         u.email,
            "role":          u.role,
            "nome_operador": u.nome_operador,
            "ativo":         u.ativo,
            "primeiro_login":u.primeiro_login,
            "ultimo_acesso": u.ultimo_acesso.isoformat() if u.ultimo_acesso else None,
            "permissoes":    obter_permissoes(u),
        }
        for u in usuarios
    ]

@router.post("/usuarios")
def criar_usuario_endpoint(
    data: CriarUsuarioIn,
    admin: Usuario = Depends(requer_admin),
    db: Session = Depends(get_db),
):
    if data.role not in ROLES_PADRAO:
        raise HTTPException(400, f"Role inválido. Opções: {list(ROLES_PADRAO.keys())}")
    if len(data.senha) < 6:
        raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres.")
    try:
        u = criar_usuario(
            db=db,
            nome=data.nome,
            email=data.email,
            senha=data.senha,
            role=data.role,
            criado_por_id=admin.id,
            nome_operador=data.nome_operador,
            permissoes_custom=data.permissoes_custom,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": u.id, "mensagem": f"Usuário '{u.nome}' criado com sucesso."}

@router.put("/usuarios/{usuario_id}")
def editar_usuario(
    usuario_id: str,
    data: EditarUsuarioIn,
    admin: Usuario = Depends(requer_admin),
    db: Session = Depends(get_db),
):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado.")
    if data.role and data.role not in ROLES_PADRAO:
        raise HTTPException(400, f"Role inválido.")
    # Protege o admin principal de ser desativado por outro admin
    if u.role == "admin" and data.ativo is False:
        admins_ativos = db.query(Usuario).filter(
            Usuario.role == "admin", Usuario.ativo == True
        ).count()
        if admins_ativos <= 1:
            raise HTTPException(400, "Não é possível desativar o único administrador.")
    if data.nome:          u.nome = data.nome
    if data.role:          u.role = data.role
    if data.nome_operador: u.nome_operador = data.nome_operador
    if data.ativo is not None: u.ativo = data.ativo
    if data.permissoes_custom is not None: u.permissoes_json = data.permissoes_custom
    db.commit()
    return {"mensagem": f"Usuário '{u.nome}' atualizado."}

@router.post("/usuarios/{usuario_id}/reset-senha")
def reset_senha(
    usuario_id: str,
    data: ResetSenhaAdminIn,
    admin: Usuario = Depends(requer_admin),
    db: Session = Depends(get_db),
):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado.")
    if len(data.nova_senha) < 6:
        raise HTTPException(400, "Senha deve ter pelo menos 6 caracteres.")
    trocar_senha_admin(db, u, data.nova_senha)
    return {"mensagem": f"Senha de '{u.nome}' redefinida. Usuário deverá trocar no próximo login."}

@router.delete("/usuarios/{usuario_id}")
def desativar_usuario(
    usuario_id: str,
    admin: Usuario = Depends(requer_admin),
    db: Session = Depends(get_db),
):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado.")
    if u.id == admin.id:
        raise HTTPException(400, "Você não pode desativar sua própria conta.")
    u.ativo = False
    db.commit()
    return {"mensagem": f"Usuário '{u.nome}' desativado."}
