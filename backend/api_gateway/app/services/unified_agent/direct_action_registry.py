"""
DirectAction Registry — Lightweight conversational actions via REST endpoints.

Single source of truth for:
- Field specs (validation, defaults)
- UX metadata (display names, messages, trust context)
- REST endpoint routing

IRON LAWS COMPLIANCE GUARD:
DirectAction is a thin REST caller. It does NOT add accounting protections.
Any action whose endpoint creates journal entries MUST have the endpoint itself
enforce all Iron Laws (Law 4 double-entry, Law 6 source traceability, Law 20 hash
chaining, Law 22 sequence integrity, Law 23 transaction atomicity).
The `creates_journal` flag serves as documentation and code-review signal.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class FieldSpec:
    name: str
    label: str
    field_type: str = "string"  # string | number | boolean | enum | date
    required: bool = False
    default: Optional[str] = None
    options: list[str] = field(default_factory=list)  # for enum type
    description: str = ""
    hidden: bool = False         # In payload, NOT shown in confirmation table
    display_only: bool = False   # Shown in table, stripped before REST call


@dataclass
class ImpactRule:
    """Conditional trust context shown below the confirmation table."""
    field: str                # payload field to check
    condition: str            # "zero" | "nonzero" | "always"
    message_template: str     # Python format string, e.g. "Saldo {formatted_value}"

    def evaluate(self, payload: dict) -> Optional[str]:
        """Evaluate this rule against payload. Returns message or None."""
        value = payload.get(self.field, 0)
        try:
            numeric = int(value) if value else 0
        except (ValueError, TypeError):
            numeric = 0

        if self.condition == "zero" and numeric != 0:
            return None
        if self.condition == "nonzero" and numeric == 0:
            return None

        formatted_value = f"Rp {numeric:,}".replace(",", ".")
        return self.message_template.format(
            value=value,
            formatted_value=formatted_value,
        )


@dataclass
class CategoryMapping:
    """Maps a field value to an accounting category label."""
    field: str   # payload field (e.g. "account_type")
    mapping: dict[str, str] = field(default_factory=dict)
    default: str = ""


@dataclass
class PreFlightCheck:
    """Pre-flight check before proposing an action to user."""
    endpoint: str                    # "/api/items/{id}/can-delete"
    fail_action: str                 # "reject" | "suggest_alternative" | "warn"
    fail_message_template: str       # "{name} sudah punya transaksi..."
    alternatives: list[str] = field(default_factory=list)


import re as _re


def _safe_format(template: str, payload: dict, **extra) -> str:
    """Format template with payload + extra kwargs. Missing keys -> placeholder stripped."""
    fmt_kwargs = {**{k: v for k, v in payload.items() if isinstance(v, (str, int, float))}, **extra}
    try:
        return template.format(**fmt_kwargs)
    except KeyError:
        # Strip any remaining {placeholder} patterns
        return _re.sub(r'\{[^}]+\}', '', template).strip()


@dataclass
class QueryParam:
    """Parameter specification for query actions."""
    name: str           # query param key
    label: str          # display label
    param_type: str = "string"  # string | date | enum | number
    required: bool = False
    default: str = ""


@dataclass
class QueryActionConfig:
    """Read-only query configuration — no mutations, no confirmation flow."""
    action_key: str
    display_name: str
    rest_endpoint: str          # GET endpoint
    rest_method: str = "GET"
    response_format: str = "summary"  # single_value | summary | table | list
    signal_words: list[str] = field(default_factory=list)
    query_params: list[QueryParam] = field(default_factory=list)
    description: str = ""       # for LLM tool description


@dataclass
class ChartQueryConfig(QueryActionConfig):
    """Query config that returns CHART message_type with visual chart data."""
    chart_type: str = "bar"              # line | bar | area | pie | donut | horizontal_bar
    complexity_hint: str = "simple"      # simple -> inline, complex -> artifact
    chart_features: dict = field(default_factory=dict)  # {brush: True, legend_toggle: True}


@dataclass
class DirectActionConfig:
    action_key: str
    display_name: str
    rest_endpoint: str
    rest_method: str = "POST"
    entity_type: str = ""
    risk_level: str = "low"  # low | medium | high
    creates_journal: bool = False  # Iron Laws flag
    ttl_seconds: int = 300  # 5 minutes default
    fields: list[FieldSpec] = field(default_factory=list)

    # --- Integration Metadata (auto-wires intent detection + event dispatch) ---
    action_type_key: str = ""        # uppercase key, e.g. "CREATE_BANK_ACCOUNT" (for event dispatch)
    signal_words: list[str] = field(default_factory=list)  # triggers DIRECT ACTION mode in intent_bias

    # --- UX Metadata (scalable, no hardcoding in build functions) ---
    entity_name_field: str = "name"  # which payload field = entity display name
    loading_message_template: str = "Memproses\u2026"  # e.g. "Membuat akun {entity_name}\u2026"
    success_message_template: str = "Berhasil dibuat."  # e.g. "Akun \'{entity_name}\' berhasil dibuat."
    category: Optional[CategoryMapping] = None  # accounting category in confirm table
    impact_rules: list[ImpactRule] = field(default_factory=list)  # trust context rules
    pre_flight_checks: list[PreFlightCheck] = field(default_factory=list)
    journal_preview_endpoint: str = ""  # POST endpoint for journal preview (creates_journal actions)

    def get_entity_name(self, payload: dict) -> str:
        """Extract entity display name from payload."""
        return str(payload.get(self.entity_name_field, "")).strip()

    def get_loading_message(self, payload: dict) -> str:
        """Build loading message for confirming state."""
        name = self.get_entity_name(payload)
        return _safe_format(self.loading_message_template, payload, entity_name=name)

    def get_success_message(self, payload: dict) -> str:
        """Build success message after action completes."""
        name = self.get_entity_name(payload)
        return _safe_format(self.success_message_template, payload, entity_name=name)

    def get_category_label(self, payload: dict) -> Optional[str]:
        """Get accounting category label from payload, if configured."""
        if not self.category:
            return None
        field_value = str(payload.get(self.category.field, ""))
        return self.category.mapping.get(field_value, self.category.default) or None

    def get_impact_notes(self, payload: dict) -> list[str]:
        """Evaluate all impact rules and return applicable messages."""
        notes = []
        for rule in self.impact_rules:
            msg = rule.evaluate(payload)
            if msg:
                notes.append(msg)
        return notes


# \u2500\u2500\u2500 Registry \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

DIRECT_ACTIONS: dict[str, DirectActionConfig] = {
    "create_bank_account": DirectActionConfig(
        action_key="create_bank_account",
        display_name="Buat Akun Kas & Bank",
        rest_endpoint="/api/bank-accounts",
        rest_method="POST",
        entity_type="bank_account",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_BANK_ACCOUNT",
        signal_words=["rekening", "bank account", "akun bank", "kas toko",
                       "buat rekening", "bikin rekening", "akun kas"],
        entity_name_field="account_name",
        loading_message_template="Membuat akun {entity_name}\u2026",
        success_message_template="Akun \'{entity_name}\' berhasil dibuat.",
        category=CategoryMapping(
            field="account_type",
            mapping={
                "cash": "Aset \u2192 Kas & Setara Kas",
                "bank": "Aset \u2192 Kas & Setara Kas",
                "petty_cash": "Aset \u2192 Kas Kecil",
                "e_wallet": "Aset \u2192 Kas & Setara Kas",
                "credit_card": "Liabilitas \u2192 Utang Kartu Kredit",
            },
            default="Aset \u2192 Kas & Setara Kas",
        ),
        impact_rules=[
            ImpactRule(
                field="opening_balance",
                condition="zero",
                message_template="Saldo awal Rp 0 \u2014 tidak membuat jurnal pembukaan.",
            ),
            ImpactRule(
                field="opening_balance",
                condition="nonzero",
                message_template="Sistem akan membuat jurnal pembukaan {formatted_value}.",
            ),
        ],
        fields=[
            FieldSpec(name="account_name", label="Nama Akun", required=True,
                      description="Nama akun, misal 'Kas Toko' atau 'BCA Utama'"),
            FieldSpec(name="account_type", label="Tipe Akun", field_type="enum",
                      required=False, default="cash",
                      options=["bank", "cash", "petty_cash", "e_wallet", "credit_card"],
                      description="Jenis rekening"),
            FieldSpec(name="bank_name", label="Nama Bank", required=False,
                      description="Nama bank (BCA, Mandiri, dll). Kosongkan untuk kas."),
            FieldSpec(name="account_number", label="Nomor Rekening", required=False,
                      description="Nomor rekening bank"),
            FieldSpec(name="opening_balance", label="Saldo Awal", field_type="number",
                      required=False, default="0",
                      description="Saldo awal dalam Rupiah"),
            FieldSpec(name="currency", label="Mata Uang", field_type="enum",
                      required=False, default="IDR",
                      options=["IDR", "USD", "EUR", "SGD"],
                      description="Mata uang rekening"),
            FieldSpec(name="is_default", label="Akun Utama", field_type="boolean",
                      required=False, default="false",
                      description="Jadikan sebagai rekening utama?"),
            FieldSpec(name="notes", label="Catatan", required=False),
        ],
    ),
    "create_vendor": DirectActionConfig(
        action_key="create_vendor",
        display_name="Buat Vendor/Supplier",
        rest_endpoint="/api/vendors",
        rest_method="POST",
        entity_type="vendor",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_VENDOR",
        signal_words=["vendor baru", "supplier baru", "tambah vendor",
                       "tambah supplier", "buat vendor"],
        entity_name_field="name",
        loading_message_template="Membuat vendor {entity_name}\u2026",
        success_message_template="Vendor \'{entity_name}\' berhasil dibuat.",
        category=None,  # Vendors don't have accounting category
        impact_rules=[],  # No impact rules for vendors
        fields=[
            FieldSpec(name="name", label="Nama Vendor", required=True,
                      description="Nama vendor/supplier"),
            FieldSpec(name="company_name", label="Nama Perusahaan", required=False),
            FieldSpec(name="phone", label="Telepon", required=False),
            FieldSpec(name="phone2", label="Telepon 2", required=False),
            FieldSpec(name="email", label="Email", required=False),
            FieldSpec(name="community", label="Komunitas/Organisasi", required=False),
            FieldSpec(name="address", label="Alamat", required=False),
            FieldSpec(name="tax_id", label="NPWP", required=False),
            FieldSpec(name="notes", label="Catatan", required=False),
        ],
    ),
    "create_customer": DirectActionConfig(
        action_key="create_customer",
        display_name="Buat Pelanggan",
        rest_endpoint="/api/customers",
        rest_method="POST",
        entity_type="customer",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_CUSTOMER",
        signal_words=["pelanggan baru", "customer baru", "tambah pelanggan",
                       "tambah customer", "buat pelanggan", "daftarkan pelanggan"],
        entity_name_field="name",
        loading_message_template="Membuat pelanggan {entity_name}…",
        success_message_template="Pelanggan '{entity_name}' berhasil dibuat.",
        category=None,
        impact_rules=[],
        fields=[
            FieldSpec(name="name", label="Nama Pelanggan", required=True,
                      description="Nama pelanggan"),
            FieldSpec(name="company_name", label="Nama Perusahaan", required=False),
            FieldSpec(name="phone", label="Telepon", required=False),
            FieldSpec(name="phone2", label="Telepon 2", required=False),
            FieldSpec(name="email", label="Email", required=False),
            FieldSpec(name="address", label="Alamat", required=False),
            FieldSpec(name="community", label="Komunitas/Organisasi", required=False),
            FieldSpec(name="tax_id", label="NPWP", required=False),
            FieldSpec(name="notes", label="Catatan", required=False),
        ],
    ),
    "delete_bank_account": DirectActionConfig(
        action_key="delete_bank_account",
        display_name="Hapus Akun Kas & Bank",
        rest_endpoint="/api/bank-accounts/{id}",
        rest_method="DELETE",
        entity_type="bank_account",
        risk_level="medium",
        creates_journal=True,  # May reverse opening balance journal
        ttl_seconds=300,
        action_type_key="DELETE_BANK_ACCOUNT",
        signal_words=["hapus rekening", "hapus akun", "delete account",
                       "hapus kas", "buang akun"],
        entity_name_field="account_name",
        loading_message_template="Menghapus akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil dihapus.",
        category=None,
        impact_rules=[],
        fields=[
            FieldSpec(name="account_id", label="ID Akun", required=True,
                      description="UUID akun yang akan dihapus (dari search)"),
            FieldSpec(name="account_name", label="Nama Akun", required=True,
                      description="Nama akun untuk konfirmasi"),
        ],
    ),



    "create_bill_payment": DirectActionConfig(
        action_key="create_bill_payment",
        display_name="Bayar Faktur Pembelian",
        rest_endpoint="/api/bill-payments",
        rest_method="POST",
        entity_type="bill_payment",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="CREATE_BILL_PAYMENT",
        signal_words=["bayar faktur", "bayar bill", "lunasi faktur", "pembayaran vendor"],
        entity_name_field="vendor_name",
        loading_message_template="Mencatat pembayaran ke {entity_name}…",
        success_message_template="Pembayaran untuk {entity_name} berhasil dicatat.",
        category=None,
        impact_rules=[
            ImpactRule(
                field="total_amount",
                condition="nonzero",
                message_template="Hutang berkurang {formatted_value}, saldo bank berkurang {formatted_value}.",
            ),
        ],
        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(name="vendor_id", label="Vendor ID", required=True, hidden=True),
            FieldSpec(name="bill_id", label="Bill ID", required=True, hidden=True),
            FieldSpec(name="bank_account_id", label="Bank Account ID", required=True, hidden=True),
            FieldSpec(name="session_id", label="Session ID", hidden=True),
            FieldSpec(name="statement_line_id", label="Statement Line ID", hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
            FieldSpec(name="bill_number", label="No. Faktur", display_only=True),
            FieldSpec(name="bank_account_name", label="Dari Rekening", display_only=True),
            # Hidden display (backend needs for context, redundant for user)
            FieldSpec(name="bill_amount", label="Total Faktur", field_type="number", hidden=True),
            FieldSpec(name="amount_due", label="Sisa Tagihan", field_type="number", hidden=True),
            FieldSpec(name="statement_description", label="Mutasi Bank", hidden=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="total_amount", label="Jumlah Bayar", field_type="number", required=True),
            FieldSpec(name="payment_date", label="Tanggal", field_type="date", required=True),
            FieldSpec(name="payment_method", label="Metode", default="bank_transfer", hidden=True),
        ],
    ),



    "create_receive_payment": DirectActionConfig(
        action_key="create_receive_payment",
        display_name="Terima Pembayaran Pelanggan",
        rest_endpoint="/api/receive-payments",
        rest_method="POST",
        entity_type="receive_payment",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="CREATE_RECEIVE_PAYMENT",
        signal_words=["terima pembayaran", "pembayaran pelanggan", "bayar piutang", "pelunasan faktur"],
        entity_name_field="customer_name",
        loading_message_template="Mencatat pembayaran dari {entity_name}…",
        success_message_template="Pembayaran dari {entity_name} berhasil dicatat.",
        category=None,
        impact_rules=[
            ImpactRule(
                field="total_amount",
                condition="nonzero",
                message_template="Saldo bank bertambah {formatted_value}, piutang pelanggan berkurang {formatted_value}.",
            ),
        ],
        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(name="customer_id",        label="Customer ID",         required=True, hidden=True),
            FieldSpec(name="bank_account_id",    label="Bank Account ID",     required=True, hidden=True),
            FieldSpec(name="session_id",         label="Session ID",          hidden=True),
            FieldSpec(name="statement_line_id",  label="Statement Line ID",   hidden=True),
            FieldSpec(name="allocations",        label="Allocations",         field_type="json", required=True, hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="customer_name",      label="Pelanggan",           display_only=True),
            FieldSpec(name="invoice_numbers",    label="No. Faktur",          display_only=True),
            FieldSpec(name="bank_account_name",  label="Ke Rekening",         display_only=True),
            FieldSpec(name="statement_description", label="Mutasi Bank",      hidden=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="total_amount",       label="Jumlah Terima",       field_type="number", required=True),
            FieldSpec(name="payment_date",       label="Tanggal",             field_type="date",   required=True),
            FieldSpec(name="payment_method",     label="Metode",              default="bank_transfer", hidden=True),
        ],
    ),
    # ============ TRANSACTION CRUD (Tahap 4) ============
    "create_sales_invoice": DirectActionConfig(
        action_key="create_sales_invoice",
        display_name="Buat Faktur Penjualan",
        rest_endpoint="/api/sales-invoices",
        rest_method="POST",
        entity_type="sales_invoice",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_SALES_INVOICE",
        signal_words=[
            "faktur penjualan", "invoice penjualan", "buat faktur",
            "bikin invoice", "jual ke", "tagih", "buat tagihan",
            "invoice baru", "faktur baru",
        ],
        journal_preview_endpoint="",
        entity_name_field="customer_name",
        loading_message_template="Membuat faktur penjualan untuk {entity_name}\u2026",
        success_message_template="Faktur penjualan untuk '{entity_name}' berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="PPN {formatted_value}% akan dibukukan ke PPN Keluaran.",
            ),
        ],
        fields=[
            FieldSpec(name="customer_id", label="ID Pelanggan", required=True, hidden=True,
                      description="UUID pelanggan — resolve via search_customers"),
            FieldSpec(name="customer_name", label="Pelanggan", required=True),
            FieldSpec(name="invoice_date", label="Tanggal Faktur", field_type="date", required=True),
            FieldSpec(name="due_date", label="Jatuh Tempo", field_type="date", required=True),
            FieldSpec(name="items", label="Item", field_type="json", required=True, hidden=True,
                      description="Array of {item_id, description, quantity, unit_price}"),
            FieldSpec(name="tax_rate", label="Pajak (%)", field_type="percent", default="0"),
            FieldSpec(name="discount_percent", label="Diskon (%)", field_type="percent", default="0"),
            FieldSpec(name="notes", label="Catatan"),
            FieldSpec(name="auto_post", label="Auto Post", default="true", hidden=True),
        ],
    ),
    "create_bill": DirectActionConfig(
        action_key="create_bill",
        display_name="Buat Faktur Pembelian",
        rest_endpoint="/api/bills/v2",
        rest_method="POST",
        entity_type="bill",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_BILL",
        signal_words=[
            "faktur pembelian", "bill", "tagihan masuk", "buat bill",
            "catat pembelian", "beli dari", "faktur supplier", "faktur vendor",
        ],
        journal_preview_endpoint="",
        entity_name_field="vendor_name",
        loading_message_template="Membuat faktur pembelian dari {entity_name}\u2026",
        success_message_template="Faktur pembelian dari '{entity_name}' berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="PPN {formatted_value}% akan dibukukan ke PPN Masukan.",
            ),
        ],
        fields=[
            FieldSpec(name="vendor_id", label="ID Vendor", hidden=True,
                      description="UUID vendor — resolve via search_vendors"),
            FieldSpec(name="vendor_name", label="Vendor", required=True),
            FieldSpec(name="issue_date", label="Tanggal Bill", field_type="date"),
            FieldSpec(name="due_date", label="Jatuh Tempo", field_type="date", required=True),
            FieldSpec(name="invoice_number", label="No. Faktur Vendor"),
            FieldSpec(name="items", label="Item", field_type="json", required=True, hidden=True,
                      description="Array of {product_id, product_name, qty, price, unit}"),
            FieldSpec(name="tax_rate", label="Pajak (%)", field_type="percent", default="0"),
            FieldSpec(name="notes", label="Catatan"),
            FieldSpec(name="status", label="Status", default="posted", hidden=True),
        ],
    ),
    "create_expense": DirectActionConfig(
        action_key="create_expense",
        display_name="Catat Biaya / Pengeluaran",
        rest_endpoint="/api/expenses",
        rest_method="POST",
        entity_type="expense",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_EXPENSE",
        signal_words=[
            "catat biaya", "catat pengeluaran", "bayar biaya",
            "bayar listrik", "bayar sewa", "bayar internet",
            "biaya operasional", "pengeluaran", "expense", "keluar uang",
        ],
        journal_preview_endpoint="",
        entity_name_field="description",
        loading_message_template="Mencatat biaya: {entity_name}\u2026",
        success_message_template="Biaya '{entity_name}' berhasil dicatat.",
        impact_rules=[
            ImpactRule(
                field="amount",
                condition="always",
                message_template="Saldo kas/bank akan berkurang Rp {formatted_value}.",
            ),
        ],
        fields=[
            FieldSpec(name="expense_date", label="Tanggal", field_type="date", required=True),
            FieldSpec(name="paid_through_id", label="Dibayar Dari", required=True, hidden=True,
                      description="UUID akun kas/bank — resolve via search_bank_accounts"),
            FieldSpec(name="paid_through_name", label="Dibayar Dari", display_only=True),
            FieldSpec(name="account_id", label="Akun Biaya", required=True, hidden=True,
                      description="UUID akun biaya — resolve via search_accounts"),
            FieldSpec(name="account_name", label="Akun Biaya", display_only=True),
            FieldSpec(name="amount", label="Jumlah", field_type="number", required=True),
            FieldSpec(name="description", label="Deskripsi", required=True),
            FieldSpec(name="vendor_id", label="ID Vendor", hidden=True),
            FieldSpec(name="vendor_name", label="Vendor"),
            FieldSpec(name="tax_rate", label="Pajak (%)", field_type="percent", default="0"),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "create_journal_entry": DirectActionConfig(
        action_key="create_journal_entry",
        display_name="Buat Jurnal Umum",
        rest_endpoint="/api/journals",
        rest_method="POST",
        entity_type="journal_entry",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="CREATE_JOURNAL_ENTRY",
        signal_words=[
            "jurnal umum", "buat jurnal", "journal entry", "manual journal",
            "jurnal penyesuaian", "adjusting entry", "jurnal koreksi",
        ],
        journal_preview_endpoint="",
        entity_name_field="description",
        loading_message_template="Membuat jurnal umum: {entity_name}\u2026",
        success_message_template="Jurnal umum berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="lines",
                condition="always",
                message_template="\u26a0\ufe0f Jurnal manual. Tidak bisa diubah setelah posting (Law 2). Koreksi = reversal.",
            ),
        ],
        fields=[
            FieldSpec(name="entry_date", label="Tanggal", field_type="date", required=True),
            FieldSpec(name="description", label="Keterangan", required=True),
            FieldSpec(name="lines", label="Baris Jurnal", field_type="json", required=True, hidden=True,
                      description="Array of {account_id, description, debit, credit}. "
                                  "Law 4: total debit HARUS = total credit. Min 2 lines."),
        ],
    ),
    "create_stock_adjustment": DirectActionConfig(
        action_key="create_stock_adjustment",
        display_name="Penyesuaian Stok",
        rest_endpoint="/api/stock-adjustments",
        rest_method="POST",
        entity_type="stock_adjustment",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_STOCK_ADJUSTMENT",
        signal_words=[
            "sesuaikan stok", "stock adjustment", "koreksi stok",
            "tambah stok", "kurangi stok", "penyesuaian persediaan",
            "stok opname", "stock opname",
        ],
        journal_preview_endpoint="",
        entity_name_field="adjustment_type",
        loading_message_template="Menyesuaikan stok\u2026",
        success_message_template="Penyesuaian stok berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="adjustment_type",
                condition="always",
                message_template="Stok akan disesuaikan. Jurnal: Inventory \u2194 Adjustment account.",
            ),
        ],
        fields=[
            FieldSpec(name="adjustment_date", label="Tanggal", field_type="date", required=True),
            FieldSpec(name="adjustment_type", label="Tipe", field_type="enum",
                      options=["increase", "decrease", "recount", "damaged", "expired"], required=True),
            FieldSpec(name="items", label="Item", field_type="json", required=True, hidden=True,
                      description="Array of {product_id, quantity_adjustment, reason_detail}. "
                                  "quantity_adjustment: positive=increase, negative=decrease."),
            FieldSpec(name="storage_location_id", label="Lokasi Penyimpanan", hidden=True),
            FieldSpec(name="reference_no", label="No. Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    # ============ VOID / REVERSAL ACTIONS (Tahap 4) ============
    "void_sales_invoice": DirectActionConfig(
        action_key="void_sales_invoice",
        display_name="Batalkan Faktur Penjualan",
        rest_endpoint="/api/sales-invoices/{id}/void",
        rest_method="POST",
        entity_type="sales_invoice",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_SALES_INVOICE",
        signal_words=["batalkan faktur", "void invoice", "cancel invoice", "batal invoice"],
        entity_name_field="invoice_number",
        loading_message_template="Membatalkan faktur {entity_name}\u2026",
        success_message_template="Faktur '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="invoice_number", label="No. Faktur", display_only=True),
            FieldSpec(name="reason", label="Alasan", required=True),
        ],
    ),
    "void_bill": DirectActionConfig(
        action_key="void_bill",
        display_name="Batalkan Faktur Pembelian",
        rest_endpoint="/api/bills/{id}/void",
        rest_method="POST",
        entity_type="bill",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_BILL",
        signal_words=["batalkan bill", "void bill", "hapus faktur pembelian", "batal bill"],
        entity_name_field="bill_number",
        loading_message_template="Membatalkan bill {entity_name}\u2026",
        success_message_template="Bill '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="bill_number", label="No. Bill", display_only=True),
            FieldSpec(name="reason", label="Alasan", required=True),
        ],
    ),
    "void_receive_payment": DirectActionConfig(
        action_key="void_receive_payment",
        display_name="Batalkan Penerimaan Pembayaran",
        rest_endpoint="/api/receive-payments/{id}/void",
        rest_method="POST",
        entity_type="receive_payment",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_RECEIVE_PAYMENT",
        signal_words=["batalkan pembayaran masuk", "void payment received", "batal terima bayaran"],
        entity_name_field="payment_number",
        loading_message_template="Membatalkan pembayaran {entity_name}\u2026",
        success_message_template="Pembayaran '{entity_name}' berhasil dibatalkan. Invoice kembali outstanding.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="payment_number", label="No. Pembayaran", display_only=True),
            FieldSpec(name="void_reason", label="Alasan", required=True),
        ],
    ),
    "void_bill_payment": DirectActionConfig(
        action_key="void_bill_payment",
        display_name="Batalkan Pembayaran Tagihan",
        rest_endpoint="/api/bill-payments/{id}/void",
        rest_method="POST",
        entity_type="bill_payment",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_BILL_PAYMENT",
        signal_words=["batalkan pembayaran keluar", "void bill payment", "batal bayar tagihan"],
        entity_name_field="payment_number",
        loading_message_template="Membatalkan pembayaran {entity_name}\u2026",
        success_message_template="Pembayaran '{entity_name}' berhasil dibatalkan. Bill kembali outstanding.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="payment_number", label="No. Pembayaran", display_only=True),
            FieldSpec(name="void_reason", label="Alasan", required=True),
        ],
    ),
    "void_expense": DirectActionConfig(
        action_key="void_expense",
        display_name="Batalkan Biaya",
        rest_endpoint="/api/expenses/{id}/void",
        rest_method="POST",
        entity_type="expense",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_EXPENSE",
        signal_words=["batalkan biaya", "void expense", "batal pengeluaran"],
        entity_name_field="description",
        loading_message_template="Membatalkan biaya {entity_name}\u2026",
        success_message_template="Biaya '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="description", label="Deskripsi", display_only=True),
            FieldSpec(name="reason", label="Alasan", required=True),
        ],
    ),
    "reverse_journal": DirectActionConfig(
        action_key="reverse_journal",
        display_name="Balik Jurnal (Reversal)",
        rest_endpoint="/api/journals/{id}/reverse",
        rest_method="POST",
        entity_type="journal_entry",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="REVERSE_JOURNAL",
        signal_words=["balik jurnal", "reverse journal", "batalkan jurnal", "koreksi jurnal"],
        entity_name_field="journal_number",
        loading_message_template="Membalik jurnal {entity_name}\u2026",
        success_message_template="Jurnal '{entity_name}' berhasil dibalik. Jurnal reversal dibuat.",
        impact_rules=[
            ImpactRule(
                field="id",
                condition="always",
                message_template="\u26a0\ufe0f Jurnal asli TIDAK dihapus (Law 2). Sistem buat jurnal baru yang membalik debit \u2194 credit.",
            ),
        ],
        fields=[
            FieldSpec(name="id", label="ID Jurnal", required=True, hidden=True),
            FieldSpec(name="journal_number", label="No. Jurnal", display_only=True),
            FieldSpec(name="reversal_date", label="Tanggal Reversal", field_type="date", required=True),
            FieldSpec(name="reason", label="Alasan Reversal", required=True),
        ],
    ),
    "void_stock_adjustment": DirectActionConfig(
        action_key="void_stock_adjustment",
        display_name="Batalkan Penyesuaian Stok",
        rest_endpoint="/api/stock-adjustments/{id}/void",
        rest_method="POST",
        entity_type="stock_adjustment",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=60,
        action_type_key="VOID_STOCK_ADJUSTMENT",
        signal_words=["batalkan stock adjustment", "batal koreksi stok"],
        entity_name_field="product_name",
        loading_message_template="Membatalkan penyesuaian stok {entity_name}\u2026",
        success_message_template="Penyesuaian stok berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="product_name", label="Produk", display_only=True),
            FieldSpec(name="reason", label="Alasan", required=True),
        ],
    ),
    # ============ BANK RECONCILIATION ============
    # NOTE: agentic_reconcile removed from DirectAction — it is now a regular read tool
    # (defined in tool_registry.py + tool_executor.py) because automatch is analysis, not a write action.
    "confirm_recon_batch": DirectActionConfig(
        action_key="confirm_recon_batch",
        display_name="Konfirmasi Batch Rekonsiliasi",
        rest_endpoint="/api/bank-reconciliation/sessions/{session_id}/confirm-batch",
        rest_method="POST",
        entity_type="reconciliation",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CONFIRM_RECON_BATCH",
        signal_words=["konfirmasi batch", "setuju semua", "terima semua cocokkan"],
        entity_name_field="session_id",
        loading_message_template="Mengkonfirmasi {action_count} aksi rekonsiliasi\u2026",
        success_message_template="Berhasil mengkonfirmasi {action_count} aksi rekonsiliasi.",
        fields=[
            FieldSpec(name="session_id", label="Session ID", required=True, hidden=True),
            FieldSpec(name="action_ids", label="Action IDs", required=True, hidden=True),
        ],
    ),
    "categorize_statement": DirectActionConfig(
        action_key="categorize_statement",
        display_name="Kategorisasi Statement",
        rest_endpoint="/api/bank-reconciliation/sessions/{session_id}/categorize",
        rest_method="POST",
        entity_type="reconciliation",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CATEGORIZE_STATEMENT",
        signal_words=["kategorisasi", "buat transaksi dari statement", "catat sebagai",
                       "biaya bank", "admin bank", "biaya admin", "bunga bank",
                       "ini biaya", "ini expense", "catat pengeluaran"],
        entity_name_field="description",
        loading_message_template="Mengkategorisasi transaksi\u2026",
        success_message_template="Statement berhasil dikategorisasi sebagai {account_name}.",
        category=None,
        impact_rules=[
            ImpactRule(
                field="account_id",
                condition="always",
                message_template="Transaksi akan dicatat ke akun yang dipilih.",
            ),
        ],
        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(name="session_id", label="Session ID", required=True, hidden=True),
            FieldSpec(name="statement_line_id", label="Line ID", required=True, hidden=True),
            FieldSpec(name="account_id", label="Akun Tujuan", required=False, hidden=False),
            FieldSpec(name="contact_id", label="Kontak ID", hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="statement_description", label="Mutasi Bank", display_only=True),
            FieldSpec(name="statement_date", label="Tanggal", display_only=True),
            FieldSpec(name="amount", label="Jumlah", field_type="number", display_only=True),
            FieldSpec(name="account_name", label="Akun Tujuan", display_only=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="description", label="Deskripsi"),
        ],
    ),

    # ── Confirm Single Match (conversational one-by-one review) ──
    "confirm_single_match": DirectActionConfig(
        action_key="confirm_single_match",
        display_name="Konfirmasi Kecocokan",
        rest_endpoint="/api/bank-reconciliation/sessions/{session_id}/match",
        rest_method="POST",
        entity_type="reconciliation",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=120,

        action_type_key="CONFIRM_SINGLE_MATCH",
        signal_words=["cocok", "match ini", "cocokkan ini", "setuju match",
                       "confirm match", "betul cocok", "ini pasangannya"],

        entity_name_field="statement_line_id",
        loading_message_template="Mencocokkan statement line…",
        success_message_template="Berhasil dicocokkan.",

        impact_rules=[],

        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(name="session_id", label="Session ID", required=True, hidden=True),
            FieldSpec(name="statement_line_id", label="Line ID", required=True, hidden=True),
            FieldSpec(name="transaction_ids", label="Transaction IDs", required=True, hidden=True),
            FieldSpec(name="adjustment_account_id", label="Akun Penyesuaian ID", hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="statement_description", label="Mutasi Bank", display_only=True),
            FieldSpec(name="statement_amount", label="Jumlah Mutasi", field_type="number", display_only=True),
            FieldSpec(name="transaction_description", label="Transaksi Cocok", display_only=True),
            FieldSpec(name="transaction_amount", label="Jumlah Transaksi", field_type="number", display_only=True),
            FieldSpec(name="match_confidence", label="Confidence", display_only=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="adjustment_amount", label="Jumlah Penyesuaian", field_type="number"),
            FieldSpec(name="adjustment_account_name", label="Akun Penyesuaian", display_only=True),
        ],
    ),
    # ============ DOCUMENT INTAKE ============
    "confirm_document_draft": DirectActionConfig(
        action_key="confirm_document_draft",
        display_name="Konfirmasi Draft Dokumen",
        rest_endpoint="/api/document-intake/document/{document_id}/confirm",
        rest_method="POST",
        entity_type="document_intake",
        risk_level="medium",
        creates_journal=True,  # Phase 8: confirm triggers journal posting via KernelDocumentExecutor
        ttl_seconds=300,
        action_type_key="CONFIRM_DOCUMENT_DRAFT",
        signal_words=["konfirmasi draft", "setuju draft", "approve draft",
                       "terima draft", "ok draft", "lanjut posting"],
        entity_name_field="document_title",
        loading_message_template="Mengkonfirmasi draft {entity_name}...",
        success_message_template="Draft \'{entity_name}\' berhasil dikonfirmasi.",
        fields=[
            FieldSpec(name="document_id", label="Document ID", required=True, hidden=True),
            FieldSpec(name="document_title", label="Dokumen", display_only=True),
            FieldSpec(name="doc_type", label="Tipe Dokumen", display_only=True),
            FieldSpec(name="counterparty_name", label="Pihak Terkait", display_only=True),
            FieldSpec(name="journal_description", label="Keterangan Jurnal", display_only=True),
            FieldSpec(name="total_debit", label="Total Debit", field_type="number", display_only=True),
            FieldSpec(name="total_credit", label="Total Kredit", field_type="number", display_only=True),
            FieldSpec(name="confidence", label="Confidence", display_only=True),
            FieldSpec(name="overrides", label="Overrides", field_type="string", hidden=True),
        ],
        impact_rules=[
            ImpactRule(field="total_debit", condition="nonzero",
                      message_template="Jurnal senilai {formatted_value} akan dibuat setelah posting."),
        ],
    ),
    "reject_document_draft": DirectActionConfig(
        action_key="reject_document_draft",
        display_name="Tolak Draft Dokumen",
        rest_endpoint="/api/document-intake/document/{document_id}/reject",
        rest_method="POST",
        entity_type="document_intake",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="REJECT_DOCUMENT_DRAFT",
        signal_words=["tolak draft", "reject draft", "buang draft", "batalkan draft"],
        entity_name_field="document_title",
        loading_message_template="Menolak draft {entity_name}...",
        success_message_template="Draft \'{entity_name}\' ditolak.",
        fields=[
            FieldSpec(name="document_id", label="Document ID", required=True, hidden=True),
            FieldSpec(name="document_title", label="Dokumen", display_only=True),
            FieldSpec(name="doc_type", label="Tipe Dokumen", display_only=True),
            FieldSpec(name="reason", label="Alasan Penolakan", required=False,
                      description="Alasan kenapa draft ditolak"),
        ],
        impact_rules=[],
    ),

    # ============ CREATE — Items, Accounts, Warehouses ============
    "create_item": DirectActionConfig(
        action_key="create_item",
        display_name="Buat Barang/Jasa",
        rest_endpoint="/api/items",
        rest_method="POST",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_ITEM",
        signal_words=["barang baru", "item baru", "tambah barang", "tambah item",
                       "produk baru", "jasa baru", "tambah produk", "tambah jasa"],
        entity_name_field="name",
        loading_message_template="Membuat barang/jasa {entity_name}…",
        success_message_template="Barang/jasa '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(name="name", label="Nama", required=True),
            FieldSpec(name="sku", label="Kode/SKU"),
            FieldSpec(name="base_unit", label="Satuan", description="Contoh: pcs, roll, meter, kg"),
            FieldSpec(name="sales_price", label="Harga Jual", field_type="number"),
            FieldSpec(name="purchase_price", label="Harga Beli", field_type="number"),
            FieldSpec(name="item_type", label="Tipe", field_type="enum",
                      options=["goods", "service", "non_inventory"], default="goods"),
            FieldSpec(name="description", label="Deskripsi"),
        ],
    ),
    "create_account": DirectActionConfig(
        action_key="create_account",
        display_name="Buat Akun Baru (CoA)",
        rest_endpoint="/api/accounts",
        rest_method="POST",
        entity_type="account",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_ACCOUNT",
        signal_words=["akun baru", "tambah akun", "buat akun",
                       "akun CoA baru", "chart of accounts baru"],
        entity_name_field="name",
        loading_message_template="Membuat akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(name="code", label="Kode Akun", required=True,
                      description="Contoh: 1-10700, 5-20100"),
            FieldSpec(name="name", label="Nama Akun", required=True),
            FieldSpec(name="type", label="Tipe", field_type="enum", required=True,
                      options=["ASSET", "RECEIVABLE", "LIABILITY", "PAYABLE",
                               "EQUITY", "REVENUE", "COGS", "EXPENSE",
                               "OTHER_INCOME", "OTHER_EXPENSE"]),
            FieldSpec(name="normal_balance", label="Saldo Normal", field_type="enum",
                      options=["DEBIT", "CREDIT"],
                      description="Auto-derived: DEBIT untuk Aset/Beban, CREDIT untuk Liabilitas/Ekuitas/Pendapatan"),
            FieldSpec(name="parent_id", label="Akun Induk",
                      description="UUID akun induk (opsional)"),
        ],
        category=CategoryMapping(
            field="account_type",
            mapping={
                "ASSET": "Aset",
                "LIABILITY": "Liabilitas",
                "EQUITY": "Ekuitas",
                "REVENUE": "Pendapatan",
                "EXPENSE": "Beban",
                "COGS": "Harga Pokok Penjualan",
            },
            default="",
        ),
    ),
    "create_warehouse": DirectActionConfig(
        action_key="create_warehouse",
        display_name="Buat Gudang",
        rest_endpoint="/api/warehouses",
        rest_method="POST",
        entity_type="warehouse",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_WAREHOUSE",
        signal_words=["gudang baru", "tambah gudang", "buat gudang", "warehouse baru"],
        entity_name_field="name",
        loading_message_template="Membuat gudang {entity_name}…",
        success_message_template="Gudang '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(name="code", label="Kode Gudang",
                      description="Kode unik gudang, e.g. GD-CIKUPA. Auto-generated dari nama jika tidak diisi."),
            FieldSpec(name="name", label="Nama Gudang", required=True),
            FieldSpec(name="address", label="Alamat"),
            FieldSpec(name="city", label="Kota"),
            FieldSpec(name="description", label="Keterangan"),
        ],
    ),

    # ============ UPDATE — Customers, Vendors, Items, Bank Accounts, Accounts, Warehouses ============
    "update_customer": DirectActionConfig(
        action_key="update_customer",
        display_name="Edit Pelanggan",
        rest_endpoint="/api/customers/{id}",
        rest_method="PATCH",
        entity_type="customer",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_CUSTOMER",
        signal_words=["edit pelanggan", "ubah pelanggan", "ganti nama pelanggan",
                       "update pelanggan", "perbarui pelanggan"],
        entity_name_field="name",
        loading_message_template="Memperbarui pelanggan {entity_name}…",
        success_message_template="Pelanggan '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Pelanggan"),
            FieldSpec(name="phone", label="Telepon"),
            FieldSpec(name="email", label="Email"),
            FieldSpec(name="address", label="Alamat"),
            FieldSpec(name="company_name", label="Nama Perusahaan"),
        ],
    ),
    "update_vendor": DirectActionConfig(
        action_key="update_vendor",
        display_name="Edit Vendor",
        rest_endpoint="/api/vendors/{id}",
        rest_method="PATCH",
        entity_type="vendor",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_VENDOR",
        signal_words=["edit vendor", "ubah vendor", "ganti nama vendor",
                       "update vendor", "edit supplier"],
        entity_name_field="name",
        loading_message_template="Memperbarui vendor {entity_name}…",
        success_message_template="Vendor '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Vendor"),
            FieldSpec(name="phone", label="Telepon"),
            FieldSpec(name="email", label="Email"),
            FieldSpec(name="address", label="Alamat"),
        ],
    ),
    "update_item": DirectActionConfig(
        action_key="update_item",
        display_name="Edit Barang/Jasa",
        rest_endpoint="/api/items/{id}",
        rest_method="PUT",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_ITEM",
        signal_words=["edit barang", "ubah barang", "ganti harga",
                       "update item", "ubah harga jual", "ganti nama barang"],
        entity_name_field="name",
        loading_message_template="Memperbarui {entity_name}…",
        success_message_template="'{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama"),
            FieldSpec(name="sku", label="Kode/SKU"),
            FieldSpec(name="base_unit", label="Satuan"),
            FieldSpec(name="sales_price", label="Harga Jual", field_type="number"),
            FieldSpec(name="purchase_price", label="Harga Beli", field_type="number"),
            FieldSpec(name="description", label="Deskripsi"),
        ],
    ),
    "update_bank_account": DirectActionConfig(
        action_key="update_bank_account",
        display_name="Edit Akun Kas & Bank",
        rest_endpoint="/api/bank-accounts/{id}",
        rest_method="PATCH",
        entity_type="bank_account",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_BANK_ACCOUNT",
        signal_words=["edit rekening", "ubah rekening", "ganti nama bank",
                       "update bank account", "edit akun kas"],
        entity_name_field="account_name",
        loading_message_template="Memperbarui akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="account_name", label="Nama Akun"),
            FieldSpec(name="bank_name", label="Nama Bank"),
            FieldSpec(name="account_number", label="Nomor Rekening"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "update_account": DirectActionConfig(
        action_key="update_account",
        display_name="Edit Akun (CoA)",
        rest_endpoint="/api/accounts/{id}",
        rest_method="PATCH",
        entity_type="account",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_ACCOUNT",
        signal_words=["edit akun", "ubah akun", "ganti nama akun",
                       "update CoA", "rename akun"],
        entity_name_field="name",
        loading_message_template="Memperbarui akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Akun"),
            FieldSpec(name="description", label="Deskripsi"),
        ],
    ),
    "update_warehouse": DirectActionConfig(
        action_key="update_warehouse",
        display_name="Edit Gudang",
        rest_endpoint="/api/warehouses/{id}",
        rest_method="PATCH",
        entity_type="warehouse",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_WAREHOUSE",
        signal_words=["edit gudang", "ubah gudang", "ganti nama gudang", "update warehouse"],
        entity_name_field="name",
        loading_message_template="Memperbarui gudang {entity_name}…",
        success_message_template="Gudang '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Gudang"),
            FieldSpec(name="address", label="Alamat"),
            FieldSpec(name="description", label="Keterangan"),
        ],
    ),

    # ============ DELETE — Customers, Vendors, Items, Warehouses ============
    "delete_customer": DirectActionConfig(
        action_key="delete_customer",
        display_name="Hapus Pelanggan",
        rest_endpoint="/api/customers/{id}",
        rest_method="DELETE",
        entity_type="customer",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=120,
        action_type_key="DELETE_CUSTOMER",
        signal_words=["hapus pelanggan", "delete pelanggan", "buang pelanggan"],
        entity_name_field="name",
        loading_message_template="Menghapus pelanggan {entity_name}…",
        success_message_template="Pelanggan '{entity_name}' berhasil dihapus.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama", required=True),
        ],
    ),
    "delete_vendor": DirectActionConfig(
        action_key="delete_vendor",
        display_name="Hapus Vendor",
        rest_endpoint="/api/vendors/{id}",
        rest_method="DELETE",
        entity_type="vendor",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=120,
        action_type_key="DELETE_VENDOR",
        signal_words=["hapus vendor", "delete vendor", "buang supplier"],
        entity_name_field="name",
        loading_message_template="Menghapus vendor {entity_name}…",
        success_message_template="Vendor '{entity_name}' berhasil dihapus.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama", required=True),
        ],
    ),
    "delete_item": DirectActionConfig(
        action_key="delete_item",
        display_name="Hapus Barang/Jasa",
        rest_endpoint="/api/items/{id}",
        rest_method="DELETE",
        entity_type="item",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=120,
        action_type_key="DELETE_ITEM",
        signal_words=["hapus barang", "delete item", "buang produk", "hapus jasa"],
        entity_name_field="name",
        loading_message_template="Menghapus {entity_name}…",
        success_message_template="'{entity_name}' berhasil dihapus.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama", required=True),
        ],
    ),
    "delete_warehouse": DirectActionConfig(
        action_key="delete_warehouse",
        display_name="Hapus Gudang",
        rest_endpoint="/api/warehouses/{id}",
        rest_method="DELETE",
        entity_type="warehouse",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=120,
        action_type_key="DELETE_WAREHOUSE",
        signal_words=["hapus gudang", "delete gudang", "buang gudang"],
        entity_name_field="name",
        loading_message_template="Menghapus gudang {entity_name}…",
        success_message_template="Gudang '{entity_name}' berhasil dihapus.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama", required=True),
        ],
    ),
}


# \u2500\u2500\u2500 Query Actions Registry \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# READ-ONLY queries \u2014 no mutations, no confirmation flow.
# Law 16: all financial numbers derive from journal_lines.

QUERY_ACTIONS: dict[str, QueryActionConfig] = {
    "query_cash_balance": QueryActionConfig(
        action_key="query_cash_balance",
        display_name="Saldo Kas & Bank",
        rest_endpoint="/api/kasbank/stats",
        response_format="single_value",
        description="Saldo kas, bank, dan total. Termasuk arus masuk/keluar hari ini.",
        signal_words=["saldo kas", "uang kas", "cash balance", "saldo bank",
                       "berapa saldo", "total kas", "uang di bank", "saldo rekening",
                       "kas berapa", "dana tersedia"],
    ),
    "query_profit_loss": QueryActionConfig(
        action_key="query_profit_loss",
        display_name="Laporan Laba Rugi",
        rest_endpoint="/api/reports/profit-loss",
        response_format="summary",
        description="Laba rugi: pendapatan, HPP, laba kotor, beban operasional, laba bersih.",
        signal_words=["laba rugi", "profit loss", "P&L", "income statement",
                       "pendapatan", "untung rugi", "margin", "net income",
                       "berapa laba", "berapa rugi", "keuntungan"],
        query_params=[
            QueryParam(name="start_date", label="Dari Tanggal", param_type="date"),
            QueryParam(name="end_date", label="Sampai Tanggal", param_type="date"),
        ],
    ),
    "query_balance_sheet": QueryActionConfig(
        action_key="query_balance_sheet",
        display_name="Neraca",
        rest_endpoint="/api/reports/neraca/{periode}",
        response_format="summary",
        description="Neraca: aset, kewajiban, ekuitas. Cek apakah balance.",
        signal_words=["neraca", "balance sheet", "posisi keuangan",
                       "total aset", "total kewajiban", "ekuitas"],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string",
                      default="current"),
        ],
    ),
    "query_cash_flow": QueryActionConfig(
        action_key="query_cash_flow",
        display_name="Arus Kas",
        rest_endpoint="/api/reports/arus-kas/{periode}",
        response_format="summary",
        description="Arus kas: operasi, investasi, pendanaan, kas awal/akhir.",
        signal_words=["arus kas", "cash flow", "aliran kas", "kas masuk keluar"],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string",
                      default="current"),
        ],
    ),
    "query_ar_aging": QueryActionConfig(
        action_key="query_ar_aging",
        display_name="Aging Piutang",
        rest_endpoint="/api/reports/ar-aging",
        response_format="summary",
        description="Aging piutang: current, 1-30, 31-60, 61-90, 91-120, >120 hari.",
        signal_words=["piutang", "receivable", "yang belum bayar", "overdue",
                       "hutang pelanggan", "tagihan belum dibayar", "aging piutang",
                       "umur piutang", "berapa piutang"],
        query_params=[
            QueryParam(name="as_of", label="Per Tanggal", param_type="date"),
        ],
    ),
    "query_ap_aging": QueryActionConfig(
        action_key="query_ap_aging",
        display_name="Aging Hutang",
        rest_endpoint="/api/reports/ap-aging",
        response_format="summary",
        description="Aging hutang usaha: current, 1-30, 31-60, 61-90, 91-120, >120 hari.",
        signal_words=["hutang usaha", "payable", "aging hutang", "umur hutang",
                       "tagihan vendor", "berapa hutang", "kewajiban vendor"],
        query_params=[
            QueryParam(name="as_of", label="Per Tanggal", param_type="date"),
        ],
    ),
    "query_invoice_summary": QueryActionConfig(
        action_key="query_invoice_summary",
        display_name="Ringkasan Faktur Penjualan",
        rest_endpoint="/api/sales-invoices/summary",
        response_format="summary",
        description="Ringkasan faktur: total, draft, posted, partial, paid, overdue, outstanding.",
        signal_words=["ringkasan faktur", "invoice summary", "faktur overdue",
                       "berapa faktur", "outstanding invoice", "faktur belum bayar"],
    ),
    "query_bills_outstanding": QueryActionConfig(
        action_key="query_bills_outstanding",
        display_name="Tagihan Outstanding",
        rest_endpoint="/api/bills/outstanding-summary",
        response_format="summary",
        description="Tagihan belum bayar: total outstanding, overdue, vendor count, urgency.",
        signal_words=["tagihan outstanding", "tagihan belum bayar", "bills outstanding",
                       "berapa tagihan", "tagihan overdue"],
    ),
    "query_trial_balance": QueryActionConfig(
        action_key="query_trial_balance",
        display_name="Neraca Saldo",
        rest_endpoint="/api/reports/trial-balance",
        response_format="table",
        description="Neraca saldo: semua akun dengan debit/credit balance. Cek apakah balance.",
        signal_words=["neraca saldo", "trial balance", "saldo akun",
                       "balance semua akun", "daftar saldo"],
        query_params=[
            QueryParam(name="start_date", label="Dari Tanggal", param_type="date"),
            QueryParam(name="end_date", label="Sampai Tanggal", param_type="date"),
        ],
    ),
    "query_top_expenses": QueryActionConfig(
        action_key="query_top_expenses",
        display_name="Top Pengeluaran",
        rest_endpoint="/api/dashboard/top-expenses",
        response_format="table",
        description="Top kategori pengeluaran dengan persentase.",
        signal_words=["pengeluaran terbesar", "top expenses", "biaya terbesar",
                       "kategori pengeluaran", "beban terbesar"],
        query_params=[
            QueryParam(name="start_date", label="Dari Tanggal", param_type="date"),
            QueryParam(name="end_date", label="Sampai Tanggal", param_type="date"),
        ],
    ),
    "query_expense_summary": QueryActionConfig(
        action_key="query_expense_summary",
        display_name="Ringkasan Beban",
        rest_endpoint="/api/expenses/summary",
        response_format="summary",
        description="Ringkasan beban: total count, amount, tax, top accounts.",
        signal_words=["ringkasan beban", "expense summary", "total beban",
                       "berapa beban", "pengeluaran bulan ini"],
    ),
    "query_general_ledger": QueryActionConfig(
        action_key="query_general_ledger",
        display_name="Buku Besar",
        rest_endpoint="/api/ledger",
        response_format="table",
        description="Buku besar: semua akun dengan saldo debit/credit/net.",
        signal_words=["buku besar", "general ledger", "ledger", "GL"],
        query_params=[
            QueryParam(name="start_date", label="Dari Tanggal", param_type="date"),
            QueryParam(name="end_date", label="Sampai Tanggal", param_type="date"),
        ],
    ),
    "query_periods": QueryActionConfig(
        action_key="query_periods",
        display_name="Periode Akuntansi",
        rest_endpoint="/api/periods",
        response_format="list",
        description="Daftar periode akuntansi: status open/closed.",
        signal_words=["periode akuntansi", "fiscal period", "daftar periode",
                       "periode buka", "periode tutup"],
    ),

    # ═══════════════ CHART QUERIES ═══════════════
    "chart_revenue_expense": ChartQueryConfig(
        action_key="chart_revenue_expense",
        display_name="Pendapatan vs Beban",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        description="Grafik perbandingan pendapatan dan beban.",
        chart_type="bar",
        complexity_hint="simple",
        chart_features={"legend_toggle": True},
        signal_words=["grafik pendapatan", "grafik beban", "chart laba rugi",
                       "visualisasi pendapatan", "grafik revenue", "chart pendapatan beban",
                       "tunjukkan grafik", "tampilkan grafik"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_cash_flow": ChartQueryConfig(
        action_key="chart_cash_flow",
        display_name="Arus Kas",
        rest_endpoint="/api/reports/arus-kas/{periode}",
        description="Grafik arus kas: operasional, investasi, pendanaan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["grafik arus kas", "grafik cash flow", "chart cash flow",
                       "visualisasi arus kas", "tren kas"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_expense_breakdown": ChartQueryConfig(
        action_key="chart_expense_breakdown",
        display_name="Komposisi Beban",
        rest_endpoint="/api/dashboard/top-expenses",
        description="Grafik donut komposisi beban per kategori.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["grafik beban kategori", "pie chart beban", "komposisi beban",
                       "kemana uang pergi", "breakdown beban", "chart expense"],
    ),
    "chart_top_customers": ChartQueryConfig(
        action_key="chart_top_customers",
        display_name="Top Pelanggan",
        rest_endpoint="/api/reports/pendapatan/{periode}",
        description="Grafik bar horizontal pelanggan dengan revenue tertinggi.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["grafik pelanggan", "top pelanggan", "chart customer",
                       "pelanggan terbesar", "ranking pelanggan"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default=""),
                      QueryParam(name="limit", label="Jumlah", param_type="number", default="5")],
    ),
    "chart_ar_aging": ChartQueryConfig(
        action_key="chart_ar_aging",
        display_name="Aging Piutang",
        rest_endpoint="/api/reports/aging-trend",
        description="Grafik tren aging piutang over time.",
        chart_type="line",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["grafik piutang", "tren piutang", "chart aging",
                       "grafik receivable", "visualisasi piutang"],
    ),

    # ═══════════════ BATCH 1: Dashboard & KPI (6) ═══════════════
    "chart_kas_composition": ChartQueryConfig(
        action_key="chart_kas_composition",
        display_name="Komposisi Kas & Bank",
        rest_endpoint="/api/dashboard/kas-bank",
        description="Grafik donut komposisi saldo kas dan bank.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["grafik kas", "komposisi kas", "chart bank", "saldo kas bank",
                       "pie chart kas", "distribusi kas"],
    ),
    "chart_cash_projection": ChartQueryConfig(
        action_key="chart_cash_projection",
        display_name="Proyeksi Arus Kas",
        rest_endpoint="/api/dashboard/cash-flow-projection",
        description="Grafik proyeksi arus kas ke depan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["proyeksi kas", "cash projection", "prediksi kas",
                       "forecast kas", "grafik proyeksi"],
    ),
    "chart_overdue_invoices": ChartQueryConfig(
        action_key="chart_overdue_invoices",
        display_name="Invoice Jatuh Tempo",
        rest_endpoint="/api/dashboard/overdue-invoices",
        description="Grafik invoice yang sudah jatuh tempo per pelanggan.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["grafik overdue invoice", "invoice jatuh tempo", "chart piutang overdue",
                       "tagihan terlambat"],
        query_params=[QueryParam(name="limit", label="Jumlah", param_type="number", default="10")],
    ),
    "chart_overdue_bills": ChartQueryConfig(
        action_key="chart_overdue_bills",
        display_name="Tagihan Jatuh Tempo",
        rest_endpoint="/api/dashboard/overdue-bills",
        description="Grafik tagihan supplier yang sudah jatuh tempo.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["grafik overdue bill", "tagihan jatuh tempo", "chart hutang overdue",
                       "bill terlambat"],
    ),
    "chart_cash_flow_trends": ChartQueryConfig(
        action_key="chart_cash_flow_trends",
        display_name="Tren Kas Masuk & Keluar",
        rest_endpoint="/api/dashboard/cash-flow-trends",
        description="Grafik tren kas masuk dan keluar harian/mingguan.",
        chart_type="area",
        complexity_hint="simple",
        chart_features={"legend_toggle": True},
        signal_words=["tren kas", "grafik kas masuk keluar", "cash flow trend",
                       "aliran kas", "uang masuk keluar"],
        query_params=[QueryParam(name="months", label="Bulan", param_type="number", default="6")],
    ),
    "chart_dashboard_kpi": ChartQueryConfig(
        action_key="chart_dashboard_kpi",
        display_name="KPI Dashboard",
        rest_endpoint="/api/dashboard/summary",
        description="Grafik ringkasan KPI: pendapatan, beban, piutang, hutang, kas.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["grafik kpi", "dashboard chart", "ringkasan grafik",
                       "chart overview", "grafik ringkasan"],
    ),

    # ═══════════════ BATCH 2: Laporan Keuangan (6) ═══════════════
    "chart_neraca": ChartQueryConfig(
        action_key="chart_neraca",
        display_name="Neraca (Balance Sheet)",
        rest_endpoint="/api/reports/neraca/{periode}",
        description="Grafik neraca: aset vs kewajiban & ekuitas.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["grafik neraca", "chart balance sheet", "visualisasi neraca",
                       "grafik aset kewajiban"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_neraca_composition": ChartQueryConfig(
        action_key="chart_neraca_composition",
        display_name="Komposisi Aset",
        rest_endpoint="/api/reports/neraca/{periode}",
        description="Grafik donut komposisi aset perusahaan.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["komposisi aset", "pie aset", "distribusi aset",
                       "chart aset"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_profit_trend": ChartQueryConfig(
        action_key="chart_profit_trend",
        display_name="Tren Laba Rugi",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        description="Grafik tren pendapatan, beban, dan laba bulanan.",
        chart_type="line",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["tren laba", "profit trend", "grafik laba bulanan",
                       "tren pendapatan", "monthly profit"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_profit_comparison": ChartQueryConfig(
        action_key="chart_profit_comparison",
        display_name="Perbandingan Laba Rugi",
        rest_endpoint="/api/reports/profit-loss/comparison",
        description="Grafik perbandingan laba rugi dua periode.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["perbandingan laba", "comparison profit", "bandingkan laba rugi",
                       "laba bulan lalu vs sekarang"],
        query_params=[
            QueryParam(name="period1_start", label="Periode 1 Mulai", param_type="date", default=""),
            QueryParam(name="period1_end", label="Periode 1 Akhir", param_type="date", default=""),
            QueryParam(name="period2_start", label="Periode 2 Mulai", param_type="date", default=""),
            QueryParam(name="period2_end", label="Periode 2 Akhir", param_type="date", default=""),
        ],
    ),
    "chart_gross_margin": ChartQueryConfig(
        action_key="chart_gross_margin",
        display_name="Margin Kotor",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        description="Grafik revenue, HPP, dan gross profit.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["grafik margin", "gross margin chart", "chart hpp",
                       "grafik margin kotor", "revenue vs hpp"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),
    "chart_monthly_cashflow": ChartQueryConfig(
        action_key="chart_monthly_cashflow",
        display_name="Arus Kas Bulanan",
        rest_endpoint="/api/reports/arus-kas/{periode}",
        description="Grafik arus kas operasional, investasi, pendanaan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["arus kas bulanan", "monthly cash flow", "grafik arus kas detail"],
        query_params=[QueryParam(name="periode", label="Periode", param_type="string", default="")],
    ),

    # ═══════════════ BATCH 3: AR/AP (6) ═══════════════
    "chart_ap_aging": ChartQueryConfig(
        action_key="chart_ap_aging",
        display_name="Aging Hutang",
        rest_endpoint="/api/reports/ap-aging",
        description="Grafik aging hutang per bucket waktu.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["grafik hutang", "aging hutang", "chart ap aging",
                       "grafik payable", "visualisasi hutang", "aging vendor"],
    ),
    "chart_ar_summary": ChartQueryConfig(
        action_key="chart_ar_summary",
        display_name="Ringkasan Piutang",
        rest_endpoint="/api/dashboard/piutang",
        description="Grafik donut ringkasan piutang per bucket.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["ringkasan piutang", "donut piutang", "pie piutang",
                       "distribusi piutang"],
    ),
    "chart_ap_summary": ChartQueryConfig(
        action_key="chart_ap_summary",
        display_name="Ringkasan Hutang",
        rest_endpoint="/api/dashboard/hutang",
        description="Grafik donut ringkasan hutang per bucket.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["ringkasan hutang", "donut hutang", "pie hutang",
                       "distribusi hutang"],
    ),
    "chart_invoice_status": ChartQueryConfig(
        action_key="chart_invoice_status",
        display_name="Status Invoice",
        rest_endpoint="/api/sales-invoices/summary",
        description="Grafik donut status invoice: draft, posted, partial, paid.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["grafik invoice", "status invoice", "chart sales invoice",
                       "distribusi invoice", "pie invoice"],
    ),
    "chart_bill_status": ChartQueryConfig(
        action_key="chart_bill_status",
        display_name="Status Tagihan",
        rest_endpoint="/api/bills/summary",
        description="Grafik donut status tagihan: paid, partial, unpaid, overdue.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["grafik tagihan", "status bill", "chart bill",
                       "distribusi tagihan", "pie tagihan"],
    ),
    "chart_payment_trends": ChartQueryConfig(
        action_key="chart_payment_trends",
        display_name="Tren Pembayaran",
        rest_endpoint="/api/bill-payments/summary",
        description="Grafik pembayaran per metode.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["grafik pembayaran", "payment trend", "chart payment",
                       "tren bayar", "metode pembayaran"],
    ),

    # ═══════════════ BATCH 4: Inventory & Products (5) ═══════════════
    "chart_top_products": ChartQueryConfig(
        action_key="chart_top_products",
        display_name="Produk Terlaris",
        rest_endpoint="/api/inventory/top-products",
        description="Grafik produk terlaris berdasarkan qty terjual.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["produk terlaris", "top product", "barang laris",
                       "chart produk", "ranking produk", "grafik penjualan produk"],
        query_params=[QueryParam(name="limit", label="Jumlah", param_type="number", default="10")],
    ),
    "chart_product_margins": ChartQueryConfig(
        action_key="chart_product_margins",
        display_name="Margin Produk",
        rest_endpoint="/api/inventory/product-margins",
        description="Grafik margin per produk: revenue, COGS, profit.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["margin produk", "product margin", "keuntungan produk",
                       "profit per produk", "grafik margin produk"],
        query_params=[QueryParam(name="limit", label="Jumlah", param_type="number", default="10")],
    ),
    "chart_slow_moving": ChartQueryConfig(
        action_key="chart_slow_moving",
        display_name="Produk Lambat Terjual",
        rest_endpoint="/api/inventory/slow-moving-products",
        description="Grafik produk yang lambat terjual.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["produk lambat", "slow moving", "barang tidak laku",
                       "dead stock", "grafik stok lambat"],
        query_params=[
            QueryParam(name="days", label="Hari", param_type="number", default="30"),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "chart_sales_trend": ChartQueryConfig(
        action_key="chart_sales_trend",
        display_name="Tren Penjualan",
        rest_endpoint="/api/sales-receipts/daily-summary",
        description="Grafik tren penjualan harian.",
        chart_type="line",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=["tren penjualan", "sales trend", "grafik penjualan",
                       "penjualan harian", "daily sales", "grafik sales"],
    ),
    "chart_top_vendors": ChartQueryConfig(
        action_key="chart_top_vendors",
        display_name="Top Vendor",
        rest_endpoint="/api/vendors",
        description="Grafik vendor dengan saldo hutang terbesar.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["top vendor", "vendor terbesar", "grafik vendor",
                       "ranking vendor", "chart supplier"],
        query_params=[QueryParam(name="limit", label="Jumlah", param_type="number", default="10")],
    ),

    # ═══════════════ BATCH 5: Financial Ratios (4) ═══════════════
    "chart_profitability_ratios": ChartQueryConfig(
        action_key="chart_profitability_ratios",
        display_name="Rasio Profitabilitas",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio profitabilitas: ROA, ROE, margin.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["rasio profitabilitas", "profitability ratio", "grafik roa roe",
                       "chart margin ratio", "rasio keuntungan"],
    ),
    "chart_liquidity_ratios": ChartQueryConfig(
        action_key="chart_liquidity_ratios",
        display_name="Rasio Likuiditas",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio likuiditas: cash, quick, current ratio.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["rasio likuiditas", "liquidity ratio", "grafik current ratio",
                       "chart quick ratio", "rasio lancar"],
    ),
    "chart_leverage_ratios": ChartQueryConfig(
        action_key="chart_leverage_ratios",
        display_name="Rasio Leverage",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio leverage: debt to equity, debt to asset.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=["rasio leverage", "debt ratio", "grafik hutang modal",
                       "chart leverage", "rasio solvabilitas"],
    ),
    "chart_ratio_dashboard": ChartQueryConfig(
        action_key="chart_ratio_dashboard",
        display_name="Dashboard Rasio Keuangan",
        rest_endpoint="/api/financial-ratios",
        description="Dashboard lengkap semua rasio keuangan.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["dashboard rasio", "semua rasio", "financial ratio dashboard",
                       "grafik rasio lengkap", "rasio keuangan"],
    ),

    # ═══════════════ BATCH 6: Budget & Production (3) ═══════════════
    "chart_budget_vs_actual": ChartQueryConfig(
        action_key="chart_budget_vs_actual",
        display_name="Budget vs Aktual",
        rest_endpoint="/api/budgets/{budget_id}/vs-actual",
        description="Grafik perbandingan budget vs realisasi.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["budget vs actual", "realisasi anggaran", "grafik budget",
                       "chart anggaran", "budget realisasi"],
        query_params=[QueryParam(name="budget_id", label="Budget ID", param_type="string", required=True)],
    ),
    "chart_variance_alerts": ChartQueryConfig(
        action_key="chart_variance_alerts",
        display_name="Peringatan Varians",
        rest_endpoint="/api/budgets/variance-alerts",
        description="Grafik item budget yang melebihi/di bawah target.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=["varians budget", "variance alert", "over budget",
                       "grafik varians", "penyimpangan anggaran"],
    ),
    "chart_production_costs": ChartQueryConfig(
        action_key="chart_production_costs",
        display_name="Biaya Produksi",
        rest_endpoint="/api/production/{order_id}/cost-analysis",
        description="Grafik analisis biaya produksi: material, labor, overhead.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=["biaya produksi", "production cost", "grafik cost analysis",
                       "analisis biaya", "chart produksi"],
        query_params=[QueryParam(name="order_id", label="Order ID", param_type="string", required=True)],
    ),
}


# \u2500\u2500\u2500 Unified Registry (DirectAction + Query) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
REGISTRY: dict[str, "DirectActionConfig | QueryActionConfig | ChartQueryConfig"] = {
    **DIRECT_ACTIONS,
    **QUERY_ACTIONS,
}


# \u2500\u2500\u2500 Helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def get_direct_action(action_key: str) -> Optional[DirectActionConfig]:
    return DIRECT_ACTIONS.get(action_key)


def get_action_keys() -> list[str]:
    return list(DIRECT_ACTIONS.keys())


def get_required_fields(action_key: str) -> list[FieldSpec]:
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return []
    return [f for f in config.fields if f.required]


def validate_payload(action_key: str, payload: dict) -> tuple[bool, list[str]]:
    """Validate payload against registry. Returns (is_valid, missing_fields)."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return False, [f"Unknown action_key: {action_key}"]
    missing = []
    for f in config.fields:
        if f.required and not payload.get(f.name):
            missing.append(f.label)
    return len(missing) == 0, missing


def apply_defaults(action_key: str, payload: dict) -> dict:
    """Apply default values for missing optional fields."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return payload
    result = dict(payload)
    for f in config.fields:
        if f.default is not None and f.name not in result:
            if f.field_type == "number":
                result[f.name] = int(f.default) if f.default.isdigit() else float(f.default)
            elif f.field_type == "boolean":
                result[f.name] = f.default.lower() == "true"
            else:
                result[f.name] = f.default

    # Auto-generate warehouse code from name if missing
    if action_key == "create_warehouse" and "code" not in result and "name" in result:
        import re as _re
        # "Gudang Cikupa" -> "GD-CIKUPA"
        name = result["name"]
        # Remove common prefix "Gudang" for shorter code
        short = _re.sub(r"^[Gg]udang\s+", "", name).strip()
        code = "GD-" + _re.sub(r"[^A-Za-z0-9]+", "-", short).upper().strip("-")[:30]
        result["code"] = code

    # Auto-derive normal_balance for create_account based on type
    if action_key == "create_account" and "normal_balance" not in result and "type" in result:
        acct_type = result["type"].upper()
        # DEBIT types: ASSET, RECEIVABLE, COGS, EXPENSE, OTHER_EXPENSE
        # CREDIT types: LIABILITY, PAYABLE, EQUITY, REVENUE, OTHER_INCOME
        debit_types = {"ASSET", "RECEIVABLE", "COGS", "EXPENSE", "OTHER_EXPENSE"}
        result["normal_balance"] = "DEBIT" if acct_type in debit_types else "CREDIT"

    # Map legacy field names for create_account (account_code -> code, account_type -> type)
    if action_key == "create_account":
        if "account_code" in result and "code" not in result:
            result["code"] = result.pop("account_code")
        if "account_type" in result and "type" not in result:
            result["type"] = result.pop("account_type")

    return result


def build_confirmation_table(action_key: str, payload: dict) -> str:
    """Build markdown confirmation table with trust context — fully config-driven."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return ""

    lines = [f"### {config.display_name}\n"]
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")

    for f in config.fields:
        if f.hidden:
            continue
        value = payload.get(f.name)
        if not value or (isinstance(value, str) and not value.strip()):
            if f.display_only:
                # Display-only fields: skip when empty (no placeholder)
                continue
            # Skip empty fields entirely — cleaner table
            pass
            continue
        if value is not None and str(value).strip():
            display_value = str(value)
            if f.field_type == "number":
                try:
                    num_val = float(value)
                    display_value = f"Rp {int(num_val):,}".replace(",", ".")
                except (ValueError, TypeError):
                    pass
            elif f.field_type == "boolean":
                display_value = "Ya" if value else "Tidak"
            lines.append(f"| {f.label} | {display_value} |")

    # Trust context: category (from config, not hardcoded)
    cat_label = config.get_category_label(payload)
    if cat_label:
        lines.append(f"| Kategori | {cat_label} |")

    # Trust context: impact notes (from config rules)
    impact_notes = config.get_impact_notes(payload)
    for note in impact_notes:
        lines.append(f"\n{note}")

    return "\n".join(lines)


def build_review_card_payload(action_key: str, payload: dict, journal_preview: list | None = None) -> dict | None:
    """Build structured review card payload for frontend rendering.

    Returns structured data that replaces the markdown confirmation_table.
    Frontend uses this for InlineReviewCard (in-chat) and ReviewCardArtifact (side panel).
    """
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return None

    # Header fields — from FieldSpec, same iteration as build_confirmation_table
    header = []
    for f in config.fields:
        if f.hidden:
            continue
        value = payload.get(f.name)
        if not value or (isinstance(value, str) and not value.strip()):
            # Skip empty fields entirely — cleaner card
            continue
        display_value = str(value)
        if f.field_type == "percent":
            try:
                num_val = float(value)
                if num_val == 0:
                    continue  # Skip 0% fields entirely
                display_value = f"{num_val:g}%"
            except (ValueError, TypeError):
                pass
        elif f.field_type == "number":
            try:
                num_val = float(value)
                display_value = f"Rp {int(num_val):,}".replace(",", ".")
            except (ValueError, TypeError):
                pass
        elif f.field_type == "boolean":
            display_value = "Ya" if value else "Tidak"
        header.append({
            "label": f.label,
            "value": display_value,
            "field_type": f.field_type,
        })

    # Category label (from config mapping)
    category_label = config.get_category_label(payload)

    # Impact notes → warnings
    impact_notes = config.get_impact_notes(payload)
    warnings = []
    for note in impact_notes:
        wtype = "warning" if "\u26a0" in note or "tidak" in note.lower() else "info"
        warnings.append({"type": wtype, "message": note})

    # Journal preview lines
    journal_lines = None
    journal_balanced = None
    if journal_preview and isinstance(journal_preview, list):
        journal_lines = []
        total_dr = 0.0
        total_cr = 0.0
        for jl in journal_preview:
            dr = float(jl.get("debit", 0) or 0)
            cr = float(jl.get("credit", 0) or 0)
            total_dr += dr
            total_cr += cr
            journal_lines.append({
                "dir": "Dr" if dr > 0 else "Cr",
                "account": jl.get("account_name", jl.get("account", "")),
                "amount": dr if dr > 0 else cr,
            })
        journal_balanced = abs(total_dr - total_cr) < 0.01

    # Items + totals (for invoice-type actions)
    items = None
    totals = None
    payload_items = payload.get("items")
    if isinstance(payload_items, list) and len(payload_items) > 0:
        items = []
        subtotal = 0.0
        for item in payload_items:
            qty = float(item.get("quantity", item.get("qty", 0)) or 0)
            price = float(item.get("unit_price", item.get("price", 0)) or 0)
            item_subtotal = qty * price
            subtotal += item_subtotal
            items.append({
                "name": item.get("description", item.get("product_name", item.get("name", "Item"))),
                "qty": qty,
                "unit": item.get("unit", "Pcs"),
                "price": price,
                "subtotal": item_subtotal,
            })
        # Compute discount from discount_percent (or raw discount)
        discount_pct = float(payload.get("discount_percent", 0) or 0)
        raw_discount = float(payload.get("discount", 0) or 0)
        discount = (subtotal * discount_pct / 100.0) if discount_pct else raw_discount

        after_discount = subtotal - discount

        # Compute tax from tax_rate (or raw tax_amount)
        tax_rate_val = float(payload.get("tax_rate", 0) or 0)
        raw_tax = float(payload.get("tax_amount", 0) or 0)
        tax = (after_discount * tax_rate_val / 100.0) if tax_rate_val else raw_tax

        totals = {
            "subtotal": subtotal,
            "grand_total": after_discount + tax,
        }
        if discount:
            totals["discount"] = discount
        if tax:
            totals["tax"] = tax
            totals["tax_rate"] = tax_rate_val

    # Determine render_target
    # Invoice/bill always artifact (financial document = review in side panel)
    has_items = items is not None and len(items) > 0
    render_target = "artifact" if config.creates_journal and has_items else "inline"

    return {
        "render_target": render_target,
        "title": config.display_name,
        "header": header,
        "items": items,
        "totals": totals,
        "journal_lines": journal_lines,
        "journal_balanced": journal_balanced,
        "warnings": warnings if warnings else None,
        "impact_notes": impact_notes if impact_notes else None,
        "category_label": category_label,
        "version": 1,
    }


def build_ux_metadata(action_key: str, payload: dict) -> dict:
    """Build UX metadata dict for DIRECT_ACTION_PREVIEW response data.
    Includes everything frontend needs — no hardcoding required."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return {}
    return {
        "loading_message": config.get_loading_message(payload),
        "success_message": config.get_success_message(payload),
        "entity_type": config.entity_type,           # for data-changed events
        "action_type_key": config.action_type_key,    # uppercase key
    }


def get_all_signal_words() -> list[str]:
    """Collect all signal_words from BOTH registries — for auto-wiring intent_bias."""
    words = []
    for config in DIRECT_ACTIONS.values():
        words.extend(config.signal_words)
    for config in QUERY_ACTIONS.values():
        words.extend(config.signal_words)
    return words


def get_query_action(action_key: str) -> Optional[QueryActionConfig]:
    """Get a query action config by key."""
    return QUERY_ACTIONS.get(action_key)


def get_query_keys() -> list[str]:
    """Get all registered query action keys."""
    return list(QUERY_ACTIONS.keys())


def get_fields_description(action_key: str) -> str:
    """Generate field description for system prompt."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return ""
    parts = []
    for f in config.fields:
        req = " (WAJIB)" if f.required else ""
        desc = f" \u2014 {f.description}" if f.description else ""
        opts = " [{}]".format(", ".join(f.options)) if f.options else ""
        parts.append(f"  - {f.name}: {f.label}{req}{opts}{desc}")
    return "\n".join(parts)

