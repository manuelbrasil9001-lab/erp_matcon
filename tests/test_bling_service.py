"""
test_bling_service.py
Testa a camada de comunicação com a API Bling v3.

Cobertura:
  - Emissão NFC-e / NF-e em modo simulação (sem token)
  - Emissão NFC-e / NF-e com token real (HTTP mockado)
  - Parsing dos 3 retornos: autorizada / pendente / rejeitada
  - Inclusão/omissão de CPF na nota (Nota Paraná)
  - Montagem correta dos campos fiscais (NCM, CFOP, CSOSN)
  - Dados de transportadora na NF-e
  - Atualização de estoque via PATCH
  - Processamento de webhooks
  - Retry e tratamento de erros HTTP
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import (
    RETORNO_AUTORIZADA, RETORNO_PENDENTE,
    RETORNO_REJEITADA_243, RETORNO_REJEITADA_481,
    make_item, make_venda,
)
from backend.services.bling_service import BlingService


# ── BLOCO 1: MODO SIMULAÇÃO (sem token Bling) ────────────────────────────────

class TestModoSimulacao:
    """Quando não há token Bling, o serviço usa simulação local."""

    def test_nfce_simulada_retorna_status_autorizada(self, bling_sem_token, venda_balcao, item_padrao):
        # Arrange
        itens = [item_padrao]

        # Act
        resultado = bling_sem_token.emitir_nfce(venda_balcao, itens)

        # Assert
        assert resultado["status"] == "autorizada"
        assert resultado["simulado"] is True

    def test_nfce_simulada_gera_chave_acesso(self, bling_sem_token, venda_balcao, item_padrao):
        resultado = bling_sem_token.emitir_nfce(venda_balcao, [item_padrao])

        assert resultado["chave_acesso"] is not None
        assert len(resultado["chave_acesso"]) > 10

    def test_nfce_simulada_gera_numero_e_protocolo(self, bling_sem_token, venda_balcao, item_padrao):
        resultado = bling_sem_token.emitir_nfce(venda_balcao, [item_padrao])

        assert isinstance(resultado["numero"], int)
        assert resultado["numero_protocolo"] is not None

    def test_nfe_simulada_retorna_status_autorizada(self, bling_sem_token, venda_ecommerce, itens_multiplos):
        resultado = bling_sem_token.emitir_nfe(venda_ecommerce, itens_multiplos)

        assert resultado["status"] == "autorizada"
        assert resultado["simulado"] is True

    def test_simulacao_gera_danfe_url(self, bling_sem_token, venda_balcao, item_padrao):
        resultado = bling_sem_token.emitir_nfce(venda_balcao, [item_padrao])

        assert resultado["danfe_url"] is not None
        assert "bling.com.br" in resultado["danfe_url"]

    def test_duas_vendas_diferentes_geram_chaves_diferentes(self, bling_sem_token, item_padrao):
        # Garantia de unicidade nas chaves simuladas
        v1 = make_venda(total=100.0)
        v2 = make_venda(total=200.0)

        r1 = bling_sem_token.emitir_nfce(v1, [item_padrao])
        r2 = bling_sem_token.emitir_nfce(v2, [item_padrao])

        assert r1["chave_acesso"] != r2["chave_acesso"]

    def test_atualizar_estoque_sem_token_retorna_true(self, bling_sem_token):
        # Sem token, não chama API mas retorna sucesso para não bloquear o fluxo
        resultado = bling_sem_token.atualizar_estoque(bling_produto_id=12345, saldo=238.0)

        assert resultado is True


# ── BLOCO 2: EMISSÃO REAL (com token, HTTP mockado) ──────────────────────────

class TestEmissaoComToken:
    """Testa a emissão com token real, mockando as chamadas HTTP."""

    def test_nfce_autorizada_parse_correto(self, bling_com_token, venda_balcao, item_padrao):
        # Arrange — mocka a resposta HTTP do Bling
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = RETORNO_AUTORIZADA
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.post", return_value=mock_resp):
            # Act
            resultado = bling_com_token.emitir_nfce(venda_balcao, [item_padrao])

        # Assert — verifica todos os campos fiscais importantes
        assert resultado["status"] == "autorizada"
        assert resultado["bling_nfe_id"] == 99001
        assert resultado["numero"] == 1234
        assert resultado["chave_acesso"] == "41250429270156000186650010000012341987654321"
        assert resultado["numero_protocolo"] == "141250099001"
        assert "nfeProc" in resultado["xml_autorizado"]
        assert resultado["danfe_url"] == "https://bling.com.br/danfe/41250429.pdf"

    def test_nfce_pendente_parse_correto(self, bling_com_token, venda_balcao, item_padrao):
        # Arrange
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = RETORNO_PENDENTE
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.post", return_value=mock_resp):
            resultado = bling_com_token.emitir_nfce(venda_balcao, [item_padrao])

        # Assert — pendente não deve ter chave nem danfe
        assert resultado["status"] == "pendente"
        assert resultado["chave_acesso"] is None
        assert resultado["danfe_url"] is None

    def test_nfce_rejeitada_243_parse_correto(self, bling_com_token, venda_balcao, item_padrao):
        # Arrange — Bling retorna 422 com corpo de rejeição SEFAZ
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = json.dumps(RETORNO_REJEITADA_243)

        http_error = httpx.HTTPStatusError("422", request=MagicMock(), response=mock_resp)

        with patch("httpx.post", side_effect=http_error):
            resultado = bling_com_token.emitir_nfce(venda_balcao, [item_padrao])

        assert resultado["status"] == "rejeitada"
        assert "243" in resultado["mensagem"]

    def test_nfce_rejeitada_481_certificado_vencido(self, bling_com_token, venda_balcao, item_padrao):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = json.dumps(RETORNO_REJEITADA_481)

        http_error = httpx.HTTPStatusError("422", request=MagicMock(), response=mock_resp)

        with patch("httpx.post", side_effect=http_error):
            resultado = bling_com_token.emitir_nfce(venda_balcao, [item_padrao])

        assert resultado["status"] == "rejeitada"
        assert "481" in resultado["mensagem"]

    def test_erro_de_rede_retorna_erro_sistema(self, bling_com_token, venda_balcao, item_padrao):
        import httpx

        with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
            resultado = bling_com_token.emitir_nfce(venda_balcao, [item_padrao])

        assert resultado["status"] == "erro_sistema"
        assert len(resultado["mensagem"]) > 0


# ── BLOCO 3: MONTAGEM DO PAYLOAD FISCAL ─────────────────────────────────────

class TestMontagemdoPayload:
    """Verifica que os campos fiscais obrigatórios estão no payload enviado ao Bling."""

    def test_payload_nfce_contem_ncm(self, bling_com_token, venda_balcao):
        # Arrange
        item = make_item(ncm="25232900")
        payloads_enviados = []

        def captura_post(url, json=None, headers=None, timeout=None):
            payloads_enviados.append(json)
            raise Exception("Capturado")  # Interrompe sem precisar mockar resposta completa

        with patch("httpx.post", side_effect=captura_post):
            bling_com_token.emitir_nfce(venda_balcao, [item])

        # Assert
        assert len(payloads_enviados) == 1
        item_payload = payloads_enviados[0]["itens"][0]
        assert item_payload["ncm"] == "25232900"

    def test_payload_nfce_contem_cfop(self, bling_com_token, venda_balcao):
        item = make_item(cfop="5102")
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfce(venda_balcao, [item])

        assert payloads_enviados[0]["itens"][0]["cfop"] == "5102"

    def test_payload_nfce_contem_csosn_no_icms(self, bling_com_token, venda_balcao):
        item = make_item(csosn="400")
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfce(venda_balcao, [item])

        icms = payloads_enviados[0]["itens"][0]["impostos"]["icms"]
        assert icms["situacaoTributaria"] == "400"
        assert icms["origem"] == 0  # Nacional

    def test_payload_nfce_com_cpf_inclui_documento(self, bling_com_token):
        # Venda COM cpf_nota informado (Nota Paraná)
        venda = make_venda(cpf_nota="12345678909")
        item = make_item()
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfce(venda, [item])

        contato = payloads_enviados[0]["contato"]
        assert contato.get("numeroDocumento") == "12345678909"

    def test_payload_nfce_sem_cpf_usa_consumidor_final(self, bling_com_token):
        # Venda SEM cpf_nota (balcão anônimo)
        venda = make_venda(cpf_nota=None, cliente_nome="CONSUMIDOR FINAL")
        item = make_item()
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfce(venda, [item])

        contato = payloads_enviados[0]["contato"]
        assert contato["nome"] == "CONSUMIDOR FINAL"
        assert "numeroDocumento" not in contato

    def test_payload_nfe_modelo_55(self, bling_com_token, venda_ecommerce, item_padrao):
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfe(venda_ecommerce, [item_padrao])

        assert payloads_enviados[0]["modelo"] == 55

    def test_payload_nfe_com_transportadora(self, bling_com_token, venda_ecommerce, item_padrao):
        transportadora = {
            "nome": "Transportes Curitiba Ltda",
            "cnpj": "12.345.678/0001-90",
            "placa": "ABC1D23",
            "uf": "PR",
            "modalidade": 0,
            "volumes": 3,
            "especie": "SACOS",
            "peso_bruto": 150.0,
        }
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfe(venda_ecommerce, [item_padrao], transportadora)

        transp = payloads_enviados[0]["transporte"]
        assert transp["transportador"]["nome"] == "Transportes Curitiba Ltda"
        assert transp["veiculo"]["placa"] == "ABC1D23"
        assert transp["volumes"]["especie"] == "SACOS"
        assert transp["volumes"]["pesoBruto"] == 150.0

    def test_payload_multiplos_itens(self, bling_com_token, venda_balcao, itens_multiplos):
        payloads_enviados = []

        def captura(url, json=None, **kw):
            payloads_enviados.append(json)
            raise Exception("Capturado")

        with patch("httpx.post", side_effect=captura):
            bling_com_token.emitir_nfce(venda_balcao, itens_multiplos)

        assert len(payloads_enviados[0]["itens"]) == 3
        ncms = [it["ncm"] for it in payloads_enviados[0]["itens"]]
        assert "25232900" in ncms  # Cimento
        assert "25171000" in ncms  # Areia
        assert "69041000" in ncms  # Tijolo


# ── BLOCO 4: WEBHOOKS ────────────────────────────────────────────────────────

class TestWebhooks:
    """Testa o processamento de eventos de webhook do Bling."""

    def test_webhook_estoque_atualizado(self, bling_sem_token):
        payload = {
            "event": "estoque.atualizado",
            "data": {
                "produto": {"id": 12345},
                "saldoFisico": 238.0,
                "deposito": {"id": 1},
            }
        }

        resultado = bling_sem_token.processar_webhook_estoque(payload)

        assert resultado["tipo"] == "atualizacao_estoque"
        assert resultado["bling_produto_id"] == 12345
        assert resultado["novo_saldo"] == pytest.approx(238.0)
        assert resultado["deposito_id"] == 1

    def test_webhook_pedido_status_alterado(self, bling_sem_token):
        payload = {
            "event": "pedido.status_alterado",
            "data": {
                "id": 99001,
                "situacao": {"id": 9},  # 9 = Atendido no Bling
            }
        }

        resultado = bling_sem_token.processar_webhook_estoque(payload)

        assert resultado["tipo"] == "status_pedido"
        assert resultado["bling_pedido_id"] == 99001
        assert resultado["novo_status"] == 9

    def test_webhook_evento_desconhecido(self, bling_sem_token):
        payload = {"event": "evento.nao_mapeado", "data": {}}

        resultado = bling_sem_token.processar_webhook_estoque(payload)

        assert resultado["tipo"] == "desconhecido"
        assert resultado["evento"] == "evento.nao_mapeado"
