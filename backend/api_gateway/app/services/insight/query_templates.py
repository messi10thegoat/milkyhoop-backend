"""
Query templates for the insight module.

Each template maps Indonesian keyword patterns to REST API endpoints.
This is a READ-ONLY module — all queries use GET requests.
"""

QUERY_TEMPLATES = {
    # ── VENDORS ──────────────────────────────────────────────────────
    "VENDOR_LIST": {
        "patterns": [
            "daftar vendor",
            "list vendor",
            "vendor apa saja",
            "lihat vendor",
            "semua vendor",
            "vendor yang terdaftar",
        ],
        "entity_type": "vendor",
        "api_endpoint": "/api/vendors",
        "response_type": "list",
        "narrator_hint": "Daftar vendor",
    },
    "VENDOR_COUNT": {
        "patterns": [
            "berapa vendor",
            "jumlah vendor",
            "total vendor",
            "ada berapa vendor",
            "vendor ada berapa",
            "berapa vendor yang terdaftar",
        ],
        "entity_type": "vendor",
        "api_endpoint": "/api/vendors",
        "response_type": "count",
        "narrator_hint": "Jumlah vendor",
    },
    "VENDOR_SEARCH": {
        "patterns": [
            "cari vendor",
            "vendor yang namanya",
            "vendor bernama",
            "ada vendor",
        ],
        "entity_type": "vendor",
        "api_endpoint": "/api/vendors",
        "response_type": "search",
        "narrator_hint": "Hasil pencarian vendor",
    },
    # ── CUSTOMERS ────────────────────────────────────────────────────
    "CUSTOMER_LIST": {
        "patterns": [
            "daftar pelanggan",
            "list pelanggan",
            "list customer",
            "semua pelanggan",
            "lihat pelanggan",
            "pelanggan yang terdaftar",
            "daftar customer",
        ],
        "entity_type": "customer",
        "api_endpoint": "/api/customers",
        "response_type": "list",
        "narrator_hint": "Daftar pelanggan",
    },
    "CUSTOMER_COUNT": {
        "patterns": [
            "berapa pelanggan",
            "jumlah pelanggan",
            "total pelanggan",
            "ada berapa customer",
            "ada berapa pelanggan",
            "pelanggan ada berapa",
            "berapa pelanggan yang terdaftar",
            "berapa customer yang terdaftar",
        ],
        "entity_type": "customer",
        "api_endpoint": "/api/customers",
        "response_type": "count",
        "narrator_hint": "Jumlah pelanggan",
    },
    "CUSTOMER_SEARCH": {
        "patterns": [
            "cari pelanggan",
            "pelanggan yang namanya",
            "customer bernama",
            "ada pelanggan",
            "cari customer",
        ],
        "entity_type": "customer",
        "api_endpoint": "/api/customers",
        "response_type": "search",
        "narrator_hint": "Hasil pencarian pelanggan",
    },
    # ── PRODUCTS / ITEMS ─────────────────────────────────────────────
    "PRODUCT_LIST": {
        "patterns": [
            "daftar produk",
            "list produk",
            "list barang",
            "semua produk",
            "lihat barang",
            "daftar barang dan jasa",
            "produk apa saja",
            "barang apa saja",
            "daftar item",
            "master data barang",
            "semua barang dan jasa",
            "semua barang",
            "lihat barang dan jasa",
            "master data produk",
            "data barang",
            "data produk",
            "lihat semua barang",
            "lihat semua produk",
        ],
        "entity_type": "product",
        "api_endpoint": "/api/items",
        "response_type": "list",
        "narrator_hint": "Daftar produk",
    },
    "PRODUCT_COUNT": {
        "patterns": [
            "berapa produk",
            "jumlah produk",
            "total barang",
            "ada berapa item",
            "berapa barang",
            "produk ada berapa",
            "barang ada berapa",
            "total produk",
            "berapa produk yang terdaftar",
            "berapa barang yang terdaftar",
        ],
        "entity_type": "product",
        "api_endpoint": "/api/items",
        "response_type": "count",
        "narrator_hint": "Jumlah produk",
    },
    "PRODUCT_SEARCH": {
        "patterns": [
            "cari produk",
            "produk yang namanya",
            "barang bernama",
            "cek stok",
            "cari barang",
            "cari item",
            "ada produk",
            "ada barang",
        ],
        "entity_type": "product",
        "api_endpoint": "/api/items",
        "response_type": "search",
        "narrator_hint": "Hasil pencarian produk",
    },
    # ── BILLS (hutang) ───────────────────────────────────────────────
    "BILL_LIST": {
        "patterns": [
            "daftar tagihan",
            "list hutang",
            "tagihan apa saja",
            "lihat hutang",
            "daftar faktur pembelian",
            "faktur pembelian",
            "purchase invoice",
        ],
        "entity_type": "bill",
        "api_endpoint": "/api/bills",
        "response_type": "list",
        "narrator_hint": "Daftar tagihan pembelian",
    },
    "BILL_COUNT": {
        "patterns": [
            "berapa tagihan",
            "jumlah hutang",
            "total tagihan",
            "ada berapa hutang",
            "berapa faktur pembelian",
        ],
        "entity_type": "bill",
        "api_endpoint": "/api/bills",
        "response_type": "count",
        "narrator_hint": "Jumlah tagihan",
    },
    "BILL_OUTSTANDING": {
        "patterns": [
            "hutang belum dibayar",
            "sisa hutang",
            "outstanding hutang",
            "total hutang",
            "hutang belum lunas",
            "tagihan belum dibayar",
        ],
        "entity_type": "bill",
        "api_endpoint": "/api/bills",
        "response_type": "sum",
        "sum_field": "amount_due",
        "api_params": {"status": "unpaid"},
        "narrator_hint": "Total hutang belum dibayar",
    },

    # ═══════ PIUTANG (Receivables) ═══════
    "PIUTANG_SUMMARY": {
        "patterns": [
            "total piutang", "piutang berapa", "sisa piutang",
            "ringkasan piutang", "ada piutang", "piutang saat ini",
        ],
        "entity_type": "piutang",
        "api_endpoint": "/api/dashboard/piutang",
        "response_type": "dashboard",
        "narrator_hint": "Ringkasan piutang",
    },
    "PIUTANG_OVERDUE": {
        "patterns": [
            "piutang jatuh tempo", "overdue piutang", "piutang telat",
            "invoice overdue", "faktur jatuh tempo", "tagihan jatuh tempo pelanggan",
        ],
        "entity_type": "piutang",
        "api_endpoint": "/api/dashboard/overdue-invoices",
        "response_type": "list",
        "narrator_hint": "Piutang jatuh tempo",
    },
    "CUSTOMER_WITH_PIUTANG": {
        "patterns": [
            "pelanggan yang punya piutang", "customer ada piutang",
            "siapa yang belum bayar", "pelanggan belum bayar",
            "pelanggan yang masih ada piutang", "ada pelanggan piutang",
            "pelanggan piutang", "customer piutang",
        ],
        "entity_type": "customer",
        "api_endpoint": "/api/customers",
        "response_type": "filter",
        "filter_field": "outstanding_balance",
        "filter_op": "gt",
        "filter_value": 0,
        "narrator_hint": "Pelanggan dengan piutang",
    },

    # ═══════ HUTANG (Payables) ═══════
    "HUTANG_SUMMARY": {
        "patterns": [
            "ringkasan hutang", "hutang berapa total",
            "sisa hutang semua", "hutang saat ini",
        ],
        "entity_type": "hutang",
        "api_endpoint": "/api/dashboard/hutang",
        "response_type": "dashboard",
        "narrator_hint": "Ringkasan hutang",
    },

    # ═══════ SALES INVOICES (Faktur Penjualan) ═══════
    "INVOICE_LIST": {
        "patterns": [
            "daftar faktur penjualan", "list invoice", "faktur penjualan",
            "daftar invoice penjualan", "semua faktur penjualan",
            "lihat faktur penjualan", "sales invoice",
        ],
        "entity_type": "invoice",
        "api_endpoint": "/api/sales-invoices",
        "response_type": "list",
        "narrator_hint": "Daftar faktur penjualan",
    },
    "INVOICE_COUNT": {
        "patterns": [
            "berapa faktur penjualan", "jumlah invoice penjualan",
            "total faktur penjualan", "ada berapa faktur penjualan",
        ],
        "entity_type": "invoice",
        "api_endpoint": "/api/sales-invoices",
        "response_type": "count",
        "narrator_hint": "Jumlah faktur penjualan",
    },
    "INVOICE_SEARCH": {
        "patterns": [
            "cari faktur penjualan", "faktur nomor", "invoice nomor",
            "cari invoice", "faktur penjualan yang",
        ],
        "entity_type": "invoice",
        "api_endpoint": "/api/sales-invoices",
        "response_type": "search",
        "narrator_hint": "Hasil pencarian faktur penjualan",
    },

    # ═══════ PAYMENTS RECEIVED (Pembayaran Diterima) ═══════
    "PAYMENT_RECEIVED_LIST": {
        "patterns": [
            "pembayaran diterima", "pembayaran masuk", "pembayaran dari pelanggan",
            "siapa yang sudah bayar", "daftar pembayaran diterima",
            "pembayaran terakhir", "riwayat pembayaran masuk",
        ],
        "entity_type": "payment_received",
        "api_endpoint": "/api/receive-payments",
        "response_type": "list",
        "narrator_hint": "Daftar pembayaran diterima",
    },
    "PAYMENT_RECEIVED_COUNT": {
        "patterns": [
            "berapa pembayaran diterima", "jumlah pembayaran masuk",
            "total pembayaran diterima",
        ],
        "entity_type": "payment_received",
        "api_endpoint": "/api/receive-payments",
        "response_type": "count",
        "narrator_hint": "Jumlah pembayaran diterima",
    },

    # ═══════ PAYMENTS MADE (Pembayaran Keluar) ═══════
    "PAYMENT_MADE_LIST": {
        "patterns": [
            "pembayaran keluar", "pembayaran ke vendor", "pembayaran ke supplier",
            "daftar pembayaran keluar", "riwayat pembayaran keluar",
        ],
        "entity_type": "payment_made",
        "api_endpoint": "/api/bill-payments",
        "response_type": "list",
        "narrator_hint": "Daftar pembayaran keluar",
    },
    "PAYMENT_MADE_COUNT": {
        "patterns": [
            "berapa pembayaran keluar", "jumlah pembayaran vendor",
            "total pembayaran keluar",
        ],
        "entity_type": "payment_made",
        "api_endpoint": "/api/bill-payments",
        "response_type": "count",
        "narrator_hint": "Jumlah pembayaran keluar",
    },

    # ═══════ EXPENSES (Biaya/Pengeluaran) ═══════
    "EXPENSE_LIST": {
        "patterns": [
            "daftar biaya", "daftar pengeluaran", "list expense",
            "rincian pengeluaran", "rincian biaya", "lihat biaya",
            "lihat pengeluaran", "semua pengeluaran",
        ],
        "entity_type": "expense",
        "api_endpoint": "/api/expenses",
        "response_type": "list",
        "narrator_hint": "Daftar pengeluaran/biaya",
    },
    "EXPENSE_COUNT": {
        "patterns": [
            "berapa pengeluaran", "jumlah biaya", "total biaya",
            "total pengeluaran", "biaya berapa",
        ],
        "entity_type": "expense",
        "api_endpoint": "/api/expenses",
        "response_type": "count",
        "narrator_hint": "Jumlah pengeluaran",
    },
    "EXPENSE_SUMMARY": {
        "patterns": [
            "ringkasan biaya", "top pengeluaran", "biaya terbesar",
            "pengeluaran terbesar", "kategori biaya", "biaya per kategori",
        ],
        "entity_type": "expense",
        "api_endpoint": "/api/dashboard/top-expenses",
        "response_type": "dashboard",
        "narrator_hint": "Ringkasan pengeluaran terbesar",
    },
}
