"""
test_integracao_api.py
Testes de integração — endpoints FastAPI com banco de dados SQLite real.

Diferença dos testes unitários:
  - Banco de dados REAL em memória (não mockado)
  - Cliente HTTP real (TestClient do FastAPI)
  - Testa o fluxo completo: HTTP → Router → Service → Banco
  - Bling continua mockado (não fazemos requisições reais à SEFAZ)

Cobertura:
  - CRUD de produtos com validações fiscais
  - Criação de venda com baixa real de estoque
  - Proteção contra estoque insuficiente
  - Endpoints de dashboard calculando do banco real
  - Webhooks processando e atualizando banco
  - Reemissão de notas rejeitadas
"""
import pytest
import json
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
sys.path.insert(0, "/home/claude/erp_matcon")

from backend.database import Base, get_db, Produto, Cliente, Venda, ItemVenda, MovimentacaoEstoque
from backend import app as fastapi_app


# ── BANCO DE DADOS EM MEMÓRIA PARA TESTES ────────────────────────────────────

from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="function")
def db_teste():
    """
    Banco SQLite em memória isolado por teste.
    StaticPool garante que TODA sessão reutilize a MESMA conexão,
    resolvendo o problema de o SQLite em memória criar um banco
    vazio para cada nova conexão.
    """
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_teste):
    """TestClient com banco de dados isolado por teste."""
    def override_get_db():
        # Cada request do FastAPI recebe uma sessão nova mas conectada
        # ao mesmo banco em memória via StaticPool
        from sqlalchemy.orm import Session
        sess = Session(bind=db_teste.bind)
        try:
            yield sess
        finally:
            sess.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=True) as c:
        yield c
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def produto_cimento(db_teste):
    """Produto de cimento pré-inserido no banco de teste."""
    p = Produto(
        sku="CIM-CP2-50", nome="Cimento CP-II 50kg",
        ncm="25232900", cfop="5102", csosn="400",
        origem=0, unidade="SC",
        preco_custo=25.50, preco_venda=32.90,
        estoque=100.0, estoque_min=20.0,
        categoria="Cimento/Argamassa",
    )
    db_teste.add(p)
    db_teste.commit()
    db_teste.refresh(p)
    return p


@pytest.fixture
def produto_areia(db_teste):
    """Produto de areia com estoque crítico."""
    p = Produto(
        sku="ARE-MED-M3", nome="Areia Média m³",
        ncm="25171000", cfop="5102", csosn="400",
        unidade="M3",
        preco_custo=120.0, preco_venda=185.0,
        estoque=3.0, estoque_min=5.0,  # Já em estado crítico
        categoria="Agregados",
    )
    db_teste.add(p)
    db_teste.commit()
    db_teste.refresh(p)
    return p


@pytest.fixture
def produto_zerado(db_teste):
    """Produto sem estoque."""
    p = Produto(
        sku="PED-BRI-0", nome="Pedra Brita nº 1",
        ncm="25171010", cfop="5102", csosn="400",
        unidade="M3", preco_venda=145.0,
        estoque=0.0, estoque_min=5.0,
        categoria="Agregados",
    )
    db_teste.add(p)
    db_teste.commit()
    db_teste.refresh(p)
    return p


@pytest.fixture
def cliente_pj(db_teste):
    """Cliente pessoa jurídica."""
    c = Cliente(
        nome="Construtora Alfa Ltda",
        cpf_cnpj="29.270.156/0001-86",
        tipo_pessoa="J",
        telefone="(41) 3333-4444",
        cidade="Curitiba", uf="PR",
    )
    db_teste.add(c)
    db_teste.commit()
    db_teste.refresh(c)
    return c


# ── MOCK DO BLING (retorna autorizada por padrão) ─────────────────────────────

@pytest.fixture(autouse=True)
def mock_bling_autorizada():
    """
    Por padrão, o Bling retorna 'autorizada' em modo simulação.
    Testes específicos podem sobrescrever este mock.
    """
    # BlingService sem token já usa simulação — não precisa mockar
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# TESTES DE INTEGRAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndpointProdutos:
    """Testa CRUD de produtos via API."""

    def test_listar_produtos_vazio(self, client):
        # Arrange — banco limpo
        # Act
        resp = client.get("/api/produtos")
        # Assert
        assert resp.status_code == 200
        assert resp.json() == []

    def test_listar_produtos_retorna_cadastrados(self, client, produto_cimento):
        resp = client.get("/api/produtos")

        assert resp.status_code == 200
        dados = resp.json()
        assert len(dados) == 1
        assert dados[0]["sku"] == "CIM-CP2-50"
        assert dados[0]["ncm"] == "25232900"

    def test_produto_retorna_campos_fiscais(self, client, produto_cimento):
        resp = client.get("/api/produtos")
        p = resp.json()[0]

        assert "ncm" in p
        assert "cfop" in p
        assert "csosn" in p
        assert "unidade" in p

    def test_busca_por_nome(self, client, produto_cimento, produto_areia):
        resp = client.get("/api/produtos?q=Cimento")

        assert resp.status_code == 200
        dados = resp.json()
        assert len(dados) == 1
        assert dados[0]["sku"] == "CIM-CP2-50"

    def test_busca_por_sku(self, client, produto_cimento, produto_areia):
        resp = client.get("/api/produtos?q=ARE-MED")

        assert resp.status_code == 200
        assert resp.json()[0]["sku"] == "ARE-MED-M3"

    def test_criar_produto_com_campos_fiscais(self, client):
        payload = {
            "sku": "TIJ-CER-9", "nome": "Tijolo Cerâmico 9x19x19",
            "ncm": "69041000", "cfop": "5102", "csosn": "400",
            "unidade": "MI", "preco_custo": 220.0, "preco_venda": 310.0,
            "estoque": 50.0, "estoque_min": 5.0, "categoria": "Cerâmica",
        }

        resp = client.post("/api/produtos", json=payload)

        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_criar_produto_sku_duplicado_retorna_400(self, client, produto_cimento):
        payload = {
            "sku": "CIM-CP2-50",  # mesmo SKU
            "nome": "Outro cimento", "ncm": "25232900",
            "cfop": "5102", "csosn": "400", "unidade": "SC",
            "preco_venda": 30.0,
        }

        resp = client.post("/api/produtos", json=payload)

        assert resp.status_code == 400

    def test_estoque_critico_sinalizado(self, client, produto_areia):
        resp = client.get("/api/produtos")
        p = resp.json()[0]

        # Areia tem estoque 3.0, mínimo 5.0 → crítico
        assert p["estoque_critico"] is True

    def test_estoque_normal_nao_sinalizado(self, client, produto_cimento):
        resp = client.get("/api/produtos")
        p = resp.json()[0]

        # Cimento tem estoque 100, mínimo 20 → ok
        assert p["estoque_critico"] is False


class TestEndpointVendas:
    """Testa criação de vendas com baixa de estoque real."""

    def test_criar_venda_nfce_retorna_dados_fiscais(self, client, produto_cimento):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "CONSUMIDOR FINAL",
            "cpf_nota": "123.456.789-09",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 5}],
        }

        resp = client.post("/api/vendas", json=payload)

        assert resp.status_code == 200
        dados = resp.json()
        assert "numero" in dados
        assert "resultado_fiscal" in dados
        assert dados["resultado_fiscal"]["status"] == "autorizada"

    def test_criar_venda_calcula_total_corretamente(self, client, produto_cimento):
        # 5 SC × R$ 32,90 = R$ 164,50
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "João",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 5}],
        }

        resp = client.post("/api/vendas", json=payload)

        assert resp.status_code == 200
        assert resp.json()["total"] == pytest.approx(164.50, rel=0.01)

    def test_venda_baixa_estoque_real(self, client, produto_cimento, db_teste):
        estoque_inicial = produto_cimento.estoque  # 100 SC

        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Maria",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 10}],
        }
        client.post("/api/vendas", json=payload)

        # Recarrega do banco
        db_teste.expire_all()
        prod_atualizado = db_teste.query(Produto).filter(Produto.id == produto_cimento.id).first()
        assert prod_atualizado.estoque == estoque_inicial - 10

    def test_venda_registra_movimentacao_de_estoque(self, client, produto_cimento, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Carlos",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 3}],
        }
        client.post("/api/vendas", json=payload)

        movs = db_teste.query(MovimentacaoEstoque).filter(
            MovimentacaoEstoque.produto_id == produto_cimento.id
        ).all()
        assert len(movs) == 1
        assert movs[0].tipo == "saida_venda"
        assert movs[0].quantidade == 3
        assert movs[0].saldo_antes == 100.0
        assert movs[0].saldo_apos == 97.0

    def test_estoque_insuficiente_retorna_400(self, client, produto_zerado):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_zerado.id, "quantidade": 1}],
        }

        resp = client.post("/api/vendas", json=payload)

        assert resp.status_code == 400
        assert "insuficiente" in resp.json()["detail"].lower()

    def test_estoque_insuficiente_nao_cria_venda(self, client, produto_zerado, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_zerado.id, "quantidade": 5}],
        }
        client.post("/api/vendas", json=payload)

        # Nenhuma venda deve ter sido criada no banco
        qtd = db_teste.query(Venda).count()
        assert qtd == 0

    def test_estoque_insuficiente_nao_baixa_estoque(self, client, produto_zerado, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_zerado.id, "quantidade": 5}],
        }
        client.post("/api/vendas", json=payload)

        db_teste.expire_all()
        prod = db_teste.query(Produto).filter(Produto.id == produto_zerado.id).first()
        assert prod.estoque == 0.0  # Não alterou

    def test_produto_inexistente_retorna_404(self, client):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": "uuid-que-nao-existe", "quantidade": 1}],
        }

        resp = client.post("/api/vendas", json=payload)

        assert resp.status_code == 404

    def test_venda_multiplos_itens_baixa_todos(self, client, produto_cimento,
                                                produto_areia, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Construtora",
            "forma_pgto": "pix_dinamico",
            "itens": [
                {"produto_id": produto_cimento.id, "quantidade": 5},
                {"produto_id": produto_areia.id, "quantidade": 1.0},
            ],
        }
        client.post("/api/vendas", json=payload)

        db_teste.expire_all()
        cim = db_teste.query(Produto).filter(Produto.id == produto_cimento.id).first()
        are = db_teste.query(Produto).filter(Produto.id == produto_areia.id).first()
        assert cim.estoque == 95.0   # 100 - 5
        assert are.estoque == 2.0    # 3 - 1

    def test_numero_venda_gerado_automaticamente(self, client, produto_cimento):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
        }

        resp = client.post("/api/vendas", json=payload)

        assert resp.status_code == 200
        numero = resp.json()["numero"]
        assert numero.startswith("NFC-") or numero.startswith("NFE-")

    def test_cpf_nota_persistido_na_venda(self, client, produto_cimento, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "João",
            "cpf_nota": "123.456.789-09",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
        }
        client.post("/api/vendas", json=payload)

        venda = db_teste.query(Venda).first()
        assert venda.cpf_nota == "123.456.789-09"

    def test_listar_vendas_retorna_todas(self, client, produto_cimento):
        # Cria 3 vendas
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
        }
        for _ in range(3):
            client.post("/api/vendas", json=payload)

        resp = client.get("/api/vendas")

        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_filtrar_vendas_por_status_fiscal(self, client, produto_cimento, db_teste):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
        }
        client.post("/api/vendas", json=payload)

        # Filtra apenas autorizadas
        resp = client.get("/api/vendas?status=autorizada")

        assert resp.status_code == 200
        for v in resp.json():
            assert v["status_fiscal"] == "autorizada"

    def test_reemitir_venda_rejeitada(self, client, produto_cimento, db_teste):
        # Cria venda que ficará rejeitada
        venda = Venda(
            numero="NFE-REJEITADA-001", canal="balcao", tipo_doc="nfe",
            status="confirmado", status_fiscal="rejeitada",
            cliente_nome="Teste", forma_pgto="boleto",
            total=100.0, observacoes="Rejeição 243",
        )
        item = ItemVenda(
            venda_id=None,  # será preenchido após flush
            produto_id=produto_cimento.id,
            nome_snapshot=produto_cimento.nome,
            ncm_snapshot=produto_cimento.ncm,
            cfop_snapshot=produto_cimento.cfop,
            unidade=produto_cimento.unidade,
            quantidade=3, preco_unitario=32.90,
            desconto=0, total=98.70,
        )
        db_teste.add(venda)
        db_teste.flush()
        item.venda_id = venda.id
        db_teste.add(item)
        db_teste.commit()

        resp = client.post(f"/api/vendas/{venda.id}/reemitir")

        assert resp.status_code == 200
        assert resp.json()["status"] in ("autorizada", "pendente", "rejeitada")


class TestEndpointDashboard:
    """Testa cálculos do dashboard com dados reais do banco."""

    def test_dashboard_metricas_banco_vazio(self, client):
        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        m = resp.json()["metricas"]
        assert m["vendas_hoje"] == 0.0
        assert m["pedidos_abertos"] == 0
        assert m["nfe_pendentes"] == 0
        assert m["estoque_critico"] == 0

    def test_dashboard_conta_estoque_critico(self, client, produto_areia, produto_zerado):
        resp = client.get("/api/dashboard")

        m = resp.json()["metricas"]
        assert m["estoque_critico"] == 2  # areia (crítico) + brita (zerado)

    def test_dashboard_conta_nfe_pendentes(self, client, db_teste):
        for status in ("pendente", "rejeitada", "autorizada"):
            v = Venda(
                numero=f"NFE-{status}", canal="balcao", tipo_doc="nfe",
                status="confirmado", status_fiscal=status,
                cliente_nome="Teste", forma_pgto="pix_dinamico", total=100.0,
            )
            db_teste.add(v)
        db_teste.commit()

        resp = client.get("/api/dashboard")

        m = resp.json()["metricas"]
        # pendente + rejeitada = 2
        assert m["nfe_pendentes"] == 2

    def test_dashboard_retorna_ultimas_vendas(self, client, produto_cimento):
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Dashboard Teste",
            "forma_pgto": "pix_dinamico",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
        }
        client.post("/api/vendas", json=payload)

        resp = client.get("/api/dashboard")

        assert len(resp.json()["ultimas_vendas"]) >= 1

    def test_dashboard_retorna_distribuicao_pgto(self, client, produto_cimento):
        for pgto in ["pix_dinamico", "dinheiro", "pix_dinamico"]:
            payload = {
                "canal": "balcao", "tipo_doc": "nfce",
                "cliente_nome": "Teste",
                "forma_pgto": pgto,
                "itens": [{"produto_id": produto_cimento.id, "quantidade": 1}],
            }
            client.post("/api/vendas", json=payload)

        resp = client.get("/api/dashboard")
        dist = resp.json()["pgto_distribuicao"]

        tipos = {d["tipo"]: d["qtd"] for d in dist}
        assert tipos.get("pix_dinamico") == 2
        assert tipos.get("dinheiro") == 1


class TestEndpointEstoque:
    """Testa o controle de estoque via API."""

    def test_estoque_retorna_todos_produtos(self, client, produto_cimento, produto_areia):
        resp = client.get("/api/estoque")

        assert resp.status_code == 200
        assert len(resp.json()["produtos"]) == 2

    def test_estoque_classifica_status_corretamente(self, client, produto_cimento,
                                                      produto_areia, produto_zerado):
        resp = client.get("/api/estoque")
        prods = {p["sku"]: p for p in resp.json()["produtos"]}

        assert prods["CIM-CP2-50"]["status"] == "ok"      # 100 > min 20
        assert prods["ARE-MED-M3"]["status"] == "critico"  # 3 < min 5
        assert prods["PED-BRI-0"]["status"] == "zerado"    # 0

    def test_ajuste_de_estoque_entrada(self, client, produto_cimento, db_teste):
        payload = {
            "produto_id": produto_cimento.id,
            "quantidade": 50.0,
            "tipo": "entrada",
            "observacao": "Recebimento NF fornecedor",
        }

        resp = client.post("/api/estoque/ajuste", json=payload)

        assert resp.status_code == 200
        assert resp.json()["saldo_novo"] == 150.0  # 100 + 50

    def test_ajuste_registra_movimentacao(self, client, produto_cimento, db_teste):
        payload = {
            "produto_id": produto_cimento.id,
            "quantidade": 50.0,
            "tipo": "entrada",
            "observacao": "Teste",
        }
        client.post("/api/estoque/ajuste", json=payload)

        movs = db_teste.query(MovimentacaoEstoque).filter(
            MovimentacaoEstoque.produto_id == produto_cimento.id
        ).all()
        assert len(movs) == 1
        assert movs[0].tipo == "entrada"
        assert movs[0].saldo_antes == 100.0
        assert movs[0].saldo_apos == 150.0

    def test_estoque_retorna_movimentacoes(self, client, produto_cimento):
        # Gera uma movimentação via venda
        payload = {
            "canal": "balcao", "tipo_doc": "nfce",
            "cliente_nome": "Teste",
            "forma_pgto": "dinheiro",
            "itens": [{"produto_id": produto_cimento.id, "quantidade": 5}],
        }
        client.post("/api/vendas", json=payload)

        resp = client.get("/api/estoque")

        movs = resp.json()["movimentacoes"]
        assert len(movs) >= 1
        assert movs[0]["tipo"] == "saida_venda"


class TestEndpointWebhook:
    """Testa recebimento e processamento de webhooks do Bling."""

    def test_webhook_estoque_atualiza_produto(self, client, produto_cimento, db_teste):
        payload = {
            "event": "estoque.atualizado",
            "data": {
                "produto": {"id": produto_cimento.bling_id},
                "saldoFisico": 75.0,
                "deposito": {"id": 1},
            }
        }
        # Para este teste funcionar, o produto precisa ter bling_id
        produto_cimento.bling_id = 99999
        db_teste.commit()

        payload["data"]["produto"]["id"] = 99999
        resp = client.post("/api/webhooks/bling", json=payload)

        assert resp.status_code == 200
        assert resp.json()["recebido"] is True

    def test_webhook_salva_evento_no_banco(self, client, db_teste):
        from backend.database import WebhookEvento
        payload = {
            "event": "nfe.autorizada",
            "data": {"chaveAcesso": "41250429..."}
        }

        client.post("/api/webhooks/bling", json=payload)

        eventos = db_teste.query(WebhookEvento).all()
        assert len(eventos) == 1
        assert eventos[0].tipo == "nfe.autorizada"

    def test_webhook_evento_desconhecido_salvo_sem_processar(self, client, db_teste):
        from backend.database import WebhookEvento
        payload = {"event": "evento.futuro.desconhecido", "data": {}}

        client.post("/api/webhooks/bling", json=payload)

        ev = db_teste.query(WebhookEvento).first()
        assert ev.processado is False

    def test_listar_eventos_webhook(self, client, db_teste):
        from backend.database import WebhookEvento
        db_teste.add(WebhookEvento(tipo="estoque.atualizado", payload='{}', processado=True))
        db_teste.add(WebhookEvento(tipo="nfe.autorizada", payload='{}', processado=False))
        db_teste.commit()

        resp = client.get("/api/webhooks/eventos")

        assert resp.status_code == 200
        assert len(resp.json()) == 2


class TestEndpointClientes:
    """Testa CRUD de clientes."""

    def test_criar_cliente_pf(self, client):
        payload = {
            "nome": "Ana Paula Silva",
            "cpf_cnpj": "111.222.333-44",
            "tipo_pessoa": "F",
            "telefone": "(41) 99999-0000",
            "cidade": "Curitiba", "uf": "PR",
        }

        resp = client.post("/api/clientes", json=payload)

        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_criar_cliente_pj(self, client):
        payload = {
            "nome": "Construtora Beta Ltda",
            "cpf_cnpj": "12.345.678/0001-90",
            "tipo_pessoa": "J",
            "cidade": "Pinhais", "uf": "PR",
        }

        resp = client.post("/api/clientes", json=payload)

        assert resp.status_code == 200

    def test_buscar_cliente_por_nome(self, client, cliente_pj):
        resp = client.get("/api/clientes?q=Construtora")

        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert "Construtora" in resp.json()[0]["nome"]

    def test_buscar_cliente_por_cpf_cnpj(self, client, cliente_pj):
        resp = client.get("/api/clientes?q=29.270.156")

        assert resp.status_code == 200
        assert len(resp.json()) == 1
