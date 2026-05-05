"""
Serviço Bling — integração real com API v3
Emite NFC-e/NF-e/NFS-e e sincroniza estoque
"""
import httpx
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("bling")

BLING_BASE = "https://api.bling.com.br/Api/v3"

FORMA_PGTO = {
    "pix_dinamico": 17, "cartao_credito": 3,
    "cartao_debito": 4, "dinheiro": 1, "boleto": 2,
}


class BlingService:
    def __init__(self, access_token: str = ""):
        self.token = access_token

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _simular_emissao(self, tipo: str, venda_id: str, total: float) -> dict:
        """
        Simulação do retorno da SEFAZ quando não há token Bling configurado.
        Em produção, o retorno real vem do endpoint /nfe do Bling.
        """
        import random, hashlib
        chave = hashlib.sha1(f"{venda_id}{total}".encode()).hexdigest()[:44].upper()
        num = random.randint(1000, 9999)
        return {
            "status": "autorizada",
            "tipo": tipo,
            "numero": num,
            "serie": 1,
            "chave_acesso": f"4125{chave}",
            "numero_protocolo": f"1412500{num}",
            "danfe_url": f"https://bling.com.br/danfe/{chave}.pdf",
            "xml_autorizado": f"<nfeProc><chNFe>{chave}</chNFe></nfeProc>",
            "bling_nfe_id": num,
            "mensagem": f"{'NFC-e' if tipo=='nfce' else 'NF-e'} autorizada com sucesso (simulação).",
            "simulado": True,
        }

    def emitir_nfce(self, venda: dict, itens: list) -> dict:
        """Emite NFC-e via Bling API v3. Fallback: simulação local."""
        if not self.token:
            return self._simular_emissao("nfce", venda["id"], venda["total"])

        payload = {
            "tipo": 2, "finalidade": 1, "modelo": 65,
            "naturezaOperacao": "VENDA DE MERCADORIAS",
            "dataOperacao": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "presencaComprador": 1, "consumidorFinal": 1,
            "contato": {"nome": venda.get("cliente_nome", "CONSUMIDOR FINAL")},
            "itens": [self._montar_item(it) for it in itens],
            "parcelas": [{"dias": 0, "valor": venda["total"],
                          "formaPagamento": {"id": FORMA_PGTO.get(venda.get("forma_pgto","pix_dinamico"), 17)}}],
            "informacoesAdicionais": venda.get("observacoes", "NFC-e emitida via ERP MatCon"),
        }
        if venda.get("cpf_nota"):
            cpf = venda["cpf_nota"].replace(".","").replace("-","").replace("/","")
            payload["contato"]["numeroDocumento"] = cpf

        try:
            r = httpx.post(f"{BLING_BASE}/nfe", json=payload, headers=self._headers(), timeout=30)
            r.raise_for_status()
            return self._parse_retorno(r.json(), "nfce")
        except httpx.HTTPStatusError as e:
            return self._parse_rejeicao(e.response.text, "nfce")
        except Exception as e:
            logger.error("Erro ao emitir NFC-e: %s", e)
            return {"status": "erro_sistema", "mensagem": str(e)}

    def emitir_nfe(self, venda: dict, itens: list, transportadora: dict = None) -> dict:
        """Emite NF-e Modelo 55 via Bling."""
        if not self.token:
            return self._simular_emissao("nfe", venda["id"], venda["total"])

        payload = {
            "tipo": 2, "finalidade": 1, "modelo": 55,
            "naturezaOperacao": "VENDA E REMESSA DE MERCADORIAS",
            "dataOperacao": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "presencaComprador": 9,
            "contato": {"nome": venda.get("cliente_nome","CONSUMIDOR FINAL")},
            "itens": [self._montar_item(it) for it in itens],
            "parcelas": [{"dias": 30, "valor": venda["total"],
                          "formaPagamento": {"id": FORMA_PGTO.get(venda.get("forma_pgto","boleto"), 2)}}],
            "transporte": {"modalidadeFrete": 0} if not transportadora else {
                "modalidadeFrete": transportadora.get("modalidade", 0),
                "transportador": {"nome": transportadora.get("nome",""), "cpfCnpj": transportadora.get("cnpj","")},
                "veiculo": {"placa": transportadora.get("placa",""), "uf": transportadora.get("uf","PR")},
                "volumes": {"quantidade": transportadora.get("volumes",1),
                            "especie": transportadora.get("especie","VOLUMES"),
                            "pesoBruto": transportadora.get("peso_bruto",0)},
                "valorFrete": venda.get("frete",0),
            },
        }
        try:
            r = httpx.post(f"{BLING_BASE}/nfe", json=payload, headers=self._headers(), timeout=30)
            r.raise_for_status()
            return self._parse_retorno(r.json(), "nfe")
        except httpx.HTTPStatusError as e:
            return self._parse_rejeicao(e.response.text, "nfe")
        except Exception as e:
            return {"status": "erro_sistema", "mensagem": str(e)}

    def consultar_nfe(self, bling_nfe_id: int) -> dict:
        if not self.token:
            return {"status": "autorizada", "simulado": True}
        try:
            r = httpx.get(f"{BLING_BASE}/nfe/{bling_nfe_id}", headers=self._headers(), timeout=15)
            r.raise_for_status()
            dados = r.json().get("data", {})
            sit = dados.get("situacao", {}).get("id", 0)
            mapa = {6:"autorizada", 4:"pendente", 9:"rejeitada", 2:"cancelada"}
            return {"status": mapa.get(sit,"pendente"), "chave_acesso": dados.get("chaveAcesso"),
                    "danfe_url": dados.get("linkDanfe"), "xml": dados.get("xml")}
        except Exception as e:
            return {"status": "erro_sistema", "mensagem": str(e)}

    def atualizar_estoque(self, bling_produto_id: int, saldo: float) -> bool:
        if not self.token:
            return True
        try:
            r = httpx.patch(f"{BLING_BASE}/estoques/{bling_produto_id}",
                            json={"operacao":"B","quantidade":saldo,"deposito":{"id":1}},
                            headers=self._headers(), timeout=15)
            return r.status_code == 200
        except:
            return False

    def _montar_item(self, it: dict) -> dict:
        return {
            "descricao": it["nome_snapshot"],
            "codigo": it.get("sku",""),
            "unidade": it["unidade"],
            "quantidade": it["quantidade"],
            "valor": it["preco_unitario"],
            "tipo": "P",
            "ncm": it["ncm_snapshot"],
            "cfop": it["cfop_snapshot"],
            "desconto": it.get("desconto", 0),
            "impostos": {
                "icms": {"situacaoTributaria": it.get("csosn","400"), "origem": 0,
                         "baseCalculo": 0, "aliquota": 0, "valor": 0},
                "pis":  {"situacaoTributaria":"07","aliquota":0.65,"valor":0,"baseCalculo":it["total"]},
                "cofins":{"situacaoTributaria":"07","aliquota":3.0,"valor":0,"baseCalculo":it["total"]},
            },
        }

    def _parse_retorno(self, resp: dict, tipo: str) -> dict:
        dados = resp.get("data", {})
        sit = dados.get("situacao", {}).get("id", 4)
        mapa = {6:"autorizada", 4:"pendente", 9:"rejeitada"}
        return {
            "status": mapa.get(sit, "pendente"),
            "tipo": tipo,
            "bling_nfe_id": dados.get("id"),
            "numero": dados.get("numero"),
            "serie": dados.get("serie"),
            "chave_acesso": dados.get("chaveAcesso"),
            "numero_protocolo": dados.get("numeroProtocolo"),
            "xml_autorizado": dados.get("xml"),
            "danfe_url": dados.get("linkDanfe"),
            "mensagem": "Documento processado pelo Bling.",
        }

    def processar_webhook_estoque(self, payload_webhook: dict) -> dict:
        """
        Processa evento de webhook do Bling.
        Retorna dict padronizado para o ERP atualizar seu banco de dados.
        """
        evento = payload_webhook.get("event", "")
        dados = payload_webhook.get("data", {})

        if evento == "estoque.atualizado":
            return {
                "tipo": "atualizacao_estoque",
                "bling_produto_id": dados.get("produto", {}).get("id"),
                "novo_saldo": float(dados.get("saldoFisico", 0)),
                "deposito_id": dados.get("deposito", {}).get("id", 1),
            }
        elif evento == "pedido.status_alterado":
            return {
                "tipo": "status_pedido",
                "bling_pedido_id": dados.get("id"),
                "novo_status": dados.get("situacao", {}).get("id"),
            }
        else:
            logger.warning("Webhook desconhecido recebido: %s", evento)
            return {"tipo": "desconhecido", "evento": evento}

    def _parse_rejeicao(self, body: str, tipo: str) -> dict:
        try:
            dados = json.loads(body)
            msg = dados.get("error", {}).get("message", body)
        except:
            msg = body
        return {"status": "rejeitada", "tipo": tipo, "mensagem": msg}
