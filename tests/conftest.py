"""
conftest.py — Fixtures compartilhadas da suíte de testes do ERP MatCon
Padrão: AAA (Arrange / Act / Assert) em todos os testes
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import sys
sys.path.insert(0, "/home/claude/erp_matcon")

from backend.services.bling_service import BlingService
from backend.routers.api import (
    VendaCreate, ItemVendaIn,
)


# ── FÁBRICAS DE DADOS ────────────────────────────────────────────────────────

def make_item(
    produto_id="prod-cimento-001",
    nome="Cimento CP-II 50kg",
    ncm="25232900",
    cfop="5102",
    csosn="400",
    unidade="SC",
    quantidade=10,
    preco_unitario=32.90,
    desconto=0.0,
):
    """Fábrica de ItemVendaIn com valores fiscais reais."""
    total = round(quantidade * preco_unitario - desconto, 2)
    return {
        "produto_id": produto_id,
        "nome_snapshot": nome,
        "ncm_snapshot": ncm,
        "cfop_snapshot": cfop,
        "csosn": csosn,
        "unidade": unidade,
        "quantidade": quantidade,
        "preco_unitario": preco_unitario,
        "desconto": desconto,
        "total": total,
        "sku": "CIM-CP2-50",
    }


def make_venda(
    canal="balcao",
    tipo_doc="nfce",
    cliente_nome="CONSUMIDOR FINAL",
    cpf_nota=None,
    forma_pgto="pix_dinamico",
    total=329.0,
    itens=None,
):
    """Fábrica de dicionário de venda para o BlingService."""
    return {
        "id": "venda-uuid-teste-001",
        "canal": canal,
        "tipo_doc": tipo_doc,
        "cliente_nome": cliente_nome,
        "cpf_nota": cpf_nota,
        "forma_pgto": forma_pgto,
        "total": total,
        "observacoes": None,
    }


# ── RESPOSTAS MOCK DA SEFAZ (via Bling) ─────────────────────────────────────

RETORNO_AUTORIZADA = {
    "data": {
        "id": 99001,
        "numero": 1234,
        "serie": 1,
        "situacao": {"id": 6, "valor": "Autorizada"},
        "chaveAcesso": "41250429270156000186650010000012341987654321",
        "numeroProtocolo": "141250099001",
        "xml": "<nfeProc><chNFe>41250429270156000186650010000012341987654321</chNFe></nfeProc>",
        "linkDanfe": "https://bling.com.br/danfe/41250429.pdf",
        "dataEmissao": "2025-04-29 09:00:00",
    }
}

RETORNO_PENDENTE = {
    "data": {
        "id": 99002,
        "numero": None,
        "serie": 1,
        "situacao": {"id": 4, "valor": "Pendente"},
        "chaveAcesso": None,
        "numeroProtocolo": None,
        "xml": None,
        "linkDanfe": None,
    }
}

RETORNO_REJEITADA_243 = {
    "data": {
        "id": 99003,
        "situacao": {"id": 9, "valor": "243"},
        "chaveAcesso": None,
    },
    "error": {
        "type": "SEFAZ_REJECTION",
        "message": "Rejeição 243: CPF do destinatário inválido",
    }
}

RETORNO_REJEITADA_204 = {
    "data": {
        "id": 99004,
        "situacao": {"id": 9, "valor": "204"},
    },
    "error": {
        "type": "SEFAZ_REJECTION",
        "message": "Rejeição 204: Duplicidade de NF-e",
    }
}

RETORNO_REJEITADA_481 = {
    "data": {
        "id": 99005,
        "situacao": {"id": 9, "valor": "481"},
    },
    "error": {
        "type": "SEFAZ_REJECTION",
        "message": "Rejeição 481: Certificado digital vencido",
    }
}


# ── FIXTURES PYTEST ──────────────────────────────────────────────────────────

@pytest.fixture
def bling_sem_token():
    """BlingService sem token — usa simulação local."""
    return BlingService(access_token="")


@pytest.fixture
def bling_com_token():
    """BlingService com token fictício — requests reais são mockados nos testes."""
    return BlingService(access_token="token-fake-para-testes")


@pytest.fixture
def item_padrao():
    return make_item()


@pytest.fixture
def itens_multiplos():
    return [
        make_item(produto_id="prod-001", nome="Cimento CP-II 50kg", quantidade=10, preco_unitario=32.90),
        make_item(produto_id="prod-002", nome="Areia Média m³", ncm="25171000", unidade="M3", quantidade=2, preco_unitario=185.00),
        make_item(produto_id="prod-003", nome="Tijolo Cerâmico", ncm="69041000", unidade="MI", quantidade=5, preco_unitario=310.00),
    ]


@pytest.fixture
def venda_balcao():
    return make_venda(canal="balcao", tipo_doc="nfce", cpf_nota="123.456.789-09")


@pytest.fixture
def venda_ecommerce():
    return make_venda(canal="ecommerce", tipo_doc="nfe", cliente_nome="Construtora Alfa Ltda",
                     cpf_nota="29.270.156/0001-86", forma_pgto="boleto", total=12400.0)


@pytest.fixture
def mock_repo_fiscal():
    """Repositório fiscal mockado — simula banco de dados."""
    repo = MagicMock()
    repo.salvar.return_value = True
    repo.salvar_rejeicao.return_value = True
    repo.atualizar_status.return_value = True
    repo.listar_pendentes.return_value = []
    return repo


@pytest.fixture
def mock_repo_estoque():
    """Repositório de estoque mockado."""
    repo = MagicMock()
    repo.registrar_saida.return_value = True
    repo.obter_saldo.return_value = Decimal("238.0")
    repo.obter_bling_id.return_value = 12345
    return repo


@pytest.fixture
def mock_fila():
    """Fila de tarefas (Celery/RQ) mockada."""
    fila = MagicMock()
    fila.enfileirar.return_value = True
    fila.enfileirar_com_delay.return_value = "task-id-mock"
    return fila
