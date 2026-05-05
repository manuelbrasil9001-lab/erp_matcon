"""
Banco de dados — suporta SQLite (local) e PostgreSQL (Railway/produção)
A variável DATABASE_URL define qual banco usar:
  - Não definida / começa com sqlite → SQLite local
  - Começa com postgresql → PostgreSQL (Railway, Render, Supabase)
"""
import os
from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Boolean,
    DateTime, Text, ForeignKey, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import uuid

# ── URL do banco ─────────────────────────────────────────────
_DB_URL = os.getenv("DATABASE_URL", "")

if _DB_URL.startswith("postgres://"):
    # Railway usa postgres:// mas SQLAlchemy precisa de postgresql://
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)

if not _DB_URL or _DB_URL.startswith("sqlite"):
    # Desenvolvimento local → SQLite
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _DB_URL = f"sqlite:///{os.path.join(_BASE_DIR, 'erp.db')}"
    engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})
else:
    # Produção → PostgreSQL
    engine = create_engine(_DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)

DATABASE_URL = _DB_URL
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── MODELS ───────────────────────────────────────────────────

class Produto(Base):
    __tablename__ = "produtos"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sku         = Column(String, unique=True, index=True)
    nome        = Column(String, nullable=False)
    ncm         = Column(String(8), nullable=False)
    cfop        = Column(String(4), default="5102")
    csosn       = Column(String(4), default="400")
    origem      = Column(Integer, default=0)
    unidade     = Column(String(10), nullable=False)
    preco_custo = Column(Float, default=0)
    preco_venda = Column(Float, nullable=False)
    estoque     = Column(Float, default=0)
    estoque_min = Column(Float, default=0)
    ativo       = Column(Boolean, default=True)
    bling_id    = Column(Integer, nullable=True)
    categoria   = Column(String, default="Geral")


class Cliente(Base):
    __tablename__ = "clientes"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    nome        = Column(String, nullable=False)
    cpf_cnpj    = Column(String, nullable=True)
    tipo_pessoa = Column(String(1), default="F")
    telefone    = Column(String, nullable=True)
    email       = Column(String, nullable=True)
    cidade      = Column(String, default="Curitiba")
    uf          = Column(String(2), default="PR")


class Venda(Base):
    __tablename__ = "vendas"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    numero          = Column(String, unique=True)
    canal           = Column(String, default="balcao")
    tipo_doc        = Column(String, default="nfce")
    status          = Column(String, default="rascunho")
    status_fiscal   = Column(String, default="pendente")
    cliente_id      = Column(String, ForeignKey("clientes.id"), nullable=True)
    cliente_nome    = Column(String, default="CONSUMIDOR FINAL")
    cpf_nota        = Column(String, nullable=True)
    forma_pgto      = Column(String, default="pix_dinamico")
    subtotal        = Column(Float, default=0)
    desconto        = Column(Float, default=0)
    total           = Column(Float, default=0)
    valor_icms      = Column(Float, default=0)
    chave_acesso    = Column(String, nullable=True)
    danfe_url       = Column(String, nullable=True)
    bling_nfe_id    = Column(Integer, nullable=True)
    observacoes     = Column(Text, nullable=True)
    operador_id     = Column(String, ForeignKey("usuarios.id"), nullable=True)
    operador_nome   = Column(String, nullable=True)
    criado_em       = Column(DateTime, default=datetime.utcnow)
    itens           = relationship("ItemVenda", back_populates="venda", cascade="all, delete-orphan")
    operador        = relationship("Usuario", back_populates="vendas", foreign_keys=[operador_id])


class ItemVenda(Base):
    __tablename__ = "itens_venda"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    venda_id        = Column(String, ForeignKey("vendas.id"), nullable=False)
    produto_id      = Column(String, ForeignKey("produtos.id"), nullable=False)
    nome_snapshot   = Column(String, nullable=False)
    ncm_snapshot    = Column(String(8), nullable=False)
    cfop_snapshot   = Column(String(4), nullable=False)
    unidade         = Column(String(10), nullable=False)
    quantidade      = Column(Float, nullable=False)
    qtd_entregue    = Column(Float, default=0)
    preco_unitario  = Column(Float, nullable=False)
    desconto        = Column(Float, default=0)
    total           = Column(Float, nullable=False)
    venda           = relationship("Venda", back_populates="itens")


class MovimentacaoEstoque(Base):
    __tablename__ = "movimentacoes_estoque"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    produto_id  = Column(String, ForeignKey("produtos.id"))
    tipo        = Column(String)
    quantidade  = Column(Float)
    saldo_antes = Column(Float)
    saldo_apos  = Column(Float)
    referencia  = Column(String, nullable=True)
    criado_em   = Column(DateTime, default=datetime.utcnow)


class WebhookEvento(Base):
    __tablename__ = "webhook_eventos"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tipo        = Column(String)
    payload     = Column(Text)
    processado  = Column(Boolean, default=False)
    criado_em   = Column(DateTime, default=datetime.utcnow)


# ── SEED ─────────────────────────────────────────────────────

PRODUTOS_SEED = [
    {"sku":"CIM-CP2-50","nome":"Cimento CP-II 50kg","ncm":"25232900","cfop":"5102","csosn":"400","unidade":"SC","preco_custo":25.50,"preco_venda":32.90,"estoque":248,"estoque_min":50,"categoria":"Cimento/Argamassa"},
    {"sku":"CIM-CP5-50","nome":"Cimento CP-V ARI 50kg","ncm":"25232900","cfop":"5102","csosn":"400","unidade":"SC","preco_custo":28.00,"preco_venda":36.50,"estoque":120,"estoque_min":30,"categoria":"Cimento/Argamassa"},
    {"sku":"ARG-AC2-20","nome":"Argamassa AC-II 20kg","ncm":"38244090","cfop":"5102","csosn":"400","unidade":"SC","preco_custo":12.00,"preco_venda":18.90,"estoque":85,"estoque_min":20,"categoria":"Cimento/Argamassa"},
    {"sku":"ARE-MED-M3","nome":"Areia Média m³","ncm":"25171000","cfop":"5102","csosn":"400","unidade":"M3","preco_custo":120.00,"preco_venda":185.00,"estoque":1.5,"estoque_min":5,"categoria":"Agregados"},
    {"sku":"PED-BRI-1","nome":"Pedra Brita nº 1","ncm":"25171010","cfop":"5102","csosn":"400","unidade":"M3","preco_custo":95.00,"preco_venda":145.00,"estoque":0,"estoque_min":5,"categoria":"Agregados"},
    {"sku":"TIJ-CER-9","nome":"Tijolo Cerâmico 9x19x19","ncm":"69041000","cfop":"5102","csosn":"400","unidade":"MI","preco_custo":220.00,"preco_venda":310.00,"estoque":38,"estoque_min":5,"categoria":"Cerâmica/Alvenaria"},
    {"sku":"CER-60-BRA","nome":"Cerâmica 60x60 Branca","ncm":"69072100","cfop":"5102","csosn":"400","unidade":"M2","preco_custo":28.00,"preco_venda":45.90,"estoque":180,"estoque_min":30,"categoria":"Cerâmica/Alvenaria"},
    {"sku":"TIN-SUV-18","nome":"Tinta Suvinil Acrílica 18L","ncm":"32091010","cfop":"5102","csosn":"400","unidade":"UN","preco_custo":145.00,"preco_venda":219.90,"estoque":24,"estoque_min":6,"categoria":"Tintas"},
    {"sku":"TUB-PVC-50","nome":"Tubo PVC Esgoto 50mm 6m","ncm":"39172900","cfop":"5102","csosn":"400","unidade":"UN","preco_custo":18.50,"preco_venda":29.90,"estoque":62,"estoque_min":10,"categoria":"Hidráulico"},
    {"sku":"FER-CA50-10","nome":"Ferro CA-50 10mm 12m","ncm":"72142000","cfop":"5102","csosn":"400","unidade":"UN","preco_custo":42.00,"preco_venda":68.00,"estoque":45,"estoque_min":10,"categoria":"Ferragem"},
]

CLIENTES_SEED = [
    {"nome":"Construtora Alfa Ltda","cpf_cnpj":"29.270.156/0001-86","tipo_pessoa":"J","telefone":"(41) 3333-4444","email":"compras@alfa.com.br","cidade":"Curitiba","uf":"PR"},
    {"nome":"João da Silva","cpf_cnpj":"123.456.789-09","tipo_pessoa":"F","telefone":"(41) 99999-1111","cidade":"Curitiba","uf":"PR"},
    {"nome":"Maria Oliveira","cpf_cnpj":"987.654.321-00","tipo_pessoa":"F","telefone":"(41) 98888-2222","cidade":"Pinhais","uf":"PR"},
    {"nome":"Reforma Rápida ME","cpf_cnpj":"12.345.678/0001-90","tipo_pessoa":"J","telefone":"(41) 3222-5555","cidade":"São José dos Pinhais","uf":"PR"},
]


def seed(db):
    from sqlalchemy import text
    tem_produtos = False
    try:
        tem_produtos = db.execute(text("SELECT COUNT(*) FROM produtos")).scalar() > 0
    except Exception:
        pass

    if not tem_produtos:
        for p in PRODUTOS_SEED:
            db.add(Produto(**p))
        for c in CLIENTES_SEED:
            db.add(Cliente(**c))

    from .auth_models import Usuario
    from .services.auth_service import hash_senha

    try:
        tem_usuarios = db.execute(text("SELECT COUNT(*) FROM usuarios")).scalar() > 0
    except Exception:
        tem_usuarios = False

    if not tem_usuarios:
        admin = Usuario(
            nome="Administrador", email="admin@matcon.com.br",
            senha_hash=hash_senha("admin123"), role="admin",
            nome_operador="Admin", primeiro_login=False, ativo=True,
        )
        db.add(admin)
        db.flush()
        for nome, email, senha, role, op in [
            ("Carlos Gerente","gerente@matcon.com.br","gerente123","gerente","Carlos"),
            ("João Vendedor", "joao@matcon.com.br",   "joao123",   "vendedor","João"),
            ("Maria Estoque", "maria@matcon.com.br",   "maria123",  "estoquista","Maria"),
        ]:
            db.add(Usuario(nome=nome, email=email, senha_hash=hash_senha(senha),
                           role=role, nome_operador=op, criado_por=admin.id,
                           primeiro_login=(role=="gerente"), ativo=True))

    db.commit()


def _migrar_banco(eng):
    """Adiciona colunas novas em bancos antigos sem apagar dados."""
    from sqlalchemy import inspect, text
    inspector = inspect(eng)
    tabelas = inspector.get_table_names()
    with eng.connect() as conn:
        if "vendas" in tabelas:
            cols = [c["name"] for c in inspector.get_columns("vendas")]
            if "operador_id" not in cols:
                conn.execute(text("ALTER TABLE vendas ADD COLUMN operador_id VARCHAR"))
                print("  [migração] vendas.operador_id adicionada")
            if "operador_nome" not in cols:
                conn.execute(text("ALTER TABLE vendas ADD COLUMN operador_nome VARCHAR"))
                print("  [migração] vendas.operador_nome adicionada")
        conn.commit()


def init_db():
    from . import auth_models  # noqa — registra os models
    Base.metadata.create_all(bind=engine)
    _migrar_banco(engine)
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
