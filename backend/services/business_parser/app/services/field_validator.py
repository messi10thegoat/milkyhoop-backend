"""
Field Validator for Multi-Turn Conversations
Purpose: Validate transaction completeness - works for ALL UMKM types
Phase 1.5 - Generic design for all transaction types
"""
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class FieldValidator:
    """Validates transaction completeness - works for ALL UMKM types"""

    # Required fields per transaction type (GENERIC - not business-specific)
    REQUIRED_FIELDS = {
        "pembelian": ["nama_produk", "jumlah", "satuan", "harga_satuan", "metode_pembayaran"],
        "penjualan": ["nama_produk", "jumlah", "satuan", "harga_satuan", "metode_pembayaran"],
        "beban": ["kategori_beban", "total_nominal", "metode_pembayaran"],
        "modal": ["total_nominal", "metode_pembayaran", "keterangan"],
        "prive": ["total_nominal", "metode_pembayaran", "keterangan"]
    }

    # Natural Indonesian clarification questions (friendly tone with "kak")
    CLARIFICATION_QUESTIONS = {
        "nama_produk": {
            "pembelian": "Maaf kak, barang apa yang dibeli?",
            "penjualan": "Maaf kak, barang apa yang dijual?"
        },
        "jumlah": "Berapa jumlahnya kak?",
        "satuan": "Satuannya apa ya kak? (misal: pcs, kg, liter, dll)",
        "harga_satuan": "Harganya berapa per satuan kak?",
        "total_nominal": "Total nominalnya berapa kak?",
        "metode_pembayaran": "Pembayarannya pakai apa kak? (tunai/transfer)",
        "kategori_beban": "Kategori bebannya apa kak? (misal: gaji, listrik, sewa, dll)",
        "keterangan": "Bisa kasih keterangan singkat kak?"
    }

    def __init__(self):
        """Initialize field validator"""
        logger.info("FieldValidator initialized - Phase 1.5")

    def detect_missing_fields(
        self,
        intent: str,
        extracted_data: Dict[str, Any]
    ) -> List[str]:
        """
        Detect which required fields are missing from extracted data

        Args:
            intent: Transaction intent (pembelian, penjualan, beban, modal, prive)
            extracted_data: Data extracted from user message

        Returns:
            List of missing field names (empty if complete)
        """
        try:
            # Get required fields for this intent
            required = self.REQUIRED_FIELDS.get(intent, [])

            if not required:
                logger.warning(f"Unknown intent for validation: {intent}")
                return []

            # Check which fields are missing or empty
            missing = []
            for field in required:
                value = extracted_data.get(field)

                # Field is missing if:
                # - Not present in dict (key doesn't exist)
                # - None
                # - Empty string
                # - Zero (for numeric fields like jumlah, harga_satuan, total_nominal)
                # CRITICAL: Check if key exists first, then check value
                if field not in extracted_data:
                    missing.append(field)
                elif value is None:
                    missing.append(field)
                elif isinstance(value, str) and value.strip() == "":
                    missing.append(field)
                elif isinstance(value, (int, float)) and value == 0:
                    # Special case: total_nominal can be 0 for beban, but jumlah/harga_satuan cannot be 0
                    if field in ["jumlah", "harga_satuan"]:
                        missing.append(field)
                    elif field == "total_nominal" and extracted_data.get("items"):
                        # If we have items, total_nominal should be calculated from items
                        # Only missing if no items and no total_nominal
                        pass
                    else:
                        missing.append(field)

            if missing:
                logger.info(f"[PHASE1.5] Missing fields for {intent}: {missing}")
            else:
                logger.info(f"[PHASE1.5] ✅ All required fields present for {intent}")

            return missing

        except Exception as e:
            logger.error(f"Error detecting missing fields: {e}")
            return []

    def generate_question(
        self,
        intent: str,
        missing_field: str
    ) -> str:
        """
        Generate natural clarification question for missing field

        Args:
            intent: Transaction intent
            missing_field: Name of the missing field

        Returns:
            Natural Indonesian question
        """
        try:
            # Get question template
            question_template = self.CLARIFICATION_QUESTIONS.get(missing_field)

            # Handle intent-specific questions (nama_produk)
            if isinstance(question_template, dict):
                question = question_template.get(intent, question_template.get("default", ""))
            else:
                question = question_template

            if not question:
                # Fallback generic question
                question = f"Maaf kak, bisa kasih info tentang {missing_field}?"

            logger.info(f"[PHASE1.5] Generated question for {missing_field}: {question}")
            return question

        except Exception as e:
            logger.error(f"Error generating question: {e}")
            return f"Maaf kak, bisa kasih info tentang {missing_field}?"

    def is_complete(
        self,
        intent: str,
        extracted_data: Dict[str, Any]
    ) -> bool:
        """
        Check if transaction has all required fields

        Args:
            intent: Transaction intent
            extracted_data: Data extracted from user message

        Returns:
            True if complete, False if missing fields
        """
        missing = self.detect_missing_fields(intent, extracted_data)
        is_complete = len(missing) == 0

        logger.info(f"[PHASE1.5] Transaction completeness check: {is_complete} (intent={intent})")

        return is_complete

    def get_all_missing_fields(
        self,
        intent: str,
        extracted_data: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Get all missing fields with their clarification questions

        Args:
            intent: Transaction intent
            extracted_data: Data extracted from user message

        Returns:
            Dict mapping field names to clarification questions
        """
        missing_fields = self.detect_missing_fields(intent, extracted_data)

        result = {}
        for field in missing_fields:
            result[field] = self.generate_question(intent, field)

        return result

    def format_confirmation_message(
        self,
        intent: str,
        extracted_data: Dict[str, Any]
    ) -> str:
        """
        Format transaction data into friendly confirmation message

        Args:
            intent: Transaction intent
            extracted_data: Complete transaction data

        Returns:
            Formatted confirmation message in Indonesian
        """
        try:
            # Intent-specific formatting
            if intent == "pembelian":
                msg = f"📝 Konfirmasi Pembelian:\n\n"
                
                # Get data from items array if available, otherwise from flat fields
                items = extracted_data.get("items", [])
                if items and len(items) > 0:
                    first_item = items[0]
                    nama_produk = first_item.get("nama_produk", extracted_data.get('nama_produk', '-'))
                    jumlah = first_item.get("jumlah", extracted_data.get('jumlah', 0))
                    satuan = first_item.get("satuan", extracted_data.get('satuan', ''))
                    harga_satuan = first_item.get("harga_satuan", extracted_data.get('harga_satuan', 0))
                    total = first_item.get("subtotal", 0) or extracted_data.get('total_nominal', 0)
                else:
                    nama_produk = extracted_data.get('nama_produk', '-')
                    jumlah = extracted_data.get('jumlah', 0)
                    satuan = extracted_data.get('satuan', '')
                    harga_satuan = extracted_data.get('harga_satuan', 0)
                    # Calculate total from jumlah * harga_satuan if not set
                    total = extracted_data.get('total_nominal', 0) or extracted_data.get('total_harga', 0)
                    if total == 0 and jumlah and harga_satuan:
                        total = int(jumlah * harga_satuan)
                
                msg += f"Barang: {nama_produk}\n"
                msg += f"Jumlah: {jumlah} {satuan}\n"
                msg += f"Harga satuan: Rp {harga_satuan:,.0f}\n"
                msg += f"Total: Rp {total:,.0f}\n"
                msg += f"Pembayaran: {extracted_data.get('metode_pembayaran', '-')}\n"

            elif intent == "penjualan":
                msg = f"📝 Konfirmasi Penjualan:\n\n"
                
                # Get data from items array if available, otherwise from flat fields
                items = extracted_data.get("items", [])
                if items and len(items) > 0:
                    first_item = items[0]
                    nama_produk = first_item.get("nama_produk", extracted_data.get('nama_produk', '-'))
                    jumlah = first_item.get("jumlah", extracted_data.get('jumlah', 0))
                    satuan = first_item.get("satuan", extracted_data.get('satuan', ''))
                    harga_satuan = first_item.get("harga_satuan", extracted_data.get('harga_satuan', 0))
                    total = first_item.get("subtotal", 0) or extracted_data.get('total_nominal', 0)
                else:
                    nama_produk = extracted_data.get('nama_produk', '-')
                    jumlah = extracted_data.get('jumlah', 0)
                    satuan = extracted_data.get('satuan', '')
                    harga_satuan = extracted_data.get('harga_satuan', 0)
                    # Calculate total from jumlah * harga_satuan if not set
                    total = extracted_data.get('total_nominal', 0) or extracted_data.get('total_harga', 0)
                    if total == 0 and jumlah and harga_satuan:
                        total = int(jumlah * harga_satuan)
                
                msg += f"Barang: {nama_produk}\n"
                msg += f"Jumlah: {jumlah} {satuan}\n"
                msg += f"Harga satuan: Rp {harga_satuan:,.0f}\n"
                msg += f"Total: Rp {total:,.0f}\n"
                msg += f"Pembayaran: {extracted_data.get('metode_pembayaran', '-')}\n"

            elif intent == "beban":
                msg = f"📝 Konfirmasi Beban:\n\n"
                msg += f"Kategori: {extracted_data.get('kategori_beban', '-')}\n"
                msg += f"Total: Rp {extracted_data.get('total_nominal', 0):,.0f}\n"
                msg += f"Pembayaran: {extracted_data.get('metode_pembayaran', '-')}\n"
                if extracted_data.get('keterangan'):
                    msg += f"Keterangan: {extracted_data.get('keterangan', '-')}\n"

            elif intent == "modal":
                msg = f"📝 Konfirmasi Modal:\n\n"
                msg += f"Total: Rp {extracted_data.get('total_nominal', 0):,.0f}\n"
                msg += f"Pembayaran: {extracted_data.get('metode_pembayaran', '-')}\n"
                msg += f"Keterangan: {extracted_data.get('keterangan', '-')}\n"

            elif intent == "prive":
                msg = f"📝 Konfirmasi Prive (Penarikan):\n\n"
                msg += f"Total: Rp {extracted_data.get('total_nominal', 0):,.0f}\n"
                msg += f"Pembayaran: {extracted_data.get('metode_pembayaran', '-')}\n"
                msg += f"Keterangan: {extracted_data.get('keterangan', '-')}\n"

            else:
                msg = f"📝 Konfirmasi Transaksi:\n\n{extracted_data}\n"

            msg += f"\n✅ Lanjutkan? (ya/tidak)\n"
            msg += f"📝 Edit? Sebutkan yang mau diubah\n"
            msg += f"❌ Batal? Ketik 'batal'"

            return msg

        except Exception as e:
            logger.error(f"Error formatting confirmation: {e}")
            return f"Konfirmasi transaksi {intent}:\n{extracted_data}"


# Singleton instance for easy import
field_validator = FieldValidator()