"""
Keyword-based parser (Layer 0)

Fast keyword matching for common intents.
Expected to handle ~30% of traffic with 5ms latency.
"""

from datetime import datetime
from typing import Dict, Any, Optional
from app.extractors.product_extractor import extract_product_name
from .base_parser import BaseParser


class KeywordParser(BaseParser):
    """
    Layer 0: Fast keyword matching (5ms)
    Returns classification if confidence >= 0.75
    """

    def parse(self, text: str, context: Optional[str] = None, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Parse using keyword matching.

        Args:
            text: User input text
            context: Optional conversation context (unused)
            tenant_id: Optional tenant identifier (unused)

        Returns:
            Classification dict or None if no match
        """
        text_lower = text.lower()

        # Define keyword patterns for each intent (EXPANDED - 50+ patterns)
        KEYWORD_PATTERNS = {
            "financial_report": {
                "patterns": [
                    # Laba Rugi
                    ["laporan", "laba"], ["laporan", "rugi"],
                    ["untung", "berapa"], ["rugi", "berapa"],
                    ["pendapatan", "total"], ["pendapatan", "bulan"],
                    ["laba", "bersih"], ["laba", "kotor"],
                    ["profit", "berapa"], ["loss", "berapa"],

                    # Neraca & Saldo
                    ["laporan", "neraca"], ["neraca", "saldo"],
                    ["saldo", "kas"], ["berapa", "kas"],
                    ["kas", "saya"], ["uang", "tersedia"],
                    ["total", "aset"], ["total", "hutang"],
                    ["piutang", "berapa"], ["hutang", "berapa"],
                    ["balance", "sheet"],

                    # Arus Kas
                    ["arus", "kas"], ["cash", "flow"],
                    ["kas", "masuk"], ["kas", "keluar"],

                    # Gaji
                    ["gaji", "siapa"], ["bayar", "gaji"],
                    ["gaji", "sudah"], ["gaji", "belum"],
                    ["gaji", "bulan"], ["daftar", "gaji"],
                    ["salary", "payment"],

                    # Pajak
                    ["pajak", "berapa"], ["pph", "berapa"],
                    ["omzet", "tahun"], ["omzet", "bulan"],
                    ["tax", "info"],
                ],
                "confidence": 0.80,
                "entities": {
                    "report_type": "laba_rugi",
                    "periode_pelaporan": datetime.now().strftime("%Y-%m")
                }
            },

            "inventory_query": {
                "patterns": [
                    # Stock level
                    ["stok", "berapa"], ["cek", "stok"],
                    ["sisa", "stok"], ["persediaan", "berapa"],
                    ["berapa", "stok"], ["jumlah", "stok"],

                    # Availability
                    ["ada", "stok"], ["tersedia", "berapa"],
                    ["masih", "ada"], ["stock", "check"],

                    # Location
                    ["stok", "gudang"], ["persediaan", "gudang"],
                ],
                "confidence": 0.80,
                "entities": {
                    "query_type": "stock_level",
                    "product_name": extract_product_name(text)
                }
            },

            "top_products": {
                "patterns": [
                    ["produk", "terlaris"], ["barang", "terlaris"],
                    ["paling", "laku"], ["best", "seller"],
                    ["terbanyak", "terjual"], ["top", "produk"],
                    ["favorit", "customer"], ["paling", "banyak"],
                    ["produk", "populer"], ["barang", "favorit"],
                ],
                "confidence": 0.85,
                "entities": {
                    "time_range": "monthly",
                    "limit": 10
                }
            },

            "low_sell_products": {
                "patterns": [
                    ["produk", "sepi"], ["kurang", "laku"],
                    ["paling", "sedikit"], ["tidak", "laku"],
                    ["lambat", "jual"], ["jarang", "terjual"],
                    ["barang", "mati"], ["slow", "moving"],
                ],
                "confidence": 0.85,
                "entities": {
                    "time_range": "monthly",
                    "limit": 10,
                    "threshold": 0
                }
            },

            "query_transaksi": {
                "patterns": [
                    # By party
                    ["transaksi", "supplier"], ["pembelian", "dari"],
                    ["transaksi", "customer"], ["penjualan", "ke"],

                    # By date
                    ["transaksi", "hari"], ["transaksi", "bulan"],
                    ["transaksi", "kemarin"], ["transaksi", "minggu"],
                    ["riwayat", "transaksi"], ["history", "transaksi"],

                    # By type
                    ["semua", "pembelian"], ["semua", "penjualan"],
                    ["daftar", "beban"], ["list", "transaction"],
                ],
                "confidence": 0.75,
                "entities": {
                    "date_range": "",
                    "jenis_transaksi": ""
                }
            },

            "inventory_history": {
                "patterns": [
                    ["riwayat", "stok"], ["history", "stok"],
                    ["pergerakan", "stok"], ["mutasi", "stok"],
                    ["stok", "masuk"], ["stok", "keluar"],
                    ["movement", "inventory"],
                ],
                "confidence": 0.80,
                "entities": {
                    "query_type": "movement_history",
                    "product_name": extract_product_name(text),
                    "date_range": ""
                }
            },

            "koreksi": {
                "patterns": [
                    ["ada", "salah"], ["koreksi", "transaksi"],
                    ["ubah", "transaksi"], ["edit", "transaksi"],
                    ["perbaiki", "data"], ["ralat", "transaksi"],
                    ["harusnya", "jadi"], ["seharusnya", "berapa"],
                ],
                "confidence": 0.75,
                "entities": {
                    "field_to_update": "",
                    "reference": "transaksi_terakhir"
                }
            }
        }

        # Check each intent pattern
        for intent, config in KEYWORD_PATTERNS.items():
            for pattern in config["patterns"]:
                # Check if ALL keywords in pattern exist
                if all(keyword in text_lower for keyword in pattern):
                    return {
                        "intent": intent,
                        "entities": config["entities"],
                        "confidence": config["confidence"],
                        "source": "keyword"
                    }

        return None