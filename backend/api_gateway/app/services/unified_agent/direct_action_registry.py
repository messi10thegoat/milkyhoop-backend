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
            FieldSpec(name="email", label="Email", required=False),
            FieldSpec(name="address", label="Alamat", required=False),
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
                message_template="Dr. Hutang Usaha Rp {formatted_value} / Cr. Bank Rp {formatted_value}",
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
            FieldSpec(name="bill_amount", label="Total Faktur", field_type="number", display_only=True),
            FieldSpec(name="amount_due", label="Sisa Tagihan", field_type="number", display_only=True),
            FieldSpec(name="bank_account_name", label="Rekening Pembayaran", display_only=True),
            FieldSpec(name="statement_description", label="Mutasi Bank", display_only=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="total_amount", label="Jumlah Bayar", field_type="number", required=True),
            FieldSpec(name="payment_date", label="Tanggal", field_type="date", required=True),
            FieldSpec(name="payment_method", label="Metode", default="bank_transfer"),
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
                message_template="Dr. Bank Rp {formatted_value} / Cr. Piutang Usaha Rp {formatted_value}",
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
            FieldSpec(name="bank_account_name",  label="Rekening",            display_only=True),
            FieldSpec(name="statement_description", label="Mutasi Bank",      display_only=True),
            # Regular (shown + sent to backend)
            FieldSpec(name="total_amount",       label="Jumlah Terima",       field_type="number", required=True),
            FieldSpec(name="payment_date",       label="Tanggal",             field_type="date",   required=True),
            FieldSpec(name="payment_method",     label="Metode",              default="bank_transfer"),
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
                message_template="\u2728 Akan membuat jurnal: Debit akun tujuan, Credit rekening bank.",
            ),
        ],
        fields=[
            # Hidden (backend needs, user does not see)
            FieldSpec(name="session_id", label="Session ID", required=True, hidden=True),
            FieldSpec(name="statement_line_id", label="Line ID", required=True, hidden=True),
            FieldSpec(name="account_id", label="Akun ID", required=True, hidden=True),
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
    """Collect all signal_words from registry — for auto-wiring intent_bias."""
    words = []
    for config in DIRECT_ACTIONS.values():
        words.extend(config.signal_words)
    return words


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
