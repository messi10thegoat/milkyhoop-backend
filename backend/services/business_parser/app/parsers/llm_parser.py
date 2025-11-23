"""
LLM-based parser (Layer 2)

OpenAI GPT classification with rule-based fallback.
Expected to handle ~40% of traffic with 800ms latency.
"""

import os
import json
import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from openai import OpenAI

from app.prompts.tenant_prompt import TENANT_PROMPT_TEMPLATE
from app.extractors.item_extractor import extract_items_from_text
from app.extractors.payment_extractor import extract_payment_method
from app.extractors.employee_extractor import extract_employee_names, extract_periode_gaji
from app.extractors.product_extractor import extract_product_name
from .base_parser import BaseParser

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class LLMParser(BaseParser):
    """
    Layer 2: LLM-based classification using OpenAI GPT
    
    Falls back to rule-based classification if LLM fails.
    """

    def parse(self, text: str, context: Optional[str] = None, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse using OpenAI LLM with rule-based fallback.

        Args:
            text: User input text
            context: Optional conversation context
            tenant_id: Optional tenant identifier

        Returns:
            Classification dict with intent, entities, confidence, model_used
        """
        current_period = datetime.now().strftime("%Y-%m")
        today = datetime.now().strftime("%Y-%m-%d")

        # Escape {{ and }} in template to prevent format string errors
        escaped_template = TENANT_PROMPT_TEMPLATE.replace("{{", "{{{{").replace("}}", "}}}}")

        prompt = escaped_template.format(
            user_input=text.strip(),
            tenant_id=tenant_id or "unknown",
            today=today,
            current_period=current_period
        )

        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise NLP classifier. Output pure JSON only, no markdown."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=600
            )

            content = response.choices[0].message.content.strip()

            # Clean markdown
            if "```" in content:
                content = re.sub(r'```(?:json)?', '', content).strip()

            parsed = json.loads(content)

            # Validate & normalize
            if not parsed.get("intent"):
                raise ValueError("Missing intent")

            # Intent normalization
            intent_map = {
                "transaction": "transaction_record",
                "record_transaction": "transaction_record",
                "return": "retur_penjualan",
                "retur": "retur_penjualan",
                "refund": "retur_penjualan",
                "payment": "pembayaran_hutang",
                "bayar_hutang": "pembayaran_hutang",
                "cicilan": "pembayaran_hutang",
                "report": "financial_report",
                "top": "top_products",
                "best_seller": "top_products",
                "low_sell": "low_sell_products",
                "kurang_laku": "low_sell_products",
                "query": "query_transaksi",
                "filter": "query_transaksi",
                "inventory": "inventory_query",
                "stock": "inventory_query",
                "check_stock": "inventory_query",
                "koreksi": "koreksi",
                "koreksi_transaksi": "koreksi",
                "update": "koreksi",
                "ubah": "koreksi",
                "salah": "koreksi",
                "general": "general_inquiry"
            }

            intent = parsed["intent"].lower().strip()
            parsed["intent"] = intent_map.get(intent, intent)

            # Ensure defaults
            parsed.setdefault("confidence", 0.85)
            parsed.setdefault("entities", {})
            parsed["model_used"] = "gpt-3.5-turbo"

            # POST-PROCESSING: Extract items array if LLM failed
            entities = parsed.get("entities", {})
            if parsed.get("intent") == "transaction_record":
                # Check if items array is missing or empty
                if not entities.get("items") or len(entities.get("items", [])) == 0:
                    print(f"[FALLBACK] LLM failed to extract items, using regex parser")
                    fallback_items = extract_items_from_text(text)
                    if fallback_items:
                        entities["items"] = fallback_items
                        # Recalculate total_nominal from items
                        total = sum(item.get("subtotal", 0) for item in fallback_items)
                        if total > 0:
                            entities["total_nominal"] = total
                            print(f"[FALLBACK] Extracted {len(fallback_items)} items, total={total}")

                # Extract metode_pembayaran with fuzzy matching
                if not entities.get("metode_pembayaran"):
                    fuzzy_method = extract_payment_method(text.lower(), use_fuzzy=True)
                    if fuzzy_method:
                        entities["metode_pembayaran"] = fuzzy_method
                        logger.info(f"[LLM_POST] Extracted metode_pembayaran: {fuzzy_method}")

            # Auto-classify kategori_beban for beban transactions
            if parsed.get("intent") == "transaction_record" and entities.get("jenis_transaksi") == "beban":
                text_lower = text.lower()

                if not entities.get("kategori_beban"):
                    if any(k in text_lower for k in ["gaji", "bayar gaji", "gaji karyawan", "upah"]):
                        entities["kategori_beban"] = "beban_gaji"
                    elif any(k in text_lower for k in ["listrik", "pln", "tagihan listrik"]):
                        entities["kategori_beban"] = "beban_listrik"
                    elif any(k in text_lower for k in ["sewa", "rent", "sewa tempat"]):
                        entities["kategori_beban"] = "beban_sewa"
                    elif any(k in text_lower for k in ["pajak", "ppn", "pph"]):
                        entities["kategori_beban"] = "beban_pajak"
                    else:
                        entities["kategori_beban"] = "beban_operasional"

                # Auto-extract detail_karyawan and periode_gaji for beban_gaji
                if entities.get("kategori_beban") == "beban_gaji":
                    if not entities.get("detail_karyawan"):
                        employee_names = extract_employee_names(text)
                        if employee_names:
                            entities["detail_karyawan"] = employee_names

                    if not entities.get("periode_gaji"):
                        periode = extract_periode_gaji(text_lower)
                        if periode:
                            entities["periode_gaji"] = periode

            # Inventory fallback extraction
            if parsed["intent"] == "inventory_query":
                if not entities.get("product_name"):
                    product = extract_product_name(text)
                    if product:
                        entities["product_name"] = product

            print(f"[LLM] {parsed['intent']} (conf: {parsed['confidence']:.2f})")

            return parsed

        except (json.JSONDecodeError, Exception) as e:
            print(f"[LLM] Error: {e}, fallback to rules")
            return self._rule_fallback(text)

    def _rule_fallback(self, text: str) -> Dict[str, Any]:
        """
        Fast rule-based fallback when LLM fails.

        Args:
            text: User input text

        Returns:
            Classification dict
        """
        text_lower = text.lower()

        # Retur detection (PRIORITY)
        if any(k in text_lower for k in ["return", "retur", "rusak", "refund", "kembalikan"]):
            return {
                "intent": "retur_penjualan",
                "entities": {"is_retur": True, "alasan_retur": "rusak"},
                "confidence": 0.85,
                "model_used": "regex"
            }

        # Pembayaran hutang
        if any(k in text_lower for k in ["bayar cicilan", "bayar hutang", "pelunasan", "cicilan"]) and any(k in text_lower for k in ["supplier", "utang", "hutang"]):
            return {
                "intent": "pembayaran_hutang",
                "entities": {},
                "confidence": 0.80,
                "model_used": "regex"
            }

        # Transaction - detect jual/beli/bayar (with OR without amount for incomplete transactions)
        transaction_keywords = ["jual", "beli", "bayar", "setor", "catat", "penjualan", "pembelian"]
        has_transaction_keyword = any(k in text_lower for k in transaction_keywords)
        has_amount = re.search(r'(?:rp\s*)?\d+[\s.,]*(?:rb|jt|juta|ribu|juta)?', text_lower) or re.search(r'\d+', text)

        if has_transaction_keyword:
            # Auto-detect jenis_transaksi
            entities = {}
            if any(k in text_lower for k in ["jual", "terjual", "penjualan"]):
                entities["jenis_transaksi"] = "penjualan"
            elif any(k in text_lower for k in ["beli", "pembelian", "membeli"]):
                entities["jenis_transaksi"] = "pembelian"
            elif any(k in text_lower for k in ["bayar", "beban", "pengeluaran"]):
                entities["jenis_transaksi"] = "beban"
                # Auto-detect kategori_beban
                if any(k in text_lower for k in ["gaji", "upah"]):
                    entities["kategori_beban"] = "beban_gaji"

            # Extract product/service name
            product_match = re.search(r'(?:jual|beli|bayar|setor|catat|penjualan|pembelian)\s+([a-zA-Z0-9\s]+?)(?:\s|$|@|Rp|tunai|transfer|kas|jumlah|\d)', text_lower)
            if product_match:
                product_name = product_match.group(1).strip()
                # Remove common stop words
                stop_words = ["yang", "dari", "ke", "untuk", "dengan", "adalah", "ini", "itu", "sebanyak", "sejumlah", "jumlah", "qty", "quantity"]
                product_words = [w for w in product_name.split() if w not in stop_words and w not in transaction_keywords]
                if product_words:
                    product_name = " ".join(product_words)
                    if product_name and not product_name.isdigit():
                        entities.setdefault("items", [])
                        entities["items"].append({"nama_produk": product_name})

            # Extract detail_karyawan for beban_gaji
            if entities.get("jenis_transaksi") == "beban" and entities.get("kategori_beban") == "beban_gaji":
                employee_names = extract_employee_names(text)
                if employee_names:
                    entities["detail_karyawan"] = employee_names

                periode = extract_periode_gaji(text_lower)
                if periode:
                    entities["periode_gaji"] = periode

            # Extract total_nominal from amount
            if has_amount and not entities.get("total_nominal"):
                amount_match = re.search(r'(?:rp\s*)?(\d+)[\s.,]*(?:rb|jt|juta|ribu)?', text_lower)
                if amount_match:
                    amount_str = amount_match.group(1).replace('.', '').replace(',', '')
                    amount_num = int(amount_str)

                    if 'juta' in text_lower or 'jt' in text_lower:
                        total_nominal = amount_num * 1000000
                    elif 'ribu' in text_lower or 'rb' in text_lower:
                        total_nominal = amount_num * 1000
                    else:
                        if amount_num > 1000:
                            total_nominal = amount_num * 1000000
                        else:
                            total_nominal = amount_num * 1000

                    entities["total_nominal"] = total_nominal

            # Extract items array using regex
            has_complete_items = False
            if entities.get("items") and len(entities.get("items", [])) > 0:
                first_item = entities["items"][0]
                has_complete_items = (first_item.get("jumlah") is not None and
                                     first_item.get("harga_satuan") is not None)

            if not has_complete_items:
                fallback_items = extract_items_from_text(text)
                if fallback_items:
                    entities["items"] = fallback_items
                    total_from_items = sum(item.get("subtotal", 0) for item in fallback_items)
                    if total_from_items > 0:
                        entities["total_nominal"] = total_from_items

            # Extract metode_pembayaran with fuzzy matching
            if not entities.get("metode_pembayaran"):
                fuzzy_method = extract_payment_method(text_lower, use_fuzzy=True)
                if fuzzy_method:
                    entities["metode_pembayaran"] = fuzzy_method
                    logger.info(f"[RULE] Extracted metode_pembayaran: {fuzzy_method}")

            confidence = 0.85 if has_amount else 0.75

            return {
                "intent": "transaction_record",
                "entities": entities,
                "confidence": confidence,
                "model_used": "regex"
            }

        # Query transaksi
        if any(k in text_lower for k in ["transaksi", "tampilkan", "filter"]) and any(k in text_lower for k in ["supplier", "customer", "bulan", "oktober"]):
            return {
                "intent": "query_transaksi",
                "entities": {},
                "confidence": 0.75,
                "model_used": "regex"
            }

        # Inventory query vs history
        if any(k in text_lower for k in ["stok", "stock", "persediaan"]):
            if any(k in text_lower for k in ["riwayat", "history", "histori", "pergerakan", "movement", "masuk keluar"]):
                return {
                    "intent": "inventory_history",
                    "entities": {
                        "query_type": "movement_history",
                        "product_name": extract_product_name(text)
                    },
                    "confidence": 0.80,
                    "model_used": "regex"
                }
            else:
                return {
                    "intent": "inventory_query",
                    "entities": {
                        "query_type": "stock_level",
                        "product_name": extract_product_name(text)
                    },
                    "confidence": 0.78,
                    "model_used": "regex"
                }

        # Financial (including salary payment queries)
        salary_keywords = ["sudah bayar gaji", "bayar gaji siapa", "gaji siapa saja", "belum bayar gaji",
                           "yang belum dibayar", "gaji bulan", "total pengeluaran gaji", "pengeluaran gaji"]
        financial_keywords = ["untung", "rugi", "laba", "neraca", "kas"]

        if any(k in text_lower for k in salary_keywords) or any(k in text_lower for k in financial_keywords):
            return {
                "intent": "financial_report",
                "entities": {
                    "report_type": "laba_rugi",
                    "periode_pelaporan": datetime.now().strftime("%Y-%m")
                },
                "confidence": 0.80,
                "model_used": "regex"
            }

        # Low sell products
        if any(k in text_lower for k in ["kurang laku", "slow moving", "tidak laku"]):
            return {
                "intent": "low_sell_products",
                "entities": {"time_range": "monthly", "limit": 10},
                "confidence": 0.85,
                "model_used": "regex"
            }

        # Top products
        if any(k in text_lower for k in ["terlaris", "paling laku", "best seller"]):
            return {
                "intent": "top_products",
                "entities": {"time_range": "monthly", "limit": 10},
                "confidence": 0.85,
                "model_used": "regex"
            }

        # Koreksi
        if any(k in text_lower for k in ["koreksi", "salah", "ubah", "ganti", "edit"]) and any(k in text_lower for k in ["transaksi", "tadi", "terakhir", "yang"]):
            return {
                "intent": "koreksi",
                "entities": {"reference": "transaksi_tadi"},
                "confidence": 0.85,
                "model_used": "regex"
            }

        # Default
        return {
            "intent": "general_inquiry",
            "entities": {},
            "confidence": 0.60,
            "model_used": "regex"
        }
