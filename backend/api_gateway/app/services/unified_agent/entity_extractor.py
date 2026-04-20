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
    "calc_rank_expense_accounts": "Topik sebelumnya: ranking pengeluaran per akun",
    "calc_count_customers_inactive": "Topik sebelumnya: jumlah pelanggan tidak aktif",
    "calc_count_vendors_inactive": "Topik sebelumnya: jumlah vendor tidak aktif",
    "calc_count_expenses_this_month": "Topik sebelumnya: jumlah pengeluaran bulan ini",
    # Batch 3: Credit Notes
    "query_credit_notes_list": "Topik sebelumnya: daftar nota kredit",
    "query_credit_note_detail": "Topik sebelumnya: detail nota kredit",
    "query_credit_notes_summary": "Topik sebelumnya: ringkasan nota kredit",
    # Batch 3: Vendor Credits
    "query_vendor_credits_list": "Topik sebelumnya: daftar vendor credit",
    "query_vendor_credit_detail": "Topik sebelumnya: detail vendor credit",
    "query_vendor_credits_summary": "Topik sebelumnya: ringkasan vendor credit",
    # Batch 3: Quotes
    "query_quotes_list": "Topik sebelumnya: daftar penawaran",
    "query_quote_detail": "Topik sebelumnya: detail penawaran",
    "query_quotes_summary": "Topik sebelumnya: ringkasan penawaran",
    # Batch 3: Bank Transfers
    "query_bank_transfers_list": "Topik sebelumnya: daftar transfer bank",
    # Batch 3: Customer Deposits
    "query_customer_deposits_list": "Topik sebelumnya: daftar deposit pelanggan",
    # Batch 3: Vendor Deposits
    "query_vendor_deposits_list": "Topik sebelumnya: daftar deposit vendor",
    "calc_avg_harga_beli": "Topik sebelumnya: rata-rata harga beli",
    "calc_avg_harga_jual": "Topik sebelumnya: rata-rata harga jual",
    "calc_count_bank_accounts": "Topik sebelumnya: jumlah rekening bank",
    "calc_count_bills_active": "Topik sebelumnya: jumlah tagihan aktif",
    "calc_count_bills_outstanding": "Topik sebelumnya: jumlah tagihan belum lunas",
    "calc_count_customers_active": "Topik sebelumnya: jumlah pelanggan aktif",
    "calc_count_expenses_month": "Topik sebelumnya: jumlah pengeluaran bulan ini",
    "calc_count_invoices_outstanding": "Topik sebelumnya: jumlah faktur belum lunas",
    "calc_count_items_active": "Topik sebelumnya: jumlah barang aktif",
    "calc_count_items_inactive": "Topik sebelumnya: jumlah barang tidak aktif",
    "calc_count_sales_invoices_active": "Topik sebelumnya: jumlah faktur penjualan aktif",
    "calc_count_vendors_active": "Topik sebelumnya: jumlah vendor aktif",
    "calc_profit_margin_per_item": "Topik sebelumnya: margin keuntungan per barang",
    "calc_rank_items_by_price": "Topik sebelumnya: ranking barang berdasarkan harga",
    "calc_rank_items_by_stock": "Topik sebelumnya: ranking barang berdasarkan stok",
    "calc_sum_all_bank_balances": "Topik sebelumnya: total saldo semua bank",
    "calc_sum_bank_balance": "Topik sebelumnya: saldo bank tertentu",
    "calc_sum_bills_outstanding": "Topik sebelumnya: total tagihan belum lunas",
    "calc_sum_harga_beli": "Topik sebelumnya: total harga beli",
    "calc_sum_harga_jual": "Topik sebelumnya: total harga jual",
    "calc_sum_invoices_outstanding": "Topik sebelumnya: total faktur belum lunas",
    "calc_sum_paid_this_month": "Topik sebelumnya: total pembayaran bulan ini",
    "calc_sum_received_this_month": "Topik sebelumnya: total penerimaan bulan ini",
    "calc_sum_stok": "Topik sebelumnya: total stok",
    "calc_top_selling_items": "Topik sebelumnya: barang terlaris",
    "query_account_detail": "Topik sebelumnya: detail akun",
    "query_account_ledger": "Topik sebelumnya: buku besar akun",
    "query_ap_aging": "Topik sebelumnya: aging hutang",
    "query_ar_aging": "Topik sebelumnya: aging piutang",
    "query_balance_sheet": "Topik sebelumnya: neraca / balance sheet",
    "query_bank_account_detail": "Topik sebelumnya: detail rekening bank",
    "query_bank_transactions_by_date": "Topik sebelumnya: transaksi bank per tanggal",
    "query_bill_payment_detail": "Topik sebelumnya: detail pembayaran tagihan",
    "query_bills_by_vendor": "Topik sebelumnya: tagihan per vendor",
    "query_bills_unpaid": "Topik sebelumnya: tagihan belum lunas",
    "query_cash_flow": "Topik sebelumnya: arus kas",
    "query_categories_list": "Topik sebelumnya: daftar kategori",
    "query_customers_with_overdue": "Topik sebelumnya: pelanggan dengan overdue",
    "query_dashboard_summary": "Topik sebelumnya: ringkasan dashboard",
    "query_expense_detail": "Topik sebelumnya: detail pengeluaran",
    "query_expenses_by_date_range": "Topik sebelumnya: pengeluaran per periode",
    "query_inventory_health": "Topik sebelumnya: kesehatan inventori",
    "query_inventory_summary": "Topik sebelumnya: ringkasan inventori",
    "query_item_activity": "Topik sebelumnya: aktivitas barang",
    "query_item_batches": "Topik sebelumnya: batch barang",
    "query_item_journal": "Topik sebelumnya: jurnal barang",
    "query_item_related": "Topik sebelumnya: barang terkait",
    "query_item_stock_card": "Topik sebelumnya: kartu stok barang",
    "query_item_transactions": "Topik sebelumnya: transaksi barang",
    "query_item_sales_summary": "Topik sebelumnya: omzet penjualan barang",
    "query_items_by_price": "Topik sebelumnya: barang berdasarkan harga",
    "query_items_by_stock": "Topik sebelumnya: barang berdasarkan stok",
    "query_items_expired": "Topik sebelumnya: barang kadaluarsa",
    "query_items_expiring_soon": "Topik sebelumnya: barang segera kadaluarsa",
    "query_items_inactive": "Topik sebelumnya: barang tidak aktif",
    "query_items_margins": "Topik sebelumnya: margin barang",
    "query_items_no_stock": "Topik sebelumnya: barang tanpa stok",
    "query_items_quarantine": "Topik sebelumnya: barang karantina",
    "query_items_search": "Topik sebelumnya: pencarian barang",
    "query_items_slow_moving": "Topik sebelumnya: barang slow moving",
    "query_items_stats": "Topik sebelumnya: statistik barang",
    "query_items_top_products": "Topik sebelumnya: produk teratas",
    "query_items_units": "Topik sebelumnya: satuan barang",
    "query_journal_detail": "Topik sebelumnya: detail jurnal",
    "query_overdue_all": "Topik sebelumnya: semua yang jatuh tempo",
    "query_profit_loss": "Topik sebelumnya: laba rugi",
    "query_receive_payment_detail": "Topik sebelumnya: detail penerimaan pembayaran",
    "query_recurring_bills_list": "Topik sebelumnya: daftar tagihan berulang",
    "query_sales_invoices_unpaid": "Topik sebelumnya: faktur penjualan belum lunas",
    "query_stock_adjustment_detail": "Topik sebelumnya: detail penyesuaian stok",
    "query_stock_adjustments": "Topik sebelumnya: daftar penyesuaian stok",
    "query_stock_adjustments_summary": "Topik sebelumnya: ringkasan penyesuaian stok",
    "query_stock_in_transit": "Topik sebelumnya: stok dalam perjalanan",
    "query_stock_transfers": "Topik sebelumnya: daftar transfer stok",
    "query_trial_balance": "Topik sebelumnya: neraca saldo",
    "query_vendors_with_overdue": "Topik sebelumnya: vendor dengan overdue",
    "query_warehouse_stock": "Topik sebelumnya: stok gudang",
    "query_warehouse_stock_value": "Topik sebelumnya: nilai stok gudang",
    "query_warehouses": "Topik sebelumnya: daftar gudang",
    # Manufacturing
    "query_bom_list": "Topik sebelumnya: daftar BOM (Bill of Materials)",
    "query_bom_detail": "Topik sebelumnya: detail BOM",
    "query_bom_cost_breakdown": "Topik sebelumnya: biaya BOM",
    "query_bom_materials_required": "Topik sebelumnya: kebutuhan material BOM",
    "query_work_order_list": "Topik sebelumnya: daftar work order (perintah produksi)",
    "query_work_order_detail": "Topik sebelumnya: detail work order",
    "query_work_order_cost_analysis": "Topik sebelumnya: analisis biaya produksi",
    "query_production_active": "Topik sebelumnya: work order aktif",
    "query_production_schedule": "Topik sebelumnya: jadwal produksi",
    "query_material_issues": "Topik sebelumnya: material issue (bahan keluar)",
    "query_fg_receipts": "Topik sebelumnya: FG receipt (barang jadi masuk)",
    "query_work_center_list": "Topik sebelumnya: daftar work center",
    "create_work_order": "Topik sebelumnya: buat work order",
    "create_bom": "Topik sebelumnya: buat BOM",
    "create_work_center": "Topik sebelumnya: buat work center",
    "issue_materials": "Topik sebelumnya: issue material",
    "report_production_output": "Topik sebelumnya: report output produksi",
    "calc_count_work_orders_active": "Topik sebelumnya: jumlah work order aktif",
    "calc_count_bom_active": "Topik sebelumnya: jumlah BOM aktif",
    "calc_count_work_orders_draft": "Topik sebelumnya: jumlah work order draft",
    "calc_count_work_centers": "Topik sebelumnya: jumlah work center",
    "calc_rank_work_orders_by_quantity": "Topik sebelumnya: ranking work order berdasarkan jumlah",
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
                            "create_sales_order",
                            "update_sales_invoice",
                            "update_sales_order",
                            "void_sales_order",
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
    "query_item_sales_summary",
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
                            "calc_rank_expense_accounts",
                            "calc_count_customers_inactive",
                            "calc_count_vendors_inactive",
                            "calc_count_expenses_this_month",
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
                            # Batch 2 calc intents
                            "calc_rank_expense_accounts",
                            "calc_count_customers_inactive",
                            "calc_count_vendors_inactive",
                            "calc_count_expenses_this_month",
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
                            "query_items_no_stock",
                            "query_customers_with_overdue",
                            "query_vendors_with_overdue",
                            "query_sales_invoices_unpaid",
                            "query_bills_by_vendor",
                            "query_bills_unpaid",
                            "query_expenses_by_date_range",
                            "query_account_ledger",
                            "query_ar_aging",
                            "query_ap_aging",
                            "query_dashboard_summary",
                            "query_overdue_all",
                            "query_recurring_bills_list",
                            "query_bank_transactions_by_date",
                            # Batch 3 report + cross-module calc intents
                            "query_profit_loss",
                            "query_balance_sheet",
                            "query_cash_flow",
                            "query_trial_balance",
                            "calc_profit_margin_per_item",
                            "calc_top_selling_items",
                            # Batch 3
                            "create_credit_note",
                            "void_credit_note",
                            "create_vendor_credit",
                            "void_vendor_credit",
                            "create_quote",
                            "create_bank_transfer",
                            "void_bank_transfer",
                            "create_customer_deposit",
                            "void_customer_deposit",
                            "create_vendor_deposit",
                            "void_vendor_deposit",
                            "query_credit_notes_list",
                            "query_credit_note_detail",
                            "query_credit_notes_summary",
                            "query_vendor_credits_list",
                            "query_vendor_credit_detail",
                            "query_vendor_credits_summary",
                            "query_quotes_list",
                            "query_quote_detail",
                            "query_quotes_summary",
                            "query_bank_transfers_list",
                            "query_customer_deposits_list",
                            "query_vendor_deposits_list",
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
                            "account_name": {
                                "type": ["string", "null"],
                                "description": "Nama akun biaya/beban (e.g. Beban Pemeliharaan, Beban Listrik, Biaya Admin)",
                            },
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
- "transaksi barang X" / "riwayat transaksi X" -> query_item_transactions
- "omzet penjualan X" / "total terjual X" / "penjualan X berapa" / "sudah terjual berapa" / "revenue X" -> query_item_sales_summary
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
- "ranking pengeluaran per akun" / "peringkat biaya akun" -> calc_rank_expense_accounts
- "berapa pelanggan tidak aktif" -> calc_count_customers_inactive
- "berapa vendor tidak aktif" -> calc_count_vendors_inactive
- "berapa pengeluaran bulan ini" / "jumlah biaya bulan ini" -> calc_count_expenses_this_month
PENTING: Kalkulasi numerik (rata-rata, total, jumlah, ranking) -> WAJIB pakai calc_* intent.

== CONTEXT AWARENESS ==
Jika ada konteks "[Topik sebelumnya: ...]" di akhir pesan, gunakan itu sebagai referensi.
- "piutang" + "dari siapa?" -> query_ar_invoices
- "hutang" + "yang paling besar?" -> query_ap_outstanding
- "saldo bank" + "yang paling banyak?" -> query_bank_accounts_list
- "pelanggan" + "data lengkapnya?" -> query_customer_detail
Jika user mengganti topik secara eksplisit, IKUTI user.

== PREFIX NOISE ==
Abaikan kata pembuka yang tidak relevan:
"ok", "oke", "kalau", "terus", "nah", "eh", "btw", "oh iya", dll.
Fokus ke inti maksud:
- "ok, kalau piutang berapa total?" -> query_ar_outstanding
- "terus hutang gw gimana?" -> query_ap_outstanding
- "nah saldo BCA berapa?" -> query_bank_account_balance
- "eh ada stok habis ga?" -> query_items_no_stock

== DISAMBIGUATION ==
- "daftar pelanggan" / "list pelanggan" / "siapa aja pelanggan" -> query_customers_list (BUKAN query_customers_with_overdue)
- "pelanggan yang terlambat/overdue/jatuh tempo" -> query_customers_with_overdue (HARUS ada kata terlambat/overdue/jatuh tempo)
- "daftar vendor" / "list vendor" -> query_vendors_list (BUKAN query_vendors_with_overdue)
- "vendor yang terlambat/overdue" -> query_vendors_with_overdue

== FOLLOW-UP CONTEXT ==
Jika ada konteks sebelumnya, follow-up singkat harus dipahami:
- Setelah piutang: "yang paling besar?" -> query_ar_invoices
- Setelah hutang: "yang paling besar?" -> query_ap_outstanding
- Setelah daftar apapun: "urutkan dari terbesar" -> reformat_as_table
- Setelah piutang/hutang: "per faktur" / "tampilkan per faktur" -> contextual_drill_down

== DISAMBIGUATION ==
- "daftar pelanggan" / "list pelanggan" / "siapa aja pelanggan" -> query_customers_list
- "pelanggan yang terlambat/overdue/jatuh tempo" -> query_customers_with_overdue (HARUS ada kata terlambat/overdue/jatuh tempo)
- "daftar vendor" / "list vendor" -> query_vendors_list
- "vendor yang terlambat/overdue" -> query_vendors_with_overdue

== CONTEXT FOLLOW-UP ==
- Setelah piutang: "yang paling besar?" -> query_ar_invoices (faktur piutang terbesar)
- Setelah hutang: "yang paling besar?" -> query_ap_outstanding (drill-down hutang terbesar)
- Setelah daftar apapun: "urutkan dari terbesar" -> reformat_as_table (re-format last response sorted)
- Setelah piutang/hutang: "per faktur" / "tampilkan per faktur" -> contextual_drill_down

== FALLBACK ==
- Pesan ambigu/tidak jelas -> intent: "ambiguous"
"""

PIPELINE_ENABLED_INTENTS = {
    # Re-format + drill-down
    "reformat_as_table",
    "drilldown_table",
    "contextual_drill_down",
    # Batch 2 calc intents
    "calc_rank_expense_accounts",
    "calc_count_customers_inactive",
    "calc_count_vendors_inactive",
    "calc_count_expenses_this_month",
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
    "query_item_sales_summary",
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
    # Batch 2 query intents
    "query_items_no_stock",
    "query_customers_with_overdue",
    "query_vendors_with_overdue",
    "query_sales_invoices_unpaid",
    "query_bills_by_vendor",
    "query_bills_unpaid",
    "query_expenses_by_date_range",
    "query_account_ledger",
    "query_ar_aging",
    "query_ap_aging",
    "query_dashboard_summary",
    "query_overdue_all",
    "query_recurring_bills_list",
    "query_bank_transactions_by_date",
    # Batch 3 report + cross-module calc intents
    "query_profit_loss",
    "query_balance_sheet",
    "query_cash_flow",
    "query_trial_balance",
    "calc_profit_margin_per_item",
    "calc_top_selling_items",
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
    # Batch 3: Credit Notes, Vendor Credits, Quotes, Bank Transfers, Deposits
    "create_credit_note",
    "void_credit_note",
    "create_vendor_credit",
    "void_vendor_credit",
    "create_quote",
    "create_sales_order",
    "update_sales_invoice",
    "update_sales_order",
    "void_sales_order",
    "create_bank_transfer",
    "void_bank_transfer",
    "create_customer_deposit",
    "void_customer_deposit",
    "create_vendor_deposit",
    "void_vendor_deposit",
    "query_credit_notes_list",
    "query_credit_note_detail",
    "query_credit_notes_summary",
    "query_vendor_credits_list",
    "query_vendor_credit_detail",
    "query_vendor_credits_summary",
    "query_quotes_list",
    "query_quote_detail",
    "query_quotes_summary",
    "query_bank_transfers_list",
    "query_customer_deposits_list",
    "query_vendor_deposits_list",
    # ── Manufacturing ──
    "query_bom_list",
    "query_bom_detail",
    "query_bom_cost_breakdown",
    "query_bom_materials_required",
    "create_bom",
    "query_work_order_list",
    "query_work_order_detail",
    "query_work_order_cost_analysis",
    "query_production_active",
    "query_production_schedule",
    "query_material_issues",
    "query_fg_receipts",
    "create_work_order",
    "release_work_order",
    "start_work_order",
    "complete_work_order",
    "issue_materials",
    "report_production_output",
    "void_work_order",
    "cancel_work_order",
    "query_work_center_list",
    "create_work_center",
    "calc_count_work_orders_active",
    "calc_count_bom_active",
    "calc_count_work_orders_draft",
    "calc_count_work_centers",
    "calc_rank_work_orders_by_quantity",
}


def is_pipeline_enabled(intent: str) -> bool:
    """Check if intent should use compiler pipeline or fallback to agent loop."""
    return intent in PIPELINE_ENABLED_INTENTS


# ── Code-Driven CRUD Intent Classifier ────────────────────────────────────
# Deterministic, 0ms, runs BEFORE LLM extraction.
# Returns (intent, entity_name_raw, name_field) or (None, None, None) if no CRUD match.

import re as _re  # noqa: E402

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
        "terima",
        "terima pembayaran",
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
    "_credit_note": {
        "keywords": ["nota kredit", "credit note"],
        "name_field": "credit_note_number",
    },
    "_vendor_credit": {
        "keywords": ["vendor credit", "kredit vendor"],
        "name_field": "vendor_credit_number",
    },
    "_quote": {
        "keywords": ["penawaran", "quote", "quotation"],
        "name_field": "quote_number",
    },
    "_bank_transfer": {
        "keywords": ["transfer bank", "transfer antar rekening", "bank transfer"],
        "name_field": "transfer_number",
    },
    "_customer_deposit": {
        "keywords": ["deposit pelanggan", "customer deposit", "uang muka pelanggan"],
        "name_field": "deposit_number",
    },
    "_vendor_deposit": {
        "keywords": ["deposit vendor", "vendor deposit", "uang muka vendor"],
        "name_field": "deposit_number",
    },
    "_work_order": {
        "keywords": ["work order", "wo", "perintah produksi", "order produksi"],
        "name_field": "work_order_number",
    },
    "_bom": {
        "keywords": ["bom", "bill of materials", "resep produksi"],
        "name_field": "bom_code",
    },
    "_work_center": {
        "keywords": ["work center", "stasiun kerja"],
        "name_field": "work_center_name",
    },
}


def classify_query_intent(user_text: str) -> tuple:
    """Code-driven query intent classifier. 0ms, deterministic."""
    import re as _qre

    t = user_text.strip().lower()

    # ── Calc engine intents: superlative + ranking patterns ──
    # Re-enabled from DISABLED P2.1 — LLM Router unreliable for these intents.
    # Covers: "piutang paling besar", "siapa yang hutangnya terbesar", "vendor mana paling banyak kita hutangi"
    if (
        _qre.search(
            r"(?:piutang|ar).*(?:paling\s+besar|terbesar|paling\s+banyak|paling\s+tinggi)",
            t,
        )
        or _qre.search(
            r"(?:paling\s+besar|terbesar|paling\s+banyak).*(?:piutang|ar)", t
        )
        or _qre.search(
            r"(?:pelanggan|customer).*(?:piutang|ar).*(?:paling|terbesar|terbanyak)", t
        )
        or _qre.search(
            r"(?:ranking|peringkat).*(?:pelanggan|customer).*(?:piutang|ar)", t
        )
        or _qre.search(
            r"(?:ranking|peringkat).*(?:piutang|ar).*(?:pelanggan|customer)", t
        )
    ):
        return "calc_rank_customers_by_ar", None, None
    if (
        _qre.search(
            r"(?:hutang|utang|\bap\b).*(?:paling\s+besar|terbesar|paling\s+banyak|paling\s+tinggi)",
            t,
        )
        or _qre.search(
            r"(?:paling\s+besar|terbesar|paling\s+banyak).*(?:hutang|utang|\bap\b)", t
        )
        or _qre.search(
            r"(?:vendor|pemasok).*(?:hutang|utang).*(?:paling|terbesar|terbanyak)", t
        )
        or _qre.search(
            r"(?:ranking|peringkat).*(?:vendor|pemasok).*(?:hutang|utang|\bap\b)", t
        )
        or _qre.search(
            r"(?:ranking|peringkat).*(?:hutang|utang|\bap\b).*(?:vendor|pemasok)", t
        )
        or _qre.search(
            r"(?:vendor|pemasok).*(?:paling\s+banyak).*(?:hutang|hutangi)", t
        )
    ):
        return "calc_rank_vendors_by_ap", None, None
    # if _qre.search(r"(?:total|jumlah).*(?:penjualan|sales).*(?:bulan\s*ini)", t):
    #     return "calc_sum_sales_this_month", None, None
    # if _qre.search(r"(?:total|jumlah).*(?:pembelian|purchase).*(?:bulan\s*ini)", t):
    #     return "calc_sum_purchases_this_month", None, None
    # if _qre.search(
    #     r"(?:total|jumlah).*(?:pengeluaran|biaya|expense).*(?:bulan\s*ini)", t
    # ):
    #     return "calc_sum_expenses_this_month", None, None
    # if _qre.search(
    #     r"(?:total|jumlah).*(?:saldo|balance).*(?:semua|seluruh).*(?:rekening|bank)", t
    # ) or _qre.search(r"(?:total|jumlah).*(?:semua|seluruh).*(?:saldo|balance)", t):
    #     return "calc_sum_all_bank_balances", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 2 calc intents ──
    # if _qre.search(
    #     r"(?:ranking|peringkat).*(?:pengeluaran|biaya).*(?:akun|account)", t
    # ) or _qre.search(r"(?:pengeluaran|biaya).*(?:per|tiap).*(?:akun|account)", t):
    #     return "calc_rank_expense_accounts", None, None
    # if _qre.search(
    #     r"(?:berapa|jumlah).*(?:pelanggan|customer).*(?:tidak\s*aktif|inactive)", t
    # ):
    #     return "calc_count_customers_inactive", None, None
    # if _qre.search(
    #     r"(?:berapa|jumlah).*(?:vendor|pemasok).*(?:tidak\s*aktif|inactive)", t
    # ):
    #     return "calc_count_vendors_inactive", None, None
    # if _qre.search(r"(?:berapa|jumlah).*(?:pengeluaran|biaya).*(?:bulan\s*ini)", t):
    #     return "calc_count_expenses_this_month", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Report intents ──
    # IMPORTANT: trial_balance ("neraca saldo") MUST be checked BEFORE balance_sheet ("neraca")
    # if _qre.search(r"(?:neraca\s*saldo|trial\s*balance)", t):
    #     return "query_trial_balance", None, None
    # if _qre.search(
    #     r"(?:laba\s*rugi|profit\s*loss|untung\s*rugi|pendapatan\s+dan\s+beban)", t
    # ):
    #     return "query_profit_loss", None, None
    # if _qre.search(r"\bneraca\b(?!\s*saldo)", t):
    #     return "query_balance_sheet", None, None
    # if _qre.search(r"(?:arus\s*kas|cash\s*flow|aliran\s*kas)", t):
    #     return "query_cash_flow", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Cross-module calc intents ──
    # if _qre.search(
    #     r"(?:margin|keuntungan|profit)\s*(?:per|tiap)?\s*(?:barang|item|produk)", t
    # ):
    #     return "calc_profit_margin_per_item", None, None
    # if _qre.search(
    #     r"(?:barang|produk|item).*(?:terlaris|paling\s*laku|top\s*selling|paling\s*banyak\s*terjual)",
    #     t,
    # ):
    #     return "calc_top_selling_items", None, None

    # ── Manufacturing query intents (code-driven, 0ms) ──
    # WO number detection: WO-XXXX-XXXXXX pattern
    _wo_match = _qre.search(r"(WO-\d{4}-\d{4,6})", t, _qre.IGNORECASE)
    _bom_match = _qre.search(r"(?:bom(?:\s+code)?[:\s]+)?([A-Z][A-Z0-9]+-\d{3}(?:-[A-Z0-9]+)*)", t, _qre.IGNORECASE)

    # Detail WO by number: "detail work order WO-2026-000031" or just "WO-2026-000031"
    if _wo_match:
        _wo_num = _wo_match.group(1).upper()
        if _qre.search(r"(?:biaya|cost|analisis|analysis)", t):
            return "query_work_order_cost_analysis", _wo_num, "work_order_number"
        if _qre.search(r"(?:void|batalkan|cancel|batal)", t):
            pass  # let CRUD classifier handle void/cancel
        else:
            return "query_work_order_detail", _wo_num, "work_order_number"

    # Detail BOM by code: "biaya BOM BOMBER-001-COPY" or "detail BOM POLO-001"
    if _bom_match and _qre.search(r"(?:bom|bill\s+of\s+materials|resep)", t):
        _bom_code = _bom_match.group(1).upper()
        if _qre.search(r"(?:biaya|cost|breakdown|rincian)", t):
            return "query_bom_cost_breakdown", _bom_code, "bom_code"
        if _qre.search(r"(?:material|bahan|kebutuhan)", t):
            return "query_bom_materials_required", _bom_code, "bom_code"
        return "query_bom_detail", _bom_code, "bom_code"

    # List queries
    if _qre.search(r"(?:daftar|list|semua|lihat)\s+(?:bom|bill\s+of\s+materials|resep\s+produksi)", t):
        return "query_bom_list", None, None
    if _qre.search(r"(?:daftar|list|semua|lihat)\s+(?:work\s*order|wo\b|perintah\s+produksi)", t):
        return "query_work_order_list", None, None
    if _qre.search(r"(?:daftar|list|semua)\s+(?:work\s*center|stasiun\s+kerja)", t):
        return "query_work_center_list", None, None

    # Detail without code (generic)
    if _qre.search(r"(?:detail|lihat)\s+(?:bom)", t):
        return "query_bom_detail", None, None
    if _qre.search(r"(?:detail|lihat)\s+(?:work\s*order|wo\b)", t):
        return "query_work_order_detail", None, None

    # Cost/biaya queries
    if _qre.search(r"(?:biaya|cost)\s+(?:bom|breakdown)", t):
        return "query_bom_cost_breakdown", None, None
    if _qre.search(r"(?:biaya|cost)\s+(?:produksi|work\s*order|wo\b)", t):
        return "query_work_order_cost_analysis", None, None

    # Status/filter queries
    if _qre.search(r"(?:wo|work\s*order|produksi)\s+(?:aktif|active|berjalan|in.progress)", t):
        return "query_production_active", None, None
    if _qre.search(r"(?:jadwal|schedule)\s+(?:produksi|manufacturing)", t):
        return "query_production_schedule", None, None

    # Material/FG queries
    if _qre.search(r"(?:material|bahan)\s+(?:dibutuhkan|perlu|yang\s+perlu)", t):
        return "query_bom_materials_required", None, None
    if _qre.search(r"(?:material\s+issue|bahan\s+keluar|pengeluaran\s+bahan)", t):
        return "query_material_issues", None, None
    if _qre.search(r"(?:fg\s+receipt|barang\s+jadi\s+masuk|penerimaan\s+produksi)", t):
        return "query_fg_receipts", None, None

    # ── FIX 2: Manufacturing calc intents (code-driven, override LLM) ──
    if _qre.search(r"(?:berapa|jumlah|hitung).*(?:work\s*order|wo\b).*(?:aktif|active|berjalan)", t):
        return "calc_count_work_orders_active", None, None
    if _qre.search(r"(?:berapa|jumlah|hitung).*(?:bom).*(?:aktif|active)", t):
        return "calc_count_bom_active", None, None
    if _qre.search(r"(?:berapa|jumlah|hitung).*(?:work\s*order|wo\b).*(?:draft|belum\s+release)", t):
        return "calc_count_work_orders_draft", None, None
    if _qre.search(r"(?:berapa|jumlah|hitung).*(?:work\s*center|stasiun\s+kerja)", t):
        return "calc_count_work_centers", None, None

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

    # ── Item sales summary (omzet, revenue, total terjual) ──
    # Deterministic: "omzet penjualan X", "total terjual X", "penjualan X berapa"
    _item_sales_match = _qre.search(
        r"(?:omzet|total)\s+(?:penjualan|terjual)|(?:sudah|yang)\s+terjual|penjualan\s+(?:barang|produk|item)",
        t,
    )
    if _item_sales_match:
        return "query_item_sales_summary", None, None

    # ── Batch 2: New query intents ──────────────────────────────────────────
    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Items no stock
    # if _qre.search(r"(?:barang|item|stok).*(?:habis|kosong|out of stock|nol)", t):
    #     return "query_items_no_stock", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Customers list (MUST be before overdue check)
    # if _qre.search(r"(?:daftar|list|siapa\s+(?:saja|aja)).*(?:pelanggan|customer)", t) or _qre.search(r"(?:pelanggan|customer).*(?:siapa\s+(?:saja|aja)|daftar|list)", t):
    #     if not _qre.search(r"(?:terlambat|overdue|jatuh\s*tempo)", t):
    #         return "query_customers_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Vendors list (MUST be before overdue check)
    # if _qre.search(r"(?:daftar|list|siapa\s+(?:saja|aja)).*(?:vendor|pemasok)", t) or _qre.search(r"(?:vendor|pemasok).*(?:siapa\s+(?:saja|aja)|daftar|list)", t):
    #     if not _qre.search(r"(?:terlambat|overdue|jatuh\s*tempo)", t):
    #         return "query_vendors_list", None, None

    # KEEP: overdue patterns are financial-critical
    # Customers with overdue
    if _qre.search(r"(?:pelanggan|customer).*(?:terlambat|overdue|jatuh\s*tempo)", t):
        return "query_customers_with_overdue", None, None

    # Vendors with overdue
    if _qre.search(r"(?:vendor|pemasok).*(?:terlambat|overdue|jatuh\s*tempo)", t):
        return "query_vendors_with_overdue", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Sales invoices unpaid
    # if _qre.search(r"(?:faktur penjualan|invoice).*(?:belum\s*(?:di)?bayar|unpaid)", t):
    #     return "query_sales_invoices_unpaid", None, None

    # KEEP: "belum lunas" is financial-critical (AR/AP adjacent)
    # Bills unpaid (careful not to conflict with query_ap_outstanding)
    if _qre.search(
        r"(?:tagihan|faktur pembelian).*(?:belum\s*(?:di)?bayar|unpaid|belum\s*lunas)",
        t,
    ):
        return "query_bills_unpaid", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Account ledger
    # if _qre.search(r"(?:buku\s*besar|general\s*ledger|mutasi).*(?:akun|account)", t):
    #     return "query_account_ledger", None, None
    # if _qre.search(r"(?:akun|account).*(?:buku\s*besar|general\s*ledger|mutasi)", t):
    #     return "query_account_ledger", None, None

    # AR/AP aging
    if _qre.search(r"(?:aging|umur).*(?:piutang|ar)", t):
        return "query_ar_aging", None, None
    if _qre.search(r"(?:piutang|ar).*(?:aging|umur)", t):
        return "query_ar_aging", None, None
    if _qre.search(r"(?:aging|umur).*(?:hutang|utang|\bap\b)", t):
        return "query_ap_aging", None, None
    if _qre.search(r"(?:hutang|utang|\bap\b).*(?:aging|umur)", t):
        return "query_ap_aging", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Dashboard summary
    # if _qre.search(
    #     r"(?:ringkasan|summary|rangkuman).*(?:bisnis|usaha|keuangan|dashboard)", t
    # ):
    #     return "query_dashboard_summary", None, None

    # KEEP: overdue patterns are financial-critical
    # Overdue all
    if _qre.search(r"(?:apa\s+(?:saja|aja)).*(?:jatuh\s*tempo|overdue)", t):
        return "query_overdue_all", None, None
    if _qre.search(r"(?:semua|seluruh).*(?:jatuh\s*tempo|overdue)", t):
        return "query_overdue_all", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Recurring bills
    # if _qre.search(
    #     r"(?:daftar|list).*(?:recurring|berulang|rutin).*(?:tagihan|faktur|bill)", t
    # ):
    #     return "query_recurring_bills_list", None, None
    # if _qre.search(r"(?:tagihan|faktur|bill).*(?:recurring|berulang|rutin)", t):
    #     return "query_recurring_bills_list", None, None

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
    # ── Document number detail queries (MUST be before list/summary patterns) ──
    # "EXP-2604-0016 ini apa?" / "detail INV-0042" / "rincian PB-0003"
    _doc_ref_match = _qre.search(
        r"\b(EXP|INV|PB|JE|CN|VC|QT|RP|BP|SA|BT|CD|VD)-[\w-]+\b", t, _qre.IGNORECASE
    )
    if _doc_ref_match:
        _doc_prefix = _doc_ref_match.group(1).upper()
        _doc_number = _doc_ref_match.group(0)
        _prefix_to_intent = {
            "EXP": "query_expense_detail",
            "INV": "query_sales_invoice_detail",
            "PB": "query_bill_detail",
            "JE": "query_journal_detail",
            "CN": "query_credit_note_detail",
            "VC": "query_vendor_credit_detail",
            "QT": "query_quote_detail",
            "RP": "query_receive_payment_detail",
            "BP": "query_bill_payment_detail",
            "SA": "query_stock_adjustment_detail",
        }
        _detail_intent = _prefix_to_intent.get(_doc_prefix)
        if _detail_intent:
            return _detail_intent, _doc_number, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Expenses by account (guard: skip if doc reference present — handled above)
    # if _qre.search(r"(?:pengeluaran|biaya).*(?:untuk|akun)", t):
    #     return "query_expenses_by_account", None, None
    # Receive payments list
    # if _qre.search(
    #     r"(?:daftar|list).*(?:penerimaan|pembayaran\s*masuk|receive\s*payment)", t
    # ):
    #     return "query_receive_payments_list", None, None
    # Bill payments list
    # if _qre.search(
    #     r"(?:daftar|list).*(?:pembayaran\s*keluar|payment\s*out|bill\s*payment|pembayaran\s*tagihan)",
    #     t,
    # ):
    #     return "query_bill_payments_list", None, None
    # Journals list
    # if _qre.search(r"(?:daftar|list|semua)\s+jurnal", t):
    #     return "query_journals_list", None, None
    # Accounts list
    # if _qre.search(r"(?:daftar|list).*(?:akun|coa|chart\s*of\s*accounts)", t):
    #     return "query_accounts_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Credit Notes ──
    # if _qre.search(r"(?:daftar|list|semua).*(?:nota\s*kredit|credit\s*note)", t):
    #     return "query_credit_notes_list", None, None
    # if _qre.search(r"(?:detail|info|cek).*(?:nota\s*kredit|credit\s*note)", t):
    #     return "query_credit_note_detail", None, None
    # if _qre.search(r"(?:ringkasan|summary|total).*(?:nota\s*kredit|credit\s*note)", t):
    #     return "query_credit_notes_summary", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Vendor Credits ──
    # if _qre.search(r"(?:daftar|list|semua).*(?:vendor\s*credit|kredit\s*vendor)", t):
    #     return "query_vendor_credits_list", None, None
    # if _qre.search(r"(?:detail|info|cek).*(?:vendor\s*credit|kredit\s*vendor)", t):
    #     return "query_vendor_credit_detail", None, None
    # if _qre.search(
    #     r"(?:ringkasan|summary|total).*(?:vendor\s*credit|kredit\s*vendor)", t
    # ):
    #     return "query_vendor_credits_summary", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Quotes ──
    # if _qre.search(r"(?:daftar|list|semua).*(?:penawaran|quote|quotation)", t):
    #     return "query_quotes_list", None, None
    # if _qre.search(r"(?:detail|info|cek).*(?:penawaran|quote)", t):
    #     return "query_quote_detail", None, None
    # if _qre.search(r"(?:ringkasan|summary|total).*(?:penawaran|quote)", t):
    #     return "query_quotes_summary", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Bank Transfers ──
    # if _qre.search(
    #     r"(?:daftar|list|semua|riwayat).*(?:transfer\s*bank|transfer\s*antar)", t
    # ):
    #     return "query_bank_transfers_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Customer Deposits ──
    # if _qre.search(
    #     r"(?:daftar|list|semua).*(?:deposit\s*pelanggan|customer\s*deposit)", t
    # ):
    #     return "query_customer_deposits_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # ── Batch 3: Vendor Deposits ──
    # if _qre.search(r"(?:daftar|list|semua).*(?:deposit\s*vendor|vendor\s*deposit)", t):
    #     return "query_vendor_deposits_list", None, None

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

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Bank/Kas list
    # if _qre.search(r"\b(daftar|list|semua)\s+(rekening|bank|kas)\b", t):
    #     return "query_bank_accounts_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Sales invoices
    # if _qre.search(r"\b(daftar|list|semua)\s+(faktur\s+penjualan|invoice)\b", t):
    #     return "query_sales_invoices_list", None, None
    # if _qre.search(r"\b(ringkasan|summary|rekap|total)\s+(penjualan|sales)\b", t):
    #     return "query_sales_invoices_summary", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Bills (KEEP overdue/unpaid patterns above — financial-critical)
    # if _qre.search(r"\b(daftar|list|semua)\s+(faktur\s+pembelian|tagihan|bill)\b", t):
    #     return "query_bills_list", None, None
    # if _qre.search(r"\b(ringkasan|summary|rekap|total)\s+(pembelian|bill)\b", t):
    #     return "query_bills_summary", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Expenses
    # if _qre.search(r"\b(daftar|list|semua)\s+(pengeluaran|biaya|expense)\b", t):
    #     return "query_expenses_list", None, None
    # if _qre.search(
    #     r"\b(ringkasan|summary|rekap|total)\s+(pengeluaran|biaya|expense)\b", t
    # ):
    #     return "query_expenses_summary", None, None

    # KEEP: Low stock / inactive — financial/operational-critical
    if _qre.search(
        r"\b(stok rendah|hampir habis|low stock|stok.*habis|mau habis|stok.*menipis|menipis)\b",
        t,
    ):
        return "query_items_low_stock", None, None
    if _qre.search(r"\b(barang|item|produk)\b.*\b(tidak aktif|nonaktif|inactive)\b", t):
        return "query_items_inactive", None, None
    # KEEP: Slow moving — fast-path to avoid LLM latency
    if _qre.search(r"\b(slow[\s-]?moving|dead\s*stock)\b", t) or _qre.search(
        r"\bbarang\s+(lambat|lama|mati|tidak\s+laku|slow)\b", t
    ):
        return "query_items_slow_moving", None, None
    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # if _qre.search(r"\b(daftar|list|semua)\s+(kategori)\b", t):
    #     return "query_categories_list", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Account (CoA) detail — "detail akun kas", "info akun beban gaji"
    # if _qre.search(
    #     r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:akun|account|coa)",
    #     t,
    # ):
    #     return "query_account_detail", None, None

    # DISABLED P2.1 (2026-04-14): handled by LLM Router
    # Customer/Vendor detail — "data pelanggan X", "detail vendor Y", "info customer Z"
    # if _qre.search(
    #     r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:pelanggan|customer)",
    #     t,
    # ):
    #     return "query_customer_detail", None, None
    # if _qre.search(
    #     r"(?:data|detail|info|informasi|cek|lihat)\s+(?:lengkap\s+)?(?:vendor|pemasok|supplier)",
    #     t,
    # ):
    #     return "query_vendor_detail", None, None

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
    # Batch 2 calc intents
    ("calc_rank_expense_accounts",)
    ("calc_count_customers_inactive",)
    ("calc_count_vendors_inactive",)
    ("calc_count_expenses_this_month",)

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
    # Batch 2 calc intents
    ("calc_rank_expense_accounts",)
    ("calc_count_customers_inactive",)
    ("calc_count_vendors_inactive",)
    ("calc_count_expenses_this_month",)

    # Re-format requests — user wants last response as table
    # GUARD: if text contains domain-specific nouns, it's a NEW query, not reformat
    _has_domain_noun = bool(
        _qre.search(
            r"\b(?:barang|item|produk|stok|stock|pelanggan|customer|vendor|pemasok|"
            r"faktur|invoice|tagihan|bill|pengeluaran|biaya|expense|"
            r"jurnal|akun|account|rekening|bank|gaji|payroll)\b",
            t,
        )
    )
    if not _has_domain_noun:
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

    # Find ALL matching entities, then pick earliest position in text.
    # This ensures "daftarkan vendor baru dengan rekening" matches vendor
    # (appears first) not bank_account (longer keyword "rekening").
    sorted_entities = []
    for suffix, config in _ENTITY_KEYWORDS.items():
        for kw in config["keywords"]:
            sorted_entities.append((len(kw), kw, suffix, config))

    _entity_candidates = []  # (position, keyword_len, suffix, config, end_pos, source)
    for _, kw, suffix, config in sorted_entities:
        idx = remaining.find(kw)
        if idx != -1:
            _entity_candidates.append(
                (idx, len(kw), suffix, config, idx + len(kw), "remaining")
            )
        else:
            idx_full = search_text.find(kw)
            if idx_full != -1 and idx_full >= action_end_pos - 2:
                _adj_end = (idx_full + len(kw)) - action_end_pos
                if _adj_end < 0:
                    _adj_end = len(remaining)
                _entity_candidates.append(
                    (idx_full, len(kw), suffix, config, _adj_end, "full")
                )

    if _entity_candidates:
        # Sort by position ASC (earliest first), then by keyword length DESC (longest wins at same position)
        _entity_candidates.sort(key=lambda x: (x[0], -x[1]))
        _, _, entity_suffix, entity_config, entity_end_pos, _src = _entity_candidates[0]

    if not entity_suffix:
        return None, None, None

    # Step 3: Build intent
    if action == "void" and entity_suffix not in (
        "_sales_invoice",
        "_bill",
        "_expense",
        "_receive_payment",
        "_bill_payment",
        "_credit_note",
        "_vendor_credit",
        "_bank_transfer",
        "_customer_deposit",
        "_vendor_deposit",
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
