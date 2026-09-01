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
import logging

logger = logging.getLogger(__name__)
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
    # T179-Q3 FASE 0 (2026-08-30): skema PER-BARIS untuk field berbentuk
    # daftar. Kalau None (DEFAULT untuk SEMUA field yang ada), build_intent_schema
    # berperilaku persis seperti sebelum patch -> responseSchema aksi lain
    # byte-identik. Kalau diisi, field itu dideklarasikan sebagai
    # {"type":"array","items":{"type":"object",...}} — bentuk yang TERBUKTI
    # diterima Gemini responseSchema (probe live 2026-08-30, HTTP 200) dan
    # TERBUKTI lolos GeminiClient._clean_schema tanpa diruntuhkan.
    item_schema: Optional[dict] = None


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
        # FIX_AQUA_PERCENT_DISPLAY 2026-05-13: expose {formatted_percent} for percent templates
        try:
            _pct_num = float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            _pct_num = 0.0
        formatted_percent = f"{_pct_num:g}"
        return self.message_template.format(
            value=value,
            formatted_value=formatted_value,
            formatted_percent=formatted_percent,
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


# Puing yang tertinggal ketika sebuah placeholder mengosong. Diterapkan
# BERURUTAN — kutip kosong dulu, baru spasi, baru tanda baca — karena
# menghapus "''" itu sendiri melahirkan spasi ganda.
_PUING: tuple[tuple[str, str], ...] = (
    (r"(?<!\w)([\'\"])\1(?!\w)", ""),   # '' atau "" (kutip yang isinya lenyap)
    (r"[ \t]{2,}", " "),                 # spasi ganda
    (r"[ \t]+([.,;:!?])", r"\1"),        # spasi sebelum tanda baca
    (r"\(\s*\)", ""),                     # tanda kurung yang jadi kosong
    (r"[ \t]{2,}", " "),                 # sapuan kedua: aturan di atas bisa
)                                       # menyisakan spasi ganda baru


def _rapikan(teks: str) -> str:
    """Buang puing yang ditinggalkan placeholder kosong.

    Kalimat yang terlihat rusak ("Faktur '' berhasil dibatalkan.") merusak
    kepercayaan lebih dalam daripada kalimat yang sekadar kurang lengkap
    ("Faktur berhasil dibatalkan."): yang pertama membuat user bertanya apa
    lagi yang rusak di balik layar, tepat setelah menyentuh pembukuannya.
    """
    for pola, ganti in _PUING:
        teks = _re.sub(pola, ganti, teks)
    return teks.strip()


class _Kosong(dict):
    """Kunci yang tak ada -> string kosong, BUKAN KeyError.

    str.format_map memanggil __missing__ hanya untuk kunci yang hilang, jadi
    placeholder yang TERSEDIA tetap terisi. Itu inti T74.
    """

    def __missing__(self, key: str) -> str:  # noqa: D105
        return ""


def _safe_format(template: str, payload: dict, **extra) -> str:
    """Isi placeholder yang ADA; yang hilang dikosongkan lalu puingnya dirapikan.

    ⚠️ Perilaku LAMA gagal semua-atau-tak-satu-pun: satu KeyError membuang
    SELURUH placeholder, termasuk yang nilainya tersedia. Itu sebabnya
    create_sales_order tampil di layar sebagai

        "Pesanan  untuk '' tersimpan sebagai draft."

    padahal customer_name pada payload yang sama terisi "Toko Melati" —
    satu-satunya yang benar-benar hilang adalah order_number, yang memang
    belum lahir saat kartu pratinjau dibuat.

    Penting: puing TIDAK hanya muncul saat kunci hilang. `entity_name` selalu
    ADA (get_entity_name mengembalikan "" bila field-nya kosong), jadi
    "Faktur '{entity_name}'" sudah bisa mencetak "Faktur ''" tanpa satu pun
    KeyError. 49 dari 120 template terbukti mengeluarkan puing pada keadaan
    terdegradasi. Karena itu _rapikan() dipanggil SELALU, bukan hanya di
    jalur galat.
    """
    fmt_kwargs = _Kosong(
        {k: v for k, v in payload.items() if isinstance(v, (str, int, float))}
    )
    fmt_kwargs.update(extra)
    try:
        hasil = template.format_map(fmt_kwargs)
    except (IndexError, ValueError):
        # Template cacat (kurung tak berpasangan / spesifikasi format salah).
        # Jangan pernah melempar dari jalur pesan: kembalikan bentuk polos.
        hasil = _re.sub(r"\{[^}]*\}", "", template)
    return _rapikan(hasil)


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
    # dok. 81 (3) — label tombol konfirmasi. Kosong = pakai display_name,
    # yang sudah menyebut apa yang aksi ini lakukan.
    label_tombol: str = ""
    # T74 — kalimat pengganti untuk saat sebuah placeholder TAK BERNILAI.
    #
    # Merapikan puing sudah cukup untuk 213 dari 214 render (seluruh kombinasi
    # hadir/hilang atas 120 template): "Faktur '{entity_name}'" menjadi
    # "Faktur", dan itu kalimat Indonesia yang sah. Yang TIDAK bisa
    # diselamatkan hanyalah kalimat yang placeholder-nya diikat kata fungsi —
    # "dikategorisasi sebagai {account_name}" runtuh jadi "dikategorisasi
    # sebagai." apa pun yang dirapikan, karena "sebagai" menuntut objek.
    #
    # Sengaja BUKAN perbaikan otomatis berbasis daftar kata fungsi: aturan
    # semacam itu akan menyunting kalimat yang memuat DATA USER (nama
    # pelanggan yang kebetulan berakhir "…dari"), dan menyunting data user
    # diam-diam lebih buruk daripada satu kalimat yang kurang lengkap.
    # Kosong = pakai jalur rapikan biasa.
    success_message_kosong: str = ""

    def get_entity_name(self, payload: dict) -> str:
        """Extract entity display name from payload."""
        return str(payload.get(self.entity_name_field, "")).strip()

    def get_loading_message(self, payload: dict) -> str:
        """Build loading message for confirming state."""
        name = self.get_entity_name(payload)
        return _safe_format(self.loading_message_template, payload, entity_name=name)

    def get_success_message(self, payload: dict, hasil: dict | None = None) -> str:
        """Build success message after action completes.

        `hasil` = badan respons endpoint (mis. {"data": {"quote_number": ...}}).
        Nomor dokumen LAHIR DI ENDPOINT, jadi ia tidak pernah ada di payload —
        tanpa argumen ini sebuah template ber-{quote_number} akan dirender
        dengan lubang kosong oleh _safe_format (KeyError -> placeholder
        dibuang), dan pesan yang seharusnya bisa ditindaklanjuti berubah jadi
        kalimat menggantung. Opsional supaya pemanggil pra-eksekusi
        (build_ux_metadata) tak berubah perilakunya.
        """
        name = self.get_entity_name(payload)
        _extra = {"entity_name": name}
        if isinstance(hasil, dict):
            for _sumber in (hasil, hasil.get("data")):
                if not isinstance(_sumber, dict):
                    continue
                for k, v in _sumber.items():
                    # RESPONS MENANG ATAS PAYLOAD. Pesan sukses menjelaskan apa
                    # yang TERJADI, dan payload hanyalah apa yang DIMINTA —
                    # payload bisa keliru, respons tidak.
                    #
                    # Ini bukan pilihan gaya. Percobaan pertama memakai aturan
                    # sebaliknya (payload menang) dan gate-nya MERAH dengan cara
                    # yang tak terduga: payload create_quote membawa
                    # quote_number berisi SELURUH KALIMAT USER (kelas T44 —
                    # _amankan_nomor_dokumen hanya menjaga create_bill /
                    # create_purchase_invoice), sehingga kalimatnya berbunyi
                    # "Penawaran untuk toko melati, 10 pcs kaos hitam 24s
                    # tersimpan sebagai draft". Endpoint mengabaikan nomor palsu
                    # itu dan menghasilkan QUO-2608-0004 yang benar; hanya
                    # responsnya yang tahu.
                    #
                    # Dua template lain memakai placeholder non-entity_name:
                    #   categorize_statement -> account_name : responsnya
                    #     mengembalikan nama akun dari DB (category_account),
                    #     jadi respons-menang justru LEBIH benar daripada nama
                    #     hasil resolusi di payload.
                    #   confirm_recon_batch -> action_count : endpointnya
                    #     (/sessions/{id}/confirm-batch) TIDAK ADA di router
                    #     mana pun, jadi aksi itu tak pernah tereksekusi.
                    # Keduanya diperiksa sebelum urutan ini dibalik.
                    if k == "entity_name":
                        continue
                    if isinstance(v, (str, int, float)) and v != "":
                        _extra[k] = v
        # T74: bila sebuah placeholder yang dituntut template tak bernilai dan
        # aksi ini menyediakan kalimat pengganti, pakai kalimat itu — merapikan
        # puing tak menyelamatkan kalimat yang placeholder-nya diikat kata
        # fungsi. Diperiksa SEBELUM format supaya keputusannya soal DATA,
        # bukan soal bentuk teks yang sudah terlanjur rusak.
        if self.success_message_kosong:
            _tersedia = {
                k: v
                for k, v in {**payload, **_extra}.items()
                if isinstance(v, (str, int, float)) and str(v).strip() != ""
            }
            _diminta = set(_re.findall(r"\{(\w+)\}", self.success_message_template))
            if _diminta - set(_tersedia):
                return _safe_format(self.success_message_kosong, payload, **_extra)
        return _safe_format(self.success_message_template, payload, **_extra)

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
            # FIX_TRANSFER_ADMIN_FEE: carry transfer admin fee to the POST so the
            # bill-payment journal books Dr Biaya Admin Bank / Cr Bank = nominal + fee.
            FieldSpec(
                name="bank_fee_amount",
                label="Biaya Admin Bank",
                field_type="number",
            ),
            FieldSpec(
                name="bank_fee_account_id", label="Bank Fee Account ID", hidden=True
            ),
            FieldSpec(
                name="bank_fee_account_name",
                label="Akun Biaya Admin",
                display_only=True,
            ),
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
                message_template="PPN {formatted_percent}% akan dibukukan ke PPN Keluaran.",
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
                # T182-A: sebelum ini `items` dideklarasikan ke model sebagai
                # ["string","null"] (field_type="json" jatuh ke cabang else di
                # build_intent_schema karena cabang array HANYA aktif bila
                # item_schema TERISI, dan satu-satunya yang mengisinya adalah
                # create_bill sejak T179-Q3). Model lalu berhak mengisi `items`
                # dengan PROSA, json.loads gagal diam-diam -> items=[] ->
                # scalar-fallback di _enrich_sales_invoice mengarang satu baris
                # {description:"Item", quantity:1, unit_price:0}.
                #
                # Nama kunci per-baris SENGAJA BEDA dari create_bill
                # (product_name/qty/price). Untuk faktur PENJUALAN jalur hilir
                # memakai description/quantity/unit_price -- diverifikasi dari
                # tiga sumber: FieldSpec.description di bawah, scalar-fallback
                # + _enrich_items di tool_executor.py, dan 272 baris nyata
                # pending_actions.action_plan->items (kunci terukur:
                # quantity 305, unit_price 305, description 305, item_id 296,
                # unit 259, discount_percent 2).
                #
                # item_id sengaja TIDAK dideklarasikan (mengikuti create_bill
                # yang juga tak mendeklarasikan product_id): UUID tidak bisa
                # dikarang model, ia diresolusi _enrich_items lewat description.
                item_schema={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "Nama barang/jasa. WAJIB nama barang, "
                                "BUKAN nama pelanggan."
                            ),
                        },
                        "quantity": {"type": "number", "description": "Kuantitas"},
                        "unit_price": {
                            "type": "number",
                            "description": "Harga satuan JUAL dalam Rupiah",
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Satuan bila user sebut",
                        },
                        "discount_percent": {
                            "type": ["number", "null"],
                            "description": (
                                "Diskon per-baris persen bila user EKSPLISIT sebut"
                            ),
                        },
                    },
                    "required": ["description", "quantity", "unit_price"],
                },
                description=(
                    "Array of items. Each item dapat berisi: "
                    "item_id, description (nama barang/jasa), quantity, unit, "
                    "unit_price, discount_percent (per-line), discount_amount "
                    "(per-line), tax_rate (per-line override), tax_code, "
                    "batch_no, exp_date (YYYY-MM-DD). "
                    "Per-line fields hanya diisi kalau user EKSPLISIT sebut "
                    "untuk item itu (mis. 'item A diskon 10%, item B diskon 20%')."
                ),
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
            FieldSpec(
                name="discount_amount",
                label="Diskon (Rp)",
                field_type="number",
                default="0",
                description="Diskon invoice-level dalam Rp (alternatif ke discount_percent). Kalau user bilang 'diskon Rp 50.000' atau 'potongan 50000' set di sini.",
            ),
            FieldSpec(
                name="ref_no",
                label="No. Referensi",
                description="Nomor referensi eksternal (PO customer, e.g. 'PO-12345'). Kalau user sebut 'PO X' atau 'ref X' set di sini.",
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
        # dok. 81 (4c): gejala dan bentuknya IDENTIK dengan penawaran —
        # routers/sales_orders.py juga menulis 'draft' tanpa syarat.
        success_message_template=(
            "Pesanan {order_number} untuk '{entity_name}' tersimpan sebagai "
            "draft. Konfirmasi lewat Penjualan \u2192 Pesanan Penjualan."
        ),
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="Pajak {formatted_percent}% akan diterapkan per item.",
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
                # T182-C: sebelum ini `items` dideklarasikan ke model sebagai
                # ["string","null"] (field_type="json" jatuh ke cabang else di
                # build_intent_schema; cabang array HANYA aktif bila
                # item_schema TERISI). Model lalu berhak mengisi `items` dengan
                # PROSA, json.loads gagal diam-diam -> items=[] -> scalar-
                # fallback mengarang satu baris {description:"Item",
                # quantity:1, unit_price:0}. Kelas bug yang sama sudah
                # diperbaiki untuk create_bill (T179-Q3) dan
                # create_sales_invoice (T182-A), keduanya hijau di produksi.
                #
                # Nama kunci per-baris diverifikasi dari DUA sumber, bukan
                # disalin dari tiket: (a) docstring + jalur hilir
                # _enrich_sales_order di tool_executor.py, (b) sensus
                # pending_actions.action_plan->items untuk CREATE_SALES_ORDER
                # di produksi (SELECT saja): item_id 255, description 255,
                # quantity 255, unit 255, unit_price 172.
                #
                # discount_percent per-baris SENGAJA TIDAK dideklarasikan:
                # nol kemunculan di sensus itu (beda dari create_sales_invoice
                # yang punya 2). Diskon SO hidup di tingkat header.
                #
                # item_id sengaja TIDAK dideklarasikan (mengikuti create_bill
                # dan create_sales_invoice): UUID tidak bisa dikarang model,
                # ia diresolusi _enrich_items lewat description.
                item_schema={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "Nama barang/jasa. WAJIB nama barang, "
                                "BUKAN nama pelanggan."
                            ),
                        },
                        "quantity": {"type": "number", "description": "Kuantitas"},
                        "unit_price": {
                            "type": "number",
                            "description": "Harga satuan JUAL dalam Rupiah",
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Satuan bila user sebut",
                        },
                    },
                    "required": ["description", "quantity", "unit_price"],
                },
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
        # T8 2026-08-11: endpointnya kini ADA (routers/bills.py). Sebelum ini
        # nilainya "" sehingga faktur pembelian adalah satu-satunya dokumen
        # berbaris yang dampak jurnalnya tak pernah sampai ke kartu.
        journal_preview_endpoint="/api/bills/preview-journal",
        entity_name_field="vendor_name",
        loading_message_template="Membuat faktur pembelian dari {entity_name}\u2026",
        success_message_template="Faktur pembelian dari '{entity_name}' berhasil dibuat.",
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="PPN {formatted_percent}% akan dibukukan ke PPN Masukan.",
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
            FieldSpec(
                name="invoice_number",
                label="No. Faktur",
                description=(
                    "Nomor dokumen INTERNAL, dibuat otomatis oleh sistem "
                    "(format PB-YYMM-0001). JANGAN diisi dari kalimat user. "
                    "Nomor faktur milik VENDOR bukan field ini — pakai ref_no."
                ),
            ),
            # H 2026-08-11: sampai commit ini nomor faktur VENDOR tidak punya
            # tempat yang sah, jadi model menaruhnya di invoice_number — dan
            # bills_service:2553 melewati generator PB- begitu field itu terisi,
            # sehingga NOMOR DOKUMEN berubah jadi kalimat user. Memberi tempat
            # yang benar lebih murah daripada terus menangkapnya di pagar.
            FieldSpec(
                name="ref_no",
                label="No. Faktur Vendor",
                description=(
                    "Nomor faktur yang tertera pada dokumen VENDOR "
                    "(mis. INV/BE/2026/0812). Kosongkan bila user tidak "
                    "menyebutkan nomor."
                ),
            ),
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=True,
                hidden=True,
                # T179-Q3 FASE 0: sebelum ini `items` dideklarasikan ke Gemini
                # sebagai ["string","null"] (field_type="json" jatuh ke cabang
                # else di build_intent_schema), jadi model MENGARANG teks yang
                # gagal di-json.loads -> produksi mengukur n_items=-3 tipe=str
                # 4 dari 4. Deklarasi array ini memberi model bentuk yang benar.
                # Nama kunci per-baris = product_name/qty/price, konsisten dengan
                # deskripsi di bawah dan skema bills/v2; jalur hilir
                # _enrich_purchase_invoice tetap menerima alias lama, jadi ia
                # tetap jadi jaring bila model sesekali kembali mengirim string.
                item_schema={
                    "type": "object",
                    "properties": {
                        "product_name": {
                            "type": "string",
                            "description": (
                                "Nama barang/jasa. WAJIB nama barang, "
                                "BUKAN nama vendor."
                            ),
                        },
                        "qty": {"type": "number", "description": "Kuantitas"},
                        "price": {
                            "type": "number",
                            "description": "Harga satuan BELI dalam Rupiah",
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Satuan bila user sebut",
                        },
                        "discount_percent": {
                            "type": ["number", "null"],
                            "description": (
                                "Diskon per-baris persen bila user EKSPLISIT sebut"
                            ),
                        },
                    },
                    "required": ["product_name", "qty", "price"],
                },
                description=(
                    "Array of items. Each item dapat berisi: "
                    "product_id, product_name (nama barang), qty, unit, price, "
                    "discount_percent (per-line), tax_rate (per-line override), "
                    "tax_code_id, batch_no, exp_date (YYYY-MM format), "
                    "bonus_qty (qty bonus/free, tidak masuk total). "
                    "Per-line fields hanya diisi kalau user EKSPLISIT sebut."
                ),
            ),
            FieldSpec(
                name="tax_rate", label="Pajak (%)", field_type="percent", default="0"
            ),
            FieldSpec(
                name="invoice_discount_percent",
                label="Diskon Faktur (%)",
                field_type="percent",
                default="0",
                description="Diskon invoice-level (%). Bills V2 punya field invoice_discount.",
            ),
            FieldSpec(
                name="invoice_discount_amount",
                label="Diskon Faktur (Rp)",
                field_type="number",
                default="0",
                description="Diskon invoice-level dalam Rp (alternatif ke percent).",
            ),
            FieldSpec(
                name="cash_discount_percent",
                label="Diskon Tunai (%)",
                field_type="percent",
                default="0",
                description="Diskon tunai (%) untuk pembayaran cepat.",
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
        label_tombol="Posting",
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
            # FIX_POST_DRAFT 2026-06-20
            "terbitkan",
            "menerbitkan",
            "terbitkan faktur pembelian",
            "terbitkan tagihan",
            "sahkan faktur pembelian",
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
    # FIX_POST_DRAFT 2026-06-20 — post an EXISTING DRAFT sales invoice. Mirrors
    # post_bill: hits the proven /post endpoint (draft -> posted, AR + journal),
    # never creates a new doc. Payload field `invoice_id` is mapped to `id` by
    # entity_resolver._build_payload (post_sales_invoice branch).
    "post_sales_invoice": DirectActionConfig(
        label_tombol="Posting",
        action_key="post_sales_invoice",
        display_name="Posting Faktur Penjualan",
        rest_endpoint="/api/sales-invoices/{id}/post",
        rest_method="POST",
        entity_type="sales_invoice",
        risk_level="medium",
        creates_journal=True,
        ttl_seconds=120,
        action_type_key="POST_SALES_INVOICE",
        signal_words=[
            "posting invoice",
            "posting faktur penjualan",
            "post invoice",
            "approve invoice",
            "terbitkan faktur penjualan",
            "menerbitkan faktur penjualan",
            "sahkan faktur penjualan",
        ],
        entity_name_field="invoice_number",
        loading_message_template="Memposting faktur {entity_name}…",
        success_message_template="Faktur '{entity_name}' berhasil diposting ke ledger.",
        impact_rules=[
            ImpactRule(
                field="grand_total",
                condition="always",
                message_template="Jurnal: Dr Piutang Usaha + Cr Pendapatan sebesar {formatted_value}",
            ),
        ],
        fields=[
            FieldSpec(name="id", label="ID", required=True, hidden=True),
            FieldSpec(name="invoice_number", label="No. Faktur", display_only=True),
            FieldSpec(name="customer_name", label="Pelanggan", display_only=True),
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
        # T74: "…dikategorisasi sebagai." bukan kalimat. Ini satu-satunya dari
        # 214 render yang tak bisa diselamatkan dengan merapikan puing.
        success_message_kosong="Statement berhasil dikategorisasi.",
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
            # T144 FASE 2 — KONTROL NEGATIF PROSA, LANGKAH 1.
            # Deskripsi ini SENGAJA tidak memuat frasa "Array of ...".
            FieldSpec(
                name="items",
                label="Item",
                field_type="json",
                required=False,
                description=(
                    "Barang bila user menyebut LEBIH DARI SATU barang dalam "
                    "satu pesan. Tiap barang dapat berisi: nama_produk, "
                    "item_type, base_unit, sales_price, purchase_price, sku, "
                    "kategori, description. Kosongkan bila user hanya "
                    "menyebut satu barang."
                ),
            ),
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
        label_tombol="Simpan Penawaran",
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
        # dok. 81 (4): penawaran SELALU lahir draft — routers/quotes.py
        # menulis status 'draft' tanpa syarat, dan doc_status dari FE diabaikan
        # untuk aksi ini (T67). Kalimat lama, "Penawaran berhasil dibuat.",
        # tidak berbohong tapi juga tidak memberi tahu DUA hal yang dibutuhkan
        # owner: bahwa dokumennya belum terkirim, dan apa langkah berikutnya.
        # Digabung dengan tombol berlabel "Posting", itu yang membuat owner
        # mengira gagal lalu memeriksa ulang di dashboard — dan chatmode
        # kehilangan satu-satunya keunggulannya, yaitu tak perlu diperiksa.
        success_message_template=(
            "Penawaran {quote_number} tersimpan sebagai draft. "
            "Kirim ke pelanggan lewat Penjualan \u2192 Penawaran."
        ),
        impact_rules=[
            ImpactRule(
                field="tax_rate",
                condition="nonzero",
                message_template="Pajak {formatted_percent}% akan diterapkan per item.",
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
                # T182-C: sebelum ini `items` dideklarasikan ke model sebagai
                # ["string","null"] (field_type="json" jatuh ke cabang else di
                # build_intent_schema; cabang array HANYA aktif bila
                # item_schema TERISI). Model lalu berhak mengisi `items` dengan
                # PROSA, json.loads gagal diam-diam -> items=[] -> scalar-
                # fallback mengarang satu baris palsu. Kelas bug yang sama
                # sudah ditutup untuk create_bill (T179-Q3),
                # create_sales_invoice (T182-A), dan create_sales_order
                # (commit sebelumnya di tiket ini).
                #
                # Nama kunci per-baris diverifikasi dari DUA sumber, bukan
                # disalin dari tiket: (a) docstring + jalur hilir _enrich_quote
                # di tool_executor.py (items[] butuh description + unit_price),
                # (b) sensus pending_actions.action_plan->items untuk
                # CREATE_QUOTE di produksi (SELECT saja): description 1383,
                # quantity 1285, unit 1284, item_id 1179, unit_price 935.
                #
                # discount_percent per-baris SENGAJA TIDAK dideklarasikan: nol
                # kemunculan di sensus itu, dan diskon penawaran hidup di
                # tingkat header (discount_value + discount_type).
                #
                # item_id sengaja TIDAK dideklarasikan (mengikuti create_bill,
                # create_sales_invoice, create_sales_order): UUID tidak bisa
                # dikarang model, ia diresolusi _enrich_items lewat description.
                item_schema={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": (
                                "Nama barang/jasa. WAJIB nama barang, "
                                "BUKAN nama pelanggan."
                            ),
                        },
                        "quantity": {"type": "number", "description": "Kuantitas"},
                        "unit_price": {
                            "type": "number",
                            "description": "Harga satuan JUAL dalam Rupiah",
                        },
                        "unit": {
                            "type": ["string", "null"],
                            "description": "Satuan bila user sebut",
                        },
                    },
                    "required": ["description", "quantity", "unit_price"],
                },
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
            # T197: DP (uang muka) penawaran. Sebelum ini create_quote punya
            # NOL field DP walaupun tabel `quotes`, skema QuoteCreate, endpoint
            # POST /api/quotes (resolve_dp), form FE, dan template PDF
            # (quote.html blok "Jadwal Pembayaran") SEMUANYA sudah mendukung
            # dp_amount/dp_percent. Akibatnya "DP 60 persen" mendarat di
            # `notes` — satu-satunya laci yang tersisa bagi model — lalu
            # hilang sebagai NILAI: tak tersimpan ke kolom, tak muncul di PDF.
            #
            # Tidak ada whitelist di jalur konfirmasi (unified_chat.py hanya
            # membuang field display_only), jadi begitu kedua nama ini hidup di
            # payload mereka ikut apa adanya ke body POST /api/quotes.
            #
            # field_type: dp_percent = "percent" (bukan "number") supaya kartu
            # mencetak "60%", bukan "Rp 60" — build_review_card_payload
            # memformat "number" sebagai Rupiah. Keduanya sama-sama
            # dideklarasikan ke model sebagai ["number","null"]
            # (build_intent_schema), jadi ekstraksi tidak berubah bentuk.
            FieldSpec(
                name="dp_percent",
                label="DP (%)",
                field_type="percent",
                # JANGAN menambahkan alias telanjang "dp" di sini. "dp" secara
                # alami ambigu: "DP 60" berarti persen, "DP 5 juta" berarti
                # nominal. Alias telanjang memaksa keduanya masuk ke dp_percent,
                # sehingga "DP 5 juta" jadi dp_percent=5000000 -> 422 dari
                # POST /api/quotes. Deskripsi kedua field sudah mengarahkan
                # model memilih dp_percent vs dp_amount dengan benar.
                aliases=["uang_muka_persen", "down_payment_percent"],
                description=(
                    "Uang muka dalam PERSEN. Isi HANYA bila user menyebut "
                    "persentase, mis. \"DP 60 persen\" -> 60. JANGAN mengisi "
                    "dp_percent dan dp_amount sekaligus. JANGAN menaruh "
                    "informasi DP di `notes`."
                ),
            ),
            FieldSpec(
                name="dp_amount",
                label="DP (Rp)",
                field_type="number",
                aliases=["uang_muka", "dp_nominal", "down_payment"],
                description=(
                    "Uang muka dalam RUPIAH. Isi HANYA bila user menyebut "
                    "nominal, mis. \"DP 5 juta\" -> 5000000. JANGAN mengisi "
                    "dp_amount dan dp_percent sekaligus. JANGAN menaruh "
                    "informasi DP di `notes`."
                ),
            ),
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
    # Fix A (Bug C+G+I): deterministic per-customer / per-vendor rollups.
    # response_format="summary" with deterministic Python template — orchestrator
    # short-circuits LLM polish for these two action_keys (Iron Law 1).
    "query_ar_by_customer": QueryActionConfig(
        action_key="query_ar_by_customer",
        display_name="Piutang per Pelanggan",
        rest_endpoint="/api/sales-invoices/outstanding-summary",
        response_format="summary",
        description=(
            "Rekap piutang outstanding per pelanggan (deterministic — bypasses LLM polish). "
            "Sumber: compute_ar_outstanding() (journal-derived). Iron Law 1 compliant."
        ),
        signal_words=[
            "rekap per pelanggan",
            "rekap piutang per pelanggan",
            "tabel per pelanggan",
            "per pelanggan dalam tabel",
            "siapa pelanggan dengan piutang",
            "pelanggan piutang terbesar",
            "rekap piutang per customer",
        ],
        query_params=[],
    ),
    "query_ap_by_vendor": QueryActionConfig(
        action_key="query_ap_by_vendor",
        display_name="Utang per Vendor",
        rest_endpoint="/api/bills/outstanding-summary",
        response_format="summary",
        description=(
            "Rekap utang outstanding per vendor (deterministic — bypasses LLM polish). "
            "Sumber: compute_ap_outstanding() (journal-derived). Iron Law 1 compliant."
        ),
        signal_words=[
            "rekap per vendor",
            "rekap hutang per vendor",
            "rekap utang per vendor",
            "tabel per vendor",
            "per vendor dalam tabel",
            "vendor mana yang utangnya paling besar",
            "vendor mana yang hutangnya paling besar",
            "vendor dengan hutang terbesar",
        ],
        query_params=[],
    ),
    # Why-question contributing-facts (Phase 2, 2026-06-05): MoM driver deltas.
    # NOT backed by a REST endpoint — computed via driver_deltas.compute_driver_deltas
    # and intercepted by a deterministic dispatch in the orchestrator (mirrors
    # query_ar_by_customer / _render_ar_ap_by_entity). rest_endpoint is a sentinel
    # and is never called. response_format="summary".
    "query_business_drivers": QueryActionConfig(
        action_key="query_business_drivers",
        display_name="Faktor Pendorong Keuangan (MoM)",
        rest_endpoint="__computed__/driver-deltas",  # sentinel: never fetched
        response_format="summary",
        description=(
            "Faktor-faktor kontributor di balik pertanyaan 'kenapa' keuangan "
            "(cash flow / laba / omzet / pengeluaran / piutang / hutang). "
            "Membandingkan periode berjalan vs periode sebelumnya (MoM), "
            "journal-derived (Iron Law 1). Deterministic — tidak ada LLM polish."
        ),
        signal_words=[
            "kenapa cash flow",
            "kenapa arus kas",
            "kenapa laba turun",
            "kenapa untung turun",
            "kenapa omzet turun",
            "kenapa pengeluaran naik",
            "mengapa kas berkurang",
            "kenapa piutang naik",
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
    # query_items_list added 2026-06-05: plain "daftar barang" list (no search keyword)
    "query_items_list": QueryActionConfig(
        action_key="query_items_list",
        display_name="Daftar Barang",
        rest_endpoint="/api/items",
        response_format="table",
        description="Daftar barang/produk aktif (tanpa kata kunci pencarian).",
        signal_words=[
            "daftar barang",
            "daftar produk",
            "list barang",
            "list produk",
            "barang saya",
            "produk saya",
        ],
        query_params=[
            QueryParam(
                name="status", label="Status", param_type="string", default="active"
            ),
            QueryParam(name="limit", label="Jumlah", param_type="number", default="50"),
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
    # FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09): a missing required HIDDEN
    # (or display_only) field is a RESOLUTION failure, not a user-askable field.
    # Hidden fields (customer_id, bank_account_id, allocations, ...) carry raw
    # internal UUIDs the user can never supply. Surfacing their labels ("Customer
    # ID", "Bank Account ID") to the user is a UX/trust bug. We therefore keep
    # is_valid accurate (any missing required field => invalid, so the propose
    # never proceeds with incomplete data), but the user-facing `missing` list
    # EXCLUDES hidden/display_only labels. Callers that need to know a hidden
    # field is unresolved should inspect the payload directly (resolver/pills),
    # not echo an ID label.
    missing = []  # user-facing labels ONLY (hidden/display_only excluded)
    has_any_missing = False
    for f in config.fields:
        if f.required and not payload.get(f.name):
            has_any_missing = True
            if not getattr(f, "hidden", False) and not getattr(
                f, "display_only", False
            ):
                missing.append(f.label)

    # At-least-one group validation
    if hasattr(config, "at_least_one_groups"):
        for group in config.at_least_one_groups:
            field_names = group["fields"]
            group_label = group["label"]
            has_any = any(payload.get(fn) for fn in field_names)
            if not has_any:
                has_any_missing = True
                missing.append(group_label)

    return (not has_any_missing), missing


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


# ══════════════════════════════════════════════════════════════════════
# T144 FASE 2 — create_item BANYAK BARANG (satu pesan, satu kartu)
#
# Subfield nama dipilih `nama_produk`, BUKAN `name` (D6). `name` adalah
# entity_name_field create_item: WAJIB, identitas entitas, dan sudah punya
# dua penulis global tak-sadar-items (orchestrator: normalisasi nama
# entitas + jalur pil). Verifikasi tabrakan: `nama_produk` TIDAK pernah
# dibaca/ditulis sebagai kunci TOP-LEVEL payload di jalur create_item —
# seluruh 20 kemunculannya di backend adalah (a) nama KOLOM products, atau
# (b) subfield baris item pada aksi lain, atau (c) pemetaan tampilan
# entitas yang SUDAH ADA (update_item/delete_item). Nol enricher terdaftar
# untuk CREATE_ITEM (ENRICHERS tak memuat kunci itu), jadi tak ada
# penambal yang bisa menyentuhnya.
#
# ⚠️ KOREKSI atas usul D6: body POST /api/items memakai `name`, BUKAN
# `nama_produk` (schemas/items.py CreateItemRequest.name). `nama_produk`
# adalah nama KOLOM DB. Pemetaan subfield -> body dilakukan eksplisit di
# unified_chat._t144_body_item.
# ══════════════════════════════════════════════════════════════════════

T144_BATAS_ITEM = 10

# Field yang kalau kosong membuat satu baris BERMASALAH (ditandai, tetap
# ditampilkan). `sales_price`/`purchase_price` diperiksa sebagai pasangan:
# nol dua-duanya = TAK BISA dibuat (endpoint menolak lewat at_least_one).
T144_FIELD_BARIS = ("nama_produk", "item_type", "base_unit", "sales_price", "purchase_price")


def t144_masalah_baris(baris: dict) -> list[str]:
    """Label field yang hilang pada satu baris. Kosong = baris sehat."""
    if not isinstance(baris, dict):
        return ["baris bukan objek"]
    _label = {
        "nama_produk": "nama",
        "item_type": "tipe",
        "base_unit": "satuan",
        "sales_price": "harga jual",
        "purchase_price": "harga beli",
    }
    return [
        _label[k]
        for k in T144_FIELD_BARIS
        if baris.get(k) in (None, "", 0)
    ]


def t144_baris_bisa_dibuat(baris: dict) -> bool:
    """Cukup untuk POST /api/items? nama + satuan + minimal satu harga."""
    if not isinstance(baris, dict):
        return False
    return bool(
        str(baris.get("nama_produk") or "").strip()
        and str(baris.get("base_unit") or "").strip()
        and (baris.get("sales_price") or baris.get("purchase_price"))
    )


def _t144_rupiah(v) -> str:
    try:
        return "Rp {:,}".format(int(float(v))).replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def t144_baris_teks(i: int, baris: dict) -> tuple[str, str]:
    """(label, value) satu baris untuk kartu. Nol angka karangan."""
    masalah = t144_masalah_baris(baris)
    nama = str(baris.get("nama_produk") or "(tanpa nama)").strip()
    label = "%d. %s" % (i, nama)
    if masalah:
        label = "\u26a0 " + label
    bagian = [
        "Jual %s" % _t144_rupiah(baris.get("sales_price"))
        if baris.get("sales_price")
        else "Jual -",
        "Beli %s" % _t144_rupiah(baris.get("purchase_price"))
        if baris.get("purchase_price")
        else "Beli -",
        str(baris.get("base_unit") or "-"),
        _API_TO_LABEL_MAP.get(
            str(baris.get("item_type") or "").lower(),
            str(baris.get("item_type") or "-"),
        ),
    ]
    nilai = " \u00b7 ".join(bagian)
    if masalah:
        nilai += "  \u2014 belum ada: " + ", ".join(masalah)
    return label, nilai


def t144_items_bulk(action_key: str, payload: dict) -> list | None:
    """Baris bulk create_item, atau None kalau ini jalur skalar."""
    if action_key != "create_item":
        return None
    baris = payload.get("items")
    if not isinstance(baris, list) or not baris:
        return None
    return baris


def t144_peringatan(baris_list: list) -> list[dict]:
    """Peringatan per-baris (D3): sebut BARIS MANA dan apa yang kurang."""
    keluar = []
    for i, b in enumerate(baris_list, start=1):
        masalah = t144_masalah_baris(b)
        if not masalah:
            continue
        nama = str(b.get("nama_produk") or "(tanpa nama)").strip()
        if t144_baris_bisa_dibuat(b):
            keluar.append({
                "type": "warning",
                "message": "Baris %d (%s) belum lengkap: %s. Tetap bisa didaftarkan." % (i, nama, ", ".join(masalah)),
            })
        else:
            keluar.append({
                "type": "error",
                "message": "Baris %d (%s) TIDAK bisa didaftarkan: %s." % (i, nama, ", ".join(masalah)),
            })
    return keluar


# ══════════════════════════════════════════════════════════════════════
# T171 FASE 1 — SLIDE MULTI-BARANG
#
# N>1 barang dalam satu pesan TIDAK lagi jadi satu kartu tabel. Ia jadi:
#   (a) satu KALIMAT PEMBUKA yang menyebut jumlah + SELURUH N nama, dan
#   (b) N slide `CREATE_ITEM` SKALAR biasa, lahir SATU PER SATU.
#
# Kalimat pembuka adalah satu-satunya kesempatan owner membandingkan harga
# berdampingan sebelum slide memisahkan mereka -> ia WAJIB memuat seluruh N
# nama, tidak terpotong. Ia memakai ulang t144_baris_teks (T144), bukan
# format baru.
# ══════════════════════════════════════════════════════════════════════


def t171_kalimat_pembuka(baris_list: list) -> str:
    """Kalimat pembuka slide 1: jumlah + SELURUH N nama, tanpa potong."""
    _n = len(baris_list)
    _keluar = [
        "Ada %d barang di pesan ini. Saya tampilkan satu per satu supaya "
        "tiap barang bisa dicek — dan dilewati — sendiri-sendiri.\n" % _n
    ]
    for _i, _b in enumerate(baris_list, start=1):
        _lb, _vl = t144_baris_teks(_i, _b)
        _keluar.append("%s — %s" % (_lb, _vl))
    return "\n".join(_keluar)


def t171_baris_ke_payload(baris: dict, warisan: dict | None = None) -> dict:
    """Satu baris bulk -> payload create_item SKALAR (kunci body POST /api/items).

    ⚠️ `name`, bukan `nama_produk`: `nama_produk` adalah kolom DB; body
    CreateItemRequest memakai `name`.
    """
    _p = dict(warisan or {})
    _p["name"] = str(baris.get("nama_produk") or "").strip()
    for _k in ("item_type", "base_unit", "sales_price", "purchase_price"):
        _v = baris.get(_k)
        if _v not in (None, ""):
            _p[_k] = _v
        else:
            _p.pop(_k, None)
    return _p


AKUN_TAK_DIKENALI = "(akun belum dikenali)"


def bangun_pratinjau_jurnal_dari_lines(payload: dict) -> list | None:
    """Pratinjau jurnal untuk `create_journal_entry`, DIHITUNG LOKAL.

    Kenapa lokal dan bukan endpoint pratinjau: barisnya SUDAH ADA di payload --
    user sendiri yang mengarangnya. Memanggil jaringan untuk menyusun ulang apa
    yang sudah dipegang menambah satu jalur non-fatal yang mengembalikan None
    diam-diam (`_get_journal_preview`: `if resp.status_code >= 400: return None`),
    dan None di sana berarti kartu kembali kosong -- persis penyakit yang
    diperbaiki tiket ini. Nol panggilan jaringan = nol cara gagal senyap.

    NAMA AKUN, BUKAN UUID. `account_id` pada baris jurnal adalah UUID mentah;
    memuntahkannya ke layar sudah pernah terjadi (kartu quick_stock_adjustment)
    dan tidak diulang di sini. Yang memuat nama akun adalah `description` tiap
    baris (terukur dari dump `pending_actions.action_plan`: "Beban Sewa",
    "Bank"). Kalau `description` kosong, kartu berkata jujur
    "(akun belum dikenali)" -- tidak pernah menambal dengan UUID.

    Bentuk keluaran SENGAJA sama dengan yang dibalas endpoint pratinjau
    (`account_name` / `debit` / `credit`) supaya dua konsumen yang sudah ada --
    `build_confirmation_table` dan `build_review_card_payload` -- memakainya
    tanpa cabang khusus. Keseimbangan tetap dihitung oleh
    `build_review_card_payload`, satu tempat, sama untuk semua aksi.
    """
    lines = payload.get("lines")
    if isinstance(lines, str):
        try:
            import json as _json

            lines = _json.loads(lines)
        except (ValueError, TypeError):
            return None
    if not isinstance(lines, list) or not lines:
        return None

    pratinjau = []
    for baris in lines:
        if not isinstance(baris, dict):
            continue
        # `debit`/`credit` bisa TIDAK ADA sama sekali pada satu sisi (terukur:
        # baris kredit yang tak memuat kunci "debit"), jadi `or 0` wajib.
        try:
            dr = float(baris.get("debit", 0) or 0)
            cr = float(baris.get("credit", 0) or 0)
        except (ValueError, TypeError):
            continue
        nama = str(baris.get("description") or "").strip() or AKUN_TAK_DIKENALI
        pratinjau.append({"account_name": nama, "debit": dr, "credit": cr})

    return pratinjau or None


def build_confirmation_table(
    action_key: str, payload: dict, journal_preview: list | None = None
) -> str:
    """Build markdown confirmation table with trust context — fully config-driven."""
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return ""

    # T171 FASE 1 — cabang bulk DIBUBARKAN. N>1 barang tidak lagi menjadi satu
    # tabel; ia dipecah jadi N slide skalar di _execute_propose_direct, dan isi
    # tabel lama pindah ke KALIMAT PEMBUKA (t171_kalimat_pembuka). Pembantu
    # render baris (t144_baris_teks dst) DIPERTAHANKAN dan dipakai ulang di sana.
    if t144_items_bulk(action_key, payload) is not None:
        logger.warning(
            "[T171_SISA_BULK] payload create_item MASIH membawa `items` saat "
            "kartu dibangun -- pemecahan slide tidak terjadi. baris=%d",
            len(payload.get("items") or []),
        )

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
            # FIX_AQUA_PERCENT_DISPLAY 2026-05-13: render percent fields with % suffix
            if f.field_type == "percent":
                try:
                    num_val = float(value)
                    display_value = f"{num_val:g}%"
                except (ValueError, TypeError):
                    display_value = f"{value}%"
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

        # T184 -- baris total + status seimbang pada versi TEKS kartu, dengan
        # radius yang sama seperti di build_review_card_payload: hanya jurnal
        # umum. Aksi lain memakai fungsi ini juga; menambah baris di sana akan
        # mengubah teks konfirmasi mereka tanpa diminta.
        if action_key == "create_journal_entry":
            _tdr = sum(float(jl.get("debit", 0) or 0) for jl in journal_preview)
            _tcr = sum(float(jl.get("credit", 0) or 0) for jl in journal_preview)
            lines.append(
                "  Total Debit  Rp {:,.0f}".format(_tdr).replace(",", ".")
            )
            lines.append(
                "  Total Kredit Rp {:,.0f}".format(_tcr).replace(",", ".")
            )
            if abs(_tdr - _tcr) < 0.01:
                lines.append("  Status: SEIMBANG")
            else:
                lines.append(
                    "  Status: TIDAK SEIMBANG (selisih Rp {:,.0f})".format(
                        abs(_tdr - _tcr)
                    ).replace(",", ".")
                )

    return "\n".join(lines)


def _pesan_jurnal_tanpa_dampak(
    action_key: str, payload: dict, total_dr: float, total_cr: float
) -> str:
    """Kalimat untuk kartu ketika pratinjau jurnal TIDAK LAYAK ditampilkan.

    Harus menyebut APA yang kurang, bukan bahwa ada yang kurang. "Jurnal tidak
    valid" memindahkan pekerjaan diagnosa ke user yang bukan akuntan; "Jumlah
    untuk 'jasa sablon' belum diisi" memberi tahu tombol mana yang harus ditekan.
    """
    # T184 -- JURNAL UMUM: barisnya DIARANG USER, jadi diagnosanya berbeda.
    #
    # Cabang di bawah berkata "laporkan ke tim MilkyHoop" karena untuk faktur
    # dan pembayaran barisnya disusun SERVER: jurnal sepihak di sana memang bug
    # kami. Pada jurnal umum manual barisnya diketik user, jadi kalimat yang
    # sama menyuruh user melapor atas kesalahan yang bisa ia perbaiki sendiri
    # dalam satu kalimat -- dan menyembunyikan apa yang sebenarnya kurang.
    if action_key == "create_journal_entry":
        if total_dr > 0 or total_cr > 0:
            ada = "debit" if total_dr > 0 else "kredit"
            kurang = "kredit" if total_dr > 0 else "debit"
            return (
                f"Jurnal ini hanya punya sisi {ada}; sisi {kurang}-nya belum "
                "ada. Setiap jurnal butuh dua sisi yang jumlahnya sama. "
                f"Sebutkan akun {kurang}-nya lalu kirim lagi."
            )
        return (
            "Semua baris jurnal ini bernilai Rp 0, jadi belum ada yang bisa "
            "dicatat. Sebutkan nominal tiap barisnya lalu kirim lagi."
        )

    # Satu sisi saja padahal ada nilai: ini bukan kesalahan input user.
    if total_dr > 0 or total_cr > 0:
        sisi = "kredit" if total_dr > 0 else "debit"
        return (
            f"Pratinjau jurnal ini tidak punya sisi {sisi}, jadi belum bisa "
            "ditampilkan. Dokumennya belum tersimpan — laporkan ke tim MilkyHoop."
        )

    items = payload.get("items")
    if isinstance(items, str):
        try:
            import json as _json

            items = _json.loads(items)
        except Exception:
            items = None
    if isinstance(items, list) and items:
        tanpa_jumlah, tanpa_harga = [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            nama = str(
                it.get("product_name")
                or it.get("description")
                or it.get("name")
                or "barang/jasa"
            ).strip()
            try:
                _q = float(it.get("quantity", it.get("qty", 0)) or 0)
            except (TypeError, ValueError):
                _q = 0.0
            try:
                _h = float(it.get("unit_price", it.get("price", 0)) or 0)
            except (TypeError, ValueError):
                _h = 0.0
            if _q <= 0:
                tanpa_jumlah.append(nama)
            elif _h <= 0:
                tanpa_harga.append(nama)
        if tanpa_jumlah:
            _d = ", ".join(f"\u2018{n}\u2019" for n in tanpa_jumlah[:3])
            return (
                f"Jumlah untuk {_d} belum diisi, jadi nilai dokumen ini Rp 0 dan "
                "belum ada dampak jurnal. Sebutkan berapa banyak — misalnya "
                "“2 pcs” — lalu kirim lagi."
            )
        if tanpa_harga:
            _d = ", ".join(f"\u2018{n}\u2019" for n in tanpa_harga[:3])
            return (
                f"Harga untuk {_d} belum diisi, jadi nilai dokumen ini Rp 0 dan "
                "belum ada dampak jurnal. Sebutkan harganya lalu kirim lagi."
            )

    return (
        "Nilai transaksi ini masih Rp 0, jadi belum ada dampak jurnal. "
        "Sebutkan nominalnya lalu kirim lagi."
    )


def build_review_card_payload(
    action_key: str,
    payload: dict,
    journal_preview: list | None = None,
    preview_warnings: list | None = None,
) -> dict | None:
    """Build structured review card payload for frontend rendering.

    Returns structured data that replaces the markdown confirmation_table.
    Frontend uses this for InlineReviewCard (in-chat) and ReviewCardArtifact (side panel).
    """
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return None

    # T171 FASE 1 — cabang bulk DIBUBARKAN (lihat build_confirmation_table).
    # Kartu create_item selalu skema G1 (kunci per-field), tidak pernah lagi
    # skema M1 (5x kunci "items").

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
        # T171 FASE 1 — pensil MATI untuk slide batch. Slide lahir dari daftar
        # yang owner ketik sendiri; mengeditnya di tengah rentetan akan menabrak
        # antrean (`_batch_queue` sudah terkunci di action_plan slide ini).
        # Efek samping DISETUJUI: tombol "Edit" ikut hilang (nolFieldEditable).
        # NOL sentuhan payloadOverridesRef -> T160 tak tersentuh.
        _is_editable = (
            f.editable
            and not f.display_only
            and not f.name.endswith("_id")
            and f.field_type not in ("enum", "boolean")
            and not payload.get("_batch_id")
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

    # Warnings dari endpoint pratinjau (pelanggan tak ada, stok minus, periode
    # tertutup, biaya pokok belum terbentuk). Sebelumnya dibuang di
    # tool_executor._get_journal_preview dan tak pernah sampai ke user.
    # Heuristik `impact_notes` di atas TIDAK dipakai untuk ini: ia menandai "info"
    # bila teks tak memuat kata "tidak", sehingga "Stok ... kurang dari ..." akan
    # salah turun kelas jadi info. Yang dari pratinjau adalah temuan atas data
    # nyata — default-nya peringatan, kecuali catatan kebijakan.
    for note in preview_warnings or []:
        if not note:
            continue
        wtype = "info" if str(note).startswith("Kebijakan tenant") else "warning"
        warnings.append({"type": wtype, "message": str(note)})

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

        # JURNAL TANPA SISI DEBIT BUKAN JURNAL.
        #
        # `abs(total_dr - total_cr) < 0.01` sendirian TIDAK BISA menolak jurnal
        # kosong: 0 - 0 = 0, jadi ✓ BALANCE menyala untuk dokumen bernilai Rp 0
        # (qty absen pada baris jasa, nominal pembayaran 0). Terbukti pada LIMA
        # aksi sekaligus — faktur jual, faktur beli, biaya, terima bayar, bayar
        # tagihan — karena renderer ini dipakai bersama.
        #
        # Ini instans KEDUA ✓ BALANCE berbohong. T6: nilai hilang dari kedua
        # sisi sehingga selisihnya tetap nol. Di sini: nilainya nol sejak awal.
        # Mekanismenya berbeda, kesimpulannya sama — PEMERIKSA YANG HANYA
        # MELIHAT SELISIH TAK PERNAH BISA MELIHAT KETIADAAN.
        #
        # Tiga syarat, ditulis terpisah walau syarat ke-3 tercakup dua yang awal,
        # supaya yang membaca tahu ketiganya memang dimaksudkan.
        _tanpa_debit = total_dr <= 0
        _tanpa_kredit = total_cr <= 0
        _total_nol = total_dr <= 0 and total_cr <= 0
        if _tanpa_debit or _tanpa_kredit or _total_nol:
            # Bentuk keluaran mengikuti preseden 47fef208 (non-PKP + pajak):
            # NOL BARIS JURNAL + satu kalimat jelas — bukan
            # journal_balanced=False, yang merender “TIDAK BALANCE” dan
            # menyesatkan: masalahnya bukan ketimpangan, melainkan ketiadaan.
            journal_lines = None
            journal_balanced = None
            warnings.append(
                {
                    "type": "warning",
                    "message": _pesan_jurnal_tanpa_dampak(
                        action_key, payload, total_dr, total_cr
                    ),
                }
            )
        else:
            journal_balanced = abs(total_dr - total_cr) < 0.01

            # T184 -- TOTAL DAN STATUS SEIMBANG HARUS TERBACA, BUKAN TERSIRAT.
            #
            # Renderer kartu (ReviewCardArtifact / InlineReviewCard) hanya
            # menggambar lencana POSITIF: `journal_balanced && "checkmark
            # Balance"`. Untuk journal_balanced=False ia menggambar NOL --
            # tidak ada teks "tidak seimbang" di mana pun di kartu chat
            # (terukur di bundel terpasang main.82ce0f51.js: "Tidak Seimbang"
            # muncul 5x, semuanya di neraca/arus kas/trial balance, nol di
            # kartu chat). Jadi jurnal timpang tampil sebagai KETIADAAN
            # lencana, dan ketiadaan tidak pernah terbaca sebagai peringatan.
            #
            # Slot `warnings` DIGAMBAR TANPA SYARAT oleh kedua renderer, jadi
            # di situlah kalimatnya ditaruh. Sekalian totalnya: kartu tidak
            # punya slot total untuk jurnal (`totals` milik baris barang), dan
            # meminjam slot itu akan salah merender.
            #
            # RADIUS: hanya create_journal_entry. Aksi lain (faktur jual/beli,
            # biaya, terima bayar, bayar tagihan) memakai renderer yang sama;
            # menambah catatan di sana akan mengubah kartu mereka byte demi
            # byte tanpa diminta. Kebutaan "tidak seimbang" pada aksi-aksi itu
            # NYATA tapi diusulkan terpisah -- di sana barisnya disusun server
            # dari dokumen, bukan diarang user.
            if action_key == "create_journal_entry":
                _dr = "Rp {:,.0f}".format(total_dr).replace(",", ".")
                _cr = "Rp {:,.0f}".format(total_cr).replace(",", ".")
                if journal_balanced:
                    warnings.append(
                        {
                            "type": "info",
                            "message": (
                                f"Total debit {_dr} = total kredit {_cr}. "
                                "Jurnal SEIMBANG."
                            ),
                        }
                    )
                else:
                    _selisih = "Rp {:,.0f}".format(abs(total_dr - total_cr)).replace(
                        ",", "."
                    )
                    warnings.append(
                        {
                            "type": "warning",
                            "message": (
                                f"JURNAL TIDAK SEIMBANG. Total debit {_dr}, "
                                f"total kredit {_cr}, selisih {_selisih}. "
                                "Jurnal ini akan DITOLAK saat disimpan -- "
                                "perbaiki barisnya dulu."
                            ),
                        }
                    )

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
        # FIX_AQUA_DISCOUNT_AMOUNT_APPLY 2026-05-19: consume discount_amount (invoice) / invoice_discount_amount (bill) Rp fixed
        discount_pct = float(
            payload.get("discount_percent", payload.get("invoice_discount_percent", 0))
            or 0
        )
        raw_discount = float(
            payload.get(
                "discount_amount",
                payload.get("invoice_discount_amount", payload.get("discount", 0)),
            )
            or 0
        )
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
    #
    # K3 2026-08-12: penentunya adalah PUNYA BARIS ITEM, bukan punya jurnal.
    # Yang menentukan BENTUK kartu adalah apakah dokumennya berbaris — baris
    # butuh tabel, dan tabel butuh panel samping. Punya jurnal menentukan
    # DAMPAK AKUNTANSI, bukan bentuk tampilan; mencampur keduanya membuat
    # penawaran dan pesanan penjualan — dokumen berbaris yang memang tak
    # berjurnal — dipaksa masuk kartu inline yang tak muat menampung tabelnya.
    #
    # Ketiadaan jurnal TIDAK melahirkan bagian kosong: journal_lines dan
    # journal_balanced tetap None (lihat blok "Journal preview lines" di atas),
    # bentuk yang sama yang ditetapkan e380b613 supaya kartu tak pernah
    # menampilkan "✓ BALANCE" untuk jurnal yang tak ada.
    has_items = items is not None and len(items) > 0
    render_target = "artifact" if has_items else "inline"

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


# ══════════════════════════════════════════════════════════════════════════
# TOMBOL KONFIRMASI — diumumkan config, bukan dikarang FE  (dok. 81 bagian 3)
#
# FE menampilkan DUA tombol ("Simpan Draft" / "Posting") dan mengirim
# doc_status. Tapi backend hanya MENERJEMAHKAN doc_status untuk LIMA aksi;
# untuk 55 sisanya — termasuk penawaran dan konversi — kedua tombol memanggil
# jalur yang sama dan menghasilkan hasil yang sama. Salah satunya berkata
# "Posting" pada dokumen yang tak pernah berjurnal.
#
# Dua himpunan di bawah adalah SUMBER TUNGGAL: routers/unified_chat.py
# membacanya untuk MENERJEMAHKAN doc_status, dan tombol yang diumumkan ke FE
# diturunkan dari himpunan yang SAMA. Jadi aksi ke-61 tak bisa punya perilaku
# dua-tombol tanpa tombolnya ikut muncul, dan sebaliknya.
#
# ⚠️ "Posting" TIDAK dihapus di mana-mana. Untuk post_bill /
# post_sales_invoice / faktur bermuatan jurnal, kata itu JUJUR. Menyeragamkan
# semuanya jadi "Simpan" akan menukar satu kebohongan dengan kebohongan ke
# arah sebaliknya — dan yang kedua lebih berbahaya, karena menyembunyikan
# saat jurnal benar-benar terbentuk.
# ══════════════════════════════════════════════════════════════════════════

# doc_status="DRAFT" mengubah payload untuk aksi-aksi ini
AKSI_DOC_STATUS_DRAFT: tuple[str, ...] = (
    "create_sales_invoice",
    "create_sales_order",
    "create_bill",
)
# doc_status="POSTED" memicu panggilan /post kedua untuk aksi-aksi ini
AKSI_DOC_STATUS_POSTED: tuple[str, ...] = (
    "create_credit_note",
    "create_vendor_credit",
)
AKSI_DUA_TOMBOL: tuple[str, ...] = AKSI_DOC_STATUS_DRAFT + AKSI_DOC_STATUS_POSTED

# Label sisi "terbitkan" pada aksi dua-tombol. create_sales_order menghormati
# doc_status TAPI nol jurnal — jadi "Posting" salah untuknya, sementara untuk
# faktur penjualan/pembelian dan nota kredit ia benar.
_LABEL_TERBIT = {
    "create_sales_order": "Simpan",
}


def tombol_konfirmasi(action_key: str) -> list[dict]:
    """Tombol yang BERMAKNA untuk aksi ini.

    Satu tombol = doc_status diabaikan backend, jadi menawarkan dua pilihan
    adalah pilihan palsu. Dua tombol = keduanya benar-benar berbeda hasilnya.
    """
    config = DIRECT_ACTIONS.get(action_key)
    if not config:
        return []
    if action_key in AKSI_DUA_TOMBOL:
        return [
            {"doc_status": "DRAFT", "label": "Simpan Draft"},
            {
                "doc_status": "POSTED",
                "label": _LABEL_TERBIT.get(action_key, "Posting"),
            },
        ]
    return [
        {
            "doc_status": "POSTED",
            "label": config.label_tombol or config.display_name,
        }
    ]


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
        # dok. 81 (3): kunci BARU. FE lama tak mengenalnya dan mengabaikannya;
        # sampai FE menyusul, ini NOL DAMPAK ke layar.
        "tombol": tombol_konfirmasi(action_key),
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


# ══════════════════════════════════════════════════════════════════════════
# AKSI ATAS DOKUMEN YANG SUDAH ADA  (dok. 79, 2026-08-13)
#
# Kelas aksi yang MENGGERAKKAN dokumen yang sudah lahir — bukan membuatnya,
# bukan membatalkannya. Endpointnya sudah ada dan lengkap sejak lama; yang
# tak pernah ada adalah jalur chat menuju ke sana (dok. 78: 12 endpoint,
# nol action_key).
#
# KENAPA TABEL, BUKAN DUA BELAS CONFIG DITULIS TANGAN
# Tiap aksi membawa tujuh hal yang bisa keliru (endpoint, tabel sumber,
# kolom nomor, status yang sah, kata kerja, pesan, creates_journal). Dua
# belas config = 84 kesempatan menyimpang. Kita sudah membayar bentuk itu
# tiga kali: enrichment x3, nama item x3, nomor dokumen x2.
#
# Sebuah BARIS TABEL tidak bisa punya jalur kode yang berbeda. Satu-satunya
# cara menyimpang adalah mengubah _bangun_aksi_dokumen — satu tempat, satu
# diff. Menyimpang jadi mustahil secara struktural, bukan secara disiplin.
#
# Kolomnya dirancang untuk dua belas sejak awal meskipun baru SATU yang
# diisi: menambah kolom nanti berarti menyentuh pembangun, dan pembangun
# adalah titik yang harus stabil.
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LangkahBerikutnya:
    """Langkah yang menyelesaikan kebuntuan, untuk satu status yang memblokir.

    Pesan gerbang lama berhenti di SEBAB: "Penawaran QUO-2608-0013 berstatus
    'draf', jadi belum bisa dikonversi jadi pesanan." Benar, dan owner tetap
    harus menebak solusinya — pindah ke dashboard, menemukan tombol "Kirim
    Penawaran", lalu kembali ke chat. Sebab tanpa langkah memindahkan beban
    berpikir ke orang yang justru datang ke chat untuk menghindarinya.

    ⚠️ `aksi_prasyarat` membuat kalimatnya JUJUR SECARA STRUKTURAL. Menyuruh
    owner mengetik "kirim penawaran X" hanya sah bila aksi itu benar-benar
    terdaftar. Alih-alih mengandalkan urutan penggarapan (N1 sebelum N2 —
    sesuatu yang tak meninggalkan jejak kalau salah), kalimatnya DITURUNKAN
    dari isi registry: baris quote_send dicabut -> teks otomatis kembali
    menunjuk dashboard. Tak ada keadaan di mana bot menyuruh mengetik perintah
    yang tak ada.
    """

    status: str  # status yang memblokir
    aksi_prasyarat: str  # action_key penyedia jalur chat; "" = selalu teks_chat
    teks_chat: str  # {nomor} = nomor dokumen
    teks_dashboard: str


@dataclass(frozen=True)
class AksiDokumen:
    """Satu baris deklarasi aksi-atas-dokumen. Data, bukan kode."""

    action_key: str
    entity_type: str
    display_name: str
    # ── sumber: bagaimana nomor dokumen jadi UUID ──
    tabel: str
    kolom_nomor: str
    field_nomor: str  # nama field yang membawa nomor di entities/payload
    label_nomor: str  # untuk BARIS TABEL kartu ("No. Penawaran")
    sebutan: str  # untuk KALIMAT ke user ("Penawaran") — beda tempat, beda bentuk
    kata_kerja_pasif: str  # "dikonversi" — dipakai menyusun kalimat penolakan
    # ── eksekusi ──
    endpoint: str
    rest_method: str
    # ── gerbang status (dipakai DI DEPAN; endpoint tetap menjaga di belakang) ──
    status_boleh: tuple[str, ...]
    # ── bahasa ──
    kata_kerja: tuple[str, ...]  # di AWAL kalimat
    kata_sumber: tuple[str, ...]  # kata entitas dokumen sumber, di mana saja
    kata_tujuan: tuple[str, ...]  # kata dokumen TUJUAN, di mana saja
    pesan_memproses: str
    pesan_sukses: str
    # ── risiko ──
    creates_journal: bool
    risk_level: str
    ttl_seconds: int
    # ── penunjuk hasil, untuk pesan penolakan yang berguna ──
    kolom_tujuan_id: str = ""
    tabel_tujuan: str = ""
    kolom_nomor_tujuan: str = ""
    # ── ringkasan dokumen untuk kartu (pratinjau DOKUMEN, bukan jurnal) ──
    kolom_ringkas: tuple[tuple[str, str], ...] = ()  # (kolom_db, field_payload)
    tabel_baris: str = ""
    kolom_induk_baris: str = ""
    label_tombol: str = "Konfirmasi"
    # N2 — apa yang harus owner LAKUKAN, bukan hanya kenapa ia ditolak.
    langkah: tuple["LangkahBerikutnya", ...] = ()
    # Sebagian aksi tak punya dokumen tujuan (mengirim penawaran menghasilkan
    # penawaran yang sama, berstatus lain). Kosong = kalimat gerbang tidak
    # menambahkan "jadi <tujuan>".
    sebutan_tujuan: str = ""


DOCUMENT_ACTIONS: tuple[AksiDokumen, ...] = (
    AksiDokumen(
        action_key="quote_to_order",
        entity_type="quote",
        display_name="Konversi Penawaran ke Pesanan",
        tabel="quotes",
        kolom_nomor="quote_number",
        field_nomor="quote_number",
        label_nomor="No. Penawaran",
        sebutan="Penawaran",
        sebutan_tujuan="pesanan",
        kata_kerja_pasif="dikonversi",
        endpoint="/api/quotes/{id}/to-order",
        rest_method="POST",
        # routers/quotes.py:1475 — sumber kebenaran kedua ada di endpoint.
        # Gate WAJIB membuktikan kedua sisi sepakat; kalau endpoint berubah
        # dan baris ini tidak, gate itu yang merah.
        status_boleh=("sent", "accepted", "viewed"),
        kata_kerja=("konversi", "jadikan"),
        kata_sumber=("penawaran", "quote", "quotation"),
        kata_tujuan=("pesanan", "sales order", "so"),
        pesan_memproses="Mengonversi penawaran {entity_name}\u2026",
        pesan_sukses="Penawaran '{entity_name}' berhasil dikonversi jadi pesanan.",
        creates_journal=False,  # diverifikasi dok. 79 M1b: kode + trigger
        risk_level="medium",
        ttl_seconds=120,
        kolom_tujuan_id="converted_to_id",
        tabel_tujuan="sales_orders",
        kolom_nomor_tujuan="order_number",
        kolom_ringkas=(
            ("customer_name", "customer_name"),
            ("total_amount", "total_amount"),
            ("status", "_status"),
        ),
        tabel_baris="quote_items",
        kolom_induk_baris="quote_id",
        label_tombol="Konversi",
        langkah=(
            LangkahBerikutnya(
                status="draft",
                aksi_prasyarat="quote_send",
                teks_chat="Kirim penawarannya dulu — ketik \u201ckirim penawaran {nomor}\u201d.",
                teks_dashboard=(
                    "Kirim penawarannya dulu lewat Penjualan \u2192 Penawaran, "
                    "tombol \u201cKirim Penawaran\u201d."
                ),
            ),
        ),
    ),
    # ── N1: mengirim penawaran (draft -> sent) ────────────────────────────
    # Satu baris tabel, nol jalur baru. Yang membuatnya layak ditambahkan
    # bukan endpointnya (sudah ada sejak lama) melainkan bahwa TANPA-nya
    # rantai penawaran putus di tengah: owner bisa MEMBUAT penawaran lewat
    # chat dan bisa MENGONVERSI-nya lewat chat, tapi langkah di antara
    # keduanya hanya ada di dashboard.
    #
    # ⚠️ NOL EFEK KELUAR. POST /api/quotes/{id}/send hanya
    # "UPDATE quotes SET status='sent', sent_at=NOW()". Cabang pengiriman
    # email dijaga body.send_email (default False, dan kami tak mengirimkan
    # body sama sekali), DAN panggilan email-nya sendiri masih dikomentari —
    # quotes.py tidak mengimpor email_service, sementara signup.py dan
    # team_members.py memang mengimpornya. Jadi aksi ini mengubah KEADAAN
    # dokumen, bukan mengirim apa pun ke pelanggan.
    AksiDokumen(
        action_key="quote_send",
        entity_type="quote",
        display_name="Kirim Penawaran",
        tabel="quotes",
        kolom_nomor="quote_number",
        field_nomor="quote_number",
        label_nomor="No. Penawaran",
        sebutan="Penawaran",
        kata_kerja_pasif="dikirim",
        endpoint="/api/quotes/{id}/send",
        rest_method="POST",
        # ⚠️ Endpoint menerima draft DAN sent; gerbang chat sengaja LEBIH
        # SEMPIT, dan itu bukan kelalaian menyalin:
        #   1. mengirim ulang yang sudah 'sent' menimpa sent_at diam-diam —
        #      owner kehilangan kapan penawaran benar-benar dikirim;
        #   2. lebih buruk, trg_quote_expiry membalik NEW.status := 'expired'
        #      ketika OLD='sent' DAN NEW='sent' DAN expiry_date < hari ini.
        #      Jadi "kirim penawaran" pada penawaran lewat tanggal justru
        #      MENG-EXPIRE-kannya. Nol pesan, nol galat.
        # Chat memilih jalur yang tak bisa mengejutkan; dashboard tetap punya
        # keleluasaan penuh.
        status_boleh=("draft",),
        kata_kerja=("kirim", "kirimkan"),
        kata_sumber=("penawaran", "quote", "quotation"),
        kata_tujuan=(),
        pesan_memproses="Mengirim penawaran {entity_name}\u2026",
        pesan_sukses=(
            "Penawaran {quote_number} ditandai terkirim. "
            "Sekarang bisa dikonversi jadi pesanan."
        ),
        creates_journal=False,
        risk_level="low",
        ttl_seconds=120,
        kolom_ringkas=(
            ("customer_name", "customer_name"),
            ("total_amount", "total_amount"),
            ("status", "_status"),
        ),
        tabel_baris="quote_items",
        kolom_induk_baris="quote_id",
        label_tombol="Kirim Penawaran",
        langkah=(
            LangkahBerikutnya(
                status="sent",
                aksi_prasyarat="quote_to_order",
                teks_chat=(
                    "Penawaran ini sudah terkirim. Lanjutkan dengan "
                    "\u201ckonversi penawaran {nomor} jadi pesanan\u201d."
                ),
                teks_dashboard=(
                    "Penawaran ini sudah terkirim. Lanjutkan lewat "
                    "Penjualan \u2192 Penawaran."
                ),
            ),
        ),
    ),
)

DOCUMENT_ACTIONS_BY_KEY: dict[str, AksiDokumen] = {
    a.action_key: a for a in DOCUMENT_ACTIONS
}


def _bangun_aksi_dokumen(a: AksiDokumen) -> DirectActionConfig:
    """Satu-satunya tempat sebuah baris DOCUMENT_ACTIONS jadi config.

    Kartu sengaja TIDAK punya bagian jurnal: keempat aksi konversi nol jurnal
    (dok. 79 M1b, diverifikasi dua arah — badan endpoint DAN trigger tabel).
    journal_preview_endpoint dibiarkan kosong supaya tak lahir slot kosong;
    itu bentuk yang ditetapkan e380b613.
    """
    fields = [
        FieldSpec(name="id", label="ID", required=True, hidden=True),
        FieldSpec(name=a.field_nomor, label=a.label_nomor, display_only=True),
    ]
    for _, field_payload in a.kolom_ringkas:
        if field_payload.startswith("_"):
            continue  # kolom kerja (mis. status), bukan untuk mata user
        fields.append(
            FieldSpec(
                name=field_payload,
                label={
                    "customer_name": "Pelanggan",
                    "total_amount": "Total",
                }.get(field_payload, field_payload),
                field_type="number" if field_payload.endswith("_amount") else "string",
                display_only=True,
            )
        )
    if a.tabel_baris:
        fields.append(
            FieldSpec(
                name="jumlah_baris", label="Jumlah baris", display_only=True
            )
        )
    return DirectActionConfig(
        action_key=a.action_key,
        display_name=a.display_name,
        rest_endpoint=a.endpoint,
        rest_method=a.rest_method,
        entity_type=a.entity_type,
        risk_level=a.risk_level,
        creates_journal=a.creates_journal,
        ttl_seconds=a.ttl_seconds,
        action_type_key=a.action_key.upper(),
        signal_words=[
            f"{kk} {ks}" for kk in a.kata_kerja for ks in a.kata_sumber
        ],
        entity_name_field=a.field_nomor,
        loading_message_template=a.pesan_memproses,
        success_message_template=a.pesan_sukses,
        journal_preview_endpoint="",
        label_tombol=a.label_tombol,
        fields=fields,
    )


DIRECT_ACTIONS.update(
    {a.action_key: _bangun_aksi_dokumen(a) for a in DOCUMENT_ACTIONS}
)
