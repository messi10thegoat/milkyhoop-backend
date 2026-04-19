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
from typing import Optional


@dataclass
class FieldSpec:
    name: str
    label: str
    field_type: str = "string"  # string | number | boolean | enum | date
    required: bool = False
    default: Optional[str] = None
    options: list[str] = field(default_factory=list)  # for enum type
    description: str = ""
    hidden: bool = False  # In payload, NOT shown in confirmation table
    editable: bool = True  # Editable in inline review card
    display_only: bool = False  # Shown in table, stripped before REST call
    aliases: list[str] = field(
        default_factory=list
    )  # LLM variant names → normalized to `name`


@dataclass
class ImpactRule:
    """Conditional trust context shown below the confirmation table."""

    field: str  # payload field to check
    condition: str  # "zero" | "nonzero" | "always"
    message_template: str  # Python format string, e.g. "Saldo {formatted_value}"

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

    field: str  # payload field (e.g. "account_type")
    mapping: dict[str, str] = field(default_factory=dict)
    default: str = ""


@dataclass
class PreFlightCheck:
    """Pre-flight check before proposing an action to user."""

    endpoint: str  # "/api/items/{id}/can-delete"
    fail_action: str  # "reject" | "suggest_alternative" | "warn"
    fail_message_template: str  # "{name} sudah punya transaksi..."
    alternatives: list[str] = field(default_factory=list)


import re as _re  # noqa: E402


def _safe_format(template: str, payload: dict, **extra) -> str:
    """Format template with payload + extra kwargs. Missing keys -> placeholder stripped."""
    fmt_kwargs = {
        **{k: v for k, v in payload.items() if isinstance(v, (str, int, float))},
        **extra,
    }
    try:
        return template.format(**fmt_kwargs)
    except KeyError:
        # Strip any remaining {placeholder} patterns
        return _re.sub(r"\{[^}]+\}", "", template).strip()


@dataclass
class QueryParam:
    """Parameter specification for query actions."""

    name: str  # query param key
    label: str  # display label
    param_type: str = "string"  # string | date | enum | number
    required: bool = False
    default: str = ""


@dataclass
class QueryActionConfig:
    """Read-only query configuration — no mutations, no confirmation flow."""

    action_key: str
    display_name: str
    rest_endpoint: str  # GET endpoint
    rest_method: str = "GET"
    response_format: str = "summary"  # single_value | summary | table | list
    signal_words: list[str] = field(default_factory=list)
    query_params: list[QueryParam] = field(default_factory=list)
    description: str = ""  # for LLM tool description


@dataclass
class ChartQueryConfig(QueryActionConfig):
    """Query config that returns CHART message_type with visual chart data."""

    chart_type: str = "bar"  # line | bar | area | pie | donut | horizontal_bar
    complexity_hint: str = "simple"  # simple -> inline, complex -> artifact
    chart_features: dict = field(
        default_factory=dict
    )  # {brush: True, legend_toggle: True}


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
    at_least_one_groups: list[dict] = field(
        default_factory=list
    )  # [{fields: [...], label: "..."}]
    action_type_key: str = (
        ""  # uppercase key, e.g. "CREATE_BANK_ACCOUNT" (for event dispatch)
    )
    signal_words: list[str] = field(
        default_factory=list
    )  # triggers DIRECT ACTION mode in intent_bias

    # --- UX Metadata (scalable, no hardcoding in build functions) ---
    entity_name_field: str = "name"  # which payload field = entity display name
    loading_message_template: str = (
        "Memproses\u2026"  # e.g. "Membuat akun {entity_name}\u2026"
    )
    success_message_template: str = (
        "Berhasil dibuat."  # e.g. "Akun \'{entity_name}\' berhasil dibuat."
    )
    category: Optional[CategoryMapping] = None  # accounting category in confirm table
    impact_rules: list[ImpactRule] = field(default_factory=list)  # trust context rules
    pre_flight_checks: list[PreFlightCheck] = field(default_factory=list)
    journal_preview_endpoint: str = (
        ""  # POST endpoint for journal preview (creates_journal actions)
    )
    # --- Default CoA account resolution (parity with form UI) ---
    # Format: {"sales_account_id": (account_type, code_prefix, name_hint), ...}
    # Resolved at confirm time — injects *_id + friendly *_account name into payload.
    default_accounts_policy: dict = field(default_factory=dict)

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
        signal_words=[
            "rekening",
            "bank account",
            "akun bank",
            "kas toko",
            "buat rekening",
            "bikin rekening",
            "akun kas",
        ],
        entity_name_field="account_name",
        loading_message_template="Membuat akun {entity_name}\u2026",
        success_message_template="Akun '{entity_name}' berhasil dibuat.",
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
            FieldSpec(
                name="account_name",
                label="Nama Akun",
                required=True,
                description="Nama akun, misal 'Kas Toko' atau 'BCA Utama'",
            ),
            FieldSpec(
                name="account_type",
                label="Tipe Akun",
                field_type="enum",
                required=False,
                default="cash",
                options=["bank", "cash", "petty_cash", "e_wallet", "credit_card"],
                description="Jenis rekening",
            ),
            FieldSpec(
                name="bank_name",
                label="Nama Bank",
                required=False,
                description="Nama bank (BCA, Mandiri, dll). Kosongkan untuk kas.",
            ),
            FieldSpec(
                name="account_number",
                label="Nomor Rekening",
                required=False,
                description="Nomor rekening bank",
            ),
            FieldSpec(
                name="opening_balance",
                label="Saldo Awal",
                field_type="number",
                required=False,
                default="0",
                description="Saldo awal dalam Rupiah",
            ),
            FieldSpec(
                name="currency",
                label="Mata Uang",
                field_type="enum",
                required=False,
                default="IDR",
                options=["IDR", "USD", "EUR", "SGD"],
                description="Mata uang rekening",
            ),
            FieldSpec(
                name="is_default",
                label="Akun Utama",
                field_type="boolean",
                required=False,
                default="false",
                description="Jadikan sebagai rekening utama?",
            ),
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
        signal_words=[
            "vendor baru",
            "supplier baru",
            "tambah vendor",
            "tambah supplier",
            "buat vendor",
        ],
        entity_name_field="name",
        loading_message_template="Membuat vendor {entity_name}\u2026",
        success_message_template="Vendor '{entity_name}' berhasil dibuat.",
        category=None,  # Vendors don't have accounting category
        impact_rules=[],  # No impact rules for vendors
        fields=[
            FieldSpec(
                name="name",
                label="Nama Vendor",
                required=True,
                description="Nama vendor/supplier",
            ),
            FieldSpec(name="company_name", label="Nama Perusahaan", required=False),
            FieldSpec(name="phone", label="Telepon", required=False),
            FieldSpec(name="phone2", label="Telepon 2", required=False),
            FieldSpec(name="email", label="Email", required=False),
            FieldSpec(name="community", label="Komunitas/Organisasi", required=False),
            FieldSpec(name="address", label="Alamat", required=False),
            FieldSpec(name="tax_id", label="NPWP", required=False),
            FieldSpec(name="notes", label="Catatan", required=False),
            FieldSpec(
                name="bank_name",
                label="Nama Bank",
                required=False,
                description="Nama bank vendor",
            ),
            FieldSpec(
                name="bank_account_number",
                label="No Rekening Bank",
                required=False,
                description="Nomor rekening bank vendor",
            ),
            FieldSpec(
                name="bank_account_holder",
                label="Pemilik Rekening",
                required=False,
                description="Nama pemilik rekening bank",
            ),
            FieldSpec(
                name="bank_branch",
                label="Cabang Bank",
                required=False,
                description="Cabang bank vendor",
            ),
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
        signal_words=[
            "pelanggan baru",
            "customer baru",
            "tambah pelanggan",
            "tambah customer",
            "buat pelanggan",
            "daftarkan pelanggan",
        ],
        entity_name_field="name",
        loading_message_template="Membuat pelanggan {entity_name}…",
        success_message_template="Pelanggan '{entity_name}' berhasil dibuat.",
        category=None,
        impact_rules=[],
        fields=[
            FieldSpec(
                name="name",
                label="Nama Pelanggan",
                required=True,
                description="Nama pelanggan",
            ),
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
        signal_words=[
            "hapus rekening",
            "hapus akun",
            "delete account",
            "hapus kas",
            "buang akun",
        ],
        entity_name_field="account_name",
        loading_message_template="Menghapus akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil dihapus.",
        category=None,
        impact_rules=[],
        fields=[
            FieldSpec(
                name="account_id",
                label="ID Akun",
                required=True,
                description="UUID akun yang akan dihapus (dari search)",
            ),
            FieldSpec(
                name="account_name",
                label="Nama Akun",
                required=True,
                description="Nama akun untuk konfirmasi",
            ),
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
        journal_preview_endpoint="/api/bill-payments/preview-journal",
        signal_words=[
            "bayar faktur",
            "bayar bill",
            "lunasi faktur",
            "pembayaran vendor",
        ],
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
            FieldSpec(
                name="bill_id",
                label="Bill ID",
                required=False,
                hidden=True,
                aliases=["EXTRACT:allocations.bill_id"],
            ),
            FieldSpec(
                name="bank_account_id",
                label="Bank Account ID",
                required=True,
                hidden=True,
                aliases=["payment_account_id", "account_id"],
            ),
            FieldSpec(name="session_id", label="Session ID", hidden=True),
            FieldSpec(name="statement_line_id", label="Statement Line ID", hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
            FieldSpec(name="bill_number", label="No. Faktur", display_only=True),
            FieldSpec(
                name="bank_account_name", label="Dari Rekening", display_only=True
            ),
            # Hidden display (backend needs for context, redundant for user)
            FieldSpec(
                name="bill_amount",
                label="Total Faktur",
                field_type="number",
                hidden=True,
            ),
            FieldSpec(
                name="amount_due",
                label="Sisa Tagihan",
                field_type="number",
                hidden=True,
            ),
            FieldSpec(name="statement_description", label="Mutasi Bank", hidden=True),
            # Regular (shown + sent to backend)
            FieldSpec(
                name="total_amount",
                label="Jumlah Bayar",
                field_type="number",
                required=True,
                aliases=[
                    "EXTRACT:allocations.amount_applied",
                    "amount",
                    "payment_amount",
                ],
            ),
            FieldSpec(
                name="payment_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(
                name="payment_method",
                label="Metode",
                default="bank_transfer",
                hidden=True,
            ),
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
        journal_preview_endpoint="/api/receive-payments/preview-journal",
        signal_words=[
            "terima pembayaran",
            "pembayaran pelanggan",
            "bayar piutang",
            "pelunasan faktur",
        ],
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
            FieldSpec(
                name="customer_id", label="Customer ID", required=True, hidden=True
            ),
            FieldSpec(
                name="bank_account_id",
                label="Bank Account ID",
                required=True,
                hidden=True,
                aliases=["payment_account_id", "account_id"],
            ),
            FieldSpec(name="session_id", label="Session ID", hidden=True),
            FieldSpec(name="statement_line_id", label="Statement Line ID", hidden=True),
            FieldSpec(
                name="allocations",
                label="Allocations",
                field_type="json",
                required=False,  # auto-built by _enrich_receive_payment from oldest unpaid invoices
                hidden=True,
            ),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(name="customer_name", label="Pelanggan", display_only=True),
            FieldSpec(name="invoice_numbers", label="No. Faktur", display_only=True),
            FieldSpec(name="bank_account_name", label="Ke Rekening", display_only=True),
            FieldSpec(name="statement_description", label="Mutasi Bank", hidden=True),
            # Regular (shown + sent to backend)
            FieldSpec(
                name="total_amount",
                label="Jumlah Terima",
                field_type="number",
                required=True,
            ),
            FieldSpec(
                name="payment_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(
                name="payment_method",
                label="Metode",
                default="bank_transfer",
                hidden=True,
            ),
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
            "faktur penjualan",
            "invoice penjualan",
            "buat faktur",
            "bikin invoice",
            "jual ke",
            "tagih",
            "buat tagihan",
            "invoice baru",
            "faktur baru",
        ],
        journal_preview_endpoint="/api/sales-invoices/preview-journal",
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
            FieldSpec(
                name="customer_id",
                label="ID Pelanggan",
                required=True,
                hidden=True,
                description="UUID pelanggan — resolve via search_customers",
            ),
            FieldSpec(name="customer_name", label="Pelanggan", required=True),
            FieldSpec(
                name="invoice_date",
                label="Tanggal Faktur",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="due_date", label="Jatuh Tempo", field_type="date", required=True
            ),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {item_id, description, quantity, unit_price}",
            ),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
            FieldSpec(
                name="discount_percent",
                label="Diskon (%)",
                field_type="percent",
                default="0",
            ),
            FieldSpec(name="notes", label="Catatan"),
            FieldSpec(name="auto_post", label="Auto Post", default="true", hidden=True),
        ],
    ),
    "create_sales_order": DirectActionConfig(
        action_key="create_sales_order",
        display_name="Buat Pesanan Penjualan",
        rest_endpoint="/api/sales-orders",
        rest_method="POST",
        entity_type="sales_order",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_SALES_ORDER",
        signal_words=[
            "buat pesanan",
            "bikin pesanan",
            "pesanan baru",
            "sales order",
            "order penjualan",
            "buat SO",
            "bikin SO",
        ],
        entity_name_field="customer_name",
        loading_message_template="Membuat pesanan penjualan untuk {entity_name}\u2026",
        success_message_template="Pesanan penjualan untuk '{entity_name}' berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="Pajak {formatted_value}% akan diterapkan per item.",
            ),
        ],
        fields=[
            FieldSpec(
                name="customer_id",
                label="ID Pelanggan",
                required=True,
                hidden=True,
                description="UUID pelanggan — resolve via search_customers",
            ),
            FieldSpec(name="customer_name", label="Pelanggan", required=True),
            FieldSpec(
                name="order_date",
                label="Tanggal Pesanan",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="expected_ship_date",
                label="Tanggal Kirim",
                field_type="date",
            ),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {item_id, description, quantity, unit_price, unit}",
            ),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
            FieldSpec(
                name="discount_percent",
                label="Diskon (%)",
                field_type="percent",
                default="0",
            ),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
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
            "faktur pembelian",
            "bill",
            "tagihan masuk",
            "buat bill",
            "catat pembelian",
            "beli dari",
            "faktur supplier",
            "faktur vendor",
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
            FieldSpec(
                name="vendor_id",
                label="ID Vendor",
                hidden=True,
                description="UUID vendor — resolve via search_vendors",
            ),
            FieldSpec(name="vendor_name", label="Vendor", required=True),
            FieldSpec(name="issue_date", label="Tanggal Bill", field_type="date"),
            FieldSpec(
                name="due_date", label="Jatuh Tempo", field_type="date", required=True
            ),
            FieldSpec(name="invoice_number", label="No. Faktur Vendor"),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {product_id, product_name, qty, price, unit}",
            ),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
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
            "catat biaya",
            "catat pengeluaran",
            "bayar biaya",
            "bayar listrik",
            "bayar sewa",
            "bayar internet",
            "biaya operasional",
            "pengeluaran",
            "expense",
            "keluar uang",
        ],
        journal_preview_endpoint="/api/expenses/preview-journal",
        entity_name_field="description",
        loading_message_template="Mencatat biaya: {entity_name}\u2026",
        success_message_template="Biaya '{entity_name}' berhasil dicatat.",
        impact_rules=[
            ImpactRule(
                field="amount",
                condition="always",
                message_template="Saldo kas/bank akan berkurang {formatted_value}.",
            ),
        ],
        fields=[
            FieldSpec(
                name="expense_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(
                name="paid_through_id",
                label="Dibayar Dari",
                required=True,
                hidden=True,
                description="UUID akun kas/bank — resolve via search_bank_accounts",
                aliases=["bank_account_id", "payment_account_id"],
            ),
            FieldSpec(
                name="paid_through_name", label="Dibayar Dari", display_only=True
            ),
            FieldSpec(
                name="account_id",
                label="Akun Biaya",
                required=True,
                hidden=True,
                description="UUID akun biaya — resolve via search_accounts",
                aliases=["expense_account_id", "biaya_id"],
            ),
            FieldSpec(name="account_name", label="Akun Biaya", display_only=True),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(name="description", label="Deskripsi", required=True),
            FieldSpec(name="vendor_id", label="ID Vendor", hidden=True),
            FieldSpec(name="vendor_name", label="Vendor"),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
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
            "jurnal umum",
            "buat jurnal",
            "journal entry",
            "manual journal",
            "jurnal penyesuaian",
            "adjusting entry",
            "jurnal koreksi",
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
            FieldSpec(
                name="entry_date",
                label="Tanggal",
                field_type="date",
                required=True,
                aliases=["journal_date", "date", "tanggal"],
            ),
            FieldSpec(
                name="description",
                label="Keterangan",
                required=True,
                aliases=["memo", "keterangan", "note"],
            ),
            FieldSpec(
                name="lines",
                label="Baris Jurnal",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {account_id, description, debit, credit}. "
                "Law 4: total debit HARUS = total credit. Min 2 lines.",
            ),
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
            "sesuaikan stok",
            "stock adjustment",
            "koreksi stok",
            "tambah stok",
            "kurangi stok",
            "penyesuaian persediaan",
            "stok opname",
            "stock opname",
        ],
        journal_preview_endpoint="/api/stock-adjustments/preview-journal",
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
            FieldSpec(
                name="adjustment_date",
                label="Tanggal",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="adjustment_type",
                label="Tipe",
                field_type="enum",
                options=["increase", "decrease", "recount", "damaged", "expired"],
                required=True,
            ),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {product_id, quantity_adjustment, reason_detail}. "
                "quantity_adjustment: positive=increase, negative=decrease.",
            ),
            FieldSpec(
                name="storage_location_id", label="Lokasi Penyimpanan", hidden=True
            ),
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
        signal_words=[
            "batalkan faktur",
            "void invoice",
            "cancel invoice",
            "batal invoice",
        ],
        entity_name_field="invoice_number",
        loading_message_template="Membatalkan faktur {entity_name}\u2026",
        success_message_template="Faktur '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="invoice_number", label="No. Faktur", display_only=True),
            FieldSpec(
                name="reason",
                label="Alasan",
                required=True,
                aliases=[
                    "void_reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
        ],
    ),
    "update_sales_invoice": DirectActionConfig(
        action_key="update_sales_invoice",
        display_name="Ubah Faktur Penjualan",
        rest_endpoint="/api/sales-invoices/{id}",
        rest_method="PATCH",
        entity_type="sales_invoice",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_SALES_INVOICE",
        signal_words=[
            "ubah faktur",
            "edit faktur penjualan",
            "update invoice",
            "ganti faktur",
        ],
        entity_name_field="invoice_number",
        loading_message_template="Mengubah faktur {entity_name}\u2026",
        success_message_template="Faktur '{entity_name}' berhasil diubah.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="invoice_number", label="No. Faktur", display_only=True),
            FieldSpec(name="customer_id", label="ID Pelanggan", hidden=True),
            FieldSpec(name="customer_name", label="Pelanggan"),
            FieldSpec(name="invoice_date", label="Tanggal Faktur", field_type="date"),
            FieldSpec(name="due_date", label="Jatuh Tempo", field_type="date"),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                hidden=True,
                description="Optional replacement items array",
            ),
            FieldSpec(name="tax_rate", label="Pajak (%)", field_type="percent"),
            FieldSpec(
                name="discount_percent", label="Diskon (%)", field_type="percent"
            ),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "update_sales_order": DirectActionConfig(
        action_key="update_sales_order",
        display_name="Ubah Pesanan Penjualan",
        rest_endpoint="/api/sales-orders/{id}",
        rest_method="PATCH",
        entity_type="sales_order",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_SALES_ORDER",
        signal_words=[
            "ubah pesanan",
            "edit pesanan",
            "update SO",
            "ganti order penjualan",
        ],
        entity_name_field="order_number",
        loading_message_template="Mengubah pesanan {entity_name}\u2026",
        success_message_template="Pesanan '{entity_name}' berhasil diubah.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="order_number", label="No. Pesanan", display_only=True),
            FieldSpec(name="customer_id", label="ID Pelanggan", hidden=True),
            FieldSpec(name="customer_name", label="Pelanggan"),
            FieldSpec(name="order_date", label="Tanggal Pesanan", field_type="date"),
            FieldSpec(
                name="expected_ship_date", label="Tanggal Kirim", field_type="date"
            ),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                hidden=True,
                description="Optional replacement items array",
            ),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_sales_order": DirectActionConfig(
        action_key="void_sales_order",
        display_name="Batalkan Pesanan Penjualan",
        rest_endpoint="/api/sales-orders/{id}/cancel",
        rest_method="POST",
        entity_type="sales_order",
        risk_level="high",
        creates_journal=False,
        ttl_seconds=60,
        action_type_key="VOID_SALES_ORDER",
        signal_words=[
            "batalkan pesanan",
            "cancel pesanan",
            "void SO",
            "batal order penjualan",
        ],
        entity_name_field="order_number",
        loading_message_template="Membatalkan pesanan {entity_name}\u2026",
        success_message_template="Pesanan '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="order_number", label="No. Pesanan", display_only=True),
            FieldSpec(
                name="reason",
                label="Alasan",
                required=True,
                aliases=[
                    "void_reason",
                    "cancel_reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
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
        signal_words=[
            "batalkan bill",
            "void bill",
            "hapus faktur pembelian",
            "batal bill",
        ],
        entity_name_field="bill_number",
        loading_message_template="Membatalkan bill {entity_name}\u2026",
        success_message_template="Bill '{entity_name}' berhasil dibatalkan.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="bill_number", label="No. Bill", display_only=True),
            FieldSpec(
                name="reason",
                label="Alasan",
                required=True,
                aliases=[
                    "void_reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
        ],
    ),
    "post_bill": DirectActionConfig(
        action_key="post_bill",
        display_name="Posting Faktur Pembelian",
        rest_endpoint="/api/bills/{id}/post",
        rest_method="POST",
        entity_type="bill",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="POST_BILL",
        signal_words=[
            "posting bill",
            "posting faktur",
            "post bill",
            "posting tagihan",
            "approve bill",
            "posting faktur pembelian",
        ],
        entity_name_field="bill_number",
        loading_message_template="Memposting faktur {entity_name}…",
        success_message_template="Faktur '{entity_name}' berhasil diposting ke ledger.",
        impact_rules=[
            ImpactRule(
                field="grand_total",
                condition="always",
                message_template="Jurnal: Dr Persediaan + Cr Hutang Usaha sebesar {formatted_value}",
            ),
        ],
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="bill_number", label="No. Faktur", display_only=True),
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
            FieldSpec(
                name="grand_total",
                label="Total",
                field_type="number",
                display_only=True,
            ),
        ],
    ),
    "update_bill": DirectActionConfig(
        action_key="update_bill",
        display_name="Edit Faktur Pembelian",
        rest_endpoint="/api/bills/v2/{id}",
        rest_method="PATCH",
        entity_type="bill",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="UPDATE_BILL",
        signal_words=[
            "edit bill",
            "edit faktur",
            "ubah faktur",
            "update bill",
            "ganti vendor faktur",
            "edit tagihan",
        ],
        entity_name_field="bill_number",
        loading_message_template="Memperbarui faktur {entity_name}…",
        success_message_template="Faktur '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="bill_number", label="No. Faktur", display_only=True),
            FieldSpec(name="vendor_id", label="ID Vendor", hidden=True),
            FieldSpec(name="vendor_name", label="Vendor"),
            FieldSpec(name="issue_date", label="Tanggal", field_type="date"),
            FieldSpec(name="due_date", label="Jatuh Tempo", field_type="date"),
            FieldSpec(name="items", label="Item", field_type="json", hidden=True),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "delete_bill": DirectActionConfig(
        action_key="delete_bill",
        display_name="Hapus Faktur Pembelian",
        rest_endpoint="/api/bills/{id}",
        rest_method="DELETE",
        entity_type="bill",
        risk_level="medium",
        creates_journal=False,
        ttl_seconds=120,
        action_type_key="DELETE_BILL",
        signal_words=[
            "hapus bill",
            "hapus faktur",
            "delete bill",
            "buang faktur",
            "hapus tagihan",
        ],
        entity_name_field="bill_number",
        loading_message_template="Menghapus faktur {entity_name}…",
        success_message_template="Faktur '{entity_name}' berhasil dihapus.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="bill_number", label="No. Faktur", required=True),
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
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
        signal_words=[
            "batalkan pembayaran masuk",
            "void payment received",
            "batal terima bayaran",
        ],
        entity_name_field="payment_number",
        loading_message_template="Membatalkan pembayaran {entity_name}\u2026",
        success_message_template="Pembayaran '{entity_name}' berhasil dibatalkan. Invoice kembali outstanding.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="payment_number", label="No. Pembayaran", display_only=True),
            FieldSpec(
                name="void_reason",
                label="Alasan",
                required=True,
                aliases=[
                    "reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
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
        signal_words=[
            "batalkan pembayaran keluar",
            "void bill payment",
            "batal bayar tagihan",
        ],
        entity_name_field="payment_number",
        loading_message_template="Membatalkan pembayaran {entity_name}\u2026",
        success_message_template="Pembayaran '{entity_name}' berhasil dibatalkan. Bill kembali outstanding.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="payment_number", label="No. Pembayaran", display_only=True),
            FieldSpec(
                name="void_reason",
                label="Alasan",
                required=True,
                aliases=[
                    "reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
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
            FieldSpec(
                name="reason",
                label="Alasan",
                required=True,
                aliases=[
                    "void_reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
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
        signal_words=[
            "balik jurnal",
            "reverse journal",
            "batalkan jurnal",
            "koreksi jurnal",
        ],
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
            FieldSpec(
                name="reversal_date",
                label="Tanggal Reversal",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="reason",
                label="Alasan Reversal",
                required=True,
                aliases=[
                    "void_reason",
                    "description",
                    "alasan",
                    "note",
                    "notes",
                    "keterangan",
                ],
            ),
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
            FieldSpec(
                name="session_id", label="Session ID", required=True, hidden=True
            ),
            FieldSpec(
                name="action_ids", label="Action IDs", required=True, hidden=True
            ),
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
        signal_words=[
            "kategorisasi",
            "buat transaksi dari statement",
            "catat sebagai",
            "biaya bank",
            "admin bank",
            "biaya admin",
            "bunga bank",
            "ini biaya",
            "ini expense",
            "catat pengeluaran",
        ],
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
            FieldSpec(
                name="session_id", label="Session ID", required=True, hidden=True
            ),
            FieldSpec(
                name="statement_line_id", label="Line ID", required=True, hidden=True
            ),
            FieldSpec(
                name="account_id", label="Akun Tujuan", required=False, hidden=False
            ),
            FieldSpec(name="contact_id", label="Kontak ID", hidden=True),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(
                name="statement_description", label="Mutasi Bank", display_only=True
            ),
            FieldSpec(name="statement_date", label="Tanggal", display_only=True),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", display_only=True
            ),
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
        signal_words=[
            "cocok",
            "match ini",
            "cocokkan ini",
            "setuju match",
            "confirm match",
            "betul cocok",
            "ini pasangannya",
        ],
        entity_name_field="statement_line_id",
        loading_message_template="Mencocokkan statement line…",
        success_message_template="Berhasil dicocokkan.",
        impact_rules=[],
        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(
                name="session_id", label="Session ID", required=True, hidden=True
            ),
            FieldSpec(
                name="statement_line_id", label="Line ID", required=True, hidden=True
            ),
            FieldSpec(
                name="transaction_ids",
                label="Transaction IDs",
                required=True,
                hidden=True,
            ),
            FieldSpec(
                name="adjustment_account_id", label="Akun Penyesuaian ID", hidden=True
            ),
            # Display-only (user sees, stripped before REST call)
            FieldSpec(
                name="statement_description", label="Mutasi Bank", display_only=True
            ),
            FieldSpec(
                name="statement_amount",
                label="Jumlah Mutasi",
                field_type="number",
                display_only=True,
            ),
            FieldSpec(
                name="transaction_description",
                label="Transaksi Cocok",
                display_only=True,
            ),
            FieldSpec(
                name="transaction_amount",
                label="Jumlah Transaksi",
                field_type="number",
                display_only=True,
            ),
            FieldSpec(name="match_confidence", label="Confidence", display_only=True),
            # Regular (shown + sent to backend)
            FieldSpec(
                name="adjustment_amount",
                label="Jumlah Penyesuaian",
                field_type="number",
            ),
            FieldSpec(
                name="adjustment_account_name",
                label="Akun Penyesuaian",
                display_only=True,
            ),
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
        signal_words=[
            "konfirmasi draft",
            "setuju draft",
            "approve draft",
            "terima draft",
            "ok draft",
            "lanjut posting",
        ],
        entity_name_field="document_title",
        loading_message_template="Mengkonfirmasi draft {entity_name}...",
        success_message_template="Draft '{entity_name}' berhasil dikonfirmasi.",
        fields=[
            FieldSpec(
                name="document_id", label="Document ID", required=True, hidden=True
            ),
            FieldSpec(name="document_title", label="Dokumen", display_only=True),
            FieldSpec(name="doc_type", label="Tipe Dokumen", display_only=True),
            FieldSpec(
                name="counterparty_name", label="Pihak Terkait", display_only=True
            ),
            FieldSpec(
                name="journal_description", label="Keterangan Jurnal", display_only=True
            ),
            FieldSpec(
                name="total_debit",
                label="Total Debit",
                field_type="number",
                display_only=True,
            ),
            FieldSpec(
                name="total_credit",
                label="Total Kredit",
                field_type="number",
                display_only=True,
            ),
            FieldSpec(name="confidence", label="Confidence", display_only=True),
            FieldSpec(
                name="overrides", label="Overrides", field_type="string", hidden=True
            ),
        ],
        impact_rules=[
            ImpactRule(
                field="total_debit",
                condition="nonzero",
                message_template="Jurnal senilai {formatted_value} akan dibuat setelah posting.",
            ),
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
        success_message_template="Draft '{entity_name}' ditolak.",
        fields=[
            FieldSpec(
                name="document_id", label="Document ID", required=True, hidden=True
            ),
            FieldSpec(name="document_title", label="Dokumen", display_only=True),
            FieldSpec(name="doc_type", label="Tipe Dokumen", display_only=True),
            FieldSpec(
                name="reason",
                label="Alasan Penolakan",
                required=False,
                description="Alasan kenapa draft ditolak",
            ),
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
        default_accounts_policy={
            "sales_account_id": ("REVENUE", "4-10100", "penjualan"),
            "purchase_account_id": ("EXPENSE", "5-20900", "lain"),
            "inventory_account_id": ("ASSET", "1-10600", "persediaan"),
            "cogs_account_id": ("COGS", "5-10100", "hpp"),
        },
        at_least_one_groups=[
            {
                "fields": ["sales_price", "purchase_price"],
                "label": "Harga Jual atau Harga Beli (minimal salah satu)",
            },
        ],
        signal_words=[
            "barang baru",
            "item baru",
            "tambah barang",
            "tambah item",
            "produk baru",
            "jasa baru",
            "tambah produk",
            "tambah jasa",
        ],
        entity_name_field="name",
        loading_message_template="Membuat barang/jasa {entity_name}…",
        success_message_template="Barang/jasa '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(name="name", label="Nama", required=True),
            FieldSpec(
                name="item_type",
                label="Tipe",
                field_type="enum",
                required=True,
                options=["persediaan", "jasa", "non-persediaan"],
                description="goods=Barang (track stok), service=Jasa (tanpa stok), non_inventory=Barang tanpa stok",
            ),
            FieldSpec(
                name="base_unit",
                label="Satuan",
                required=True,
                description="Contoh: pcs, roll, meter, kg, box, tube",
            ),
            FieldSpec(
                name="sales_price",
                label="Harga Jual",
                field_type="number",
                aliases=["harga_jual", "unit_price", "selling_price"],
            ),
            FieldSpec(
                name="purchase_price",
                label="Harga Beli",
                field_type="number",
                aliases=["harga_beli", "buying_price", "cost_price"],
            ),
            FieldSpec(name="sku", label="Kode/SKU"),
            FieldSpec(name="description", label="Deskripsi"),
            FieldSpec(
                name="kategori",
                label="Kategori",
                aliases=["category", "category_name", "kategori_barang"],
                description="Kategori barang, contoh: Bahan Kain, Elektronik, Makanan",
            ),
            FieldSpec(
                name="purchase_tax",
                label="Pajak Beli",
                aliases=["pajak_beli", "tax_purchase", "ppn_beli"],
                description="Kode pajak pembelian, contoh: PPN 12%, PPN 11%",
            ),
            FieldSpec(
                name="sales_tax",
                label="Pajak Jual",
                aliases=["pajak_jual", "tax_sales", "ppn_jual"],
                description="Kode pajak penjualan, contoh: PPN 12%, PPN 11%",
            ),
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
        signal_words=[
            "akun baru",
            "tambah akun",
            "buat akun",
            "akun CoA baru",
            "chart of accounts baru",
        ],
        entity_name_field="name",
        loading_message_template="Membuat akun {entity_name}…",
        success_message_template="Akun '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(
                name="code",
                label="Kode Akun",
                required=True,
                description="Contoh: 1-10700, 5-20100",
                aliases=["account_code", "kode_akun"],
            ),
            FieldSpec(name="name", label="Nama Akun", required=True),
            FieldSpec(
                name="type",
                label="Tipe",
                field_type="enum",
                required=True,
                options=[
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
                aliases=["account_type", "account_type_coa", "tipe"],
            ),
            FieldSpec(
                name="normal_balance",
                label="Saldo Normal",
                field_type="enum",
                options=["DEBIT", "CREDIT"],
                description="Auto-derived: DEBIT untuk Aset/Beban, CREDIT untuk Liabilitas/Ekuitas/Pendapatan",
            ),
            FieldSpec(
                name="parent_id",
                label="Akun Induk",
                description="UUID akun induk (opsional)",
            ),
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
            FieldSpec(
                name="code",
                label="Kode Gudang",
                description="Kode unik gudang, e.g. GD-CIKUPA. Auto-generated dari nama jika tidak diisi.",
            ),
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
        signal_words=[
            "edit pelanggan",
            "ubah pelanggan",
            "ganti nama pelanggan",
            "update pelanggan",
            "perbarui pelanggan",
        ],
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
        signal_words=[
            "edit vendor",
            "ubah vendor",
            "ganti nama vendor",
            "update vendor",
            "edit supplier",
            "rekening vendor",
            "data bank vendor",
            "nomor rekening vendor",
            "tambah rekening vendor",
            "ubah rekening vendor",
            "bank vendor",
            "update rekening vendor",
        ],
        entity_name_field="name",
        loading_message_template="Memperbarui vendor {entity_name}…",
        success_message_template="Vendor '{entity_name}' berhasil diperbarui.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Vendor"),
            FieldSpec(name="phone", label="Telepon"),
            FieldSpec(name="email", label="Email"),
            FieldSpec(name="address", label="Alamat"),
            FieldSpec(
                name="bank_name",
                label="Nama Bank",
                description="Nama bank vendor (BCA, Mandiri, dll)",
            ),
            FieldSpec(
                name="bank_account_number",
                label="No Rekening Bank",
                description="Nomor rekening bank vendor",
            ),
            FieldSpec(
                name="bank_account_holder",
                label="Pemilik Rekening",
                description="Nama pemilik rekening bank",
            ),
            FieldSpec(
                name="bank_branch",
                label="Cabang Bank",
                description="Cabang bank vendor",
            ),
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
        signal_words=[
            "edit barang",
            "ubah barang",
            "ganti harga",
            "update item",
            "ubah harga jual",
            "ganti nama barang",
        ],
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
        signal_words=[
            "edit akun kas",
            "ubah akun kas",
            "ganti nama akun kas",
            "update bank account",
            "edit akun bank",
            "ubah rekening kas",
            "edit rekening perusahaan",
            "update akun kas bank",
        ],
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
        signal_words=[
            "edit akun",
            "ubah akun",
            "ganti nama akun",
            "update CoA",
            "rename akun",
        ],
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
        signal_words=[
            "edit gudang",
            "ubah gudang",
            "ganti nama gudang",
            "update warehouse",
        ],
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
    # ============ ITEM MANAGEMENT ============
    "toggle_item_status": DirectActionConfig(
        action_key="toggle_item_status",
        display_name="Ubah Status Barang",
        rest_endpoint="/api/items/{id}/status",
        rest_method="PATCH",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="TOGGLE_ITEM_STATUS",
        signal_words=[
            "nonaktifkan barang",
            "aktifkan barang",
            "disable item",
            "enable item",
            "matikan barang",
            "hidupkan barang",
            "nonaktifkan produk",
            "aktifkan produk",
        ],
        entity_name_field="name",
        loading_message_template="Mengubah status {entity_name}\u2026",
        success_message_template="Status '{entity_name}' berhasil diubah.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="name", label="Nama Barang", display_only=True),
            FieldSpec(
                name="status",
                label="Status Baru",
                field_type="enum",
                options=["active", "inactive"],
                required=True,
            ),
        ],
    ),
    "create_category": DirectActionConfig(
        action_key="create_category",
        display_name="Buat Kategori",
        rest_endpoint="/api/items/categories",
        rest_method="POST",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_CATEGORY",
        signal_words=[
            "kategori baru",
            "tambah kategori",
            "buat kategori",
            "bikin kategori",
        ],
        entity_name_field="name",
        loading_message_template="Membuat kategori {entity_name}\u2026",
        success_message_template="Kategori '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(name="name", label="Nama Kategori", required=True),
            FieldSpec(name="description", label="Deskripsi"),
        ],
    ),
    "create_unit": DirectActionConfig(
        action_key="create_unit",
        display_name="Buat Satuan",
        rest_endpoint="/api/items/units",
        rest_method="POST",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_UNIT",
        signal_words=[
            "satuan baru",
            "tambah satuan",
            "buat satuan",
            "bikin satuan",
            "unit baru",
        ],
        entity_name_field="name",
        loading_message_template="Membuat satuan {entity_name}\u2026",
        success_message_template="Satuan '{entity_name}' berhasil dibuat.",
        fields=[
            FieldSpec(
                name="name",
                label="Nama Satuan",
                required=True,
                description="Contoh: pcs, kg, meter, roll, lusin, box",
            ),
            FieldSpec(
                name="abbreviation", label="Singkatan", description="Contoh: pcs, kg, m"
            ),
        ],
    ),
    "duplicate_item": DirectActionConfig(
        action_key="duplicate_item",
        display_name="Duplikasi Barang",
        rest_endpoint="/api/items/{id}/duplicate",
        rest_method="POST",
        entity_type="item",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="DUPLICATE_ITEM",
        signal_words=[
            "duplikasi barang",
            "copy barang",
            "gandakan barang",
            "duplikat produk",
            "copy item",
        ],
        entity_name_field="name",
        loading_message_template="Menduplikasi {entity_name}\u2026",
        success_message_template="'{entity_name}' berhasil diduplikasi.",
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(
                name="name", label="Dari Barang", required=True, display_only=True
            ),
            FieldSpec(
                name="new_name",
                label="Nama Baru",
                description="Nama untuk produk duplikat. Default: nama asli + ' (2)'",
            ),
        ],
    ),
    # ═══════════════ BATCH 3: Credit Note Actions ═══════════════
    "create_credit_note": DirectActionConfig(
        action_key="create_credit_note",
        display_name="Buat Nota Kredit",
        rest_endpoint="/api/credit-notes",
        rest_method="POST",
        entity_type="credit_note",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_CREDIT_NOTE",
        signal_words=["buat nota kredit", "create credit note", "nota kredit baru"],
        entity_name_field="credit_note_number",
        loading_message_template="Membuat nota kredit…",
        success_message_template="Nota kredit berhasil dibuat.",
        fields=[
            FieldSpec(
                name="customer_id", label="ID Pelanggan", required=True, hidden=True
            ),
            FieldSpec(name="customer_name", label="Pelanggan", display_only=True),
            FieldSpec(
                name="credit_note_date",
                label="Tanggal",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(name="reason", label="Alasan", required=True),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_credit_note": DirectActionConfig(
        action_key="void_credit_note",
        display_name="Void Nota Kredit",
        rest_endpoint="/api/credit-notes/{id}/void",
        rest_method="POST",
        entity_type="credit_note",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="VOID_CREDIT_NOTE",
        signal_words=["void nota kredit", "batalkan nota kredit"],
        entity_name_field="credit_note_number",
        loading_message_template="Membatalkan nota kredit…",
        success_message_template="Nota kredit berhasil di-void.",
        fields=[
            FieldSpec(name="id", label="Credit Note ID", required=True, hidden=True),
            FieldSpec(
                name="credit_note_number", label="No. Nota Kredit", display_only=True
            ),
            FieldSpec(name="reason", label="Alasan Void", required=True),
        ],
    ),
    # ═══════════════ BATCH 3: Vendor Credit Actions ═══════════════
    "create_vendor_credit": DirectActionConfig(
        action_key="create_vendor_credit",
        display_name="Buat Vendor Credit",
        rest_endpoint="/api/vendor-credits",
        rest_method="POST",
        entity_type="vendor_credit",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_VENDOR_CREDIT",
        signal_words=["buat vendor credit", "create vendor credit"],
        entity_name_field="vendor_credit_number",
        loading_message_template="Membuat vendor credit…",
        success_message_template="Vendor credit berhasil dibuat.",
        fields=[
            FieldSpec(name="vendor_id", label="ID Vendor", required=True, hidden=True),
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
            FieldSpec(
                name="vendor_credit_date",
                label="Tanggal",
                field_type="date",
                required=True,
            ),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(name="reason", label="Alasan", required=True),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_vendor_credit": DirectActionConfig(
        action_key="void_vendor_credit",
        display_name="Void Vendor Credit",
        rest_endpoint="/api/vendor-credits/{id}/void",
        rest_method="POST",
        entity_type="vendor_credit",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="VOID_VENDOR_CREDIT",
        signal_words=["void vendor credit", "batalkan vendor credit"],
        entity_name_field="vendor_credit_number",
        loading_message_template="Membatalkan vendor credit…",
        success_message_template="Vendor credit berhasil di-void.",
        fields=[
            FieldSpec(name="id", label="Vendor Credit ID", required=True, hidden=True),
            FieldSpec(
                name="vendor_credit_number",
                label="No. Vendor Credit",
                display_only=True,
            ),
            FieldSpec(name="reason", label="Alasan Void", required=True),
        ],
    ),
    # ═══════════════ BATCH 3: Quote Actions ═══════════════
    "create_quote": DirectActionConfig(
        action_key="create_quote",
        display_name="Buat Penawaran",
        rest_endpoint="/api/quotes",
        rest_method="POST",
        entity_type="quote",
        risk_level="low",
        creates_journal=False,
        ttl_seconds=300,
        action_type_key="CREATE_QUOTE",
        signal_words=["buat penawaran", "create quote", "bikin quotation"],
        entity_name_field="quote_number",
        loading_message_template="Membuat penawaran…",
        success_message_template="Penawaran berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="Pajak {formatted_value}% akan diterapkan per item.",
            ),
        ],
        fields=[
            FieldSpec(
                name="customer_id",
                label="ID Pelanggan",
                required=True,
                hidden=True,
                description="UUID pelanggan — resolve via search_customers",
            ),
            FieldSpec(name="customer_name", label="Pelanggan", required=True),
            FieldSpec(
                name="quote_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(name="expiry_date", label="Berlaku Sampai", field_type="date"),
            FieldSpec(name="subject", label="Judul Penawaran"),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                description="Array of {item_id, description, quantity, unit_price, unit}",
            ),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
            FieldSpec(
                name="discount_value", label="Diskon", field_type="number", default="0"
            ),
            FieldSpec(
                name="discount_type",
                label="Tipe Diskon",
                default="percentage",
                hidden=True,
            ),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    # ═══════════════ BATCH 3: Bank Transfer Actions ═══════════════
    "create_bank_transfer": DirectActionConfig(
        action_key="create_bank_transfer",
        display_name="Transfer Antar Bank",
        rest_endpoint="/api/bank-transfers",
        rest_method="POST",
        entity_type="bank_transfer",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_BANK_TRANSFER",
        signal_words=["transfer bank", "pindah dana", "transfer antar rekening"],
        entity_name_field="reference",
        loading_message_template="Memproses transfer bank…",
        success_message_template="Transfer bank berhasil dicatat.",
        impact_rules=[
            ImpactRule(
                field="amount",
                condition="always",
                message_template="Dana Rp {formatted_value} akan dipindah antar rekening.",
            ),
        ],
        fields=[
            FieldSpec(
                name="from_account_id",
                label="Dari Rekening",
                required=True,
                hidden=True,
            ),
            FieldSpec(
                name="from_account_name", label="Dari Rekening", display_only=True
            ),
            FieldSpec(
                name="to_account_id", label="Ke Rekening", required=True, hidden=True
            ),
            FieldSpec(name="to_account_name", label="Ke Rekening", display_only=True),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(
                name="transfer_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_bank_transfer": DirectActionConfig(
        action_key="void_bank_transfer",
        display_name="Void Transfer Bank",
        rest_endpoint="/api/bank-transfers/{id}/void",
        rest_method="POST",
        entity_type="bank_transfer",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="VOID_BANK_TRANSFER",
        signal_words=["void transfer", "batalkan transfer bank"],
        entity_name_field="reference",
        loading_message_template="Membatalkan transfer bank…",
        success_message_template="Transfer bank berhasil di-void.",
        fields=[
            FieldSpec(name="id", label="Transfer ID", required=True, hidden=True),
            FieldSpec(name="reference", label="Referensi", display_only=True),
            FieldSpec(name="reason", label="Alasan Void", required=True),
        ],
    ),
    # ═══════════════ BATCH 3: Customer Deposit Actions ═══════════════
    "create_customer_deposit": DirectActionConfig(
        action_key="create_customer_deposit",
        display_name="Catat Deposit Pelanggan",
        rest_endpoint="/api/customer-deposits",
        rest_method="POST",
        entity_type="customer_deposit",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_CUSTOMER_DEPOSIT",
        signal_words=["deposit pelanggan", "uang muka pelanggan", "terima deposit"],
        entity_name_field="reference",
        loading_message_template="Mencatat deposit pelanggan…",
        success_message_template="Deposit pelanggan berhasil dicatat.",
        fields=[
            FieldSpec(
                name="customer_id", label="ID Pelanggan", required=True, hidden=True
            ),
            FieldSpec(name="customer_name", label="Pelanggan", display_only=True),
            FieldSpec(
                name="bank_account_id", label="Rekening", required=True, hidden=True
            ),
            FieldSpec(name="bank_account_name", label="Rekening", display_only=True),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(
                name="deposit_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_customer_deposit": DirectActionConfig(
        action_key="void_customer_deposit",
        display_name="Void Deposit Pelanggan",
        rest_endpoint="/api/customer-deposits/{id}/void",
        rest_method="POST",
        entity_type="customer_deposit",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="VOID_CUSTOMER_DEPOSIT",
        signal_words=["void deposit pelanggan", "batalkan deposit pelanggan"],
        entity_name_field="reference",
        loading_message_template="Membatalkan deposit pelanggan…",
        success_message_template="Deposit pelanggan berhasil di-void.",
        fields=[
            FieldSpec(name="id", label="Deposit ID", required=True, hidden=True),
            FieldSpec(name="reference", label="Referensi", display_only=True),
            FieldSpec(name="reason", label="Alasan Void", required=True),
        ],
    ),
    # ═══════════════ BATCH 3: Vendor Deposit Actions ═══════════════
    "create_vendor_deposit": DirectActionConfig(
        action_key="create_vendor_deposit",
        display_name="Catat Deposit Vendor",
        rest_endpoint="/api/vendor-deposits",
        rest_method="POST",
        entity_type="vendor_deposit",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=300,
        action_type_key="CREATE_VENDOR_DEPOSIT",
        signal_words=["deposit vendor", "uang muka vendor", "bayar deposit vendor"],
        entity_name_field="reference",
        loading_message_template="Mencatat deposit vendor…",
        success_message_template="Deposit vendor berhasil dicatat.",
        fields=[
            FieldSpec(name="vendor_id", label="ID Vendor", required=True, hidden=True),
            FieldSpec(name="vendor_name", label="Vendor", display_only=True),
            FieldSpec(
                name="bank_account_id", label="Rekening", required=True, hidden=True
            ),
            FieldSpec(name="bank_account_name", label="Rekening", display_only=True),
            FieldSpec(
                name="amount", label="Jumlah", field_type="number", required=True
            ),
            FieldSpec(
                name="deposit_date", label="Tanggal", field_type="date", required=True
            ),
            FieldSpec(name="reference", label="Referensi"),
            FieldSpec(name="notes", label="Catatan"),
        ],
    ),
    "void_vendor_deposit": DirectActionConfig(
        action_key="void_vendor_deposit",
        display_name="Void Deposit Vendor",
        rest_endpoint="/api/vendor-deposits/{id}/void",
        rest_method="POST",
        entity_type="vendor_deposit",
        risk_level="high",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="VOID_VENDOR_DEPOSIT",
        signal_words=["void deposit vendor", "batalkan deposit vendor"],
        entity_name_field="reference",
        loading_message_template="Membatalkan deposit vendor…",
        success_message_template="Deposit vendor berhasil di-void.",
        fields=[
            FieldSpec(name="id", label="Deposit ID", required=True, hidden=True),
            FieldSpec(name="reference", label="Referensi", display_only=True),
            FieldSpec(name="reason", label="Alasan Void", required=True),
        ],
    ),
}


# \u2500\u2500\u2500 Query Actions Registry \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# READ-ONLY queries \u2014 no mutations, no confirmation flow.
# Law 16: all financial numbers derive from journal_lines.

QUERY_ACTIONS: dict[str, QueryActionConfig] = {
    # AR/AP Outstanding (ARAP Rule 5/6 compliant — journal-derived)
    "query_ar_outstanding": QueryActionConfig(
        action_key="query_ar_outstanding",
        display_name="Piutang (AR Outstanding)",
        rest_endpoint="/api/sales-invoices/outstanding-summary",
        response_format="summary",
        description="Ringkasan piutang: total outstanding, overdue, current, jumlah pelanggan. Journal-derived via compute_ar_outstanding().",
        signal_words=[
            "piutang",
            "total piutang",
            "ar outstanding",
            "siapa yang punya piutang",
            "berapa piutang",
        ],
        query_params=[],
    ),
    "query_ar_invoices": QueryActionConfig(
        action_key="query_ar_invoices",
        display_name="Faktur Belum Lunas",
        rest_endpoint="/api/sales-invoices",
        response_format="list",
        description="Daftar faktur penjualan yang belum lunas. status=unpaid excludes draft/void/paid.",
        signal_words=["faktur belum lunas", "siapa yang punya piutang"],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="unpaid"
            ),
            QueryParam(name="limit", label="Limit", param_type="number", default="50"),
        ],
    ),
    "query_ap_outstanding": QueryActionConfig(
        action_key="query_ap_outstanding",
        display_name="Utang (AP Outstanding)",
        rest_endpoint="/api/bills/outstanding-summary",
        response_format="summary",
        description="Ringkasan hutang: total outstanding, overdue, current, jumlah vendor. Journal-derived via compute_ap_outstanding().",
        signal_words=[
            "utang",
            "total utang",
            "ap outstanding",
            "hutang",
            "siapa yang punya hutang",
            "berapa hutang",
        ],
        query_params=[],
    ),
    # Kas & Bank
    "query_bank_accounts_list": QueryActionConfig(
        action_key="query_bank_accounts_list",
        display_name="Daftar Rekening",
        rest_endpoint="/api/bank-accounts",
        response_format="list",
        description="Daftar semua rekening bank/kas.",
        signal_words=["daftar rekening", "list rekening", "semua rekening"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20")
        ],
    ),
    "query_bank_account_detail": QueryActionConfig(
        action_key="query_bank_account_detail",
        display_name="Detail Rekening",
        rest_endpoint="/api/bank-accounts/{id}",
        response_format="detail",
        description="Detail 1 rekening bank/kas.",
        signal_words=["detail rekening", "info rekening", "cek rekening"],
        query_params=[
            QueryParam(
                name="id", label="Account ID", param_type="string", required=True
            )
        ],
    ),
    "query_bank_transactions": QueryActionConfig(
        action_key="query_bank_transactions",
        display_name="Transaksi Bank",
        rest_endpoint="/api/bank-accounts/{id}/transactions",
        response_format="list",
        description="Daftar transaksi di rekening tertentu.",
        signal_words=["transaksi bank", "mutasi bank", "mutasi rekening"],
        query_params=[
            QueryParam(
                name="id", label="Account ID", param_type="string", required=True
            ),
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # Faktur Penjualan
    "query_sales_invoices_list": QueryActionConfig(
        action_key="query_sales_invoices_list",
        display_name="Daftar Faktur Penjualan",
        rest_endpoint="/api/sales-invoices",
        response_format="list",
        description="Daftar semua faktur penjualan.",
        signal_words=["daftar faktur penjualan", "list faktur penjualan"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20")
        ],
    ),
    "query_sales_invoice_detail": QueryActionConfig(
        action_key="query_sales_invoice_detail",
        display_name="Detail Faktur Penjualan",
        rest_endpoint="/api/sales-invoices/{id}",
        response_format="detail",
        description="Detail 1 faktur penjualan.",
        signal_words=["detail faktur penjualan"],
        query_params=[
            QueryParam(
                name="id", label="Invoice ID", param_type="string", required=True
            )
        ],
    ),
    "query_sales_invoices_summary": QueryActionConfig(
        action_key="query_sales_invoices_summary",
        display_name="Ringkasan Faktur Penjualan",
        rest_endpoint="/api/sales-invoices",
        response_format="list",
        description="Ringkasan faktur penjualan.",
        signal_words=["ringkasan penjualan", "total penjualan"],
        query_params=[],
    ),
    # Faktur Pembelian
    "query_bills_list": QueryActionConfig(
        action_key="query_bills_list",
        display_name="Daftar Faktur Pembelian",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Daftar faktur pembelian yang belum lunas. status=unpaid excludes draft/void/paid.",
        signal_words=["daftar faktur pembelian", "list tagihan"],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="unpaid"
            ),
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    "query_bill_detail": QueryActionConfig(
        action_key="query_bill_detail",
        display_name="Detail Faktur Pembelian",
        rest_endpoint="/api/bills/{id}",
        response_format="detail",
        description="Detail 1 faktur pembelian.",
        signal_words=["detail faktur pembelian"],
        query_params=[
            QueryParam(name="id", label="Bill ID", param_type="string", required=True)
        ],
    ),
    "query_bills_summary": QueryActionConfig(
        action_key="query_bills_summary",
        display_name="Ringkasan Faktur Pembelian",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Ringkasan faktur pembelian.",
        signal_words=["ringkasan pembelian", "total pembelian"],
        query_params=[],
    ),
    # Expense
    "query_expenses_list": QueryActionConfig(
        action_key="query_expenses_list",
        display_name="Daftar Pengeluaran",
        rest_endpoint="/api/expenses",
        response_format="list",
        description="Daftar semua pengeluaran.",
        signal_words=["daftar pengeluaran", "list pengeluaran"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20")
        ],
    ),
    "query_expense_detail": QueryActionConfig(
        action_key="query_expense_detail",
        display_name="Detail Pengeluaran",
        rest_endpoint="/api/expenses/{id}",
        response_format="detail",
        description="Detail 1 pengeluaran.",
        signal_words=["detail pengeluaran"],
        query_params=[
            QueryParam(
                name="id", label="Expense ID", param_type="string", required=True
            )
        ],
    ),
    "query_expenses_summary": QueryActionConfig(
        action_key="query_expenses_summary",
        display_name="Ringkasan Pengeluaran",
        rest_endpoint="/api/expenses/summary",
        response_format="summary",
        description="Ringkasan pengeluaran.",
        signal_words=["ringkasan pengeluaran", "total pengeluaran"],
        query_params=[],
    ),
    "query_cash_balance": QueryActionConfig(
        action_key="query_cash_balance",
        display_name="Saldo Kas & Bank",
        rest_endpoint="/api/kasbank/stats",
        response_format="single_value",
        description="Saldo kas, bank, dan total. Termasuk arus masuk/keluar hari ini.",
        signal_words=[
            "saldo kas",
            "uang kas",
            "cash balance",
            "saldo bank",
            "berapa saldo",
            "total kas",
            "uang di bank",
            "saldo rekening",
            "kas berapa",
            "dana tersedia",
        ],
    ),
    "query_profit_loss": QueryActionConfig(
        action_key="query_profit_loss",
        display_name="Laporan Laba Rugi",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        response_format="summary",
        description="Laba rugi: pendapatan, HPP, laba kotor, beban operasional, laba bersih.",
        signal_words=[
            "laba rugi",
            "profit loss",
            "P&L",
            "income statement",
            "pendapatan",
            "untung rugi",
            "margin",
            "net income",
            "berapa laba",
            "berapa rugi",
            "keuntungan",
        ],
        query_params=[],
    ),
    "query_balance_sheet": QueryActionConfig(
        action_key="query_balance_sheet",
        display_name="Neraca",
        rest_endpoint="/api/reports/neraca/{periode}",
        response_format="summary",
        description="Neraca: aset, kewajiban, ekuitas. Cek apakah balance.",
        signal_words=[
            "neraca",
            "balance sheet",
            "posisi keuangan",
            "total aset",
            "total kewajiban",
            "ekuitas",
        ],
        query_params=[
            QueryParam(
                name="periode", label="Periode", param_type="string", default="current"
            ),
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
            QueryParam(
                name="periode", label="Periode", param_type="string", default="current"
            ),
        ],
    ),
    "query_ar_aging": QueryActionConfig(
        action_key="query_ar_aging",
        display_name="Aging Piutang",
        rest_endpoint="/api/reports/ar-aging",
        response_format="summary",
        description="Aging piutang: current, 1-30, 31-60, 61-90, 91-120, >120 hari.",
        signal_words=[
            "piutang",
            "receivable",
            "yang belum bayar",
            "overdue",
            "hutang pelanggan",
            "tagihan belum dibayar",
            "aging piutang",
            "umur piutang",
            "berapa piutang",
        ],
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
        signal_words=[
            "hutang usaha",
            "payable",
            "aging hutang",
            "umur hutang",
            "tagihan vendor",
            "berapa hutang",
            "kewajiban vendor",
        ],
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
        signal_words=[
            "ringkasan faktur",
            "invoice summary",
            "faktur overdue",
            "berapa faktur",
            "outstanding invoice",
            "faktur belum bayar",
        ],
    ),
    "query_bills_outstanding": QueryActionConfig(
        action_key="query_bills_outstanding",
        display_name="Tagihan Outstanding",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Tagihan belum bayar: total outstanding, overdue, vendor count, urgency.",
        signal_words=[
            "tagihan outstanding",
            "tagihan belum bayar",
            "bills outstanding",
            "berapa tagihan",
            "tagihan overdue",
        ],
    ),
    "query_trial_balance": QueryActionConfig(
        action_key="query_trial_balance",
        display_name="Neraca Saldo",
        rest_endpoint="/api/reports/trial-balance",
        response_format="table",
        description="Neraca saldo: semua akun dengan debit/credit balance. Cek apakah balance.",
        signal_words=[
            "neraca saldo",
            "trial balance",
            "saldo akun",
            "balance semua akun",
            "daftar saldo",
        ],
        query_params=[],
    ),
    "query_top_expenses": QueryActionConfig(
        action_key="query_top_expenses",
        display_name="Top Pengeluaran",
        rest_endpoint="/api/dashboard/top-expenses",
        response_format="table",
        description="Top kategori pengeluaran dengan persentase.",
        signal_words=[
            "pengeluaran terbesar",
            "top expenses",
            "biaya terbesar",
            "kategori pengeluaran",
            "beban terbesar",
        ],
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
        signal_words=[
            "ringkasan beban",
            "expense summary",
            "total beban",
            "berapa beban",
            "pengeluaran bulan ini",
        ],
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
        signal_words=[
            "periode akuntansi",
            "fiscal period",
            "daftar periode",
            "periode buka",
            "periode tutup",
        ],
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
        signal_words=[
            "grafik pendapatan",
            "grafik beban",
            "chart laba rugi",
            "visualisasi pendapatan",
            "grafik revenue",
            "chart pendapatan beban",
            "tunjukkan grafik",
            "tampilkan grafik",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_cash_flow": ChartQueryConfig(
        action_key="chart_cash_flow",
        display_name="Arus Kas",
        rest_endpoint="/api/reports/arus-kas/{periode}",
        description="Grafik arus kas: operasional, investasi, pendanaan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=[
            "grafik arus kas",
            "grafik cash flow",
            "chart cash flow",
            "visualisasi arus kas",
            "tren kas",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_expense_breakdown": ChartQueryConfig(
        action_key="chart_expense_breakdown",
        display_name="Komposisi Beban",
        rest_endpoint="/api/dashboard/top-expenses",
        description="Grafik donut komposisi beban per kategori.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "grafik beban kategori",
            "pie chart beban",
            "komposisi beban",
            "kemana uang pergi",
            "breakdown beban",
            "chart expense",
        ],
    ),
    "chart_top_customers": ChartQueryConfig(
        action_key="chart_top_customers",
        display_name="Top Pelanggan",
        rest_endpoint="/api/reports/pendapatan/{periode}",
        description="Grafik bar horizontal pelanggan dengan revenue tertinggi.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "grafik pelanggan",
            "top pelanggan",
            "chart customer",
            "pelanggan terbesar",
            "ranking pelanggan",
        ],
        query_params=[
            QueryParam(
                name="periode", label="Periode", param_type="string", default=""
            ),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="5"),
        ],
    ),
    "chart_ar_aging": ChartQueryConfig(
        action_key="chart_ar_aging",
        display_name="Aging Piutang",
        rest_endpoint="/api/reports/aging-trend",
        description="Grafik tren aging piutang over time.",
        chart_type="line",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=[
            "grafik piutang",
            "tren piutang",
            "chart aging",
            "grafik receivable",
            "visualisasi piutang",
        ],
    ),
    # ═══════════════ BATCH 1: Dashboard & KPI (6) ═══════════════
    "chart_kas_composition": ChartQueryConfig(
        action_key="chart_kas_composition",
        display_name="Komposisi Kas & Bank",
        rest_endpoint="/api/dashboard/kas-bank",
        description="Grafik donut komposisi saldo kas dan bank.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "grafik kas",
            "komposisi kas",
            "chart bank",
            "saldo kas bank",
            "pie chart kas",
            "distribusi kas",
        ],
    ),
    "chart_cash_projection": ChartQueryConfig(
        action_key="chart_cash_projection",
        display_name="Proyeksi Arus Kas",
        rest_endpoint="/api/dashboard/cash-flow-projection",
        description="Grafik proyeksi arus kas ke depan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=[
            "proyeksi kas",
            "cash projection",
            "prediksi kas",
            "forecast kas",
            "grafik proyeksi",
        ],
    ),
    "chart_overdue_invoices": ChartQueryConfig(
        action_key="chart_overdue_invoices",
        display_name="Invoice Jatuh Tempo",
        rest_endpoint="/api/dashboard/overdue-invoices",
        description="Grafik invoice yang sudah jatuh tempo per pelanggan.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "grafik overdue invoice",
            "invoice jatuh tempo",
            "chart piutang overdue",
            "tagihan terlambat",
        ],
        query_params=[
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10")
        ],
    ),
    "chart_overdue_bills": ChartQueryConfig(
        action_key="chart_overdue_bills",
        display_name="Tagihan Jatuh Tempo",
        rest_endpoint="/api/dashboard/overdue-bills",
        description="Grafik tagihan supplier yang sudah jatuh tempo.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "grafik overdue bill",
            "tagihan jatuh tempo",
            "chart hutang overdue",
            "bill terlambat",
        ],
    ),
    "chart_cash_flow_trends": ChartQueryConfig(
        action_key="chart_cash_flow_trends",
        display_name="Tren Kas Masuk & Keluar",
        rest_endpoint="/api/dashboard/cash-flow-trends",
        description="Grafik tren kas masuk dan keluar harian/mingguan.",
        chart_type="area",
        complexity_hint="simple",
        chart_features={"legend_toggle": True},
        signal_words=[
            "tren kas",
            "grafik kas masuk keluar",
            "cash flow trend",
            "aliran kas",
            "uang masuk keluar",
        ],
        query_params=[
            QueryParam(name="months", label="Bulan", param_type="number", default="6")
        ],
    ),
    "chart_dashboard_kpi": ChartQueryConfig(
        action_key="chart_dashboard_kpi",
        display_name="KPI Dashboard",
        rest_endpoint="/api/dashboard/summary",
        description="Grafik ringkasan KPI: pendapatan, beban, piutang, hutang, kas.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "grafik kpi",
            "dashboard chart",
            "ringkasan grafik",
            "chart overview",
            "grafik ringkasan",
        ],
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
        signal_words=[
            "grafik neraca",
            "chart balance sheet",
            "visualisasi neraca",
            "grafik aset kewajiban",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_neraca_composition": ChartQueryConfig(
        action_key="chart_neraca_composition",
        display_name="Komposisi Aset",
        rest_endpoint="/api/reports/neraca/{periode}",
        description="Grafik donut komposisi aset perusahaan.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=["komposisi aset", "pie aset", "distribusi aset", "chart aset"],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_profit_trend": ChartQueryConfig(
        action_key="chart_profit_trend",
        display_name="Tren Laba Rugi",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        description="Grafik tren pendapatan, beban, dan laba bulanan.",
        chart_type="line",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=[
            "tren laba",
            "profit trend",
            "grafik laba bulanan",
            "tren pendapatan",
            "monthly profit",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_profit_comparison": ChartQueryConfig(
        action_key="chart_profit_comparison",
        display_name="Perbandingan Laba Rugi",
        rest_endpoint="/api/reports/profit-loss/comparison",
        description="Grafik perbandingan laba rugi dua periode.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=[
            "perbandingan laba",
            "comparison profit",
            "bandingkan laba rugi",
            "laba bulan lalu vs sekarang",
        ],
        query_params=[
            QueryParam(
                name="period1_start",
                label="Periode 1 Mulai",
                param_type="date",
                default="",
            ),
            QueryParam(
                name="period1_end",
                label="Periode 1 Akhir",
                param_type="date",
                default="",
            ),
            QueryParam(
                name="period2_start",
                label="Periode 2 Mulai",
                param_type="date",
                default="",
            ),
            QueryParam(
                name="period2_end",
                label="Periode 2 Akhir",
                param_type="date",
                default="",
            ),
        ],
    ),
    "chart_gross_margin": ChartQueryConfig(
        action_key="chart_gross_margin",
        display_name="Margin Kotor",
        rest_endpoint="/api/reports/laba-rugi/{periode}",
        description="Grafik revenue, HPP, dan gross profit.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "grafik margin",
            "gross margin chart",
            "chart hpp",
            "grafik margin kotor",
            "revenue vs hpp",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
    ),
    "chart_monthly_cashflow": ChartQueryConfig(
        action_key="chart_monthly_cashflow",
        display_name="Arus Kas Bulanan",
        rest_endpoint="/api/reports/arus-kas/{periode}",
        description="Grafik arus kas operasional, investasi, pendanaan.",
        chart_type="area",
        complexity_hint="complex",
        chart_features={"brush": True, "legend_toggle": True},
        signal_words=[
            "arus kas bulanan",
            "monthly cash flow",
            "grafik arus kas detail",
        ],
        query_params=[
            QueryParam(name="periode", label="Periode", param_type="string", default="")
        ],
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
        signal_words=[
            "grafik hutang",
            "aging hutang",
            "chart ap aging",
            "grafik payable",
            "visualisasi hutang",
            "aging vendor",
        ],
    ),
    "chart_ar_summary": ChartQueryConfig(
        action_key="chart_ar_summary",
        display_name="Ringkasan Piutang",
        rest_endpoint="/api/dashboard/piutang",
        description="Grafik donut ringkasan piutang per bucket.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "ringkasan piutang",
            "donut piutang",
            "pie piutang",
            "distribusi piutang",
        ],
    ),
    "chart_ap_summary": ChartQueryConfig(
        action_key="chart_ap_summary",
        display_name="Ringkasan Hutang",
        rest_endpoint="/api/dashboard/hutang",
        description="Grafik donut ringkasan hutang per bucket.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "ringkasan hutang",
            "donut hutang",
            "pie hutang",
            "distribusi hutang",
        ],
    ),
    "chart_invoice_status": ChartQueryConfig(
        action_key="chart_invoice_status",
        display_name="Status Invoice",
        rest_endpoint="/api/sales-invoices/summary",
        description="Grafik donut status invoice: draft, posted, partial, paid.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "grafik invoice",
            "status invoice",
            "chart sales invoice",
            "distribusi invoice",
            "pie invoice",
        ],
    ),
    "chart_bill_status": ChartQueryConfig(
        action_key="chart_bill_status",
        display_name="Status Tagihan",
        rest_endpoint="/api/bills/summary",
        description="Grafik donut status tagihan: paid, partial, unpaid, overdue.",
        chart_type="donut",
        complexity_hint="simple",
        signal_words=[
            "grafik tagihan",
            "status bill",
            "chart bill",
            "distribusi tagihan",
            "pie tagihan",
        ],
    ),
    "chart_payment_trends": ChartQueryConfig(
        action_key="chart_payment_trends",
        display_name="Tren Pembayaran",
        rest_endpoint="/api/bill-payments/summary",
        description="Grafik pembayaran per metode.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "grafik pembayaran",
            "payment trend",
            "chart payment",
            "tren bayar",
            "metode pembayaran",
        ],
    ),
    # ═══════════════ BATCH 4: Inventory & Products (5) ═══════════════
    "chart_top_products": ChartQueryConfig(
        action_key="chart_top_products",
        display_name="Produk Terlaris",
        rest_endpoint="/api/inventory/top-products",
        description="Grafik produk terlaris berdasarkan qty terjual.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "produk terlaris",
            "top product",
            "barang laris",
            "chart produk",
            "ranking produk",
            "grafik penjualan produk",
        ],
        query_params=[
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10")
        ],
    ),
    "chart_product_margins": ChartQueryConfig(
        action_key="chart_product_margins",
        display_name="Margin Produk",
        rest_endpoint="/api/inventory/product-margins",
        description="Grafik margin per produk: revenue, COGS, profit.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=[
            "margin produk",
            "product margin",
            "keuntungan produk",
            "profit per produk",
            "grafik margin produk",
        ],
        query_params=[
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10")
        ],
    ),
    "chart_slow_moving": ChartQueryConfig(
        action_key="chart_slow_moving",
        display_name="Produk Lambat Terjual",
        rest_endpoint="/api/inventory/slow-moving-products",
        description="Grafik produk yang lambat terjual.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "produk lambat",
            "slow moving",
            "barang tidak laku",
            "dead stock",
            "grafik stok lambat",
        ],
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
        signal_words=[
            "tren penjualan",
            "sales trend",
            "grafik penjualan",
            "penjualan harian",
            "daily sales",
            "grafik sales",
        ],
    ),
    "chart_top_vendors": ChartQueryConfig(
        action_key="chart_top_vendors",
        display_name="Top Vendor",
        rest_endpoint="/api/vendors",
        description="Grafik vendor dengan saldo hutang terbesar.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "top vendor",
            "vendor terbesar",
            "grafik vendor",
            "ranking vendor",
            "chart supplier",
        ],
        query_params=[
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10")
        ],
    ),
    # ═══════════════ BATCH 5: Financial Ratios (4) ═══════════════
    "chart_profitability_ratios": ChartQueryConfig(
        action_key="chart_profitability_ratios",
        display_name="Rasio Profitabilitas",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio profitabilitas: ROA, ROE, margin.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "rasio profitabilitas",
            "profitability ratio",
            "grafik roa roe",
            "chart margin ratio",
            "rasio keuntungan",
        ],
    ),
    "chart_liquidity_ratios": ChartQueryConfig(
        action_key="chart_liquidity_ratios",
        display_name="Rasio Likuiditas",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio likuiditas: cash, quick, current ratio.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "rasio likuiditas",
            "liquidity ratio",
            "grafik current ratio",
            "chart quick ratio",
            "rasio lancar",
        ],
    ),
    "chart_leverage_ratios": ChartQueryConfig(
        action_key="chart_leverage_ratios",
        display_name="Rasio Leverage",
        rest_endpoint="/api/financial-ratios",
        description="Grafik rasio leverage: debt to equity, debt to asset.",
        chart_type="bar",
        complexity_hint="simple",
        signal_words=[
            "rasio leverage",
            "debt ratio",
            "grafik hutang modal",
            "chart leverage",
            "rasio solvabilitas",
        ],
    ),
    "chart_ratio_dashboard": ChartQueryConfig(
        action_key="chart_ratio_dashboard",
        display_name="Dashboard Rasio Keuangan",
        rest_endpoint="/api/financial-ratios",
        description="Dashboard lengkap semua rasio keuangan.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=[
            "dashboard rasio",
            "semua rasio",
            "financial ratio dashboard",
            "grafik rasio lengkap",
            "rasio keuangan",
        ],
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
        signal_words=[
            "budget vs actual",
            "realisasi anggaran",
            "grafik budget",
            "chart anggaran",
            "budget realisasi",
        ],
        query_params=[
            QueryParam(
                name="budget_id", label="Budget ID", param_type="string", required=True
            )
        ],
    ),
    "chart_variance_alerts": ChartQueryConfig(
        action_key="chart_variance_alerts",
        display_name="Peringatan Varians",
        rest_endpoint="/api/budgets/variance-alerts",
        description="Grafik item budget yang melebihi/di bawah target.",
        chart_type="horizontal_bar",
        complexity_hint="simple",
        signal_words=[
            "varians budget",
            "variance alert",
            "over budget",
            "grafik varians",
            "penyimpangan anggaran",
        ],
    ),
    "chart_production_costs": ChartQueryConfig(
        action_key="chart_production_costs",
        display_name="Biaya Produksi",
        rest_endpoint="/api/production/{order_id}/cost-analysis",
        description="Grafik analisis biaya produksi: material, labor, overhead.",
        chart_type="bar",
        complexity_hint="complex",
        chart_features={"legend_toggle": True},
        signal_words=[
            "biaya produksi",
            "production cost",
            "grafik cost analysis",
            "analisis biaya",
            "chart produksi",
        ],
        query_params=[
            QueryParam(
                name="order_id", label="Order ID", param_type="string", required=True
            )
        ],
    ),
    # ============ ITEM & INVENTORY QUERIES ============
    "query_item_detail": QueryActionConfig(
        action_key="query_item_detail",
        display_name="Detail Barang",
        rest_endpoint="/api/items/{id}",
        response_format="summary",
        description="Detail produk: stok, harga, WAC, nilai. Resolve item by name first.",
        signal_words=[
            "stok",
            "berapa stok",
            "stock",
            "persediaan",
            "harga barang",
            "detail barang",
            "info barang",
            "cek barang",
            "lihat barang",
        ],
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True)
        ],
    ),
    "query_item_stock_card": QueryActionConfig(
        action_key="query_item_stock_card",
        display_name="Kartu Stok",
        rest_endpoint="/api/items/{id}/history",
        response_format="table",
        description="Kartu stok / stock card: riwayat in/out per tanggal.",
        signal_words=[
            "kartu stok",
            "stock card",
            "riwayat stok",
            "mutasi stok",
            "pergerakan stok",
            "history stok",
        ],
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "query_item_transactions": QueryActionConfig(
        action_key="query_item_transactions",
        display_name="Transaksi Barang",
        rest_endpoint="/api/items/{id}/transactions",
        response_format="table",
        description="Riwayat transaksi produk: pembelian, penjualan, adjustment.",
        signal_words=[
            "transaksi barang",
            "riwayat transaksi",
            "history pembelian",
            "history penjualan",
            "transaksi produk",
        ],
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "query_customer_detail": QueryActionConfig(
        action_key="query_customer_detail",
        display_name="Detail Pelanggan",
        rest_endpoint="/api/customers/{id}",
        response_format="detail",
        description="Detail pelanggan: nama, telepon, email, alamat, piutang.",
        signal_words=["pelanggan", "customer", "data pelanggan"],
        query_params=[],
    ),
    "query_vendor_detail": QueryActionConfig(
        action_key="query_vendor_detail",
        display_name="Detail Vendor",
        rest_endpoint="/api/vendors/{id}",
        response_format="detail",
        description="Detail vendor/pemasok: nama, telepon, email, alamat, hutang.",
        signal_words=["vendor", "pemasok", "supplier", "data vendor"],
        query_params=[],
    ),
    "query_items_summary": QueryActionConfig(
        action_key="query_items_summary",
        display_name="Ringkasan Barang",
        rest_endpoint="/api/items/summary",
        response_format="summary",
        description="Ringkasan: total produk aktif, nilai stok, dll.",
        signal_words=[
            "ringkasan barang",
            "ringkasan produk",
            "ringkasan inventory",
            "total barang",
            "berapa barang",
            "ada berapa produk",
            "summary barang",
            "statistik barang",
        ],
    ),
    "query_items_low_stock": QueryActionConfig(
        action_key="query_items_low_stock",
        display_name="Stok Rendah",
        rest_endpoint="/api/inventory/low-stock",
        response_format="table",
        description="Barang dengan stok di bawah reorder level atau stok habis.",
        signal_words=[
            "stok rendah",
            "low stock",
            "stok habis",
            "stok menipis",
            "barang hampir habis",
            "perlu restock",
            "reorder",
            "stok kritis",
        ],
        query_params=[
            QueryParam(
                name="include_zero_stock",
                label="Include Zero Stock",
                param_type="string",
                default="true",
            ),
        ],
    ),
    "query_items_top_products": QueryActionConfig(
        action_key="query_items_top_products",
        display_name="Produk Terlaris",
        rest_endpoint="/api/inventory/top-products",
        response_format="table",
        description="Produk terlaris berdasarkan qty terjual.",
        signal_words=[
            "barang terlaris",
            "produk terlaris",
            "top produk",
            "paling laku",
            "best seller",
            "terjual terbanyak",
        ],
        query_params=[
            QueryParam(
                name="period",
                label="Periode",
                param_type="string",
                default="this_month",
            ),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="5"),
        ],
    ),
    "query_items_slow_moving": QueryActionConfig(
        action_key="query_items_slow_moving",
        display_name="Barang Lambat Terjual",
        rest_endpoint="/api/inventory/slow-moving-products",
        response_format="table",
        description="Barang yang lambat terjual / dead stock.",
        signal_words=[
            "barang lambat",
            "slow moving",
            "tidak laku",
            "dead stock",
            "barang lama",
            "barang mati",
        ],
        query_params=[
            QueryParam(name="days", label="Hari", param_type="number", default="30"),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "query_items_margins": QueryActionConfig(
        action_key="query_items_margins",
        display_name="Margin Produk",
        rest_endpoint="/api/inventory/product-margins",
        response_format="table",
        description="Margin per produk: revenue, COGS, profit, margin %.",
        signal_words=[
            "margin produk",
            "keuntungan produk",
            "margin barang",
            "profit per produk",
            "margin per item",
        ],
        query_params=[
            QueryParam(
                name="period",
                label="Periode",
                param_type="string",
                default="this_month",
            ),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
            QueryParam(
                name="sort", label="Urutan", param_type="string", default="margin_desc"
            ),
        ],
    ),
    "query_warehouse_stock": QueryActionConfig(
        action_key="query_warehouse_stock",
        display_name="Stok per Gudang",
        rest_endpoint="/api/warehouses/{id}/stock",
        response_format="table",
        description="Stok barang di gudang tertentu.",
        signal_words=[
            "stok gudang",
            "barang di gudang",
            "isi gudang",
            "stok di",
            "warehouse stock",
        ],
        query_params=[
            QueryParam(
                name="id", label="Warehouse ID", param_type="string", required=True
            )
        ],
    ),
    # ─── Items Module: Additional Queries (wired 2026-03-09) ─────────────
    "query_items_search": QueryActionConfig(
        action_key="query_items_search",
        display_name="Cari Barang",
        rest_endpoint="/api/items",
        response_format="table",
        description="Cari barang berdasarkan nama, kode, atau barcode.",
        signal_words=[
            "cari barang",
            "cari produk",
            "search barang",
            "barang yang namanya",
            "ada barang",
            "produk apa saja",
        ],
        query_params=[
            QueryParam(
                name="search", label="Kata kunci", param_type="string", required=True
            ),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
            QueryParam(
                name="status", label="Status", param_type="string", default="active"
            ),
        ],
    ),
    "query_items_by_stock": QueryActionConfig(
        action_key="query_items_by_stock",
        display_name="Ranking Stok Barang",
        rest_endpoint="/api/items?limit=50&status=active",
        response_format="detail",
        description="Ranking barang berdasarkan jumlah stok (terbanyak/tersedikit).",
        signal_words=[
            "stok paling banyak",
            "item terbanyak stoknya",
            "stok tertinggi",
            "urutan stok",
            "ranking stok",
            "stok terbanyak",
        ],
    ),
    "query_items_units": QueryActionConfig(
        action_key="query_items_units",
        display_name="Daftar Satuan",
        rest_endpoint="/api/items/units",
        response_format="detail",
        description="Daftar satuan produk (pcs, kg, box, dll).",
        signal_words=[
            "daftar satuan",
            "list satuan",
            "satuan apa saja",
            "unit apa saja",
            "ada satuan apa",
        ],
    ),
    "query_items_stats": QueryActionConfig(
        action_key="query_items_stats",
        display_name="Statistik Barang",
        rest_endpoint="/api/items/stats?status=active",
        response_format="summary",
        description="Statistik barang masuk/keluar.",
        signal_words=[
            "statistik barang",
            "stats barang",
            "barang masuk keluar",
            "pergerakan barang",
        ],
    ),
    "query_items_inactive": QueryActionConfig(
        action_key="query_items_inactive",
        display_name="Barang Tidak Aktif",
        rest_endpoint="/api/items?status=inactive",
        response_format="detail",
        description="Daftar barang yang sudah dinonaktifkan.",
        signal_words=[
            "item tidak aktif",
            "barang nonaktif",
            "produk dinonaktifkan",
            "item inactive",
            "barang tidak aktif",
            "item dinonaktifkan",
        ],
    ),
    "query_inventory_summary": QueryActionConfig(
        action_key="query_inventory_summary",
        display_name="Ringkasan Inventory",
        rest_endpoint="/api/inventory/summary",
        response_format="summary",
        description="Ringkasan inventory: total nilai stok, jumlah item, dll.",
        signal_words=[
            "ringkasan inventory",
            "inventory summary",
            "total stok",
            "nilai inventory",
            "nilai persediaan",
        ],
    ),
    "query_stock_adjustments": QueryActionConfig(
        action_key="query_stock_adjustments",
        display_name="Daftar Penyesuaian Stok",
        rest_endpoint="/api/stock-adjustments",
        response_format="table",
        description="Daftar penyesuaian stok (draft/posted/void).",
        signal_words=[
            "penyesuaian stok",
            "stock adjustment",
            "daftar adjustment",
            "riwayat penyesuaian",
            "opname",
        ],
        query_params=[
            QueryParam(name="status", label="Status", param_type="string"),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "query_stock_adjustments_summary": QueryActionConfig(
        action_key="query_stock_adjustments_summary",
        display_name="Ringkasan Penyesuaian Stok",
        rest_endpoint="/api/stock-adjustments/summary",
        response_format="summary",
        description="Ringkasan penyesuaian stok: jumlah draft/posted/void.",
        signal_words=[
            "ringkasan penyesuaian",
            "summary adjustment",
            "total penyesuaian",
        ],
    ),
    "query_stock_transfers": QueryActionConfig(
        action_key="query_stock_transfers",
        display_name="Daftar Transfer Stok",
        rest_endpoint="/api/stock-transfers",
        response_format="table",
        description="Daftar transfer stok antar gudang.",
        signal_words=[
            "transfer stok",
            "stock transfer",
            "daftar transfer",
            "pemindahan barang",
            "kirim barang antar gudang",
        ],
        query_params=[
            QueryParam(name="limit", label="Jumlah", param_type="number", default="10"),
        ],
    ),
    "query_stock_in_transit": QueryActionConfig(
        action_key="query_stock_in_transit",
        display_name="Barang Dalam Perjalanan",
        rest_endpoint="/api/stock-transfers/in-transit",
        response_format="table",
        description="Barang yang sedang dalam perjalanan antar gudang.",
        signal_words=[
            "dalam perjalanan",
            "in transit",
            "belum diterima",
            "barang kirim",
            "sedang dikirim",
        ],
    ),
    "query_warehouses": QueryActionConfig(
        action_key="query_warehouses",
        display_name="Daftar Gudang",
        rest_endpoint="/api/warehouses",
        response_format="list",
        description="Daftar gudang yang tersedia.",
        signal_words=[
            "daftar gudang",
            "list gudang",
            "ada gudang apa",
            "gudang apa saja",
            "warehouse",
        ],
    ),
    "query_warehouse_stock_value": QueryActionConfig(
        action_key="query_warehouse_stock_value",
        display_name="Nilai Stok Gudang",
        rest_endpoint="/api/warehouses/{id}/stock-value",
        response_format="summary",
        description="Total nilai stok di gudang tertentu.",
        signal_words=[
            "nilai stok gudang",
            "value gudang",
            "harga stok gudang",
            "total nilai gudang",
        ],
        query_params=[
            QueryParam(
                name="id", label="Warehouse ID", param_type="string", required=True
            )
        ],
    ),
    "query_inventory_health": QueryActionConfig(
        action_key="query_inventory_health",
        display_name="Health Check Inventory",
        rest_endpoint="/api/inventory/health",
        response_format="summary",
        description="Health check inventory: consistency, anomalies.",
        signal_words=[
            "health inventory",
            "cek inventory",
            "inventory sehat",
            "anomali stok",
            "konsistensi stok",
        ],
    ),
    "query_item_journal": QueryActionConfig(
        action_key="query_item_journal",
        display_name="Jurnal Barang",
        rest_endpoint="/api/items/{id}/journal-entries",
        response_format="table",
        description="Journal entries terkait barang tertentu.",
        signal_words=[
            "jurnal barang",
            "journal barang",
            "jurnal terkait",
            "ayat jurnal barang",
        ],
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True)
        ],
    ),
    "query_item_activity": QueryActionConfig(
        action_key="query_item_activity",
        display_name="Aktivitas Barang",
        rest_endpoint="/api/items/{id}/activity",
        response_format="detail",
        signal_words=[
            "aktivitas barang",
            "log barang",
            "siapa edit",
            "history perubahan",
            "activity log",
        ],
        description="Log aktivitas/perubahan pada item. Resolve item by name first.",
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True)
        ],
    ),
    "query_item_related": QueryActionConfig(
        action_key="query_item_related",
        display_name="Dokumen Terkait Barang",
        rest_endpoint="/api/items/{id}/related",
        response_format="detail",
        signal_words=[
            "dokumen terkait",
            "faktur barang",
            "transaksi pakai barang",
            "invoice barang",
            "bill barang",
        ],
        description="Dokumen terkait item (faktur, tagihan, PO). Resolve item by name first.",
        query_params=[
            QueryParam(name="id", label="Item ID", param_type="string", required=True)
        ],
    ),
    "query_item_batches": QueryActionConfig(
        action_key="query_item_batches",
        display_name="Batch & Expiry Barang",
        rest_endpoint="/api/item-batches",
        response_format="detail",
        signal_words=[
            "batch",
            "expiry",
            "kadaluarsa",
            "lot number",
            "expired",
            "tanggal kedaluwarsa",
        ],
        description="Batch dan tanggal kadaluarsa item. Resolve item by name first.",
        query_params=[
            QueryParam(
                name="item_id", label="Item ID", param_type="string", required=True
            )
        ],
    ),
    "query_categories_list": QueryActionConfig(
        action_key="query_categories_list",
        display_name="Daftar Kategori",
        rest_endpoint="/api/items/categories",
        response_format="list",
        description="Daftar kategori produk.",
        signal_words=[
            "daftar kategori",
            "list kategori",
            "kategori apa saja",
            "ada kategori apa",
        ],
    ),
    # ═══════════════ BATCH 1 QUERY INTENTS ═══════════════
    # Customer AR (entity-specific)
    "query_customer_ar": QueryActionConfig(
        action_key="query_customer_ar",
        display_name="Piutang Pelanggan",
        rest_endpoint="/api/customers/{id}",
        response_format="detail",
        description="Detail pelanggan termasuk saldo piutang (AR). Resolve customer by name first.",
        signal_words=["piutang pelanggan", "ar pelanggan", "tagihan pelanggan"],
        query_params=[
            QueryParam(
                name="id", label="Customer ID", param_type="string", required=True
            ),
        ],
    ),
    # Vendor AP (entity-specific)
    "query_vendor_ap": QueryActionConfig(
        action_key="query_vendor_ap",
        display_name="Hutang Vendor",
        rest_endpoint="/api/vendors/{id}",
        response_format="detail",
        description="Detail vendor termasuk saldo hutang (AP). Resolve vendor by name first.",
        signal_words=["hutang vendor", "ap vendor", "utang vendor"],
        query_params=[
            QueryParam(
                name="id", label="Vendor ID", param_type="string", required=True
            ),
        ],
    ),
    # Sales invoices overdue
    "query_sales_invoices_overdue": QueryActionConfig(
        action_key="query_sales_invoices_overdue",
        display_name="Faktur Penjualan Jatuh Tempo",
        rest_endpoint="/api/sales-invoices",
        response_format="list",
        description="Faktur penjualan yang jatuh tempo. Polish: filter overdue dari response.",
        signal_words=["faktur jatuh tempo", "invoice overdue", "faktur terlambat"],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="active"
            ),
            QueryParam(name="limit", label="Limit", param_type="number", default="50"),
        ],
    ),
    # Bills overdue
    "query_bills_overdue": QueryActionConfig(
        action_key="query_bills_overdue",
        display_name="Tagihan Jatuh Tempo",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Tagihan/faktur pembelian yang jatuh tempo. Polish: filter overdue dari response.",
        signal_words=["tagihan jatuh tempo", "bill overdue", "tagihan terlambat"],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="active"
            ),
            QueryParam(name="limit", label="Limit", param_type="number", default="50"),
        ],
    ),
    # Expenses by account
    "query_expenses_by_account": QueryActionConfig(
        action_key="query_expenses_by_account",
        display_name="Pengeluaran per Akun",
        rest_endpoint="/api/expenses",
        response_format="list",
        description="Daftar pengeluaran. Polish: filter by account name dari response.",
        signal_words=["pengeluaran untuk", "biaya untuk", "expense akun"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="50"),
        ],
    ),
    # Receive payments list
    "query_receive_payments_list": QueryActionConfig(
        action_key="query_receive_payments_list",
        display_name="Daftar Penerimaan Pembayaran",
        rest_endpoint="/api/receive-payments",
        response_format="list",
        description="Daftar semua penerimaan pembayaran.",
        signal_words=["daftar penerimaan", "list receive payment", "pembayaran masuk"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # Receive payment detail
    "query_receive_payment_detail": QueryActionConfig(
        action_key="query_receive_payment_detail",
        display_name="Detail Penerimaan Pembayaran",
        rest_endpoint="/api/receive-payments/{id}",
        response_format="detail",
        description="Detail 1 penerimaan pembayaran.",
        signal_words=["detail penerimaan", "detail receive payment"],
        query_params=[
            QueryParam(
                name="id", label="Payment ID", param_type="string", required=True
            ),
        ],
    ),
    # Bill payments list
    "query_bill_payments_list": QueryActionConfig(
        action_key="query_bill_payments_list",
        display_name="Daftar Pembayaran Tagihan",
        rest_endpoint="/api/bill-payments",
        response_format="list",
        description="Daftar semua pembayaran tagihan.",
        signal_words=[
            "daftar pembayaran tagihan",
            "list bill payment",
            "pembayaran keluar",
        ],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # Bill payment detail
    "query_bill_payment_detail": QueryActionConfig(
        action_key="query_bill_payment_detail",
        display_name="Detail Pembayaran Tagihan",
        rest_endpoint="/api/bill-payments/{id}",
        response_format="detail",
        description="Detail 1 pembayaran tagihan.",
        signal_words=["detail pembayaran tagihan", "detail bill payment"],
        query_params=[
            QueryParam(
                name="id", label="Payment ID", param_type="string", required=True
            ),
        ],
    ),
    # Journals list
    "query_journals_list": QueryActionConfig(
        action_key="query_journals_list",
        display_name="Daftar Jurnal",
        rest_endpoint="/api/journals",
        response_format="list",
        description="Daftar semua jurnal.",
        signal_words=["daftar jurnal", "list jurnal", "semua jurnal"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # Journal detail
    "query_journal_detail": QueryActionConfig(
        action_key="query_journal_detail",
        display_name="Detail Jurnal",
        rest_endpoint="/api/journals/{id}",
        response_format="detail",
        description="Detail 1 jurnal entry.",
        signal_words=["detail jurnal", "info jurnal"],
        query_params=[
            QueryParam(
                name="id", label="Journal ID", param_type="string", required=True
            ),
        ],
    ),
    # Accounts list
    "query_accounts_list": QueryActionConfig(
        action_key="query_accounts_list",
        display_name="Daftar Akun",
        rest_endpoint="/api/accounts",
        response_format="list",
        description="Daftar semua akun (chart of accounts).",
        signal_words=["daftar akun", "list akun", "chart of accounts", "coa"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="50"),
        ],
    ),
    # Account detail
    "query_account_detail": QueryActionConfig(
        action_key="query_account_detail",
        display_name="Detail Akun",
        rest_endpoint="/api/accounts/{account_id}",
        response_format="detail",
        description="Detail 1 akun.",
        signal_words=["detail akun", "info akun"],
        query_params=[
            QueryParam(
                name="id", label="Account ID", param_type="string", required=True
            ),
        ],
    ),
    # Stock adjustment detail
    "query_stock_adjustment_detail": QueryActionConfig(
        action_key="query_stock_adjustment_detail",
        display_name="Detail Penyesuaian Stok",
        rest_endpoint="/api/stock-adjustments/{id}",
        response_format="detail",
        description="Detail 1 penyesuaian stok.",
        signal_words=["detail penyesuaian", "detail stock adjustment"],
        query_params=[
            QueryParam(
                name="id", label="Adjustment ID", param_type="string", required=True
            ),
        ],
    ),
    # Bank account balance (journal-derived)
    "query_bank_account_balance": QueryActionConfig(
        action_key="query_bank_account_balance",
        display_name="Saldo Rekening",
        rest_endpoint="/api/bank-accounts/{id}",
        response_format="detail",
        description="Saldo 1 rekening bank/kas (journal-derived). Resolve bank by name first.",
        signal_words=["saldo rekening", "saldo bank", "balance rekening"],
        query_params=[
            QueryParam(
                name="id", label="Account ID", param_type="string", required=True
            ),
        ],
    ),
    # -- Batch 2 query intents -----------------------------------------------
    "query_items_no_stock": QueryActionConfig(
        action_key="query_items_no_stock",
        display_name="Barang Stok Habis",
        rest_endpoint="/api/inventory/low-stock",
        response_format="table",
        description="Daftar barang yang stoknya habis (out of stock).",
        signal_words=[
            "stok habis",
            "barang habis",
            "out of stock",
            "stok kosong",
            "stok nol",
        ],
        query_params=[
            QueryParam(
                name="include_zero_stock",
                label="Include Zero Stock",
                param_type="string",
                default="true",
            ),
        ],
    ),
    "query_customers_list": QueryActionConfig(
        action_key="query_customers_list",
        display_name="Daftar Pelanggan",
        rest_endpoint="/api/customers",
        response_format="list",
        description="Daftar semua pelanggan.",
        signal_words=[
            "daftar pelanggan",
            "list pelanggan",
            "siapa pelanggan",
            "customer list",
        ],
        query_params=[
            QueryParam(
                name="is_active", label="Aktif", param_type="string", default="true"
            ),
        ],
    ),
    "query_vendors_list": QueryActionConfig(
        action_key="query_vendors_list",
        display_name="Daftar Vendor",
        rest_endpoint="/api/vendors",
        response_format="list",
        description="Daftar semua vendor/pemasok.",
        signal_words=["daftar vendor", "list vendor", "daftar pemasok", "vendor list"],
        query_params=[
            QueryParam(
                name="is_active", label="Aktif", param_type="string", default="true"
            ),
        ],
    ),
    "query_customers_with_overdue": QueryActionConfig(
        action_key="query_customers_with_overdue",
        display_name="Pelanggan Jatuh Tempo",
        rest_endpoint="/api/sales-invoices/summary",
        response_format="summary",
        description="Pelanggan dengan faktur jatuh tempo.",
        signal_words=[
            "pelanggan jatuh tempo",
            "customer overdue",
            "pelanggan terlambat",
        ],
        query_params=[],
    ),
    "query_vendors_with_overdue": QueryActionConfig(
        action_key="query_vendors_with_overdue",
        display_name="Vendor Jatuh Tempo",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Vendor dengan tagihan jatuh tempo.",
        signal_words=["vendor jatuh tempo", "vendor overdue", "vendor terlambat"],
        query_params=[],
    ),
    "query_sales_invoices_unpaid": QueryActionConfig(
        action_key="query_sales_invoices_unpaid",
        display_name="Faktur Penjualan Belum Bayar",
        rest_endpoint="/api/sales-invoices",
        response_format="list",
        description="Daftar faktur penjualan yang belum dibayar.",
        signal_words=[
            "faktur belum bayar",
            "invoice unpaid",
            "faktur penjualan belum lunas",
        ],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="unpaid"
            ),
        ],
    ),
    "query_bills_by_vendor": QueryActionConfig(
        action_key="query_bills_by_vendor",
        display_name="Tagihan per Vendor",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Daftar tagihan/faktur pembelian dari vendor tertentu. vendor_id via entity resolution.",
        signal_words=["tagihan vendor", "faktur pembelian vendor", "bills vendor"],
        query_params=[
            QueryParam(
                name="vendor_id", label="Vendor ID", param_type="string", required=True
            ),
        ],
    ),
    "query_bills_unpaid": QueryActionConfig(
        action_key="query_bills_unpaid",
        display_name="Tagihan Belum Bayar",
        rest_endpoint="/api/bills",
        response_format="list",
        description="Daftar tagihan/faktur pembelian yang belum dibayar.",
        signal_words=[
            "tagihan belum bayar",
            "bills unpaid",
            "faktur pembelian belum lunas",
        ],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="unpaid"
            ),
        ],
    ),
    "query_expenses_by_date_range": QueryActionConfig(
        action_key="query_expenses_by_date_range",
        display_name="Pengeluaran per Periode",
        rest_endpoint="/api/expenses",
        response_format="list",
        description="Daftar pengeluaran dalam rentang tanggal tertentu.",
        signal_words=["pengeluaran bulan", "biaya periode", "expense tanggal"],
        query_params=[
            QueryParam(name="start_date", label="Tanggal Mulai", param_type="date"),
            QueryParam(name="end_date", label="Tanggal Akhir", param_type="date"),
        ],
    ),
    "query_account_ledger": QueryActionConfig(
        action_key="query_account_ledger",
        display_name="Buku Besar Akun",
        rest_endpoint="/api/accounts/{account_id}/journal-entries",
        response_format="list",
        description="Mutasi/buku besar untuk akun tertentu. account_id via entity resolution.",
        signal_words=["buku besar", "general ledger", "mutasi akun", "ledger akun"],
        query_params=[
            QueryParam(
                name="account_id",
                label="Account ID",
                param_type="string",
                required=True,
            ),
        ],
    ),
    "query_dashboard_summary": QueryActionConfig(
        action_key="query_dashboard_summary",
        display_name="Ringkasan Dashboard",
        rest_endpoint="/api/dashboard/all",
        response_format="summary",
        description="Ringkasan bisnis/keuangan dari dashboard.",
        signal_words=[
            "ringkasan bisnis",
            "summary keuangan",
            "dashboard summary",
            "rangkuman usaha",
        ],
        query_params=[],
    ),
    "query_overdue_all": QueryActionConfig(
        action_key="query_overdue_all",
        display_name="Semua Jatuh Tempo",
        rest_endpoint="/api/dashboard/all",
        response_format="summary",
        description="Semua item jatuh tempo (faktur + tagihan).",
        signal_words=["semua jatuh tempo", "semua overdue", "apa saja jatuh tempo"],
        query_params=[],
    ),
    "query_recurring_bills_list": QueryActionConfig(
        action_key="query_recurring_bills_list",
        display_name="Tagihan Berulang",
        rest_endpoint="/api/recurring-bills",
        response_format="list",
        description="Daftar tagihan berulang/recurring.",
        signal_words=["tagihan berulang", "recurring bills", "tagihan rutin"],
        query_params=[],
    ),
    "query_bank_transactions_by_date": QueryActionConfig(
        action_key="query_bank_transactions_by_date",
        display_name="Transaksi Bank per Tanggal",
        rest_endpoint="/api/bank-accounts/{id}/transactions",
        response_format="list",
        description="Daftar transaksi bank/kas dalam rentang tanggal. bank_account + date via entity resolution.",
        signal_words=[
            "transaksi bank tanggal",
            "mutasi bank periode",
            "transaksi rekening tanggal",
        ],
        query_params=[
            QueryParam(
                name="id", label="Bank Account ID", param_type="string", required=True
            ),
            QueryParam(name="start_date", label="Tanggal Mulai", param_type="date"),
            QueryParam(name="end_date", label="Tanggal Akhir", param_type="date"),
        ],
    ),
    # ═══════════════ BATCH 3: Credit Notes ═══════════════
    "query_credit_notes_list": QueryActionConfig(
        action_key="query_credit_notes_list",
        display_name="Daftar Nota Kredit",
        rest_endpoint="/api/credit-notes",
        response_format="list",
        description="Daftar semua nota kredit penjualan.",
        signal_words=["daftar nota kredit", "list credit note", "nota kredit"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    "query_credit_note_detail": QueryActionConfig(
        action_key="query_credit_note_detail",
        display_name="Detail Nota Kredit",
        rest_endpoint="/api/credit-notes/{id}",
        response_format="detail",
        description="Detail 1 nota kredit.",
        signal_words=["detail nota kredit", "info nota kredit"],
        query_params=[
            QueryParam(
                name="id", label="Credit Note ID", param_type="string", required=True
            ),
        ],
    ),
    "query_credit_notes_summary": QueryActionConfig(
        action_key="query_credit_notes_summary",
        display_name="Ringkasan Nota Kredit",
        rest_endpoint="/api/credit-notes/summary",
        response_format="summary",
        description="Ringkasan nota kredit: total, applied, unapplied.",
        signal_words=["ringkasan nota kredit", "total nota kredit"],
        query_params=[],
    ),
    # ═══════════════ BATCH 3: Vendor Credits ═══════════════
    "query_vendor_credits_list": QueryActionConfig(
        action_key="query_vendor_credits_list",
        display_name="Daftar Vendor Credit",
        rest_endpoint="/api/vendor-credits",
        response_format="list",
        description="Daftar semua vendor credit.",
        signal_words=["daftar vendor credit", "list vendor credit"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    "query_vendor_credit_detail": QueryActionConfig(
        action_key="query_vendor_credit_detail",
        display_name="Detail Vendor Credit",
        rest_endpoint="/api/vendor-credits/{id}",
        response_format="detail",
        description="Detail 1 vendor credit.",
        signal_words=["detail vendor credit"],
        query_params=[
            QueryParam(
                name="id", label="Vendor Credit ID", param_type="string", required=True
            ),
        ],
    ),
    "query_vendor_credits_summary": QueryActionConfig(
        action_key="query_vendor_credits_summary",
        display_name="Ringkasan Vendor Credit",
        rest_endpoint="/api/vendor-credits/summary",
        response_format="summary",
        description="Ringkasan vendor credit: total, applied, unapplied.",
        signal_words=["ringkasan vendor credit", "total vendor credit"],
        query_params=[],
    ),
    # ═══════════════ BATCH 3: Quotes ═══════════════
    "query_quotes_list": QueryActionConfig(
        action_key="query_quotes_list",
        display_name="Daftar Penawaran",
        rest_endpoint="/api/quotes",
        response_format="list",
        description="Daftar semua penawaran/quotation.",
        signal_words=["daftar penawaran", "list quote", "quotation", "penawaran"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    "query_quote_detail": QueryActionConfig(
        action_key="query_quote_detail",
        display_name="Detail Penawaran",
        rest_endpoint="/api/quotes/{id}",
        response_format="detail",
        description="Detail 1 penawaran.",
        signal_words=["detail penawaran", "info quote"],
        query_params=[
            QueryParam(name="id", label="Quote ID", param_type="string", required=True),
        ],
    ),
    "query_quotes_summary": QueryActionConfig(
        action_key="query_quotes_summary",
        display_name="Ringkasan Penawaran",
        rest_endpoint="/api/quotes/summary",
        response_format="summary",
        description="Ringkasan penawaran: total, draft, sent, accepted, rejected.",
        signal_words=["ringkasan penawaran", "total penawaran"],
        query_params=[],
    ),
    # ═══════════════ BATCH 3: Bank Transfers ═══════════════
    "query_bank_transfers_list": QueryActionConfig(
        action_key="query_bank_transfers_list",
        display_name="Daftar Transfer Bank",
        rest_endpoint="/api/bank-transfers",
        response_format="list",
        description="Daftar semua transfer antar bank.",
        signal_words=["daftar transfer bank", "list transfer", "transfer antar bank"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # ═══════════════ BATCH 3: Customer Deposits ═══════════════
    "query_customer_deposits_list": QueryActionConfig(
        action_key="query_customer_deposits_list",
        display_name="Daftar Deposit Pelanggan",
        rest_endpoint="/api/customer-deposits",
        response_format="list",
        description="Daftar semua deposit/uang muka pelanggan.",
        signal_words=[
            "daftar deposit pelanggan",
            "uang muka pelanggan",
            "customer deposit",
        ],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
    ),
    # ═══════════════ BATCH 3: Vendor Deposits ═══════════════
    "query_vendor_deposits_list": QueryActionConfig(
        action_key="query_vendor_deposits_list",
        display_name="Daftar Deposit Vendor",
        rest_endpoint="/api/vendor-deposits",
        response_format="list",
        description="Daftar semua deposit/uang muka vendor.",
        signal_words=["daftar deposit vendor", "uang muka vendor", "vendor deposit"],
        query_params=[
            QueryParam(name="limit", label="Limit", param_type="number", default="20"),
        ],
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

    # At-least-one group validation
    if hasattr(config, "at_least_one_groups"):
        for group in config.at_least_one_groups:
            field_names = group["fields"]
            group_label = group["label"]
            has_any = any(payload.get(fn) for fn in field_names)
            if not has_any:
                missing.append(group_label)

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
                result[f.name] = (
                    int(f.default) if f.default.isdigit() else float(f.default)
                )
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
    if (
        action_key == "create_account"
        and "normal_balance" not in result
        and "type" in result
    ):
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


# Reverse mapping: API value -> Indonesian display label
_API_TO_LABEL_MAP = {
    "goods": "persediaan",
    "service": "jasa",
    "non_inventory": "non-persediaan",
    "cash": "tunai",
    "bank_transfer": "transfer bank",
}


def _build_api_to_label(options: list) -> dict:
    """Build reverse mapping from API values to display labels using FieldSpec options + known mappings."""
    result = dict(_API_TO_LABEL_MAP)
    # Also map option values to themselves (identity) so Indonesian options pass through
    for opt in options:
        result[opt.lower()] = opt
    return result


def build_confirmation_table(
    action_key: str, payload: dict, journal_preview: list | None = None
) -> str:
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
            # Reverse-map API values to Indonesian display labels
            if f.options and isinstance(value, str):
                _api_to_label = _build_api_to_label(f.options)
                display_value = _api_to_label.get(str(value).lower(), display_value)
            lines.append(f"| {f.label} | {display_value} |")

    # Trust context: category (from config, not hardcoded)
    cat_label = config.get_category_label(payload)
    if cat_label:
        lines.append(f"| Kategori | {cat_label} |")

    # Trust context: impact notes (from config rules)
    impact_notes = config.get_impact_notes(payload)
    for note in impact_notes:
        lines.append(f"\n{note}")

    # Journal preview section
    if journal_preview and isinstance(journal_preview, list):
        lines.append("")
        lines.append("📒 **Dampak Jurnal:**")
        for jl in journal_preview:
            account = jl.get("account_name", "?")
            debit = float(jl.get("debit", 0) or 0)
            credit = float(jl.get("credit", 0) or 0)
            if debit > 0:
                lines.append(f"  Dr. {account}  Rp {int(debit):,}".replace(",", "."))
            if credit > 0:
                lines.append(f"  Cr. {account}  Rp {int(credit):,}".replace(",", "."))

    return "\n".join(lines)


def build_review_card_payload(
    action_key: str, payload: dict, journal_preview: list | None = None
) -> dict | None:
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
        # Reverse-map API values to Indonesian display labels
        if f.options and isinstance(value, str):
            _api_to_label = _build_api_to_label(f.options)
            display_value = _api_to_label.get(str(value).lower(), display_value)
        _is_editable = (
            f.editable
            and not f.display_only
            and not f.name.endswith("_id")
            and f.field_type not in ("enum", "boolean")
        )
        header.append(
            {
                "label": f.label,
                "value": display_value,
                "field_type": f.field_type,
                "key": f.name,
                "editable": _is_editable,
            }
        )

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
            journal_lines.append(
                {
                    "dir": "Dr" if dr > 0 else "Cr",
                    "account": jl.get("account_name", jl.get("account", "")),
                    "amount": dr if dr > 0 else cr,
                }
            )
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
            items.append(
                {
                    "name": item.get(
                        "description",
                        item.get("product_name", item.get("name", "Item")),
                    ),
                    "qty": qty,
                    "unit": item.get("unit", "Pcs"),
                    "price": price,
                    "subtotal": item_subtotal,
                }
            )
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
        "entity_type": config.entity_type,  # for data-changed events
        "action_type_key": config.action_type_key,  # uppercase key
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
