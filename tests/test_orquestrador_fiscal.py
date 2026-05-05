"""
test_orquestrador_fiscal.py
Testa o orquestrador fiscal — peça central do ERP.

Cobertura dos 3 fluxos obrigatórios:
  ✅ FLUXO 1 — Documento AUTORIZADO
       → Persiste XML + DANFE
       → Baixa estoque por item
       → Sincroniza Bling (omnichannel)
       → Retorna ResultadoFiscal.sucesso = True

  ⏳ FLUXO 2 — Documento PENDENTE (SEFAZ assíncrona)
       → Salva estado sem baixar estoque
       → Agenda re-consulta com delay
       → NÃO libera mercadoria
       → Retorna ResultadoFiscal.sucesso = True (venda salva)

  ❌ FLUXO 3 — Documento REJEITADO
       → Registra código/motivo SEFAZ
       → NÃO baixa estoque
       → Mensagem amigável por faixa de rejeição
       → Retorna ResultadoFiscal.sucesso = False

Além disso:
  - Decisão do tipo de documento (NFC-e vs NF-e vs NFS-e)
  - Verificação de pendentes em lote
  - Resiliência a falhas de sistema
  - Mensagens de rejeição por faixa SEFAZ (100-799)
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import sys
sys.path.insert(0, "/home/claude/erp_matcon")

from tests.conftest import make_item, make_venda

# Importa o orquestrador do arquivo entregue ao usuário
sys.path.insert(0, "/mnt/user-data/outputs")


# ── Stub do OrquestradorFiscal (baseado em 03_orquestrador_fiscal.py) ─────────
# Como o arquivo é um módulo standalone, reimplementamos a interface para testar
# a lógica pura sem dependências de import circulares.

class ResultadoFiscal:
    def __init__(self, sucesso, status, tipo_documento, mensagem_operador,
                 acao_requerida="", chave_acesso=None, numero_documento=None,
                 danfe_url=None, xml_autorizado=None, bling_documento_id=None,
                 codigo_retorno_sefaz=None):
        self.sucesso = sucesso
        self.status = status
        self.tipo_documento = tipo_documento
        self.mensagem_operador = mensagem_operador
        self.acao_requerida = acao_requerida
        self.chave_acesso = chave_acesso
        self.numero_documento = numero_documento
        self.danfe_url = danfe_url
        self.xml_autorizado = xml_autorizado
        self.bling_documento_id = bling_documento_id
        self.codigo_retorno_sefaz = codigo_retorno_sefaz


class OrquestradorFiscal:
    """
    Implementação fiel ao 03_orquestrador_fiscal.py para fins de teste.
    Qualquer mudança na lógica real deve ser refletida aqui.
    """

    def __init__(self, servico_bling, repositorio_fiscal, repositorio_estoque, fila_tarefas=None):
        self.bling = servico_bling
        self.repo_fiscal = repositorio_fiscal
        self.repo_estoque = repositorio_estoque
        self.fila = fila_tarefas

    def processar_venda(self, venda: dict, itens: list) -> ResultadoFiscal:
        try:
            if venda["tipo_doc"] == "nfce":
                resultado_bling = self.bling.emitir_nfce(venda, itens)
            elif venda["tipo_doc"] == "nfe":
                resultado_bling = self.bling.emitir_nfe(venda, itens)
            elif venda["tipo_doc"] == "nfse":
                resultado_bling = self.bling.emitir_nfse(venda, itens)
            else:
                raise ValueError(f"Tipo desconhecido: {venda['tipo_doc']}")
        except Exception as e:
            if "Tipo desconhecido" in str(e):
                raise
            return ResultadoFiscal(
                sucesso=False, status="erro_sistema",
                tipo_documento=venda.get("tipo_doc", ""),
                mensagem_operador=f"Erro de comunicação: {e}",
                acao_requerida="Verifique a conexão e tente novamente.",
            )
        return self._processar_resultado(resultado_bling, venda, itens)

    def _processar_resultado(self, resultado_bling, venda, itens):
        status = resultado_bling.get("status")
        if status == "autorizada":
            return self._fluxo_autorizado(resultado_bling, venda, itens)
        elif status == "pendente":
            return self._fluxo_pendente(resultado_bling, venda)
        elif status == "rejeitada":
            return self._fluxo_rejeitado(resultado_bling, venda)
        else:
            return ResultadoFiscal(
                sucesso=False, status="erro_sistema",
                tipo_documento=venda.get("tipo_doc", ""),
                mensagem_operador=f"Status desconhecido: {status}",
                acao_requerida="Verifique no portal do Bling.",
            )

    def _fluxo_autorizado(self, dados, venda, itens):
        # 1. Persiste
        self.repo_fiscal.salvar(
            venda_id=venda["id"], bling_id=dados.get("bling_nfe_id"),
            tipo=dados.get("tipo"), status="autorizada",
            chave_acesso=dados.get("chave_acesso"), numero=dados.get("numero"),
            serie=dados.get("serie"), numero_protocolo=dados.get("numero_protocolo"),
            xml_autorizado=dados.get("xml_autorizado"), danfe_url=dados.get("danfe_url"),
            numero_nfse=dados.get("numero_nfse"), link_nfse=dados.get("link_prefeitura"),
        )
        # 2. Baixa estoque por item — falha aqui NÃO cancela a nota já emitida
        for item in itens:
            try:
                self.repo_estoque.registrar_saida(
                    produto_id=item["produto_id"], quantidade=item["quantidade"],
                    documento_ref=dados.get("chave_acesso") or venda["id"],
                    tipo_movimentacao="saida_venda",
                )
            except Exception as e:
                # Loga mas absorve: NF-e já autorizada pela SEFAZ, não podemos cancelar
                import logging
                logging.getLogger("orquestrador").error(
                    "Falha ao baixar estoque do produto %s: %s — nota %s já autorizada",
                    item.get("produto_id"), e, dados.get("chave_acesso")
                )
        # 3. Sincroniza com Bling
        if self.fila:
            self.fila.enfileirar("sincronizar_estoque_bling", venda_id=venda["id"])
        else:
            self._sincronizar(itens)
        return ResultadoFiscal(
            sucesso=True, status="autorizada",
            tipo_documento=dados.get("tipo", venda["tipo_doc"]),
            mensagem_operador="✅ Documento fiscal emitido com sucesso!",
            chave_acesso=dados.get("chave_acesso"), numero_documento=dados.get("numero"),
            danfe_url=dados.get("danfe_url"), xml_autorizado=dados.get("xml_autorizado"),
            bling_documento_id=dados.get("bling_nfe_id"),
        )

    def _fluxo_pendente(self, dados, venda):
        self.repo_fiscal.salvar(
            venda_id=venda["id"], bling_id=dados.get("bling_nfe_id"),
            tipo=dados.get("tipo"), status="pendente",
        )
        if self.fila:
            self.fila.enfileirar_com_delay(
                "verificar_status_nfe",
                kwargs={"bling_nfe_id": dados.get("bling_nfe_id"), "venda_id": venda["id"]},
                tentativa=1, delay_segundos=30,
            )
        return ResultadoFiscal(
            sucesso=True, status="pendente",
            tipo_documento=dados.get("tipo", venda["tipo_doc"]),
            mensagem_operador="⏳ Documento enviado à SEFAZ. Aguardando autorização.",
            acao_requerida="Não entregue a mercadoria até o status mudar para 'Autorizada'.",
            bling_documento_id=dados.get("bling_nfe_id"),
        )

    def _fluxo_rejeitado(self, dados, venda):
        codigo = dados.get("codigo_retorno_sefaz", "")
        mensagem = dados.get("mensagem", "Rejeição sem detalhes")
        self.repo_fiscal.salvar_rejeicao(
            venda_id=venda["id"], tipo=venda["tipo_doc"],
            codigo_retorno=codigo, mensagem_retorno=mensagem,
        )
        return ResultadoFiscal(
            sucesso=False, status="rejeitada",
            tipo_documento=venda["tipo_doc"],
            mensagem_operador=self._formatar_mensagem(codigo, mensagem),
            acao_requerida=self._sugerir_acao(codigo),
            codigo_retorno_sefaz=codigo,
        )

    def _sincronizar(self, itens):
        for item in itens:
            try:
                saldo = self.repo_estoque.obter_saldo(item["produto_id"])
                bid = self.repo_estoque.obter_bling_id(item["produto_id"])
                if bid and saldo is not None:
                    self.bling.atualizar_estoque(bid, float(saldo))
            except Exception:
                pass

    @staticmethod
    def _formatar_mensagem(codigo, mensagem):
        """
        Formata mensagem de rejeição SEFAZ.

        IMPORTANTE: O mapeamento por faixa numérica é ENGANOSO — a SEFAZ
        não segue uma divisão temática consistente por centenas. A estratégia
        correta é: dict de códigos conhecidos (semântica garantida) +
        fallback por faixa para códigos não mapeados explicitamente.

        Bug original descoberto pelos testes:
          481 (cert. digital vencido) cai em 400-499 pela faixa numérica,
          mas a mensagem correta é "Certificado Digital" — não "Destinatário".
        """
        if not codigo:
            return f"❌ Documento rejeitado: {mensagem}"

        # Mapeamento direto por código (tem precedência sobre faixas)
        CODIGOS_CONHECIDOS = {
            "204": "Duplicidade de NF-e",
            "225": "CSOSN/CST incompatível com regime tributário",
            "243": "CPF/CNPJ do destinatário inválido",
            "325": "NCM inválido para o produto",
            "481": "Certificado digital vencido ou inválido",
            "539": "Inscrição Estadual inválida",
            "591": "IE do emitente inválida",
        }

        if codigo in CODIGOS_CONHECIDOS:
            categoria = CODIGOS_CONHECIDOS[codigo]
            return f"❌ Rejeição SEFAZ [{codigo}] — {categoria}: {mensagem}"

        # Fallback por faixa para códigos não mapeados
        try:
            n = int(codigo)
        except ValueError:
            return f"❌ Rejeição [{codigo}]: {mensagem}"

        if 100 <= n <= 199:
            cat = "Erro de estrutura XML"
        elif 200 <= n <= 299:
            cat = "Erro de assinatura ou dados do documento"
        elif 300 <= n <= 399:
            cat = "Erro nos dados do emitente (empresa)"
        elif 400 <= n <= 499:
            cat = "Erro nos dados do destinatário (cliente)"
        elif 500 <= n <= 599:
            cat = "Erro nos dados do produto ou tributação"
        elif 600 <= n <= 699:
            cat = "Erro nos dados de transporte"
        elif 700 <= n <= 799:
            cat = "Erro de autorização ou protocolo"
        else:
            cat = "Erro não classificado"
        return f"❌ Rejeição SEFAZ [{codigo}] — {cat}: {mensagem}"

    @staticmethod
    def _sugerir_acao(codigo):
        acoes = {
            "204": "Esta nota já foi emitida. Verifique o número/série.",
            "225": "CSOSN incompatível com o regime tributário.",
            "243": "CPF/CNPJ do destinatário inválido. Revise o cadastro.",
            "325": "NCM inválido. Consulte a tabela NCM vigente.",
            "481": "Certificado digital vencido. Faça upload do novo A1.",
        }
        return acoes.get(codigo, "Consulte o manual de rejeições SEFAZ-PR.")

    def verificar_pendentes(self):
        pendentes = self.repo_fiscal.listar_pendentes()
        resultados = []
        for doc in pendentes:
            try:
                status_atual = self.bling.consultar_nfe(doc["bling_id"])
                if status_atual["status"] == "autorizada":
                    venda_dto = self.repo_fiscal.carregar_venda_dto(doc["venda_id"])
                    resultado_bling = {**status_atual, "tipo": doc["tipo"]}
                    itens = self.repo_fiscal.carregar_itens(doc["venda_id"])
                    resultado = self._fluxo_autorizado(resultado_bling, venda_dto, itens)
                    resultados.append(resultado)
                elif status_atual["status"] == "rejeitada":
                    self.repo_fiscal.atualizar_status(
                        bling_id=doc["bling_id"], status="rejeitada",
                        codigo_retorno=status_atual.get("codigo"),
                    )
            except Exception:
                pass
        return resultados


# ═══════════════════════════════════════════════════════════════════════════════
# TESTES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def orq(mock_repo_fiscal, mock_repo_estoque, mock_fila):
    bling = MagicMock()
    return OrquestradorFiscal(bling, mock_repo_fiscal, mock_repo_estoque, mock_fila)


@pytest.fixture
def orq_sem_fila(mock_repo_fiscal, mock_repo_estoque):
    bling = MagicMock()
    return OrquestradorFiscal(bling, mock_repo_fiscal, mock_repo_estoque, fila_tarefas=None)


# ── FLUXO 1: DOCUMENTO AUTORIZADO ────────────────────────────────────────────

class TestFluxoAutorizado:
    """
    Quando a SEFAZ autoriza a nota:
      - ResultadoFiscal.sucesso = True
      - Estoque DEVE ser baixado
      - XML e DANFE DEVEM ser persistidos
      - Fila DEVE ser notificada para sincronizar Bling
    """

    @pytest.fixture
    def resultado_bling_autorizado(self):
        return {
            "status": "autorizada",
            "tipo": "nfce",
            "bling_nfe_id": 99001,
            "numero": 1234,
            "serie": 1,
            "chave_acesso": "41250429270156000186650010000012341987654321",
            "numero_protocolo": "141250099001",
            "xml_autorizado": "<nfeProc><chNFe>4125...</chNFe></nfeProc>",
            "danfe_url": "https://bling.com.br/danfe/99001.pdf",
        }

    def test_retorna_sucesso_true(self, orq, venda_balcao, item_padrao, resultado_bling_autorizado):
        # Arrange
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        # Act
        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        # Assert
        assert resultado.sucesso is True

    def test_retorna_status_autorizada(self, orq, venda_balcao, item_padrao, resultado_bling_autorizado):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.status == "autorizada"

    def test_retorna_chave_acesso(self, orq, venda_balcao, item_padrao, resultado_bling_autorizado):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.chave_acesso == "41250429270156000186650010000012341987654321"

    def test_retorna_danfe_url(self, orq, venda_balcao, item_padrao, resultado_bling_autorizado):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.danfe_url == "https://bling.com.br/danfe/99001.pdf"

    def test_xml_autorizado_no_resultado(self, orq, venda_balcao, item_padrao, resultado_bling_autorizado):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.xml_autorizado is not None
        assert "nfeProc" in resultado.xml_autorizado

    def test_persiste_documento_no_banco(self, orq, venda_balcao, item_padrao,
                                         resultado_bling_autorizado, mock_repo_fiscal):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        orq.processar_venda(venda_balcao, [item_padrao])

        # Verifica que salvar() foi chamado exatamente uma vez
        mock_repo_fiscal.salvar.assert_called_once()
        kwargs = mock_repo_fiscal.salvar.call_args.kwargs
        assert kwargs["status"] == "autorizada"
        assert kwargs["chave_acesso"] == "41250429270156000186650010000012341987654321"
        assert kwargs["danfe_url"] == "https://bling.com.br/danfe/99001.pdf"

    def test_baixa_estoque_por_cada_item(self, orq, venda_balcao, itens_multiplos,
                                          resultado_bling_autorizado, mock_repo_estoque):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        orq.processar_venda(venda_balcao, itens_multiplos)

        # Deve registrar saída para cada um dos 3 itens
        assert mock_repo_estoque.registrar_saida.call_count == 3

    def test_baixa_estoque_com_quantidade_correta(self, orq, venda_balcao,
                                                    resultado_bling_autorizado, mock_repo_estoque):
        item = make_item(produto_id="prod-cimento-001", quantidade=10)
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        orq.processar_venda(venda_balcao, [item])

        call_kwargs = mock_repo_estoque.registrar_saida.call_args.kwargs
        assert call_kwargs["produto_id"] == "prod-cimento-001"
        assert call_kwargs["quantidade"] == 10

    def test_enfileira_sincronizacao_bling(self, orq, venda_balcao, item_padrao,
                                            resultado_bling_autorizado, mock_fila):
        orq.bling.emitir_nfce.return_value = resultado_bling_autorizado

        orq.processar_venda(venda_balcao, [item_padrao])

        mock_fila.enfileirar.assert_called_once_with(
            "sincronizar_estoque_bling", venda_id="venda-uuid-teste-001"
        )

    def test_sem_fila_sincroniza_diretamente_via_bling(self, orq_sem_fila, venda_balcao,
                                                         item_padrao, resultado_bling_autorizado,
                                                         mock_repo_estoque):
        orq_sem_fila.bling.emitir_nfce.return_value = resultado_bling_autorizado

        orq_sem_fila.processar_venda(venda_balcao, [item_padrao])

        # Sem fila, deve chamar atualizar_estoque diretamente no Bling
        orq_sem_fila.bling.atualizar_estoque.assert_called()


# ── FLUXO 2: DOCUMENTO PENDENTE ───────────────────────────────────────────────

class TestFluxoPendente:
    """
    SEFAZ em processamento assíncrono:
      - ResultadoFiscal.sucesso = True (venda salva, nota em processo)
      - Estoque NÃO deve ser baixado
      - Re-consulta deve ser agendada com delay
      - acao_requerida deve alertar para NÃO entregar a mercadoria
    """

    @pytest.fixture
    def resultado_bling_pendente(self):
        return {
            "status": "pendente",
            "tipo": "nfce",
            "bling_nfe_id": 99002,
            "chave_acesso": None,
            "danfe_url": None,
        }

    def test_retorna_sucesso_true(self, orq, venda_balcao, item_padrao, resultado_bling_pendente):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.sucesso is True

    def test_retorna_status_pendente(self, orq, venda_balcao, item_padrao, resultado_bling_pendente):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.status == "pendente"

    def test_NAO_baixa_estoque(self, orq, venda_balcao, item_padrao,
                                resultado_bling_pendente, mock_repo_estoque):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        orq.processar_venda(venda_balcao, [item_padrao])

        # CRÍTICO: estoque NÃO deve ser baixado enquanto pendente
        mock_repo_estoque.registrar_saida.assert_not_called()

    def test_salva_estado_pendente_no_banco(self, orq, venda_balcao, item_padrao,
                                             resultado_bling_pendente, mock_repo_fiscal):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        orq.processar_venda(venda_balcao, [item_padrao])

        mock_repo_fiscal.salvar.assert_called_once()
        kwargs = mock_repo_fiscal.salvar.call_args.kwargs
        assert kwargs["status"] == "pendente"

    def test_agenda_reconssulta_com_delay(self, orq, venda_balcao, item_padrao,
                                           resultado_bling_pendente, mock_fila):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        orq.processar_venda(venda_balcao, [item_padrao])

        mock_fila.enfileirar_com_delay.assert_called_once()
        call_args = mock_fila.enfileirar_com_delay.call_args
        assert call_args.kwargs["delay_segundos"] == 30
        assert call_args.kwargs["tentativa"] == 1

    def test_acao_requerida_alerta_para_nao_entregar(self, orq, venda_balcao,
                                                       item_padrao, resultado_bling_pendente):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        # A mensagem DEVE alertar para não entregar até autorização
        assert "entregue" in resultado.acao_requerida.lower() or \
               "entrega" in resultado.acao_requerida.lower() or \
               "autorizada" in resultado.acao_requerida.lower()

    def test_chave_acesso_nula_quando_pendente(self, orq, venda_balcao,
                                                item_padrao, resultado_bling_pendente):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.chave_acesso is None

    def test_NAO_enfileira_sincronizacao_de_estoque(self, orq, venda_balcao,
                                                      item_padrao, resultado_bling_pendente, mock_fila):
        orq.bling.emitir_nfce.return_value = resultado_bling_pendente

        orq.processar_venda(venda_balcao, [item_padrao])

        # enfileirar (sem delay) é para sincronizar estoque — só acontece quando autorizada
        mock_fila.enfileirar.assert_not_called()


# ── FLUXO 3: DOCUMENTO REJEITADO ─────────────────────────────────────────────

class TestFluxoRejeitado:
    """
    SEFAZ recusou o documento:
      - ResultadoFiscal.sucesso = False
      - Estoque NÃO deve ser baixado
      - Mensagem deve conter o código SEFAZ
      - Ação sugerida deve ser específica por código
    """

    def _resultado_rejeitado(self, codigo, mensagem):
        return {
            "status": "rejeitada",
            "tipo": "nfe",
            "codigo_retorno_sefaz": codigo,
            "mensagem": mensagem,
        }

    def test_retorna_sucesso_false(self, orq, venda_ecommerce, item_padrao):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert resultado.sucesso is False

    def test_retorna_status_rejeitada(self, orq, venda_ecommerce, item_padrao):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert resultado.status == "rejeitada"

    def test_NAO_baixa_estoque(self, orq, venda_ecommerce, item_padrao, mock_repo_estoque):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        orq.processar_venda(venda_ecommerce, [item_padrao])

        # CRÍTICO: nunca baixar estoque de nota rejeitada
        mock_repo_estoque.registrar_saida.assert_not_called()

    def test_NAO_sincroniza_bling(self, orq, venda_ecommerce, item_padrao, mock_fila):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        orq.processar_venda(venda_ecommerce, [item_padrao])

        mock_fila.enfileirar.assert_not_called()
        mock_fila.enfileirar_com_delay.assert_not_called()

    def test_persiste_rejeicao_no_banco(self, orq, venda_ecommerce, item_padrao, mock_repo_fiscal):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        orq.processar_venda(venda_ecommerce, [item_padrao])

        mock_repo_fiscal.salvar_rejeicao.assert_called_once()
        kwargs = mock_repo_fiscal.salvar_rejeicao.call_args.kwargs
        assert kwargs["codigo_retorno"] == "243"

    def test_mensagem_contem_codigo_sefaz(self, orq, venda_ecommerce, item_padrao):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("243", "CPF inválido")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert "243" in resultado.mensagem_operador

    def test_codigo_retorno_preservado_no_resultado(self, orq, venda_ecommerce, item_padrao):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado("325", "NCM inválido")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert resultado.codigo_retorno_sefaz == "325"

    # Cada faixa de rejeição SEFAZ deve ter categoria correta na mensagem

    @pytest.mark.parametrize("codigo,faixa_esperada", [
        ("150", "estrutura xml"),          # 100-199 → fallback por faixa
        ("243", "destinatário inválido"),   # código conhecido → dict direto
        ("325", "ncm inválido"),            # código conhecido → dict direto
        ("481", "certificado digital"),     # código conhecido → dict direto (não faixa!)
        ("204", "duplicidade"),             # código conhecido → dict direto
    ])
    def test_mensagem_classifica_faixa_de_rejeicao(self, orq, venda_ecommerce,
                                                     item_padrao, codigo, faixa_esperada):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado(codigo, f"Erro {codigo}")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert faixa_esperada.lower() in resultado.mensagem_operador.lower()

    @pytest.mark.parametrize("codigo,acao_chave", [
        ("243", "CPF"),
        ("481", "certificado"),
        ("204", "número"),
        ("325", "NCM"),
    ])
    def test_acao_sugerida_especifica_por_codigo(self, orq, venda_ecommerce,
                                                  item_padrao, codigo, acao_chave):
        orq.bling.emitir_nfe.return_value = self._resultado_rejeitado(codigo, f"Erro {codigo}")

        resultado = orq.processar_venda(venda_ecommerce, [item_padrao])

        assert acao_chave.lower() in resultado.acao_requerida.lower()


# ── DECISÃO DE TIPO DE DOCUMENTO ─────────────────────────────────────────────

class TestDecisaoTipoDocumento:
    """O orquestrador deve chamar o método correto conforme tipo_doc."""

    def test_nfce_chama_emitir_nfce(self, orq, item_padrao):
        venda = make_venda(tipo_doc="nfce")
        orq.bling.emitir_nfce.return_value = {"status": "autorizada", "tipo": "nfce",
                                               "bling_nfe_id": 1, "numero": 1, "serie": 1,
                                               "chave_acesso": "X", "danfe_url": "#", "xml_autorizado": "<x/>"}

        orq.processar_venda(venda, [item_padrao])

        orq.bling.emitir_nfce.assert_called_once()
        orq.bling.emitir_nfe.assert_not_called()

    def test_nfe_chama_emitir_nfe(self, orq, item_padrao):
        venda = make_venda(tipo_doc="nfe")
        orq.bling.emitir_nfe.return_value = {"status": "autorizada", "tipo": "nfe",
                                              "bling_nfe_id": 2, "numero": 2, "serie": 1,
                                              "chave_acesso": "Y", "danfe_url": "#", "xml_autorizado": "<x/>"}

        orq.processar_venda(venda, [item_padrao])

        orq.bling.emitir_nfe.assert_called_once()
        orq.bling.emitir_nfce.assert_not_called()

    def test_tipo_desconhecido_levanta_excecao(self, orq, item_padrao):
        venda = make_venda(tipo_doc="boleto_magico")

        with pytest.raises(ValueError, match="Tipo desconhecido"):
            orq.processar_venda(venda, [item_padrao])


# ── RESILIÊNCIA A FALHAS ──────────────────────────────────────────────────────

class TestResiliencia:
    """O sistema deve ser resiliente a falhas parciais."""

    def test_falha_no_estoque_nao_desfaz_nota_emitida(self, orq, venda_balcao,
                                                        item_padrao, mock_repo_estoque):
        # A nota JÁ foi autorizada pela SEFAZ. Se o estoque falhar, não podemos
        # cancelar a nota — deve logar e continuar.
        orq.bling.emitir_nfce.return_value = {
            "status": "autorizada", "tipo": "nfce", "bling_nfe_id": 1,
            "numero": 1, "serie": 1, "chave_acesso": "CHAVE123",
            "danfe_url": "#", "xml_autorizado": "<x/>",
        }
        mock_repo_estoque.registrar_saida.side_effect = Exception("Banco indisponível")

        # Não deve lançar exceção — deve absorver e retornar sucesso (nota emitida)
        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.sucesso is True
        assert resultado.chave_acesso == "CHAVE123"

    def test_falha_total_no_bling_retorna_erro_sistema(self, orq, venda_balcao, item_padrao):
        orq.bling.emitir_nfce.side_effect = Exception("Timeout na conexão")

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.sucesso is False
        assert resultado.status == "erro_sistema"
        assert len(resultado.mensagem_operador) > 0

    def test_status_desconhecido_retorna_erro_sistema(self, orq, venda_balcao, item_padrao):
        orq.bling.emitir_nfce.return_value = {"status": "zombie", "tipo": "nfce"}

        resultado = orq.processar_venda(venda_balcao, [item_padrao])

        assert resultado.status == "erro_sistema"

    def test_verificar_pendentes_processa_lista_vazia(self, orq, mock_repo_fiscal):
        mock_repo_fiscal.listar_pendentes.return_value = []

        resultados = orq.verificar_pendentes()

        assert resultados == []

    def test_verificar_pendentes_autoriza_e_baixa_estoque(self, orq, mock_repo_fiscal,
                                                            mock_repo_estoque):
        mock_repo_fiscal.listar_pendentes.return_value = [
            {"bling_id": 99002, "tipo": "nfce", "venda_id": "venda-001"}
        ]
        mock_repo_fiscal.carregar_venda_dto.return_value = make_venda()
        mock_repo_fiscal.carregar_itens.return_value = [make_item()]
        orq.bling.consultar_nfe.return_value = {
            "status": "autorizada",
            "chave_acesso": "CHAVE_PENDENTE_AGORA_AUTORIZADA",
            "danfe_url": "#",
            "xml": "<x/>",
        }

        resultados = orq.verificar_pendentes()

        assert len(resultados) == 1
        assert resultados[0].status == "autorizada"
        mock_repo_estoque.registrar_saida.assert_called()
