"""
Tool Registry for MilkyHoop Unified Agent.

Defines all tools available to the agent:
- 35 READ tools (GET-only, Law 10 compliant)
- 2 ACTION tools (propose_action, simulate_action)

Pattern: identical to ragllm/api_tools.py but with action tools added.
"""

from typing import Dict, List, Any

from .direct_action_registry import DIRECT_ACTIONS, QUERY_ACTIONS


# ─── Endpoint Mapping ──────────────────────────────────────────────────────────
# Maps tool name → HTTP method + API path (for read tools executed via httpx)

TOOL_ENDPOINTS: Dict[str, Dict[str, str]] = {
    # Master Data — Search
    "search_customers": {"method": "GET", "path": "/api/customers/autocomplete"},
    "search_vendors": {"method": "GET", "path": "/api/vendors/autocomplete"},
    "search_items": {"method": "GET", "path": "/api/items"},
    "search_accounts": {"method": "GET", "path": "/api/accounts/dropdown"},
    "search_bank_accounts": {"method": "GET", "path": "/api/bank-accounts"},
    "get_customer_invoices": {"method": "GET", "path": "/api/sales-invoices"},
    "get_vendor_bills": {"method": "GET", "path": "/api/bills"},
    # Master Data — Detail
    "get_customer_detail": {"method": "GET", "path": "/api/customers/{id}"},
    "get_vendor_detail": {"method": "GET", "path": "/api/vendors/{id}"},
    "get_item_detail": {"method": "GET", "path": "/api/items/{id}"},
    # Master Data — List
    "get_customers": {"method": "GET", "path": "/api/customers"},
    "get_vendors": {"method": "GET", "path": "/api/vendors"},
    "get_items": {"method": "GET", "path": "/api/items"},
    # Financial Documents
    "get_invoices": {"method": "GET", "path": "/api/sales-invoices"},
    "get_invoice_detail": {"method": "GET", "path": "/api/sales-invoices/{id}"},
    "get_bills": {"method": "GET", "path": "/api/bills"},
    "get_bill_detail": {"method": "GET", "path": "/api/bills/{id}"},
    "get_expenses": {"method": "GET", "path": "/api/expenses"},
    "get_expense_detail": {"method": "GET", "path": "/api/expenses/{id}"},
    "get_credit_notes": {"method": "GET", "path": "/api/credit-notes"},
    "get_purchase_orders": {"method": "GET", "path": "/api/purchase-orders"},
    # Payments
    "get_receive_payments": {"method": "GET", "path": "/api/receive-payments"},
    "get_bill_payments": {"method": "GET", "path": "/api/bill-payments"},
    # Accounting
    "get_journal_entries": {"method": "GET", "path": "/api/journals"},
    "get_general_ledger": {"method": "GET", "path": "/api/ledger"},
    "get_trial_balance": {"method": "GET", "path": "/api/reports/trial-balance"},
    "get_accounting_periods": {"method": "GET", "path": "/api/periods"},
    "get_chart_of_accounts": {"method": "GET", "path": "/api/accounts"},
    # Reports
    "get_profit_loss": {"method": "GET", "path": "/api/reports/laba-rugi/{periode}"},
    "get_balance_sheet": {"method": "GET", "path": "/api/reports/neraca/{periode}"},
    "get_cash_flow": {"method": "GET", "path": "/api/reports/arus-kas/{periode}"},
    "get_ar_aging": {"method": "GET", "path": "/api/reports/aging-receivable"},
    "get_ap_aging": {"method": "GET", "path": "/api/reports/aging-payable"},
    # Banking
    "get_bank_accounts": {"method": "GET", "path": "/api/bank-accounts"},
    "get_bank_transactions": {
        "method": "GET",
        "path": "/api/bank-accounts/{bank_account_id}/transactions",
    },
    "get_bank_reconciliation": {
        "method": "GET",
        "path": "/api/bank-reconciliation/accounts",
    },
    # Dashboard
    "get_dashboard_summary": {"method": "GET", "path": "/api/dashboard/summary"},
    "get_overdue_invoices": {
        "method": "GET",
        "path": "/api/dashboard/overdue-invoices",
    },
    "get_overdue_bills": {"method": "GET", "path": "/api/dashboard/overdue-bills"},
    # Inventory Analytics
    "get_top_products": {"method": "GET", "path": "/api/inventory/top-products"},
    "get_slow_moving_products": {
        "method": "GET",
        "path": "/api/inventory/slow-moving-products",
    },
    "get_product_margins": {"method": "GET", "path": "/api/inventory/product-margins"},
    # Financial Ratios
    "get_financial_ratios": {"method": "GET", "path": "/api/financial-ratios"},
    "get_ratio_dashboard": {"method": "GET", "path": "/api/financial-ratios/dashboard"},
    "get_ratio_trend": {"method": "GET", "path": "/api/financial-ratios/trend"},
    "get_ratio_alerts": {"method": "GET", "path": "/api/financial-ratios/alerts"},
    # Budgets
    "get_budgets": {"method": "GET", "path": "/api/budgets"},
    "get_budget_detail": {"method": "GET", "path": "/api/budgets/{id}/vs-actual"},
    # Document Intake
    "review_document": {
        "method": "GET",
        "path": "/api/document-intake/document/{document_id}",
    },
    # Cost Centers
    "get_cost_centers": {"method": "GET", "path": "/api/cost-centers"},
    "get_cost_center_summary": {
        "method": "GET",
        "path": "/api/cost-centers/{id}/summary",
    },
    # === Sprint 2: Cash & Payment Workflows ===
    "get_bank_transfers": {
        "method": "GET",
        "path": "/api/bank-transfers",
        "params": ["status", "search", "date_from", "date_to"],
        "description": "List transfer antar rekening bank",
    },
    "get_bank_transfer_detail": {
        "method": "GET",
        "path": "/api/bank-transfers/{id}",
        "params": [],
        "description": "Detail transfer bank termasuk jurnal",
    },
    "get_bank_transfer_summary": {
        "method": "GET",
        "path": "/api/bank-transfers/summary",
        "params": [],
        "description": "Ringkasan total transfer bank",
    },
    "get_vendor_deposits": {
        "method": "GET",
        "path": "/api/vendor-deposits",
        "params": ["status", "vendor_id"],
        "description": "List uang muka ke vendor",
    },
    "get_vendor_deposit_detail": {
        "method": "GET",
        "path": "/api/vendor-deposits/{id}",
        "params": [],
        "description": "Detail deposit vendor termasuk sisa yang belum digunakan",
    },
    "get_customer_deposits": {
        "method": "GET",
        "path": "/api/customer-deposits",
        "params": ["status", "customer_id", "search"],
        "description": "List uang muka pelanggan",
    },
    "get_customer_deposit_detail": {
        "method": "GET",
        "path": "/api/customer-deposits/{id}",
        "params": [],
        "description": "Detail deposit pelanggan",
    },
    "get_cheques": {
        "method": "GET",
        "path": "/api/cheques",
        "params": ["cheque_type", "status", "search"],
        "description": "List giro/cheque. Filter: pending, deposited, cleared, bounced",
    },
    # === Sprint 3: Recurring & Pipeline ===
    "get_recurring_invoices": {
        "method": "GET",
        "path": "/api/recurring-invoices",
        "params": ["status", "customer_id"],
        "description": "List faktur berulang (recurring invoice)",
    },
    "get_recurring_invoices_due": {
        "method": "GET",
        "path": "/api/recurring-invoices/due",
        "params": [],
        "description": "Faktur berulang yang jatuh tempo",
    },
    "get_recurring_bills": {
        "method": "GET",
        "path": "/api/recurring-bills",
        "params": ["status", "vendor_id"],
        "description": "List tagihan berulang (subscription, langganan)",
    },
    "get_recurring_bills_due": {
        "method": "GET",
        "path": "/api/recurring-bills/due",
        "params": [],
        "description": "Tagihan berulang yang jatuh tempo",
    },
    "get_sales_orders": {
        "method": "GET",
        "path": "/api/sales-orders",
        "params": ["status", "customer_id", "search"],
        "description": "List sales order",
    },
    "get_sales_order_detail": {
        "method": "GET",
        "path": "/api/sales-orders/{id}",
        "params": [],
        "description": "Detail sales order termasuk item dan status fulfillment",
    },
    "get_quotes": {
        "method": "GET",
        "path": "/api/quotes",
        "params": ["status", "customer_id", "search"],
        "description": "List penawaran (quotes)",
    },
    # ── Sprint 4: Asset & Inventory Operations ──────────────────
    "get_fixed_assets": {
        "method": "GET",
        "path": "/api/fixed-assets",
        "params": ["status", "category_id", "search"],
        "description": "List aset tetap. Filter: status, category, search.",
    },
    "get_fixed_asset_detail": {
        "method": "GET",
        "path": "/api/fixed-assets/{id}",
        "params": ["id"],
        "description": "Detail aset tetap + depresiasi.",
    },
    "get_stock_adjustments": {
        "method": "GET",
        "path": "/api/stock-adjustments",
        "params": ["status", "adjustment_type", "search", "date_from", "date_to"],
        "description": "List penyesuaian stok. Filter: status, type, date range.",
    },
    "get_stock_adjustment_detail": {
        "method": "GET",
        "path": "/api/stock-adjustments/{id}",
        "params": ["id"],
        "description": "Detail penyesuaian stok.",
    },
    "get_payroll_summary": {
        "method": "GET",
        "path": "/api/payroll/summary",
        "params": [],
        "description": "Ringkasan payroll/gaji bulan ini.",
    },
    # Direct Actions
    "propose_direct_action": {
        "method": "POST",
        "path": "/internal/direct-action",
    },  # handled internally
}


# ─── Action Type Enum Values ──────────────────────────────────────────────────
# Maps action_type string → proto enum int for gRPC calls

ACTION_TYPE_MAP: Dict[str, int] = {
    "CREATE_CUSTOMER": 0,
    "CREATE_VENDOR": 2,
    "CREATE_PRODUCT": 3,
    "CREATE_SALES_INVOICE": 10,
    "CREATE_PURCHASE_INVOICE": 11,
    "CREATE_EXPENSE": 12,
    "CREATE_CREDIT_NOTE": 13,
    "CREATE_PURCHASE_ORDER": 14,
    "RECEIVE_PAYMENT": 20,
    "MAKE_PAYMENT": 21,
    "BANK_TRANSFER": 22,
    "POST_GENERAL_JOURNAL": 30,
    "REVERSE_JOURNAL": 31,
    "CLOSE_PERIOD": 32,
    "REOPEN_PERIOD": 33,
}

# Action types that skip user confirmation (low-risk master data)
AUTO_EXECUTE_ACTIONS = {"CREATE_CUSTOMER", "CREATE_VENDOR", "CREATE_PRODUCT"}


# ─── Tool Definitions (JSON Schema Standard) ─────────────────────────────────
# Format: JSON Schema standard (provider-agnostic)

READ_TOOLS: List[Dict[str, Any]] = [
    # ── Master Data Search ──
    {
        "name": "search_customers",
        "description": "Cari pelanggan (nama/email/telepon). Return: id, name, phone, company_name.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Kata kunci pencarian (nama/email)",
                }
            },
            "required": ["q"],
        },
    },
    {
        "name": "search_vendors",
        "description": "Cari vendor (nama/kode). Return: id, name, phone, company_name.",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Kata kunci pencarian (nama)"}
            },
            "required": ["q"],
        },
    },
    {
        "name": "search_items",
        "description": "Cari barang/jasa (nama/SKU). Return: id, name, sku, price, unit.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Kata kunci pencarian (nama/SKU)",
                }
            },
            "required": ["search"],
        },
    },
    {
        "name": "search_accounts",
        "description": "Cari akun CoA (nama/kode). WAJIB sertakan account_type.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Kata kunci pencarian (nama/kode akun)",
                },
                "type": {
                    "type": "string",
                    "description": "Tipe akun (WAJIB): ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE, COGS, OTHER_INCOME, OTHER_EXPENSE, RECEIVABLE, PAYABLE",
                    "enum": [
                        "ASSET",
                        "LIABILITY",
                        "EQUITY",
                        "REVENUE",
                        "EXPENSE",
                        "COGS",
                        "OTHER_INCOME",
                        "OTHER_EXPENSE",
                        "RECEIVABLE",
                        "PAYABLE",
                    ],
                },
            },
            "required": ["search"],
        },
    },
    {
        "name": "search_bank_accounts",
        "description": "Cari rekening bank (nama/nomor). Return: id, account_name, bank_name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Nama bank atau nomor rekening",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_customer_invoices",
        "description": "Daftar invoice outstanding pelanggan (journal-derived). Default: outstanding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "UUID pelanggan (dari search_customers)",
                },
                "status": {
                    "type": "string",
                    "enum": ["outstanding", "all"],
                    "description": "Filter. Default: outstanding (journal-derived via compute functions)",
                },
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_vendor_bills",
        "description": "Daftar bill outstanding vendor (journal-derived). Default: outstanding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_id": {
                    "type": "string",
                    "description": "UUID vendor (dari search_vendors)",
                },
                "status": {
                    "type": "string",
                    "enum": ["outstanding", "all"],
                    "description": "Filter. Default: outstanding (journal-derived via compute functions)",
                },
            },
            "required": ["vendor_id"],
        },
    },
    # ── Master Data Detail ──
    {
        "name": "get_customer_detail",
        "description": "Detail pelanggan by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID pelanggan"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_vendor_detail",
        "description": "Detail vendor by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID vendor"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_item_detail",
        "description": "Detail barang/jasa by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID item"}},
            "required": ["id"],
        },
    },
    # ── Master Data List ──
    {
        "name": "get_customers",
        "description": "List semua pelanggan.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_vendors",
        "description": "List semua vendor.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_items",
        "description": "List semua barang/jasa.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Financial Documents ──
    {
        "name": "get_invoices",
        "description": "List faktur penjualan. Filter: search, status, customer_id, periode.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Cari berdasarkan nama customer atau nomor faktur (ILIKE)",
                },
                "status": {
                    "type": "string",
                    "description": "Filter: draft, posted, partial, paid, overdue, void. PENTING: 'sudah terbit' bukan status — jangan filter, tampilkan semua. 'belum lunas' = partial. 'lunas' = paid. Jangan pakai status selain yang tercantum (JANGAN pakai sent/issued/published).",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter by customer UUID",
                },
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_invoice_detail",
        "description": "Detail faktur penjualan by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID faktur"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_bills",
        "description": "List tagihan/bill. Filter: status, search, vendor_id, periode.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter: draft, posted, partial, paid, overdue, void. PENTING: 'sudah terbit' bukan status — jangan filter, tampilkan semua. 'belum lunas' = partial. 'lunas' = paid. Jangan pakai status selain yang tercantum (JANGAN pakai sent/issued/published).",
                },
                "search": {
                    "type": "string",
                    "description": "Cari berdasarkan nama vendor atau nomor tagihan (ILIKE)",
                },
                "vendor_id": {"type": "string", "description": "Filter by vendor UUID"},
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_bill_detail",
        "description": "Detail tagihan by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID tagihan"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_expenses",
        "description": "List transaksi biaya/pengeluaran.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_expense_detail",
        "description": "Detail transaksi biaya by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID expense"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_credit_notes",
        "description": "List nota kredit.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_purchase_orders",
        "description": "List purchase orders.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Payments ──
    {
        "name": "get_receive_payments",
        "description": "List penerimaan pembayaran (dari pelanggan).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_bill_payments",
        "description": "List pembayaran tagihan (ke vendor).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Accounting ──
    {
        "name": "get_journal_entries",
        "description": "List jurnal entries. Filter by source_type, date_from, date_to.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "description": "Filter: SALES_INVOICE, PURCHASE_INVOICE, PAYMENT, EXPENSE, MANUAL_JOURNAL, REVERSAL",
                },
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_general_ledger",
        "description": "Buku besar per akun. Return transaksi detail per akun dalam rentang tanggal.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "UUID akun"},
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_trial_balance",
        "description": "Neraca saldo. Return saldo debit/kredit per akun.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02), default hari ini",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_accounting_periods",
        "description": "List periode akuntansi. Return: id, name, start_date, end_date, status (OPEN/CLOSED).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_chart_of_accounts",
        "description": "Daftar semua akun (chart of accounts). Return: id, code, name, type, parent_id.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Reports ──
    {
        "name": "get_profit_loss",
        "description": "Laporan laba rugi. Return: pendapatan, beban, laba bersih per kategori.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_balance_sheet",
        "description": "Neraca (balance sheet). Return: aset, liabilitas, ekuitas.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_cash_flow",
        "description": "Laporan arus kas. Return: aktivitas operasi, investasi, pendanaan.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_ar_aging",
        "description": "Aging piutang (accounts receivable). GUNAKAN TOOL INI untuk semua pertanyaan tentang piutang: total, per pelanggan, per faktur, siapa yang punya piutang, nomor faktur belum lunas. Return: customers[] (per pelanggan: customer_name, invoices[{invoice_number, balance, due_date, amount_paid, aging_bucket}], totals per umur), summary (current, 1-30, 31-60, 61-90, 90+), total_outstanding.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ap_aging",
        "description": "Aging hutang (accounts payable). GUNAKAN TOOL INI untuk semua pertanyaan tentang hutang: total hutang, hutang per vendor, tagihan belum lunas, vendor mana yang masih kita hutangi, tagihan overdue. Return: vendors[] (per vendor: vendor_name, bills[{bill_number, balance, due_date, amount_paid, aging_bucket}], totals per umur), summary (current, 1-30, 31-60, 61-90, 90+), total_outstanding.",
        "parameters": {
            "type": "object",
            "properties": {
                "periode": {
                    "type": "string",
                    "description": "Periode format YYYY-MM (contoh: 2026-02)",
                }
            },
            "required": [],
        },
    },
    # ── Banking ──
    {
        "name": "get_bank_accounts",
        "description": "List akun bank. Return: id, name, account_number, balance.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_bank_transactions",
        "description": "List mutasi/transaksi bank untuk akun tertentu. GUNAKAN untuk melihat riwayat transaksi bank, transfer masuk/keluar, pembayaran, pengeluaran yang tercatat di rekening bank. Return: daftar transaksi dengan tanggal, jumlah, deskripsi, source.",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_account_id": {
                    "type": "string",
                    "description": "UUID akun bank (dapatkan dari get_bank_accounts)",
                },
                "date_from": {
                    "type": "string",
                    "description": "Tanggal mulai filter (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Tanggal akhir filter (YYYY-MM-DD)",
                },
                "transaction_type": {
                    "type": "string",
                    "description": "Filter jenis transaksi. PENTING: JANGAN filter jika user tanya 'transaksi masuk' atau 'transaksi keluar' — ambil semua lalu kelompokkan. Jenis: deposit, withdrawal, payment_received, payment_made, opening. Uang MASUK = deposit + payment_received. Uang KELUAR = withdrawal + payment_made.",
                },
                "is_reconciled": {
                    "type": "boolean",
                    "description": "Filter berdasarkan status rekonsiliasi",
                },
                "skip": {"type": "integer", "description": "Offset/skip (default 0)"},
                "limit": {
                    "type": "integer",
                    "description": "Jumlah per halaman (default 50, max 200)",
                },
            },
            "required": ["bank_account_id"],
        },
    },
    {
        "name": "get_bank_reconciliation",
        "description": "Status rekonsiliasi bank.",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_account_id": {"type": "string", "description": "UUID akun bank"}
            },
            "required": [],
        },
    },
    # ── Dashboard ──
    {
        "name": "get_dashboard_summary",
        "description": "Ringkasan dashboard: total revenue, expense, profit, receivables, payables.",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Periode: month, quarter, year",
                    "enum": ["month", "quarter", "year"],
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_overdue_invoices",
        "description": "List faktur penjualan yang sudah lewat jatuh tempo.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_overdue_bills",
        "description": "List tagihan pembelian yang sudah lewat jatuh tempo.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Inventory Analytics ──
    {
        "name": "get_top_products",
        "description": "Produk paling laris berdasarkan jumlah terjual dari inventory ledger. GUNAKAN TOOL INI untuk pertanyaan: produk terlaris, barang paling banyak dibeli, ranking penjualan produk, produk paling laku. Parameter: period (all/this_month/last_month/this_year), limit (default 10). Return: products[] (product_name, sku, unit, total_qty_sold, transaction_count, first_sale, last_sale).",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Filter periode: all (semua), this_month (bulan ini), last_month (bulan lalu), this_year (tahun ini)",
                    "enum": ["all", "this_month", "last_month", "this_year"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah produk (default 10, max 50)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_slow_moving_products",
        "description": "Produk yang PALING SEDIKIT terjual, termasuk produk BELUM PERNAH terjual. GUNAKAN TOOL INI untuk pertanyaan: produk tidak laku, barang lambat terjual, slow moving, dead stock, produk menumpuk, barang mana yang belum laku. Parameter: period (all/this_month/last_month/this_year), limit (default 10). Return: products[] (product_name, sku, unit, total_qty_sold, transaction_count, last_sale).",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Filter periode: all, this_month, last_month, this_year",
                    "enum": ["all", "this_month", "last_month", "this_year"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah produk (default 10, max 50)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_product_margins",
        "description": "Analisis margin keuntungan produk. Menampilkan harga jual, harga beli, margin per unit, persentase margin, dan total profit dari penjualan aktual. GUNAKAN TOOL INI untuk pertanyaan: margin produk, keuntungan per produk, profitabilitas, produk paling untung, produk margin tipis/kecil/besar. Parameter: period (all/this_month/last_month/this_year), limit (default 10), sort (margin_desc/margin_asc/revenue_desc/profit_desc). Return: products[] (product_name, sell_price, buy_price, unit_margin, margin_percent, total_qty_sold, total_revenue, total_cogs, total_profit).",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Filter periode: all, this_month, last_month, this_year",
                    "enum": ["all", "this_month", "last_month", "this_year"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah produk (default 10, max 50)",
                },
                "sort": {
                    "type": "string",
                    "description": "Urutan: margin_desc, margin_asc, revenue_desc, profit_desc",
                    "enum": [
                        "margin_desc",
                        "margin_asc",
                        "revenue_desc",
                        "profit_desc",
                    ],
                },
            },
            "required": [],
        },
    },
    # -- Financial Ratios --
    {
        "name": "get_financial_ratios",
        "description": "Rasio keuangan bisnis (likuiditas, solvabilitas, profitabilitas, efisiensi). GUNAKAN TOOL INI untuk pertanyaan: kesehatan keuangan, apakah kita likuid, current ratio, debt ratio, profit margin ratio, ROA, ROE. Return: ratios per kategori dengan value, status (baik/perlu perhatian/bahaya), ideal_range.",
        "parameters": {
            "type": "object",
            "properties": {
                "as_of_date": {
                    "type": "string",
                    "description": "Tanggal analisis YYYY-MM-DD (default: hari ini)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ratio_dashboard",
        "description": "Dashboard ringkas rasio keuangan utama: current ratio, quick ratio, gross margin, net margin, debt-to-equity, inventory turnover. Termasuk alert dan tren. GUNAKAN untuk overview cepat kesehatan keuangan.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_ratio_trend",
        "description": "Tren rasio keuangan dari waktu ke waktu. GUNAKAN untuk pertanyaan: margin naik atau turun, tren likuiditas, perkembangan rasio. Return: data tren per periode + analisis (improving/declining/stable).",
        "parameters": {
            "type": "object",
            "properties": {
                "ratio": {
                    "type": "string",
                    "description": "Kode rasio: current_ratio, quick_ratio, gross_profit_margin, net_profit_margin, debt_to_equity, inventory_turnover",
                },
                "periods": {
                    "type": "integer",
                    "description": "Jumlah periode (default 12, max 60)",
                },
                "period_type": {
                    "type": "string",
                    "description": "Tipe periode: monthly, quarterly, yearly",
                    "enum": ["monthly", "quarterly", "yearly"],
                },
            },
            "required": ["ratio"],
        },
    },
    {
        "name": "get_ratio_alerts",
        "description": "Alert rasio keuangan yang melewati batas ideal. GUNAKAN untuk pertanyaan: ada masalah keuangan, warning, red flag, rasio yang perlu perhatian. Return: list rasio dengan alert level (warning/danger).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # -- Budgets --
    {
        "name": "get_budgets",
        "description": "List semua budget/anggaran. GUNAKAN untuk pertanyaan: budget apa saja, anggaran tahun ini, daftar budget. Return: list budget dengan nama, periode, status, total amount.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_budget_detail",
        "description": "Detail budget vs realisasi aktual. GUNAKAN untuk pertanyaan: budget marketing gimana, sudah over budget belum, realisasi vs anggaran, variance budget. Return: items per akun dengan budget_amount, actual_amount, variance, percentage_used.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID budget"},
                "month": {
                    "type": "integer",
                    "description": "Filter bulan tertentu (1-12, optional)",
                },
            },
            "required": ["id"],
        },
    },
    # -- Cost Centers --
    {
        "name": "get_cost_centers",
        "description": "List cost center/departemen. GUNAKAN untuk pertanyaan: biaya per departemen, cost center apa saja, list departemen. Return: list cost center dengan nama, kode, parent.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_cost_center_summary",
        "description": "Ringkasan biaya per cost center/departemen. GUNAKAN untuk pertanyaan: departemen mana paling boros, biaya departemen X berapa, breakdown biaya per cost center. Return: items per akun dengan debit, credit, net amount.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID cost center"},
                "start_date": {
                    "type": "string",
                    "description": "Tanggal mulai YYYY-MM-DD",
                },
                "end_date": {
                    "type": "string",
                    "description": "Tanggal akhir YYYY-MM-DD",
                },
            },
            "required": ["id", "start_date", "end_date"],
        },
    },
    # === Sprint 2: Cash & Payment Workflows ===
    {
        "name": "get_bank_transfers",
        "description": "List transfer antar rekening bank. GUNAKAN untuk: riwayat transfer bank, transfer dari BCA ke Mandiri, mutasi antar rekening.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["all", "draft", "posted", "void"],
                    "description": "Filter status transfer",
                },
                "search": {
                    "type": "string",
                    "description": "Cari berdasarkan nomor transfer",
                },
                "date_from": {
                    "type": "string",
                    "description": "Tanggal mulai (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Tanggal akhir (YYYY-MM-DD)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_bank_transfer_detail",
        "description": "Detail transfer bank termasuk jurnal terkait.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID transfer bank"}
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_bank_transfer_summary",
        "description": "Ringkasan total transfer bank. Untuk: total transfer bulan ini, ringkasan mutasi antar rekening.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_vendor_deposits",
        "description": "List uang muka ke vendor (advance payment). GUNAKAN untuk: berapa advance ke vendor, deposit vendor, uang muka pembelian.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter status: draft, posted, partial, applied, void",
                },
                "vendor_id": {
                    "type": "string",
                    "description": "Filter berdasarkan vendor UUID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_vendor_deposit_detail",
        "description": "Detail deposit vendor termasuk sisa yang belum digunakan dan riwayat aplikasi.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID deposit vendor"}
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_customer_deposits",
        "description": "List uang muka pelanggan (customer deposit). GUNAKAN untuk: pelanggan mana yang punya deposit, ada uang muka pelanggan, deposit customer.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["all", "draft", "posted", "partial", "applied", "void"],
                    "description": "Filter status deposit",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter berdasarkan customer UUID",
                },
                "search": {
                    "type": "string",
                    "description": "Cari berdasarkan nomor deposit atau nama pelanggan",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_customer_deposit_detail",
        "description": "Detail deposit pelanggan termasuk sisa dan riwayat aplikasi.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID deposit pelanggan"}
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_cheques",
        "description": "List giro/cheque. GUNAKAN untuk: cheque mana yang belum cair, ada giro bounced, status giro, daftar cek.",
        "parameters": {
            "type": "object",
            "properties": {
                "cheque_type": {
                    "type": "string",
                    "enum": ["received", "issued"],
                    "description": "Tipe: diterima atau dikeluarkan",
                },
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "deposited",
                        "cleared",
                        "bounced",
                        "cancelled",
                        "replaced",
                    ],
                    "description": "Status giro",
                },
                "search": {
                    "type": "string",
                    "description": "Cari nomor giro atau nama pihak",
                },
            },
            "required": [],
        },
    },
    # === Sprint 3: Recurring & Pipeline ===
    {
        "name": "get_recurring_invoices",
        "description": "List faktur berulang (recurring invoice). GUNAKAN untuk: recurring invoice apa saja, faktur otomatis, invoice berlangganan.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter status: active, paused, expired",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter berdasarkan customer UUID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_recurring_invoices_due",
        "description": "Faktur berulang yang jatuh tempo untuk diproses. GUNAKAN untuk: invoice recurring mana yang harus diproses, ada tagihan otomatis yang perlu dikirim?",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recurring_bills",
        "description": "List tagihan berulang (subscription, langganan). GUNAKAN untuk: tagihan recurring apa saja, subscription aktif, biaya berlangganan.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter status: active, paused, expired",
                },
                "vendor_id": {
                    "type": "string",
                    "description": "Filter berdasarkan vendor UUID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_recurring_bills_due",
        "description": "Tagihan berulang yang jatuh tempo bulan ini. GUNAKAN untuk: tagihan recurring mana yang harus dibayar, ada subscription yang jatuh tempo?",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_sales_orders",
        "description": "List sales order. GUNAKAN untuk: ada berapa order pending, pesanan yang belum dikirim, sales order bulan ini.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "all",
                        "draft",
                        "confirmed",
                        "partial_shipped",
                        "shipped",
                        "partial_invoiced",
                        "invoiced",
                        "completed",
                        "cancelled",
                    ],
                    "description": "Filter status order",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter berdasarkan customer UUID",
                },
                "search": {
                    "type": "string",
                    "description": "Cari nomor order atau nama pelanggan",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_sales_order_detail",
        "description": "Detail sales order termasuk item, status fulfillment, dan shipment.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID sales order"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_quotes",
        "description": "List penawaran (quotes/quotation). GUNAKAN untuk: penawaran mana yang belum disepakati, nilai total quotes aktif, berapa quote yang expired.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "all",
                        "draft",
                        "sent",
                        "accepted",
                        "expired",
                        "declined",
                        "converted",
                    ],
                    "description": "Filter status penawaran",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter berdasarkan customer UUID",
                },
                "search": {
                    "type": "string",
                    "description": "Cari nomor penawaran atau nama pelanggan",
                },
            },
            "required": [],
        },
    },
    # ── Sprint 4: Asset & Inventory Operations ──────────────────
    {
        "name": "get_fixed_assets",
        "description": "List aset tetap perusahaan. Filter: status (active, disposed, sold), category_id, search. Untuk 'aset apa saja?', 'daftar aset tetap'",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter status: active, disposed, sold",
                },
                "category_id": {
                    "type": "string",
                    "description": "Filter by category UUID",
                },
                "search": {"type": "string", "description": "Cari nama aset"},
            },
            "required": [],
        },
    },
    {
        "name": "get_fixed_asset_detail",
        "description": "Detail aset tetap termasuk informasi depresiasi, nilai buku, dan jadwal penyusutan. Untuk 'detail aset X', 'berapa depresiasi mesin?'",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "UUID aset tetap"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_stock_adjustments",
        "description": "List penyesuaian stok/inventory. Filter: status (draft, posted, void), adjustment_type (increase, decrease, recount, damaged, expired). Untuk 'ada adjustment stok?', 'penyesuaian stok bulan ini'",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter: all, draft, posted, void",
                },
                "adjustment_type": {
                    "type": "string",
                    "description": "Filter: increase, decrease, recount, damaged, expired",
                },
                "search": {"type": "string", "description": "Cari nomor adjustment"},
                "date_from": {
                    "type": "string",
                    "description": "Tanggal mulai (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Tanggal akhir (YYYY-MM-DD)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_stock_adjustment_detail",
        "description": "Detail penyesuaian stok termasuk item yang disesuaikan dan jurnal terkait. Untuk 'detail adjustment SA-001'",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "UUID stock adjustment"}
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_payroll_summary",
        "description": "Ringkasan penggajian/payroll: jumlah run per status, total gaji bulan ini. Untuk 'berapa total gajian?', 'expense payroll bulan ini', 'ringkasan penggajian'",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    # ── Document Intake ──
    {
        "name": "review_document",
        "description": "Ambil detail dokumen intake: hasil OCR, draft plan, draft jurnal. Gunakan saat user minta review dokumen tertentu.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "UUID dokumen yang akan di-review",
                }
            },
            "required": ["document_id"],
        },
    },
]


# ─── Action Tools ──────────────────────────────────────────────────────────────

ACTION_TYPE_ENUM = list(ACTION_TYPE_MAP.keys())

ACTION_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "propose_action",
        "description": (
            "Usulkan aksi akuntansi untuk konfirmasi user. "
            "MEMVALIDASI dan membuat preview, tapi TIDAK mengeksekusi. "
            "WAJIB resolve semua ID via search tools SEBELUM memanggil ini. "
            "Semua field langsung di top-level, JANGAN nest di payload. "
            "HANYA untuk: BANK_TRANSFER, CREATE_CREDIT_NOTE, CLOSE_PERIOD, REOPEN_PERIOD. "
            "Semua transaksi dan master data CRUD lainnya → pakai propose_direct_action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ACTION_TYPE_ENUM,
                    "description": "Tipe aksi",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Asumsi yang dibuat LLM",
                },
                "customer_id": {
                    "type": "string",
                    "description": "UUID customer (dari search_customers). Untuk: sales invoice, credit note, receive payment",
                },
                "vendor_id": {
                    "type": "string",
                    "description": "UUID vendor (dari search_vendors). Untuk: purchase invoice, make payment, purchase order",
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {
                                "type": "string",
                                "description": "UUID item (dari search_items)",
                            },
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "description": {"type": "string"},
                        },
                        "required": ["item_id", "quantity", "unit_price"],
                    },
                    "description": "Daftar item. Untuk: invoices, purchase orders, credit notes",
                },
                "invoice_date": {
                    "type": "string",
                    "description": "Tanggal faktur YYYY-MM-DD. Untuk: sales/purchase invoice",
                },
                "due_date": {
                    "type": "string",
                    "description": "Tanggal jatuh tempo YYYY-MM-DD",
                },
                "invoice_id": {
                    "type": "string",
                    "description": "UUID faktur yang dibayar/di-credit note",
                },
                "bill_id": {"type": "string", "description": "UUID bill yang dibayar"},
                "amount": {
                    "type": "number",
                    "description": "Jumlah pembayaran/expense/transfer",
                },
                "payment_date": {
                    "type": "string",
                    "description": "Tanggal pembayaran YYYY-MM-DD",
                },
                "payment_method": {
                    "type": "string",
                    "description": "Metode bayar: cash, bank_transfer, giro",
                },
                "payment_account_id": {
                    "type": "string",
                    "description": "UUID akun kas/bank untuk pembayaran",
                },
                "expense_account_id": {
                    "type": "string",
                    "description": "UUID akun beban",
                },
                "from_account_id": {
                    "type": "string",
                    "description": "UUID akun asal (bank transfer)",
                },
                "to_account_id": {
                    "type": "string",
                    "description": "UUID akun tujuan (bank transfer)",
                },
                "description": {
                    "type": "string",
                    "description": "Keterangan/memo transaksi",
                },
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_id": {"type": "string"},
                            "debit": {"type": "number"},
                            "credit": {"type": "number"},
                            "description": {"type": "string"},
                        },
                    },
                    "description": "Journal lines (untuk POST_GENERAL_JOURNAL)",
                },
                "posting_date": {
                    "type": "string",
                    "description": "Tanggal posting YYYY-MM-DD",
                },
                "journal_id": {
                    "type": "string",
                    "description": "UUID jurnal yang di-reverse",
                },
                "name": {
                    "type": "string",
                    "description": "Nama (customer/vendor/product baru)",
                },
                "email": {
                    "type": "string",
                    "description": "Email (customer/vendor baru)",
                },
                "phone": {
                    "type": "string",
                    "description": "Telepon (customer/vendor baru)",
                },
                "sku": {"type": "string", "description": "SKU produk baru"},
                "unit": {
                    "type": "string",
                    "description": "Satuan produk (pcs, kg, dll)",
                },
                "sell_price": {
                    "type": "number",
                    "description": "Harga jual produk baru",
                },
                "buy_price": {
                    "type": "number",
                    "description": "Harga beli produk baru",
                },
                "periode": {
                    "type": "string",
                    "description": "Periode YYYY-MM (untuk CLOSE_PERIOD, REOPEN_PERIOD)",
                },
            },
            "required": ["action_type"],
        },
    },
    {
        "name": "simulate_action",
        "description": (
            "Simulasi aksi tanpa membuat pending action. "
            "Hanya validasi + preview jurnal. Untuk analisis what-if."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ACTION_TYPE_ENUM,
                    "description": "Tipe aksi yang disimulasi",
                },
                "customer_id": {"type": "string", "description": "UUID customer"},
                "vendor_id": {"type": "string", "description": "UUID vendor"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                        },
                    },
                    "description": "Daftar item",
                },
                "amount": {"type": "number", "description": "Jumlah"},
                "invoice_date": {"type": "string", "description": "Tanggal YYYY-MM-DD"},
            },
            "required": ["action_type"],
        },
    },
    {
        "name": "propose_direct_action",
        "description": (
            "Propose a direct action for user confirmation via inline table + CTA buttons. "
            "Use for master data CRUD AND bank reconciliation actions. "
            "Include ALL field values in payload. "
            "KEY FIELDS: "
            "create_sales_invoice: customer_id, items [{item_id, description, quantity, unit_price}], auto_post=true. "
            "create_bill: vendor_id, vendor_name, issue_date (NOT bill_date!), items [{product_id, product_name, qty, price, unit}], status=posted. "
            "create_expense: expense_date, paid_through_id (NOT bank_account_id!), account_id, amount, description. "
            "create_journal_entry: entry_date (NOT journal_date!), description (NOT memo!), lines [{account_id, description, debit, credit}]. "
            "create_receive_payment: customer_id, allocations [{invoice_id, amount_applied}], total_amount, payment_date, bank_account_id. "
            "create_bill_payment: vendor_id, allocations [{bill_id, amount_applied}], total_amount, payment_date, bank_account_id. "
            "create_stock_adjustment: adjustment_date, adjustment_type, items [{product_id, quantity_adjustment, reason_detail}], notes. "
            "VOID: void_sales_invoice/void_bill={id, number, reason}. "
            "For recon: copy review_next_unmatched data into display fields. "
            "Hidden fields (session_id, statement_line_id) required but not shown."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_key": {
                    "type": "string",
                    "enum": list(DIRECT_ACTIONS.keys()),
                    "description": "The action to propose",
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Field values for the action. "
                        "For create_bank_account: account_name (required), account_type, bank_name, account_number, opening_balance, currency, is_default, notes. "
                        "For create_vendor: name (required), company_name, phone, email, address, tax_id, notes. "
                        "For create_customer: name (required), phone, email, address, company_name, notes. "
                        "For create_item: name (required), sku, base_unit, sales_price, purchase_price, item_type, description. "
                        "For create_account: account_code (required), name (required), account_type (required: ASSET/LIABILITY/EQUITY/REVENUE/EXPENSE/COGS), parent_id, description. "
                        "For create_warehouse: name (required), address, description. "
                        "For update_*: id (required) + fields to change. "
                        "For delete_*: id (required), name (for confirmation). "
                        "For categorize_statement: session_id, statement_line_id, account_id (required), statement_description, statement_date, amount, account_name, description. "
                        "For confirm_single_match: session_id, statement_line_id, transaction_ids (required), statement_description, statement_amount, transaction_description, transaction_amount, match_confidence, adjustment_amount. "
                    ),
                    "properties": {
                        "account_name": {
                            "type": "string",
                            "description": "Nama rekening bank (required for create_bank_account)",
                        },
                        "account_type": {
                            "type": "string",
                            "enum": [
                                "bank",
                                "cash",
                                "petty_cash",
                                "e_wallet",
                                "credit_card",
                            ],
                            "description": "Tipe akun: bank, cash, petty_cash, e_wallet, credit_card (default: cash)",
                        },
                        "bank_name": {
                            "type": "string",
                            "description": "Nama bank (e.g. BCA, BRI, Mandiri)",
                        },
                        "account_number": {
                            "type": "string",
                            "description": "Nomor rekening bank",
                        },
                        "opening_balance": {
                            "type": "number",
                            "description": "Saldo awal (default: 0)",
                        },
                        "currency": {
                            "type": "string",
                            "enum": ["IDR", "USD", "EUR", "SGD"],
                            "description": "Mata uang (default: IDR)",
                        },
                        "is_default": {
                            "type": "boolean",
                            "description": "Apakah ini rekening utama (default: false)",
                        },
                        "name": {
                            "type": "string",
                            "description": "Nama vendor/supplier (required for create_vendor)",
                        },
                        "company_name": {
                            "type": "string",
                            "description": "Nama perusahaan vendor",
                        },
                        "phone": {"type": "string", "description": "Nomor telepon"},
                        "email": {"type": "string", "description": "Alamat email"},
                        "address": {"type": "string", "description": "Alamat"},
                        "tax_id": {"type": "string", "description": "NPWP"},
                        "notes": {"type": "string", "description": "Catatan tambahan"},
                        "sku": {
                            "type": "string",
                            "description": "Kode/SKU barang (for create_item)",
                        },
                        "base_unit": {
                            "type": "string",
                            "description": "Satuan barang: pcs, roll, meter, kg (for create_item)",
                        },
                        "sales_price": {
                            "type": "number",
                            "description": "Harga jual (for create_item)",
                        },
                        "purchase_price": {
                            "type": "number",
                            "description": "Harga beli (for create_item)",
                        },
                        "item_type": {
                            "type": "string",
                            "enum": ["goods", "service", "non_inventory"],
                            "description": "Tipe: goods, service, atau non_inventory (for create_item, default: goods)",
                        },
                        "description": {"type": "string", "description": "Deskripsi"},
                        "account_code": {
                            "type": "string",
                            "description": "Kode akun CoA, e.g. 1-10700, 5-20100 (for create_account)",
                        },
                        "account_type_coa": {
                            "type": "string",
                            "enum": [
                                "ASSET",
                                "RECEIVABLE",
                                "LIABILITY",
                                "PAYABLE",
                                "EQUITY",
                                "REVENUE",
                                "COGS",
                                "EXPENSE",
                                "OTHER_INCOME",
                                "OTHER_EXPENSE",
                            ],
                            "description": "Tipe akun CoA (for create_account). Will be mapped to 'type' field automatically.",
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "UUID akun induk (opsional, for create_account)",
                        },
                        "id": {
                            "type": "string",
                            "description": "UUID entity (required for update/delete actions)",
                        },
                    },
                },
            },
            "required": ["action_key", "payload"],
        },
    },
    {
        "name": "start_workflow",
        "description": (
            "Start bank reconciliation or document review workflow ONLY. "
            "NOT for tutorials — use start_tutorial instead. "
            "Kamu = INTERPRETER, engine = CONTROLLER. "
            "Kirim data yang diekstrak dari user message via user_data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_type": {
                    "type": "string",
                    "enum": ["bank_reconciliation", "document_review"],
                    "description": "Tipe workflow. bank_reconciliation untuk rekon bank, document_review untuk review draft dokumen AI.",
                },
                "user_data": {
                    "type": "object",
                    "description": "Data dari user: account_id, account_name, statement_ending_balance, file_ref, no_file, dll.",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "UUID akun bank",
                        },
                        "account_name": {
                            "type": "string",
                            "description": "Nama akun bank",
                        },
                        "statement_ending_balance": {
                            "type": "number",
                            "description": "Saldo akhir rekening koran (IDR)",
                        },
                        "file_ref": {
                            "type": "string",
                            "description": "Reference ke file yang diupload (dari attached file)",
                        },
                        "no_file": {
                            "type": "boolean",
                            "description": "true jika user tidak mau upload file (mode manual)",
                        },
                        "statement_start_date": {
                            "type": "string",
                            "description": "Tanggal awal statement (YYYY-MM-DD)",
                        },
                        "statement_end_date": {
                            "type": "string",
                            "description": "Tanggal akhir statement (YYYY-MM-DD)",
                        },
                        "document_id": {
                            "type": "string",
                            "description": "UUID dokumen yang akan di-review (untuk document_review workflow)",
                        },
                    },
                },
            },
            "required": ["workflow_type", "user_data"],
        },
    },
    {
        "name": "cancel_workflow",
        "description": (
            "Batalkan workflow rekonsiliasi yang sedang aktif. "
            "Panggil ini kalau user bilang 'batalkan', 'stop', 'cancel', atau 'berhenti'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_type": {
                    "type": "string",
                    "enum": ["bank_reconciliation", "document_review"],
                    "description": "Tipe workflow yang dibatalkan",
                    "default": "bank_reconciliation",
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_document_context",
            "description": "Update data dokumen aktif berdasarkan koreksi user. Gunakan ketika user mengoreksi vendor, item, total, atau data lain dari dokumen yang sedang di-review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "object",
                        "description": "Koreksi data dokumen. Contoh: {\"vendor_name\": \"PT X\"} atau {\"total_amount\": 5000000} atau {\"items\": {\"0\": {\"qty\": 5}}}"
                    }
                },
                "required": ["edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_document_context",
            "description": "Update data dokumen aktif berdasarkan koreksi user. Gunakan ketika user mengoreksi vendor, item, total, atau data lain dari dokumen yang sedang di-review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "object",
                        "description": "Koreksi data dokumen. Contoh: {\"vendor_name\": \"PT X\"} atau {\"total_amount\": 5000000} atau {\"items\": {\"0\": {\"qty\": 5}}}"
                    }
                },
                "required": ["edits"]
            }
        }
    },
]


# ─── Query Tools ──────────────────────────────────────────────────────────────
# Read-only financial intelligence queries — no mutations


def _build_query_tool_definition() -> Dict[str, Any]:
    """Build execute_query tool definition dynamically from QUERY_ACTIONS registry."""
    query_keys = list(QUERY_ACTIONS.keys())
    descriptions = []
    for key, config in QUERY_ACTIONS.items():
        descriptions.append(f"{key}: {config.description}")
    desc_text = " | ".join(descriptions)

    return {
        "name": "execute_query",
        "description": (
            "Execute a read-only financial query OR generate visual chart. Returns formatted data or CHART visualization. "
            "Use for ANY question about financial data: balances, reports, aging, summaries. For CHART/GRAFIK requests, use query keys starting with chart_ (e.g. chart_cash_flow, chart_revenue_expense). "
            "Available queries: " + desc_text
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query_key": {
                    "type": "string",
                    "enum": query_keys,
                    "description": "The query to execute",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Query parameters. Common params: "
                        "start_date/end_date (YYYY-MM-DD), as_of (YYYY-MM-DD), "
                        "periode (YYYY-MM format). "
                        "Dates are auto-filled if not provided."
                    ),
                },
            },
            "required": ["query_key"],
        },
    }


QUERY_TOOL: Dict[str, Any] = _build_query_tool_definition()

# ─── Session Tools ────────────────────────────────────────────────────────────
# Tools that query session-level data (not kernel API, not gRPC)

SESSION_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_session_events",
        "description": (
            "Lihat riwayat aksi dan pencarian yang sudah dilakukan dalam sesi ini. "
            "Gunakan saat user bertanya 'apa yang tadi saya lakukan?' atau untuk "
            "mengingat konteks percakapan sebelumnya."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Jumlah event terakhir (default: 10, max: 20)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "search_chat_history",
        "description": (
            "Cari di riwayat chat sesi sebelumnya. Gunakan saat user menyebut "
            "'yang kemarin', 'yang tadi', atau mereferensi percakapan lama. "
            "Cari berdasarkan kata kunci di event log dan pesan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kata kunci pencarian (nama customer, invoice, aksi, dll)",
                },
                "days_back": {
                    "type": "integer",
                    "description": "Berapa hari ke belakang (default: 7, max: 30)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "review_next_unmatched",
        "description": (
            "Lihat item berikutnya yang belum cocok (unmatched) di session rekonsiliasi. "
            "READ-ONLY — tidak mengubah data apapun. "
            "Mengembalikan: data statement line + saran kecocokan terbaik (jika ada). "
            "Gunakan saat user bilang 'review', 'lihat berikutnya', 'next', 'mulai review'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "ID session rekonsiliasi aktif",
                },
                "skip": {
                    "type": "integer",
                    "description": "Jumlah item yang di-skip (default: 0, untuk lanjut ke item berikutnya)",
                },
            },
            "required": ["session_id"],
        },
    },
]


# --- Tutorial Tools (wired from tutorial_registry.py) ---

TUTORIAL_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_tutorial",
        "description": (
            "Get structured tutorial for guiding user. Returns tutorial config "
            "with steps, prerequisites, and linked actions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tutorial_key": {
                    "type": "string",
                    "description": "Tutorial key, e.g. 'onboarding', 'tutorial_invoicing'",
                },
            },
            "required": ["tutorial_key"],
        },
    },
    {
        "name": "list_tutorials",
        "description": "List all available tutorials with their keys, step counts, and signal words.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "start_tutorial",
        "description": "Start a tutorial for the user. Use this when user asks for tutorial, guidance, ajarin, or how-to. Creates progress record and returns first step.",
        "parameters": {
            "type": "object",
            "properties": {
                "tutorial_key": {
                    "type": "string",
                    "description": "Tutorial key to start",
                },
            },
            "required": ["tutorial_key"],
        },
    },
    {
        "name": "advance_tutorial",
        "description": "Advance the user's active tutorial to the next step. Call after step completion.",
        "parameters": {
            "type": "object",
            "properties": {
                "tutorial_key": {
                    "type": "string",
                    "description": "Tutorial key to advance",
                },
            },
            "required": ["tutorial_key"],
        },
    },
    {
        "name": "dismiss_tutorial",
        "description": "Dismiss/skip a tutorial. Respects cooldown before re-offering.",
        "parameters": {
            "type": "object",
            "properties": {
                "tutorial_key": {
                    "type": "string",
                    "description": "Tutorial key to dismiss",
                },
            },
            "required": ["tutorial_key"],
        },
    },
]


# ─── Registry Functions ────────────────────────────────────────────────────────

# Combined list: all read tools + action tools

# ─── Phase 2A: Tool Domains ──────────────────────────────────────────────────
from enum import Enum

class ToolDomain(str, Enum):
    CORE        = "CORE"
    MASTER_DATA = "MASTER_DATA"
    AR_INVOICES = "AR_INVOICES"
    AP_BILLS    = "AP_BILLS"
    ACCOUNTING  = "ACCOUNTING"
    REPORTS     = "REPORTS"
    BANKING     = "BANKING"
    ACTIONS     = "ACTIONS"
    WORKFLOW    = "WORKFLOW"
    CHARTS      = "CHARTS"
    EXPENSES    = "EXPENSES"
    INVENTORY   = "INVENTORY"
    ANALYTICS   = "ANALYTICS"
    PIPELINE    = "PIPELINE"

# Map each tool → set of domains it belongs to
# CORE tools are loaded for EVERY request
TOOL_DOMAINS: dict = {
    # ── CORE (always loaded) ──
    "execute_query":          {"CORE"},
    "get_dashboard_summary":  {"CORE"},
    "search_customers":       {"CORE", "MASTER_DATA"},
    "search_vendors":         {"CORE", "MASTER_DATA"},
    "search_items":           {"CORE", "MASTER_DATA"},
    "search_accounts":        {"CORE", "ACCOUNTING"},
    "search_bank_accounts":   {"CORE", "BANKING"},

    # ── SESSION ──
    "get_session_events":     {"CORE"},
    "search_chat_history":    {"CORE"},
    "review_next_unmatched":  {"WORKFLOW"},

    # ── TUTORIAL ──
    "get_tutorial":           {"CORE"},
    "list_tutorials":         {"CORE"},
    "start_tutorial":         {"CORE"},
    "advance_tutorial":       {"CORE"},
    "dismiss_tutorial":       {"CORE"},

    # ── MASTER DATA ──
    "get_customer_detail":    {"MASTER_DATA"},
    "get_vendor_detail":      {"MASTER_DATA"},
    "get_item_detail":        {"MASTER_DATA", "INVENTORY"},
    "get_customers":          {"MASTER_DATA"},
    "get_vendors":            {"MASTER_DATA"},
    "get_items":              {"MASTER_DATA", "INVENTORY"},

    # ── AR / INVOICES ──
    "get_invoices":           {"AR_INVOICES"},
    "get_invoice_detail":     {"AR_INVOICES"},
    "get_customer_invoices":  {"AR_INVOICES", "MASTER_DATA"},
    "get_receive_payments":   {"AR_INVOICES"},
    "get_ar_aging":           {"AR_INVOICES", "REPORTS"},
    "get_overdue_invoices":   {"AR_INVOICES"},
    "get_credit_notes":       {"AR_INVOICES"},

    # ── AP / BILLS ──
    "get_bills":              {"AP_BILLS"},
    "get_bill_detail":        {"AP_BILLS"},
    "get_vendor_bills":       {"AP_BILLS", "MASTER_DATA"},
    "get_bill_payments":      {"AP_BILLS"},
    "get_ap_aging":           {"AP_BILLS", "REPORTS"},
    "get_overdue_bills":      {"AP_BILLS"},
    "get_purchase_orders":    {"AP_BILLS", "PIPELINE"},

    # ── EXPENSES ──
    "get_expenses":           {"EXPENSES"},
    "get_expense_detail":     {"EXPENSES"},

    # ── ACCOUNTING ──
    "get_journal_entries":    {"ACCOUNTING"},
    "get_general_ledger":     {"ACCOUNTING"},
    "get_trial_balance":      {"ACCOUNTING", "REPORTS"},
    "get_accounting_periods": {"ACCOUNTING"},
    "get_chart_of_accounts":  {"ACCOUNTING"},

    # ── REPORTS ──
    "get_profit_loss":        {"REPORTS"},
    "get_balance_sheet":      {"REPORTS"},
    "get_cash_flow":          {"REPORTS"},

    # ── BANKING ──
    "get_bank_accounts":      {"BANKING"},
    "get_bank_transactions":  {"BANKING"},
    "get_bank_reconciliation":{"BANKING"},
    "get_bank_transfers":     {"BANKING"},
    "get_bank_transfer_detail":{"BANKING"},
    "get_bank_transfer_summary":{"BANKING"},

    # ── INVENTORY ──
    "get_top_products":       {"INVENTORY", "ANALYTICS"},
    "get_slow_moving_products":{"INVENTORY", "ANALYTICS"},
    "get_product_margins":    {"INVENTORY", "ANALYTICS"},
    "get_stock_adjustments":  {"INVENTORY"},
    "get_stock_adjustment_detail":{"INVENTORY"},

    # ── ANALYTICS ──
    "get_financial_ratios":   {"ANALYTICS", "REPORTS"},
    "get_ratio_dashboard":    {"ANALYTICS", "REPORTS"},
    "get_ratio_trend":        {"ANALYTICS"},
    "get_ratio_alerts":       {"ANALYTICS"},
    "get_budgets":            {"ANALYTICS"},
    "get_budget_detail":      {"ANALYTICS"},
    "get_cost_centers":       {"ANALYTICS"},
    "get_cost_center_summary":{"ANALYTICS"},
    "get_payroll_summary":    {"ANALYTICS"},

    # ── PIPELINE (deposits, cheques, recurring, orders, quotes) ──
    "get_vendor_deposits":    {"PIPELINE", "AP_BILLS"},
    "get_vendor_deposit_detail":{"PIPELINE", "AP_BILLS"},
    "get_customer_deposits":  {"PIPELINE", "AR_INVOICES"},
    "get_customer_deposit_detail":{"PIPELINE", "AR_INVOICES"},
    "get_cheques":            {"PIPELINE", "BANKING"},
    "get_recurring_invoices": {"PIPELINE", "AR_INVOICES"},
    "get_recurring_invoices_due":{"PIPELINE", "AR_INVOICES"},
    "get_recurring_bills":    {"PIPELINE", "AP_BILLS"},
    "get_recurring_bills_due":{"PIPELINE", "AP_BILLS"},
    "get_sales_orders":       {"PIPELINE"},
    "get_sales_order_detail": {"PIPELINE"},
    "get_quotes":             {"PIPELINE"},
    "get_fixed_assets":       {"PIPELINE"},
    "get_fixed_asset_detail": {"PIPELINE"},
    "review_document":        {"WORKFLOW"},

    # ── ACTIONS (propose / simulate) ──
    "propose_action":         {"ACTIONS"},
    "simulate_action":        {"ACTIONS"},
    "propose_direct_action":  {"ACTIONS"},
    "update_document_context": {"ACTIONS"},
    "update_document_context": {"ACTIONS"},

    # ── WORKFLOW ──
    "start_workflow":         {"WORKFLOW"},
    "cancel_workflow":        {"WORKFLOW"},
}


def get_tools_for_domains(domains: set) -> list:
    """Return tool definitions filtered by active domains.

    Args:
        domains: Set of ToolDomain string values (e.g. {"CORE", "BANKING"})

    Returns:
        List of tool definition dicts matching any of the given domains.
    """
    result = []
    seen = set()
    for tool in ALL_TOOLS:
        name = tool["name"]
        if name in seen:
            continue
        tool_domains = TOOL_DOMAINS.get(name)
        if tool_domains is None:
            # Tool not in domain map — include it (safety: don't drop unknown tools)
            result.append(tool)
            seen.add(name)
            continue
        if tool_domains & domains:  # intersection
            result.append(tool)
            seen.add(name)
    return result


ALL_TOOLS: List[Dict[str, Any]] = (
    READ_TOOLS + ACTION_TOOLS + [QUERY_TOOL] + SESSION_TOOLS + TUTORIAL_TOOLS
)

# Set of tool names that are action tools (handled by gRPC, not httpx)
ACTION_TOOL_NAMES = {
    "propose_action",
    "simulate_action",
    "propose_direct_action",
    "execute_query",
}

# Set of tool names that are session tools (handled by session_manager, not API)
SESSION_TOOL_NAMES = {
    "get_session_events",
    "search_chat_history",
    "review_next_unmatched",
    "start_workflow",
    "cancel_workflow",
}

# Set of tool names that are tutorial tools (DB-backed, no session_manager needed)
TUTORIAL_TOOL_NAMES = {
    "get_tutorial",
    "list_tutorials",
    "start_tutorial",
    "advance_tutorial",
    "dismiss_tutorial",
}

# Set of all valid tool names
ALL_TOOL_NAMES = {t["name"] for t in ALL_TOOLS}


def get_tools() -> List[Dict[str, Any]]:
    """Return tool definitions in JSON Schema standard format (provider-agnostic)."""
    return ALL_TOOLS


def get_tools_for_openai() -> List[Dict[str, Any]]:
    """Return tool definitions in OpenAI function calling format (backward compat)."""
    return [{"type": "function", "function": tool} for tool in ALL_TOOLS]


def get_endpoint_for_tool(tool_name: str) -> Dict[str, str] | None:
    """Get HTTP endpoint info for a read tool."""
    return TOOL_ENDPOINTS.get(tool_name)


def is_action_tool(tool_name: str) -> bool:
    """Check if tool is an action tool (handled via gRPC)."""
    return tool_name in ACTION_TOOL_NAMES


def is_session_tool(tool_name: str) -> bool:
    """Check if tool is a session tool (handled by session_manager)."""
    return tool_name in SESSION_TOOL_NAMES


def is_tutorial_tool(tool_name: str) -> bool:
    """Check if tool is a tutorial tool (DB-backed, not session_manager)."""
    return tool_name in TUTORIAL_TOOL_NAMES


def is_valid_tool(tool_name: str) -> bool:
    """Check if tool name exists in registry."""
    return tool_name in ALL_TOOL_NAMES


def get_action_type_enum(action_type: str) -> int | None:
    """Map action type string to proto enum integer."""
    return ACTION_TYPE_MAP.get(action_type)


def is_direct_action_tool(tool_name: str) -> bool:
    """Check if tool is a direct action tool (handled internally, not gRPC)."""
    return tool_name == "propose_direct_action"
