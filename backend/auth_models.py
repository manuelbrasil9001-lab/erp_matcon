"""
auth_models.py — Models de autenticação, roles e permissões
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from .database import Base


# Módulos do sistema — cada um pode ter permissão independente
MODULOS_SISTEMA = [
    "dashboard",
    "pdv",
    "fiscal",
    "estoque",
    "produtos",
    "clientes",
    "relatorios",
    "configuracoes",
    "usuarios",          # só admin
]

# Roles pré-definidos com permissões padrão
ROLES_PADRAO = {
    "admin": {
        "descricao": "Acesso total ao sistema",
        "permissoes": {m: {"visualizar": True, "criar": True, "editar": True, "excluir": True}
                       for m in MODULOS_SISTEMA},
    },
    "gerente": {
        "descricao": "Gerencia vendas, estoque e relatórios",
        "permissoes": {
            "dashboard":      {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "pdv":            {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "fiscal":         {"visualizar": True,  "criar": True,  "editar": False, "excluir": False},
            "estoque":        {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "produtos":       {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "clientes":       {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "relatorios":     {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "configuracoes":  {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "usuarios":       {"visualizar": False, "criar": False, "editar": False, "excluir": False},
        },
    },
    "vendedor": {
        "descricao": "Opera o PDV e consulta estoque",
        "permissoes": {
            "dashboard":      {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "pdv":            {"visualizar": True,  "criar": True,  "editar": False, "excluir": False},
            "fiscal":         {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "estoque":        {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "produtos":       {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "clientes":       {"visualizar": True,  "criar": True,  "editar": False, "excluir": False},
            "relatorios":     {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "configuracoes":  {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "usuarios":       {"visualizar": False, "criar": False, "editar": False, "excluir": False},
        },
    },
    "estoquista": {
        "descricao": "Gerencia estoque e produtos",
        "permissoes": {
            "dashboard":      {"visualizar": True,  "criar": False, "editar": False, "excluir": False},
            "pdv":            {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "fiscal":         {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "estoque":        {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "produtos":       {"visualizar": True,  "criar": True,  "editar": True,  "excluir": False},
            "clientes":       {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "relatorios":     {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "configuracoes":  {"visualizar": False, "criar": False, "editar": False, "excluir": False},
            "usuarios":       {"visualizar": False, "criar": False, "editar": False, "excluir": False},
        },
    },
}


class Usuario(Base):
    __tablename__ = "usuarios"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    nome            = Column(String(100), nullable=False)
    email           = Column(String(120), unique=True, nullable=False, index=True)
    senha_hash      = Column(String(200), nullable=False)
    role            = Column(String(30), nullable=False, default="vendedor")
    # Permissões customizadas — sobrescrevem as do role se presentes
    permissoes_json = Column(JSON, nullable=True)
    ativo           = Column(Boolean, default=True)
    primeiro_login  = Column(Boolean, default=True)   # força trocar senha no primeiro acesso
    criado_por      = Column(String, ForeignKey("usuarios.id"), nullable=True)
    criado_em       = Column(DateTime, default=datetime.utcnow)
    ultimo_acesso   = Column(DateTime, nullable=True)
    # Identificação na nota fiscal
    nome_operador   = Column(String(60), nullable=True)  # Nome que aparece na NF-e

    vendas          = relationship("Venda", back_populates="operador",
                                   foreign_keys="Venda.operador_id")


class SessaoToken(Base):
    """Tabela de tokens JWT emitidos — permite invalidar sessões."""
    __tablename__ = "sessoes_token"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    usuario_id  = Column(String, ForeignKey("usuarios.id"), nullable=False)
    token_hash  = Column(String(64), unique=True, nullable=False)
    expira_em   = Column(DateTime, nullable=False)
    ativo       = Column(Boolean, default=True)
    criado_em   = Column(DateTime, default=datetime.utcnow)
    ip_origem   = Column(String(45), nullable=True)
