"""
Few-shot examples for each prompt type.

Indonesian language examples that help the LLM understand expected
input/output format. Used as additional user/assistant message pairs
injected before the actual user message.
"""

# =============================================================================
# CLASSIFY INTENT EXAMPLES
# =============================================================================
CLASSIFY_EXAMPLES_V1 = [
    # ACTION examples
    {
        "user": "buatkan invoice pembelian dari PT Sinar Jaya",
        "assistant": '{"intent": "ACTION", "action_type": "CREATE_PURCHASE_INVOICE", "confidence": 0.95, "reason": "User ingin membuat faktur pembelian"}',
    },
    {
        "user": "catat faktur penjualan ke toko maju, kaos 10 pcs @50rb",
        "assistant": '{"intent": "ACTION", "action_type": "CREATE_SALES_INVOICE", "confidence": 0.95, "reason": "User ingin membuat faktur penjualan"}',
    },
    {
        "user": "tambah vendor baru PT Global Teknik",
        "assistant": '{"intent": "ACTION", "action_type": "CREATE_VENDOR", "confidence": 0.90, "reason": "User ingin menambah vendor baru"}',
    },
    {
        "user": "bayar tagihan ke supplier ABC sebesar 5jt",
        "assistant": '{"intent": "ACTION", "action_type": "MAKE_PAYMENT", "confidence": 0.90, "reason": "User ingin melakukan pembayaran"}',
    },
    # READ examples
    {
        "user": "berapa saldo kas bulan ini?",
        "assistant": '{"intent": "READ", "action_type": null, "confidence": 0.90, "reason": "User ingin melihat saldo kas"}',
    },
    {
        "user": "tampilkan laporan laba rugi",
        "assistant": '{"intent": "READ", "action_type": null, "confidence": 0.85, "reason": "User ingin melihat laporan"}',
    },
    {
        "user": "cek stok barang kaos polos",
        "assistant": '{"intent": "READ", "action_type": null, "confidence": 0.85, "reason": "User ingin melihat data stok"}',
    },
    # CONFIRM examples
    {
        "user": "ya lanjutkan",
        "assistant": '{"intent": "CONFIRM", "action_type": null, "confidence": 0.95, "reason": "User mengkonfirmasi aksi"}',
    },
    {
        "user": "ok setuju",
        "assistant": '{"intent": "CONFIRM", "action_type": null, "confidence": 0.90, "reason": "User menyetujui"}',
    },
    # CANCEL examples
    {
        "user": "batal deh jangan jadi",
        "assistant": '{"intent": "CANCEL", "action_type": null, "confidence": 0.90, "reason": "User membatalkan"}',
    },
    {
        "user": "cancel aja",
        "assistant": '{"intent": "CANCEL", "action_type": null, "confidence": 0.90, "reason": "User membatalkan"}',
    },
    # UNCLEAR examples
    {
        "user": "cuaca hari ini gimana?",
        "assistant": '{"intent": "UNCLEAR", "action_type": null, "confidence": 0.85, "reason": "Di luar scope akuntansi"}',
    },
]


# =============================================================================
# PARSE INVOICE EXAMPLES
# =============================================================================
PARSE_EXAMPLES_V1 = [
    {
        "user": "faktur dari budi jaya, kaos 10 pcs @50rb, celana 5 pcs @75rb",
        "assistant": """{
  "document_type": "purchase_invoice",
  "counterparty_name": "Budi Jaya",
  "invoice_number": null,
  "issue_date": null,
  "due_date": null,
  "tax_rate": 11,
  "tax_inclusive": false,
  "notes": "",
  "items": [
    {"name": "Kaos", "qty": 10, "unit": "pcs", "price": 50000, "discount_percent": 0},
    {"name": "Celana", "qty": 5, "unit": "pcs", "price": 75000, "discount_percent": 0}
  ],
  "missing_fields": ["invoice_number", "issue_date"],
  "clarification_needed": null
}""",
    },
    {
        "user": "invoice penjualan ke toko maju, mie goreng 100 dus @25rb disc 5%, total termasuk ppn",
        "assistant": """{
  "document_type": "sales_invoice",
  "counterparty_name": "Toko Maju",
  "invoice_number": null,
  "issue_date": null,
  "due_date": null,
  "tax_rate": 11,
  "tax_inclusive": true,
  "notes": "",
  "items": [
    {"name": "Mie Goreng", "qty": 100, "unit": "dus", "price": 25000, "discount_percent": 5}
  ],
  "missing_fields": ["invoice_number", "issue_date"],
  "clarification_needed": null
}""",
    },
    {
        "user": "beli semen 50 sak dari PT Holcim harga 60rb per sak, faktur no INV-2024-001 tanggal 15 jan 2024",
        "assistant": """{
  "document_type": "purchase_invoice",
  "counterparty_name": "PT Holcim",
  "invoice_number": "INV-2024-001",
  "issue_date": "2024-01-15",
  "due_date": null,
  "tax_rate": 11,
  "tax_inclusive": false,
  "notes": "",
  "items": [
    {"name": "Semen", "qty": 50, "unit": "sak", "price": 60000, "discount_percent": 0}
  ],
  "missing_fields": ["due_date"],
  "clarification_needed": null
}""",
    },
]


# =============================================================================
# CONVERSATIONAL EXAMPLES
# =============================================================================
CONVERSATIONAL_EXAMPLES_V1 = [
    {
        "user": "halo milky",
        "assistant": "Halo! Ada yang bisa saya bantu soal keuangan bisnis kamu?",
    },
    {
        "user": "gimana cara buat faktur pembelian?",
        "assistant": "Gampang! Tinggal ketik aja detail fakturnya, misal: 'buat faktur dari PT ABC, beli kaos 10 pcs @50rb'. Nanti saya bantu susun datanya.",
    },
    {
        "user": "apa bedanya faktur pembelian dan penjualan?",
        "assistant": "Faktur pembelian itu tagihan dari supplier ke kamu (hutang usaha). Faktur penjualan itu tagihan dari kamu ke customer (piutang usaha). Mau buat yang mana?",
    },
    {
        "user": "PPN sekarang berapa persen?",
        "assistant": "PPN Indonesia sekarang 11%. Mau saya bantu hitung pajak untuk transaksi tertentu?",
    },
]


# =============================================================================
# EXAMPLES REGISTRY
# =============================================================================
EXAMPLES_REGISTRY = {
    "classify_intent": {
        "v1": CLASSIFY_EXAMPLES_V1,
        "active": "v1",
    },
    "parse_invoice": {
        "v1": PARSE_EXAMPLES_V1,
        "active": "v1",
    },
    "conversational": {
        "v1": CONVERSATIONAL_EXAMPLES_V1,
        "active": "v1",
    },
}


def get_examples_for(prompt_name: str) -> list:
    """
    Get the currently active few-shot examples for a prompt type.

    Args:
        prompt_name: Key in EXAMPLES_REGISTRY (e.g., "classify_intent")

    Returns:
        List of {"user": ..., "assistant": ...} example pairs.

    Raises:
        KeyError: If prompt_name is not registered.
    """
    entry = EXAMPLES_REGISTRY[prompt_name]
    active_version = entry["active"]
    return entry[active_version]


# =============================================================================
# PARSE MASTER DATA EXAMPLES
# =============================================================================
PARSE_MASTER_DATA_EXAMPLES_V1 = [
    {
        "user": "[CREATE_VENDOR] daftarkan vendor baru PT Sinar Jaya Abadi",
        "assistant": '{"action_type": "CREATE_VENDOR", "extracted_fields": {"name": "PT Sinar Jaya Abadi"}, "clarification_needed": false, "missing_fields": [], "assumptions": []}',
    },
    {
        "user": "[CREATE_PRODUCT] tambah produk kemeja putih lengan panjang, harga beli 110rb, harga jual 155rb per pcs",
        "assistant": '{"action_type": "CREATE_PRODUCT", "extracted_fields": {"name": "Kemeja Putih Lengan Panjang", "buy_price": 110000, "sell_price": 155000, "unit": "pcs"}, "clarification_needed": false, "missing_fields": [], "assumptions": []}',
    },
    {
        "user": "[CREATE_CUSTOMER] tambah pelanggan Toko Makmur Sentosa, alamat Jl. Sudirman 123",
        "assistant": '{"action_type": "CREATE_CUSTOMER", "extracted_fields": {"name": "Toko Makmur Sentosa", "address": "Jl. Sudirman 123"}, "clarification_needed": false, "missing_fields": [], "assumptions": []}',
    },
    {
        "user": "[CREATE_PRODUCT] daftarkan barang celana",
        "assistant": '{"action_type": "CREATE_PRODUCT", "extracted_fields": {"name": "Celana", "unit": "pcs"}, "clarification_needed": true, "missing_fields": ["buy_price", "sell_price"], "assumptions": ["Satuan default: pcs"]}',
    },
    {
        "user": "[CREATE_VENDOR] tambah supplier CV Maju Bersama, NPWP 01.234.567.8-012.345, telp 081234567890",
        "assistant": '{"action_type": "CREATE_VENDOR", "extracted_fields": {"name": "CV Maju Bersama", "tax_id": "01.234.567.8-012.345", "phone": "081234567890"}, "clarification_needed": false, "missing_fields": [], "assumptions": []}',
    },
    {
        "user": "[CREATE_PRODUCT] tambah jasa konsultasi pajak, harga jual 2,5jt per jam",
        "assistant": '{"action_type": "CREATE_PRODUCT", "extracted_fields": {"name": "Jasa Konsultasi Pajak", "sell_price": 2500000, "unit": "jam"}, "clarification_needed": true, "missing_fields": ["buy_price"], "assumptions": ["Jasa biasanya tidak punya harga beli, tapi field masih tersedia jika dibutuhkan"]}',
    },
]

# Register parse_master_data examples in EXAMPLES_REGISTRY
EXAMPLES_REGISTRY["parse_master_data"] = {
    "v1": PARSE_MASTER_DATA_EXAMPLES_V1,
    "active": "v1",
}
