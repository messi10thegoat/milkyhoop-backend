"""
Regex-based parser (Layer 1)

Regex extraction for structured transaction patterns.
Expected to handle ~30% of traffic with 10ms latency.
"""

import re
from typing import Dict, Any, Optional
from .base_parser import BaseParser
from app.extractors.payment_extractor import extract_payment_method


class RegexParser(BaseParser):
    """
    Layer 1: Regex-based extraction (10ms)
    Returns classification if confidence >= 0.80
    """

    def parse(self, text: str, context: Optional[str] = None, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Parse using regex patterns.

        Args:
            text: User input text
            context: Optional conversation context (unused)
            tenant_id: Optional tenant identifier (unused)

        Returns:
            Classification dict or None if no match
        """
        text_lower = text.lower()

        # ===========================================
        # PATTERN 0: Transaction with satuan
        # Match: "jual kopi 10 gelas @15ribu", "jual teh 100 gelas @15000"
        # ===========================================
        tx_satuan_pattern = re.compile(
            r'\b(jual|beli|bayar)\s+([a-zA-Z][a-zA-Z0-9\s]+?)\s+(\d+)\s+([a-zA-Z]+)\s+@\s*(?:rp\s*)?(\d+[\.,]?\d*)\s*(rb|ribu|k|jt|juta)?',
            re.IGNORECASE
        )
        tx_satuan_match = tx_satuan_pattern.search(text)

        if tx_satuan_match:
            jenis_raw = tx_satuan_match.group(1).lower()
            nama_produk = tx_satuan_match.group(2).strip()
            jumlah = int(tx_satuan_match.group(3))
            satuan = tx_satuan_match.group(4).strip()
            harga_raw_str = tx_satuan_match.group(5).replace('.', '').replace(',', '')
            harga_raw = float(harga_raw_str)
            unit = (tx_satuan_match.group(6) or "").lower()

            # Determine jenis_transaksi
            if jenis_raw in ["jual", "terjual"]:
                jenis_transaksi = "penjualan"
            elif jenis_raw in ["beli", "membeli"]:
                jenis_transaksi = "pembelian"
            else:
                jenis_transaksi = "beban"

            # Convert unit to full amount
            if 'jt' in unit or 'juta' in unit:
                harga_satuan = int(harga_raw * 1000000)
            elif 'rb' in unit or 'ribu' in unit or 'k' in unit:
                harga_satuan = int(harga_raw * 1000)
            else:
                harga_satuan = int(harga_raw)

            subtotal = jumlah * harga_satuan
            total_nominal = subtotal

            # Extract metode_pembayaran with fuzzy matching (handles typos)
            metode_pembayaran = extract_payment_method(text_lower, use_fuzzy=True)

            # Build entities
            entities = {
                "jenis_transaksi": jenis_transaksi,
                "total_nominal": total_nominal,
                "items": [{
                    "nama_produk": nama_produk,
                    "jumlah": jumlah,
                    "satuan": satuan,
                    "harga_satuan": harga_satuan,
                    "subtotal": subtotal
                }]
            }

            if metode_pembayaran:
                entities["metode_pembayaran"] = metode_pembayaran

            # Add inventory impact for penjualan/pembelian
            if jenis_transaksi in ["penjualan", "pembelian"]:
                jenis_movement = "keluar" if jenis_transaksi == "penjualan" else "masuk"
                jumlah_movement = -float(jumlah) if jenis_transaksi == "penjualan" else float(jumlah)

                entities["inventory_impact"] = {
                    "is_tracked": True,
                    "jenis_movement": jenis_movement,
                    "lokasi_gudang": "gudang-utama",
                    "items_inventory": [{
                        "produk_id": "",
                        "jumlah_movement": jumlah_movement,
                        "stok_setelah": 0
                    }]
                }

            return {
                "intent": "transaction_record",
                "entities": entities,
                "confidence": 0.90,
                "source": "regex"
            }

        # ===========================================
        # PATTERN 1: Transaction Record
        # Match: "jual 10 kopi @15rb", "beli 5 buku @20000"
        # ===========================================
        tx_pattern = re.compile(
            r'\b(jual|beli|bayar)\s+(\d+)\s+([a-zA-Z][a-zA-Z0-9\s]*?)\s+@\s*(?:rp\s*)?(\d+)\s*(rb|ribu|k|jt|juta)?',
            re.IGNORECASE
        )
        tx_match = tx_pattern.search(text)

        if tx_match:
            jenis_raw = tx_match.group(1).lower()
            jumlah = int(tx_match.group(2))
            nama_produk = tx_match.group(3).strip()
            harga_raw = float(tx_match.group(4))
            unit = (tx_match.group(5) or "").lower()

            # Determine jenis_transaksi
            if jenis_raw in ["jual", "terjual"]:
                jenis_transaksi = "penjualan"
            elif jenis_raw in ["beli", "membeli"]:
                jenis_transaksi = "pembelian"
            else:
                jenis_transaksi = "beban"

            # Convert unit to full amount
            if 'jt' in unit or 'juta' in unit:
                harga_satuan = int(harga_raw * 1000000)
            elif 'rb' in unit or 'ribu' in unit or 'k' in unit:
                harga_satuan = int(harga_raw * 1000)
            else:
                harga_satuan = int(harga_raw)

            subtotal = jumlah * harga_satuan
            total_nominal = subtotal

            # Extract metode_pembayaran with fuzzy matching (handles typos)
            metode_pembayaran = extract_payment_method(text_lower, use_fuzzy=True)

            # Build entities
            entities = {
                "jenis_transaksi": jenis_transaksi,
                "total_nominal": total_nominal,
                "items": [{
                    "nama_produk": nama_produk,
                    "jumlah": jumlah,
                    "satuan": "pcs",
                    "harga_satuan": harga_satuan,
                    "subtotal": subtotal
                }]
            }

            if metode_pembayaran:
                entities["metode_pembayaran"] = metode_pembayaran

            # Add inventory impact for penjualan/pembelian
            if jenis_transaksi in ["penjualan", "pembelian"]:
                jenis_movement = "keluar" if jenis_transaksi == "penjualan" else "masuk"
                jumlah_movement = -float(jumlah) if jenis_transaksi == "penjualan" else float(jumlah)

                entities["inventory_impact"] = {
                    "is_tracked": True,
                    "jenis_movement": jenis_movement,
                    "lokasi_gudang": "gudang-utama",
                    "items_inventory": [{
                        "produk_id": "",
                        "jumlah_movement": jumlah_movement,
                        "stok_setelah": 0
                    }]
                }

            return {
                "intent": "transaction_record",
                "entities": entities,
                "confidence": 0.90,
                "source": "regex"
            }

        # ===========================================
        # PATTERN 2: Simple Transaction (no @)
        # Match: "jual 100rb", "bayar 50jt"
        # ===========================================
        simple_tx_pattern = re.compile(
            r'\b(jual|beli|bayar)\s+(?:rp\s*)?(\d+)\s*(rb|ribu|jt|juta)?',
            re.IGNORECASE
        )
        simple_match = simple_tx_pattern.search(text)

        if simple_match:
            jenis_raw = simple_match.group(1).lower()
            amount = float(simple_match.group(2))
            unit = (simple_match.group(3) or "").lower()

            # Convert to full amount
            if 'jt' in unit or 'juta' in unit:
                total_nominal = int(amount * 1000000)
            elif 'rb' in unit or 'ribu' in unit:
                total_nominal = int(amount * 1000)
            else:
                # Default: assume ribu if < 10000, else full amount
                total_nominal = int(amount * 1000 if amount < 10000 else amount)

            # Determine jenis_transaksi
            if jenis_raw in ["jual", "terjual"]:
                jenis_transaksi = "penjualan"
            elif jenis_raw in ["beli", "membeli"]:
                jenis_transaksi = "pembelian"
            else:
                jenis_transaksi = "beban"

            return {
                "intent": "transaction_record",
                "entities": {
                    "jenis_transaksi": jenis_transaksi,
                    "total_nominal": total_nominal
                },
                "confidence": 0.75,
                "source": "regex"
            }

        # ===========================================
        # PATTERN 3: Retur (Return)
        # Match: "return 3 kemeja", "retur 5 buku rusak"
        # ===========================================
        if any(k in text_lower for k in ["return", "retur", "rusak", "refund"]):
            retur_pattern = re.compile(
                r'\b(?:return|retur)\s+(\d+)\s+([a-zA-Z][a-zA-Z0-9\s]+)',
                re.IGNORECASE
            )
            retur_match = retur_pattern.search(text)

            if retur_match:
                jumlah = int(retur_match.group(1))
                nama_produk = retur_match.group(2).strip()

                return {
                    "intent": "retur_penjualan",
                    "entities": {
                        "jenis_transaksi": "retur_penjualan",
                        "items": [{
                            "nama_produk": nama_produk,
                            "jumlah": jumlah,
                            "satuan": "pcs"
                        }],
                        "keterangan": "Retur barang",
                        "is_retur": True
                    },
                    "confidence": 0.85,
                    "source": "regex"
                }

        # ===========================================
        # PATTERN 4: Pembayaran Hutang
        # Match: "bayar cicilan 5jt", "bayar hutang 10jt"
        # ===========================================
        if any(k in text_lower for k in ["bayar cicilan", "bayar hutang", "pelunasan"]):
            hutang_pattern = re.compile(
                r'\b(?:bayar\s+(?:cicilan|hutang|utang))\s+(?:rp\s*)?(\d+)\s*(rb|ribu|jt|juta)?',
                re.IGNORECASE
            )
            hutang_match = hutang_pattern.search(text)

            if hutang_match:
                amount = float(hutang_match.group(1))
                unit = (hutang_match.group(2) or "").lower()

                if 'jt' in unit or 'juta' in unit:
                    total_nominal = int(amount * 1000000)
                elif 'rb' in unit or 'ribu' in unit:
                    total_nominal = int(amount * 1000)
                else:
                    total_nominal = int(amount)

                return {
                    "intent": "pembayaran_hutang",
                    "entities": {
                        "jenis_transaksi": "pembayaran_hutang",
                        "total_nominal": total_nominal
                    },
                    "confidence": 0.85,
                    "source": "regex"
                }

        return None