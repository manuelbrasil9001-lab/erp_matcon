"""
Routers FastAPI — todos os endpoints do ERP MatCon
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

from ..database import get_db, Produto, Cliente, Venda, ItemVenda, MovimentacaoEstoque, WebhookEvento
from ..services.bling_service import BlingService

router = APIRouter()

# Token Bling configurável (em produção vem do banco/env)
_bling_token = ""

def get_bling():
    return BlingService(access_token=_bling_token)


# ── DASHBOARD ──────────────────────────────────────────────────────────────

@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    hoje = datetime.utcnow().date()
    vendas_hoje = db.query(func.sum(Venda.total)).filter(
        func.date(Venda.criado_em) == hoje
    ).scalar() or 0

    pedidos_abertos = db.query(func.count(Venda.id)).filter(
        Venda.status.in_(["rascunho","confirmado"])
    ).scalar() or 0

    nfe_pendentes = db.query(func.count(Venda.id)).filter(
        Venda.status_fiscal.in_(["pendente","rejeitada"])
    ).scalar() or 0

    estoque_critico = db.query(func.count(Produto.id)).filter(
        Produto.estoque <= Produto.estoque_min,
        Produto.ativo == True
    ).scalar() or 0

    # Últimas 5 vendas
    ultimas = db.query(Venda).order_by(desc(Venda.criado_em)).limit(5).all()

    # Distribuição por forma de pagamento (todas)
    pgtos = db.query(Venda.forma_pgto, func.count(Venda.id)).group_by(Venda.forma_pgto).all()

    return {
        "metricas": {
            "vendas_hoje": round(vendas_hoje, 2),
            "pedidos_abertos": pedidos_abertos,
            "nfe_pendentes": nfe_pendentes,
            "estoque_critico": estoque_critico,
        },
        "ultimas_vendas": [
            {"numero": v.numero, "cliente": v.cliente_nome,
             "total": v.total, "status_fiscal": v.status_fiscal,
             "tipo_doc": v.tipo_doc, "criado_em": v.criado_em.isoformat(),
             "operador_nome": v.operador_nome}
            for v in ultimas
        ],
        "pgto_distribuicao": [{"tipo": p, "qtd": q} for p, q in pgtos],
    }


# ── PRODUTOS ────────────────────────────────────────────────────────────────

@router.get("/api/produtos")
def listar_produtos(q: str = "", categoria: str = "", db: Session = Depends(get_db)):
    query = db.query(Produto).filter(Produto.ativo == True)
    if q:
        query = query.filter(Produto.nome.ilike(f"%{q}%") | Produto.sku.ilike(f"%{q}%"))
    if categoria:
        query = query.filter(Produto.categoria == categoria)
    produtos = query.order_by(Produto.nome).all()
    return [
        {"id": p.id, "sku": p.sku, "nome": p.nome, "ncm": p.ncm,
         "cfop": p.cfop, "csosn": p.csosn, "unidade": p.unidade,
         "preco_venda": p.preco_venda, "estoque": p.estoque,
         "estoque_min": p.estoque_min, "categoria": p.categoria,
         "estoque_critico": p.estoque <= p.estoque_min}
        for p in produtos
    ]

@router.get("/api/produtos/categorias")
def categorias(db: Session = Depends(get_db)):
    cats = db.query(Produto.categoria).distinct().order_by(Produto.categoria).all()
    return [c[0] for c in cats if c[0]]

class ProdutoCreate(BaseModel):
    sku: str; nome: str; ncm: str; cfop: str = "5102"
    csosn: str = "400"; unidade: str; preco_custo: float = 0
    preco_venda: float; estoque: float = 0; estoque_min: float = 0
    categoria: str = "Geral"

@router.post("/api/produtos")
def criar_produto(data: ProdutoCreate, db: Session = Depends(get_db)):
    if db.query(Produto).filter(Produto.sku == data.sku).first():
        raise HTTPException(400, "SKU já cadastrado")
    p = Produto(**data.dict())
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "mensagem": "Produto criado com sucesso"}


# ── CLIENTES ────────────────────────────────────────────────────────────────

@router.get("/api/clientes")
def listar_clientes(q: str = "", db: Session = Depends(get_db)):
    query = db.query(Cliente)
    if q:
        query = query.filter(Cliente.nome.ilike(f"%{q}%") | Cliente.cpf_cnpj.ilike(f"%{q}%"))
    clientes = query.order_by(Cliente.nome).limit(20).all()
    return [{"id":c.id,"nome":c.nome,"cpf_cnpj":c.cpf_cnpj,
             "tipo_pessoa":c.tipo_pessoa,"telefone":c.telefone,"cidade":c.cidade} for c in clientes]

class ClienteCreate(BaseModel):
    nome: str; cpf_cnpj: Optional[str] = None; tipo_pessoa: str = "F"
    telefone: Optional[str] = None; email: Optional[str] = None
    cidade: str = "Curitiba"; uf: str = "PR"

@router.post("/api/clientes")
def criar_cliente(data: ClienteCreate, db: Session = Depends(get_db)):
    c = Cliente(**data.dict())
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "mensagem": "Cliente criado"}


# ── VENDAS / PDV ────────────────────────────────────────────────────────────

class ItemVendaIn(BaseModel):
    produto_id: str; quantidade: float; desconto: float = 0

class VendaCreate(BaseModel):
    canal: str = "balcao"
    tipo_doc: str = "nfce"
    cliente_id: Optional[str] = None
    cliente_nome: str = "CONSUMIDOR FINAL"
    cpf_nota: Optional[str] = None
    forma_pgto: str = "pix_dinamico"
    desconto: float = 0
    observacoes: Optional[str] = None
    itens: List[ItemVendaIn]

@router.post("/api/vendas")
def criar_venda(
    data: VendaCreate,
    request: Request,
    db: Session = Depends(get_db),
    bling: BlingService = Depends(get_bling),
):
    # ── Identifica o operador pelo token JWT (opcional — fallback gracioso) ──
    operador_id   = None
    operador_nome = "Sistema"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from ..services.auth_service import decodificar_token
        from ..auth_models import Usuario
        payload = decodificar_token(auth_header[7:])
        if payload:
            op = db.query(Usuario).filter(Usuario.id == payload.get("sub")).first()
            if op:
                operador_id   = op.id
                operador_nome = op.nome_operador or op.nome
    # Busca produtos e calcula totais
    itens_completos = []
    subtotal = 0
    for it in data.itens:
        p = db.query(Produto).filter(Produto.id == it.produto_id).first()
        if not p:
            raise HTTPException(404, f"Produto {it.produto_id} não encontrado")
        if p.estoque < it.quantidade:
            raise HTTPException(400, f"Estoque insuficiente para '{p.nome}': disponível {p.estoque} {p.unidade}")
        total_item = round((it.quantidade * p.preco_venda) - it.desconto, 2)
        subtotal += total_item
        itens_completos.append({
            "produto_id": p.id, "sku": p.sku,
            "nome_snapshot": p.nome, "ncm_snapshot": p.ncm,
            "cfop_snapshot": p.cfop, "csosn": p.csosn,
            "unidade": p.unidade, "quantidade": it.quantidade,
            "preco_unitario": p.preco_venda, "desconto": it.desconto,
            "total": total_item,
        })

    total = round(subtotal - data.desconto, 2)
    numero = f"{'NFC' if data.tipo_doc=='nfce' else 'NFE'}-{str(uuid.uuid4())[:6].upper()}"

    # Cria a venda no banco
    venda = Venda(
        numero=numero, canal=data.canal, tipo_doc=data.tipo_doc,
        status="confirmado", status_fiscal="enviando",
        cliente_id=data.cliente_id, cliente_nome=data.cliente_nome,
        cpf_nota=data.cpf_nota, forma_pgto=data.forma_pgto,
        subtotal=subtotal, desconto=data.desconto, total=total,
        observacoes=data.observacoes,
        operador_id=operador_id,
        operador_nome=operador_nome,
    )
    db.add(venda); db.flush()

    campos_item = {"produto_id","nome_snapshot","ncm_snapshot","cfop_snapshot",
                   "unidade","quantidade","preco_unitario","desconto","total"}
    for it in itens_completos:
        db.add(ItemVenda(venda_id=venda.id, **{k:v for k,v in it.items() if k in campos_item}))

    db.commit()

    # Emite nota fiscal via Bling
    venda_dict = {"id": venda.id, "total": total, "cliente_nome": data.cliente_nome,
                  "cpf_nota": data.cpf_nota, "forma_pgto": data.forma_pgto}

    if data.tipo_doc == "nfce":
        resultado = bling.emitir_nfce(venda_dict, itens_completos)
    else:
        resultado = bling.emitir_nfe(venda_dict, itens_completos)

    # Atualiza venda com retorno do Bling
    venda.status_fiscal = resultado.get("status", "pendente")
    venda.chave_acesso = resultado.get("chave_acesso")
    venda.danfe_url = resultado.get("danfe_url")
    venda.bling_nfe_id = resultado.get("bling_nfe_id")

    # Baixa estoque somente se autorizada
    if resultado.get("status") == "autorizada":
        venda.status = "faturado"
        for it in itens_completos:
            p = db.query(Produto).filter(Produto.id == it["produto_id"]).first()
            saldo_antes = p.estoque
            p.estoque = round(p.estoque - it["quantidade"], 4)
            db.add(MovimentacaoEstoque(
                produto_id=p.id, tipo="saida_venda",
                quantidade=it["quantidade"], saldo_antes=saldo_antes,
                saldo_apos=p.estoque, referencia=venda.numero,
            ))

    db.commit()

    return {
        "venda_id": venda.id,
        "numero": venda.numero,
        "total": total,
        "resultado_fiscal": resultado,
    }

@router.get("/api/vendas")
def listar_vendas(status: str = "", tipo_doc: str = "", db: Session = Depends(get_db)):
    query = db.query(Venda).order_by(desc(Venda.criado_em))
    if status:
        query = query.filter(Venda.status_fiscal == status)
    if tipo_doc:
        query = query.filter(Venda.tipo_doc == tipo_doc)
    vendas = query.limit(50).all()
    return [
        {"id": v.id, "numero": v.numero, "canal": v.canal,
         "tipo_doc": v.tipo_doc, "status": v.status,
         "status_fiscal": v.status_fiscal, "cliente_nome": v.cliente_nome,
         "total": v.total, "chave_acesso": v.chave_acesso,
         "danfe_url": v.danfe_url, "observacoes": v.observacoes,
         "operador_nome": v.operador_nome,
         "criado_em": v.criado_em.isoformat()}
        for v in vendas
    ]

@router.get("/api/vendas/{venda_id}/itens")
def itens_venda(venda_id: str, db: Session = Depends(get_db)):
    itens = db.query(ItemVenda).filter(ItemVenda.venda_id == venda_id).all()
    return [{"nome": i.nome_snapshot, "ncm": i.ncm_snapshot, "cfop": i.cfop_snapshot,
             "unidade": i.unidade, "quantidade": i.quantidade, "qtd_entregue": i.qtd_entregue,
             "preco_unitario": i.preco_unitario, "total": i.total} for i in itens]

@router.post("/api/vendas/{venda_id}/reemitir")
def reemitir(venda_id: str, db: Session = Depends(get_db), bling: BlingService = Depends(get_bling)):
    venda = db.query(Venda).filter(Venda.id == venda_id).first()
    if not venda:
        raise HTTPException(404, "Venda não encontrada")
    itens = db.query(ItemVenda).filter(ItemVenda.venda_id == venda_id).all()
    itens_dict = [{"nome_snapshot":i.nome_snapshot,"ncm_snapshot":i.ncm_snapshot,
                   "cfop_snapshot":i.cfop_snapshot,"csosn":"400","unidade":i.unidade,
                   "quantidade":i.quantidade,"preco_unitario":i.preco_unitario,
                   "desconto":i.desconto,"total":i.total} for i in itens]
    venda_dict = {"id":venda.id,"total":venda.total,"cliente_nome":venda.cliente_nome,
                  "cpf_nota":venda.cpf_nota,"forma_pgto":venda.forma_pgto}
    resultado = bling.emitir_nfce(venda_dict, itens_dict) if venda.tipo_doc == "nfce" else bling.emitir_nfe(venda_dict, itens_dict)
    venda.status_fiscal = resultado.get("status","pendente")
    venda.chave_acesso = resultado.get("chave_acesso")
    venda.danfe_url = resultado.get("danfe_url")
    db.commit()
    return resultado


# ── ESTOQUE ────────────────────────────────────────────────────────────────

@router.get("/api/estoque")
def estoque(db: Session = Depends(get_db)):
    produtos = db.query(Produto).filter(Produto.ativo == True).order_by(Produto.nome).all()
    movs = db.query(MovimentacaoEstoque).order_by(desc(MovimentacaoEstoque.criado_em)).limit(10).all()
    return {
        "produtos": [
            {"id":p.id,"sku":p.sku,"nome":p.nome,"unidade":p.unidade,
             "estoque":p.estoque,"estoque_min":p.estoque_min,"categoria":p.categoria,
             "status": "ok" if p.estoque > p.estoque_min else ("critico" if p.estoque > 0 else "zerado")}
            for p in produtos
        ],
        "movimentacoes": [
            {"tipo":m.tipo,"quantidade":m.quantidade,"saldo_antes":m.saldo_antes,
             "saldo_apos":m.saldo_apos,"referencia":m.referencia,
             "criado_em":m.criado_em.isoformat()}
            for m in movs
        ],
    }

class AjusteEstoque(BaseModel):
    produto_id: str; quantidade: float; tipo: str = "ajuste"; observacao: str = ""

@router.post("/api/estoque/ajuste")
def ajuste_estoque(data: AjusteEstoque, db: Session = Depends(get_db)):
    p = db.query(Produto).filter(Produto.id == data.produto_id).first()
    if not p:
        raise HTTPException(404, "Produto não encontrado")
    saldo_antes = p.estoque
    if data.tipo == "entrada":
        p.estoque = round(p.estoque + data.quantidade, 4)
    else:
        p.estoque = round(data.quantidade, 4)  # ajuste absoluto
    db.add(MovimentacaoEstoque(produto_id=p.id, tipo=data.tipo,
                                quantidade=data.quantidade, saldo_antes=saldo_antes,
                                saldo_apos=p.estoque, referencia=data.observacao))
    db.commit()
    return {"saldo_novo": p.estoque, "mensagem": "Estoque ajustado"}


# ── WEBHOOKS BLING ──────────────────────────────────────────────────────────

@router.post("/api/webhooks/bling")
async def webhook_bling(payload: dict, db: Session = Depends(get_db)):
    import json
    ev = WebhookEvento(tipo=payload.get("event","desconhecido"), payload=json.dumps(payload))
    db.add(ev)
    evento = payload.get("event","")
    dados = payload.get("data",{})

    if evento == "estoque.atualizado":
        bling_id = dados.get("produto",{}).get("id")
        novo_saldo = dados.get("saldoFisico",0)
        p = db.query(Produto).filter(Produto.bling_id == bling_id).first()
        if p:
            p.estoque = novo_saldo
            ev.processado = True

    elif evento in ("nfe.autorizada","nfe.status_alterado"):
        chave = dados.get("chaveAcesso")
        if chave:
            v = db.query(Venda).filter(Venda.chave_acesso == chave).first()
            if v:
                v.status_fiscal = "autorizada"
                ev.processado = True

    db.commit()
    return {"recebido": True}

@router.get("/api/webhooks/eventos")
def listar_eventos(db: Session = Depends(get_db)):
    evs = db.query(WebhookEvento).order_by(desc(WebhookEvento.criado_em)).limit(20).all()
    return [{"tipo":e.tipo,"processado":e.processado,"criado_em":e.criado_em.isoformat()} for e in evs]


# ── CONFIGURAÇÕES BLING ────────────────────────────────────────────────────

class ConfigBling(BaseModel):
    access_token: str

@router.post("/api/config/bling")
def configurar_bling(data: ConfigBling):
    global _bling_token
    _bling_token = data.access_token
    return {"mensagem": "Token Bling configurado. Próximas emissões usarão a API real."}

@router.get("/api/config/bling/status")
def status_bling():
    if not _bling_token:
        return {"configurado": False, "modo": "simulacao",
                "mensagem": "Sem token Bling. Emissões em modo simulação."}
    try:
        r = httpx.get(f"https://api.bling.com.br/Api/v3/usuarios/me",
                      headers={"Authorization": f"Bearer {_bling_token}"}, timeout=10)
        if r.status_code == 200:
            return {"configurado": True, "modo": "producao", "usuario": r.json().get("data",{}).get("nome")}
        return {"configurado": True, "modo": "token_invalido", "codigo": r.status_code}
    except:
        return {"configurado": True, "modo": "erro_conexao"}
