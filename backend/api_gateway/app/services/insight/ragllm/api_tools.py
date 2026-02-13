"""
API Tools Definition for LLM Function Calling.

Each tool maps to a READ-ONLY GET endpoint on the internal API.
Iron Law 0/10: LLM NEVER writes data. All tools are GET-only.

Format follows OpenAI function calling spec.
"""

from typing import Any, Dict, List


# Tool definitions for OpenAI function calling
API_TOOLS: List[Dict[str, Any]] = [
    # ===== REPORTS =====
    {
        "type": "function",
        "function": {
            "name": "get_profit_loss",
            "description": "Dapatkan laporan laba rugi (profit & loss / income statement). Menunjukkan pendapatan, beban, dan laba/rugi bersih untuk periode tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal periode (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir periode (YYYY-MM-DD)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance_sheet",
            "description": "Dapatkan neraca (balance sheet). Menunjukkan aset, kewajiban, dan ekuitas pada tanggal tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_of_date": {"type": "string", "description": "Tanggal neraca (YYYY-MM-DD). Default: hari ini."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_flow",
            "description": "Dapatkan laporan arus kas (cash flow statement). Menunjukkan arus kas masuk dan keluar dari operasi, investasi, dan pendanaan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trial_balance",
            "description": "Dapatkan neraca saldo (trial balance). Menunjukkan saldo debit dan kredit semua akun.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_of_date": {"type": "string", "description": "Tanggal (YYYY-MM-DD). Default: hari ini."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ar_aging",
            "description": "Dapatkan aging piutang (accounts receivable aging). Menunjukkan piutang yang belum dibayar berdasarkan umur (current, 1-30, 31-60, 61-90, >90 hari).",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_of_date": {"type": "string", "description": "Tanggal (YYYY-MM-DD). Default: hari ini."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ap_aging",
            "description": "Dapatkan aging hutang (accounts payable aging). Menunjukkan hutang yang belum dibayar berdasarkan umur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_of_date": {"type": "string", "description": "Tanggal (YYYY-MM-DD). Default: hari ini."},
                },
                "required": [],
            },
        },
    },

    # ===== DASHBOARD =====
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_summary",
            "description": "Dapatkan ringkasan dashboard: total pendapatan, pengeluaran, laba bersih, kas tersedia, piutang, hutang.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "Periode: '7d' (7 hari), '30d' (30 hari), 'month' (bulan ini). Default: 'month'."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_piutang",
            "description": "Dapatkan ringkasan piutang dari dashboard: total piutang, piutang jatuh tempo, aging breakdown per pelanggan.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_hutang",
            "description": "Dapatkan ringkasan hutang dari dashboard: total hutang, hutang jatuh tempo, aging breakdown per vendor.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_kas_bank",
            "description": "Dapatkan saldo kas dan bank: daftar rekening bank beserta saldo masing-masing, total kas tersedia.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_flow_trends",
            "description": "Dapatkan tren arus kas bulanan: kas masuk vs kas keluar per bulan, tren naik/turun.",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "Jumlah bulan ke belakang. Default: 6."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_expenses",
            "description": "Dapatkan pengeluaran/biaya terbesar: daftar kategori pengeluaran diurutkan dari yang terbesar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 10."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overdue_invoices",
            "description": "Dapatkan faktur penjualan yang jatuh tempo (overdue): daftar invoice yang belum dibayar melewati tanggal jatuh tempo.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overdue_bills",
            "description": "Dapatkan faktur pembelian yang jatuh tempo (overdue): daftar bill yang belum dibayar melewati tanggal jatuh tempo.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    # ===== TRANSACTIONS =====
    {
        "type": "function",
        "function": {
            "name": "get_sales_invoices",
            "description": "Dapatkan daftar faktur penjualan (sales invoices). Bisa difilter berdasarkan status, pelanggan, atau tanggal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter status: 'draft', 'sent', 'paid', 'partial', 'overdue', 'void'"},
                    "customer_id": {"type": "string", "description": "Filter berdasarkan ID pelanggan (UUID)"},
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "search": {"type": "string", "description": "Cari berdasarkan nomor invoice atau nama pelanggan"},
                    "limit": {"type": "integer", "description": "Jumlah item per halaman. Default: 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bills",
            "description": "Dapatkan daftar faktur pembelian (purchase bills). Bisa difilter berdasarkan status, vendor, atau tanggal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter status: 'draft', 'approved', 'paid', 'partial', 'overdue', 'void'"},
                    "vendor_id": {"type": "string", "description": "Filter berdasarkan ID vendor (UUID)"},
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "search": {"type": "string", "description": "Cari berdasarkan nomor bill atau nama vendor"},
                    "limit": {"type": "integer", "description": "Jumlah item per halaman. Default: 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_receive_payments",
            "description": "Dapatkan daftar pembayaran diterima (receive payments / penerimaan). Pembayaran dari pelanggan untuk faktur penjualan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bill_payments",
            "description": "Dapatkan daftar pembayaran ke vendor (bill payments / pengeluaran). Pembayaran untuk faktur pembelian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_expenses",
            "description": "Dapatkan daftar pengeluaran/biaya (expenses). Biaya operasional yang tidak terkait pembelian barang.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "category": {"type": "string", "description": "Filter kategori pengeluaran"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journals",
            "description": "Dapatkan daftar jurnal (journal entries). Catatan akuntansi debit/kredit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir (YYYY-MM-DD)"},
                    "source_type": {"type": "string", "description": "Filter sumber: 'SALES_INVOICE', 'PURCHASE_INVOICE', 'PAYMENT_RECEIVED', 'PAYMENT_MADE', 'EXPENSE', 'MANUAL_JOURNAL'"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 20."},
                },
                "required": [],
            },
        },
    },

    # ===== MASTER DATA =====
    {
        "type": "function",
        "function": {
            "name": "get_accounts",
            "description": "Dapatkan daftar akun (chart of accounts). Daftar akun akuntansi beserta tipe dan saldonya.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_type": {"type": "string", "description": "Filter tipe akun: 'asset', 'liability', 'equity', 'revenue', 'expense'"},
                    "search": {"type": "string", "description": "Cari berdasarkan nama atau kode akun"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customers",
            "description": "Dapatkan daftar pelanggan (customers). Termasuk informasi kontak dan saldo piutang.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Cari berdasarkan nama pelanggan"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 50."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vendors",
            "description": "Dapatkan daftar vendor/supplier. Termasuk informasi kontak dan saldo hutang.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Cari berdasarkan nama vendor"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 50."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_items",
            "description": "Dapatkan daftar barang dan jasa (products/items). Termasuk harga jual, harga beli, dan stok.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Cari berdasarkan nama produk"},
                    "item_type": {"type": "string", "description": "Filter tipe: 'product', 'service'"},
                    "limit": {"type": "integer", "description": "Jumlah item. Default: 50."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bank_accounts",
            "description": "Dapatkan daftar rekening bank beserta saldo. Termasuk kas tunai dan rekening bank.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    # ===== ANALYTICS =====
    {
        "type": "function",
        "function": {
            "name": "get_financial_ratios",
            "description": "Dapatkan rasio keuangan: current ratio, quick ratio, debt-to-equity, profit margin, ROA, ROE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "as_of_date": {"type": "string", "description": "Tanggal (YYYY-MM-DD). Default: hari ini."},
                },
                "required": [],
            },
        },
    },
]


# Mapping: function_name → API endpoint + method
TOOL_ENDPOINTS = {
    # Reports
    "get_profit_loss":      {"method": "GET", "path": "/api/reports/profit-loss"},
    "get_balance_sheet":    {"method": "GET", "path": "/api/reports/balance-sheet"},
    "get_cash_flow":        {"method": "GET", "path": "/api/reports/cash-flow"},
    "get_trial_balance":    {"method": "GET", "path": "/api/reports/trial-balance"},
    "get_ar_aging":         {"method": "GET", "path": "/api/reports/ar-aging"},
    "get_ap_aging":         {"method": "GET", "path": "/api/reports/ap-aging"},
    # Dashboard
    "get_dashboard_summary":  {"method": "GET", "path": "/api/dashboard/summary"},
    "get_dashboard_piutang":  {"method": "GET", "path": "/api/dashboard/piutang"},
    "get_dashboard_hutang":   {"method": "GET", "path": "/api/dashboard/hutang"},
    "get_dashboard_kas_bank": {"method": "GET", "path": "/api/dashboard/kas-bank"},
    "get_cash_flow_trends":   {"method": "GET", "path": "/api/dashboard/cash-flow-trends"},
    "get_top_expenses":       {"method": "GET", "path": "/api/dashboard/top-expenses"},
    "get_overdue_invoices":   {"method": "GET", "path": "/api/dashboard/overdue-invoices"},
    "get_overdue_bills":      {"method": "GET", "path": "/api/dashboard/overdue-bills"},
    # Transactions
    "get_sales_invoices":   {"method": "GET", "path": "/api/sales-invoices"},
    "get_bills":            {"method": "GET", "path": "/api/bills"},
    "get_receive_payments": {"method": "GET", "path": "/api/receive-payments"},
    "get_bill_payments":    {"method": "GET", "path": "/api/bill-payments"},
    "get_expenses":         {"method": "GET", "path": "/api/expenses"},
    "get_journals":         {"method": "GET", "path": "/api/journals"},
    # Master Data
    "get_accounts":       {"method": "GET", "path": "/api/accounts"},
    "get_customers":      {"method": "GET", "path": "/api/customers"},
    "get_vendors":        {"method": "GET", "path": "/api/vendors"},
    "get_items":          {"method": "GET", "path": "/api/items"},
    "get_bank_accounts":  {"method": "GET", "path": "/api/bank-accounts"},
    # Analytics
    "get_financial_ratios": {"method": "GET", "path": "/api/analytics/financial-ratios"},
}


def get_tools_for_openai() -> List[Dict[str, Any]]:
    """Return tool definitions formatted for OpenAI function calling."""
    return API_TOOLS


def get_endpoint_for_tool(tool_name: str) -> Dict[str, str] | None:
    """Lookup the API endpoint for a tool name."""
    return TOOL_ENDPOINTS.get(tool_name)


def get_tool_names() -> List[str]:
    """Return all available tool names."""
    return list(TOOL_ENDPOINTS.keys())
