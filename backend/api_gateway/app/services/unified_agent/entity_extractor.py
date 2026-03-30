"""
Entity Extractor — Compiler Pipeline Stage 1.

Extracts intent + entities from user message using OpenAI response_format (json_schema).
Output is guaranteed to match schema — no normalization needed.

This replaces the LLM agent loop for supported intents.
LLM role: language interpreter only. NOT decision maker.
"""

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("unified_agent.entity_extractor")


SESSION_CONTEXT_HINTS = {
    # AR domain
    "query_ar_outstanding": "Topik sebelumnya: piutang (AR outstanding, faktur penjualan belum lunas)",
    "query_ar_invoices": "Topik sebelumnya: daftar faktur penjualan / piutang",
    "query_customer_ar": "Topik sebelumnya: piutang pelanggan tertentu",
    "query_sales_invoices_list": "Topik sebelumnya: daftar faktur penjualan",
    "query_sales_invoice_detail": "Topik sebelumnya: detail faktur penjualan",
    "query_sales_invoices_overdue": "Topik sebelumnya: faktur penjualan jatuh tempo",
    "query_receive_payments_list": "Topik sebelumnya: daftar penerimaan pembayaran",
    "query_sales_invoices_summary": "Topik sebelumnya: ringkasan penjualan",
    # AP domain
    "query_ap_outstanding": "Topik sebelumnya: hutang (AP outstanding, faktur pembelian belum lunas)",
    "query_bills_list": "Topik sebelumnya: daftar faktur pembelian / hutang",
    "query_vendor_ap": "Topik sebelumnya: hutang ke vendor tertentu",
    "query_bill_detail": "Topik sebelumnya: detail faktur pembelian",
    "query_bills_overdue": "Topik sebelumnya: tagihan jatuh tempo",
    "query_bill_payments_list": "Topik sebelumnya: daftar pembayaran keluar",
    "query_bills_summary": "Topik sebelumnya: ringkasan pembelian",
    # Items domain
    "query_item_detail": "Topik sebelumnya: detail barang/produk",
    "query_items_summary": "Topik sebelumnya: ringkasan barang",
    "query_items_low_stock": "Topik sebelumnya: stok rendah",
    # Customer/Vendor domain
    "query_customer_detail": "Topik sebelumnya: detail pelanggan",
    "query_customers_list": "Topik sebelumnya: daftar pelanggan",
    "query_customers_summary": "Topik sebelumnya: ringkasan pelanggan",
    "query_vendor_detail": "Topik sebelumnya: detail pemasok/vendor",
    "query_vendors_list": "Topik sebelumnya: daftar pemasok/vendor",
    "query_vendors_summary": "Topik sebelumnya: ringkasan vendor",
    # Bank domain
    "query_cash_balance": "Topik sebelumnya: saldo kas/bank",
    "query_bank_accounts_list": "Topik sebelumnya: daftar rekening bank",
    "query_bank_account_balance": "Topik sebelumnya: saldo rekening tertentu",
    "query_bank_transactions": "Topik sebelumnya: transaksi bank",
    # Expense domain
    "query_expenses_list": "Topik sebelumnya: daftar pengeluaran/biaya",
    "query_expenses_summary": "Topik sebelumnya: ringkasan pengeluaran",
    "query_expenses_by_account": "Topik sebelumnya: pengeluaran per akun",
    # Journal/Accounts
    "query_journals_list": "Topik sebelumnya: daftar jurnal",
    "query_accounts_list": "Topik sebelumnya: daftar akun",
    # Calc intents
    "calc_rank_customers_by_ar": "Topik sebelumnya: ranking pelanggan berdasarkan piutang",
    "calc_rank_vendors_by_ap": "Topik sebelumnya: ranking vendor berdasarkan hutang",
    "calc_sum_sales_this_month": "Topik sebelumnya: total penjualan bulan ini",
    "calc_sum_purchases_this_month": "Topik sebelumnya: total pembelian bulan ini",
    "calc_sum_expenses_this_month": "Topik sebelumnya: total pengeluaran bulan ini",
}


@dataclass
class ExtractionResult:
    """Result of entity extraction."""

    intent: str = "ambiguous"
    entities: dict = field(default_factory=dict)
    modifiers: list = field(default_factory=list)
    confidence: float = 1.0
    raw_response: dict = field(default_factory=dict)
    needs_escalation: bool = False


EXTRACTION_SCHEMAS = {
    "general": {
        "type": "json_schema",
        "json_schema": {
            "name": "entity_extraction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "The action intent detected from user message",
                        "enum": [
                            "create_customer",
                            "create_vendor",
                            "create_warehouse",
                            "create_bank_account",
                            "create_item",
                            "create_sales_invoice",
                            "create_bill",
                            "create_receive_payment",
                            "create_bill_payment",
                            "create_expense",
                            "create_journal_entry",
                            "void_sales_invoice",
                            "void_bill",
                            "void_receive_payment",
                            "void_bill_payment",
                            "void_expense",
                            "reverse_journal",
                            "update_customer",
                            "update_vendor",
                            "update_item",
                            "delete_customer",
                            "delete_vendor",
                            "query_item_detail",
                            "query_item_stock_card",
                            "query_item_transactions",
                            "query_items_summary",
                            "query_items_low_stock",
                            "query_items_top_products",
                            "query_items_slow_moving",
                            "query_items_margins",
                            "query_warehouse_stock",
                            "query_categories_list",
                            "query_items_search",
                            "query_items_by_stock",
                            "query_items_inactive",
                            "query_items_units",
                            "query_items_stats",
                            "query_inventory_summary",
                            "query_stock_adjustments",
                            "query_stock_adjustments_summary",
                            "query_stock_transfers",
                            "query_stock_in_transit",
                            "query_warehouses",
                            "query_warehouse_stock_value",
                            "query_inventory_health",
                            "query_item_journal",
                            "create_item",
                            "create_warehouse",
                            "quick_stock_adjustment",
                            "create_stock_transfer",
                            "query_item_activity",
                            "query_item_related",
                            "query_item_batches",
                            "calc_avg_harga_jual",
                            "calc_sum_harga_jual",
                            "calc_count_items_active",
                            "calc_count_items_inactive",
                            "calc_rank_items_by_price",
                            "calc_avg_harga_beli",
                            "calc_sum_stok",
                            "calc_count_customers_active",
                            "calc_count_vendors_active",
                            "calc_count_bills_outstanding",
                            "calc_sum_bills_outstanding",
                            "calc_count_invoices_outstanding",
                            "calc_sum_invoices_outstanding",
                            "calc_count_bank_accounts",
                            "calc_sum_bank_balance",
                            "calc_sum_all_bank_balances",
                            "calc_count_expenses_month",
                            "calc_rank_items_by_stock",
                            "calc_sum_harga_beli",
                            "calc_rank_customers_by_ar",
                            "calc_rank_vendors_by_ap",
                            "calc_sum_sales_this_month",
                            "calc_sum_purchases_this_month",
                            "calc_sum_expenses_this_month",
                            "calc_sum_received_this_month",
                            "calc_sum_paid_this_month",
                            "calc_count_sales_invoices_active",
                            "calc_count_bills_active",
                            "contextual_drill_down",
                            "query_customer_ar",
                            "query_vendor_ap",
                            "query_sales_invoices_overdue",
                            "query_bills_overdue",
                            "query_expenses_by_account",
                            "query_receive_payments_list",
                            "query_receive_payment_detail",
                            "query_bill_payments_list",
                            "query_bill_payment_detail",
                            "query_journals_list",
                            "query_journal_detail",
                            "query_accounts_list",
                            "query_account_detail",
                            "query_stock_adjustment_detail",
                            "query_bank_account_balance",
                            "chitchat",
                            "query",
                            "ambiguous",
                        ],
                    },
                    "entities": {
                        "type": "object",
                        "description": "Extracted entities from user message. Only include fields explicitly mentioned.",
                        "properties": {
                            "customer_name": {"type": ["string", "null"]},
                            "vendor_name": {"type": ["string", "null"]},
                            "item_name": {"type": ["string", "null"]},
                            "bank_name": {"type": ["string", "null"]},
                            "warehouse_name": {"type": ["string", "null"]},
                            "invoice_number": {"type": ["string", "null"]},
                            "bill_number": {"type": ["string", "null"]},
                            "amount": {"type": ["number", "null"]},
                            "quantity": {"type": ["number", "null"]},
                            "unit_price": {"type": ["number", "null"]},
                            "description": {"type": ["string", "null"]},
                            "date": {"type": ["string", "null"]},
                            "phone": {"type": ["string", "null"]},
                            "email": {"type": ["string", "null"]},
                            "address": {"type": ["string", "null"]},
                            "reason": {"type": ["string", "null"]},
                            "name": {"type": ["string", "null"]},
                            "account_type": {"type": ["string", "null"]},
                            "payment_method": {"type": ["string", "null"]},
                            "item_type": {
                                "type": ["string", "null"],
                                "description": "goods, service, or non_inventory",
                            },
                            "base_unit": {
                                "type": ["string", "null"],
                                "description": "Unit: pcs, kg, box, roll, meter, tube, dll",
                            },
                        },
                        "additionalProperties": False,
                        "required": [
                            "customer_name",
                            "vendor_name",
                            "item_name",
                            "bank_name",
                            "warehouse_name",
                            "invoice_number",
                            "bill_number",
                            "amount",
                            "quantity",
                            "unit_price",
                            "description",
                            "date",
                            "phone",
                            "email",
                            "address",
                            "reason",
                            "name",
                            "account_type",
                            "payment_method",
                            "item_type",
                            "base_unit",
                        ],
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Special modifiers: like_previous, half_amount, full_amount, etc.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0.0-1.0 that extraction is correct",
                    },
                },
                "required": ["intent", "entities", "modifiers", "confidence"],
                "additionalProperties": False,
            },
        },
    }
}

EXTRACTION_SYSTEM_PROMPT = """Kamu adalah entity extractor untuk sistem akuntansi Indonesia.

TUGAS: Extract intent dan entities dari pesan user. Output HANYA JSON sesuai schema.

RULES:
1. HANYA extract apa yang user EKSPLISIT sebutkan. Jangan assume.
2. Field yang tidak disebut = null.
3. Angka: convert "5 juta" -> 5000000, "500 ribu" -> 500000, "2,5 jt" -> 2500000.
4. "ke BCA/Mandiri/BRI" = bank_name, BUKAN vendor_name.
4b. "dari kas/bank/BCA" (sumber bayar) = bank_name. "kas" = bank_name "kas".
5. confidence: 1.0 jika intent + entities jelas. 0.5 jika ambigu. 0.3 jika tidak yakin.
6. Jika pesan tidak ada hubungan dengan aksi (greeting, tanya info) -> intent: "chitchat" atau "query".
8. PENTING: Jika pertanyaan tentang barang/stok/inventory, GUNAKAN intent query_* yang spesifik (query_item_detail, query_items_low_stock, dll). Jangan pakai "query" generik untuk pertanyaan barang.
7b. item_type: HANYA set jika user EKSPLISIT sebut tipenya (misal "barang", "jasa", "persediaan", "non-persediaan"). "goods" = persediaan/barang, "service" = jasa/layanan, "non_inventory" = non-persediaan. Jika user TIDAK eksplisit sebut tipe → null. JANGAN tebak dari nama produk.
7c. base_unit: satuan barang yang disebut user (pcs, kg, box, roll, tube, meter, lusin, dll). null jika tidak disebut.
7. Jika ada referensi implisit ("yang kemarin", "seperti biasa") -> tambah modifier yang sesuai.
8b. Jika pesan user hanya berisi jawaban singkat (misal "tablet", "goods", "pcs", "jasa") dan konteks sesi menunjukkan ada aksi PENDING, gunakan intent dari konteks sesi. Contoh: konteks PENDING create_item, user = "satuannya tablet" → intent: create_item, base_unit: "tablet".

INTENT RULES:

== AKSI CRUD (pattern-based) ==
CATATAN: CRUD intent (buat/edit/hapus) biasanya sudah di-detect oleh sistem sebelum prompt ini dipanggil. Mapping di bawah ini adalah FALLBACK jika sistem gagal mendeteksi.
Tentukan intent dari AKSI + ENTITY TYPE. Pahami MAKSUD user, jangan cocokkan kata per kata.

AKSI -> PREFIX:
- MEMBUAT/MENAMBAH sesuatu yang BELUM ADA -> create_
- MENGUBAH/MENGEDIT sesuatu yang SUDAH ADA -> update_
- MENGHAPUS/MENDELETE sesuatu yang SUDAH ADA -> delete_
- Kata kunci CREATE: buat, tambah, bikin, daftarkan, catat, input
- Kata kunci UPDATE: edit, ubah, ganti, update, perbarui, revisi, koreksi, perbaiki
- Kata kunci DELETE: hapus, delete, buang, hilangkan, remove

ENTITY TYPE -> SUFFIX:
- barang/item/produk/jasa -> _item
- pelanggan/customer -> _customer
- vendor/supplier/pemasok -> _vendor
- gudang/warehouse -> _warehouse
- rekening/kas/bank account -> _bank_account
- faktur penjualan/sales invoice -> _sales_invoice
- faktur pembelian/tagihan/bill/purchase invoice -> _bill
- jurnal/journal -> _journal_entry
- biaya/expense/pengeluaran -> _expense

Contoh: "edit item X" -> update_item. "hapus pelanggan Y" -> delete_customer. "tambah supplier Z" -> create_vendor.

NAMA ENTITY: semua teks setelah kata entity type = nama entity (item_name/customer_name/vendor_name).
Contoh: "edit item kudu tegas karo bocah" -> intent=update_item, item_name="kudu tegas karo bocah"
Contoh: "hapus barang Vitamin C 500mg" -> intent=delete_item, item_name="Vitamin C 500mg"
Contoh: "ubah harga produk Emas" -> intent=update_item, item_name="Emas"

== AKSI KHUSUS (fixed mapping) ==
- "terima pembayaran" -> create_receive_payment
- "bayar tagihan" -> create_bill_payment
- "void/batalkan faktur penjualan" -> void_sales_invoice
- "void/batalkan faktur pembelian/tagihan" -> void_bill
- "sesuaikan stok" / "tambah stok" / "kurangi stok" -> quick_stock_adjustment
- "transfer stok" / "pindah barang" -> create_stock_transfer
- PENTING: "faktur pembelian" = _bill, "faktur penjualan" = _sales_invoice

== QUERY (specific mappings) ==
- "berapa stok X?" / "cek barang X" / "detail barang X" -> query_item_detail
- "kartu stok X" / "riwayat stok X" -> query_item_stock_card
- "transaksi barang X" -> query_item_transactions
- "ringkasan barang" / "total barang" -> query_items_summary
- "stok rendah" / "hampir habis" -> query_items_low_stock
- "barang terlaris" / "paling laku" (PENJUALAN) -> query_items_top_products
- "barang lambat" / "dead stock" -> query_items_slow_moving
- "margin produk" -> query_items_margins
- "stok di gudang X" -> query_warehouse_stock
- "daftar kategori" -> query_categories_list
- "barang tidak aktif" -> query_items_inactive
- "cari barang X" -> query_items_search
- "stok terbanyak" / "ranking stok" -> query_items_by_stock
- PENTING: "stok terbanyak" = query_items_by_stock (jumlah). "terlaris" = query_items_top_products (penjualan). BEDA!
- "daftar satuan" -> query_items_units
- "statistik barang" -> query_items_stats
- "ringkasan inventory" -> query_inventory_summary
- "daftar penyesuaian stok" -> query_stock_adjustments
- "ringkasan penyesuaian" -> query_stock_adjustments_summary
- "daftar transfer stok" -> query_stock_transfers
- "barang dalam perjalanan" -> query_stock_in_transit
- "daftar gudang" -> query_warehouses
- "nilai stok gudang X" -> query_warehouse_stock_value
- "health check inventory" -> query_inventory_health
- "jurnal barang X" -> query_item_journal
- "aktivitas barang X" -> query_item_activity
- "dokumen terkait barang X" -> query_item_related
- "batch barang X" / "expiry" -> query_item_batches

== KALKULASI (code-driven, tanpa LLM) ==
- "rata-rata harga jual" / "average harga jual" -> calc_avg_harga_jual
- "rata-rata harga beli" -> calc_avg_harga_beli
- "total harga jual" / "jumlah semua harga jual" -> calc_sum_harga_jual
- "total stok" / "jumlah semua stok" -> calc_sum_stok
- "berapa item aktif" / "jumlah barang aktif" / "total produk aktif" -> calc_count_items_active
- "berapa item tidak aktif" / "barang nonaktif" -> calc_count_items_inactive
- "item termahal" / "top harga" / "ranking harga jual" -> calc_rank_items_by_price
- "berapa pelanggan aktif" / "total pelanggan" / "jumlah pelanggan" -> calc_count_customers_active
- "berapa vendor aktif" / "total vendor" / "jumlah vendor" -> calc_count_vendors_active
- "berapa tagihan belum lunas" -> calc_count_bills_outstanding
- "total hutang outstanding" -> calc_sum_bills_outstanding
- "berapa invoice belum lunas" -> calc_count_invoices_outstanding
- "total piutang outstanding" -> calc_sum_invoices_outstanding
- "berapa rekening" -> calc_count_bank_accounts
- "total saldo kas dan bank" -> calc_sum_bank_balance
- "total saldo semua rekening" -> calc_sum_all_bank_balances
- "berapa pengeluaran bulan ini" -> calc_count_expenses_month
- "ranking stok" / "item stok terbanyak" -> calc_rank_items_by_stock
- "total harga beli" / "jumlah semua harga beli" -> calc_sum_harga_beli
- "ranking piutang pelanggan" / "pelanggan hutang terbesar" -> calc_rank_customers_by_ar
- "ranking hutang vendor" / "vendor hutang terbesar" -> calc_rank_vendors_by_ap
- "total penjualan bulan ini" / "penjualan outstanding" -> calc_sum_sales_this_month
- "total pembelian bulan ini" -> calc_sum_purchases_this_month
- "total pengeluaran bulan ini" -> calc_sum_expenses_this_month
- "total diterima bulan ini" / "total pembayaran masuk" -> calc_sum_received_this_month
- "total dibayar bulan ini" / "total pembayaran keluar" -> calc_sum_paid_this_month
- "berapa faktur penjualan aktif" -> calc_count_sales_invoices_active
- "berapa faktur pembelian aktif" -> calc_count_bills_active
PENTING: Kalkulasi numerik (rata-rata, total, jumlah, ranking) -> WAJIB pakai calc_* intent.

== FALLBACK ==
- Pesan ambigu/tidak jelas -> intent: "ambiguous"
"""

PIPELINE_ENABLED_INTENTS = {
    # Re-format + drill-down
    "reformat_as_table",
    "drilldown_table",
    "contextual_drill_down",
    # Tahap 1 (master data)
    "create_customer",
    "create_vendor",
    "create_warehouse",
    "create_bank_account",
    "create_item",
    # Tahap 2b (transactions)
    "create_receive_payment",
    "create_bill_payment",
    "create_expense",
    "create_sales_invoice",
    "create_bill",
    # Items queries (Tahap items)
    "query_item_detail",
    "query_item_stock_card",
    "query_item_transactions",
    "query_items_summary",
    "query_items_low_stock",
    "query_items_top_products",
    "query_items_slow_moving",
    "query_items_margins",
    "query_warehouse_stock",
    "query_categories_list",
    # Items queries v2 (wired 2026-03-09)
    "query_items_search",
    "query_items_by_stock",
    "query_items_units",
    "query_items_stats",
    "query_items_inactive",
    "query_inventory_summary",
    "query_stock_adjustments",
    "query_stock_adjustments_summary",
    "query_stock_transfers",
    "query_stock_in_transit",
    "query_warehouses",
    "query_warehouse_stock_value",
    "query_inventory_health",
    "query_item_journal",
    "query_item_activity",
    "query_item_related",
    "query_item_batches",
    # Batch/expiry
    "query_items_expired",
    "query_items_expiring_soon",
    "query_items_quarantine",
    # Customer/Vendor queries
    "query_customers_summary",
    "query_vendors_summary",
    "query_customers_list",
    "query_vendors_list",
    "query_customer_detail",
    "query_vendor_detail",
    # AR/AP queries (ARAP compliant)
    "query_ar_outstanding",
    "query_ar_invoices",
    "query_ap_outstanding",
    "query_cash_balance",
    # Kas & Bank
    "query_bank_accounts_list",
    "query_bank_account_detail",
    "query_bank_transactions",
    # Faktur Penjualan
    "query_sales_invoices_list",
    "query_sales_invoice_detail",
    "query_sales_invoices_summary",
    # Faktur Pembelian
    "query_bills_list",
    "query_bill_detail",
    "query_bills_summary",
    # Expense
    "query_expenses_list",
    "query_expense_detail",
    "query_expenses_summary",
    # Items price/sort
    "query_items_by_price",
    # Calculation intents
    "calc_avg_harga_jual",
    "calc_sum_harga_jual",
    "calc_count_items_active",
    "calc_count_items_inactive",
    "calc_rank_items_by_price",
    "calc_avg_harga_beli",
    "calc_sum_stok",
    "calc_count_customers_active",
    "calc_count_vendors_active",
    "calc_count_bills_outstanding",
    "calc_sum_bills_outstanding",
    "calc_count_invoices_outstanding",
    "calc_sum_invoices_outstanding",
    "calc_count_bank_accounts",
    "calc_sum_bank_balance",
    "calc_count_expenses_month",
    "calc_rank_items_by_stock",
    "calc_sum_harga_beli",
    "calc_rank_customers_by_ar",
    "calc_rank_vendors_by_ap",
    "calc_sum_sales_this_month",
    "calc_sum_purchases_this_month",
    "calc_sum_expenses_this_month",
    "calc_sum_received_this_month",
    "calc_sum_paid_this_month",
    "calc_count_sales_invoices_active",
    "calc_count_bills_active",
    "calc_sum_all_bank_balances",
    # Batch 1 query intents
    "query_customer_ar",
    "query_vendor_ap",
    "query_sales_invoices_overdue",
    "query_bills_overdue",
    "query_expenses_by_account",
    "query_receive_payments_list",
    "query_receive_payment_detail",
    "query_bill_payments_list",
    "query_bill_payment_detail",
    "query_journals_list",
    "query_journal_detail",
    "query_accounts_list",
    "query_account_detail",
    "query_stock_adjustment_detail",
    "query_bank_account_balance",
    # Voids
    "void_sales_invoice",
    "void_bill",
    "void_expense",
    "void_receive_payment",
    "void_bill_payment",
    "reverse_journal",
    # Updates
    "update_item",
    "update_customer",
    "update_vendor",
    "update_bank_account",
    "update_warehouse",
    # Deletes
    "delete_item",
    "delete_customer",
    "delete_vendor",
    "delete_warehouse",
    "delete_bank_account",
}


def is_pipeline_enabled(intent: str) -> bool:
    """Check if intent should use compiler pipeline or fallback to agent loop."""
    return intent in PIPELINE_ENABLED_INTENTS


# ── Code-Driven CRUD Intent Classifier ────────────────────────────────────
# Deterministic, 0ms, runs BEFORE LLM extraction.
# Returns (intent, entity_name_raw, name_field) or (None, None, None) if no CRUD match.

import re as _re

_ACTION_KEYWORDS = {
    "create": [
        "buat",
        "tambah",
        "bikin",
        "daftarkan",
        "catat",
        "input",
        "buatkan",
        "tambahkan",
        "bikinkan",
        "registrasi",
        "register",
    ],
    "update": [
        "edit",
        "ubah",
        "ganti",
        "update",
        "perbarui",
        "revisi",
        "koreksi",
        "perbaiki",
        "modifikasi",
        "rubah",
        "tukar",
    ],
    "delete": [
        "hapus",
        "delete",
        "buang",
        "hilangkan",
        "remove",
        "hapuskan",
        "singkirkan",
    ],
    "void": [
        "void",
        "batalkan",
        "batal",
        "cancel",
        "anulir",
    ],
}

_ENTITY_KEYWORDS = {
    "_item": {
        "keywords": ["barang", "item", "produk", "jasa", "product", "goods", "service"],
        "name_field": "item_name",
    },
    "_customer": {
        "keywords": ["pelanggan", "customer", "klien", "pembeli"],
        "name_field": "customer_name",
    },
    "_vendor": {
        "keywords": ["vendor", "supplier", "pemasok", "suplier"],
        "name_field": "vendor_name",
    },
    "_warehouse": {
        "keywords": ["gudang", "warehouse", "depo"],
        "name_field": "warehouse_name",
    },
    "_bank_account": {
        "keywords": ["rekening", "akun bank", "bank account", "kas"],
        "name_field": "bank_name",
    },
    "_sales_invoice": {
        "keywords": ["faktur penjualan", "sales invoice", "invoice penjualan"],
        "name_field": "invoice_number",
    },
    "_bill": {
        "keywords": ["faktur pembelian", "tagihan", "bill", "purchase invoice"],
        "name_field": "bill_number",
    },
    "_expense": {
        "keywords": ["biaya", "expense", "pengeluaran"],
        "name_field": "description",
    },
    "_journal_entry": {
        "keywords": ["jurnal", "journal", "journal entry"],
        "name_field": "description",
    },
    "_receive_payment": {
        "keywords": ["terima pembayaran", "receive payment", "penerimaan pembayaran"],
        "name_field": "description",
    },
    "_bill_payment": {
        "keywords": [
            "bayar tagihan",
            "pembayaran tagihan",
            "bill payment",
            "bayar faktur",
        ],
        "name_field": "description",
    },
}


def classify_query_intent(user_text: str) -> tuple:
    """Code-driven query intent classifier. 0ms, deterministic."""
    import re as _qre

    t = user_text.strip().lower()

    # ── Calc engine intents (Batch 1 expansion) ──
    if _qre.search(
        r"(?:ranking|peringkat).*(?:pelanggan|customer).*(?:piutang|ar)", t
    ) or _qre.search(
        r"(?:ranking|peringkat).*(?:piutang|ar).*(?:pelanggan|customer)", t
    ):
        return "calc_rank_customers_by_ar", None, None
    if _qre.search(
        r"(?:ranking|peringkat).*(?:vendor|pemasok).*(?:hutang|utang|ap)", t
    ) or _qre.search(
        r"(?:ranking|peringkat).*(?:hutang|utang|ap).*(?:vendor|pemasok)", t
    ):
        return "calc_rank_vendors_by_ap", None, None
    if _qre.search(r"(?:total|jumlah).*(?:penjualan|sales).*(?:bulan\s*ini)", t):
        return "calc_sum_sales_this_month", None, None
    if _qre.search(r"(?:total|jumlah).*(?:pembelian|purchase).*(?:bulan\s*ini)", t):
        return "calc_sum_purchases_this_month", None, None
    if _qre.search(
        r"(?:total|jumlah).*(?:pengeluaran|biaya|expense).*(?:bulan\s*ini)", t
    ):
        return "calc_sum_expenses_this_month", None, None
    if _qre.search(
        r"(?:total|jumlah).*(?:saldo|balance).*(?:semua|seluruh).*(?:rekening|bank)", t
    ) or _qre.search(r"(?:total|jumlah).*(?:semua|seluruh).*(?:saldo|balance)", t):
        return "calc_sum_all_bank_balances", None, None

    # ── Drill-down / breakdown signals (checked BEFORE AP/AR summary) ──
    # These override AP/AR summary when user wants list/table/detail, not total
    _is_drilldown = (
        bool(
            _qre.search(
                r"(?:rekapan|rekap|breakdown|rincian|detail)\s+(?:per|tiap|semua|hutang|piutang|faktur|tagihan)",
                t,
            )
        )
        or bool(
            _qre.search(
                r"(?:rekapan|rekap|daftar)\s+(?:hutang|piutang|tagihan|faktur).*(?:tabel|table)",
                t,
            )
        )
        or bool(
            _qre.search(
                r"(?:hutang|piutang|tagihan|faktur).*(?:per\s+(?:faktur|vendor|pelanggan|customer))",
                t,
            )
        )
        or bool(
            _qre.search(
                r"(?:yang|mana|apa)\s+(?:belum\s+(?:lunas|dibayar|bayar)|jatuh\s+tempo|overdue|paling\s+dekat)",
                t,
            )
        )
        or bool(
            _qre.search(
                r"(?:belum\s+lunas|belum\s+dibayar)\s+(?:apa|mana|yang)",
                t,
            )
        )
    )

    # If drill-down signal detected AND has AP/AR keyword → route to drilldown
    _has_ap = bool(_qre.search(r"\b(utang|hutang|payable|tagihan|bill|pembelian)\b", t))
    _has_ar = bool(_qre.search(r"\b(piutang|receivable|invoice|penjualan)\b", t))

    # Also catch: "rekapan semua faktur yang belum lunas" (no explicit AP/AR keyword)
    if not _is_drilldown and _qre.search(
        r"(?:rekapan|rekap|daftar|tampilkan)\s+(?:semua\s+)?(?:faktur|tagihan|invoice|bill).*(?:belum\s+lunas|belum\s+dibayar|unpaid|overdue|jatuh\s+tempo)",
        t,
    ):
        _is_drilldown = True

    if _is_drilldown and (_has_ap or _has_ar):
        return "drilldown_table", None, None
    # Drilldown even without AP/AR keyword if "faktur/tagihan" + filter keyword present
    _has_filter = bool(
        _qre.search(
            r"(?:belum\s+lunas|belum\s+dibayar|jatuh\s+tempo|overdue|unpaid|per\s+(?:faktur|vendor|pelanggan))",
            t,
        )
    )
    if (
        _is_drilldown
        and _has_filter
        and _qre.search(r"(?:faktur|tagihan|invoice|bill)", t)
    ):
        return "drilldown_table", None, None

    # Contextual follow-up patterns (no AP/AR keyword needed — uses session state)
    if _qre.search(
        r"(?:yang|mana)\s+(?:paling\s+dekat|paling\s+besar|paling\s+lama)\s+(?:jatuh\s+tempo|hutang|piutang)",
        t,
    ):
        return "drilldown_table", None, None
    if _qre.search(
        r"(?:belum\s+lunas|belum\s+dibayar|belum\s+bayar)\b.*(?:apa\s+aja|yang\s+mana|berapa)",
        t,
    ):
        return "drilldown_table", None, None

    # ── Batch 1: Entity-specific queries (Priority 2) ──
    # Customer AR with entity name
    if _qre.search(
        r"(?:piutang|receivable)\s+(?!saya|kita|semua|total|berapa|yang|apa)\w+", t
    ):
        return "query_customer_ar", None, None
    # Vendor AP with entity name
    if _qre.search(
        r"(?:hutang|utang|payable)\s+(?:ke\s+|dari\s+)?(?!saya|kita|semua|total|berapa|yang|apa)\w+",
        t,
    ):
        if _qre.search(r"(?:vendor|supplier|pemasok|toko|cv|pt)\b", t):
            return "query_vendor_ap", None, None
    # Bank balance with specific bank name
    if _qre.search(
        r"saldo\s+(?!saya|kita|semua|total|berapa|yang|apa|kas|bank\b)\w+", t
    ):
        return "query_bank_account_balance", None, None

    # ── Batch 1: Overdue / specific list queries ──
    # Sales invoices overdue
    if _qre.search(r"faktur.*jatuh\s*tempo|overdue.*(?:penjualan|invoice)", t):
        return "query_sales_invoices_overdue", None, None
    # Bills overdue
    if _qre.search(r"tagihan.*jatuh\s*tempo|overdue.*(?:pembelian|bill)", t):
        return "query_bills_overdue", None, None
    # Expenses by account
    if _qre.search(r"(?:pengeluaran|biaya).*(?:untuk|akun)", t):
        return "query_expenses_by_account", None, None
    # Receive payments list
    if _qre.search(
        r"(?:daftar|list).*(?:penerimaan|pembayaran\s*masuk|receive\s*payment)", t
    ):
        return "query_receive_payments_list", None, None
    # Bill payments list
    if _qre.search(
        r"(?:daftar|list).*(?:pembayaran\s*keluar|payment\s*out|bill\s*payment|pembayaran\s*tagihan)",
        t,
    ):
        return "query_bill_payments_list", None, None
    # Journals list
    if _qre.search(r"(?:daftar|list|semua)\s+jurnal", t):
        return "query_journals_list", None, None
    # Accounts list
    if _qre.search(r"(?:daftar|list).*(?:akun|coa|chart\s*of\s*accounts)", t):
        return "query_accounts_list", None, None

    # AR
    if _qre.search(r"\b(piutang|receivable|ar outstanding)\b", t) or _qre.search(
        r"\bpiutang\b.*\b(kejar|tagih|prioritas)\b", t
    ):
        if _qre.search(
            r"\b(siapa|daftar|list|detail|pelanggan|faktur|invoice|nomor)\b", t
        ):
            return "query_ar_invoices", None, None
        return "query_ar_outstanding", None, None
    if _qre.search(r"\bsiapa\b.*\b(piutang|hutang|utang)\b", t):
        if "piutang" in t:
            return "query_ar_invoices", None, None
        return "query_ap_outstanding", None, None

    # AP
    if _qre.search(r"\b(utang|hutang|payable|ap outstanding)\b", t):
        return "query_ap_outstanding", None, None

    # Cash runway / uang aman
    if _qre.search(r"\b(uang|kas|cash)\b.*\b(aman|cukup|habis|bertahan)\b", t):
        return "query_cash_balance", None, None

    # Saldo
    if _qre.search(r"\b(saldo|balance)\b", t) and not _qre.search(
        r"\b(pelanggan|customer|vendor)\b", t
    ):
        return "query_cash_balance", None, None

    # Bank/Kas list
    if _qre.search(r"\b(daftar|list|semua)\s+(rekening|bank|kas)\b", t):
        return "query_bank_accounts_list", None, None

    # Sales invoices
    if _qre.search(r"\b(daftar|list|semua)\s+(faktur\s+penjualan|invoice)\b", t):
        return "query_sales_invoices_list", None, None
    if _qre.search(r"\b(ringkasan|summary|rekap|total)\s+(penjualan|sales)\b", t):
        return "query_sales_invoices_summary", None, None

    # Bills
    if _qre.search(r"\b(daftar|list|semua)\s+(faktur\s+pembelian|tagihan|bill)\b", t):
        return "query_bills_list", None, None
    if _qre.search(r"\b(ringkasan|summary|rekap|total)\s+(pembelian|bill)\b", t):
        return "query_bills_summary", None, None

    # Expenses
    if _qre.search(r"\b(daftar|list|semua)\s+(pengeluaran|biaya|expense)\b", t):
        return "query_expenses_list", None, None
    if _qre.search(
        r"\b(ringkasan|summary|rekap|total)\s+(pengeluaran|biaya|expense)\b", t
    ):
        return "query_expenses_summary", None, None

    # Low stock / inactive / categories
    if _qre.search(
        r"\b(stok rendah|hampir habis|low stock|stok.*habis|mau habis)\b", t
    ):
        return "query_items_low_stock", None, None
    if _qre.search(r"\b(barang|item|produk)\b.*\b(tidak aktif|nonaktif|inactive)\b", t):
        return "query_items_inactive", None, None
    if _qre.search(r"\b(daftar|list|semua)\s+(kategori)\b", t):
        return "query_categories_list", None, None

    # Account (CoA) detail — "detail akun kas", "info akun beban gaji"
    if _qre.search(
        r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:akun|account|coa)",
        t,
    ):
        return "query_account_detail", None, None

    # Customer/Vendor detail — "data pelanggan X", "detail vendor Y", "info customer Z"
    if _qre.search(
        r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:pelanggan|customer)",
        t,
    ):
        return "query_customer_detail", None, None
    if _qre.search(
        r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:vendor|pemasok|supplier)",
        t,
    ):
        return "query_vendor_detail", None, None

    # Contextual drill-down — "per faktur", "breakdown", "detailnya" after a summary query
    # Returns drilldown_table — orchestrator resolves to correct pipeline query using session state
    if _qre.search(
        r"(?:rekapan|rekap|breakdown|rincian|detail)\s+(?:per|tiap|masing-masing)\s+(?:faktur|tagihan|invoice|bill|pelanggan|customer|vendor|pemasok)",
        t,
    ):
        return "drilldown_table", None, None
    if _qre.search(
        r"(?:per|tiap)\s+(?:faktur|tagihan|invoice|bill).*(?:tabel|table)", t
    ):
        return "drilldown_table", None, None
    if _qre.search(
        r"(?:bikin|buat|tampilkan|tunjukkan)\s+(?:rekapan|rekap|breakdown|rincian)\s+(?:per|tiap)",
        t,
    ):
        return "drilldown_table", None, None
    if _qre.search(r"(?:detailnya|rinciannya|breakdownnya)", t):
        return "drilldown_table", None, None

    # ── contextual_drill_down — "per faktur", "breakdown", "rinci" after a previous query ──
    # Primary triggers: always match
    if _qre.search(
        r"(?:per\s+faktur|per\s+vendor|per\s+pelanggan|breakdown|\brinci\b|\brincian\b)",
        t,
    ):
        return "contextual_drill_down", None, None

    # Secondary trigger: "detail*" — match ONLY when NO entity identifier, NO temporal keyword, AND NO entity type word
    if _qre.search(r"\bdetail(?:nya|kan|in)?\b", t):
        _has_entity_id = bool(
            _qre.search(
                r"(?:INV-|PB-|EXP-|JE-|[0-9a-f]{8}-[0-9a-f]{4})", t, _qre.IGNORECASE
            )
        )
        _has_temporal = bool(
            _qre.search(
                r"(?:bulan\s+ini|kemarin|minggu|tahun\s+ini|hari\s+ini|periode|\bQ[1-4]\b)",
                t,
                _qre.IGNORECASE,
            )
        )
        _ENTITY_TYPE_WORDS = {
            "akun",
            "account",
            "coa",
            "jurnal",
            "journal",
            "stok",
            "stock",
            "penyesuaian",
            "penerimaan",
            "pembayaran",
            "payment",
            "receive",
            "faktur",
            "invoice",
            "bill",
            "tagihan",
            "vendor",
            "pelanggan",
            "customer",
            "barang",
            "item",
            "bank",
            "rekening",
            "biaya",
            "expense",
        }
        _msg_words = set(t.split())
        _has_entity_type = bool(_msg_words & _ENTITY_TYPE_WORDS)
        if not _has_entity_id and not _has_temporal and not _has_entity_type:
            return "contextual_drill_down", None, None

    # Re-format requests — user wants last response as table
    if _qre.search(
        r"(?:tampilkan|tunjukkan|bikin|buat|format|ubah)\s+(?:dalam|ke|jadi|sebagai)\s+(?:bentuk\s+)?(?:tabel|table)",
        t,
    ):
        return "reformat_as_table", None, None
    if _qre.search(
        r"(?:rekapan|rekap)\s+(?:dalam|ke)?\s*(?:bentuk\s+)?(?:tabel|table)", t
    ):
        return "reformat_as_table", None, None
    if _qre.search(
        r"(?:tolong|bisa|mau)\s+(?:di)?(?:bikin|buat|format)(?:kan)?\s+(?:tabel|table)",
        t,
    ):
        return "reformat_as_table", None, None

    return None, None, None


def classify_crud_intent(user_text: str) -> tuple:
    """
    Classify CRUD intent from user text using keyword matching.
    Returns (intent, entity_name_raw, name_field) or (None, None, None).
    """
    text = user_text.strip()
    text_lower = text.lower()

    # Step 1: Detect action
    action = None
    action_end_pos = 0

    for act, keywords in _ACTION_KEYWORDS.items():
        for kw in keywords:
            patterns = [
                rf"^{_re.escape(kw)}\b",
                rf"^(?:tolong|mohon|bisa|boleh|bantu|coba|mau|minta|gas|oke|ok|yuk|dong|sip|siap|ayo|cus|langsung|baik|ya|iya|please)\s+{_re.escape(kw)}\b",
                rf"^(?:tolong|mohon|bisa|boleh|minta)\s+(?:bantu\s+)?{_re.escape(kw)}\b",
                rf"^(?:bantu)\s+{_re.escape(kw)}\b",
            ]
            for pattern in patterns:
                m = _re.search(pattern, text_lower)
                if m:
                    action = act
                    action_end_pos = m.end()
                    break
            if action:
                break
        if action:
            break

    if not action:
        return None, None, None

    # Step 2: Detect entity type
    remaining = text_lower[action_end_pos:].strip()
    search_text = text_lower

    entity_suffix = None
    entity_config = None
    entity_end_pos = 0

    sorted_entities = []
    for suffix, config in _ENTITY_KEYWORDS.items():
        for kw in config["keywords"]:
            sorted_entities.append((len(kw), kw, suffix, config))
    sorted_entities.sort(key=lambda x: -x[0])

    for _, kw, suffix, config in sorted_entities:
        idx = remaining.find(kw)
        if idx != -1:
            entity_suffix = suffix
            entity_config = config
            entity_end_pos = idx + len(kw)
            break
        idx_full = search_text.find(kw)
        if idx_full != -1 and idx_full >= action_end_pos - 2:
            entity_suffix = suffix
            entity_config = config
            entity_end_pos = (idx_full + len(kw)) - action_end_pos
            if entity_end_pos < 0:
                entity_end_pos = len(remaining)
            break

    if not entity_suffix:
        return None, None, None

    # Step 3: Build intent
    if action == "void" and entity_suffix not in (
        "_sales_invoice",
        "_bill",
        "_expense",
        "_receive_payment",
        "_bill_payment",
    ):
        action = "delete"

    intent = f"{action}{entity_suffix}"

    # Step 4: Extract entity name
    remaining_after_entity = remaining[entity_end_pos:].strip()
    remaining_after_entity = _re.sub(
        r"^(?:yang\s+bernama|dengan\s+nama|bernama|nama|namanya|yang|dengan|lama|baru|dong|ya|nih)\s+",
        "",
        remaining_after_entity,
        flags=_re.IGNORECASE,
    ).strip()
    remaining_after_entity = remaining_after_entity.rstrip("?!.,;")
    # Strip trailing filler words (dong, ya, nih, deh, baru)
    remaining_after_entity = _re.sub(
        r"\s+(?:dong|ya|nih|deh|baru|lah|sih)$",
        "",
        remaining_after_entity,
        flags=_re.IGNORECASE,
    ).strip()
    # If entire remaining is just a filler word, clear it
    if remaining_after_entity.lower() in (
        "dong",
        "ya",
        "nih",
        "deh",
        "baru",
        "lah",
        "sih",
        "lama",
        "aja",
        "saja",
    ):
        remaining_after_entity = ""
    remaining_after_entity = remaining_after_entity.strip("\"'\u201c\u201d\u2018\u2019")

    # Truncate at comma or field-indicator words for update commands
    # "PT Bahagia Sejahtera, ubah telepon jadi 081234567890" → "PT Bahagia Sejahtera"
    if action == "update" and remaining_after_entity:
        if "," in remaining_after_entity:
            remaining_after_entity = remaining_after_entity.split(",")[0].strip()
        _fi = _re.split(
            r"\s+(?:ubah|ganti|update|set|jadikan|jadi|menjadi|ke|telepon|telp|hp|email|alamat|harga|stok)\s+",
            remaining_after_entity,
            maxsplit=1,
            flags=_re.IGNORECASE,
        )
        if len(_fi) > 1:
            remaining_after_entity = _fi[0].strip()

    entity_name = remaining_after_entity if remaining_after_entity else None
    name_field = entity_config["name_field"]

    logger.warning(
        "[INTENT_CLASSIFIER] text='%s' -> action=%s entity=%s intent=%s name='%s'",
        text[:60],
        action,
        entity_suffix,
        intent,
        entity_name or "",
    )

    return intent, entity_name, name_field


class EntityExtractor:
    """Extract intent + entities from user message using constrained JSON output."""

    def __init__(self, llm_client, default_model: str = "gpt-4o-mini-2024-07-18"):
        self.llm_client = llm_client
        self.default_model = default_model

    async def extract(
        self,
        user_text: str,
        context_summary: str = "",
        context_hint: str = "",
        model: str = None,
    ) -> ExtractionResult:
        from ..llm import LLMMessage

        system_content = EXTRACTION_SYSTEM_PROMPT
        if context_summary:
            system_content += f"\n\nKONTEKS SESI AKTIF:\n{context_summary}"

        _extraction_text = user_text
        if context_hint:
            _extraction_text = f"{user_text}\n\n[{context_hint}]"

        messages = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=_extraction_text),
        ]

        try:
            response = await self.llm_client.chat(
                messages=messages,
                tools=[],
                model=model or self.default_model,
                temperature=0.1,
                max_tokens=500,
                response_format=EXTRACTION_SCHEMAS["general"],
            )

            raw_text = response.content or ""
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            entities = {
                k: v for k, v in parsed.get("entities", {}).items() if v is not None
            }

            result = ExtractionResult(
                intent=parsed.get("intent", "ambiguous"),
                entities=entities,
                modifiers=parsed.get("modifiers", []),
                confidence=parsed.get("confidence", 0.5),
                raw_response=parsed,
            )

            if result.confidence < 0.6 or result.intent == "ambiguous":
                result.needs_escalation = True

            logger.warning(
                "[EXTRACT] intent=%s confidence=%.2f entities=%s modifiers=%s escalation=%s",
                result.intent,
                result.confidence,
                list(result.entities.keys()),
                result.modifiers,
                result.needs_escalation,
            )

            return result

        except json.JSONDecodeError as e:
            logger.warning(
                "[EXTRACT] JSON parse failed: %s, raw: %s", e, raw_text[:200]
            )
            return ExtractionResult(
                intent="ambiguous",
                confidence=0.0,
                needs_escalation=True,
            )
        except Exception as e:
            logger.error("[EXTRACT] Extraction failed: %s", e)
            return ExtractionResult(
                intent="ambiguous",
                confidence=0.0,
                needs_escalation=True,
            )


# ── Registry-Driven Schema Builder (Stage 2) ─────────────────────────────
# Builds extraction schema dynamically from DirectActionConfig.fields.
# Zero manual sync — field baru di registry auto-extractable.


def build_intent_schema(intent: str):
    """Build mini JSON schema for Stage 2 extraction from registry FieldSpecs."""
    from .direct_action_registry import get_direct_action

    config = get_direct_action(intent)
    if not config or not config.fields:
        return None

    properties = {}
    required = []

    for f in config.fields:
        if f.hidden and not f.required:
            continue
        if f.display_only:
            continue

        if f.field_type in ("number", "percent"):
            json_type = ["number", "null"]
        elif f.field_type == "boolean":
            json_type = ["boolean", "null"]
        else:
            json_type = ["string", "null"]

        prop = {"type": json_type}
        desc_parts = [f.label]
        if f.description:
            desc_parts.append(f.description)
        if f.options:
            desc_parts.append("Pilihan: " + ", ".join(f.options))
        prop["description"] = " — ".join(desc_parts)

        if f.field_type == "enum" and f.options:
            prop["enum"] = f.options + [None]

        properties[f.name] = prop
        required.append(f.name)

    if not properties:
        return None

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "extract_" + intent,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def build_intent_prompt(intent: str, collected: dict) -> str:
    """Build Stage 2 system prompt for intent-specific field extraction."""
    from .direct_action_registry import get_direct_action

    config = get_direct_action(intent)
    if not config:
        return ""

    parts = [
        "Kamu sedang membantu user " + config.display_name.lower() + ".",
        "Extract field yang disebutkan user dari pesan mereka.",
        "RULES:",
        "- HANYA extract apa yang user EKSPLISIT sebutkan.",
        "- Field yang tidak disebut = null.",
        '- Angka: "5 juta" = 5000000, "500 ribu" = 500000, "15rb" = 15000.',
    ]

    if collected:
        collected_clean = {
            k: v for k, v in collected.items() if v is not None and k != "date"
        }
        if collected_clean:
            items_str = ", ".join(
                str(k) + "=" + str(v) for k, v in list(collected_clean.items())[:8]
            )
            parts.append("")
            parts.append("Sudah terkumpul: " + items_str)
            parts.append(
                "JANGAN override data yang sudah ada kecuali user eksplisit ganti."
            )

    parts.append("")
    parts.append("Fields untuk " + config.display_name + ":")
    for f in config.fields:
        if f.hidden and not f.required:
            continue
        if f.display_only:
            continue
        req = " (WAJIB)" if f.required else ""
        desc = " — " + f.description if f.description else ""
        opts = " [" + ", ".join(f.options) + "]" if f.options else ""
        parts.append("  - " + f.name + ": " + f.label + req + opts + desc)

    return "\n".join(parts)


class FieldExtractor:
    """Stage 2: Extract intent-specific fields using registry-driven schema."""

    def __init__(self, llm_client, default_model: str = "gpt-4o-mini-2024-07-18"):
        self.llm_client = llm_client
        self.default_model = default_model

    async def extract_fields(
        self,
        user_text: str,
        intent: str,
        collected: dict,
        model: str = None,
    ) -> dict:
        """Extract intent-specific fields. Schema auto-built from registry."""
        from ..llm import LLMMessage

        schema = build_intent_schema(intent)
        if not schema:
            logger.warning("[EXTRACT_S2] No schema for intent=%s", intent)
            return {}

        system_content = build_intent_prompt(intent, collected)
        if not system_content:
            return {}

        try:
            response = await self.llm_client.chat(
                messages=[
                    LLMMessage(role="system", content=system_content),
                    LLMMessage(role="user", content=user_text),
                ],
                tools=[],
                model=model or self.default_model,
                temperature=0.1,
                max_tokens=300,
                response_format=schema,
            )

            raw_text = (response.content or "").strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

            import json as _json

            parsed = _json.loads(raw_text)
            extracted = {k: v for k, v in parsed.items() if v is not None}

            logger.warning(
                "[EXTRACT_S2] intent=%s extracted=%s from='%s'",
                intent,
                list(extracted.keys()),
                user_text[:80],
            )
            return extracted

        except Exception as e:
            logger.warning("[EXTRACT_S2] Failed: %s", e)
            return {}
