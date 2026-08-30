"""
Unified Tool Executor for MilkyHoop Agent.

Handles two types of tools:
1. READ tools - httpx GET to kernel API endpoints
2. ACTION tools - gRPC to validator + executor

Pattern: extended from ragllm/tool_executor.py with action tool support.
"""

import asyncio
import json
import re
import hashlib
import time
import logging
from datetime import date as _date_cls, datetime, timedelta
from typing import Any, Dict, List, Optional
from decimal import Decimal
from itertools import combinations

import httpx


# ── PHONE COERCION (2026-05-09) ──────────────────────────────────────────
# LLM JSON sometimes returns phone as int (e.g. 8135478652) — leading zero
# lost, sometimes precision lost too. Pydantic schemas expect Optional[str]
# (max_length=50). Coerce known phone-like keys to string and try to recover
# leading zero / full digits from OCR text if available.
_PHONE_KEYS = (
    "phone",
    "phone2",
    "mobile_phone",
    "telepon",
    "telepon_perusahaan",
    "telp",
    "hp",
    "no_hp",
    "no_telp",
    "whatsapp",
    "wa",
)
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")


def _coerce_phone_fields(payload: dict, ocr_text: Optional[str] = None) -> dict:
    """Coerce phone-like keys in payload to string, recover leading zero from OCR."""
    if not isinstance(payload, dict):
        return payload
    for key in list(payload.keys()):
        if key not in _PHONE_KEYS:
            continue
        v = payload[key]
        if v is None or isinstance(v, str):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            digits = str(int(v))
            recovered = digits
            # Try to recover leading zero / full digits from OCR text
            if ocr_text:
                for m in _PHONE_RE.findall(ocr_text):
                    cleaned = re.sub(r"[^\d+]", "", m)
                    cleaned_digits = cleaned.lstrip("+")
                    if cleaned_digits.endswith(digits) or digits in cleaned_digits:
                        recovered = cleaned
                        break
            else:
                # No OCR — assume Indonesian phone needs leading 0 if missing
                if not recovered.startswith("0") and not recovered.startswith("+"):
                    recovered = "0" + recovered
            payload[key] = recovered
            try:
                logger.warning(
                    "[FIX_PHONE_COERCE] key=%s int->%s (ocr_match=%s)",
                    key,
                    recovered,
                    recovered != digits,
                )
            except Exception:
                pass
    return payload


def _to_amount(value) -> "Decimal":
    """Convert any amount value to Decimal for precision-safe comparison (Law 25)."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        logger.warning(
            f"[MATCH] Invalid amount value: {value!r} ({type(value).__name__})"
        )
        return Decimal("0")


_ABSOLUTE_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
    r"|\d{1,2}\s+(januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember)"
    r"|tertanggal|per\s+tanggal",
    re.IGNORECASE,
)


def _user_gave_absolute_date(user_text: str) -> bool:
    """Return True when user_text contains an explicit absolute date expression.

    Used by enrichers to decide whether to trust an LLM-extracted date that
    looks stale (>30d past / prior year). If the user explicitly typed a date,
    we must honor backdated entries instead of overriding to today.
    """
    if not user_text:
        return False
    try:
        return bool(_ABSOLUTE_DATE_RE.search(user_text))
    except Exception:
        return False


# ── T143 JUDUL-DARI-ITEM 2026-08-27 ──────────────────────────────────────
# Penanda "judul yang user sebut sendiri". Bentuknya DISALIN dari
# _user_gave_absolute_date / _user_stated_issue_date (FIX_BILL_ABSDATE_PERSIST)
# — pasangan "detektor teks + penanda boolean yang ikut di payload" — karena
# jalur pil membuat ToolExecutor BARU dengan user_text = nilai pil (UUID),
# sehingga deteksi dari teks saja MUSTAHIL bertahan ke giliran kedua.
_JUDUL_EKSPLISIT_RE = re.compile(
    r"(?:^|[,;.\n]|\bdengan\s+|\bdgn\s+)\s*"
    r"(?:judul|perihal|subjek|subject)\s*[:\-]?\s+"
    r"(?P<judul>\S.*)$",
    re.IGNORECASE,
)


def _judul_eksplisit_dari_teks(user_text: str):
    """Kembalikan judul yang USER sebut sendiri, atau None.

    Menangkap ketiga bentuk yang BENAR-BENAR ada di chat_messages:
      - ", judul UNTUK PAK EKO"                (tanpa titik dua)
      - "\nJudul: Penawaran untuk Toko Melati" (huruf besar + titik dua)
      - ", dengan judul Penawaran Kaos Hitam Gramasi 24s"
    """
    if not user_text:
        return None
    try:
        m = _JUDUL_EKSPLISIT_RE.search(user_text)
    except Exception:
        return None
    if not m:
        return None
    j = (m.group("judul") or "").strip().strip("\"'")
    j = j.rstrip(" .,;:")
    return j[:255] or None


# FIX_AQUA_RELATIVE_DATE 2026-05-19: parse Indonesian relative-date phrases


# Q3B 2026-08-10: bilangan-kata Bahasa Indonesia. Parser lama hanya menerima
# DIGIT (r"(\d+)\s+bulan"), sehingga "jatuh tempo satu bulan" tak dikenali dan
# due_date HALUSINASI dari LLM bertahan sampai DB (dok. 52: 2024-05-23, mundur
# dua tahun). Bilangan kata adalah cara orang Indonesia berbicara — 44 dari 44
# frasa yang diuji gagal sebelum perbaikan ini.
#
# Disengaja: normalisasi kata->digit dilakukan DI DEPAN, sehingga seluruh regex
# yang sudah ada (termasuk jalur _due_offset_days di _apply_relative_dates)
# langsung bekerja tanpa satu pun regex disentuh. Radius perubahan minimum.
_ID_WORD_NUM = {
    "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6,
    "tujuh": 7, "delapan": 8, "sembilan": 9, "sepuluh": 10, "sebelas": 11,
    "duabelas": 12, "dua belas": 12,
}
_ID_UNITS = ("hari", "minggu", "bulan", "tahun")


def _normalize_word_numerals(text: str) -> str:
    """'satu bulan' -> '1 bulan'; 'sebulan' -> '1 bulan'. Idempoten.

    Hanya menyentuh pola <bilangan> <satuan waktu>; angka lain di kalimat
    (qty, harga) tidak tersentuh karena satuannya wajib ikut cocok.
    """
    if not text:
        return text
    out = text
    for unit in _ID_UNITS:
        # bentuk lekat: sebulan / seminggu / sehari / setahun
        out = re.sub(rf"\bse{unit}\b", f"1 {unit}", out)
    for kata, n in sorted(_ID_WORD_NUM.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{kata}\s+({'|'.join(_ID_UNITS)})\b", rf"{n} \1", out)
    return out


def _parse_relative_date_phrase(phrase: str, base):
    """Parse Indonesian relative-date phrase. Return computed date or None."""
    if not phrase:
        return None
    p = _normalize_word_numerals(phrase.lower().strip())
    base_d = base.date() if hasattr(base, "date") else base
    if p in ("hari ini", "sekarang", "today", "tanggal hari ini", "tanggal sekarang"):
        return base_d
    if p in ("besok", "esok", "esok hari", "besok hari"):
        return base_d + timedelta(days=1)
    if p == "lusa":
        return base_d + timedelta(days=2)
    if p == "kemarin":
        return base_d - timedelta(days=1)
    if p in ("minggu depan", "pekan depan", "minggu besok"):
        return base_d + timedelta(days=7)
    if p in ("bulan depan", "bulan besok"):
        return base_d + timedelta(days=30)
    if p in ("akhir minggu", "akhir pekan"):
        delta = (6 - base_d.weekday()) % 7
        return base_d + timedelta(days=delta)
    if p in ("akhir bulan",):
        if base_d.month == 12:
            nxt = base_d.replace(year=base_d.year + 1, month=1, day=1)
        else:
            nxt = base_d.replace(month=base_d.month + 1, day=1)
        return nxt - timedelta(days=1)
    m = re.search(r"(?:jatuh\s+)?tempo\s+(\d+)\s+hari", p)
    if m:
        return base_d + timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s+hari\s+(?:yang\s+)?lalu", p)
    if m:
        return base_d - timedelta(days=int(m.group(1)))
    m = re.search(
        r"(?:dalam\s+)?(\d+)\s+hari(?:\s+(?:lagi|ke\s+depan|kedepan|mendatang))?", p
    )
    if m:
        return base_d + timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s+minggu\s+(?:yang\s+)?lalu", p)
    if m:
        return base_d - timedelta(weeks=int(m.group(1)))
    m = re.search(r"(?:dalam\s+)?(\d+)\s+minggu(?:\s+(?:lagi|ke\s+depan|kedepan))?", p)
    if m:
        return base_d + timedelta(weeks=int(m.group(1)))
    m = re.search(r"(?:dalam\s+)?(\d+)\s+bulan(?:\s+(?:lagi|ke\s+depan|kedepan))?", p)
    if m:
        return base_d + timedelta(days=30 * int(m.group(1)))
    # Q3B: satuan "tahun" sebelumnya TIDAK ADA sama sekali — "1 tahun" pun gagal.
    m = re.search(r"(?:dalam\s+)?(\d+)\s+tahun(?:\s+(?:lagi|ke\s+depan|kedepan))?", p)
    if m:
        return base_d + timedelta(days=365 * int(m.group(1)))
    return None


def _apply_relative_dates(
    payload: Dict[str, Any],
    user_text: str,
    invoice_date_key: str = "invoice_date",
) -> Dict[str, Any]:
    """Post-process payload to override invoice_date / due_date when user_text
    contains Indonesian relative-date phrases. FIX_AQUA_RELATIVE_DATE 2026-05-19.
    """
    if not user_text:
        return payload
    # Q3B: normalisasi di sini juga, supaya _n_hari (pemilih basis + penyimpan
    # _due_offset_days) ikut mengenali "tempo tiga hari", bukan hanya "3 hari".
    txt = _normalize_word_numerals(user_text.lower())
    today = _date_cls.today()

    inv_match = re.search(
        r"(?:per\s+)?tanggal\s+(hari\s+ini|sekarang|besok|lusa|kemarin|esok(?:\s+hari)?)",
        txt,
    )
    if inv_match:
        start = inv_match.start()
        prefix = txt[max(0, start - 15) : start]
        if "tempo" not in prefix:
            computed = _parse_relative_date_phrase(inv_match.group(1), today)
            if computed:
                payload[invoice_date_key] = computed.isoformat()
                logger.info(
                    "[FIX_AQUA_RELATIVE_DATE] %s=%s from phrase=%s",
                    invoice_date_key,
                    computed,
                    inv_match.group(1),
                )

    try:
        inv_base = datetime.strptime(
            payload.get(invoice_date_key) or today.isoformat(), "%Y-%m-%d"
        ).date()
    except (ValueError, TypeError):
        inv_base = today

    due_match = re.search(
        r"(?:jatuh\s+)?tempo\s+([^,\.\n]+?)"
        r"(?=,|\.|$|catatan|pajak|diskon|item|dengan|untuk|harga|qty|kuantitas|jumlah)",
        txt,
    )
    if due_match:
        phrase = due_match.group(1).strip()
        _n_hari = re.match(r"^(\d+)\s+hari\b", phrase)
        if _n_hari:
            base_for_due = inv_base
        else:
            base_for_due = today
        computed = _parse_relative_date_phrase(phrase, base_for_due)
        if computed is None:
            # Q3C: frasa tempo bisa berupa tanggal ABSOLUT ("jatuh tempo 14 juni").
            # Parser absolut sudah ada dan murni — dipakai sebagai lapis kedua
            # supaya membuang due_date LLM (di _enrich_sales_invoice) tidak
            # mengorbankan kasus ini.
            computed = _parse_absolute_date_id(phrase)
        if computed:
            payload["due_date"] = computed.isoformat()
            # FIX_BILL_RELDATE_PERSIST (2026-06-18): when the user states an
            # explicit "jatuh tempo N hari" offset, persist N as a hidden marker
            # on the payload. The workflow deep-merge keeps it across turns, so
            # the enrich step on a LATER turn (e.g. "ya lanjutkan") can re-apply
            # the SAME offset against the issue_date instead of silently falling
            # back to the vendor NET-30 default. Offset-relative only (an
            # absolute "tempo 14 Juni" is already an absolute date, no offset to
            # carry). Metadata only — no amount/journal logic touched.
            if _n_hari:
                try:
                    payload["_due_offset_days"] = int(_n_hari.group(1))
                except (ValueError, TypeError):
                    pass
            logger.info(
                "[FIX_AQUA_RELATIVE_DATE] due_date=%s from phrase=%s base=%s offset=%s",
                computed,
                phrase,
                base_for_due,
                payload.get("_due_offset_days"),
            )
    return payload


# FIX_BILL_ABSDATE_PARSE 2026-06-18: deterministic Indonesian month-name /
# numeric absolute-date parser. Stage-2 LLM has no date parsing and hallucinates
# the YEAR when the user states a bare "15 februari" (e.g. -> 2023/2024). This
# helper resolves an explicit invoice date deterministically so the value is
# correct, not LLM-chosen. Metadata-only (issue_date/invoice_date) — no
# amount/journal logic touched.
_ID_MONTHS = {
    "januari": 1,
    "februari": 2,
    "pebruari": 2,  # common spelling
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "nopember": 11,  # common spelling
    "desember": 12,
    # abbreviations
    "jan": 1,
    "feb": 2,
    "peb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "agu": 8,
    "agt": 8,
    "ags": 8,
    "sep": 9,
    "sept": 9,
    "okt": 10,
    "nov": 11,
    "nop": 11,
    "des": 12,
}
# month-name alternation, longest-first so "september" wins over "sep"
_ID_MONTH_ALT = "|".join(sorted(_ID_MONTHS.keys(), key=len, reverse=True))
# "15 februari" / "15 feb 2026" (optional year)
_ABS_DATE_MONTHNAME_RE = re.compile(
    r"\b(\d{1,2})\s+(" + _ID_MONTH_ALT + r")\b(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
# "15/02/2026" / "15-02-2026" / "15/02" (optional year)
_ABS_DATE_NUMERIC_RE = re.compile(
    r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b",
)
# ISO "2026-02-15"
_ABS_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def _parse_absolute_date_id(text: str):
    """Parse an explicit absolute invoice date out of Indonesian free text.

    Recognized forms (first match wins, ISO -> month-name -> numeric):
      - ISO: 2026-02-15
      - month-name: "15 februari", "15 feb 2026", "tanggal faktur 15 februari"
      - numeric: "15/02/2026", "15-02", "tgl 15/02"

    Year rule: if the user supplies a year, honor it; if NOT, default to the
    CURRENT year (a backdated purchase invoice is normal — do NOT roll forward).

    Returns a datetime.date or None. Pure function, no LLM, no I/O.
    """
    if not text:
        return None
    try:
        cur_year = _date_cls.today().year
        # 1) ISO first (unambiguous)
        m = _ABS_DATE_ISO_RE.search(text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return _date_cls(y, mo, d)
            except ValueError:
                return None
        # 2) Indonesian month-name
        m = _ABS_DATE_MONTHNAME_RE.search(text)
        if m:
            d = int(m.group(1))
            mo = _ID_MONTHS.get(m.group(2).lower())
            y = int(m.group(3)) if m.group(3) else cur_year
            if mo:
                try:
                    return _date_cls(y, mo, d)
                except ValueError:
                    return None
        # 3) numeric DD/MM[/YYYY] (day-first, Indonesian convention)
        m = _ABS_DATE_NUMERIC_RE.search(text)
        if m:
            d = int(m.group(1))
            mo = int(m.group(2))
            if m.group(3):
                y = int(m.group(3))
                if y < 100:  # 2-digit year -> 2000s
                    y += 2000
            else:
                y = cur_year
            try:
                return _date_cls(y, mo, d)
            except ValueError:
                return None
    except Exception as _e:  # never raise from a parser
        logger.debug("[FIX_BILL_ABSDATE_PARSE] parse failed: %s", _e)
        return None
    return None


def _safe_get_name(entity: dict, entity_type: str) -> str:
    """
    Safely extract vendor/customer name from bill/invoice entity.
    Handles nested (entity.vendor.name) and flat (entity.vendor_name) formats.
    Returns empty string on failure — never crashes.
    """
    if not isinstance(entity, dict):
        return ""

    if entity_type == "bill":
        search_keys = [
            ("vendor", "name"),
            ("vendor_name", None),
            ("vendor", "display_name"),
        ]
    elif entity_type == "invoice":
        search_keys = [
            ("customer", "name"),
            ("customer_name", None),
            ("customer", "display_name"),
        ]
    else:
        return ""

    for outer_key, inner_key in search_keys:
        val = entity.get(outer_key)
        if val is None:
            continue
        if inner_key is None:
            if isinstance(val, str) and val.strip():
                return val.strip()
        elif isinstance(val, dict):
            name = val.get(inner_key, "")
            if isinstance(name, str) and name.strip():
                return name.strip()
        elif isinstance(val, str) and val.strip():
            return val.strip()

    entity_keys = list(entity.keys())[:10]
    logger.warning(f"[MATCH] Could not extract {entity_type} name. Keys: {entity_keys}")
    return ""


def _safe_get_id(entity: dict, entity_type: str) -> str:
    """
    Safely extract vendor/customer ID from bill/invoice entity.
    Handles nested (entity.vendor.id) and flat (entity.vendor_id) formats.
    """
    if not isinstance(entity, dict):
        return ""

    if entity_type == "bill":
        search_keys = [("vendor", "id"), ("vendor_id", None)]
    elif entity_type == "invoice":
        search_keys = [("customer", "id"), ("customer_id", None)]
    else:
        return ""

    for outer_key, inner_key in search_keys:
        val = entity.get(outer_key)
        if val is None:
            continue
        if inner_key is None:
            return str(val) if val else ""
        elif isinstance(val, dict):
            inner_val = val.get(inner_key)
            if inner_val:
                return str(inner_val)

    return ""


def find_allocation_options(matches: list[dict], transfer_amount: "Decimal") -> dict:
    """
    Find how to allocate transfer_amount across matching invoices/bills.
    Capped at 5 items to avoid exponential blowup.
    Pure function, no side effects.
    """
    # Exact match on 1 item -> simple
    for m in matches:
        if _to_amount(m.get("amount_due", 0)) == transfer_amount:
            return {"type": "single", "allocation": [m]}

    # Combo match (capped at 5 items max)
    capped = matches[:5]
    for r in range(2, len(capped) + 1):
        for combo in combinations(capped, r):
            if (
                sum(_to_amount(m.get("amount_due", 0)) for m in combo)
                == transfer_amount
            ):
                return {"type": "multi", "allocation": list(combo)}

    # No exact combo -> return options for user to pick
    return {"type": "needs_user_input", "options": capped}


from .tool_registry import (  # noqa: E402
    get_endpoint_for_tool,
    is_session_tool,
    is_valid_tool,
    ACTION_TYPE_MAP,
    is_tutorial_tool,
)
from .direct_action_registry import (  # noqa: E402
    get_direct_action,
    validate_payload,
    apply_defaults,
    build_confirmation_table,
    build_review_card_payload,
    build_ux_metadata,
    get_query_action,
    QueryActionConfig,
    ChartQueryConfig,
)
from .retry_controller import execute_with_retry  # noqa: E402
from .tool_metadata import get_tool_metadata  # noqa: E402
from .correlation import TurnContext  # noqa: E402
from .tutorial_registry import (  # noqa: E402
    get_tutorial,
    get_tutorial_step,
    list_available_tutorials,
)
from .tutorial_progress import (  # noqa: E402
    get_progress,
    upsert_progress,
    advance_tutorial as advance_tutorial_step,
    dismiss_tutorial as dismiss_tutorial_progress,
)

logger = logging.getLogger("unified_agent.tool_executor")
# ─── Phase 2C: Tool Response Cache ───────────────────────────────────────────
import time as _cache_time  # noqa: E402

TOOL_CACHE_TTL = 300  # 5 minutes
_cache_logger = __import__("logging").getLogger("unified_agent.cache")
CACHEABLE_TOOLS = frozenset(
    {
        "get_chart_of_accounts",
        "get_bank_accounts",
        "get_accounting_periods",
        "search_customers",
        "search_vendors",
        "search_items",
    }
)

# Per-request in-memory cache (lives for duration of one agent turn)
# This is sufficient because:
# 1. Within a single turn, the same tool may be called multiple times
# 2. Between turns, data may change so we shouldn't cache
# 3. Avoids DB complexity of session-backed cache
_turn_cache: dict = {}


def _cache_key(tool_name: str, params: dict) -> str:
    """Generate cache key from tool name + sorted params."""
    import json as _cj

    return f"{tool_name}:{_cj.dumps(params, sort_keys=True, default=str)}"


def get_from_cache(tool_name: str, params: dict) -> dict | None:
    """Check turn cache for cached result."""
    if tool_name not in CACHEABLE_TOOLS:
        return None
    key = _cache_key(tool_name, params)
    entry = _turn_cache.get(key)
    if entry and (_cache_time.time() - entry["ts"]) < TOOL_CACHE_TTL:
        _cache_logger.warning("[Phase3-Cache] HIT tool=%s", tool_name)
        return entry["result"]
    return None


def set_in_cache(tool_name: str, params: dict, result: dict):
    """Store result in turn cache."""
    if tool_name not in CACHEABLE_TOOLS:
        return
    key = _cache_key(tool_name, params)
    _turn_cache[key] = {"ts": _cache_time.time(), "result": result}
    _cache_logger.warning("[Phase3-Cache] SET tool=%s key=%s", tool_name, key[:60])


def clear_turn_cache():
    """Clear the per-turn cache. Call at start of each new turn."""
    _turn_cache.clear()


logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)

# --- Constants ---

MAX_TOOL_CALLS_PER_REQUEST = 12
READ_TOOL_TIMEOUT = 5.0
ACTION_TOOL_TIMEOUT = 10.0
MAX_RESPONSE_SIZE = 8000
MAX_LIST_ITEMS = 30
MAX_STRING_LENGTH = 500
MAX_AMOUNT = 999_999_999_999
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

KERNEL_BASE_URL = "http://localhost:8000"

# Map action_type to category string (for gRPC clients)
ACTION_CATEGORY_MAP = {
    "CREATE_CUSTOMER": "MASTER_DATA",
    "CREATE_VENDOR": "MASTER_DATA",
    "CREATE_PRODUCT": "MASTER_DATA",
    "CREATE_SALES_INVOICE": "DOCUMENT",
    "CREATE_PURCHASE_INVOICE": "DOCUMENT",
    "CREATE_EXPENSE": "DOCUMENT",
    "CREATE_CREDIT_NOTE": "DOCUMENT",
    "CREATE_PURCHASE_ORDER": "DOCUMENT",
    "RECEIVE_PAYMENT": "PAYMENT",
    "MAKE_PAYMENT": "PAYMENT",
    "BANK_TRANSFER": "PAYMENT",
    "POST_GENERAL_JOURNAL": "ACCOUNTING",
    "REVERSE_JOURNAL": "ACCOUNTING",
    "CLOSE_PERIOD": "ACCOUNTING",
    "REOPEN_PERIOD": "ACCOUNTING",
}

# --- Enrichment Registry ---
# Maps action_type -> method name for enrichment.
# None = no enrichment needed (master data, simple actions).
ACTION_ENRICHMENT = {
    # Keys MUST match DirectActionConfig.action_type_key in direct_action_registry.
    "CREATE_SALES_INVOICE": "_enrich_sales_invoice",
    "CREATE_BILL": "_enrich_purchase_invoice",
    "CREATE_EXPENSE": "_enrich_expense",
    "CREATE_CREDIT_NOTE": "_enrich_credit_note",
    "CREATE_QUOTE": "_enrich_quote",
    "CREATE_SALES_ORDER": "_enrich_sales_order",
    "CREATE_RECEIVE_PAYMENT": "_enrich_receive_payment",
    "CREATE_BILL_PAYMENT": "_enrich_make_payment",
    "CREATE_VENDOR_CREDIT": "_enrich_vendor_credit",
    "CREATE_BANK_TRANSFER": "_enrich_transfer",
    "CREATE_JOURNAL_ENTRY": "_enrich_journal",
    # Legacy aliases (kept for propose_action tool path which uses older names)
    "CREATE_PURCHASE_INVOICE": "_enrich_purchase_invoice",
    "CREATE_PURCHASE_ORDER": "_enrich_purchase_order",
    "RECEIVE_PAYMENT": "_enrich_receive_payment",
    "MAKE_PAYMENT": "_enrich_make_payment",
    "BANK_TRANSFER": "_enrich_transfer",
    "POST_GENERAL_JOURNAL": "_enrich_journal",
    # Master data / simple actions — no enrichment
    "CREATE_CUSTOMER": None,
    "CREATE_VENDOR": None,
    "CREATE_PRODUCT": None,
    "REVERSE_JOURNAL": None,
    "CLOSE_PERIOD": None,
    "REOPEN_PERIOD": None,
}


def _amankan_nomor_dokumen(payload: dict, action_key: str) -> None:
    """H 2026-08-11: jaga agar NOMOR DOKUMEN tetap milik generator.

    bills_service:2553 melewati generate_purchase_bill_number() begitu
    invoice_number terisi apa pun. Jadi satu kalimat yang mendarat di sana
    MENGGANTI nomor resmi dokumen: owner melihat "No. Faktur = kaos hitam dari
    pt benang emas" di dashboard, dan PDF ke vendor tercetak dengan judul itu.

    Akarnya (label FieldSpec yang meminta "No. Faktur Vendor") sudah diperbaiki
    di commit yang sama, dan itu menurunkan kejadian dari 3/4 run menjadi 0/4.
    Pagar ini tetap dipasang karena perilaku model tidak stabil sepanjang waktu:
    label mengurangi PELUANG, pagar menghilangkan KONSEKUENSI.

    ⚠️ bill_number IKUT DITANGANI, dan itu BUKAN kode mati.
    Ia terisi kalimat pada 4 dari 4 run, di kedua jalur, dan hari ini tidak
    merusak apa pun HANYA karena namanya tidak cocok dengan skema endpoint
    (BillCreateV2 tak punya field itu, jadi Pydantic membuangnya). Begitu ada
    yang menambahkan aliases=["bill_number"] ke FieldSpec invoice_number —
    perubahan yang tampak rapi dan tak berbahaya — bug ini bangun seketika.
    JANGAN hapus cabang ini dengan alasan "bill_number toh tak dipakai".

    Ambang sengaja LONGGAR ke arah aman (disiplin Q3c): kalau tidak yakin
    sebuah nilai adalah nomor, ia TIDAK dibuang dan TIDAK dipakai sebagai nomor
    dokumen — ia disimpan ke ref_no. Salah menebak jadi murah karena taruhannya
    bukan lagi identitas dokumen.
    """
    if action_key not in ("create_bill", "create_purchase_invoice"):
        return

    _vendor = str(payload.get("vendor_name") or "").strip().lower()

    def _kalimat(nilai: str) -> bool:
        """Jelas bukan nomor: kalimat, bukan identitas dokumen."""
        n = nilai.strip()
        if n.count(" ") > 1 or len(n) > 40:
            return True
        return bool(_vendor) and len(_vendor) >= 4 and _vendor in n.lower()

    def _simpan_ke_ref(nilai: str) -> None:
        if not payload.get("ref_no"):
            payload["ref_no"] = nilai

    # bill_number: nama yang tak dikenal endpoint (lihat docstring — ini BUKAN
    # kode mati). Nilainya diselamatkan ke ref_no bila ia tampak seperti nomor,
    # lalu kuncinya dibuang supaya tak ada yang mewariskannya.
    _bn = payload.pop("bill_number", None)
    if isinstance(_bn, str) and _bn.strip():
        if not _kalimat(_bn):
            _simpan_ke_ref(_bn.strip())
        else:
            logger.info(
                "[FIX_NOMOR_DOKUMEN] bill_number dibuang (kalimat): %r", _bn[:60]
            )

    # invoice_number SELALU dikosongkan pada pembuatan dokumen baru — bukan
    # hanya ketika isinya "mencurigakan".
    #
    # Gate H3 membuktikan ambang-kecurigaan menjawab PERTANYAAN YANG SALAH:
    # "INV/BE/2026/0812" adalah nomor vendor yang SAH, lolos semua ambang, dan
    # tetap menimpa invoice_number -> generator PB- dilewati -> nomor dokumen
    # internal menjadi nomor milik vendor. Sah atau tidaknya nilai itu TIDAK
    # relevan; yang relevan adalah invoice_number bukan milik siapa pun selain
    # generator, dan dokumen yang sedang dibuat belum punya nomor untuk dirujuk.
    #
    # Jadi: nilai yang tampak seperti nomor diselamatkan ke ref_no (tempatnya
    # yang benar), nilai yang jelas kalimat dibuang. Dua-duanya keluar dari
    # invoice_number. Disiplin Q3c: pada field bertaruhan tinggi, nilai dari
    # model tidak dipakai — ia dipindahkan ke tempat yang taruhannya rendah.
    _inv = payload.pop("invoice_number", None)
    if isinstance(_inv, str) and _inv.strip():
        if _kalimat(_inv):
            logger.warning(
                "[FIX_NOMOR_DOKUMEN] invoice_number dibuang dari nomor dokumen "
                "(kalimat), disimpan sebagai ref_no: %r",
                _inv[:60],
            )
            _simpan_ke_ref(_inv.strip())
        else:
            logger.info(
                "[FIX_NOMOR_DOKUMEN] invoice_number -> ref_no (nomor vendor, "
                "bukan nomor internal): %r",
                _inv[:60],
            )
            _simpan_ke_ref(_inv.strip())

def _t181_urai_items(payload: dict) -> None:
    """T181 FASE 1 (addendum owner 2026-08-30): urai `items` + gerbang TOLAK.

    RUMUSAN GERBANG (bukan "exception terjadi"):
        ADA teks daftar mentah, DAN kita berakhir TANPA baris.

    Sebabnya: `json.loads` yang SUKSES tapi menghasilkan non-list (mis. dict)
    berakhir persis sama seperti JSONDecodeError -- `items=[]`, lalu jalur
    skalar mengarang satu baris dari slot Stage-1 dan barang kedua menguap.
    Yang dilihat pengguna IDENTIK pada kedua cabang, jadi membedakannya di
    kode berarti membedakan dua hal yang tidak berbeda baginya.

    Keadaan (A) -- `items` TIDAK PERNAH ADA sebagai teks -- tak tersentuh:
    sentinel tak pernah disetel, jalur mengarang tetap SAH dan tetap jalan.
    """
    _raw_items = payload.get("items")
    if not isinstance(_raw_items, str):
        return
    try:
        _parsed = json.loads(_raw_items)
        payload["items"] = _parsed if isinstance(_parsed, list) else []
    except (ValueError, TypeError):
        payload["items"] = []
    if _raw_items.strip() and not payload.get("items"):
        payload["_t181_items_mentah"] = _raw_items


def _t181_pesan_tolak(mentah) -> str:
    """T181 FASE 1: kegagalan parse `items` bersuara, bukan mengarang.

    Teks yang dikutip adalah teks pengguna sendiri, dikembalikan kepadanya —
    BUKAN ditulis ke log (itu sebabnya [T181_PUING] dicabut).
    """
    _kutip = str(mentah)[:400]
    return (
        "\u26a0\ufe0f Daftar barang di pesan ini tidak bisa saya urai, jadi "
        "saya TIDAK membuat kartunya \u2014 daripada mengarang isinya dan "
        "menghilangkan barang yang lain.\n\n"
        "Yang tidak terbaca:\n\u00ab" + _kutip + "\u00bb\n\n"
        "Coba ketik ulang satu barang per baris \u2014 nama, jumlah, lalu "
        "harga satuan. Contoh:\n"
        "1. Kain Katun 10 meter @ 40000\n"
        "2. Benang Jahit 5 pcs @ 50000"
    )


class TenantContext:
    """Tenant context passed to tool executor."""

    def __init__(
        self, tenant_id: str, user_id: str, auth_token: str, tenant_name: str = ""
    ):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.auth_token = auth_token
        self.tenant_name = tenant_name


def _parse_idr_amount(text: str) -> float:
    """Best-effort parse a Rupiah amount from free text; 0.0 if none.
    Handles 'Rp 275.000', '275.000', '275 ribu', '1,5 juta', '80rb', '80k'.
    Indonesian convention: '.' = thousands, ',' = decimal."""
    if not text:
        return 0.0
    t = text.lower()
    m = re.search(r"(?:rp\s*)?(\d+(?:[.,]\d+)?)\s*(juta|jt|ribu|rb|k)\b", t)
    if m:
        try:
            n = float(m.group(1).replace(".", "").replace(",", "."))
        except Exception:
            n = 0.0
        suf = m.group(2)
        if suf in ("juta", "jt"):
            return n * 1_000_000
        if suf in ("ribu", "rb", "k"):
            return n * 1000
    m = re.search(r"rp\s*([\d][\d.]*\d|\d)", t)
    if not m:
        m = re.search(r"\b(\d{1,3}(?:\.\d{3})+)\b", t)
    if m:
        digits = re.sub(r"[^\d]", "", m.group(1))
        if digits:
            try:
                return float(digits)
            except Exception:
                return 0.0
    return 0.0


def _normalize_payment_method(value) -> str:
    """FIX_DOGFOOD_PAYMETHOD_NORMALIZE (2026-06-09): coerce any incoming
    payment_method to the receive_payments enum {'cash', 'bank_transfer'}.

    The DB CHECK chk_rcv_payment_method + Pydantic Literal only accept those
    two values; the LLM frequently leaks the raw Indonesian phrase ("transfer
    bank", "tunai", "lewat transfer") which fails at POST. A bank account was
    chosen on the card, so default to bank_transfer when ambiguous.
    """
    if value is None:
        return "bank_transfer"
    v = str(value).strip().lower()
    if v in ("cash", "bank_transfer"):
        return v
    # cash-on-hand synonyms
    if "tunai" in v or "cash" in v or v == "kas":
        return "cash"
    # bank / non-cash instrument synonyms
    _bank_tokens = (
        "transfer",
        "bank",
        "tf",
        "giro",
        "rtgs",
        "qris",
        "va",
        "virtual account",
        "debit",
        "kartu",
        "ewallet",
        "e-wallet",
    )
    if any(_tok in v for _tok in _bank_tokens):
        return "bank_transfer"
    # default: a bank account was chosen on the card
    return "bank_transfer"


def _dasar_jatuh_tempo(hari_ini: str, days: int) -> str:
    """Jatuh tempo = tanggal dokumen + N hari.

    K0: `hari_ini` datang dari pemanggil yang sudah menghitungnya menurut zona
    TENANT. Kalau ia tak dioper, kita jatuh ke zona server dan MENGATAKANNYA —
    diam di sini berarti jatuh tempo bergeser sehari tanpa jejak.
    """
    from datetime import date as _d

    if hari_ini:
        try:
            return (_d.fromisoformat(hari_ini) + timedelta(days=days)).isoformat()
        except (ValueError, TypeError):
            pass
    logger.warning(
        "[K0_ZONA] jatuh tempo dihitung tanpa zona tenant (hari_ini=%r)", hari_ini
    )
    return (_d.today() + timedelta(days=days)).isoformat()


class ToolExecutor:
    """
    Executes tools called by the unified agent.
    Routes: read tools -> httpx, action tools -> gRPC.
    """

    def __init__(
        self,
        context: TenantContext,
        session_manager=None,
        session_id: str = None,
        user_text: str = "",
    ):
        self.context = context
        self.session_manager = session_manager
        self.session_id = session_id
        self.user_text = user_text  # Original user message (may contain file_ref)
        self.call_count = 0
        self.propose_count = 0
        self._validator_client = None
        self._executor_client = None

    @staticmethod
    def _truncate(text: str, max_len: int = 15) -> str:
        """Truncate text for chart labels."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    async def _hari_ini_date(self):
        """Hari ini menurut zona waktu TENANT, bukan zona server.

        K0 2026-08-12: seluruh lapisan berjalan UTC, jadi kartu yang dibuat
        00.00-08.00 WITA mengusulkan tanggal KEMARIN — dan itu jam kerja bagi
        pemilik toko yang membukukan setelah tutup, bukan kasus tepi.

        Hanya untuk TANGGAL DOKUMEN. Cap waktu sistem tetap UTC.
        """
        from .db_utils import get_session_db_pool  # noqa: E402
        from ...utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

        pool = await get_session_db_pool()
        async with pool.acquire() as conn:
            return await tanggal_dokumen(conn, self.context.tenant_id)

    async def _hari_ini(self) -> str:
        return (await self._hari_ini_date()).isoformat()

    @property
    def validator_client(self):
        if self._validator_client is None:
            from ..action_validator_client import get_action_validator_client  # noqa: E402

            self._validator_client = get_action_validator_client()
        return self._validator_client

    @property
    def executor_client(self):
        if self._executor_client is None:
            from ..action_executor_client import get_action_executor_client  # noqa: E402

            self._executor_client = get_action_executor_client()
        return self._executor_client

    async def execute(
        self, tool_name: str, params: Dict[str, Any], turn_ctx: "TurnContext" = None
    ) -> Dict[str, Any]:
        """Execute a tool call with automatic retry handling (H4).

        Wraps _execute_once() with retry logic from RetryController.
        - Idempotent tools (reads): auto-retry up to max_retries
        - Non-idempotent tools (propose_action): retry with verify-first
        - Non-retryable errors (400, 401, 409): immediate abort
        """
        # Phase 2C: Check tool cache first
        _cached = get_from_cache(tool_name, params)
        if _cached is not None:
            logger.info(f"[TOOL_CACHE] HIT tool={tool_name}")
            return _cached

        self.call_count += 1
        if self.call_count > MAX_TOOL_CALLS_PER_REQUEST:
            return _error("BUDGET_EXCEEDED", "Batas tool call tercapai.")

        if not is_valid_tool(tool_name):
            return _error("UNKNOWN_TOOL", f"Tool {tool_name!r} tidak ditemukan.")

        _tool_meta = get_tool_metadata(tool_name)

        # --- Observability: create tool call context if turn_ctx provided ---
        tool_call_ctx = None
        try:
            if turn_ctx:
                tool_call_ctx = turn_ctx.new_tool_call(tool_name, retry_attempt=0)
        except Exception:
            pass

        # Determine action_type for propose_action (needed by retry controller)
        action_type = None
        if tool_name == "propose_action":
            action_type = params.get("action_type")

        # Wrap execution with retry logic
        result = await execute_with_retry(
            tool_name=tool_name,
            execute_fn=lambda **kw: self._execute_once(tool_name, kw),
            args=params,
            action_type=action_type,
        )

        # --- Observability: complete tool call context ---
        try:
            if tool_call_ctx:
                tc_status = "success" if result.get("success") else "failed"
                tc_error = (
                    result.get("error_type") if not result.get("success") else None
                )
                tool_call_ctx.complete(tc_status, error_type=tc_error)
                logger.info(
                    f"[TOOL_CALL] tool={tool_name} call_id={tool_call_ctx.tool_call_id} status={tc_status} latency={tool_call_ctx.latency_ms}ms"
                )
        except Exception:
            pass

        # Phase 2C: Cache successful results
        if result.get("success"):
            set_in_cache(tool_name, params, result)

        return result

    async def _execute_once(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single tool call attempt. Routes to appropriate handler."""
        try:
            if is_session_tool(tool_name):
                return await self._execute_session_tool(tool_name, params)
            elif tool_name == "propose_direct_action":
                return await self._execute_propose_direct(params)
            elif tool_name == "update_document_context":
                return await self._execute_update_document_context(params)
            elif tool_name == "execute_query":
                return await self._execute_query(params)
            elif tool_name == "propose_action":
                return await self._execute_propose(params)
            elif tool_name == "simulate_action":
                return await self._execute_simulate(params)
            elif tool_name == "get_customer_invoices":
                return await self._execute_get_customer_invoices(params)
            elif tool_name == "get_vendor_bills":
                return await self._execute_get_vendor_bills(params)
            elif is_tutorial_tool(tool_name):
                return await self._execute_tutorial_tool(tool_name, params)
            elif tool_name == "search_userguide":
                return await self._execute_search_userguide(params)
            else:
                return await self._execute_read(tool_name, params)
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": f"Tool {tool_name!r} timeout.",
                "error_type": "timeout",
                "status_code": None,
            }
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Tool {tool_name!r} connection refused.",
                "error_type": "connection_refused",
                "status_code": None,
            }
        except Exception as e:
            logger.exception(f"Tool execution error: {tool_name}")
            return _error("INTERNAL_ERROR", f"Error: {str(e)[:200]}")

    # --- Direct Action Execution ---

    async def _resolve_entity_names(self, action_key: str, payload: dict):
        """Resolve display names (vendor_name, customer_name etc.) from IDs when LLM omits them."""
        try:
            from .db_utils import get_session_db_pool

            pool = await get_session_db_pool()
            tenant_id = self.context.tenant_id
        except Exception as e:
            logger.warning(f"[resolve_entity_names] pool init failed: {e}")
            return

        # Vendor name (vendors.id = uuid)
        if (
            action_key == "create_bill_payment"
            and not payload.get("vendor_name")
            and payload.get("vendor_id")
        ):
            try:
                row = await pool.fetchrow(
                    "SELECT name FROM vendors WHERE id = $1::uuid AND tenant_id = $2",
                    str(payload["vendor_id"]),
                    tenant_id,
                )
                if row:
                    payload["vendor_name"] = row["name"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] vendor lookup: {e}")
            # Also resolve bill_number
            if not payload.get("bill_number") and payload.get("bill_id"):
                try:
                    brow = await pool.fetchrow(
                        "SELECT invoice_number, vendor_name FROM bills WHERE id = $1::uuid AND tenant_id = $2",
                        str(payload["bill_id"]),
                        tenant_id,
                    )
                    if brow:
                        payload.setdefault("bill_number", brow["invoice_number"])
                        payload.setdefault("vendor_name", brow["vendor_name"])
                except Exception as e:
                    logger.warning(f"[resolve_entity_names] bill lookup: {e}")

        # Customer name. customers.id = UUID (terverifikasi [SQL] 2026-08-09).
        # uuid: customers.id / sales_invoices.customer_id / receive_payments.customer_id.
        # varchar: credit_notes.customer_id / customer_deposits.customer_id.
        # Jangan menyamaratakan ke arah mana pun.
        if (
            action_key == "create_receive_payment"
            and not payload.get("customer_name")
            and payload.get("customer_id")
        ):
            try:
                row = await pool.fetchrow(
                    "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2",
                    str(payload["customer_id"]),
                    tenant_id,
                )
                if row:
                    payload["customer_name"] = row["nama"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] customer lookup: {e}")

        # Bank account name (bank_accounts.id = uuid)
        if not payload.get("bank_account_name") and payload.get("bank_account_id"):
            try:
                row = await pool.fetchrow(
                    "SELECT account_name FROM bank_accounts WHERE id = $1::uuid AND tenant_id = $2",
                    str(payload["bank_account_id"]),
                    tenant_id,
                )
                if row:
                    payload["bank_account_name"] = row["account_name"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] bank lookup: {e}")

    # --- Tutorial Tool Execution ---

    async def _execute_tutorial_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a tutorial tool (DB-backed, no session_manager needed)."""
        from .db_utils import get_session_db_pool  # noqa: E402

        pool = await get_session_db_pool()
        user_id = self.context.user_id
        tenant_id = self.context.tenant_id

        async with pool.acquire() as conn:
            if tool_name == "list_tutorials":
                tutorials = list_available_tutorials()
                return {"success": True, "tutorials": tutorials}

            elif tool_name == "get_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                config = get_tutorial(tutorial_key)
                if not config:
                    return _error("NOT_FOUND", f"Tutorial '{tutorial_key}' not found")
                progress = await get_progress(conn, user_id, tutorial_key)
                current = progress.current_step if progress else 0
                steps_data = []
                for step in config.steps:
                    steps_data.append(
                        {
                            "step_key": step.step_key,
                            "step_index": step.step_index,
                            "linked_action": step.linked_action,
                            "completion_trigger": step.completion_trigger,
                            "skippable": step.skippable,
                        }
                    )
                return {
                    "success": True,
                    "tutorial_key": config.tutorial_key,
                    "display_key": config.display_key,
                    "total_steps": config.total_steps,
                    "current_step": current,
                    "status": progress.status if progress else "not_started",
                    "steps": steps_data,
                }

            elif tool_name == "start_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                config = get_tutorial(tutorial_key)
                if not config:
                    return _error("NOT_FOUND", f"Tutorial '{tutorial_key}' not found")
                progress = await upsert_progress(
                    conn,
                    user_id,
                    tenant_id,
                    tutorial_key,
                    current_step=1,
                    status="active",
                )
                first_step = config.steps[0] if config.steps else None
                return {
                    "success": True,
                    "message_type": "TUTORIAL_STEP",
                    "status": "started",
                    "tutorial_key": tutorial_key,
                    "current_step": 1,
                    "total_steps": config.total_steps,
                    "step": {
                        "step_key": first_step.step_key,
                        "linked_action": first_step.linked_action,
                        "completion_trigger": first_step.completion_trigger,
                        "skippable": first_step.skippable,
                    }
                    if first_step
                    else None,
                }

            elif tool_name == "advance_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                next_step = await advance_tutorial_step(
                    conn, user_id, tenant_id, tutorial_key
                )
                if next_step is None:
                    return {
                        "success": True,
                        "status": "completed",
                        "tutorial_key": tutorial_key,
                    }
                config = get_tutorial(tutorial_key)
                step = get_tutorial_step(tutorial_key, next_step)
                return {
                    "success": True,
                    "message_type": "TUTORIAL_STEP",
                    "status": "advanced",
                    "tutorial_key": tutorial_key,
                    "current_step": next_step,
                    "total_steps": config.total_steps if config else 0,
                    "step": {
                        "step_key": step.step_key,
                        "linked_action": step.linked_action,
                        "completion_trigger": step.completion_trigger,
                    }
                    if step
                    else None,
                }

            elif tool_name == "dismiss_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                await dismiss_tutorial_progress(conn, user_id, tenant_id, tutorial_key)
                return {
                    "success": True,
                    "status": "dismissed",
                    "tutorial_key": tutorial_key,
                }

            return _error(
                "UNKNOWN_TUTORIAL_TOOL", f"Tutorial tool {tool_name!r} tidak dikenali."
            )

    async def _run_pre_flight_checks(self, config, payload: dict) -> dict:
        """Run pre-flight checks before proposing action to user.

        Returns: {"blocked": False} if all pass or no checks.
                 {"blocked": True, "message": "...", "alternatives": [...]} if fail.
        """
        if not hasattr(config, "pre_flight_checks") or not config.pre_flight_checks:
            return {"blocked": False}

        for check in config.pre_flight_checks:
            try:
                endpoint = check.endpoint.format(**payload)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.context.auth_token}",
                    }
                    resp = await client.get(
                        f"http://localhost:8000{endpoint}",
                        headers=headers,
                    )
                    if resp.status_code >= 400:
                        continue  # skip failed checks
                    result = resp.json()

                if not result.get("can_proceed", True):
                    msg = check.fail_message_template
                    try:
                        msg = msg.format(**result)
                    except (KeyError, IndexError):
                        pass

                    if check.fail_action == "reject":
                        return {"blocked": True, "message": msg}
                    elif check.fail_action == "suggest_alternative":
                        return {
                            "blocked": True,
                            "message": msg,
                            "alternatives": check.alternatives,
                        }
                    elif check.fail_action == "warn":
                        return {"blocked": False, "warning": msg}
            except Exception as e:
                logger.warning(f"Pre-flight check failed for {config.action_key}: {e}")

        return {"blocked": False}

    async def _get_journal_preview(self, config, payload: dict) -> dict | None:
        """Hit preview endpoint to get journal impact without posting.
        Returns None if config has no journal_preview_endpoint.
        Non-fatal: preview failure → continue without preview.
        """
        if (
            not hasattr(config, "journal_preview_endpoint")
            or not config.journal_preview_endpoint
        ):
            return None

        try:
            endpoint = config.journal_preview_endpoint.format(**payload)
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                resp = await client.post(
                    f"http://localhost:8000{endpoint}",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        f"Journal preview HTTP {resp.status_code} for {config.action_key}"
                    )
                    return None
                data = resp.json()
                # Dulu di sini: `return data["journal_lines"]` — yang membuang
                # SELURUH field saudara, termasuk `warnings` yang disusun endpoint
                # pratinjau (pelanggan tak ada, stok minus, periode tertutup, WAC 0).
                # Akibatnya delapan pemeriksaan nyata nol sampai ke user.
                # Kini dict dikembalikan utuh; PEMANGGIL yang memisahkannya, supaya
                # dua konsumen lama (confirmation_table, response_data) tetap
                # menerima bentuk daftar yang sama persis seperti sebelumnya.
                return data
        except Exception as e:
            logger.warning(f"Journal preview failed for {config.action_key}: {e}")
            return None

    def _normalize_payload(self, action_key: str, payload: dict) -> dict:
        """
        Generic field normalization based on FieldSpec.aliases.

        GPT-4o-mini sends "logical" but wrong field names. Instead of
        manual if-blocks per action_key, aliases are declared in FieldSpec.

        Supported patterns:
        - Simple rename: aliases=["payment_account_id"] -> name="bank_account_id"
        - Array extraction: aliases=["EXTRACT:allocations.bill_id"] -> name="bill_id"

        To add normalization for new module: just add aliases to FieldSpec in registry.
        No need to edit this file.
        """
        from .direct_action_registry import DIRECT_ACTIONS

        # Coerce phone-like fields first (FIX_PHONE_COERCE 2026-05-09).
        try:
            payload = _coerce_phone_fields(
                payload, getattr(self, "_last_ocr_text", None)
            )
        except Exception as _pc_err:
            logger.warning("[FIX_PHONE_COERCE] coerce failed: %s", _pc_err)

        config = DIRECT_ACTIONS.get(action_key)
        if not config or not config.fields:
            return payload

        for field_spec in config.fields:
            if not hasattr(field_spec, "aliases") or not field_spec.aliases:
                continue
            # Skip if canonical name already present
            if field_spec.name in payload:
                continue

            for alias in field_spec.aliases:
                if alias.startswith("EXTRACT:"):
                    # Array extraction: "EXTRACT:allocations.bill_id"
                    parts = alias[len("EXTRACT:") :].split(".", 1)
                    if len(parts) == 2:
                        array_field, nested_key = parts
                        items = payload.get(array_field, [])
                        if items and isinstance(items, list) and len(items) > 0:
                            first = items[0] if isinstance(items[0], dict) else {}
                            if nested_key in first:
                                payload[field_spec.name] = first[nested_key]
                                break
                elif alias in payload:
                    # Simple rename
                    payload[field_spec.name] = payload.pop(alias)
                    break

        # Indonesian label -> API value mapping (for user-facing options in registry)
        _LABEL_TO_API = {
            "item_type": {
                "persediaan": "goods",
                "jasa": "service",
                "non-persediaan": "non_inventory",
                "barang": "goods",
                "goods": "goods",
                "service": "service",
                "non_inventory": "non_inventory",
            },
        }
        for field_name, mapping in _LABEL_TO_API.items():
            if field_name in payload and isinstance(payload[field_name], str):
                mapped = mapping.get(payload[field_name].lower().strip())
                if mapped:
                    payload[field_name] = mapped

        # FIX_AQUA_PERCENT_NORMALIZE 2026-05-11: defensive rescale for percent fields emitted as fraction.
        # Stage 2 LLM sometimes emits 0.11 instead of 11 despite prompt rule. Rescale 0<v<1 -> v*100.
        # Trade-off: rare sub-1% rates (e.g., 0.5%) would be wrongly upscaled. In Indonesian SME accounting,
        # this case is exceedingly rare (PPN 11%, PPh integer rates, diskon biasanya integer).
        # Log warning so we can detect false-positives in telemetry.
        try:
            for _fs in config.fields:
                if getattr(_fs, "field_type", None) == "percent":
                    _v = payload.get(_fs.name)
                    if isinstance(_v, bool):
                        continue
                    if isinstance(_v, (int, float)) and 0 < _v < 1:
                        _old = _v
                        payload[_fs.name] = _v * 100
                        logger.warning(
                            "[FIX_AQUA_PERCENT_NORMALIZE] action=%s field=%s rescaled %s -> %s",
                            action_key,
                            _fs.name,
                            _old,
                            payload[_fs.name],
                        )
        except Exception as _e:
            logger.debug("[FIX_AQUA_PERCENT_NORMALIZE] skip: %s", _e)

        return payload

    async def _execute_search_userguide(self, params: dict) -> dict:
        """
        Phase 2B-1 — invoke userguide RAG retrieval.

        Resolves caller's effective permissions via PolicyEngine, then calls
        services.userguide_search.search() with SQL-level permission filter.
        Owner role bypasses the permission filter.

        Returns the LLM-friendly dict shape per BRAINSTORM "Return Format".
        """
        from .db_utils import get_session_db_pool  # noqa: E402
        from ..userguide_search import search as _ug_search  # noqa: E402

        query = (params.get("query") or "").strip()
        if not query:
            return _error("BAD_REQUEST", "query kosong.")

        max_results = params.get("max_results")
        tier_pref = params.get("tier_preference") or "auto"
        if tier_pref == "auto":
            tier_pref = None

        tenant_id = self.context.tenant_id
        user_id = self.context.user_id

        try:
            pool = await get_session_db_pool()
        except Exception as e:
            logger.exception("[search_userguide] pool init failed")
            return _error("INTERNAL_ERROR", f"db pool: {str(e)[:120]}")

        # Resolve effective permissions via PolicyEngine.
        is_owner = False
        allowed_modules: list[str] = []
        actions_for_module: dict[str, list[str]] = {}
        try:
            from ..policy_engine_client import get_policy_engine  # noqa: E402

            pe = get_policy_engine()
            eff = await pe.get_effective_permissions(user_id, tenant_id)
            if eff.get("role_code") == "OWNER":
                is_owner = True
            else:
                eff_perms = eff.get("effective_permissions", {}) or {}
                for mod, info in eff_perms.items():
                    acts = list((info or {}).get("actions") or [])
                    if acts:
                        allowed_modules.append(mod)
                        actions_for_module[mod] = acts
        except Exception:
            logger.warning(
                "[search_userguide] PolicyEngine resolve failed, falling back to closed",
                exc_info=True,
            )
            # Fail-closed: treat as no access (informational chunks only).
            is_owner = False

        try:
            result = await _ug_search(
                query,
                pool=pool,
                user_id=user_id,
                tenant_id=tenant_id,
                user_allowed_modules=allowed_modules,
                user_actions_for_module=actions_for_module,
                is_owner=is_owner,
                max_results=max_results,
                tier_preference=tier_pref,
            )
        except Exception as e:
            logger.exception("[search_userguide] search failed")
            return _error("INTERNAL_ERROR", f"search: {str(e)[:160]}")

        # Phase 2B-1.9: inject pre-rendered citation into tool result.
        response_dict = result.to_dict()
        try:
            chunks = response_dict.get("chunks") or []
            tier = response_dict.get("fallback_tier")
            if chunks and tier in (3, 4):
                top = chunks[0]
                top_title = top.get("doc_title") or top.get("title") or ""
                top_doc_id = top.get("doc_id") or ""
                if top_title and top_doc_id:
                    response_dict["citation_format"] = "[Title](docs:doc_id)"
                    response_dict[
                        "citation_example"
                    ] = f"[{top_title}](docs:{top_doc_id})"
                    response_dict["citation_required"] = True
                for c in chunks:
                    t = c.get("doc_title") or c.get("title") or ""
                    d = c.get("doc_id") or ""
                    if t and d:
                        c["citation"] = f"[{t}](docs:{d})"
            elif chunks and tier in (1, 2):
                response_dict["citation_required"] = False
        except Exception:
            logger.exception("[search_userguide] citation injection failed (non-fatal)")

        return {
            "success": True,
            **response_dict,
        }

    async def _execute_propose_direct(self, params: dict) -> dict:
        """Execute a direct action proposal - validate, store pending, return preview."""
        import uuid  # noqa: E402
        from datetime import datetime, timedelta, timezone

        action_key = params.get("action_key", "")
        payload = params.get("payload", {})

        # Fallback: if LLM puts fields at top level instead of under payload,
        # extract them automatically
        if not payload:
            payload = {k: v for k, v in params.items() if k != "action_key"}

        config = get_direct_action(action_key)
        if not config:
            return _error(
                "UNKNOWN_ACTION", f"Action '{action_key}' tidak ditemukan di registry."
            )

        # ═════════════ T171 FASE 1 — PEMECAHAN SLIDE MULTI-BARANG ═════════════
        # `items` (daftar baris create_item, hasil _t144_normalisasi_items)
        # DIBONGKAR DI SINI, sebelum normalisasi/validasi/enrichment, supaya
        # setiap slide melewati pipeline yang SAMA PERSIS dengan jalur skalar
        # satu-barang (G1). Yang lahir dari sini adalah payload SKALAR biasa.
        #
        # Sisa antrean disimpan di `_batch_queue` DI DALAM payload -> ia ikut
        # ke pending_actions.action_plan (jsonb). Sengaja BUKAN di
        # chat_session_state.document_context: hook after_propose menulis ulang
        # document_context setelah propose selesai, jadi antrean di sana bisa
        # tertimpa diam-diam. action_plan tak pernah disentuh siapa pun lagi.
        #
        # ⚠️ Slide lahir SATU PER SATU, tidak pernah N sekaligus: FE
        # `pendingAction` skalar + `remainingSeconds` prop global, dan gerbang
        # satu-kartu-per-percakapan menolak kartu kedua selagi satu PENDING
        # masih hidup.
        _t171_pembuka = None
        if action_key == "create_item" and isinstance(payload.get("items"), list):
            from .direct_action_registry import (  # noqa: E402
                t144_baris_bisa_dibuat as _t171_bisa,
                t144_masalah_baris as _t171_masalah,
                t171_baris_ke_payload as _t171_ke_payload,
                t171_kalimat_pembuka as _t171_pembuka_fn,
            )

            _t171_semua = [b for b in payload["items"] if isinstance(b, dict)]
            # Baris yang TIDAK BISA dibuat tak pernah jadi slide (endpoint pasti
            # menolaknya). Ia TIDAK dibuang diam-diam: namanya dicatat dan
            # disebut di ringkasan penutup sebagai dilewati, dengan sebabnya.
            _t171_baris = [b for b in _t171_semua if _t171_bisa(b)]
            _t171_awal = [
                "%s (%s)"
                % (
                    str(b.get("nama_produk") or "(tanpa nama)").strip(),
                    ", ".join(_t171_masalah(b)) or "data belum lengkap",
                )
                for b in _t171_semua
                if not _t171_bisa(b)
            ]
            payload.pop("items", None)
            if _t171_baris:
                _t171_total = len(_t171_baris)
                # Slot skalar DITIMPA dari baris PERTAMA. Wajib ditimpa, bukan
                # sekadar diisi kalau kosong: _t144_normalisasi_items mengangkat
                # tiap slot dari baris PERTAMA YANG PUNYA nilai itu, jadi
                # top-level bisa mencampur harga baris 1 dengan satuan baris 3.
                for _k in (
                    "name",
                    "item_name",
                    "item_type",
                    "base_unit",
                    "sales_price",
                    "purchase_price",
                ):
                    payload.pop(_k, None)
                payload.update(_t171_ke_payload(_t171_baris[0]))
                if _t171_total > 1 or _t171_awal:
                    import uuid as _t171_uuid  # noqa: E402

                    payload["_batch_id"] = str(_t171_uuid.uuid4())
                    payload["_batch_index"] = 1
                    payload["_batch_total"] = _t171_total
                    payload["_batch_queue"] = _t171_baris[1:]
                    payload["_batch_dilewati_awal"] = _t171_awal
                    _t171_pembuka = _t171_pembuka_fn(_t171_baris)
                    if _t171_awal:
                        _t171_pembuka += (
                            "\n\nTidak saya tampilkan (datanya belum cukup untuk "
                            "disimpan): " + " · ".join(_t171_awal)
                        )
                    logger.warning(
                        "[T171_SLIDE] pemecahan AKTIF: batch=%s total=%d "
                        "dilewati_awal=%d nama=%r",
                        payload["_batch_id"][:8],
                        _t171_total,
                        len(_t171_awal),
                        [str(b.get("nama_produk"))[:40] for b in _t171_baris],
                    )
            elif _t171_awal:
                return {
                    "message_type": "TEXT",
                    "text": (
                        "Tidak ada satu pun barang yang bisa saya simpan dari "
                        "pesan ini: " + " · ".join(_t171_awal) + "."
                    ),
                }

        # === PRE-FLIGHT CHECKS ===
        pre_flight = await self._run_pre_flight_checks(config, payload)
        if pre_flight.get("blocked"):
            msg = pre_flight["message"]
            if pre_flight.get("alternatives"):
                msg += "\n\nAlternatif: " + ", ".join(pre_flight["alternatives"])
            return {"message_type": "TEXT", "text": msg}

        # === JOURNAL PREVIEW ===
        # === GENERIC NORMALIZATION (replaces all manual if-blocks) ===
        # Pre-fetch OCR text for phone leading-zero recovery (FIX_PHONE_COERCE).
        self._last_ocr_text = None
        try:
            if self.session_manager and self.session_id:
                _st = await self.session_manager.get_state(self.session_id)
                _dc = getattr(_st, "document_context", None) or {}
                if _dc.get("source") == "intent_ocr" and _dc.get("ocr_text"):
                    self._last_ocr_text = _dc.get("ocr_text")
        except Exception as _ocr_err:
            logger.debug("[FIX_PHONE_COERCE] OCR fetch skipped: %s", _ocr_err)
        payload = self._normalize_payload(action_key, payload)

        # H: satu titik untuk KEDUA jalur (propose langsung dan re-propose
        # setelah pil entitas) — keduanya bertemu di sini.
        _amankan_nomor_dokumen(payload, action_key)

        # === ENRICHMENT (date defaults, field translation, CoA→bank lookup) ===
        _enrich_action_type = (
            action_key.replace("create_", "CREATE_").replace("void_", "VOID_").upper()
        )
        payload = await self._enrich_payload(_enrich_action_type, payload)

        # T181 FASE 1: keadaan (B) — jangan bangun kartu dari baris karangan.
        _t181_mentah = payload.pop("_t181_items_mentah", None)
        if _t181_mentah:
            return {
                "message_type": "TEXT",
                "text": _t181_pesan_tolak(_t181_mentah),
            }

        # FIX_AQUA_PERLINE_HINT 2026-05-09: pop per-line hint sentinel here
        # (BEFORE price-ask short-circuit so it propagates if user resumes
        # after providing prices). Stash on self for review_card builder.
        self._perline_hint_msg = payload.pop("_perline_hint", None)

        # ── FIX_AQUA_PRICE_ASK 2026-05-09 ─────────────────────────────────
        # Short-circuit: if enrichment detected line items with price=0
        # (master purchase_price/sales_price not set, user didn't override),
        # do NOT proceed to propose_direct. Return a sentinel response that
        # orchestrator interprets as "ask user for missing prices first".
        # Orchestrator will save partial payload + emit clarification text.
        _missing_prices = payload.pop("_needs_price_clarification", None)
        if _missing_prices:
            logger.warning(
                "[FIX_AQUA_PRICE_ASK] action=%s missing_prices=%s",
                action_key,
                [(m["idx"], m["name"]) for m in _missing_prices],
            )
            return {
                "success": True,
                "message_type": "AWAITING_ITEM_PRICE",
                "data": {
                    "action_key": action_key,
                    "payload": payload,  # already enriched, sentinel removed
                    "missing_prices": _missing_prices,
                },
            }

        # === POST-NORMALIZATION: domain-specific ID resolution ===
        # Auto-resolve vendor_id from bill_id when LLM sends non-UUID vendor_id
        if action_key == "create_bill_payment" and payload.get("bill_id"):
            vid = str(payload.get("vendor_id", ""))
            if not vid or len(vid) < 30:  # not a valid UUID
                try:
                    from .db_utils import get_session_db_pool

                    pool = await get_session_db_pool()
                    bill_row = await pool.fetchrow(
                        "SELECT vendor_id, vendor_name FROM bills WHERE id = $1::uuid AND tenant_id = $2",
                        str(payload["bill_id"]),
                        self.context.tenant_id,
                    )
                    if bill_row:
                        payload["vendor_id"] = str(bill_row["vendor_id"])
                        payload.setdefault("vendor_name", bill_row["vendor_name"])
                except Exception as e:
                    logger.warning(
                        f"[create_bill_payment] vendor_id resolve from bill: {e}"
                    )

        # Auto-extract customer_id from allocations for receive_payment
        if action_key == "create_receive_payment":
            if "invoice_id" in payload and "allocations" not in payload:
                payload["allocations"] = [
                    {
                        "invoice_id": payload["invoice_id"],
                        "amount_applied": payload.get(
                            "total_amount", payload.get("amount", 0)
                        ),
                    }
                ]
            if "allocations" in payload and "customer_id" not in payload:
                allocs = payload.get("allocations", [])
                if allocs and isinstance(allocs, list) and len(allocs) > 0:
                    first = allocs[0] if isinstance(allocs[0], dict) else {}
                    if "customer_id" in first:
                        payload["customer_id"] = first["customer_id"]

        # === RESOLVE ENTITY NAMES (for success/loading messages) ===
        await self._resolve_entity_names(action_key, payload)

        # Auto-resolve account_id from account_name or description keywords (for create_expense)
        if action_key == "create_expense" and not payload.get("account_id"):
            acct_name = payload.get("account_name", "")
            desc = payload.get("description", "")
            try:
                from .db_utils import get_session_db_pool

                pool = await get_session_db_pool()
                resolved = False

                # Strategy 1: Direct name match (user said "beban pemeliharaan")
                if acct_name:
                    acct_rows = await pool.fetch(
                        """SELECT id, name, account_code
                           FROM chart_of_accounts
                           WHERE tenant_id = $1 AND is_header = false AND is_active = true
                             AND account_code LIKE '5-%'
                             AND name ILIKE $2
                           ORDER BY CASE WHEN LOWER(name) = LOWER($3) THEN 0 ELSE 1 END, name
                           LIMIT 3""",
                        self.context.tenant_id,
                        "%" + acct_name + "%",
                        acct_name.strip(),
                    )
                    if acct_rows:
                        best = acct_rows[0]
                        for r in acct_rows:
                            if r["name"].lower().strip() == acct_name.lower().strip():
                                best = r
                                break
                        payload["account_id"] = str(best["id"])
                        payload["account_name"] = (
                            best["name"] + " (" + best["account_code"] + ")"
                        )
                        resolved = True

                # Strategy 2: Keyword inference from description
                if not resolved and desc:
                    _EXPENSE_KEYWORDS = {
                        "listrik": "Beban Listrik",
                        "air pdam": "Beban Air",
                        "telepon": "Beban Telepon",
                        "internet": "Beban Telepon & Internet",
                        "wifi": "Beban Telepon & Internet",
                        "sewa": "Beban Sewa",
                        "kontrak": "Beban Sewa Kantor",
                        "gaji": "Beban Gaji",
                        "transport": "Beban Transportasi",
                        "bensin": "Beban Transportasi",
                        "parkir": "Beban Transportasi",
                        "tol": "Beban Transportasi",
                        "ojek": "Beban Transportasi",
                        "grab": "Beban Transportasi",
                        "gojek": "Beban Transportasi",
                        "taxi": "Beban Transportasi",
                        "servis": "Beban Pemeliharaan",
                        "service": "Beban Pemeliharaan",
                        "reparasi": "Beban Pemeliharaan",
                        "perbaikan": "Beban Pemeliharaan",
                        "maintenance": "Beban Pemeliharaan",
                        "perawatan": "Beban Pemeliharaan",
                        "makan": "Beban Makan & Minum",
                        "minum": "Beban Makan & Minum",
                        "snack": "Beban Makan & Minum",
                        "catering": "Beban Makan & Minum",
                        "konsumsi": "Beban Makan & Minum",
                        "atk": "Beban Perlengkapan Kantor",
                        "alat tulis": "Beban Perlengkapan Kantor",
                        "kertas": "Beban Perlengkapan Kantor",
                        "tinta": "Beban Perlengkapan Kantor",
                        "printer": "Beban Perlengkapan Kantor",
                        "asuransi": "Beban Asuransi",
                        "pajak": "Beban Pajak",
                        "admin bank": "Biaya Admin Bank",
                        "transfer fee": "Biaya Admin Bank",
                        "biaya bank": "Biaya Admin Bank",
                    }
                    desc_lower = desc.lower()
                    matched_acct_name = None
                    for kw, acct in _EXPENSE_KEYWORDS.items():
                        if kw in desc_lower:
                            matched_acct_name = acct
                            break

                    if not matched_acct_name:
                        # Fallback: Beban Lain-lain
                        matched_acct_name = "Beban Lain-lain"

                    acct_rows = await pool.fetch(
                        """SELECT id, name, account_code
                           FROM chart_of_accounts
                           WHERE tenant_id = $1 AND is_header = false AND is_active = true
                             AND name ILIKE $2
                           LIMIT 1""",
                        self.context.tenant_id,
                        "%" + matched_acct_name + "%",
                    )
                    if acct_rows:
                        payload["account_id"] = str(acct_rows[0]["id"])
                        payload["account_name"] = (
                            acct_rows[0]["name"]
                            + " ("
                            + acct_rows[0]["account_code"]
                            + ")"
                        )
                        resolved = True

                if not resolved:
                    logger.info(
                        "[create_expense] Could not auto-resolve account_id for desc=%s",
                        desc[:50],
                    )
            except Exception as e:
                logger.warning(f"[create_expense] account_id resolve: {e}")

        # Validate required fields
        is_valid, missing = validate_payload(action_key, payload)
        if not is_valid:
            # Build helpful message with field descriptions.
            # FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09): validate_payload now
            # returns ONLY user-facing labels (hidden/display_only excluded), so
            # `missing` here never contains raw-ID fields. When ALL unresolved
            # required fields are hidden (e.g. bank_account_id/customer_id not
            # yet resolved), `missing` is EMPTY: emit a generic, human-friendly
            # ask instead of echoing an internal ID label. This path is normally
            # pre-empted by the orchestrator (bank pills / name-unresolved
            # branch); this is a last-resort net so we NEVER leak "Customer ID"
            # / "Bank Account ID" to the user.
            from .direct_action_registry import DIRECT_ACTIONS

            # `missing` is built from FieldSpec.label; map label -> FieldSpec.
            field_hints = []
            da_config = DIRECT_ACTIONS.get(action_key)
            if da_config:
                label_map = {f.label: f for f in da_config.fields}
                for m in missing:
                    f = label_map.get(m)
                    if f and f.options:
                        opts = ", ".join(f.options)
                        field_hints.append(f"**{f.label}** ({opts})")
                    elif f and f.description:
                        field_hints.append(f"**{f.label}** ({f.description})")
                    elif f:
                        field_hints.append(f"**{f.label}**")
                    else:
                        field_hints.append(f"**{m}**")
            hint_str = ", ".join(field_hints) if field_hints else ", ".join(missing)
            if not hint_str:
                # Hidden-only missing -> no askable label. Generic ask, no IDs.
                return {
                    "success": False,
                    "error": (
                        "Saya masih perlu memastikan beberapa detail "
                        "(pelanggan/rekening) sebelum melanjutkan. Bisa "
                        "perjelas sedikit lagi? 😊"
                    ),
                    "error_type": "VALIDATION_ERROR",
                    "missing_fields": missing,
                }
            return {
                "success": False,
                "error": f"Saya perlu info tambahan untuk melanjutkan: {hint_str}. Bisa tolong lengkapi? 😊",
                "error_type": "VALIDATION_ERROR",
                "missing_fields": missing,
            }

        # Apply defaults
        payload = apply_defaults(action_key, payload)

        # === BUCKET 4: Reject negative/zero qty at preview-build ===
        # Iron Law: invalid invariants (qty <= 0) must NOT reach pending_actions.action_plan.
        if action_key == "create_sales_invoice":
            _qty_violations = []
            _items_for_check = payload.get("items") or []
            if isinstance(_items_for_check, list):
                for _idx, _it in enumerate(_items_for_check):
                    if not isinstance(_it, dict):
                        continue
                    _q_raw = _it.get("quantity", _it.get("qty"))
                    try:
                        _q_val = float(_q_raw) if _q_raw is not None else None
                    except (ValueError, TypeError):
                        _q_val = None
                    if _q_val is not None and _q_val <= 0:
                        _desc = (
                            _it.get("description")
                            or _it.get("item_name")
                            or _it.get("name")
                            or f"item #{_idx + 1}"
                        )
                        _qty_violations.append((_desc, _q_val))
            if _qty_violations:
                _lines = [
                    f"'{d}' qty={int(q) if q == int(q) else q}"
                    for (d, q) in _qty_violations
                ]
                logger.warning(
                    "[BUCKET4_NEGQTY] rejected violations=%s payload_items=%s",
                    _qty_violations,
                    _items_for_check,
                )
                _msg = (
                    "Quantity tidak valid: "
                    + ", ".join(_lines)
                    + ". Qty harus lebih dari 0."
                )
                return {"message_type": "TEXT", "text": _msg}
        # === END BUCKET 4 ===

        # === JOURNAL PREVIEW (after normalization + validation + defaults) ===
        journal_preview = None
        preview_warnings: list[str] = []
        if config.creates_journal and config.journal_preview_endpoint:
            _pv = await self._get_journal_preview(config, payload)
            if isinstance(_pv, dict):
                journal_preview = _pv.get("journal_lines")
                preview_warnings = list(_pv.get("warnings") or [])
            else:
                # endpoint pratinjau lama yang membalas daftar telanjang
                journal_preview = _pv

        # Store pending action
        pending_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.ttl_seconds)

        try:
            from ..unified_agent.db_utils import get_session_db_pool  # noqa: E402

            pool = await get_session_db_pool()
            await pool.execute(
                """
                INSERT INTO pending_actions (
                    id, tenant_id, user_id, conversation_id,
                    action_id, action_type, action_category,
                    action_plan, status, is_direct, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                uuid.UUID(pending_id),
                self.context.tenant_id,
                self.context.user_id,
                self.session_id or "",
                action_key,
                action_key.upper(),
                "MASTER_DATA",
                json.dumps(payload, default=str),
                "PENDING",
                True,
                expires_at,
            )
        except Exception as e:
            logger.error(f"[DirectAction] DB insert failed: {e}")
            return _error("DB_ERROR", f"Gagal menyimpan action: {str(e)[:200]}")

        # Build confirmation table + structured review card
        confirmation_table = build_confirmation_table(
            action_key, payload, journal_preview
        )
        review_card = build_review_card_payload(
            action_key, payload, journal_preview, preview_warnings=preview_warnings
        )

        # Build detection reason from payload fields
        _det_parts = []
        if payload.get("amount") or payload.get("total_amount"):
            _amt = payload.get("amount") or payload.get("total_amount")
            try:
                _det_parts.append(
                    "nominal Rp {:,.0f}".format(float(_amt)).replace(",", ".")
                )
            except (ValueError, TypeError):
                pass
        # FIX_DOGFOOD_DETECTION_PARTY (2026-06-09): the detection_reason party
        # must reflect THIS action's own counterparty. The old loop picked
        # vendor_name first via break, so a CUSTOMER receive-payment whose
        # payload carried a stale/leaked vendor_name rendered "vendor 'Knitto
        # Textile Holis'" instead of the real customer (Aqua). Make the party
        # field action-aware: receive_payment -> customer; bill_payment ->
        # vendor; otherwise fall back to the generic priority order.
        if action_key in ("create_receive_payment", "receive_payment"):
            _det_party_order = [
                ("customer_name", "pelanggan"),
                ("item_name", "barang"),
                ("account_name", "akun"),
                ("name", "nama"),
            ]
        elif action_key in ("create_bill_payment", "make_payment"):
            _det_party_order = [
                ("vendor_name", "vendor"),
                ("item_name", "barang"),
                ("account_name", "akun"),
                ("name", "nama"),
            ]
        elif action_key in (
            "create_item",
            "update_item",
            "create_warehouse",
            "update_warehouse",
            "create_account",
            "update_account",
        ):
            # FIX_DETECTION_PARTY_ITEM (2026-06-18): item / master-data flows
            # have NO counterparty. The generic order put vendor_name /
            # customer_name FIRST, so a stale/leaked vendor_name (e.g. from a
            # prior vendor interaction left in session memory) surfaced as
            # "Terdeteksi dari vendor 'NONENG'" on a create_item card. Put the
            # item/entity name first and EXCLUDE vendor/customer entirely so a
            # stray counterparty can never appear on a master-data card.
            # T145 (2026-08-28): "name" DIDAHULUKAN atas "item_name". Pada
            # payload create_item nyata, "name" membawa nama LENGKAP hasil
            # ekstraksi ("Kaos 20s + Sablon Plastisol (Size XS-XL)") sedangkan
            # "item_name" membawa varian TERPOTONG ("Kaos 20s + Sablon
            # Plastisol"), sehingga label memilih yang terpotong. Sensus atas
            # pending_actions.action_plan: pada seluruh baris terdampak (yang
            # memuat KEDUA kunci pada cabang ini) item_name TAK PERNAH lebih
            # panjang dari name -- 4 identik + 1 superset, nol kasus balik.
            # Hanya URUTAN yang ditukar; vendor/customer tetap DIKECUALIKAN.
            _det_party_order = [
                ("name", "nama"),
                ("item_name", "barang"),
                ("account_name", "akun"),
            ]
        elif action_key in ("create_vendor", "update_vendor"):
            # FIX_DETECTION_PARTY_ITEM: a vendor card's party IS the vendor's
            # own name; never let a leaked customer_name surface.
            _det_party_order = [
                ("vendor_name", "vendor"),
                ("name", "nama"),
            ]
        elif action_key in ("create_customer", "update_customer"):
            # FIX_DETECTION_PARTY_ITEM: a customer card's party IS the
            # customer's own name; never let a leaked vendor_name surface.
            _det_party_order = [
                ("customer_name", "pelanggan"),
                ("name", "nama"),
            ]
        else:
            _det_party_order = [
                ("vendor_name", "vendor"),
                ("customer_name", "pelanggan"),
                ("item_name", "barang"),
                ("account_name", "akun"),
                ("name", "nama"),
            ]
        for _det_key, _det_label in _det_party_order:
            if payload.get(_det_key):
                _det_parts.append(_det_label + " '" + str(payload[_det_key]) + "'")
                break
        _detection_reason = (
            "Terdeteksi dari: " + ", ".join(_det_parts) if _det_parts else ""
        )
        # dok. 81 (2) — aksi ATAS DOKUMEN yang sudah ada: nol teks, bukan teks
        # yang benar.
        #
        # "Terdeteksi dari" bermakna ketika sistem MENEBAK: mengambil nama
        # pelanggan dari kalimat bebas, menyimpulkan nominal dari kata-kata.
        # Di sini nol tebakan terjadi — SELURUH isi kartu dibaca dari dokumen
        # sumber lewat satu SELECT, dan satu-satunya hal yang diketik user
        # (nomor dokumennya) sudah tercetak di baris PERTAMA kartu. Teks
        # "Terdeteksi dari: nominal Rp 4.250.000, pelanggan 'Toko Melati'"
        # menyebut dua hal yang TIDAK diketik owner, di bawah label yang
        # menyiratkan sistem menyimpulkan sesuatu.
        #
        # Akar yang lebih luas TIDAK disentuh di sini: _det_parts dibangun dari
        # payload untuk seluruh 60 aksi, jadi ia menggambarkan HASIL, bukan
        # masukan — daftar prioritas di atas sudah bercabang lima kali untuk
        # menambal gejala yang sama. Membawa user_text ke lapisan ini adalah
        # T69, batch tersendiri. Yang dikerjakan di sini hanya keluarga aksi
        # dokumen, tempat jawabannya bukan "teks yang benar" melainkan "tak
        # ada teks yang perlu ditampilkan".
        try:
            from .direct_action_registry import DOCUMENT_ACTIONS_BY_KEY as _DAK_DET

            if action_key in _DAK_DET:
                _detection_reason = ""
        except Exception as _det_err:  # noqa: BLE001
            logger.warning("[DETECTION] cek aksi dokumen gagal: %s", _det_err)

        # FIX_AQUA_PERLINE_HINT 2026-05-09: prepend hint banner to card
        # narrative if user mentioned per-line keywords. Helps user discover
        # Edit-form path for complex per-line config.
        _hint_msg = getattr(self, "_perline_hint_msg", None)
        if _hint_msg:
            confirmation_table = "💡 " + _hint_msg + "\n\n" + (confirmation_table or "")
            self._perline_hint_msg = None  # consume

        response_data = {
            "success": True,
            "message_type": "DIRECT_ACTION_PREVIEW",
            "content": confirmation_table,
            "data": {
                "pending_action_id": pending_id,
                "action_key": action_key,
                "detection_reason": _detection_reason,
                "display_name": f"{config.display_name}: {payload.get('name') or payload.get('entity_name') or ''}".strip(
                    ": "
                )
                if action_key.startswith("update_")
                and (payload.get("name") or payload.get("entity_name"))
                else config.display_name,
                "payload": payload,
                "expires_at": expires_at.isoformat(),
                "risk_level": config.risk_level,
                "confirmation_table": confirmation_table,
                "review_card": review_card,
                **build_ux_metadata(action_key, payload),
            },
        }

        if journal_preview:
            response_data["journal_preview"] = journal_preview

        # ═════════════ T171 FASE 1 — NARASI + PROGRESS + AUTO-MAJU ═════════════
        # Berlaku untuk SLIDE 1 (dipecah di atas) MAUPUN slide 2..N (dipropose
        # ulang oleh _advance_item_slide di router) — keduanya lewat sini.
        #
        # `content` diisi NARASI, BUKAN tabel: FE merender narasi hanya bila
        # `msg.content !== directData.confirmation_table` (MessageRenderer
        # showNarrative). Kalau keduanya sama, penanda "Barang k dari N" DAN
        # bilah progress tidak pernah muncul.
        #
        # workflow_continuation=True bahkan pada slide TERAKHIR: "lanjut"
        # terakhir itulah yang memunculkan ringkasan penutup.
        _t171_bid = payload.get("_batch_id")
        if _t171_bid:
            try:
                _t171_i = int(payload.get("_batch_index") or 1)
                _t171_n = int(payload.get("_batch_total") or 1)
            except (TypeError, ValueError):
                _t171_i, _t171_n = 1, 1
            _t171_teks = "Barang %d dari %d: **%s**" % (
                _t171_i,
                _t171_n,
                str(payload.get("name") or "(tanpa nama)"),
            )
            if _t171_pembuka:
                _t171_teks = _t171_pembuka + "\n\n" + _t171_teks
            response_data["content"] = _t171_teks
            response_data["data"]["progress"] = {
                "current": _t171_i,
                "total": _t171_n,
            }
            response_data["data"]["workflow_continuation"] = True
            response_data["data"]["_batch_id"] = _t171_bid

        return response_data

    # --- Query Execution Engine ---

    async def _execute_query(self, params: dict) -> dict:
        """Execute a read-only financial query from the query registry."""
        query_key = params.get("query_key", "")
        query_params = params.get("params") or {}

        config = get_query_action(query_key)
        if not config:
            return _error("UNKNOWN_QUERY", f"Query '{query_key}' tidak terdaftar.")

        # Build request path + query params
        path = config.rest_endpoint
        req_params = {}

        # Default: exclude draft & void for bills/invoices queries
        if (
            query_key in ("query_bills_list", "query_sales_invoices_list")
            and "status" not in query_params
        ):
            req_params["status"] = "active"

        for key, value in query_params.items():
            if value is None:
                continue
            placeholder = "{" + key + "}"
            if placeholder in path:
                val = str(value)
                if (key.endswith("_id") or key == "id") and val and len(val) < 10:
                    return {"error": f"Parameter {key} bukan UUID valid: {val}"}
                path = path.replace(placeholder, val)
            else:
                req_params[key] = value

        # Auto-fill date defaults for current month if not provided
        import datetime  # noqa: E402

        today = datetime.date.today()
        if any(qp.param_type == "date" for qp in config.query_params):
            if "start_date" not in req_params and "start_date" in [
                qp.name for qp in config.query_params
            ]:
                req_params["start_date"] = today.replace(day=1).isoformat()
            if "end_date" not in req_params and "end_date" in [
                qp.name for qp in config.query_params
            ]:
                req_params["end_date"] = today.isoformat()

        # Fill path params with defaults if still have placeholders
        for qp in config.query_params:
            placeholder = "{" + qp.name + "}"
            if placeholder in path:
                default = qp.default or today.strftime("%Y-%m")
                path = path.replace(placeholder, default)

        url = f"{KERNEL_BASE_URL}{path}"
        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                resp = await client.get(url, params=req_params, headers=headers)
                if resp.status_code >= 400:
                    return _error("API_ERROR", f"Query gagal: HTTP {resp.status_code}")
                raw_data = resp.json()
        except httpx.TimeoutException:
            return _error("TIMEOUT", f"Query '{query_key}' timeout.")
        except Exception as e:
            return _error("INTERNAL_ERROR", f"Query error: {str(e)[:200]}")

        # Unwrap common response patterns
        data = raw_data
        if isinstance(data, dict) and "data" in data:
            data = data["data"]

        # ─── CHART QUERY: return CHART message_type directly ───
        if isinstance(config, ChartQueryConfig):
            chart_spec = self._build_chart_spec(config, data, query_params)
            return {
                "message_type": "CHART",
                "content": f"Berikut grafik {config.display_name}:",
                "data": chart_spec,
            }

        # Format response based on response_format
        formatted = self._format_query_result(config, data, raw_data)

        return {
            "success": True,
            "query_key": query_key,
            "display_name": config.display_name,
            "response_format": config.response_format,
            "data": formatted,
        }

    def _format_query_result(self, config: QueryActionConfig, data, raw_data) -> dict:
        """Format query result based on response_format type."""
        fmt = config.response_format

        if fmt == "single_value":
            return self._format_single_value(data, raw_data)
        elif fmt == "summary":
            return self._format_summary(data, raw_data)
        elif fmt == "table":
            return self._format_table(data, raw_data)
        elif fmt == "list":
            return self._format_list(data, raw_data)
        else:
            return {"raw": data}

    def _format_single_value(self, data, raw_data) -> dict:
        """Format single_value: extract key metrics."""
        if isinstance(raw_data, dict):
            d = raw_data
        elif isinstance(data, dict):
            d = data
        else:
            return {"raw": data}

        result = {}
        # Pick known financial fields
        for key in [
            "total_balance",
            "cash_balance",
            "bank_balance",
            "account_count",
            "today_inflows",
            "today_outflows",
            "today_net",
        ]:
            if key in d:
                result[key] = d[key]
        if not result:
            result = d
        return result

    def _format_summary(self, data, raw_data) -> dict:
        """Format summary: return structured data as-is (LLM will narrate)."""
        if isinstance(data, dict):
            return data
        elif isinstance(raw_data, dict):
            # Strip non-data keys
            return {
                k: v
                for k, v in raw_data.items()
                if k not in ("success", "status", "message")
            }
        return {"raw": data}

    def _format_table(self, data, raw_data) -> dict:
        """Format table: truncate to max 20 rows for LLM context."""
        MAX_ROWS = 20
        if isinstance(data, list):
            truncated = len(data) > MAX_ROWS
            rows = data[:MAX_ROWS]
            result = {"rows": rows, "total_count": len(data)}
            if truncated:
                result["truncated"] = True
                result["showing"] = MAX_ROWS
            return result
        elif isinstance(data, dict):
            # Trial balance etc might have accounts list
            for key in ["accounts", "items", "rows", "entries", "data"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    truncated = len(items) > MAX_ROWS
                    result = {**{k: v for k, v in data.items() if k != key}}
                    result["rows"] = items[:MAX_ROWS]
                    result["total_count"] = len(items)
                    if truncated:
                        result["truncated"] = True
                        result["showing"] = MAX_ROWS
                    return result
            return data
        return {"raw": data}

    def _format_list(self, data, raw_data) -> dict:
        """Format list: return items with count."""
        MAX_ITEMS = 20
        if isinstance(data, list):
            truncated = len(data) > MAX_ITEMS
            result = {"items": data[:MAX_ITEMS], "total_count": len(data)}
            if truncated:
                result["truncated"] = True
            return result
        elif isinstance(data, dict) and any(
            k in data for k in ["items", "data", "periods"]
        ):
            for key in ["items", "data", "periods"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    return {
                        "items": items[:MAX_ITEMS],
                        "total_count": len(items),
                        "truncated": len(items) > MAX_ITEMS,
                    }
        return {"items": [data] if data else [], "total_count": 1 if data else 0}

    # --- Transaction Lookup Tools ---

    async def _execute_get_customer_invoices(self, params: dict) -> dict:
        """Get outstanding invoices for a customer via compute_customer_ar()."""
        customer_id = params.get("customer_id", "")
        status = params.get("status", "outstanding")

        # Guard: customer_id MUST be a valid UUID
        if not customer_id or len(customer_id) < 10:
            logger.warning(
                "get_customer_invoices called without valid customer_id: %s",
                customer_id,
            )
            return {
                "results": [],
                "error": "customer_id wajib diisi. Panggil search_customers dulu untuk mendapatkan UUID pelanggan.",
            }

        # ARAP Rule 5/6: Use /customers/{id}/open-invoices which wraps
        # compute_customer_ar() — single source of truth from journal_lines
        if status == "outstanding":
            url = f"http://localhost:8000/api/customers/{customer_id}/open-invoices"
        else:
            url = "http://localhost:8000/api/sales-invoices"

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                api_params = (
                    {}
                    if status == "outstanding"
                    else {"customer_id": customer_id, "limit": "20"}
                )
                resp = await client.get(url, headers=headers, params=api_params)
                if resp.status_code >= 400:
                    return {"results": [], "error": f"HTTP {resp.status_code}"}
                data = resp.json()
        except Exception as e:
            return {"results": [], "error": str(e)}

        if status == "outstanding":
            # Response: {"invoices": [...], "summary": {...}}
            invoices = normalize_api_response(data)
            summary = data.get("summary", {}) if isinstance(data, dict) else {}
            return {
                "results": [
                    {
                        "id": inv.get("id", ""),
                        "number": inv.get("invoice_number", ""),
                        "date": inv.get("invoice_date", ""),
                        "due_date": inv.get("due_date", ""),
                        "total": str(inv.get("total_amount", 0)),
                        "amount_paid": str(inv.get("paid_amount", 0)),
                        "amount_due": str(inv.get("remaining_amount", 0)),
                        "is_overdue": inv.get("is_overdue", False),
                        "overdue_days": inv.get("overdue_days", 0),
                    }
                    for inv in invoices
                ],
                "total_outstanding": str(summary.get("total_outstanding", 0)),
            }
        else:
            invoices = normalize_api_response(data)
            return {
                "results": [
                    {
                        "id": inv.get("id", ""),
                        "number": inv.get("invoice_number", ""),
                        "date": inv.get("invoice_date", ""),
                        "total": str(
                            inv.get(
                                "total_amount", inv.get("total", inv.get("amount", 0))
                            )
                        ),
                        "amount_paid": str(inv.get("amount_paid", 0)),
                        "amount_due": str(
                            inv.get(
                                "amount_due",
                                inv.get("total_amount", inv.get("total", 0)),
                            )
                        ),
                        "status": inv.get("status", ""),
                    }
                    for inv in invoices
                ]
            }

    async def _execute_get_vendor_bills(self, params: dict) -> dict:
        """Get outstanding bills for a vendor via compute_vendor_ap()."""
        vendor_id = params.get("vendor_id", "")
        status = params.get("status", "outstanding")

        # Guard: vendor_id MUST be a valid UUID
        if not vendor_id or len(vendor_id) < 10:
            logger.warning(
                "get_vendor_bills called without valid vendor_id: %s", vendor_id
            )
            return {
                "results": [],
                "error": "vendor_id wajib diisi. Panggil search_vendors dulu untuk mendapatkan UUID vendor.",
            }

        # ARAP Rule 5/6: Use /vendors/{id}/open-bills which wraps
        # compute_vendor_ap() — single source of truth from journal_lines
        if status == "outstanding":
            url = f"http://localhost:8000/api/vendors/{vendor_id}/open-bills"
        else:
            url = "http://localhost:8000/api/bills"

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                api_params = (
                    {}
                    if status == "outstanding"
                    else {"vendor_id": vendor_id, "limit": "20"}
                )
                resp = await client.get(url, headers=headers, params=api_params)
                if resp.status_code >= 400:
                    logger.warning(
                        "get_vendor_bills HTTP %s for vendor %s: %s",
                        resp.status_code,
                        vendor_id,
                        resp.text[:200],
                    )
                    return {"results": [], "error": f"HTTP {resp.status_code}"}
                data = resp.json()
        except Exception as e:
            logger.error("get_vendor_bills exception for vendor %s: %s", vendor_id, e)
            return {"results": [], "error": str(e)}

        if status == "outstanding":
            # Response: {"bills": [...], "total_outstanding": X}
            bills = normalize_api_response(data)
            logger.info(
                "get_vendor_bills: vendor=%s, bills_count=%d, total=%s",
                vendor_id,
                len(bills),
                data.get("total_outstanding", 0),
            )
            return {
                "results": [
                    {
                        "id": b.get("id", ""),
                        "number": b.get("bill_number", ""),
                        "date": b.get("bill_date", ""),
                        "due_date": b.get("due_date", ""),
                        "total": str(b.get("total_amount", 0)),
                        "amount_paid": str(b.get("paid_amount", 0)),
                        "amount_due": str(b.get("remaining_amount", 0)),
                        "is_overdue": b.get("is_overdue", False),
                    }
                    for b in bills
                ],
                "total_outstanding": str(data.get("total_outstanding", 0)),
            }
        else:
            bills = normalize_api_response(data)
            if not isinstance(bills, list):
                bills = []
            return {
                "results": [
                    {
                        "id": b.get("id", ""),
                        "number": b.get("bill_number", b.get("invoice_number", "")),
                        "date": b.get("bill_date", b.get("issue_date", "")),
                        "vendor_name": b.get("vendor_name", ""),
                        "total": str(
                            b.get("total_amount", b.get("total", b.get("amount", 0)))
                        ),
                        "amount_paid": str(b.get("amount_paid", 0)),
                        "amount_due": str(
                            b.get(
                                "amount_due", b.get("total_amount", b.get("total", 0))
                            )
                        ),
                        "status": b.get("status", ""),
                    }
                    for b in bills
                ]
            }

    # --- Session Tool Execution ---

    async def _execute_session_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a session tool (queries session-level data, not kernel API)."""
        if not self.session_manager or not self.session_id:
            return _error("NO_SESSION", "Session belum diinisialisasi.")

        if tool_name == "get_session_events":
            limit = min(params.get("limit", 10), 20)
            events = await self.session_manager.get_recent_events(
                self.session_id, limit=limit
            )
            return {"success": True, "data": events}

        elif tool_name == "search_chat_history":
            query = params.get("query", "")
            if not query:
                return _error("MISSING_QUERY", "Parameter 'query' wajib diisi.")
            days_back = min(params.get("days_back", 7), 30)
            results = await self.session_manager.search_chat_history(
                query, days_back=days_back
            )
            return {"success": True, "data": results}

        elif tool_name == "review_next_unmatched":
            return await self._execute_review_next_unmatched(params)

        elif tool_name == "import_bank_statement":
            return await self._execute_import_bank_statement(params)

        elif tool_name == "create_reconciliation_session":
            return await self._execute_create_recon_session(params)

        elif tool_name == "agentic_reconcile":
            return await self._execute_agentic_reconcile(params)

        elif tool_name == "start_workflow":
            return await self._execute_start_workflow(params)

        elif tool_name == "cancel_workflow":
            return await self._execute_cancel_workflow(params)

        return _error(
            "UNKNOWN_SESSION_TOOL", f"Session tool {tool_name!r} tidak dikenali."
        )

    # --- Start Workflow (Deterministic State Machine) ---

    async def _execute_start_workflow(self, params: dict) -> dict:
        """Execute start_workflow: advance the deterministic workflow engine."""
        from .workflow_engine import WorkflowEngine  # noqa: E402
        from .db_utils import get_session_db_pool  # noqa: E402

        pool = await get_session_db_pool()

        # Callback for complex tool operations (file import, etc.)
        async def execute_tool(tool_name, tool_params):
            return await self._execute_session_tool(tool_name, tool_params)

        engine = WorkflowEngine(  # noqa: F821
            db_pool=pool,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            auth_token=self.context.auth_token,
            execute_tool=execute_tool,
        )

        workflow_type = params.get("workflow_type", "bank_reconciliation")
        user_data = params.get("user_data", {})

        # Auto-inject file_ref from user message if LLM didn't include it
        if not user_data.get("file_ref") and self.user_text:
            import re as _re  # noqa: E402

            m = _re.search(r"file_ref=(chat_upload:[^\]\s]+)", self.user_text)
            if m:
                user_data["file_ref"] = m.group(1)
                logger.info(
                    f"[Workflow] Auto-injected file_ref={m.group(1)} from user message"
                )

        # Task E: Auto-inject balance from user message text (robust parser)
        if not user_data.get("statement_ending_balance") and self.user_text:
            from .balance_parser import parse_balance  # noqa: E402

            _bal_val, _bal_found = parse_balance(self.user_text)
            if _bal_found and _bal_val is not None:
                user_data["statement_ending_balance"] = int(_bal_val)
                logger.info(
                    f"[Workflow] Auto-injected balance={_bal_val} from parse_balance"
                )

        # Use conversation session_id as chat_session_id
        chat_session_id = self.session_id or "unknown"

        # Detect REVIEWING state → use resume() to increment reviewed_count
        # Also detect AWAITING_DECISION for document review resume
        existing_state = await engine.get_state(chat_session_id, workflow_type)
        if existing_state and existing_state.current_state == "AWAITING_DECISION":
            logger.info(
                "[Workflow] State=AWAITING_DECISION → calling resume() (doc review decision)"
            )
            result = await engine.resume(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                confirmed_data=user_data,
            )
        elif existing_state and existing_state.current_state == "REVIEWING":
            logger.info(
                "[Workflow] State=REVIEWING → calling resume() (reviewed_count will increment)"
            )
            result = await engine.resume(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                confirmed_data=user_data,
            )
        else:
            result = await engine.process(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                user_data=user_data,
            )

        # Auto-propose: if engine is in REVIEWING with a suggestion, bypass LLM
        logger.info(
            f"[Workflow] Engine result: state={result.new_state}, auto_results type={type(result.auto_results).__name__}, keys={list(result.auto_results.keys()) if isinstance(result.auto_results, dict) else 'N/A'}"
        )
        if (
            result.new_state == "REVIEWING"
            and result.auto_results
            and isinstance(result.auto_results, dict)
        ):
            ar = result.auto_results
            line = (ar.get("review_item") or {}).get("statement_line", {})
            line_id = line.get("id", "")
            line_desc = line.get("description", "")
            line_date = line.get("date", "")
            line_amount = line.get("amount", 0)
            sid = ar.get("session_id", "")
            ba_id = ar.get("bank_account_id", "")
            ba_name = ar.get("bank_account_name", "")

            propose_params = None

            if ar.get("bill_suggestion"):
                bs = ar["bill_suggestion"]
                propose_params = {
                    "action_key": "create_bill_payment",
                    "payload": {
                        "vendor_id": bs.get("vendor_id", ""),
                        "bill_id": bs.get("bill_id", ""),
                        "vendor_name": bs.get("vendor_name", ""),
                        "bill_number": bs.get("bill_number", ""),
                        "bill_amount": bs.get("bill_amount", 0),
                        "amount_due": bs.get("amount_due", 0),
                        "total_amount": min(abs(line_amount), bs.get("amount_due", 0))
                        if line_amount
                        else bs.get("amount_due", 0),
                        "bank_account_id": ba_id,
                        "bank_account_name": ba_name,
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "payment_date": line_date,
                    },
                }
            elif ar.get("invoice_suggestion"):
                ivs = ar["invoice_suggestion"]
                alloc_type = ivs.get("allocation_type", "single")
                if alloc_type != "needs_user_input":
                    allocations = ivs.get("allocations") or [
                        {
                            "invoice_id": ivs.get("invoice_id", ""),
                            "amount_applied": ivs.get("amount_due", line_amount),
                        }
                    ]
                    propose_params = {
                        "action_key": "create_receive_payment",
                        "payload": {
                            "customer_id": ivs.get("customer_id", ""),
                            "customer_name": ivs.get("customer_name", ""),
                            "invoice_numbers": ivs.get("invoice_number", ""),
                            "total_amount": ivs.get("amount_due", line_amount),
                            "allocations": allocations,
                            "bank_account_id": ba_id,
                            "bank_account_name": ba_name,
                            "session_id": sid,
                            "statement_line_id": line_id,
                            "statement_description": line_desc,
                            "payment_date": line_date,
                        },
                    }
            elif ar.get("category_suggestion"):
                cs = ar["category_suggestion"]
                propose_params = {
                    "action_key": "categorize_statement",
                    "payload": {
                        "account_id": cs.get("account_id", ""),
                        "account_name": cs.get("account_name", ""),
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "statement_date": line_date,
                        "amount": line_amount,
                        "description": line_desc,
                    },
                }
            elif line_id:
                # Fallback: no suggestion — propose categorize without account_id
                # User sees the line details and picks the account themselves
                logger.info(
                    f"[Workflow] No suggestion for line {line_id}, fallback categorize_statement"
                )
                propose_params = {
                    "action_key": "categorize_statement",
                    "payload": {
                        "account_id": "",
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "statement_date": line_date,
                        "amount": line_amount,
                        "description": line_desc,
                    },
                }

            if propose_params:
                logger.info(
                    f"[Workflow] Auto-propose {propose_params['action_key']} for line {line_id}"
                )
                propose_result = await self._execute_propose_direct(propose_params)
                logger.info(
                    f"[Workflow] Propose result: success={propose_result.get('success')}, msg_type={propose_result.get('message_type')}"
                )
                if not propose_result.get("success"):
                    logger.warning(
                        f"[Workflow] Auto-propose FAILED: {propose_result.get('error', 'unknown')}"
                    )
                if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                    # Enrich with narrative text + progress (in data for passthrough)
                    ri = ar.get("review_item") or {}
                    position = ri.get("position", 1)
                    remaining = ri.get("remaining", 0)
                    total = position + remaining
                    _item_line = f"{line_desc} — Rp {int(abs(line_amount)):,}".replace(
                        ",", "."
                    )
                    # Task A: On first item only, prepend import summary + breakdown
                    reviewed_count = ar.get("reviewed_count", 0)
                    _item_counter = ar.get("item_counter", "")
                    if reviewed_count == 0 and result.auto_results:
                        ar_all = result.auto_results
                        matched = ar_all.get(
                            "matched_count", ar_all.get("auto_matched", 0)
                        )
                        summary_data = (
                            ar_all.get("summary") or ar_all.get("session_stats") or {}
                        )
                        total_imported = summary_data.get(
                            "total_statement_lines",
                            summary_data.get("total_lines", total + matched),
                        )
                        account_name = ar_all.get(
                            "account_name",
                            ar_all.get("bank_account_name", "rekening bank"),
                        )
                        # Conversational narrative with breakdown
                        parts = [
                            f"Oke, rekening koran sudah diproses. Ada {total_imported} transaksi di {account_name}."
                        ]
                        if matched > 0:
                            parts.append(
                                f"{matched} transaksi langsung cocok dengan data di sistem."
                            )
                        if total == 1:
                            parts.append(
                                "Masih ada 1 transaksi yang perlu ditinjau manual."
                            )
                        else:
                            parts.append(
                                f"Masih ada {total} transaksi yang perlu ditinjau manual."
                            )
                        # Pre-scan breakdown from review_preview
                        rp = ar_all.get("review_preview", {})
                        breakdown_lines = []
                        bill_count = rp.get("bill_match", 0)
                        invoice_count = rp.get("invoice_match", 0)
                        no_match_count = rp.get("no_match", 0)
                        if bill_count > 0:
                            breakdown_lines.append(
                                f"• {bill_count} kemungkinan cocok dengan tagihan vendor."
                            )
                        if invoice_count > 0:
                            breakdown_lines.append(
                                f"• {invoice_count} kemungkinan cocok dengan invoice pelanggan."
                            )
                        if no_match_count > 0:
                            breakdown_lines.append(
                                f"• {no_match_count} perlu kategorisasi manual."
                            )
                        if breakdown_lines:
                            parts.append(
                                "Penilaian awal:\n" + "\n".join(breakdown_lines)
                            )
                        parts.append("Mari kita review satu per satu.")
                        narrative = "\n\n".join(parts)
                    else:
                        narrative = "Lanjut ke transaksi berikutnya."
                    propose_result["content"] = narrative
                    if "data" in propose_result and isinstance(
                        propose_result["data"], dict
                    ):
                        propose_result["data"]["progress"] = {
                            "current": position,
                            "total": total,
                        }
                        # Bridge: inject review_card for frontend InlineCard rendering
                        try:
                            _rc = _build_review_card(
                                line,
                                ar.get("bill_suggestion"),
                                ar.get("invoice_suggestion"),
                                ar.get("category_suggestion"),
                                ba_name,
                                position,
                                total,
                            )
                            propose_result["data"]["review_card"] = _rc
                            logger.info(
                                f"[Workflow] review_card injected: title={_rc.get('title_label')}, match={_rc.get('match', {}).get('type') if _rc.get('match') else 'none'}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[Workflow] review_card build failed: {e}",
                                exc_info=True,
                            )
                    return propose_result

        # ============ DOCUMENT REVIEW AUTO-PROPOSE ============
        if (
            workflow_type == "document_review"
            and result.auto_results
            and isinstance(result.auto_results, dict)
            and result.auto_results.get("confirm_suggestion")
        ):
            suggestion = result.auto_results["confirm_suggestion"]
            logger.info(
                f"[Workflow] Document review auto-propose: {suggestion.get('action_key')}"
            )
            propose_result = await self._execute_propose_direct(
                {
                    "action_key": suggestion["action_key"],
                    "payload": suggestion["payload"],
                }
            )
            if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                # Build narrative from instruction
                narrative = result.llm_instruction or result.auto_results.get(
                    "instruction", ""
                )
                propose_result["content"] = narrative
                propose_result["workflow_type"] = "document_review"
                propose_result["workflow_state"] = result.new_state
                return propose_result

        response = {
            "success": True,
            "advanced": result.advanced,
            "current_state": result.new_state,
            "completed": result.completed,
        }
        if result.llm_instruction:
            response["llm_instruction"] = result.llm_instruction
        if result.auto_results:
            response["auto_results"] = result.auto_results
        if result.direct_action:
            response["direct_action"] = result.direct_action
        return response

    async def _execute_cancel_workflow(self, params: dict) -> dict:
        """Cancel an active workflow."""
        from .db_utils import get_session_db_pool  # noqa: E402

        workflow_type = params.get("workflow_type", "bank_reconciliation")
        chat_session_id = self.session_id or "unknown"

        pool = await get_session_db_pool()

        async def execute_tool(tool_name, tool_params):
            return await self._execute_session_tool(tool_name, tool_params)

        engine = WorkflowEngine(  # noqa: F821
            db_pool=pool,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            auth_token=self.context.auth_token,
            execute_tool=execute_tool,
        )

        existing_state = await engine.get_state(chat_session_id, workflow_type)
        if not existing_state:
            return {"text": "Tidak ada workflow aktif untuk dibatalkan."}

        # Set state to cancelled
        await engine.cancel(chat_session_id, workflow_type)
        return {"text": "Rekonsiliasi dibatalkan.", "cancelled": True}

    # --- Review Next Unmatched (READ-ONLY — Law 0) ---

    async def _match_against_outstanding_bills(
        self, statement_line: dict, headers: dict
    ) -> list[dict]:
        """
        Cross-reference a DEBIT statement line against outstanding bills.
        Returns list of bill suggestions sorted by confidence (HIGH first).
        READ-ONLY — Law 0 compliant.
        Uses Decimal for precision-safe comparison (Law 25).
        """
        base_url = "http://localhost:8000"
        amount = abs(_to_amount(statement_line.get("amount", 0)))
        description = (statement_line.get("description") or "").upper()
        reference = (statement_line.get("reference") or "").upper()

        if amount <= 0:
            return []

        try:
            # Fetch outstanding bills (unpaid + partial) concurrently
            async def _fetch_bills(status: str):
                async with httpx.AsyncClient(timeout=10.0) as client:
                    return await client.get(
                        f"{base_url}/api/bills",
                        params={"status": status, "limit": 50},
                        headers=headers,
                    )

            resp_unpaid, resp_partial = await asyncio.gather(
                _fetch_bills("unpaid"),
                _fetch_bills("partial"),
            )

            unpaid_bills = []
            for resp in [resp_unpaid, resp_partial]:
                if resp.status_code == 200:
                    bill_data = resp.json()
                    unpaid_bills.extend(
                        bill_data.get("items", normalize_api_response(bill_data))
                    )

            if not unpaid_bills:
                return []

            suggestions = []
            for bill in unpaid_bills:
                bill_number = (bill.get("invoice_number") or "").upper()
                vendor_name = _safe_get_name(bill, "bill").upper()
                bill_amount = _to_amount(
                    bill.get("total_amount", bill.get("amount", 0))
                )
                amount_due = bill_amount - _to_amount(bill.get("amount_paid", 0))
                amount_paid = _to_amount(bill.get("amount_paid", 0))  # display only

                confidence = None
                reason = ""

                # Match 1: Reference contains bill number → HIGH confidence
                if bill_number and (
                    bill_number in reference or bill_number in description
                ):
                    if amount == amount_due:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi + jumlah persis cocok"
                    elif amount == bill_amount:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi + jumlah total cocok"
                    else:
                        confidence = "MEDIUM"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi (jumlah berbeda)"

                # Match 2: Amount exact + vendor name in description → MEDIUM confidence
                elif (
                    vendor_name and len(vendor_name) > 2 and vendor_name in description
                ):
                    _vn_display = _safe_get_name(bill, "bill") or vendor_name
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Vendor {_vn_display} cocok + jumlah persis Rp {int(amount_due):,}".replace(
                            ",", "."
                        )
                    elif amount == bill_amount:
                        confidence = "MEDIUM"
                        reason = f"Vendor {_vn_display} cocok + jumlah total Rp {int(bill_amount):,}".replace(
                            ",", "."
                        )
                    elif 0 < amount < amount_due:
                        confidence = "LOW"
                        reason = f"Vendor {_vn_display} cocok, kemungkinan pembayaran sebagian (Rp {int(amount):,} dari Rp {int(amount_due):,})".replace(
                            ",", "."
                        )

                # Match 3: Amount exact match only → LOW confidence
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa tagihan {bill_number}".replace(
                        ",", "."
                    )

                if confidence:
                    suggestions.append(
                        {
                            "bill_id": bill.get("id"),
                            "bill_number": bill.get("invoice_number"),
                            "vendor_id": _safe_get_id(bill, "bill"),
                            "vendor_name": _safe_get_name(bill, "bill"),
                            "bill_amount": int(bill_amount),
                            "amount_due": int(amount_due),
                            "amount_paid": int(amount_paid),
                            "due_date": bill.get("due_date"),
                            "confidence": confidence,
                            "reason": reason,
                        }
                    )

            # Sort: HIGH > MEDIUM > LOW
            priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            suggestions.sort(key=lambda s: priority.get(s["confidence"], 3))

            return suggestions

        except Exception as e:
            logger.warning(f"[BillMatch] Error matching bills: {e}")
            return []

    async def _match_against_outstanding_invoices(
        self, statement_line: dict, headers: dict
    ) -> list[dict]:
        """
        Cross-reference a CREDIT statement line against outstanding sales invoices.
        Returns list of invoice suggestions sorted by confidence (HIGH first).
        READ-ONLY — Law 0 compliant.
        """
        base_url = "http://localhost:8000"
        amount = abs(_to_amount(statement_line.get("amount", 0)))
        description = (statement_line.get("description") or "").upper()
        reference = (statement_line.get("reference") or "").upper()

        if amount <= 0:
            return []

        try:

            async def _fetch(params):
                async with httpx.AsyncClient(timeout=10.0) as client:
                    return await client.get(
                        f"{base_url}/api/sales-invoices",
                        params=params,
                        headers=headers,
                    )

            resp_unpaid, resp_partial = await asyncio.gather(
                _fetch({"status": "unpaid", "limit": 50}),
                _fetch({"status": "partial", "limit": 50}),
            )

            unpaid_invoices = []
            for resp in [resp_unpaid, resp_partial]:
                if resp.status_code == 200:
                    inv_data = resp.json()
                    unpaid_invoices.extend(
                        inv_data.get("items", normalize_api_response(inv_data))
                    )

            if not unpaid_invoices:
                return []

            suggestions = []
            for inv in unpaid_invoices:
                inv_number = (inv.get("invoice_number") or "").upper()
                customer_name = _safe_get_name(inv, "invoice").upper()
                inv_amount = _to_amount(inv.get("amount", inv.get("total_amount", 0)))
                amount_due = _to_amount(inv.get("total_amount", 0)) - _to_amount(
                    inv.get("amount_paid", 0)
                )  # J-compliant: compute from journal-derived fields

                confidence = None
                reason = ""

                # Match 1: Reference contains invoice number -> HIGH
                if inv_number and (
                    inv_number in reference or inv_number in description
                ):
                    if amount == amount_due:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi + jumlah persis cocok"
                    elif amount == inv_amount:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi + jumlah total cocok"
                    else:
                        confidence = "MEDIUM"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi (jumlah berbeda)"

                # Match 2: Customer name in description + amount match -> MEDIUM
                elif (
                    customer_name
                    and len(customer_name) > 2
                    and customer_name in description
                ):
                    _cn_display = _safe_get_name(inv, "invoice") or customer_name
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {_cn_display} cocok + jumlah persis Rp {int(amount_due):,}".replace(
                            ",", "."
                        )
                    elif amount == inv_amount:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {_cn_display} cocok + jumlah total Rp {int(inv_amount):,}".replace(
                            ",", "."
                        )

                # Match 3: Amount exact match only -> LOW
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa piutang {inv_number}".replace(
                        ",", "."
                    )

                if confidence:
                    suggestions.append(
                        {
                            "invoice_id": inv.get("id"),
                            "invoice_number": inv.get("invoice_number"),
                            "customer_id": _safe_get_id(inv, "invoice"),
                            "customer_name": _safe_get_name(inv, "invoice"),
                            "invoice_amount": int(inv_amount),
                            "amount_due": int(amount_due),
                            "due_date": inv.get("due_date"),
                            "confidence": confidence,
                            "reason": reason,
                        }
                    )

            # Sort: HIGH > MEDIUM > LOW
            priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            suggestions.sort(key=lambda s: priority.get(s["confidence"], 3))
            return suggestions

        except Exception as e:
            logger.warning(f"[InvoiceMatch] Error matching invoices: {e}")
            return []

    async def _resolve_coa_id(self, account_code: str, headers: dict) -> str | None:
        """Resolve account_code -> account_id via CoA API (Law 27 - no hardcoded IDs)."""
        base_url = "http://localhost:8000"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{base_url}/api/accounts",
                    params={"search": account_code, "limit": 5},
                    headers=headers,
                )
            if resp.status_code == 200:
                data = resp.json()
                items = normalize_api_response(data)
                # Exact code match (search is ILIKE, so verify exact)
                for item in items:
                    if item.get("code") == account_code:
                        return item.get("id")
                # Fallback: first result if only one
                if len(items) == 1:
                    return items[0].get("id")
            logger.warning(
                f"[AutoCat] CoA code '{account_code}' not found for tenant - "
                "pattern will be skipped."
            )
            return None
        except Exception as e:
            logger.warning(f"[AutoCat] CoA resolution error for '{account_code}': {e}")
            return None

    async def _auto_categorize(
        self, statement_line: dict, headers: dict
    ) -> dict | None:
        """
        Match statement line description against recon_category_patterns.
        Returns category suggestion or None.
        Queries: tenant-specific + system defaults (Law 24 RLS handles filtering).
        """
        description = (statement_line.get("description") or "").upper()
        if not description:
            return None

        try:
            from .db_utils import get_session_db_pool  # noqa: E402

            pool = await get_session_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    self.context.tenant_id,
                )
                patterns = await conn.fetch(
                    """
                    SELECT pattern_regex, account_code, description
                    FROM recon_category_patterns
                    WHERE (tenant_id = $1 OR (tenant_id IS NULL AND is_system_default = true))
                    ORDER BY priority DESC
                """,
                    self.context.tenant_id,
                )

            for row in patterns:
                try:
                    if re.search(row["pattern_regex"], description, re.IGNORECASE):
                        # Resolve account_code -> account_id via CoA (Law 27)
                        account_id = await self._resolve_coa_id(
                            row["account_code"], headers
                        )
                        if account_id:
                            return {
                                "account_code": row["account_code"],
                                "account_name": row["description"],
                                "account_id": account_id,
                                "pattern_matched": row["pattern_regex"],
                                "confidence": "PATTERN",
                            }
                        # account_id is None -> CoA code doesn't exist for this tenant
                except re.error:
                    logger.warning(
                        f"[AutoCat] Bad regex pattern: {row['pattern_regex']}"
                    )
                    continue

            return None

        except Exception as e:
            logger.warning(f"[AutoCat] Error: {e}")
            return None

    async def _execute_review_next_unmatched(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Fetch next unmatched statement line + best suggestion.
        READ-ONLY — does NOT modify any data (Law 0 compliant).
        """
        session_id = params.get("session_id")
        skip = params.get("skip", 0)

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")

        try:
            base_url = "http://localhost:8000"
            headers = self._build_headers()

            # GET unmatched statement lines (read-only)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/statements",
                    params={"match_status": "unmatched", "offset": skip, "limit": 1},
                    headers=headers,
                )

            if resp.status_code != 200:
                return _error(
                    "FETCH_FAILED", f"Gagal mengambil data: HTTP {resp.status_code}"
                )

            data = resp.json()
            lines = normalize_api_response(data)

            if not lines:
                # No more unmatched items
                return {
                    "success": True,
                    "data": {
                        "has_more": False,
                        "message": "Semua item sudah di-review. Tidak ada lagi yang belum cocok.",
                        "session_id": session_id,
                    },
                }

            line = lines[0]

            # Count total unmatched
            total_unmatched = data.get("total", data.get("count", len(lines)))

            # Try to get best suggestion from auto-match (read-only GET)
            suggestion = None
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    suggest_resp = await client.get(
                        f"{base_url}/api/bank-reconciliation/sessions/{session_id}/suggestions",
                        params={"statement_line_id": line["id"], "limit": 1},
                        headers=headers,
                    )
                if suggest_resp.status_code == 200:
                    suggest_data = suggest_resp.json()
                    suggestions = suggest_data.get(
                        "data", suggest_data.get("suggestions", [])
                    )
                    if suggestions:
                        suggestion = suggestions[0]
            except Exception:
                pass  # Suggestions are optional, don't fail

            # For DEBIT lines without a strong bank_tx suggestion: cross-reference outstanding bills
            bill_suggestion = None
            is_credit = line.get("is_credit", False)
            if not is_credit:  # debit = money out → potential bill payment
                try:
                    bill_matches = await self._match_against_outstanding_bills(
                        line, headers
                    )
                    if bill_matches:
                        best = bill_matches[0]
                        # Prefer bill match over weak bank_tx suggestion
                        if not suggestion or best["confidence"] in ("HIGH", "MEDIUM"):
                            bill_suggestion = best
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Bill matching failed (non-fatal): {e}"
                    )

            # Invoice matching for CREDIT lines
            invoice_suggestion = None
            invoice_matches = []
            if is_credit:  # credit = money in → potential invoice payment
                try:
                    invoice_matches = await self._match_against_outstanding_invoices(
                        line, headers
                    )
                    if invoice_matches:
                        best_inv = invoice_matches[0]
                        # Prefer invoice match over weak bank_tx suggestion
                        if not suggestion or best_inv["confidence"] in (
                            "HIGH",
                            "MEDIUM",
                        ):
                            invoice_suggestion = best_inv
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Invoice matching failed (non-fatal): {e}"
                    )

            # Auto-categorize: only if no bill/invoice match found
            category_suggestion = None
            if not bill_suggestion and not invoice_suggestion:
                try:
                    category_suggestion = await self._auto_categorize(line, headers)
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Auto-categorize failed (non-fatal): {e}"
                    )

            # Multi-invoice allocation
            if invoice_matches and invoice_suggestion:
                try:
                    line_amount = abs(_to_amount(line.get("amount", 0)))
                    allocation = find_allocation_options(invoice_matches, line_amount)
                    if allocation["type"] == "multi":
                        invoice_suggestion = {
                            **invoice_suggestion,
                            "allocation_type": "multi",
                            "allocations": [
                                {
                                    "invoice_id": m["invoice_id"],
                                    "amount_applied": int(_to_amount(m["amount_due"])),
                                }
                                for m in allocation["allocation"]
                            ],
                        }
                    elif allocation["type"] == "needs_user_input":
                        invoice_suggestion = {
                            **invoice_suggestion,
                            "allocation_type": "needs_user_input",
                            "options": allocation["options"],
                        }
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Allocation logic failed (non-fatal): {e}"
                    )

            return {
                "success": True,
                "data": {
                    "has_more": True,
                    "remaining": max(total_unmatched - 1, 0),
                    "position": skip + 1,
                    "statement_line": {
                        "id": line.get("id"),
                        "date": line.get("transaction_date") or line.get("date"),
                        "description": line.get("description"),
                        "reference": line.get("reference"),
                        "amount": line.get("amount"),
                        "type": "credit" if line.get("is_credit") else "debit",
                    },
                    "suggestion": {
                        "transaction_id": suggestion.get("transaction_id")
                        or suggestion.get("id"),
                        "description": suggestion.get("description"),
                        "amount": suggestion.get("amount"),
                        "date": suggestion.get("transaction_date")
                        or suggestion.get("date"),
                        "confidence": suggestion.get("confidence")
                        or suggestion.get("score"),
                        "match_reason": suggestion.get("match_reason")
                        or suggestion.get("reason"),
                    }
                    if suggestion
                    else None,
                    "bill_suggestion": {
                        "type": "bill_payment",
                        "bill_id": bill_suggestion["bill_id"],
                        "bill_number": bill_suggestion["bill_number"],
                        "vendor_id": bill_suggestion["vendor_id"],
                        "vendor_name": bill_suggestion["vendor_name"],
                        "bill_amount": bill_suggestion["bill_amount"],
                        "amount_due": bill_suggestion["amount_due"],
                        "due_date": bill_suggestion["due_date"],
                        "confidence": bill_suggestion["confidence"],
                        "match_reason": bill_suggestion["reason"],
                    }
                    if bill_suggestion
                    else None,
                    "invoice_suggestion": {
                        "type": "receive_payment",
                        "invoice_id": invoice_suggestion["invoice_id"],
                        "invoice_number": invoice_suggestion["invoice_number"],
                        "customer_id": invoice_suggestion["customer_id"],
                        "customer_name": invoice_suggestion["customer_name"],
                        "invoice_amount": invoice_suggestion["invoice_amount"],
                        "amount_due": invoice_suggestion["amount_due"],
                        "due_date": invoice_suggestion["due_date"],
                        "confidence": invoice_suggestion["confidence"],
                        "match_reason": invoice_suggestion["reason"],
                        "all_matches": invoice_matches[:5] if invoice_matches else [],
                    }
                    if invoice_suggestion
                    else None,
                    "category_suggestion": category_suggestion,
                    "session_id": session_id,
                },
            }

        except httpx.TimeoutException:
            return _error(
                "TIMEOUT", "Request timeout saat mengambil data rekonsiliasi."
            )
        except Exception as e:
            logger.exception(f"[ReviewNextUnmatched] Error: {e}")
            return _error("REVIEW_ERROR", f"Error: {str(e)[:200]}")

    # --- Agentic Reconcile (READ-ONLY — auto-match analysis) ---

    async def _execute_agentic_reconcile(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run automated matching analysis for a reconciliation session.
        READ-ONLY analysis — does NOT require user confirmation.
        Calls POST /api/bank-reconciliation/sessions/{session_id}/agentic-reconcile
        """
        session_id = params.get("session_id")

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")

        try:
            base_url = "http://localhost:8000"
            headers = self._build_headers()

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/agentic-reconcile",
                    headers=headers,
                    json={
                        "max_actions": 50,
                        "include_categorize": True,
                        "include_exclude": True,
                    },
                )

            if resp.status_code != 200:
                return _error(
                    "AGENTIC_RECONCILE_FAILED",
                    f"Gagal menjalankan automatch: HTTP {resp.status_code} - {resp.text[:200]}",
                )

            data = resp.json()
            action_plan = data.get("action_plan", {})
            session_stats = data.get("session_stats", {})

            # Summarize results for the agent
            actions = action_plan.get("actions", [])
            match_count = sum(1 for a in actions if a.get("action_type") == "match")
            categorize_count = sum(
                1 for a in actions if a.get("action_type") == "categorize"
            )
            exclude_count = sum(1 for a in actions if a.get("action_type") == "exclude")

            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "summary": action_plan.get("summary", ""),
                    "total_actions": action_plan.get("total_actions", 0),
                    "matched_count": match_count,
                    "categorize_count": categorize_count,
                    "exclude_count": exclude_count,
                    "estimated_resolution": action_plan.get("estimated_resolution", 0),
                    "actions": actions,
                    "session_stats": session_stats,
                },
            }

        except httpx.TimeoutException:
            return _error(
                "TIMEOUT", "Request timeout saat menjalankan automatch rekonsiliasi."
            )
        except Exception as e:
            logger.exception(f"[AgenticReconcile] Error: {e}")
            return _error("AGENTIC_RECONCILE_ERROR", f"Error: {str(e)[:200]}")

    # --- Bank Statement Import Execution ---

    async def _execute_create_recon_session(self, params: dict) -> dict:
        # Code-level enforcement: statement_ending_balance is REQUIRED
        if params.get("statement_ending_balance") is None:
            return {
                "success": False,
                "error": "statement_ending_balance wajib diisi. Tanyakan saldo akhir rekening koran ke user sebelum membuat session rekonsiliasi.",
            }
        """Create or reuse a reconciliation session for a bank account."""
        from datetime import date as date_type
        import httpx  # noqa: E402

        account_id = params.get("account_id", "")
        if not account_id:
            return {"success": False, "error": "Parameter account_id wajib diisi."}

        base_url = "http://localhost:8000"
        headers = self._build_headers()

        # --- Step 1: Check for existing active session ---
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}/api/bank-reconciliation/sessions",
                    params={"account_id": account_id, "status": "in_progress"},
                    headers=headers,
                )
            if resp.status_code == 200:
                raw = resp.json()
                sessions = raw.get("data", raw) if isinstance(raw, dict) else raw
                if isinstance(sessions, list) and len(sessions) > 0:
                    existing = sessions[0]
                    session_id = existing.get("id", existing.get("session_id", ""))
                    return {
                        "success": True,
                        "data": {
                            "session_id": session_id,
                            "status": existing.get("status", "in_progress"),
                            "mode": existing.get("mode", "import"),
                            "message": f"Menggunakan session rekonsiliasi yang sudah ada (ID: {session_id}).",
                            "existing": True,
                        },
                    }
        except Exception as e:
            logger.warning(f"Check existing session failed (non-critical): {e}")

        # --- Step 2: Create new session ---
        today = date_type.today().isoformat()
        first_of_month = date_type.today().replace(day=1).isoformat()

        body = {
            "account_id": account_id,
            "statement_date": today,
            "statement_start_date": params.get("statement_start_date", first_of_month),
            "statement_end_date": params.get("statement_end_date", today),
            "statement_beginning_balance": params.get("statement_beginning_balance", 0),
            "statement_ending_balance": params.get("statement_ending_balance"),
            "mode": params.get("mode", "import"),
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions",
                    json=body,
                    headers=headers,
                )

            if resp.status_code >= 400:
                error_text = resp.text[:300]
                if (
                    "sudah ada" in error_text.lower()
                    or "already" in error_text.lower()
                    or "in_progress" in error_text.lower()
                ):
                    return {
                        "success": False,
                        "error": "Sudah ada session rekonsiliasi aktif untuk akun ini. Gunakan session yang ada.",
                    }
                return {
                    "success": False,
                    "error": f"Gagal buat session rekonsiliasi: {error_text}",
                }

            data = resp.json()
            session_id = data.get("id", data.get("session_id", ""))
            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "status": data.get("status", "in_progress"),
                    "mode": data.get("mode", "import"),
                    "message": f"Session rekonsiliasi berhasil dibuat (ID: {session_id}). Siap untuk import file.",
                },
            }
        except Exception as e:
            logger.error(f"Create recon session error: {e}")
            return {"success": False, "error": f"Gagal membuat session: {str(e)}"}

    async def _execute_import_bank_statement(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Import a bank statement file into a reconciliation session.
        Calls the existing /api/bank-reconciliation/sessions/{id}/import endpoint.
        When no column mapping is provided in config, auto-detects columns first.
        """
        session_id = params.get("session_id")
        file_path = params.get("file_path")
        config = params.get("config", {})

        # Resolve opaque file_ref if provided (preferred over raw file_path)
        file_ref = params.get("file_ref", "")
        if file_ref:
            from utils.file_ref import resolve_file_ref

            resolved = resolve_file_ref(file_ref, self.context.tenant_id)
            if resolved:
                file_path = resolved
                logger.info(f"Resolved file_ref '{file_ref}' -> '{file_path}'")
            else:
                return _error(
                    "INVALID_FILE_REF",
                    f"File reference tidak valid atau file tidak ditemukan: {file_ref}",
                )

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")
        if not file_path:
            return _error("MISSING_FILE_PATH", "Parameter 'file_path' wajib diisi.")

        import os  # noqa: E402

        if not os.path.exists(file_path):
            return _error("FILE_NOT_FOUND", f"File tidak ditemukan: {file_path}")

        # Auto-detect format from extension
        ext = os.path.splitext(file_path)[1].lower()
        if not config.get("format"):
            format_map = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx", ".ofx": "ofx"}
            config["format"] = format_map.get(ext, "csv")

        try:
            # Read file content
            with open(file_path, "rb") as f:
                file_content = f.read()

            import httpx  # noqa: E402

            base_url = "http://localhost:8000"
            headers = self._build_headers()

            # ── Auto-detect columns when no mapping provided ──────────────
            has_column_mapping = any(
                config.get(k)
                for k in (
                    "date_column",
                    "description_column",
                    "amount_column",
                    "debit_column",
                    "credit_column",
                )
            )
            # Only auto-detect for CSV/XLSX (not OFX which has fixed structure)
            if not has_column_mapping and config.get("format") in ("csv", "xlsx"):
                logger.info(
                    "[ImportBankStatement] No column mapping in config, auto-detecting..."
                )
                try:
                    # Direct call to auto_detect_columns — bypasses WAF
                    import pandas as pd  # noqa: E402
                    import io as _io  # noqa: E402

                    filename_lower = os.path.basename(file_path).lower()
                    if filename_lower.endswith((".xlsx", ".xls")):
                        df = pd.read_excel(_io.BytesIO(file_content), nrows=20)
                    else:
                        df = pd.read_csv(_io.BytesIO(file_content), nrows=20)

                    columns = [str(c) for c in df.columns.tolist()]
                    sample_rows = []
                    for _, row in df.iterrows():
                        sample_rows.append(
                            [str(v) if pd.notna(v) else "" for v in row.tolist()]
                        )

                    from ..column_mapper import auto_detect_columns  # noqa: E402
                    from ..unified_agent.db_utils import get_session_db_pool  # noqa: E402

                    pool = await get_session_db_pool()
                    detect_result = await auto_detect_columns(
                        tenant_id=self.context.tenant_id,
                        columns=columns,
                        sample_rows=sample_rows,
                        pool=pool,
                    )
                    import_config = detect_result.to_import_config()
                    confidence = detect_result.overall_confidence
                    source = detect_result.source
                    logger.info(
                        f"[ImportBankStatement] Auto-detected columns "
                        f"(confidence={confidence}, source={source}): {import_config}"
                    )
                    # Merge detected config into user config (user values take precedence)
                    for key, value in import_config.items():
                        if not config.get(key):
                            config[key] = value
                except Exception as detect_err:
                    logger.warning(
                        f"[ImportBankStatement] Column detection error: {detect_err}"
                    )
                    # Continue with import anyway — the import endpoint has its own defaults

            # ── Call import endpoint ──────────────────────────────────────
            # Remove Content-Type for multipart — let httpx set boundary automatically
            import_headers = {
                k: v for k, v in headers.items() if k.lower() != "content-type"
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/import",
                    headers=import_headers,
                    files={
                        "file": (
                            os.path.basename(file_path),
                            file_content,
                            "application/octet-stream",
                        )
                    },
                    data={"config": json.dumps(config)},
                )

            if response.status_code in (200, 201):
                result = response.json()
                data = result.get("data", result)
                return {
                    "success": True,
                    "data": {
                        "lines_imported": data.get("lines_imported", 0),
                        "lines_skipped": data.get("lines_skipped", 0),
                        "total_debits": data.get("total_debits", 0),
                        "total_credits": data.get("total_credits", 0),
                        "date_range": data.get("date_range"),
                        "errors": data.get("errors", []),
                        "session_id": session_id,
                    },
                    "message": (
                        f"Berhasil import {data.get('lines_imported', 0)} baris statement bank. "
                        f"Total debit: Rp {data.get('total_debits', 0):,.0f}, "
                        f"Total kredit: Rp {data.get('total_credits', 0):,.0f}."
                    ),
                }
            else:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get(
                        "detail", error_json.get("message", response.text)
                    )
                except Exception:
                    pass
                return _error("IMPORT_FAILED", f"Import gagal: {error_detail}")

        except Exception as e:
            logger.exception(f"[ImportBankStatement] Error: {e}")
            return _error("IMPORT_ERROR", f"Error saat import: {str(e)[:200]}")

    async def _execute_read(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a read tool via httpx GET to kernel API."""
        endpoint = get_endpoint_for_tool(tool_name)
        if not endpoint:
            return _error("NO_ENDPOINT", f"No endpoint for {tool_name!r}.")

        if endpoint["method"] != "GET":
            logger.error(f"BLOCKED: Non-GET tool: {tool_name}")
            return _error("METHOD_BLOCKED", "Hanya GET yang diizinkan.")

        path = endpoint["path"]
        query_params = {}

        # Default: exclude draft & void for bills/invoices (hutang/piutang accuracy)
        if tool_name in ("get_bills", "get_invoices"):
            _has_status = "status" in params
            _status_val = params.get("status", "NOT_SET")
            logger.warning(
                "[ACTIVE_FILTER] tool=%s has_status=%s status_val=%s",
                tool_name,
                _has_status,
                _status_val,
            )
            if not _has_status:
                query_params["status"] = "active"
                logger.warning(
                    "[ACTIVE_FILTER] Injecting status=active for %s", tool_name
                )

        for key, value in params.items():
            if value is None:
                continue
            placeholder = "{" + key + "}"
            if placeholder in path:
                # Only validate UUID for ID-type params, not dates/periods
                if key.endswith("_id") or key == "id":
                    if not _is_valid_uuid(str(value)):
                        return _error(
                            "INVALID_UUID", f"Parameter {key!r} bukan UUID valid."
                        )
                path = path.replace(placeholder, str(value))
            else:
                if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
                    value = value[:MAX_STRING_LENGTH]
                query_params[key] = value

        url = f"{KERNEL_BASE_URL}{path}"
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
            resp = await client.get(url, params=query_params, headers=headers)
            if resp.status_code >= 400:
                return {
                    "success": False,
                    "error": f"API returned {resp.status_code}: {resp.text[:200]}",
                    "error_type": "API_ERROR",
                    "status_code": resp.status_code,
                }
            data = resp.json()

        result = normalize_api_response_or_dict(data)

        result = _truncate_result(result)
        return {"success": True, "data": result}

    # =========================================================
    # ENRICHMENT LAYER
    #
    # Architecture (Law 0 compliant):
    #   LLM  = resolve intent (WHO, WHAT, HOW MUCH)
    #   HERE = translate to kernel schema (names, defaults, descriptions)
    #   Kernel = validate + execute
    #
    # 3 Enrichment Laws:
    #   1. Never override LLM intent (backfill only)
    #   2. Data completion only (no tax calc, no validation)
    #   3. Registry-based dispatch (no scattered if/else)
    # =========================================================

    def _build_headers(self) -> Dict[str, str]:
        """Build auth headers for kernel API calls."""
        return {
            "Authorization": f"Bearer {self.context.auth_token}",
            "X-Tenant-ID": self.context.tenant_id,
            "Content-Type": "application/json",
        }

    async def _fetch_entity(
        self, client: httpx.AsyncClient, path: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a single entity from kernel API.
        Returns the entity dict or None on failure.
        """
        try:
            resp = await client.get(
                f"{KERNEL_BASE_URL}{path}",
                headers=self._build_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                # Handle wrapped response: {"data": {...}} or flat {...}
                if isinstance(data, dict):
                    normalized = normalize_api_response_or_dict(data)
                    # FIX_AQUA_UNWRAP 2026-05-09: detail endpoints return
                    # {"success":true,"data":{...}}. normalize() above only
                    # unwraps when "data" is a LIST. For dict-shaped detail
                    # responses we unwrap here so callers always see the
                    # entity body directly. Guarded by `success` key to
                    # avoid false-positive on flat payloads that happen to
                    # contain a "data" dict field.
                    if (
                        isinstance(normalized, dict)
                        and "success" in normalized
                        and isinstance(normalized.get("data"), dict)
                    ):
                        return normalized["data"]
                    return normalized
        except Exception as e:
            logger.warning(f"Entity lookup failed for {path}: {e}")
        return None

    @staticmethod
    def _check_missing_item_prices(
        payload: Dict[str, Any], action_key: str
    ) -> List[Dict[str, Any]]:
        """FIX_AQUA_PRICE_ASK 2026-05-09 — Iron Law 16/19 guard.

        After enrichment, detect items[] entries that landed at price=0 because
        the item master has no purchase/sales price set. Returning a list of
        missing-price items triggers a multi-turn ask-fill flow in orchestrator
        BEFORE propose_direct, so user is asked for the actual price instead of
        silently posting a 0-amount line (corrupts WAC + AP/AR per Law 16, or
        fails Pydantic gt=0 on Bill V2 = bad UX).

        Field naming differs by intent:
          - sales_invoice / sales_order / quote / credit_note → unit_price
          - bill (purchase_invoice V2)                        → price
        """
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return []

        if action_key in ("create_bill", "create_purchase_invoice"):
            price_field = "price"
            qty_field = "qty"
            name_field = "product_name"
            price_label = "harga beli"
        else:
            price_field = "unit_price"
            qty_field = "quantity"
            name_field = "description"
            price_label = "harga jual"

        missing: List[Dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            try:
                _p = float(item.get(price_field) or 0)
            except (TypeError, ValueError):
                _p = 0.0
            # F2 2026-08-11: JUMLAH juga wajib ditanya, bukan hanya harga.
            #
            # "jasa sablon 2 warna 500 ribu" — "2" adalah jumlah WARNA, bukan
            # jumlah barang. Sampai commit ini bot tetap mengusulkan kartu,
            # user menekan Konfirmasi, dan endpoint menolak dengan galat
            # Pydantic mentah (qty: Field(..., gt=0)). Kartu itu tak pernah
            # bisa berhasil sejak lahir.
            #
            # Kenapa qty dan BUKAN harga/tanggal/unit: qty tak bisa diturunkan
            # dari mana pun. Harga ada di master (terbukti: purchase_price
            # terambil tanpa user menyebutnya), tanggal punya default, unit
            # punya default. Menanyakan yang bisa disimpulkan akan mengubah
            # chatmode jadi wawancara — itu merusak keunggulannya atas
            # dashboard.
            _q_raw = item.get(qty_field, item.get("quantity"))
            try:
                _q_num = float(_q_raw) if _q_raw is not None else 0.0
            except (TypeError, ValueError):
                _q_num = 0.0
            _qty_kurang = _q_num <= 0
            if _p > 0 and not _qty_kurang:
                continue
            try:
                _q = int(float(item.get(qty_field) or 1))
            except (TypeError, ValueError):
                _q = 1
            _fc_list = item.get("_fuzzy_candidates") if isinstance(item, dict) else None
            missing.append(
                {
                    "idx": idx,
                    "name": str(item.get(name_field) or "Item").strip() or "Item",
                    "qty": _q,
                    "price_field": price_field,
                    "price_label": price_label,
                    # "jumlah" → tanya qty (harga bisa dari master);
                    # "harga"  → perilaku lama, tak berubah.
                    "kurang": "jumlah" if _qty_kurang else "harga",
                    # FIX_AQUA_ITEM_RESOLVE 2026-05-19: surface fuzzy candidates
                    # so price-ask renderer can show "Mirip dengan: ..." hint.
                    "fuzzy_candidates": (
                        list(_fc_list) if isinstance(_fc_list, list) else []
                    ),
                }
            )
        return missing

    async def _enrich_payload(
        self, action_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch to action-specific enrichment.
        Registry-based: add new action types by adding a method + registry entry.
        Includes structured logging for observability (zero side effects).
        """
        import time as _time  # noqa: E402

        _t0 = _time.monotonic()

        # Snapshot before enrichment
        _before_keys = set(payload.keys())
        _before_vals = {k: repr(v)[:80] for k, v in payload.items()}
        _before_item_keys = []
        _before_item_vals = []
        for it in payload.get("items", []):
            if isinstance(it, dict):
                _before_item_keys.append(set(it.keys()))
                _before_item_vals.append({k: repr(v)[:80] for k, v in it.items()})

        enricher_name = ACTION_ENRICHMENT.get(action_type)

        if enricher_name is None:
            elapsed_ms = int((_time.monotonic() - _t0) * 1000)
            logger.info(
                "[ENRICH] %s | no enrichment needed (not in registry) | %dms",
                action_type,
                elapsed_ms,
            )
            return payload

        if not hasattr(self, enricher_name):
            logger.warning(
                "[ENRICH] WARNING: %s maps to %s but method not found",
                action_type,
                enricher_name,
            )
            return payload

        enricher = getattr(self, enricher_name)
        result = await enricher(payload)

        # T181 FASE 1 2026-08-30: SATU-SATUNYA situs yang menerbitkan
        # [T181_TOLAK] (pelajaran T178: dua penerbit = satu kegagalan tampak
        # dobel). Isi string TIDAK dicetak — hanya action_key + panjang.
        if isinstance(result, dict) and result.get("_t181_items_mentah"):
            logger.warning(
                "[T181_TOLAK] action=%s len=%d",
                action_type,
                len(str(result.get("_t181_items_mentah"))),
            )

        # --- Diff before vs after for structured log ---
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        _after_keys = set(result.keys())

        backfilled = []
        skipped = []
        renamed = []

        # Top-level: new keys = backfilled
        top_new = _after_keys - _before_keys
        top_gone = _before_keys - _after_keys
        for k in sorted(top_new):
            backfilled.append(k)
        # Top-level: removed keys = renamed (find target)
        for k in sorted(top_gone):
            old_val = _before_vals.get(k)
            found = False
            for nk in sorted(top_new):
                if repr(result.get(nk))[:80] == old_val:
                    renamed.append(k + " -> " + nk)
                    if nk in backfilled:
                        backfilled.remove(nk)
                    found = True
                    break
            if not found:
                renamed.append(k + " -> (removed)")
        # Top-level: unchanged keys = skipped (LLM provided)
        for k in sorted(_before_keys & _after_keys):
            if k != "items" and _before_vals.get(k) == repr(result.get(k))[:80]:
                skipped.append(k)

        # Item-level field changes (value-based rename detection)
        _after_item_keys = []
        _after_item_vals = []
        for it in result.get("items", []):
            if isinstance(it, dict):
                _after_item_keys.append(set(it.keys()))
                _after_item_vals.append({k: repr(v)[:80] for k, v in it.items()})
        for idx in range(min(len(_before_item_keys), len(_after_item_keys))):
            bef = _before_item_keys[idx]
            aft = _after_item_keys[idx]
            bef_v = _before_item_vals[idx] if idx < len(_before_item_vals) else {}
            aft_v = _after_item_vals[idx] if idx < len(_after_item_vals) else {}
            item_new = sorted(aft - bef)
            item_gone = sorted(bef - aft)
            # Pair renames by matching values (gone key value == new key value)
            used_new = set()
            for gk in item_gone:
                old_val = bef_v.get(gk)
                found_rename = False
                if old_val is not None:
                    for nk in item_new:
                        if nk not in used_new and aft_v.get(nk) == old_val:
                            renamed.append("items[%d].%s -> %s" % (idx, gk, nk))
                            used_new.add(nk)
                            found_rename = True
                            break
                if not found_rename:
                    renamed.append("items[%d].%s -> (removed)" % (idx, gk))
            # Remaining new keys = backfilled
            for nk in item_new:
                if nk not in used_new:
                    backfilled.append("items[%d].%s" % (idx, nk))

        bf_str = ", ".join(backfilled) if backfilled else "none"
        sk_str = ", ".join(skipped) if skipped else "none"
        rn_str = ", ".join(renamed) if renamed else "none"

        logger.info(
            "[ENRICH] %s | backfilled: %s | skipped: %s | renamed: %s | %dms",
            action_type,
            bf_str,
            sk_str,
            rn_str,
            elapsed_ms,
        )

        # T168 2026-08-29: satu-satunya pemanggilan penanda yatim, di
        # dispatcher — bukan di tiap enricher — supaya satu kegagalan tak
        # pernah terbit dua kali (pelajaran T178).
        self._log_orphan_items(result, action_type)

        return result

    # --- Shared enrichment helpers ---

    def _backfill_top_item_id(self, payload: Dict[str, Any], action_key: str = "?") -> None:
        """T168 2026-08-29: backfill item_id tingkat-atas HANYA ke items[0].

        Bentuk lama di 6 situs melakukan `for item in items` — persis kebalikan
        dari komentarnya sendiri ("into items[0]"). Ekstraktor kadang memancarkan
        `item_name` TINGKAT-ATAS yang isinya nama barang BARIS PERTAMA saja;
        _resolve_item menghasilkan satu payload["item_id"], lalu loop lama
        menempelkannya ke SETIAP baris yang belum punya id. _enrich_items
        kemudian menimpa description/product_name dengan nama master, sehingga
        baris ke-2 kehilangan identitasnya SEBELUM resolve per-baris pernah
        jalan (ciri khas: qty & price baris 2 tetap utuh).

        Dokumen satu-baris: perilaku identik dengan sebelumnya (items[0] = satu-
        satunya baris). Itulah kontrol negatif utama perubahan ini.
        """
        # T168-R2 2026-08-29: penanda PEMICU. LOG-ONLY, nol perubahan perilaku.
        # SATU-SATUNYA situs yang menerbitkan [T168_PICU] (pelajaran T178).
        items = payload.get("items")
        _n = len(items) if isinstance(items, list) else -1
        _top = {
            _k: payload.get(_k)
            for _k in ("item_id", "product_id", "item_name")
            if payload.get(_k) is not None
        }

        def _picu(diterapkan, alasan, ditulis):
            logger.info(
                "[T168_PICU] action=%s baris=%s atas=%r diterapkan=%s alasan=%s ditulis=%s",
                action_key,
                _n,
                _top,
                diterapkan,
                alasan,
                ditulis,
            )

        if not items or not isinstance(items, list):
            _picu("TIDAK", "items_kosong_atau_bukan_list", "-")
            return
        _top_item_id = payload.get("item_id")
        if not _top_item_id:
            _picu("TIDAK", "payload.item_id_kosong", "-")
            return
        _first = items[0]
        if not isinstance(_first, dict):
            _picu("TIDAK", "items[0]_bukan_dict", "-")
            return
        if _first.get("item_id"):
            _picu("TIDAK", "items[0].item_id_sudah_terisi", "-")
            return
        _first["item_id"] = _top_item_id
        _picu("YA", "backfill_items[0]", "items[0].item_id")

    def _log_orphan_items(self, payload: Dict[str, Any], action_key: str) -> None:
        """T168 2026-08-29: SATU-SATUNYA situs yang menerbitkan [T168_YATIM].

        Sengaja hanya satu situs: T178 menunjukkan satu kegagalan bisa muncul
        dua kali di log kalau penanda diterbitkan dari dua tempat.
        """
        items = payload.get("items")
        if not items or not isinstance(items, list):
            return
        _n = len(items)
        for _idx, _it in enumerate(items):
            if not isinstance(_it, dict):
                continue
            if _it.get("item_id") or _it.get("product_id"):
                continue
            _typed = (
                _it.get("description")
                or _it.get("product_name")
                or _it.get("item_name")
                or _it.get("name")
                or ""
            )
            logger.info(
                "[T168_YATIM] action=%s baris=%d dari=%d nama_user=%r",
                action_key,
                _idx,
                _n,
                _typed,
            )

    async def _enrich_items(
        self,
        payload: Dict[str, Any],
        client: httpx.AsyncClient,
        price_keys: tuple = ("sales_price", "harga_jual", "selling_price"),
    ) -> Dict[str, Any]:
        """Shared: enrich item descriptions + unit_price from item master data.

        price_keys controls which DB column(s) to pull for unit_price backfill.
        Default = sales domain (sales_price/harga_jual/selling_price).
        Purchase callers (PO, bills) MUST pass price_keys=("purchase_price",
        "harga_beli") so cost (not selling) is pulled for AP-side documents.
        """
        items = payload.get("items", [])
        if not items or not isinstance(items, list):
            return payload

        user_text = getattr(self, "user_text", "") or ""

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id")

            # BUG-item-slot fix (2026-05-07): reverse lookup mirror of BUG-02 customer pattern
            if not item_id:
                name_hint = (
                    item.get("description") or item.get("item_name") or item.get("name")
                )
                if name_hint and isinstance(name_hint, str) and name_hint.strip():
                    name_hint = name_hint.strip()
                    # Mention check: name_hint must appear (token-wise) in user_text;
                    # else it is likely customer/vendor name bleed.
                    _ut_lower = user_text.lower()
                    _hint_tokens = [t for t in name_hint.lower().split() if len(t) >= 2]
                    _mentioned = bool(_hint_tokens) and all(
                        t in _ut_lower for t in _hint_tokens
                    )
                    if _mentioned:
                        search_resp = await self._fetch_entity(
                            client,
                            f"/api/items?search={name_hint}&limit=5&status=active",
                        )
                        results = []
                        if search_resp:
                            results = (
                                search_resp
                                if isinstance(search_resp, list)
                                else search_resp.get("items", [])
                            )
                        if not results:
                            # No master match. If name_hint looks like a proper noun
                            # (Capitalized multi-word), treat as customer-bleed and null out.
                            import re as _re

                            if _re.match(
                                r"^[A-Z][a-zA-ZÀ-ÿ']+(?:\s+[A-Z][a-zA-ZÀ-ÿ']+)+$",
                                name_hint,
                            ):
                                logger.info(
                                    f"BUG-item-slot: Nulled proper-noun bleed (no master match) name={name_hint}"
                                )
                                item["item_id"] = None
                                item["description"] = None
                            # Else preserve free-text description (e.g. "jasa konsultasi")
                            # FIX_AQUA_ITEM_RESOLVE 2026-05-19 (Layer B):
                            # Per-token fallback to surface fuzzy candidates.
                            # Bot will preserve item_id=None + description=name_hint
                            # but stash candidate names for the price-ask UX to display.
                            else:
                                _fc: list = []
                                _seen_ids: set = set()
                                for _tok in [
                                    t for t in name_hint.split() if len(t) >= 3
                                ][:3]:
                                    _tr = await self._fetch_entity(
                                        client,
                                        f"/api/items?search={_tok}&limit=5&status=active",
                                    )
                                    _trows = (
                                        _tr
                                        if isinstance(_tr, list)
                                        else (_tr.get("items", []) if _tr else [])
                                    )
                                    for _r in _trows:
                                        _rid = _r.get("id") or _r.get("item_id")
                                        _rname = (
                                            _r.get("name")
                                            or _r.get("nama_produk")
                                            or ""
                                        ).strip()
                                        if _rid and _rid not in _seen_ids and _rname:
                                            _seen_ids.add(_rid)
                                            _fc.append(_rname)
                                        if len(_fc) >= 5:
                                            break
                                    if len(_fc) >= 5:
                                        break
                                if _fc:
                                    # Layer A guard: ensure description preserved as
                                    # the user's literal phrase (never null) so review
                                    # cards + price prompts show "kaos polos" not "Item".
                                    item["description"] = name_hint
                                    item["item_id"] = None
                                    item["_fuzzy_candidates"] = _fc
                                    logger.info(
                                        f"FIX_AQUA_ITEM_RESOLVE: ambiguous item='{name_hint}' fuzzy={_fc}"
                                    )
                        if results:
                            exact = next(
                                (
                                    r
                                    for r in results
                                    if (r.get("name") or r.get("nama_produk") or "")
                                    .strip()
                                    .lower()
                                    == name_hint.lower()
                                ),
                                None,
                            )
                            resolved = exact or results[0]
                            rid = resolved.get("id") or resolved.get("item_id")
                            if rid:
                                item["item_id"] = rid
                                item_id = rid
                                rname = (
                                    resolved.get("name")
                                    or resolved.get("nama_produk")
                                    or name_hint
                                )
                                item["description"] = rname
                                # FIX_AQUA_PRICE 2026-05-09: parameterized via
                                # price_keys (sales: sales_price/harga_jual/...,
                                # purchase: purchase_price/harga_beli). Iterates
                                # in priority order, returns first truthy value.
                                if not item.get("unit_price"):
                                    _price = 0
                                    for _pk in price_keys:
                                        _v = resolved.get(_pk)
                                        if _v:
                                            _price = _v
                                            break
                                    item["unit_price"] = _price
                                logger.info(
                                    f"BUG-item-slot: Resolved item_id={rid} from name={name_hint}"
                                )
                    else:
                        # Customer-bleed signal: proper noun in name_hint not in user_text
                        logger.info(
                            f"BUG-item-slot: Rejected customer-bleed item description={name_hint}"
                        )
                        item["item_id"] = None
                        item["description"] = None
            if not item_id:
                continue
            # FIX_NAMA_HARGA_SATU_SUMBER 2026-08-24: item_id terikat => nama
            # baris WAJIB dari master, tanpa syarat. Bentuknya disalin dari
            # jalur name-hint di atas (item["description"] = rname). Dua
            # gerbang independen (_need_desc / _need_price) atas SATU fetch
            # membiarkan nama datang dari teks user sementara harga datang dari
            # master -> dua barang berbeda dalam satu baris. Kalau sistem salah
            # memilih barang, owner HARUS melihatnya di nama, bukan tersamar.
            # Harga tetap "master hanya bila belum diisi" — identik dengan
            # jalur name-hint — supaya jawaban user pada alur tanya-harga
            # (_check_missing_item_prices) tidak ditimpa balik ke 0.
            detail = await self._fetch_entity(client, f"/api/items/{item_id}")
            # FIX_AQUA_UNWRAP defensive belt-and-suspenders (envelope
            # unwrap also fixed at-source in _fetch_entity 2026-05-09).
            if isinstance(detail, dict) and isinstance(detail.get("data"), dict):
                detail = detail["data"]
            if detail:
                item["description"] = detail.get("name", "Item")
                if not item.get("unit_price"):
                    _price = 0
                    for _pk in price_keys:
                        _v = detail.get(_pk)
                        if _v:
                            _price = _v
                            break
                    item["unit_price"] = _price

        return payload

    def _add_due_date(
        self, payload: Dict[str, Any], days: int = 30, hari_ini: str = None
    ) -> Dict[str, Any]:
        """Add due_date = invoice_date + N days if not already set.

        BUG-05 fix: Also handles case where invoice_date is missing —
        falls back to today + N days.
        """
        if "due_date" not in payload or not payload["due_date"]:
            base_date_str = payload.get("invoice_date") or payload.get("issue_date")
            if base_date_str:
                try:
                    base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
                    payload["due_date"] = (base_date + timedelta(days=days)).strftime(
                        "%Y-%m-%d"
                    )
                except (ValueError, TypeError):
                    payload["due_date"] = _dasar_jatuh_tempo(hari_ini, days)
            else:
                payload["due_date"] = _dasar_jatuh_tempo(hari_ini, days)
        return payload

    # --- Per-action enrichment methods ---

    async def _enrich_sales_invoice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_SALES_INVOICE: customer lookup, date defaults, items normalization.

        Mirrors _enrich_quote quality:
          - Overrides stale/hallucinated invoice_date (>30 days past or prior year)
          - Forces due_date recompute when invoice_date is overridden
          - Parses stringified items JSON
          - Scalar-fallback items builder when Stage 2 returns empty list
          - Extracts top-level tax_rate from user_text regex
          - Backfills item_id from top-level to items[0]
          - Cleans up extraction artifacts (item_id, item_name, quantity, etc.)
        """
        today = await self._hari_ini()
        _ut_raw = getattr(self, "user_text", "") or ""

        # FIX_BILL_ABSDATE_PARSE 2026-06-18 (sales-invoice twin): deterministically
        # resolve an explicit invoice date from THIS turn's text + honor an
        # absolute date persisted from an earlier turn. Mirrors the bill path so
        # "tanggal faktur 15 februari" on turn 1 is not clobbered to today when
        # the card is built on a later text-only converge turn. The sales path
        # keys on invoice_date (not issue_date) and accepts the LLM "date" alias.
        _parsed_abs = _parse_absolute_date_id(_ut_raw)
        _persisted_issue_iso = payload.get("_user_issue_date")
        _persisted_issue_flag = bool(payload.get("_user_stated_issue_date"))

        # Stale-date override (LLM hallucinates 2024 dates from training cutoff)
        _date_alias = payload.pop("date", None)  # FIX_BILL_ABSDATE_PARSE: fold "date"
        _id = payload.get("invoice_date") or _date_alias

        _resolved_abs_iso = None
        if _parsed_abs is not None:
            _resolved_abs_iso = _parsed_abs.isoformat()
        elif _persisted_issue_iso:
            try:
                datetime.strptime(str(_persisted_issue_iso), "%Y-%m-%d")
                _resolved_abs_iso = str(_persisted_issue_iso)
            except (ValueError, TypeError):
                _resolved_abs_iso = None

        if _resolved_abs_iso:
            payload["invoice_date"] = _resolved_abs_iso
            payload.pop("due_date", None)  # recompute against the user's date
            logger.info(
                "[FIX_BILL_ABSDATE_PARSE] (sales) invoice_date=%s (det=%s persisted=%s)",
                _resolved_abs_iso,
                _parsed_abs is not None,
                bool(_persisted_issue_iso),
            )
        else:
            _override = False
            if not _id or _id in ("null", "", "-", "None"):
                _override = True
            else:
                try:
                    _parsed = datetime.strptime(str(_id), "%Y-%m-%d")
                    _now = datetime.now()
                    if (
                        (_now - _parsed).days > 30 or _parsed.year < _now.year
                    ) and not (
                        _user_gave_absolute_date(_ut_raw) or _persisted_issue_flag
                    ):
                        _override = True
                except (ValueError, TypeError):
                    _override = True
            if _override:
                payload["invoice_date"] = today
                payload.pop("due_date", None)  # force recompute since base changed
            else:
                payload["invoice_date"] = _id

        # FIX_AQUA_DUEDATE 2026-05-09: drop LLM-injected due_date unless user
        # explicitly mentioned "jatuh tempo"/"due date". Stage-2 LLM tends to
        # hallucinate due_date=today when user only specifies invoice_date.
        # The intent of the conversational form is: due_date is DERIVED from
        # invoice_date + customer.payment_terms_days, not LLM-extracted.
        _ut = (getattr(self, "user_text", "") or "").lower()
        # FIX_BILL_ABSDATE_PERSIST (sales twin): a persisted relative due-offset
        # counts as user-specified intent so the AQUA-pop below doesn't strip a
        # due_date that will be (re-)derived from the offset.
        _persisted_due_offset = payload.get("_due_offset_days")
        _user_specified_due = bool(
            re.search(r"\b(jatuh\s+tempo|due\s+date|tempo\s+pembayaran)\b", _ut)
        ) or _persisted_due_offset is not None
        # Q3C 2026-08-10: due_date dari LLM TIDAK PERNAH dipercaya — bukan hanya
        # ketika user diam.
        #
        # Bentuk lama: guard ini hanya membuang due_date bila user TIDAK menyebut
        # tempo. Ketika user menyebut, angka LLM dibiarkan lewat — dengan asumsi
        # diam-diam bahwa _apply_relative_dates di bawah pasti menimpanya. Asumsi
        # itu runtuh begitu parser tak mengerti frasanya: "jatuh tempo satu bulan"
        # menghasilkan due_date 2024-05-23, mundur dua tahun, sampai ke DB
        # (dok. 52 — INV-2608-0005 lahir dengan tanggal itu).
        #
        # Prinsipnya: KEGAGALAN PARSER TIDAK BOLEH BERARTI "PERCAYAI LLM".
        # due_date selalu dibuang di sini; nilai benar datang dari salah satu
        # sumber deterministik di bawah, dengan urutan:
        #   1. _apply_relative_dates  (frasa relatif ATAU tanggal absolut)
        #   2. _due_offset_days       (offset eksplisit dari giliran sebelumnya)
        #   3. customer.payment_terms_days / NET-30
        # Ketiganya turunan; nol angka dari model. Sejalan lapisan 3 arsitektur
        # target: nominal/tanggal/ID dari input user atau DB, tak pernah dari
        # generasi model.
        if payload.get("due_date") and _persisted_due_offset is None:
            logger.info(
                "[Q3C] due_date usulan LLM dibuang (user_menyebut_tempo=%s); "
                "akan diturunkan deterministik",
                _user_specified_due,
            )
            payload.pop("due_date", None)

        # Default due_date: lookup customer.payment_terms_days. Falls back to 30
        # (industry NET-30) when customer isn't yet resolved or column is null.
        _terms_days = 30
        _early_cid = payload.get("customer_id")
        if _early_cid and ("due_date" not in payload or not payload.get("due_date")):
            try:
                async with httpx.AsyncClient(timeout=5.0) as _terms_client:
                    _cust = await self._fetch_entity(
                        _terms_client, f"/api/customers/{_early_cid}"
                    )
                    if _cust and _cust.get("payment_terms_days"):  # FIX_NET0_DEFAULT
                        _terms_days = int(_cust["payment_terms_days"])
            except Exception as _terms_err:
                logger.debug(
                    "[enrich_sales_invoice] payment_terms_days lookup failed: %s",
                    _terms_err,
                )
        self._add_due_date(payload, days=_terms_days, hari_ini=today)

        # FIX_BILL_RELDATE / RELDATE_PERSIST (sales twin, 2026-06-18): resolve
        # relative dates from this turn's text + re-apply a persisted explicit
        # due-offset against the (now correct) invoice_date, so an explicit
        # "jatuh tempo N hari" stated on turn 1 survives to the converge turn.
        # Symmetric with _enrich_purchase_invoice. Metadata-only.
        _apply_relative_dates(
            payload,
            getattr(self, "user_text", "") or "",
            invoice_date_key="invoice_date",
        )
        _po = payload.get("_due_offset_days")
        if _po is not None:
            try:
                _po_base = datetime.strptime(payload["invoice_date"], "%Y-%m-%d")
                payload["due_date"] = (
                    _po_base + timedelta(days=int(_po))
                ).strftime("%Y-%m-%d")
                logger.info(
                    "[FIX_BILL_RELDATE_PERSIST] (sales) re-applied due_offset=%s "
                    "-> due_date=%s (invoice_date=%s)",
                    _po,
                    payload["due_date"],
                    payload["invoice_date"],
                )
            except (ValueError, TypeError, KeyError) as _po_err:
                logger.debug(
                    "[FIX_BILL_RELDATE_PERSIST] (sales) offset re-apply skipped: %s",
                    _po_err,
                )

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Customer name lookup
            cid = payload.get("customer_id")
            if cid and "customer_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            # BUG-02 fix: Reverse lookup — resolve customer_id from customer_name
            if not payload.get("customer_id") and payload.get("customer_name"):
                cust_name = payload["customer_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/customers?search={cust_name}&limit=5"
                )
                if search_resp:
                    items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if items:
                        exact = next(
                            (
                                c
                                for c in items
                                if c.get("name", "").strip().lower()
                                == cust_name.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or items[0]
                        payload["customer_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["customer_name"] = resolved["name"]
                        logger.info(
                            f"BUG-02: Resolved customer_id={payload['customer_id']} from name={cust_name}"
                        )
                        # FIX_AQUA_DUEDATE 2026-05-09: customer just resolved —
                        # re-fetch payment_terms_days + recompute due_date so
                        # it derives from the now-known customer rather than
                        # earlier NET-30 fallback.
                        if not _user_specified_due:
                            _terms2 = 30
                            try:
                                _cust2 = await self._fetch_entity(
                                    client,
                                    f"/api/customers/{payload['customer_id']}",
                                )
                                if (
                                    _cust2
                                    and _cust2.get("payment_terms_days")  # FIX_NET0_DEFAULT
                                ):
                                    _terms2 = int(_cust2["payment_terms_days"])
                            except Exception:
                                pass
                            payload.pop("due_date", None)
                            self._add_due_date(payload, days=_terms2, hari_ini=today)

            # Parse items if stringified JSON (Stage-2 sometimes returns it that way)
            _t181_urai_items(payload)

            # Scalar-fallback: build items[] from top-level fields if empty
            # T181 FASE 1: keadaan (A) saja — `items` tak pernah ada.
            # Kalau `items` ADA tapi gagal diurai, mengarang baris di
            # sini yang membuat barang kedua menguap tanpa suara.
            if not payload.get("items") and not payload.get(
                "_t181_items_mentah"
            ):
                _it_id = payload.get("item_id")
                _it_name = payload.get("item_name") or payload.get("name")
                _qty = payload.get("quantity")
                _price = payload.get("unit_price")
                if _it_id or _it_name or _qty or _price:
                    payload["items"] = [
                        {
                            "item_id": _it_id,
                            "description": _it_name or "Item",
                            "quantity": _qty or 1,
                            "unit_price": _price or 0,
                            "unit": payload.get("base_unit") or "pcs",
                        }
                    ]

            # Parse top-level tax_rate from user_text if missing/zero
            try:
                _cur_tr = float(payload.get("tax_rate") or 0)
            except (ValueError, TypeError):
                _cur_tr = 0.0
            if _cur_tr == 0.0 and getattr(self, "user_text", None):
                _m = re.search(
                    r"pajak\s*(\d+(?:[.,]\d+)?)\s*(?:%|persen)",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _m:
                    try:
                        _parsed_tr = float(_m.group(1).replace(",", "."))
                        payload["tax_rate"] = _parsed_tr
                        _cur_tr = _parsed_tr
                    except (ValueError, TypeError):
                        pass

            # FIX_AQUA_DISCOUNT_REGEX 2026-05-09: parse discount_percent from
            # user_text. Mirrors tax_rate pattern.
            try:
                _cur_disc = float(payload.get("discount_percent") or 0)
            except (ValueError, TypeError):
                _cur_disc = 0.0
            if _cur_disc == 0.0 and getattr(self, "user_text", None):
                _md = re.search(
                    r"diskon\s*(\d+(?:[.,]\d+)?)\s*(?:%|persen)",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _md:
                    try:
                        payload["discount_percent"] = float(
                            _md.group(1).replace(",", ".")
                        )
                    except (ValueError, TypeError):
                        pass

            # FIX_AQUA_DISCOUNT_AMOUNT_REGEX 2026-05-09: parse discount_amount
            # (Rp fixed) — alternative to discount_percent.
            # Patterns: "diskon Rp 50.000", "potongan 50000", "diskon rp50rb"
            try:
                _cur_disc_amt = float(payload.get("discount_amount") or 0)
            except (ValueError, TypeError):
                _cur_disc_amt = 0.0
            if _cur_disc_amt == 0.0 and getattr(self, "user_text", None):
                _ut_lc = self.user_text.lower()
                _mda = re.search(
                    r"(?:diskon|potongan)\s+rp\s*(\d[\d.,]*)\s*(ribu|rb|juta|jt|k)?(?!\s*%)",
                    _ut_lc,
                )
                if _mda:
                    _raw_amt = _mda.group(1).replace(".", "").replace(",", "")
                    _suf = _mda.group(2)
                    try:
                        _amt_v = float(_raw_amt)
                        if _suf in ("ribu", "rb", "k"):
                            _amt_v *= 1000
                        elif _suf in ("juta", "jt"):
                            _amt_v *= 1_000_000
                        if _amt_v > 0:
                            payload["discount_amount"] = _amt_v
                    except (ValueError, TypeError):
                        pass

            # FIX_AQUA_REFNO_REGEX 2026-05-09: parse ref_no (PO customer/external)
            # Patterns: "PO 12345", "PO-12345", "ref ABC-001", "referensi #X-1"
            # FIX_AQUA_REFNO_TIGHTEN 2026-05-19: add right-side \b + require
            # captured token contains at least one digit (avoid "po" matching
            # inside "kaos polos" -> "los")
            if not payload.get("ref_no") and getattr(self, "user_text", None):
                _mr = re.search(
                    r"(?:\b(?:po|p\.o\.|purchase\s+order|ref(?:erensi)?)\b\s*[:\#\-]?\s*)((?=[\w\-\/\.]*\d)[A-Za-z0-9][\w\-\/\.]{2,30})",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _mr:
                    _ref = _mr.group(1).strip(" -:#")
                    # Filter out common false-positives (numbers that look like dates/qty)
                    if _ref and not _ref.isdigit():
                        payload["ref_no"] = _ref

            # FIX_AQUA_NOTES_REGEX 2026-05-09: extract notes from user_text
            # when keyword present. Stage 2 LLM rarely populates "notes" field.
            if not payload.get("notes") and getattr(self, "user_text", None):
                _mn = re.search(
                    r"(?:catatan|notes?|memo|ket(?:erangan)?)\s*[:\-]\s*(.+?)(?:$|\.(?:\s|$))",
                    self.user_text,
                    re.IGNORECASE | re.DOTALL,
                )
                if _mn:
                    _note_text = _mn.group(1).strip()
                    if _note_text and len(_note_text) <= 500:
                        payload["notes"] = _note_text

            # Backfill item_id from top-level into items[0]
            self._backfill_top_item_id(payload, "si")

            # Item descriptions + backfill unit_price
            payload = await self._enrich_items(payload, client)

            # Cleanup extraction artifacts (schema rejects them)
            for _k in (
                "item_id",
                "item_name",
                "name",
                "quantity",
                "unit_price",
                "item_type",
                "base_unit",
                "date",
            ):
                payload.pop(_k, None)

        # FIX_AQUA_PRICE_ASK: detect 0-price line items so orchestrator can
        # ask user for prices BEFORE propose_direct (sentinel popped before REST).
        _missing = self._check_missing_item_prices(payload, "create_sales_invoice")
        if _missing:
            payload["_needs_price_clarification"] = _missing

        # FIX_AQUA_PERLINE_HINT 2026-05-09: Stage 2 LLM uses flat schema —
        # per-line discount/tax/batch_no/exp_date/bonus_qty are NOT extractable
        # via chat. If user_text contains per-line indicator keywords, attach
        # hint sentinel so orchestrator prepends "tap Edit untuk per-item"
        # banner to card narrative.
        _ut = (getattr(self, "user_text", "") or "").lower()
        _perline_keywords = (
            "diskon item",
            "diskon per item",
            "diskon baris",
            "pajak per item",
            "pajak baris",
            "batch",
            "lot",
            "kadaluarsa",
            "kedaluwarsa",
            "exp ",
            "expired",
            "bonus",
        )
        if any(k in _ut for k in _perline_keywords):
            payload["_perline_hint"] = (
                "Untuk diskon/pajak/batch/exp/bonus per item, silakan tap "
                "**Edit** di card untuk set per-baris."
            )

        # FIX_AQUA_RELATIVE_DATE 2026-05-19: parse Indonesian relative dates from user_text
        _apply_relative_dates(
            payload,
            getattr(self, "user_text", "") or "",
            invoice_date_key="invoice_date",
        )

        # FIX_BILL_DECIMAL_NONE (2026-06-15): mirror of the bill path. Stage-2 LLM
        # emits null for unspecified per-line fields (discount_percent, tax_rate,
        # ...); an explicit None bypasses the InvoiceItemCreate Field default and
        # 422s against the float/Decimal type at POST. Drop None-valued per-line
        # keys so schema defaults apply. Runs AFTER price-ask detection so the
        # missing-price flow is unaffected; core fields are non-None for valid lines.
        _si_items = payload.get("items", [])
        if isinstance(_si_items, list):
            for _it in _si_items:
                if isinstance(_it, dict):
                    for _nk in [_k for _k, _v in list(_it.items()) if _v is None]:
                        _it.pop(_nk, None)

        return payload

    async def _enrich_sales_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_SALES_ORDER: customer lookup, date defaults, items normalization.

        Mirrors _enrich_sales_invoice/_enrich_quote quality. Schema (CreateSalesOrderRequest):
        order_date (required), expected_ship_date (optional, defaults to order_date+7d),
        customer_id + customer_name (both required), items[] with description + unit_price.
        """
        today = await self._hari_ini()

        # Stale-date override on order_date
        _od = payload.get("order_date")
        _override = False
        if not _od or _od in ("null", "", "-", "None"):
            _override = True
        else:
            try:
                _parsed = datetime.strptime(str(_od), "%Y-%m-%d")
                _now = datetime.now()
                if (
                    (_now - _parsed).days > 30 or _parsed.year < _now.year
                ) and not _user_gave_absolute_date(
                    getattr(self, "user_text", "") or ""
                ):
                    _override = True
            except (ValueError, TypeError):
                _override = True
        if _override:
            payload["order_date"] = today
            payload.pop("expected_ship_date", None)  # force recompute

        # Default expected_ship_date = order_date + 7 days
        if not payload.get("expected_ship_date"):
            try:
                od = datetime.strptime(payload["order_date"], "%Y-%m-%d")
                payload["expected_ship_date"] = (od + timedelta(days=7)).strftime(
                    "%Y-%m-%d"
                )
            except (ValueError, TypeError):
                payload["expected_ship_date"] = (
                    datetime.now() + timedelta(days=7)
                ).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Customer name lookup
            cid = payload.get("customer_id")
            if cid and not payload.get("customer_name"):
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            # Reverse: resolve customer_id from customer_name
            if not payload.get("customer_id") and payload.get("customer_name"):
                cname = payload["customer_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/customers?search={cname}&limit=5"
                )
                if search_resp:
                    items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if items:
                        exact = next(
                            (
                                c
                                for c in items
                                if c.get("name", "").strip().lower()
                                == cname.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or items[0]
                        payload["customer_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["customer_name"] = resolved["name"]

            # Parse items if stringified JSON
            _t181_urai_items(payload)

            # Scalar-fallback
            # T181 FASE 1: keadaan (A) saja — `items` tak pernah ada.
            # Kalau `items` ADA tapi gagal diurai, mengarang baris di
            # sini yang membuat barang kedua menguap tanpa suara.
            if not payload.get("items") and not payload.get(
                "_t181_items_mentah"
            ):
                _it_id = payload.get("item_id")
                _it_name = payload.get("item_name") or payload.get("name")
                _qty = payload.get("quantity")
                _price = payload.get("unit_price")
                if _it_id or _it_name or _qty or _price:
                    payload["items"] = [
                        {
                            "item_id": _it_id,
                            "description": _it_name or "Item",
                            "quantity": _qty or 1,
                            "unit_price": _price or 0,
                            "unit": payload.get("base_unit") or "pcs",
                        }
                    ]

            # Top-level tax_rate from user_text
            try:
                _cur_tr = float(payload.get("tax_rate") or 0)
            except (ValueError, TypeError):
                _cur_tr = 0.0
            if _cur_tr == 0.0 and getattr(self, "user_text", None):
                _m = re.search(
                    r"pajak\s*(\d+(?:[.,]\d+)?)\s*(?:%|persen)",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _m:
                    try:
                        _parsed_tr = float(_m.group(1).replace(",", "."))
                        payload["tax_rate"] = _parsed_tr
                        _cur_tr = _parsed_tr
                    except (ValueError, TypeError):
                        pass

            # Backfill item_id + apply tax_rate per line
            # K1 2026-08-12: deskripsi + harga item disatukan ke _enrich_items,
            # fungsi bersama yang sudah dipakai sales_invoice / purchase_order /
            # credit_note. Ini BUKAN cabang keempat — ini menghapus yang keempat.
            #
            # Salinan yang dulu di sini adalah FOSIL versi pra-FIX_AQUA_PRICE,
            # dengan DUA cacat yang masing-masing SENDIRIAN cukup membuat harga
            # nol — jadi memperbaiki salah satunya saja tak akan terlihat:
            #   1) fetch digerbangi `not item.get("description")`, padahal
            #      resolver SUDAH mengisi description -> fetch tak pernah jalan.
            #      FIX_AQUA_PRICE 2026-05-09 memperbaiki persis ini di
            #      _enrich_items ("always fetch when description missing OR
            #      unit_price still 0"), tapi perbaikannya tak pernah menyeberang
            #      ke penawaran dan pesanan penjualan.
            #   2) ia membaca `selling_price`/`harga_jual`; /api/items/{id}
            #      mengembalikan `sales_price`. Kunci yang dibacanya TIDAK ADA
            #      di respons — [SQL] harga nyata hidup di kolom sales_price /
            #      harga_jual, sementara sales_price_amount justru 0.00.
            # Menyatukan di sini bukan kerapian: ia memindahkan perbaikan yang
            # sudah lama ada ke jalur yang tak pernah menerimanya.
            #
            # URUTAN 2026-08-25: backfill item_id tingkat-atas -> items[] HARUS
            # MENDAHULUI _enrich_items. Bentuknya disalin apa adanya dari
            # _enrich_sales_invoice (jalur yang terbukti bersih), yang sudah
            # melakukan backfill DULU baru enrich.
            # Sebabnya: pada jalur auto-resolve (mis. nama barang salah ketik,
            # confidence tinggi, tanpa pil) entity_resolver._build_payload hanya
            # menulis payload["item_id"] TINGKAT-ATAS. Kalau enrich jalan lebih
            # dulu, baris belum punya item_id -> `if not item_id: continue` di
            # _enrich_items -> cabang FIX_NAMA_HARGA_SATU_SUMBER (nama + harga
            # dari master) TAK PERNAH dijalankan, dan baris keluar dengan nama
            # ketikan user + unit_price kosong.
            self._backfill_top_item_id(payload, "so_pre")

            payload = await self._enrich_items(payload, client)

            self._backfill_top_item_id(payload, "so_post")
            items = payload.get("items", [])
            if items and isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = item.get("item_id")
                    # _enrich_items tidak mengurus satuan; hanya itu yang tersisa
                    # di sini, dan hanya kalau memang belum terisi.
                    if item_id and not item.get("unit"):
                        detail = await self._fetch_entity(
                            client, f"/api/items/{item_id}"
                        )
                        if detail:
                            item["unit"] = (
                                detail.get("base_unit") or detail.get("unit") or "pcs"
                            )
                    if not item.get("description"):
                        item["description"] = item.get("name") or "Item"
                    if _cur_tr and not item.get("tax_rate"):
                        item["tax_rate"] = _cur_tr
                    if item.get("unit_price") is not None:
                        try:
                            item["unit_price"] = int(float(item["unit_price"]))
                        except (ValueError, TypeError):
                            item["unit_price"] = 0
                    if item.get("quantity") is not None:
                        try:
                            item["quantity"] = float(item["quantity"])
                        except (ValueError, TypeError):
                            item["quantity"] = 1.0

            # Cleanup extraction artifacts
            for _k in (
                "item_id",
                "item_name",
                "name",
                "quantity",
                "unit_price",
                "item_type",
                "base_unit",
                "date",
            ):
                payload.pop(_k, None)

        return payload

    async def _enrich_purchase_invoice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich CREATE_PURCHASE_INVOICE / CREATE_BILL: vendor_name, dates, items normalization.

        Mirrors _enrich_sales_invoice quality:
          - Stale-date override for issue_date (>30d past or prior year)
          - Parses stringified items JSON
          - Scalar-fallback items builder when Stage 2 returns empty list
          - Extracts top-level tax_rate from user_text regex
          - Vendor reverse-lookup from vendor_name
          - Translates generic field names → bills/v2 schema (product_id, product_name, qty, price)
          - Cleans up extraction artifacts
        """
        today = await self._hari_ini()
        _ut_raw = getattr(self, "user_text", "") or ""

        # FIX_BILL_ABSDATE_PARSE 2026-06-18: deterministically resolve an explicit
        # issue date the user stated in THIS turn's text ("15 februari", "15 feb
        # 2026", "tgl 15/02", ISO). This kills the Stage-2 LLM year hallucination
        # (bare "15 februari" -> 2023/2024). When found it overrides the
        # LLM-extracted (possibly mis-yeared) date below.
        _parsed_abs = _parse_absolute_date_id(_ut_raw)

        # FIX_BILL_ABSDATE_PERSIST 2026-06-18: a date stated on an EARLIER turn is
        # persisted as _user_issue_date (resolved ISO) / _user_stated_issue_date
        # (bool) by the orchestrator capture sites and survives the workflow
        # deep-merge. Prefer the persisted resolved ISO when the current turn's
        # text has no date (e.g. the "ya lanjutkan" converge turn). This mirrors
        # how _due_offset_days shields the relative due offset across turns.
        _persisted_issue_iso = payload.get("_user_issue_date")
        _persisted_issue_flag = bool(payload.get("_user_stated_issue_date"))

        # Stale-date override on issue_date (bills/v2 uses issue_date).
        # Also accept invoice_date / date from LLM and migrate → issue_date.
        _legacy_id = payload.pop("invoice_date", None)
        _date_alias = payload.pop("date", None)  # FIX_BILL_ABSDATE_PARSE: fold "date" key
        _id = payload.get("issue_date") or _legacy_id or _date_alias

        # Deterministic / persisted wins outright — no override, no clobber.
        _resolved_abs_iso = None
        if _parsed_abs is not None:
            _resolved_abs_iso = _parsed_abs.isoformat()
        elif _persisted_issue_iso:
            try:
                # validate it parses; tolerate already-ISO strings
                datetime.strptime(str(_persisted_issue_iso), "%Y-%m-%d")
                _resolved_abs_iso = str(_persisted_issue_iso)
            except (ValueError, TypeError):
                _resolved_abs_iso = None

        if _resolved_abs_iso:
            payload["issue_date"] = _resolved_abs_iso
            payload.pop("due_date", None)  # recompute against the user's date
            logger.info(
                "[FIX_BILL_ABSDATE_PARSE] issue_date=%s (deterministic=%s persisted=%s)",
                _resolved_abs_iso,
                _parsed_abs is not None,
                bool(_persisted_issue_iso),
            )
        else:
            _override = False
            if not _id or _id in ("null", "", "-", "None"):
                _override = True
            else:
                try:
                    _parsed = datetime.strptime(str(_id), "%Y-%m-%d")
                    _now = datetime.now()
                    # FIX_BILL_ABSDATE_PERSIST: suppress the today-clobber when the
                    # user stated an absolute date on THIS turn OR an earlier turn
                    # (persisted flag). Parallel to _persisted_due_offset shielding
                    # the due-date pop. Without this, a date stated turn-1 is
                    # destroyed when the card is built on a later text-only turn.
                    if (
                        (_now - _parsed).days > 30 or _parsed.year < _now.year
                    ) and not (
                        _user_gave_absolute_date(_ut_raw) or _persisted_issue_flag
                    ):
                        _override = True
                except (ValueError, TypeError):
                    _override = True
            if _override:
                payload["issue_date"] = today
                payload.pop("due_date", None)  # force recompute since base changed
            else:
                payload["issue_date"] = _id

        # FIX_AQUA_DUEDATE 2026-05-09 (port from _enrich_sales_invoice): drop
        # LLM-injected due_date unless user explicitly mentioned "jatuh tempo".
        # Stage-2 LLM tends to hallucinate due_date=today.
        _ut = (getattr(self, "user_text", "") or "").lower()
        # FIX_BILL_RELDATE_PERSIST (2026-06-18): a previously-stated explicit
        # "jatuh tempo N hari" is persisted on the payload as _due_offset_days
        # by _apply_relative_dates and survives the workflow deep-merge. Treat
        # its presence as user-specified intent so the AQUA-pop below does NOT
        # strip a persisted due_date on a later turn (e.g. "ya lanjutkan") whose
        # user_text no longer contains the tempo phrase.
        _persisted_due_offset = payload.get("_due_offset_days")
        _user_specified_due = bool(
            re.search(r"\b(jatuh\s+tempo|due\s+date|tempo\s+pembayaran)\b", _ut)
        ) or _persisted_due_offset is not None
        if not _user_specified_due and payload.get("due_date"):
            payload.pop("due_date", None)

        # Default due_date: lookup vendor.payment_terms_days. Falls back to 30
        # (industry NET-30) when vendor isn't yet resolved or column is null.
        # Mirrors customer-side payment terms behavior on sales invoice.
        _bill_terms_days = 30
        _early_vid = payload.get("vendor_id")
        # Skip non-UUID vendor_id (LLM gave name, will be moved later)
        if (
            _early_vid
            and UUID_PATTERN.match(str(_early_vid))
            and ("due_date" not in payload or not payload.get("due_date"))
        ):
            try:
                async with httpx.AsyncClient(timeout=5.0) as _bv_client:
                    _vendor = await self._fetch_entity(
                        _bv_client, f"/api/vendors/{_early_vid}"
                    )
                    if _vendor and _vendor.get("payment_terms_days"):  # FIX_NET0_DEFAULT
                        _bill_terms_days = int(_vendor["payment_terms_days"])
            except Exception as _bv_err:
                logger.debug(
                    "[enrich_purchase_invoice] vendor payment_terms_days lookup failed: %s",
                    _bv_err,
                )

        # Compute due_date if not present
        if "due_date" not in payload or not payload.get("due_date"):
            try:
                _base = datetime.strptime(payload["issue_date"], "%Y-%m-%d")
                payload["due_date"] = (
                    _base + timedelta(days=_bill_terms_days)
                ).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                payload["due_date"] = (
                    datetime.now() + timedelta(days=_bill_terms_days)
                ).strftime("%Y-%m-%d")

        # FIX_BILL_RELDATE (2026-06-18): parse Indonesian relative dates from
        # user_text for the bill path (was sales-invoice-only). Resolves
        # "tanggal faktur hari ini" / "kemarin" / "besok" / "lusa" -> a real
        # issue_date (the bug stored the literal string "today" unresolved), and
        # an explicit "jatuh tempo N hari" -> due_date = issue_date + N days,
        # overriding the vendor.payment_terms_days / NET-30 default above. The
        # helper's due branch is internally gated on the literal "tempo" keyword
        # (preserving the FIX_AQUA_DUEDATE discipline: only set due_date from
        # user_text when the user explicitly mentions jatuh tempo/tempo);
        # otherwise the NET-30 / payment_terms_days fallback stands. Iron-Law
        # clean: due_date / issue_date are source-object metadata, NOT ledger
        # values — no amount or journal logic is touched.
        _apply_relative_dates(
            payload,
            getattr(self, "user_text", "") or "",
            invoice_date_key="issue_date",
        )

        # FIX_BILL_RELDATE_PERSIST (2026-06-18): re-apply a persisted explicit
        # due-offset against the (now-finalized) issue_date on EVERY turn the
        # card is built — even one whose user_text omits the tempo phrase. This
        # closes the persistence gap where "tempo 14 hari" said on turn 1 was
        # lost when the card was produced on the "ya lanjutkan" turn (NET-30 /
        # vendor default won). Deterministic, metadata-only. NET-30 still applies
        # untouched when no offset was ever stated (marker absent).
        _po = payload.get("_due_offset_days")
        if _po is not None:
            try:
                _po_base = datetime.strptime(payload["issue_date"], "%Y-%m-%d")
                payload["due_date"] = (
                    _po_base + timedelta(days=int(_po))
                ).strftime("%Y-%m-%d")
                logger.info(
                    "[FIX_BILL_RELDATE_PERSIST] re-applied due_offset=%s -> due_date=%s (issue_date=%s)",
                    _po,
                    payload["due_date"],
                    payload["issue_date"],
                )
            except (ValueError, TypeError, KeyError) as _po_err:
                logger.debug(
                    "[FIX_BILL_RELDATE_PERSIST] offset re-apply skipped: %s", _po_err
                )

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Guard: if vendor_id is not a UUID (LLM gave name), move to vendor_name
            vid_raw = payload.get("vendor_id")
            if vid_raw and not UUID_PATTERN.match(str(vid_raw)):
                if not payload.get("vendor_name"):
                    payload["vendor_name"] = str(vid_raw)
                payload.pop("vendor_id", None)

            # Vendor name lookup (forward)
            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

            # Vendor reverse-lookup (resolve vendor_id from vendor_name)
            if not payload.get("vendor_id") and payload.get("vendor_name"):
                vname = payload["vendor_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/vendors?search={vname}&limit=5"
                )
                if search_resp:
                    v_items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if v_items:
                        exact = next(
                            (
                                v
                                for v in v_items
                                if v.get("name", "").strip().lower()
                                == vname.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or v_items[0]
                        payload["vendor_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["vendor_name"] = resolved["name"]
                        logger.info(
                            f"_enrich_purchase_invoice: Resolved vendor_id={payload['vendor_id']} from name={vname}"
                        )
                        # FIX_AQUA_DUEDATE 2026-05-09: vendor just resolved —
                        # re-fetch payment_terms_days + recompute due_date so
                        # it derives from the now-known vendor rather than
                        # earlier NET-30 fallback.
                        if not _user_specified_due:
                            _terms2 = 30
                            try:
                                _vendor2 = await self._fetch_entity(
                                    client,
                                    f"/api/vendors/{payload['vendor_id']}",
                                )
                                if (
                                    _vendor2
                                    and _vendor2.get("payment_terms_days")  # FIX_NET0_DEFAULT
                                ):
                                    _terms2 = int(_vendor2["payment_terms_days"])
                            except Exception:
                                pass
                            try:
                                _base2 = datetime.strptime(
                                    payload["issue_date"], "%Y-%m-%d"
                                )
                                payload["due_date"] = (
                                    _base2 + timedelta(days=_terms2)
                                ).strftime("%Y-%m-%d")
                            except (ValueError, TypeError):
                                pass

            # Parse items if stringified JSON (Stage-2 sometimes returns it that way)
            _t181_urai_items(payload)

            # Scalar-fallback: build items[] from top-level fields if empty
            # T181 FASE 1: keadaan (A) saja — `items` tak pernah ada.
            # Kalau `items` ADA tapi gagal diurai, mengarang baris di
            # sini yang membuat barang kedua menguap tanpa suara.
            if not payload.get("items") and not payload.get(
                "_t181_items_mentah"
            ):
                _it_id = payload.get("item_id")
                _it_name = payload.get("item_name") or payload.get("name")
                _qty = payload.get("quantity")
                _price = payload.get("unit_price")
                if _it_id or _it_name or _qty or _price:
                    payload["items"] = [
                        {
                            "item_id": _it_id,
                            "description": _it_name or "Item",
                            "quantity": _qty or 1,
                            "unit_price": _price or 0,
                            "unit": payload.get("base_unit") or "pcs",
                        }
                    ]

            # Parse top-level tax_rate from user_text if missing/zero
            try:
                _cur_tr = float(payload.get("tax_rate") or 0)
            except (ValueError, TypeError):
                _cur_tr = 0.0
            if _cur_tr == 0.0 and getattr(self, "user_text", None):
                _m = re.search(
                    r"pajak\s*(\d+(?:[.,]\d+)?)\s*(?:%|persen)",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _m:
                    try:
                        _parsed_tr = float(_m.group(1).replace(",", "."))
                        payload["tax_rate"] = _parsed_tr
                        _cur_tr = _parsed_tr
                    except (ValueError, TypeError):
                        pass

            # FIX_AQUA_NOTES_REGEX 2026-05-09: extract notes from user_text.
            if not payload.get("notes") and getattr(self, "user_text", None):
                _mn = re.search(
                    r"(?:catatan|notes?|memo|ket(?:erangan)?)\s*[:\-]\s*(.+?)(?:$|\.(?:\s|$))",
                    self.user_text,
                    re.IGNORECASE | re.DOTALL,
                )
                if _mn:
                    _note_text = _mn.group(1).strip()
                    if _note_text and len(_note_text) <= 500:
                        payload["notes"] = _note_text

            # Backfill item_id from top-level into items[0]
            self._backfill_top_item_id(payload, "bill")

            # FIX_NAMA_HARGA_SATU_SUMBER 2026-08-24: salinan-terpisah dihapus.
            # Bill kini memakai _enrich_items yang SAMA dengan 5 pemanggil sisi
            # jual/PO. Satu-satunya beda nyata adalah nama kunci, jadi kunci
            # bills/v2 dinormalkan ke kunci generik SEBELUM enrich, lalu blok
            # terjemahan di bawah mengembalikannya ke product_id/product_name/
            # price. Domain harga tetap PEMBELIAN (Iron Law 16).
            items = payload.get("items", [])
            if items and isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not item.get("item_id") and item.get("product_id"):
                        item["item_id"] = item["product_id"]
                    if not item.get("description") and item.get("product_name"):
                        item["description"] = item["product_name"]
                    if not item.get("unit_price") and item.get("price"):
                        item["unit_price"] = item["price"]
                payload = await self._enrich_items(
                    payload,
                    client,
                    price_keys=("purchase_price", "harga_beli"),
                )

            # Translate generic → bills/v2 field names
            items = payload.get("items", [])
            if items and isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # nama master menang atas sisa product_name lama
                    if item.get("item_id"):
                        item["product_id"] = item["item_id"]
                    if item.get("item_id") and item.get("description"):
                        item["product_name"] = item["description"]
                    if item.get("item_id") and item.get("unit_price"):
                        item["price"] = item["unit_price"]
                    # Translate generic field names → bills/v2 schema
                    if "description" in item and "product_name" not in item:
                        item["product_name"] = item.pop("description")
                    elif "description" in item:
                        item.pop("description", None)
                    if "name" in item and "product_name" not in item:
                        item["product_name"] = item.pop("name")
                    if "quantity" in item and "qty" not in item:
                        item["qty"] = item.pop("quantity")
                    elif "quantity" in item:
                        item.pop("quantity", None)
                    if "unit_price" in item and "price" not in item:
                        item["price"] = item.pop("unit_price")
                    elif "unit_price" in item:
                        item.pop("unit_price", None)
                    if "item_id" in item and "product_id" not in item:
                        item["product_id"] = item.pop("item_id")
                    elif "item_id" in item:
                        item.pop("item_id", None)

                    # Ensure product_name exists
                    if not item.get("product_name"):
                        item["product_name"] = "Item"

                    # Coerce numeric types
                    # FIX_DOGFOOD_BILL_DUEDATE 2026-06-09: preserve decimal qty
                    # (e.g. 100.5 meter). BillItemRequestV2.qty is Decimal (widened
                    # 2026-06-02); int() truncation here silently dropped the
                    # fractional part (100.5 -> 100) corrupting subtotal. Keep float,
                    # normalize whole numbers to int for clean display.
                    if item.get("qty") is not None:
                        try:
                            _q = float(item["qty"])
                            if _q <= 0:
                                _q = 1.0
                            item["qty"] = int(_q) if _q == int(_q) else _q
                        except (ValueError, TypeError):
                            item["qty"] = 1
                    if item.get("price") is not None:
                        try:
                            item["price"] = float(item["price"])
                        except (ValueError, TypeError):
                            item["price"] = 0.0

                    # Apply top-level tax_rate per line if line lacks it
                    if _cur_tr and not item.get("tax_rate"):
                        item["tax_rate"] = _cur_tr

                    # FIX_BILL_DECIMAL_NONE (2026-06-15): Stage-2 LLM emits null
                    # for per-line fields the user did not specify (discount_percent,
                    # bonus_qty, batch_no, exp_date, tax_code_id, ...). An explicit
                    # None bypasses the schema Field default and 422s against the
                    # Decimal type at POST. Drop None-valued keys so the schema
                    # default applies. Required keys (product_name/qty/price) are
                    # already backfilled/coerced above, so this only sheds optionals.
                    for _nk in [_k for _k, _v in list(item.items()) if _v is None]:
                        item.pop(_nk, None)

            # Cleanup top-level extraction artifacts (schema rejects them)
            for _k in (
                "item_id",
                "item_name",
                "name",
                "quantity",
                "unit_price",
                "item_type",
                "base_unit",
                "date",
            ):
                payload.pop(_k, None)

        # FIX_AQUA_PRICE_ASK: detect 0-price line items.
        _missing = self._check_missing_item_prices(payload, "create_bill")
        if _missing:
            payload["_needs_price_clarification"] = _missing

        # FIX_AQUA_PERLINE_HINT 2026-05-09: hint user about per-line config.
        _ut = (getattr(self, "user_text", "") or "").lower()
        _perline_keywords = (
            "diskon item",
            "diskon per item",
            "diskon baris",
            "pajak per item",
            "pajak baris",
            "batch",
            "lot",
            "kadaluarsa",
            "kedaluwarsa",
            "exp ",
            "expired",
            "bonus",
        )
        if any(k in _ut for k in _perline_keywords):
            payload["_perline_hint"] = (
                "Untuk diskon/pajak/batch/exp/bonus per item, silakan tap "
                "**Edit** di card untuk set per-baris."
            )

        # FIX_AQUA_RELATIVE_DATE 2026-05-19: parse Indonesian relative dates from user_text
        _apply_relative_dates(
            payload, getattr(self, "user_text", "") or "", invoice_date_key="issue_date"
        )

        return payload

    async def _enrich_purchase_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # URUTAN 2026-08-25: SENGAJA TIDAK DIUBAH. Fungsi ini nol backfill
        # item_id tingkat-atas -> items[] sama sekali, jadi secara struktural ia
        # terpapar cacat urutan yang sama dengan quote/sales_order. Ia tidak
        # ikut diubah karena TAK TERJANGKAU dari chat: CREATE_PURCHASE_ORDER
        # nol entri di direct_action_registry -> nol gate merah yang sah untuk
        # membuktikan perubahannya. Mengubah tanpa gate = tebakan.
        """Enrich CREATE_PURCHASE_ORDER: vendor_name, due_date, item descriptions."""
        today = await self._hari_ini()
        payload.setdefault("order_date", today)
        if "due_date" not in payload and "order_date" in payload:
            try:
                od = datetime.strptime(payload["order_date"], "%Y-%m-%d")
                payload["due_date"] = (od + timedelta(days=30)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                payload["due_date"] = (
                    await self._hari_ini_date() + timedelta(days=30)
                ).isoformat()

        async with httpx.AsyncClient(timeout=5.0) as client:
            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

            payload = await self._enrich_items(
                payload,
                client,
                price_keys=("purchase_price", "harga_beli"),
            )  # FIX_AQUA_PRICE 2026-05-09: PO uses cost domain

        return payload

    async def _enrich_quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_QUOTE: default dates, item descriptions, apply top-level tax_rate per line.

        Quote schema (schemas/quotes.py) requires items[] with description + unit_price,
        and tax_rate lives at line level (not top-level). This enricher:
          - Defaults quote_date = today
          - Defaults expiry_date = quote_date + 14 days
          - Looks up item descriptions via /api/items/{id}
          - Applies top-level tax_rate to each line item if line has no tax_rate
          - Strips top-level tax_rate before REST (schema rejects it)
        """
        today = await self._hari_ini()
        _qd = payload.get("quote_date")
        _override = False
        if not _qd or _qd in ("null", "", "-", "None"):
            _override = True
        else:
            try:
                _parsed = datetime.strptime(str(_qd), "%Y-%m-%d")
                # LLM hallucinates stale training-cutoff dates — override if > 30 days in past or any future year mismatch
                _now = datetime.now()
                if (
                    (_now - _parsed).days > 30 or _parsed.year < _now.year
                ) and not _user_gave_absolute_date(
                    getattr(self, "user_text", "") or ""
                ):
                    _override = True
            except (ValueError, TypeError):
                _override = True
        if _override:
            payload["quote_date"] = today
            payload.pop("expiry_date", None)  # force re-default since base date changed

        if not payload.get("expiry_date"):
            try:
                qd = datetime.strptime(payload["quote_date"], "%Y-%m-%d")
                payload["expiry_date"] = (qd + timedelta(days=14)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                payload["expiry_date"] = (
                    await self._hari_ini_date() + timedelta(days=14)
                ).isoformat()

        # Keep top-level tax_rate for build_review_card_payload; REST layer handles stripping
        try:
            top_tax_rate = float(payload.get("tax_rate") or 0) or None  # noqa: F841
        except (ValueError, TypeError):
            top_tax_rate = None  # noqa: F841

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Customer name lookup
            cid = payload.get("customer_id")
            if cid and not payload.get("customer_name"):
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            # Reverse: resolve customer_id from name
            if not payload.get("customer_id") and payload.get("customer_name"):
                cname = payload["customer_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/customers?search={cname}&limit=5"
                )
                if search_resp:
                    items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if items:
                        exact = next(
                            (
                                c
                                for c in items
                                if c.get("name", "").strip().lower()
                                == cname.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or items[0]
                        payload["customer_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["customer_name"] = resolved["name"]

            # Parse items if string (Stage-2 extractor sometimes returns JSON-stringified)
            _t181_urai_items(payload)

            # Fallback: build items[] from top-level scalar fields if empty/missing
            # T181 FASE 1: keadaan (A) saja — `items` tak pernah ada.
            # Kalau `items` ADA tapi gagal diurai, mengarang baris di
            # sini yang membuat barang kedua menguap tanpa suara.
            if not payload.get("items") and not payload.get(
                "_t181_items_mentah"
            ):
                _it_id = payload.get("item_id")
                _it_name = payload.get("item_name") or payload.get("name")
                _qty = payload.get("quantity")
                _price = payload.get("unit_price")
                if _it_id or _it_name or _qty or _price:
                    payload["items"] = [
                        {
                            "item_id": _it_id,
                            "description": _it_name or "Item",
                            "quantity": _qty or 1,
                            "unit_price": _price or 0,
                            "unit": payload.get("base_unit") or "pcs",
                        }
                    ]

            # Parse tax_rate from user_text if missing or 0
            try:
                _cur_tr = float(payload.get("tax_rate") or 0)
            except (ValueError, TypeError):
                _cur_tr = 0.0
            if _cur_tr == 0.0 and getattr(self, "user_text", None):
                _m = re.search(
                    r"pajak\s*(\d+(?:[.,]\d+)?)\s*(?:%|persen)",
                    self.user_text,
                    re.IGNORECASE,
                )
                if _m:
                    try:
                        _parsed_tr = float(_m.group(1).replace(",", "."))
                        payload["tax_rate"] = _parsed_tr
                        _cur_tr = _parsed_tr
                        _top_tax_rate = _parsed_tr  # noqa: F841 (kept for parity)
                    except (ValueError, TypeError):
                        pass

            # Enrich items (description + unit_price lookup, inject item_id, apply tax_rate)
            # K1 2026-08-12: deskripsi + harga item disatukan ke _enrich_items,
            # fungsi bersama yang sudah dipakai sales_invoice / purchase_order /
            # credit_note. Ini BUKAN cabang keempat — ini menghapus yang keempat.
            #
            # Salinan yang dulu di sini adalah FOSIL versi pra-FIX_AQUA_PRICE,
            # dengan DUA cacat yang masing-masing SENDIRIAN cukup membuat harga
            # nol — jadi memperbaiki salah satunya saja tak akan terlihat:
            #   1) fetch digerbangi `not item.get("description")`, padahal
            #      resolver SUDAH mengisi description -> fetch tak pernah jalan.
            #      FIX_AQUA_PRICE 2026-05-09 memperbaiki persis ini di
            #      _enrich_items ("always fetch when description missing OR
            #      unit_price still 0"), tapi perbaikannya tak pernah menyeberang
            #      ke penawaran dan pesanan penjualan.
            #   2) ia membaca `selling_price`/`harga_jual`; /api/items/{id}
            #      mengembalikan `sales_price`. Kunci yang dibacanya TIDAK ADA
            #      di respons — [SQL] harga nyata hidup di kolom sales_price /
            #      harga_jual, sementara sales_price_amount justru 0.00.
            # Menyatukan di sini bukan kerapian: ia memindahkan perbaikan yang
            # sudah lama ada ke jalur yang tak pernah menerimanya.
            #
            # URUTAN 2026-08-25: backfill item_id tingkat-atas -> items[] HARUS
            # MENDAHULUI _enrich_items. Bentuknya disalin apa adanya dari
            # _enrich_sales_invoice (jalur yang terbukti bersih), yang sudah
            # melakukan backfill DULU baru enrich.
            # Sebabnya: pada jalur auto-resolve (mis. nama barang salah ketik,
            # confidence tinggi, tanpa pil) entity_resolver._build_payload hanya
            # menulis payload["item_id"] TINGKAT-ATAS. Kalau enrich jalan lebih
            # dulu, baris belum punya item_id -> `if not item_id: continue` di
            # _enrich_items -> cabang FIX_NAMA_HARGA_SATU_SUMBER (nama + harga
            # dari master) TAK PERNAH dijalankan, dan baris keluar dengan nama
            # ketikan user + unit_price kosong.
            self._backfill_top_item_id(payload, "quote_pre")

            payload = await self._enrich_items(payload, client)

            self._backfill_top_item_id(payload, "quote_post")
            items = payload.get("items", [])
            if items and isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = item.get("item_id")
                    # _enrich_items tidak mengurus satuan; hanya itu yang tersisa
                    # di sini, dan hanya kalau memang belum terisi.
                    if item_id and not item.get("unit"):
                        detail = await self._fetch_entity(
                            client, f"/api/items/{item_id}"
                        )
                        if detail:
                            item["unit"] = (
                                detail.get("base_unit") or detail.get("unit") or "pcs"
                            )
                    if not item.get("description"):
                        item["description"] = item.get("name") or "Item"
                    if _cur_tr and not item.get("tax_rate"):
                        item["tax_rate"] = _cur_tr
                    if item.get("unit_price") is not None:
                        try:
                            item["unit_price"] = int(float(item["unit_price"]))
                        except (ValueError, TypeError):
                            item["unit_price"] = 0
                    if item.get("quantity") is not None:
                        try:
                            item["quantity"] = float(item["quantity"])
                        except (ValueError, TypeError):
                            item["quantity"] = 1.0

            # ── T143: JUDUL DITURUNKAN SETELAH ITEM TER-RESOLVE ──────────
            # Ditempatkan SETELAH _enrich_items (satu-satunya titik di mana
            # baris sudah memegang nama MASTER dan item_id). Titik ini dilewati
            # oleh KEDUA jalur — propose langsung DAN re-propose sesudah pil
            # (unified_chat._jalankan_pil_entity -> _execute_propose_direct ->
            # _enrich_payload) — jadi judul ditinjau ulang persis saat item
            # AKHIRNYA terikat, bukan dibawa membeku dari giliran pertama.
            # Preseden urutan: a7f0bc5c (backfill item_id mendahului
            # _enrich_items).
            _judul_user = payload.pop("_user_subject", None)
            payload.pop("_user_stated_subject", None)
            if not _judul_user:
                _judul_user = _judul_eksplisit_dari_teks(
                    getattr(self, "user_text", "") or ""
                )
            _t143_items = [
                _i for _i in (payload.get("items") or []) if isinstance(_i, dict)
            ]
            _t143_terikat = [
                str(_i.get("description") or "").strip()
                for _i in _t143_items
                if _i.get("item_id") and str(_i.get("description") or "").strip()
            ]
            _t143_unik = list(dict.fromkeys(_t143_terikat))
            _t143_lama = payload.get("subject")
            if _judul_user:
                payload["subject"] = _judul_user[:255]
                logger.warning(
                    "[T143_JUDUL] eksplisit MENANG: %r (llm=%r, terikat=%s)",
                    payload["subject"],
                    _t143_lama,
                    _t143_unik,
                )
            elif len(_t143_unik) == 1:
                payload["subject"] = ("Penawaran " + _t143_unik[0])[:255]
                logger.warning(
                    "[T143_JUDUL] DITURUNKAN dari item terikat: %r <- %r (llm=%r)",
                    payload["subject"],
                    _t143_unik[0],
                    _t143_lama,
                )
            elif len(_t143_unik) > 1:
                _t143_pel = str(payload.get("customer_name") or "").strip()
                payload["subject"] = (
                    ("Penawaran untuk " + _t143_pel) if _t143_pel else "Penawaran"
                )[:255]
                logger.warning(
                    "[T143_JUDUL] multi-item (%d baris terikat) -> judul generik: "
                    "%r (llm=%r, terikat=%s)",
                    len(_t143_unik),
                    payload["subject"],
                    _t143_lama,
                    _t143_unik,
                )
            else:
                logger.warning(
                    "[T143_JUDUL] NOL item terikat -> judul teks user "
                    "DIPERTAHANKAN: %r",
                    _t143_lama,
                )

            # Strip top-level scalar fields not in CreateQuoteRequest schema (keep tax_rate for review_card)
            for _k in (
                "item_id",
                "item_name",
                "name",
                "quantity",
                "unit_price",
                "item_type",
                "base_unit",
                "date",
            ):
                payload.pop(_k, None)

        return payload

    async def _enrich_expense(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_EXPENSE: field translation, CoA→bank_account lookup, date default."""
        today = await self._hari_ini()
        # Fix: setdefault doesn't override empty/null values from document pipeline
        if not payload.get("expense_date") or payload.get("expense_date") in (
            "null",
            "",
            "-",
            "None",
        ):
            payload["expense_date"] = today
        # Translate LLM field names → kernel field names
        if "payment_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("payment_account_id")
        if "deposit_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("deposit_account_id")
        if "bank_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("bank_account_id")

        # paid_through_id MUST be a bank_accounts.id, not a CoA ID.
        # If LLM gave a CoA ID (from search_accounts), resolve to bank_account_id.
        pt_id = payload.get("paid_through_id")
        if pt_id:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Try to find bank account matching this ID or coa_id
                banks_resp = await self._fetch_entity(client, "/api/bank-accounts")
                banks = (
                    banks_resp
                    if isinstance(banks_resp, list)
                    else (banks_resp.get("items") or banks_resp.get("data") or [])
                    if isinstance(banks_resp, dict)
                    else []
                )
                if banks:
                    # Direct match (already a bank account ID)
                    direct = next(
                        (b for b in banks if str(b.get("id")) == str(pt_id)), None
                    )
                    if direct:
                        pass  # Already correct bank_account_id
                    else:
                        # Try matching coa_id (LLM gave CoA ID instead of bank account ID)
                        coa_match = next(
                            (b for b in banks if str(b.get("coa_id")) == str(pt_id)),
                            None,
                        )
                        if coa_match:
                            payload["paid_through_id"] = str(coa_match["id"])
                            logger.info(
                                f"Expense enrich: CoA {pt_id} -> bank_account {coa_match['id']}"
                            )
                        else:
                            # No match — use first bank account as fallback
                            if banks:
                                payload["paid_through_id"] = str(banks[0]["id"])
                                logger.warning(
                                    f"Expense enrich: No bank match for {pt_id}, using default {banks[0]['id']}"
                                )
        return payload

    async def _enrich_credit_note(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # URUTAN 2026-08-25: SENGAJA TIDAK DIUBAH. Sama seperti
        # _enrich_purchase_order: nol backfill item_id tingkat-atas -> items[],
        # jadi terpapar struktural, TAPI tak terjangkau dari chat (FieldSpec
        # `create_credit_note` nol field `items`) -> nol gate merah yang sah.
        """Enrich CREATE_CREDIT_NOTE: customer lookup (+reverse), stale-date override."""
        today = await self._hari_ini()

        # Stale-date override (mirrors _enrich_sales_invoice)
        _cd = payload.get("credit_note_date")
        _override = False
        if not _cd or _cd in ("null", "", "-", "None"):
            _override = True
        else:
            try:
                _parsed = datetime.strptime(str(_cd), "%Y-%m-%d")
                _now = datetime.now()
                if (
                    (_now - _parsed).days > 30 or _parsed.year < _now.year
                ) and not _user_gave_absolute_date(
                    getattr(self, "user_text", "") or ""
                ):
                    _override = True
            except (ValueError, TypeError):
                _override = True
        if _override:
            payload["credit_note_date"] = today

        async with httpx.AsyncClient(timeout=5.0) as client:
            cid = payload.get("customer_id")
            if cid and "customer_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            # Reverse: resolve customer_id from customer_name
            if not payload.get("customer_id") and payload.get("customer_name"):
                cust_name = payload["customer_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/customers?search={cust_name}&limit=5"
                )
                if search_resp:
                    items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if items:
                        exact = next(
                            (
                                c
                                for c in items
                                if c.get("name", "").strip().lower()
                                == cust_name.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or items[0]
                        payload["customer_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["customer_name"] = resolved["name"]

            payload = await self._enrich_items(payload, client)

        # FIX_AQUA_PRICE_ASK: detect 0-price line items.
        _missing = self._check_missing_item_prices(payload, "create_credit_note")
        if _missing:
            payload["_needs_price_clarification"] = _missing

        return payload

    async def _enrich_receive_payment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich RECEIVE_PAYMENT: customer lookup from invoice, field translations, defaults."""
        today = await self._hari_ini()
        # FIX_TRANSFER_RCV_DATE (2026-06-15): the document resolver sets
        # payment_date="" when OCR has no date (bank transfers often lack one).
        # setdefault does NOT override an existing empty string, so the required
        # "Tanggal" field stayed empty → propose validation rejected it
        # (success=False) → fallback crash → Lane C masked it. The bill-payment
        # twin (_enrich_make_payment) already has this empty-safe override; this
        # mirrors it. Lesson: empty-string vs missing-key — use `if not get(x)`.
        if not payload.get("payment_date") or payload.get("payment_date") in (
            "null",
            "",
            "-",
            "None",
        ):
            payload["payment_date"] = today
        # FIX_RCV_CASH_METHOD (2026-06-18): honour an EXPLICIT payment method in the
        # user text. Customers often pay CASH at the store ("pembayaran masuk secara
        # cash / tunai"), but the receive-payment default is bank_transfer -> a cash
        # payment was silently booked as a transfer. Explicit cash/tunai wins over the
        # LLM default; an explicit transfer word sets bank_transfer.
        _ut_rcv = (getattr(self, "user_text", "") or "").lower()
        import re as _re_pm

        if _re_pm.search(r"\b(cash|tunai|kontan)\b", _ut_rcv):
            payload["payment_method"] = "cash"
        elif _re_pm.search(r"\b(transfer|tf|m-?banking|qris|va)\b", _ut_rcv):
            payload["payment_method"] = "bank_transfer"
        payload.setdefault("payment_method", "bank_transfer")
        # FIX_DOGFOOD_PAYMETHOD_NORMALIZE (2026-06-09): the LLM/override can
        # set payment_method to a raw Indonesian phrase ("transfer bank",
        # "lewat transfer") that survives setdefault (only fills when absent)
        # and then violates DB CHECK chk_rcv_payment_method + Pydantic
        # CreateReceivePaymentRequest.payment_method Literal["cash",
        # "bank_transfer"] at POST. Normalize to the enum unconditionally.
        payload["payment_method"] = _normalize_payment_method(
            payload.get("payment_method")
        )

        # Translate LLM field names → kernel field names
        if "deposit_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("deposit_account_id")
        if "payment_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("payment_account_id")

        _perf_t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=5.0) as client:
            # FIX_RCV_BACKSTOP (2026-06-18): the LLM Stage-1/router intermittently
            # returns empty amount/customer for a long sentence ("Ada pembayaran masuk
            # secara cash Rp 275.000 dari pelanggan Marwa Pahude ...") -> the card
            # degenerates to "Rp 0 dari -". Recover deterministically from the text.
            _ut_bk = getattr(self, "user_text", "") or ""
            if _ut_bk:
                try:
                    _have_amt = float(
                        payload.get("total_amount") or payload.get("amount") or 0
                    )
                except Exception:
                    _have_amt = 0.0
                if _have_amt <= 0:
                    _pa = _parse_idr_amount(_ut_bk)
                    if _pa > 0:
                        payload["total_amount"] = _pa
                        logger.info("[FIX_RCV_BACKSTOP] amount from text -> %s", _pa)
                if not payload.get("customer_id") and not payload.get("customer_name"):
                    from .document_intake_v3.signals import (
                        extract_party_name as _epn_bk,
                    )

                    _cust_nm = _epn_bk(_ut_bk, "in")
                    if _cust_nm:
                        _cr = await self._fetch_entity(
                            client, f"/api/customers?search={_cust_nm}"
                        )
                        _crows = (
                            _cr
                            if isinstance(_cr, list)
                            else (_cr.get("items") or _cr.get("data") or [])
                            if isinstance(_cr, dict)
                            else []
                        )
                        if _crows:
                            _c0 = _crows[0]
                            _cidv = _c0.get("id")
                            if _cidv:
                                payload["customer_id"] = str(_cidv)
                                payload["customer_name"] = (
                                    _c0.get("name") or _c0.get("nama") or _cust_nm
                                )
                                logger.info(
                                    "[FIX_RCV_BACKSTOP] customer from text '%s' -> %s",
                                    _cust_nm,
                                    _cidv,
                                )
            # Stage 0 (sequential, required): resolve customer_id from invoice_id if needed
            inv_id = payload.get("invoice_id")
            if inv_id and "customer_id" not in payload:
                inv = await self._fetch_entity(client, f"/api/sales-invoices/{inv_id}")
                if inv:
                    payload["customer_id"] = inv.get("customer_id", "")
                    if "customer_name" not in payload:
                        payload["customer_name"] = inv.get("customer_name", "")

            _perf_t1 = time.perf_counter()

            # Stage 1 (parallel): customer name lookup + unpaid-invoices fetch.
            # Both are independent — both only need customer_id, which is now known.
            cid = payload.get("customer_id")
            need_name = bool(cid) and "customer_name" not in payload

            allocs_existing = payload.get("allocations")
            total_raw = payload.get("total_amount")
            try:
                total_f = float(total_raw) if total_raw is not None else 0.0
            except Exception:
                total_f = 0.0
            need_allocs = (not allocs_existing) and bool(cid) and total_f > 0

            stage1_tasks = []
            stage1_keys = []
            if need_name:
                stage1_tasks.append(self._fetch_entity(client, f"/api/customers/{cid}"))
                stage1_keys.append("cust")
            if need_allocs:
                stage1_tasks.append(
                    self._fetch_entity(
                        client,
                        f"/api/sales-invoices?customer_id={cid}&status=unpaid,partial&sort=invoice_date&order=asc&limit=20",
                    )
                )
                stage1_keys.append("invs")

            stage1_results = {}
            if stage1_tasks:
                _results = await asyncio.gather(*stage1_tasks, return_exceptions=True)
                for k, r in zip(stage1_keys, _results):
                    stage1_results[k] = None if isinstance(r, Exception) else r

            if "cust" in stage1_results and stage1_results["cust"]:
                payload["customer_name"] = stage1_results["cust"].get("name", "")

            # Auto-build allocations from oldest-first unpaid invoices when missing
            try:
                if need_allocs:
                    if True:
                        invs = stage1_results.get("invs")
                        inv_list = []
                        if isinstance(invs, dict):
                            inv_list = invs.get("items") or invs.get("data") or []
                        elif isinstance(invs, list):
                            inv_list = invs
                        remaining = total_f
                        built = []
                        inv_numbers = []
                        for inv in inv_list:
                            if remaining <= 0:
                                break
                            inv_id = inv.get("id") or inv.get("invoice_id")
                            if not inv_id:
                                continue
                            try:
                                outstanding = float(
                                    inv.get("outstanding_amount")
                                    or inv.get("balance_due")
                                    or inv.get("amount_due")
                                    or 0
                                )
                            except Exception:
                                outstanding = 0.0
                            if outstanding <= 0:
                                continue
                            apply_amt = min(outstanding, remaining)
                            built.append(
                                {
                                    "invoice_id": inv_id,
                                    "amount_applied": apply_amt,
                                }
                            )
                            inv_num = inv.get("invoice_number") or inv.get("number")
                            if inv_num:
                                inv_numbers.append(str(inv_num))
                            remaining -= apply_amt
                        if built:
                            payload["allocations"] = built
                            if inv_numbers and "invoice_numbers" not in payload:
                                payload["invoice_numbers"] = ", ".join(inv_numbers)
            except Exception as e:
                logger.warning(
                    f"_enrich_receive_payment allocations auto-build failed: {e}"
                )

        # ── FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09): journal-derived alloc ──
        # The REST list path above (/api/sales-invoices?status=unpaid,partial)
        # is unreliable for allocation building: the comma status filter can
        # drop `partial` invoices, and the list rows return NULL
        # outstanding_amount/balance_due -> the loop computes outstanding=0 and
        # builds NO allocations, so a customer settlement posts unallocated.
        # ARAP Rule 5 + Iron Law 16 mandate compute_ar_outstanding() (journal-
        # derived, draft/void-excluded, partial-aware) as the SINGLE source of
        # truth for AR outstanding. When allocations are still missing, build
        # them oldest-due-first from compute_ar_outstanding for this customer.
        _cid_alloc = payload.get("customer_id")
        if (not payload.get("allocations")) and _cid_alloc:
            try:
                _ra_total_raw = payload.get("total_amount") or payload.get("amount")
                _ra_total = float(_ra_total_raw) if _ra_total_raw is not None else 0.0
            except Exception:
                _ra_total = 0.0
            if _ra_total > 0:
                try:
                    from .db_utils import get_session_db_pool as _ra_pool_fn

                    _ra_pool = await _ra_pool_fn()
                    async with _ra_pool.acquire() as _ra_conn:
                        async with _ra_conn.transaction():
                            await _ra_conn.execute(
                                "SELECT set_config('app.tenant_id', $1, true)",
                                self.context.tenant_id,
                            )
                            _ra_rows = await _ra_conn.fetch(
                                """
                                SELECT invoice_id, invoice_number, outstanding
                                FROM compute_ar_outstanding($1)
                                WHERE customer_id = $2 AND outstanding > 0
                                ORDER BY due_date ASC NULLS LAST, invoice_number ASC
                                """,
                                self.context.tenant_id,
                                str(_cid_alloc),
                            )
                    _ra_remaining = _ra_total
                    _ra_built = []
                    _ra_nums = []
                    for _ra_r in _ra_rows:
                        if _ra_remaining <= 0:
                            break
                        try:
                            _ra_out = float(_ra_r["outstanding"] or 0)
                        except Exception:
                            _ra_out = 0.0
                        if _ra_out <= 0:
                            continue
                        _ra_apply = min(_ra_out, _ra_remaining)
                        _ra_built.append(
                            {
                                "invoice_id": str(_ra_r["invoice_id"]),
                                "amount_applied": _ra_apply,
                            }
                        )
                        if _ra_r["invoice_number"]:
                            _ra_nums.append(str(_ra_r["invoice_number"]))
                        _ra_remaining -= _ra_apply
                    if _ra_built:
                        payload["allocations"] = _ra_built
                        if _ra_nums and "invoice_numbers" not in payload:
                            payload["invoice_numbers"] = ", ".join(_ra_nums)
                        logger.warning(
                            "[ENRICH] rcv journal-derived allocations: %d invoice(s) %s",
                            len(_ra_built),
                            _ra_nums,
                        )
                except Exception as _ra_err:
                    logger.warning(
                        "[ENRICH] rcv journal-derived allocation build failed: %s",
                        _ra_err,
                    )

        _perf_t2 = time.perf_counter()
        logger.info(
            f"[ENRICH] rcv stage0={(_perf_t1 - _perf_t0) * 1000:.0f}ms stage1_parallel={(_perf_t2 - _perf_t1) * 1000:.0f}ms total={(_perf_t2 - _perf_t0) * 1000:.0f}ms tasks={len(stage1_tasks)}"
        )
        return payload

    async def _enrich_make_payment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_BILL_PAYMENT / MAKE_PAYMENT: vendor + bank lookup, auto-resolve bill_id.

        Mirrors _enrich_receive_payment: auto-build bill_id (single-bill allocation)
        from vendor's oldest outstanding bill when bill_id is missing.
        """
        today = await self._hari_ini()
        # FIX_DOGFOOD_PAYMETHOD_NORMALIZE (2026-06-09): symmetric defensive
        # normalization for the bill-payment twin. bill_payments_v2 has no DB
        # CHECK on payment_method, but downstream bill schemas restrict it; a
        # raw phrase like "transfer bank" should map to bank_transfer here too.
        # FIX_PAY_CASH_METHOD (2026-06-18): symmetric with receive — honour an
        # explicit cash/tunai (or transfer) in the user text for outgoing payments
        # ("bayar vendor X cash/tunai"), not just the default.
        _ut_mp = (getattr(self, "user_text", "") or "").lower()
        if re.search(r"\b(cash|tunai|kontan)\b", _ut_mp):
            payload["payment_method"] = "cash"
        elif re.search(r"\b(transfer|tf|m-?banking|qris|va)\b", _ut_mp):
            payload["payment_method"] = "bank_transfer"
        if payload.get("payment_method"):
            payload["payment_method"] = _normalize_payment_method(
                payload.get("payment_method")
            )

        # Stale-date override on payment_date
        _pd = payload.get("payment_date")
        _override = False
        if not _pd or _pd in ("null", "", "-", "None"):
            _override = True
        else:
            try:
                _parsed = datetime.strptime(str(_pd), "%Y-%m-%d")
                _now = datetime.now()
                if (
                    (_now - _parsed).days > 30 or _parsed.year < _now.year
                ) and not _user_gave_absolute_date(
                    getattr(self, "user_text", "") or ""
                ):
                    _override = True
            except (ValueError, TypeError):
                _override = True
        if _override:
            payload["payment_date"] = today

        # Field translations (LLM → kernel)
        if "deposit_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("deposit_account_id")
        if "payment_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("payment_account_id")

        _perf_t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=5.0) as client:
            # If vendor_id is not a valid UUID (LLM gave the name), move it to vendor_name
            vid_raw = payload.get("vendor_id")
            if vid_raw and not UUID_PATTERN.match(str(vid_raw)):
                if not payload.get("vendor_name"):
                    payload["vendor_name"] = str(vid_raw)
                payload.pop("vendor_id", None)

            # Stage 1 (parallel): vendor resolve (forward OR reverse) + bank forward lookup
            stage1_tasks = []
            stage1_keys = []

            vid = payload.get("vendor_id")
            vname_payload = payload.get("vendor_name")
            if vid and "vendor_name" not in payload:
                # vendor forward lookup
                stage1_tasks.append(self._fetch_entity(client, f"/api/vendors/{vid}"))
                stage1_keys.append("vendor_fwd")
            elif (not vid) and vname_payload:
                # vendor reverse lookup
                stage1_tasks.append(
                    self._fetch_entity(
                        client, f"/api/vendors?search={vname_payload}&limit=5"
                    )
                )
                stage1_keys.append("vendor_rev")

            bid = payload.get("bank_account_id")
            if (
                bid
                and UUID_PATTERN.match(str(bid))
                and "bank_account_name" not in payload
            ):
                stage1_tasks.append(
                    self._fetch_entity(client, f"/api/bank-accounts/{bid}")
                )
                stage1_keys.append("bank")

            stage1_results = {}
            if stage1_tasks:
                _results = await asyncio.gather(*stage1_tasks, return_exceptions=True)
                for k, r in zip(stage1_keys, _results):
                    stage1_results[k] = None if isinstance(r, Exception) else r

            # Process vendor forward result
            if "vendor_fwd" in stage1_results and stage1_results["vendor_fwd"]:
                payload["vendor_name"] = stage1_results["vendor_fwd"].get("name", "")

            # Process vendor reverse result
            if "vendor_rev" in stage1_results:
                search_resp = stage1_results["vendor_rev"]
                if search_resp:
                    v_items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if v_items:
                        vname = vname_payload
                        exact = next(
                            (
                                v
                                for v in v_items
                                if v.get("name", "").strip().lower()
                                == vname.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or v_items[0]
                        payload["vendor_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["vendor_name"] = resolved["name"]

            # Process bank result
            if "bank" in stage1_results and stage1_results["bank"]:
                b = stage1_results["bank"]
                payload["bank_account_name"] = (
                    b.get("name") or b.get("account_name") or ""
                )

            _perf_t1 = time.perf_counter()

            # Stage 2 (sequential, needs vendor_id): auto-resolve bill_id from oldest outstanding
            try:
                cur_bill_id = payload.get("bill_id")
                vid2 = payload.get("vendor_id")
                total = payload.get("total_amount") or payload.get("amount")
                if (not cur_bill_id) and vid2:
                    bills_resp = await self._fetch_entity(
                        client,
                        f"/api/bills?vendor_id={vid2}&status=active&sort=date:asc&limit=20",
                    )
                    bill_list = []
                    if isinstance(bills_resp, dict):
                        bill_list = (
                            bills_resp.get("items") or bills_resp.get("data") or []
                        )
                    elif isinstance(bills_resp, list):
                        bill_list = bills_resp
                    # Filter outstanding (amount_due > 0), oldest first (already sorted)
                    try:
                        total_f = float(total or 0)
                    except Exception:
                        total_f = 0.0
                    for b in bill_list:
                        try:
                            due = float(b.get("amount_due") or 0)
                        except Exception:
                            due = 0.0
                        if due <= 0:
                            continue
                        payload["bill_id"] = b.get("id") or b.get("bill_id")
                        if "bill_number" not in payload:
                            payload["bill_number"] = (
                                b.get("invoice_number") or b.get("number") or ""
                            )
                        if "bill_amount" not in payload:
                            try:
                                payload["bill_amount"] = float(b.get("amount") or 0)
                            except Exception:
                                pass
                        if "amount_due" not in payload:
                            payload["amount_due"] = due
                        # If total_amount missing, pay oldest due exactly
                        if total_f <= 0:
                            payload["total_amount"] = due
                        logger.info(
                            f"_enrich_make_payment: Auto-resolved bill_id={payload['bill_id']} for vendor={vid2}"
                        )
                        break
            except Exception as e:
                logger.warning(f"_enrich_make_payment bill auto-resolve failed: {e}")

            # Cleanup extraction artifacts
            for _k in ("date", "invoice_date", "amount"):
                payload.pop(_k, None) if _k == "date" or _k == "invoice_date" else None

        _perf_t2 = time.perf_counter()
        logger.info(
            f"[ENRICH] pay stage1_parallel={(_perf_t1 - _perf_t0) * 1000:.0f}ms stage2_bills={(_perf_t2 - _perf_t1) * 1000:.0f}ms total={(_perf_t2 - _perf_t0) * 1000:.0f}ms tasks={len(stage1_tasks)}"
        )
        return payload

    async def _enrich_vendor_credit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_VENDOR_CREDIT: vendor reverse-lookup, stale-date override."""
        today = await self._hari_ini()

        _vcd = payload.get("vendor_credit_date")
        _override = False
        if not _vcd or _vcd in ("null", "", "-", "None"):
            _override = True
        else:
            try:
                _parsed = datetime.strptime(str(_vcd), "%Y-%m-%d")
                _now = datetime.now()
                if (
                    (_now - _parsed).days > 30 or _parsed.year < _now.year
                ) and not _user_gave_absolute_date(
                    getattr(self, "user_text", "") or ""
                ):
                    _override = True
            except (ValueError, TypeError):
                _override = True
        if _override:
            payload["vendor_credit_date"] = today

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Guard: non-UUID vendor_id → treat as name
            vid_raw = payload.get("vendor_id")
            if vid_raw and not UUID_PATTERN.match(str(vid_raw)):
                if not payload.get("vendor_name"):
                    payload["vendor_name"] = str(vid_raw)
                payload.pop("vendor_id", None)

            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

            # Reverse lookup
            if not payload.get("vendor_id") and payload.get("vendor_name"):
                vname = payload["vendor_name"]
                search_resp = await self._fetch_entity(
                    client, f"/api/vendors?search={vname}&limit=5"
                )
                if search_resp:
                    v_items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if v_items:
                        exact = next(
                            (
                                v
                                for v in v_items
                                if v.get("name", "").strip().lower()
                                == vname.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or v_items[0]
                        payload["vendor_id"] = resolved.get("id", "")
                        if resolved.get("name"):
                            payload["vendor_name"] = resolved["name"]

        return payload

    async def _enrich_transfer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich BANK_TRANSFER: transfer_date default."""
        today = await self._hari_ini()
        payload.setdefault("transfer_date", today)
        # from_bank_id, to_bank_id, amount must come from LLM
        return payload

    async def _enrich_journal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich POST_GENERAL_JOURNAL: posting_date default."""
        today = await self._hari_ini()
        payload.setdefault("posting_date", today)
        return payload

    # --- Propose Action Execution ---

    async def _execute_propose(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate -> prepare pending action. Does NOT execute."""
        action_type = params.get("action_type")
        assumptions = params.get("assumptions", [])

        # Collect all non-meta fields as payload (flat schema design)
        _meta_keys = {"action_type", "assumptions"}
        payload = {
            k: v for k, v in params.items() if k not in _meta_keys and v is not None
        }

        # === ENRICHMENT STEP ===
        # LLM provides intent (IDs, qty, price).
        # Enrichment adds kernel-required fields (names, defaults, descriptions).
        payload = await self._enrich_payload(action_type, payload)

        # T181 FASE 1: keadaan (B) — jangan teruskan payload berbaris karangan
        # ke validator.
        _t181_mentah = payload.pop("_t181_items_mentah", None)
        if _t181_mentah:
            return _error("ITEMS_TIDAK_TERBACA", _t181_pesan_tolak(_t181_mentah))
        logger.info(
            f"propose_action: type={action_type}, payload_keys={list(payload.keys())}"
        )

        if action_type not in ACTION_TYPE_MAP:
            return _error(
                "INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid."
            )

        amount_error = _validate_amounts(payload)
        if amount_error:
            return _error("INVALID_AMOUNT", amount_error)

        category = ACTION_CATEGORY_MAP.get(action_type, "DOCUMENT")
        idempotency_key = _generate_idempotency_key(
            self.context.tenant_id, action_type, payload
        )

        # Step 1: Validate via gRPC (individual params)
        validation = await self.validator_client.validate_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_id=action_type,
            action_type=action_type,
            category=category,
            draft_payload=payload,
            idempotency_key=idempotency_key,
            confidence=0.9,
        )

        if not validation.get("valid"):
            errors = validation.get("errors", [])
            return {
                "success": False,
                "data": {
                    "status": "VALIDATION_FAILED",
                    "errors": [
                        {
                            "layer": e.get("layer", ""),
                            "code": e.get("code", ""),
                            "message": e.get("message", ""),
                        }
                        for e in errors
                    ],
                },
            }

        # Step 2: Prepare pending action via gRPC
        prepare_result = await self.executor_client.prepare_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_type=action_type,
            category=category,
            draft_payload=payload,
            idempotency_key=idempotency_key,
            confidence=0.9,
            assumptions=assumptions,
        )

        if not prepare_result.get("success", False) and not prepare_result.get(
            "pending_action_id"
        ):
            return _error("PREPARE_FAILED", "Gagal membuat pending action.")

        dry_run = validation.get("dry_run", {})
        journal_lines = dry_run.get("journal_entries", [])

        return {
            "success": True,
            "data": {
                "status": "ACTION_PREVIEW",
                "pending_action_id": prepare_result.get("pending_action_id"),
                "confirmation_token": prepare_result.get("confirmation_token"),
                "preview": {
                    "action_type": action_type,
                    "payload": payload,
                    "assumptions": assumptions,
                    "journal_lines": [
                        {
                            "account": l.get("account_name", l.get("account_code", "")),
                            "debit": l.get("debit", 0),
                            "credit": l.get("credit", 0),
                            "description": l.get("description", ""),
                        }
                        for l in journal_lines  # noqa: E741
                    ],
                    "total_debit": dry_run.get("total_debit", 0),
                    "total_credit": dry_run.get("total_credit", 0),
                    "balanced": dry_run.get("balanced", False),
                    "impact_summary": dry_run.get("impact_summary", ""),
                    "confirmation_message": validation.get("confirmation_message", ""),
                    "risk_level": validation.get("risk_level", 0),
                },
                "expires_at": prepare_result.get("expires_at"),
            },
        }

    # --- Simulate Action Execution ---

    async def _execute_simulate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate only, no pending action. For what-if analysis."""
        action_type = params.get("action_type")

        # Flat payload extraction (same as propose)
        _meta_keys = {"action_type", "assumptions"}
        payload = {
            k: v for k, v in params.items() if k not in _meta_keys and v is not None
        }

        if action_type not in ACTION_TYPE_MAP:
            return _error(
                "INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid."
            )

        # === SAME ENRICHMENT ===
        payload = await self._enrich_payload(action_type, payload)

        # T181 FASE 1: keadaan (B) — jangan teruskan payload berbaris karangan
        # ke validator.
        _t181_mentah = payload.pop("_t181_items_mentah", None)
        if _t181_mentah:
            return _error("ITEMS_TIDAK_TERBACA", _t181_pesan_tolak(_t181_mentah))

        # dry_run_action expects individual params
        dry_run_result = await self.validator_client.dry_run_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_type=action_type,
            draft_payload=payload,
        )

        if dry_run_result is None:
            return {
                "success": False,
                "data": {
                    "status": "SIMULATION_FAILED",
                    "errors": [
                        {
                            "layer": "DRY_RUN",
                            "code": "FAILED",
                            "message": "Simulasi gagal",
                        }
                    ],
                },
            }

        result = dry_run_result
        return {
            "success": True,
            "data": {
                "status": "SIMULATION_OK",
                "journal_lines": [
                    {
                        "account": l.get("account_name", ""),
                        "debit": l.get("debit", 0),
                        "credit": l.get("credit", 0),
                    }
                    for l in result.get("journal_entries", [])  # noqa: E741
                ],
                "total_debit": result.get("total_debit", 0),
                "total_credit": result.get("total_credit", 0),
                "balanced": result.get("balanced", False),
            },
        }

    # ═══════════════ CHART SPEC BUILDERS ═══════════════

    def _build_chart_spec(self, config: "ChartQueryConfig", data, params: dict) -> dict:
        """Build a ChartSpec from API response data."""
        import datetime  # noqa: E402

        spec = {
            "chart_type": config.chart_type,
            "render_target": "artifact"
            if config.complexity_hint == "complex"
            else "inline",
            "title": config.display_name,
            "subtitle": params.get("periode", datetime.date.today().strftime("%Y-%m")),
            "datasets": [],
            "labels": [],
            "slices": [],
            "features": config.chart_features or {},
            "summary": "",
            "highlights": [],
            "value_format": "currency",
        }

        try:
            transformers = {
                "chart_revenue_expense": self._chart_revenue_expense,
                "chart_cash_flow": self._chart_cash_flow,
                "chart_expense_breakdown": self._chart_expense_breakdown,
                "chart_top_customers": self._chart_top_customers,
                "chart_ar_aging": self._chart_ar_aging,
                # BATCH 1: Dashboard & KPI
                "chart_kas_composition": self._chart_kas_composition,
                "chart_cash_projection": self._chart_cash_projection,
                "chart_overdue_invoices": self._chart_overdue_invoices,
                "chart_overdue_bills": self._chart_overdue_bills,
                "chart_cash_flow_trends": self._chart_cash_flow_trends,
                "chart_dashboard_kpi": self._chart_dashboard_kpi,
                # BATCH 2: Laporan Keuangan
                "chart_neraca": self._chart_neraca,
                "chart_neraca_composition": self._chart_neraca_composition,
                "chart_profit_trend": self._chart_profit_trend,
                "chart_profit_comparison": self._chart_profit_comparison,
                "chart_gross_margin": self._chart_gross_margin,
                "chart_monthly_cashflow": self._chart_monthly_cashflow,
                # BATCH 3: AR/AP
                "chart_ap_aging": self._chart_ap_aging,
                "chart_ar_summary": self._chart_ar_summary,
                "chart_ap_summary": self._chart_ap_summary,
                "chart_invoice_status": self._chart_invoice_status,
                "chart_bill_status": self._chart_bill_status,
                "chart_payment_trends": self._chart_payment_trends,
                # BATCH 4: Inventory & Products
                "chart_top_products": self._chart_top_products,
                "chart_product_margins": self._chart_product_margins,
                "chart_slow_moving": self._chart_slow_moving,
                "chart_sales_trend": self._chart_sales_trend,
                "chart_top_vendors": self._chart_top_vendors,
                # BATCH 5: Financial Ratios
                "chart_profitability_ratios": self._chart_profitability_ratios,
                "chart_liquidity_ratios": self._chart_liquidity_ratios,
                "chart_leverage_ratios": self._chart_leverage_ratios,
                "chart_ratio_dashboard": self._chart_ratio_dashboard,
                # BATCH 6: Budget & Production
                "chart_budget_vs_actual": self._chart_budget_vs_actual,
                "chart_variance_alerts": self._chart_variance_alerts,
                "chart_production_costs": self._chart_production_costs,
            }
            transformer = transformers.get(config.action_key)
            if transformer:
                spec = transformer(data, spec)
        except Exception as e:
            logger.warning(f"Chart transform error for {config.action_key}: {e}")
            spec["summary"] = f"Data tersedia tapi gagal membuat grafik: {str(e)[:100]}"

        return spec

    def _chart_revenue_expense(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into bar chart."""
        pendapatan = data.get("total_pendapatan", 0)
        beban = data.get("total_beban", 0)
        laba = data.get("laba_bersih", 0)

        # Try monthly breakdown first
        monthly = data.get("monthly_breakdown", data.get("details", []))
        if isinstance(monthly, list) and monthly:
            spec["labels"] = [
                str(m.get("bulan", m.get("month", m.get("name", "")))) for m in monthly
            ]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [
                        float(m.get("pendapatan", m.get("total_pendapatan", 0)))
                        for m in monthly
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "beban",
                    "label": "Beban",
                    "values": [
                        float(m.get("beban", m.get("total_beban", 0))) for m in monthly
                    ],
                    "color": "#C45C4B",
                },
            ]
        else:
            spec["labels"] = ["Pendapatan", "Beban"]
            spec["datasets"] = [
                {
                    "key": "amount",
                    "label": "Jumlah",
                    "values": [float(pendapatan), float(beban)],
                }
            ]

        spec["highlights"] = [
            {
                "label": "Laba Bersih",
                "value": f"Rp {float(laba):,.0f}".replace(",", "."),
                "color": "success" if float(laba) >= 0 else "danger",
            }
        ]
        return spec

    def _chart_cash_flow(self, data: dict, spec: dict) -> dict:
        """Transform arus-kas into area chart."""
        # Endpoint returns nested: operasi.net_arus_kas_operasi, investasi.net_arus_kas_investasi, etc.
        operasi = data.get("operasi", data.get("operasional", {}))
        investasi = data.get("investasi", {})
        pendanaan = data.get("pendanaan", {})

        op = float(operasi.get("net_arus_kas_operasi", operasi.get("total", 0)))
        inv = float(investasi.get("net_arus_kas_investasi", investasi.get("total", 0)))
        fin = float(pendanaan.get("net_arus_kas_pendanaan", pendanaan.get("total", 0)))
        net = float(
            data.get("kenaikan_bersih_kas", data.get("net_cash_flow", op + inv + fin))
        )

        spec["labels"] = ["Operasional", "Investasi", "Pendanaan"]
        spec["datasets"] = [
            {
                "key": "amount",
                "label": "Arus Kas",
                "values": [op, inv, fin],
                "color": "#3B9FE8",
            }
        ]
        spec["highlights"] = [
            {
                "label": "Net Cash Flow",
                "value": f"Rp {net:,.0f}".replace(",", "."),
                "color": "success" if net >= 0 else "danger",
            }
        ]
        return spec

    def _chart_expense_breakdown(self, data, spec: dict) -> dict:
        """Transform top-expenses into donut."""
        items = data if isinstance(data, list) else normalize_api_response(data)
        if not isinstance(items, list):
            items = []
        spec["slices"] = [
            {
                "label": str(
                    e.get("name", e.get("category", e.get("account_name", "Unknown")))
                ),
                "value": float(e.get("amount", e.get("total", 0))),
            }
            for e in items[:8]
        ]
        total = sum(s["value"] for s in spec["slices"])
        spec["summary"] = f"Total beban: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_top_customers(self, data, spec: dict) -> dict:
        """Transform pendapatan into horizontal bar."""
        items = data if isinstance(data, list) else normalize_api_response(data)
        if not isinstance(items, list):
            items = []
        top = items[:5]
        spec["labels"] = [
            self._truncate(str(c.get("name", c.get("customer_name", "")))) for c in top
        ]
        spec["datasets"] = [
            {
                "key": "revenue",
                "label": "Revenue",
                "values": [float(c.get("total", c.get("amount", 0))) for c in top],
                "color": "#5B8C51",
            }
        ]
        return spec

    def _chart_ar_aging(self, data, spec: dict) -> dict:
        """Transform aging-trend into line chart."""
        items = data if isinstance(data, list) else normalize_api_response(data)
        if not isinstance(items, list):
            items = []
        spec["labels"] = [
            str(t.get("period", t.get("date", t.get("as_of", "")))) for t in items
        ]
        spec["datasets"] = [
            {
                "key": "current",
                "label": "Lancar",
                "values": [float(t.get("current", 0)) for t in items],
                "color": "#5B8C51",
            },
            {
                "key": "overdue_30",
                "label": "30 hari",
                "values": [
                    float(t.get("overdue_30", t.get("30_days", 0))) for t in items
                ],
                "color": "#F5A623",
            },
            {
                "key": "overdue_60",
                "label": "60 hari",
                "values": [
                    float(t.get("overdue_60", t.get("60_days", 0))) for t in items
                ],
                "color": "#C45C4B",
            },
            {
                "key": "overdue_90",
                "label": "90+ hari",
                "values": [
                    float(t.get("overdue_90", t.get("90_days", 0))) for t in items
                ],
                "color": "#8B6AB8",
            },
        ]
        spec["drill_down"] = {
            "type": "query",
            "message_template": "Detail piutang overdue periode {label}",
        }
        return spec

    # ═══════════════ BATCH 1: Dashboard & KPI ═══════════════

    def _chart_kas_composition(self, data: dict, spec: dict) -> dict:
        """Transform kas-bank into donut: account balances."""
        accounts = data.get("accounts", [])
        spec["slices"] = [
            {
                "label": str(a.get("name", a.get("account_name", "Unknown"))),
                "value": abs(float(a.get("balance", a.get("ledger_balance", 0)))),
            }
            for a in accounts
            if float(a.get("balance", a.get("ledger_balance", 0))) != 0
        ]
        total = sum(s["value"] for s in spec["slices"])
        spec["summary"] = f"Total kas & bank: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_cash_projection(self, data: dict, spec: dict) -> dict:
        """Transform cash-flow-projection into area chart."""
        projections = normalize_api_response(data)
        if isinstance(projections, list) and projections:
            spec["labels"] = [
                str(p.get("date", p.get("period", ""))) for p in projections
            ]
            spec["datasets"] = [
                {
                    "key": "projected",
                    "label": "Saldo Proyeksi",
                    "values": [
                        float(p.get("projected_balance", p.get("balance", 0)))
                        for p in projections
                    ],
                    "color": "#3B9FE8",
                },
                {
                    "key": "inflow",
                    "label": "Kas Masuk",
                    "values": [
                        float(p.get("inflow", p.get("kas_masuk", 0)))
                        for p in projections
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "outflow",
                    "label": "Kas Keluar",
                    "values": [
                        float(p.get("outflow", p.get("kas_keluar", 0)))
                        for p in projections
                    ],
                    "color": "#C45C4B",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data proyeksi."
        return spec

    def _chart_overdue_invoices(self, data: dict, spec: dict) -> dict:
        """Transform overdue-invoices into horizontal bar."""
        invoices = normalize_api_response(data)
        spec["labels"] = [
            self._truncate(str(i.get("customer_name", i.get("name", "Unknown"))))
            for i in invoices[:10]
        ]
        spec["datasets"] = [
            {
                "key": "outstanding",
                "label": "Outstanding",
                "values": [
                    float(i.get("outstanding", i.get("amount", 0)))
                    for i in invoices[:10]
                ],
                "color": "#C45C4B",
            }
        ]
        total = float(data.get("total_outstanding", 0))
        count = int(data.get("count", len(invoices)))
        spec["highlights"] = [
            {
                "label": "Total Overdue",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "danger",
            }
        ]
        if count == 0:
            spec["summary"] = "Tidak ada invoice jatuh tempo."
        return spec

    def _chart_overdue_bills(self, data: dict, spec: dict) -> dict:
        """Transform overdue-bills into horizontal bar."""
        bills = normalize_api_response(data)
        spec["labels"] = [
            self._truncate(str(b.get("vendor_name", b.get("name", "Unknown"))))
            for b in bills[:10]
        ]
        spec["datasets"] = [
            {
                "key": "outstanding",
                "label": "Outstanding",
                "values": [
                    float(b.get("outstanding", b.get("amount", 0))) for b in bills[:10]
                ],
                "color": "#C45C4B",
            }
        ]
        total = float(data.get("total_outstanding", 0))
        spec["highlights"] = [
            {
                "label": "Total Overdue",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "danger",
            }
        ]
        if not bills:
            spec["summary"] = "Tidak ada tagihan jatuh tempo."
        return spec

    def _chart_cash_flow_trends(self, data: dict, spec: dict) -> dict:
        """Transform cash-flow-trends into area chart."""
        trends = data.get("trends", [])
        spec["labels"] = [str(t.get("label", t.get("date", ""))) for t in trends]
        spec["datasets"] = [
            {
                "key": "kas_masuk",
                "label": "Kas Masuk",
                "values": [float(t.get("kas_masuk", 0)) for t in trends],
                "color": "#5B8C51",
            },
            {
                "key": "kas_keluar",
                "label": "Kas Keluar",
                "values": [float(t.get("kas_keluar", 0)) for t in trends],
                "color": "#C45C4B",
            },
        ]
        net = float(data.get("net_flow", 0))
        spec["highlights"] = [
            {
                "label": "Net Flow",
                "value": f"Rp {net:,.0f}".replace(",", "."),
                "color": "success" if net >= 0 else "danger",
            }
        ]
        return spec

    def _chart_dashboard_kpi(self, data: dict, spec: dict) -> dict:
        """Transform dashboard summary into KPI bar chart."""
        lr = data.get("laba_rugi", {})
        piutang = data.get("piutang", {})
        hutang = data.get("hutang", {})
        kas = data.get("kas_bank", {})
        spec["labels"] = ["Pendapatan", "Beban", "Piutang", "Hutang", "Kas"]
        spec["datasets"] = [
            {
                "key": "amount",
                "label": "Jumlah",
                "values": [
                    float(lr.get("pendapatan", 0)),
                    float(lr.get("pengeluaran", 0)),
                    float(piutang.get("total", 0)),
                    float(hutang.get("total", 0)),
                    float(kas.get("total", 0)),
                ],
            }
        ]
        profit = float(lr.get("profit", 0))
        spec["highlights"] = [
            {
                "label": "Laba Bersih",
                "value": f"Rp {profit:,.0f}".replace(",", "."),
                "color": "success" if profit >= 0 else "danger",
            }
        ]
        return spec

    # ═══════════════ BATCH 2: Laporan Keuangan ═══════════════

    def _chart_neraca(self, data: dict, spec: dict) -> dict:
        """Transform neraca into grouped bar: aset vs kewajiban+ekuitas."""
        al = float(data.get("aset_lancar", {}).get("total", 0))
        at_raw = data.get("aset_tetap", {})
        at = float(at_raw.get("total_neto", at_raw.get("total", 0)))
        kp = float(data.get("kewajiban_jangka_pendek", {}).get("total", 0))
        kj = float(data.get("kewajiban_jangka_panjang", {}).get("total", 0))
        eq = float(data.get("ekuitas", {}).get("total", 0))
        spec["labels"] = [
            "Aset Lancar",
            "Aset Tetap",
            "Kwjbn Pendek",
            "Kwjbn Panjang",
            "Ekuitas",
        ]
        spec["datasets"] = [
            {
                "key": "aset",
                "label": "Aset",
                "values": [al, at, 0, 0, 0],
                "color": "#3B9FE8",
            },
            {
                "key": "kewajiban_ekuitas",
                "label": "Kewajiban & Ekuitas",
                "values": [0, 0, kp, kj, eq],
                "color": "#F5A623",
            },
        ]
        balanced = data.get("is_balanced", True)
        spec["highlights"] = [
            {
                "label": "Balance",
                "value": "Seimbang" if balanced else "TIDAK SEIMBANG",
                "color": "success" if balanced else "danger",
            }
        ]
        return spec

    def _chart_neraca_composition(self, data: dict, spec: dict) -> dict:
        """Transform neraca into donut: aset composition."""
        al = data.get("aset_lancar", {})
        slices = []
        for key, label in [
            ("kas", "Kas"),
            ("piutang_usaha", "Piutang"),
            ("persediaan", "Persediaan"),
            ("beban_dibayar_dimuka", "Beban Dibayar Dimuka"),
            ("uang_muka_pembelian", "Uang Muka"),
        ]:
            val = float(al.get(key, 0))
            if val > 0:
                slices.append({"label": label, "value": val})
        at = data.get("aset_tetap", {})
        at_total = float(at.get("total_neto", at.get("total", 0)))
        if at_total > 0:
            slices.append({"label": "Aset Tetap", "value": at_total})
        spec["slices"] = slices
        total = float(data.get("total_aset", sum(s["value"] for s in slices)))
        spec["summary"] = f"Total aset: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_profit_trend(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into line chart trend (reuses revenue_expense logic for line)."""
        monthly = data.get("monthly_breakdown", data.get("details", []))
        if isinstance(monthly, list) and monthly:
            spec["labels"] = [
                str(m.get("bulan", m.get("month", m.get("name", "")))) for m in monthly
            ]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [
                        float(m.get("pendapatan", m.get("total_pendapatan", 0)))
                        for m in monthly
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "beban",
                    "label": "Beban",
                    "values": [
                        float(m.get("beban", m.get("total_beban", 0))) for m in monthly
                    ],
                    "color": "#C45C4B",
                },
                {
                    "key": "laba",
                    "label": "Laba Bersih",
                    "values": [
                        float(m.get("laba_bersih", m.get("laba", 0))) for m in monthly
                    ],
                    "color": "#3B9FE8",
                },
            ]
        else:
            p = float(data.get("total_pendapatan", 0))
            b = float(data.get("total_beban", 0))
            l = float(data.get("laba_bersih", p - b))  # noqa: E741
            spec["labels"] = ["Periode"]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [p],
                    "color": "#5B8C51",
                },
                {"key": "beban", "label": "Beban", "values": [b], "color": "#C45C4B"},
                {"key": "laba", "label": "Laba", "values": [l], "color": "#3B9FE8"},
            ]
        return spec

    def _chart_profit_comparison(self, data: dict, spec: dict) -> dict:
        """Transform profit-loss comparison into grouped bar."""
        rev1 = float(data.get("revenue", {}).get("total", 0))
        exp1 = float(data.get("operating_expenses", {}).get("total", 0))
        cogs1 = float(data.get("cost_of_goods_sold", {}).get("total", 0))
        ni1 = float(data.get("net_income", 0))
        comp = data.get("comparison", {})
        rev2 = float(comp.get("revenue", {}).get("total", 0))
        exp2 = float(comp.get("operating_expenses", {}).get("total", 0))
        cogs2 = float(comp.get("cost_of_goods_sold", {}).get("total", 0))
        ni2 = float(comp.get("net_income", 0))
        spec["labels"] = ["Pendapatan", "HPP", "Beban Operasi", "Laba Bersih"]
        spec["datasets"] = [
            {
                "key": "period1",
                "label": "Periode 1",
                "values": [rev1, cogs1, exp1, ni1],
                "color": "#3B9FE8",
            },
            {
                "key": "period2",
                "label": "Periode 2",
                "values": [rev2, cogs2, exp2, ni2],
                "color": "#F5A623",
            },
        ]
        return spec

    def _chart_gross_margin(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into revenue/COGS/gross profit bars."""
        revenue = float(data.get("total_pendapatan", 0))
        cogs = float(data.get("total_hpp", data.get("harga_pokok", {}).get("total", 0)))
        gross = float(data.get("laba_kotor", revenue - cogs))
        spec["labels"] = ["Pendapatan", "HPP", "Laba Kotor"]
        spec["datasets"] = [
            {"key": "amount", "label": "Jumlah", "values": [revenue, cogs, gross]}
        ]
        margin_pct = (gross / revenue * 100) if revenue > 0 else 0
        spec["highlights"] = [
            {
                "label": "Margin",
                "value": f"{margin_pct:.1f}%",
                "color": "success" if margin_pct > 20 else "danger",
            }
        ]
        return spec

    def _chart_monthly_cashflow(self, data: dict, spec: dict) -> dict:
        """Transform arus-kas into monthly area chart (same as cash_flow but area)."""
        return self._chart_cash_flow(data, spec)

    # ═══════════════ BATCH 3: AR/AP ═══════════════

    def _chart_ap_aging(self, data: dict, spec: dict) -> dict:
        """Transform ap-aging into stacked bar."""
        summary = data.get("summary", data)
        labels = [
            "Lancar",
            "1-30 hari",
            "31-60 hari",
            "61-90 hari",
            "91-120 hari",
            ">120 hari",
        ]
        values = [
            float(summary.get("total_current", 0)),
            float(summary.get("total_1_30", 0)),
            float(summary.get("total_31_60", 0)),
            float(summary.get("total_61_90", 0)),
            float(summary.get("total_91_120", 0)),
            float(summary.get("total_over_120", 0)),
        ]
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "amount", "label": "Hutang", "values": values, "color": "#C45C4B"}
        ]
        total = float(summary.get("grand_total", sum(values)))
        spec["highlights"] = [
            {
                "label": "Total AP",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec

    def _chart_ar_summary(self, data: dict, spec: dict) -> dict:
        """Transform dashboard piutang into donut."""
        spec["slices"] = []
        for key, label in [
            ("current", "Lancar"),
            ("overdue_1_30", "1-30 hari"),
            ("overdue_31_60", "31-60 hari"),
            ("overdue_61_90", "61-90 hari"),
            ("overdue_90_plus", "90+ hari"),
        ]:
            val = float(data.get(key, 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = float(data.get("total", 0))
        spec["summary"] = f"Total piutang: Rp {total:,.0f}".replace(",", ".")
        if not spec["slices"] and total > 0:
            spec["slices"] = [{"label": "Lancar", "value": total}]
        return spec

    def _chart_ap_summary(self, data: dict, spec: dict) -> dict:
        """Transform dashboard hutang into donut."""
        spec["slices"] = []
        for key, label in [
            ("current", "Lancar"),
            ("overdue_1_30", "1-30 hari"),
            ("overdue_31_60", "31-60 hari"),
            ("overdue_61_90", "61-90 hari"),
            ("overdue_90_plus", "90+ hari"),
        ]:
            val = float(data.get(key, 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = float(data.get("total", 0))
        spec["summary"] = f"Total hutang: Rp {total:,.0f}".replace(",", ".")
        if not spec["slices"] and total > 0:
            spec["slices"] = [{"label": "Lancar", "value": total}]
        return spec

    def _chart_invoice_status(self, data: dict, spec: dict) -> dict:
        """Transform sales-invoices/summary into donut."""
        status_map = [
            ("draft_count", "Draft"),
            ("posted_count", "Posted"),
            ("partial_count", "Partial"),
            ("paid_count", "Lunas"),
            ("overdue_count", "Overdue"),
        ]
        spec["slices"] = [
            {"label": label, "value": float(data.get(key, 0))}
            for key, label in status_map
            if float(data.get(key, 0)) > 0
        ]
        total = int(data.get("total_count", 0))
        outstanding = float(data.get("total_outstanding", 0))
        spec[
            "summary"
        ] = f"{total} invoice, outstanding: Rp {outstanding:,.0f}".replace(",", ".")
        spec["value_format"] = "number"
        return spec

    def _chart_bill_status(self, data: dict, spec: dict) -> dict:
        """Transform bills/summary into donut."""
        breakdown = data.get("breakdown", {})
        spec["slices"] = []
        for key, label in [
            ("paid", "Lunas"),
            ("partial", "Partial"),
            ("unpaid", "Belum Bayar"),
            ("overdue", "Overdue"),
        ]:
            val = float(breakdown.get(key, {}).get("count", 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = int(data.get("total_count", 0))
        spec["summary"] = f"Total {total} tagihan"
        spec["value_format"] = "number"
        return spec

    def _chart_payment_trends(self, data: dict, spec: dict) -> dict:
        """Transform bill-payments/summary into bar by method."""
        by_method = data.get("by_method", {})
        spec["labels"] = []
        values = []
        method_labels = {
            "bank_transfer": "Transfer Bank",
            "cash": "Tunai",
            "cheque": "Cek/Giro",
            "other": "Lainnya",
        }
        for method, info in by_method.items():
            spec["labels"].append(method_labels.get(method, method))
            values.append(float(info.get("amount", 0)))
        spec["datasets"] = [
            {"key": "amount", "label": "Jumlah", "values": values, "color": "#3B9FE8"}
        ]
        total = float(data.get("total_paid", 0))
        spec["highlights"] = [
            {
                "label": "Total Bayar",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec

    # ═══════════════ BATCH 4: Inventory & Products ═══════════════

    def _chart_top_products(self, data: dict, spec: dict) -> dict:
        """Transform top-products into horizontal bar."""
        products = data.get("products", [])
        spec["labels"] = [
            self._truncate(str(p.get("product_name", ""))) for p in products[:10]
        ]
        spec["datasets"] = [
            {
                "key": "qty",
                "label": "Qty Terjual",
                "values": [float(p.get("total_qty_sold", 0)) for p in products[:10]],
                "color": "#5B8C51",
            }
        ]
        spec["value_format"] = "number"
        return spec

    def _chart_product_margins(self, data: dict, spec: dict) -> dict:
        """Transform product-margins into grouped bar."""
        products = data.get("products", [])
        top = [p for p in products if float(p.get("total_revenue", 0)) > 0][:10]
        spec["labels"] = [
            self._truncate(str(p.get("product_name", "")), 20) for p in top
        ]
        spec["datasets"] = [
            {
                "key": "revenue",
                "label": "Revenue",
                "values": [float(p.get("total_revenue", 0)) for p in top],
                "color": "#5B8C51",
            },
            {
                "key": "cogs",
                "label": "HPP",
                "values": [float(p.get("total_cogs", 0)) for p in top],
                "color": "#C45C4B",
            },
            {
                "key": "profit",
                "label": "Profit",
                "values": [float(p.get("total_profit", 0)) for p in top],
                "color": "#3B9FE8",
            },
        ]
        return spec

    def _chart_slow_moving(self, data: dict, spec: dict) -> dict:
        """Transform slow-moving-products into horizontal bar."""
        products = data.get("products", [])
        spec["labels"] = [
            self._truncate(str(p.get("product_name", ""))) for p in products[:10]
        ]
        spec["datasets"] = [
            {
                "key": "qty",
                "label": "Qty Terjual",
                "values": [float(p.get("total_qty_sold", 0)) for p in products[:10]],
                "color": "#F5A623",
            }
        ]
        if not products:
            spec["summary"] = "Tidak ada produk slow-moving."
        spec["value_format"] = "number"
        return spec

    def _chart_sales_trend(self, data: dict, spec: dict) -> dict:
        """Transform daily-summary into line chart."""
        items = normalize_api_response(data)
        if isinstance(items, list) and items:
            spec["labels"] = [str(i.get("date", i.get("tanggal", ""))) for i in items]
            spec["datasets"] = [
                {
                    "key": "total",
                    "label": "Penjualan",
                    "values": [
                        float(i.get("total", i.get("total_amount", 0))) for i in items
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "count",
                    "label": "Jumlah Transaksi",
                    "values": [
                        float(i.get("count", i.get("transaction_count", 0)))
                        for i in items
                    ],
                    "color": "#3B9FE8",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data penjualan."
        return spec

    def _chart_top_vendors(self, data: dict, spec: dict) -> dict:
        """Transform vendors list into horizontal bar by AP balance."""
        items = normalize_api_response(data)
        if isinstance(items, list):
            sorted_v = sorted(
                items, key=lambda v: float(v.get("ap_balance", 0)), reverse=True
            )[:10]
            spec["labels"] = [
                self._truncate(str(v.get("name", v.get("display_name", ""))))
                for v in sorted_v
            ]
            spec["datasets"] = [
                {
                    "key": "ap_balance",
                    "label": "Saldo Hutang",
                    "values": [float(v.get("ap_balance", 0)) for v in sorted_v],
                    "color": "#C45C4B",
                }
            ]
        return spec

    # ═══════════════ BATCH 5: Financial Ratios ═══════════════

    def _chart_profitability_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios profitability section into bar."""
        ratios = data.get("ratios", {}).get("profitability", {})
        labels, values = [], []
        for key, label in [
            ("roa", "ROA"),
            ("roe", "ROE"),
            ("net_profit_margin", "Net Margin"),
            ("gross_profit_margin", "Gross Margin"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "pct", "label": "%", "values": values, "color": "#5B8C51"}
        ]
        spec["value_format"] = "percent"
        return spec

    def _chart_liquidity_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios liquidity section into bar."""
        ratios = data.get("ratios", {}).get("liquidity", {})
        labels, values = [], []
        for key, label in [
            ("cash_ratio", "Cash Ratio"),
            ("quick_ratio", "Quick Ratio"),
            ("current_ratio", "Current Ratio"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "ratio", "label": "Rasio", "values": values, "color": "#3B9FE8"}
        ]
        wc = ratios.get("working_capital", {}).get("value")
        if wc is not None:
            spec["highlights"] = [
                {
                    "label": "Working Capital",
                    "value": f"Rp {float(wc):,.0f}".replace(",", "."),
                    "color": "neutral",
                }
            ]
        spec["value_format"] = "number"
        return spec

    def _chart_leverage_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios leverage section into bar."""
        ratios = data.get("ratios", {}).get(
            "leverage", data.get("ratios", {}).get("solvency", {})
        )
        labels, values = [], []
        for key, label in [
            ("debt_to_equity", "Debt/Equity"),
            ("debt_to_asset", "Debt/Asset"),
            ("equity_ratio", "Equity Ratio"),
            ("debt_ratio", "Debt Ratio"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "ratio", "label": "Rasio", "values": values, "color": "#F5A623"}
        ]
        spec["value_format"] = "number"
        return spec

    def _chart_ratio_dashboard(self, data: dict, spec: dict) -> dict:
        """Transform all financial-ratios into grouped bar dashboard."""
        all_ratios = data.get("ratios", {})
        labels, prof_vals, liq_vals, lev_vals = [], [], [], []
        # Profitability
        for key, label in [
            ("roa", "ROA"),
            ("roe", "ROE"),
            ("net_profit_margin", "Net Margin"),
        ]:
            r = all_ratios.get("profitability", {}).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(float(val))
                liq_vals.append(0)
                lev_vals.append(0)
        # Liquidity
        for key, label in [("current_ratio", "Current"), ("quick_ratio", "Quick")]:
            r = all_ratios.get("liquidity", {}).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(0)
                liq_vals.append(float(val))
                lev_vals.append(0)
        # Leverage
        for key, label in [("debt_to_equity", "D/E"), ("debt_to_asset", "D/A")]:
            r = all_ratios.get("leverage", all_ratios.get("solvency", {})).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(0)
                liq_vals.append(0)
                lev_vals.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {
                "key": "profitability",
                "label": "Profitabilitas",
                "values": prof_vals,
                "color": "#5B8C51",
            },
            {
                "key": "liquidity",
                "label": "Likuiditas",
                "values": liq_vals,
                "color": "#3B9FE8",
            },
            {
                "key": "leverage",
                "label": "Leverage",
                "values": lev_vals,
                "color": "#F5A623",
            },
        ]
        spec["value_format"] = "number"
        return spec

    # ═══════════════ BATCH 6: Budget & Production ═══════════════

    def _chart_budget_vs_actual(self, data: dict, spec: dict) -> dict:
        """Transform budget vs-actual into grouped bar."""
        items = normalize_api_response(data)
        if isinstance(items, list) and items:
            spec["labels"] = [
                self._truncate(str(i.get("account_name", i.get("name", ""))))
                for i in items[:15]
            ]
            spec["datasets"] = [
                {
                    "key": "budget",
                    "label": "Budget",
                    "values": [
                        float(i.get("budget_amount", i.get("budgeted", 0)))
                        for i in items[:15]
                    ],
                    "color": "#3B9FE8",
                },
                {
                    "key": "actual",
                    "label": "Aktual",
                    "values": [
                        float(i.get("actual_amount", i.get("actual", 0)))
                        for i in items[:15]
                    ],
                    "color": "#5B8C51",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data budget."
        return spec

    def _chart_variance_alerts(self, data: dict, spec: dict) -> dict:
        """Transform variance-alerts into horizontal bar."""
        alerts = normalize_api_response(data)
        if isinstance(alerts, list) and alerts:
            spec["labels"] = [
                self._truncate(str(a.get("account_name", a.get("name", ""))))
                for a in alerts[:10]
            ]
            spec["datasets"] = [
                {
                    "key": "variance_pct",
                    "label": "Varians %",
                    "values": [
                        float(a.get("variance_pct", a.get("variance_percent", 0)))
                        for a in alerts[:10]
                    ],
                    "color": "#C45C4B",
                }
            ]
        else:
            spec["summary"] = "Tidak ada peringatan varians."
        return spec

    def _chart_production_costs(self, data: dict, spec: dict) -> dict:
        """Transform production cost-analysis into bar."""
        material = float(data.get("material_cost", data.get("total_material", 0)))
        labor = float(data.get("labor_cost", data.get("total_labor", 0)))
        overhead = float(data.get("overhead_cost", data.get("total_overhead", 0)))
        total = float(data.get("total_cost", material + labor + overhead))
        spec["labels"] = ["Material", "Tenaga Kerja", "Overhead"]
        spec["datasets"] = [
            {
                "key": "cost",
                "label": "Biaya",
                "values": [material, labor, overhead],
                "color": "#3B9FE8",
            }
        ]
        spec["highlights"] = [
            {
                "label": "Total Biaya",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec

    # --- Helpers ---

    # --- Update Document Context (Layer 2 document edit) ---

    async def _execute_update_document_context(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle user corrections to active document context.
        Merges edits into Layer 2 document_context, expires old pending action.
        """
        import uuid as _uuid

        edits = params.get("edits", {})
        if not edits:
            return {"success": False, "error": "No edits provided"}

        if not self.session_manager or not self.session_id:
            return {"success": False, "error": "No session context available"}

        state = await self.session_manager.get_state(self.session_id)
        doc_ctx = getattr(state, "document_context", None)
        if not doc_ctx:
            return {
                "success": False,
                "error": "Tidak ada dokumen aktif. User belum upload dokumen.",
            }

        # Deep-merge edits
        existing_edits = doc_ctx.get("edits", {})
        for key, value in edits.items():
            if key == "items" and isinstance(value, dict):
                existing_items_edits = existing_edits.get("items", {})
                for idx_str, item_edits in value.items():
                    if idx_str in existing_items_edits:
                        existing_items_edits[idx_str] = {
                            **existing_items_edits[idx_str],
                            **item_edits,
                        }
                    else:
                        existing_items_edits[idx_str] = item_edits
                existing_edits["items"] = existing_items_edits
            else:
                existing_edits[key] = value
        doc_ctx["edits"] = existing_edits

        # Expire old pending action
        old_pending_id = doc_ctx.get("pending_action_id")
        if old_pending_id:
            try:
                from .db_utils import get_session_db_pool

                pool = await get_session_db_pool()
                await pool.execute(
                    "UPDATE pending_actions SET status = 'EXPIRED' WHERE id = $1 AND tenant_id = $2",
                    _uuid.UUID(str(old_pending_id)),
                    self.context.tenant_id,
                )
            except Exception as e:
                logger.warning(
                    f"[UpdateDocCtx] Failed to expire old pending action: {e}"
                )

        # Update Layer 2
        await self.session_manager.update_state(
            self.session_id, document_context=doc_ctx
        )

        # Build summary
        edit_parts = []
        for k, v in edits.items():
            if k != "items":
                edit_parts.append(f"{k}={v}")
        if "items" in edits:
            edit_parts.append(f"{len(edits['items'])} item dikoreksi")

        return {
            "success": True,
            "message": f"Koreksi diterapkan: {', '.join(edit_parts)}. Data dokumen sudah diperbarui.",
            "replaces_action_id": old_pending_id,
            "document_id": doc_ctx.get("document_id"),
        }


def normalize_api_response(data) -> list:
    """Extract entity list from any API response shape.

    Single source of truth for response key normalization.
    Handles: {"data": [...]}, {"items": [...]}, {"results": [...]},
             {"invoices": [...]}, {"expenses": [...]}, plain list, single dict.

    Phase 0 of EntityContextManager migration.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return [data] if data else []

    # Check known list keys (order = most common first)
    for key in (
        "data",
        "items",
        "results",
        "invoices",
        "bills",
        "expenses",
        "accounts",
        "vendors",
        "customers",
        "alerts",
        "projections",
        "trend",
        "line_items",
        "lines",
    ):
        val = data.get(key)
        if isinstance(val, list):
            return val

    # Single entity dict (has "id" field)
    if "id" in data:
        return [data]

    return []  # truly empty


def normalize_api_response_or_dict(data) -> any:
    """Like normalize_api_response but preserves dict metadata (total, summary, has_more).

    Returns the original dict with list extracted, or just the list.
    Used when callers need both the list AND metadata like total/summary.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return data

    for key in (
        "data",
        "items",
        "results",
        "invoices",
        "bills",
        "expenses",
        "accounts",
        "vendors",
        "customers",
    ):
        val = data.get(key)
        if isinstance(val, list):
            return val

    return data


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


def _is_valid_uuid(value: str) -> bool:
    return bool(UUID_PATTERN.match(value))


def _validate_amounts(payload: Dict[str, Any]) -> Optional[str]:
    amount_fields = ["amount", "unit_price", "total"]
    for key, value in payload.items():
        if key in amount_fields:
            if not isinstance(value, (int, float)) or value < 0:
                return f"Field {key!r} harus bilangan positif."
            if value > MAX_AMOUNT:
                return f"Field {key!r} melebihi batas maksimum."
        if key == "items" and isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    for f in amount_fields:
                        if f in item:
                            v = item[f]
                            if not isinstance(v, (int, float)) or v < 0:
                                return f"Item [{i}].{f} harus bilangan positif."
                            if v > MAX_AMOUNT:
                                return f"Item [{i}].{f} melebihi batas maksimum."
                    if "quantity" in item:
                        q = item["quantity"]
                        if not isinstance(q, (int, float)) or q <= 0:
                            return f"Item [{i}].quantity harus > 0."
    return None


def _generate_idempotency_key(
    tenant_id: str, action_type: str, payload: Dict[str, Any]
) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str)
    # 10-second window: prevents double-click, allows re-creation after
    # Actual execution idempotency is enforced by pending_action_id + confirm flow
    time_window = str(int(time.time()) // 10)
    raw = f"{tenant_id}:{action_type}:{normalized}:{time_window}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _truncate_result(data: Any) -> Any:
    serialized = json.dumps(data, default=str)
    if len(serialized) <= MAX_RESPONSE_SIZE:
        return data
    if isinstance(data, list) and len(data) > MAX_LIST_ITEMS:
        return data[:MAX_LIST_ITEMS] + [
            {"_truncated": True, "_total": len(data), "_showing": MAX_LIST_ITEMS}
        ]
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
                data[key] = value[:MAX_LIST_ITEMS] + [
                    {
                        "_truncated": True,
                        "_total": len(value),
                        "_showing": MAX_LIST_ITEMS,
                    }
                ]
    return data


# ─── review_card helpers (Bridge: Tahap 5F backend) ───────────────────────────


def _fmt_idr(amount) -> str:
    """Format number as Indonesian currency string (dots as thousands)."""
    try:
        return f"{abs(float(amount)):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "0"


def _build_review_card(
    statement_line: dict,
    bill_suggestion: dict | None,
    invoice_suggestion: dict | None,
    category_suggestion: dict | None,
    bank_account_name: str,
    item_number: int,
    total_items: int,
) -> dict:
    """
    Build review_card payload for frontend InlineCard rendering.
    Called inside _execute_start_workflow after auto-propose succeeds.
    """
    line_amount = abs(float(statement_line.get("amount", 0)))
    is_credit = bool(statement_line.get("is_credit", False))

    # Variant label from match type
    if bill_suggestion:
        confidence = bill_suggestion.get("confidence", "LOW")
        variant = "auto-match" if confidence == "HIGH" else "suggested"
    elif invoice_suggestion:
        confidence = invoice_suggestion.get("confidence", "LOW")
        variant = "auto-match" if confidence == "HIGH" else "suggested"
    elif category_suggestion:
        variant = "suggested"
    else:
        variant = "kategorisasi"

    title_label = f"Review {item_number} / {total_items} \u00b7 {variant}"

    # Match section
    match_data = None
    if bill_suggestion:
        match_data = {
            "type": "bill",
            "label": "Cocokkan",
            "name": f"Bill {bill_suggestion.get('bill_number', '')}",
            "detail": f"{bill_suggestion.get('vendor_name', '')} \u00b7 outstanding {_fmt_idr(bill_suggestion.get('amount_due', 0))}",
            "amount": float(bill_suggestion.get("amount_due", 0)),
        }
    elif invoice_suggestion:
        match_data = {
            "type": "invoice",
            "label": "Cocokkan",
            "name": f"Invoice {invoice_suggestion.get('invoice_number', '')}",
            "detail": f"{invoice_suggestion.get('customer_name', '')} \u00b7 outstanding {_fmt_idr(invoice_suggestion.get('amount_due', 0))}",
            "amount": float(invoice_suggestion.get("amount_due", 0)),
        }
    elif category_suggestion:
        match_data = {
            "type": "expense_account",
            "label": "Catat ke",
            "name": f"{category_suggestion.get('account_code', '')} {category_suggestion.get('account_name', '')}",
            "detail": "",
        }

    # Warning (amount mismatch)
    warning = None
    if match_data and match_data.get("amount"):
        diff = abs(line_amount - match_data["amount"])
        if diff > 0.01:
            warning = f"Selisih {_fmt_idr(diff)} \u2014 pembayaran sebagian"

    # Journal preview lines
    journal_lines = _build_journal_preview(
        statement_line,
        bill_suggestion,
        invoice_suggestion,
        category_suggestion,
        bank_account_name,
    )

    # Button labels
    if bill_suggestion or invoice_suggestion:
        confirm_label = "Ok, cocokkan"
    elif category_suggestion:
        confirm_label = "Ok, catat biaya"
    else:
        confirm_label = "Ok"

    return {
        "title_label": title_label,
        "statement": {
            "description": statement_line.get("description", ""),
            "date": str(
                statement_line.get("date", "")
                or statement_line.get("transaction_date", "")
            ),
            "amount": line_amount,
            "is_credit": is_credit,
        },
        "match": match_data,
        "warning": warning,
        "journal_lines": journal_lines,
        "cancel_label": "Lewati",
        "confirm_label": confirm_label,
    }


def _build_journal_preview(
    statement_line: dict,
    bill_suggestion: dict | None,
    invoice_suggestion: dict | None,
    category_suggestion: dict | None,
    bank_account_name: str,
) -> list:
    """Build journal preview Dr/Cr lines based on match type."""
    amount = abs(float(statement_line.get("amount", 0)))
    is_credit = bool(statement_line.get("is_credit", False))

    if bill_suggestion:
        return [
            {"dir": "Dr", "account": "Hutang Usaha", "amount": amount},
            {"dir": "Cr", "account": bank_account_name, "amount": amount},
        ]
    elif invoice_suggestion:
        return [
            {"dir": "Dr", "account": bank_account_name, "amount": amount},
            {"dir": "Cr", "account": "Piutang Usaha", "amount": amount},
        ]
    elif category_suggestion:
        cat_name = f"{category_suggestion.get('account_code', '')} {category_suggestion.get('account_name', '')}"
        if is_credit:
            return [
                {"dir": "Dr", "account": bank_account_name, "amount": amount},
                {"dir": "Cr", "account": cat_name, "amount": amount},
            ]
        else:
            return [
                {"dir": "Dr", "account": cat_name, "amount": amount},
                {"dir": "Cr", "account": bank_account_name, "amount": amount},
            ]

    return []


# ─── Tool Stage Labels (Thinking Indicator) ────────────────────────────────────


TOOL_STAGE_LABELS: dict[str, str] = {
    # === Kas & Bank ===
    "get_bank_accounts": "Mencari rekening bank",
    "get_bank_transactions": "Memeriksa mutasi bank",
    "get_bank_balance": "Memeriksa saldo",
    "get_bank_transactions": "Memeriksa mutasi bank",  # noqa: F601
    "get_bank_statement_sessions": "Memeriksa rekening koran",
    # === Jurnal & Ledger ===
    "get_journal_entries": "Memeriksa jurnal",
    "get_journal_lines": "Memeriksa buku besar",
    "get_journal_detail": "Membaca detail jurnal",
    # === Chart of Accounts ===
    "get_chart_of_accounts": "Memeriksa daftar akun",
    "get_account_detail": "Membaca detail akun",
    # === Faktur & Piutang ===
    "get_sales_invoices": "Memeriksa faktur penjualan",
    "get_sales_invoice_detail": "Membaca detail faktur",
    "get_customers": "Memeriksa data pelanggan",
    "get_customer_detail": "Membaca detail pelanggan",
    # === Bill & Hutang ===
    "get_bills": "Memeriksa tagihan",
    "get_bill_detail": "Membaca detail tagihan",
    "get_vendors": "Memeriksa data vendor",
    # === Produk & Inventori ===
    "get_products": "Memeriksa data produk",
    "get_top_products": "Menganalisis penjualan produk",
    "get_slow_moving_products": "Menganalisis produk lambat terjual",
    "get_product_margins": "Menganalisis margin produk",
    # === Rasio Keuangan ===
    "get_financial_ratios": "Menganalisis rasio keuangan",
    "get_ratio_dashboard": "Menyusun dashboard rasio",
    "get_ratio_trend": "Menganalisis tren rasio",
    "get_ratio_alerts": "Memeriksa alert keuangan",
    # === Budget ===
    "get_budgets": "Memeriksa daftar budget",
    "get_budget_detail": "Menganalisis budget vs aktual",
    # === Cost Center ===
    "get_cost_centers": "Memeriksa cost center",
    "get_cost_center_summary": "Menganalisis biaya departemen",
    # === Sprint 2: Cash & Payment Workflows ===
    "get_bank_transfers": "Memeriksa transfer bank",
    "get_bank_transfer_detail": "Melihat detail transfer bank",
    "get_bank_transfer_summary": "Meringkas transfer bank",
    "get_vendor_deposits": "Memeriksa uang muka vendor",
    "get_vendor_deposit_detail": "Melihat detail deposit vendor",
    "get_customer_deposits": "Memeriksa uang muka pelanggan",
    "get_customer_deposit_detail": "Melihat detail deposit pelanggan",
    "get_cheques": "Memeriksa daftar giro",
    # === Sprint 3: Recurring & Pipeline ===
    "get_recurring_invoices": "Memeriksa faktur berulang",
    "get_recurring_invoices_due": "Memeriksa faktur berulang jatuh tempo",
    "get_recurring_bills": "Memeriksa tagihan berulang",
    "get_recurring_bills_due": "Memeriksa tagihan berulang jatuh tempo",
    "get_sales_orders": "Memeriksa sales order",
    "get_sales_order_detail": "Melihat detail sales order",
    "get_quotes": "Memeriksa penawaran",
    # Sprint 4: Asset & Inventory Operations
    "get_fixed_assets": "Memeriksa aset tetap",
    "get_fixed_asset_detail": "Memeriksa detail aset",
    "get_stock_adjustments": "Memeriksa penyesuaian stok",
    "get_stock_adjustment_detail": "Memeriksa detail penyesuaian stok",
    "get_payroll_summary": "Memeriksa ringkasan penggajian",
    "get_product_detail": "Membaca detail produk",
    "get_warehouse_stock": "Memeriksa stok gudang",
    "search_items": "Mencari barang",
    "search_customers": "Mencari pelanggan",
    "search_vendors": "Mencari vendor",
    "search_accounts": "Mencari akun",
    "search_bank_accounts": "Mencari rekening bank",
    "get_customer_invoices": "Mencari faktur pelanggan",
    "get_vendor_bills": "Mencari tagihan vendor",
    # === Laporan ===
    "get_trial_balance": "Menyusun neraca saldo",
    "get_profit_loss": "Menyusun laporan laba rugi",
    "get_balance_sheet": "Menyusun neraca",
    "get_cashflow": "Menyusun laporan arus kas",
    # === Rekonsiliasi ===
    "get_reconciliation_workspace": "Memeriksa rekonsiliasi",
    "run_auto_match": "Mencocokkan transaksi",
    "review_next_unmatched": "Memeriksa transaksi belum cocok",
    # === Action tools ===
    "propose_action": "Menyiapkan transaksi",
    "propose_direct_action": "Menyiapkan data",
    "simulate_action": "Mensimulasikan dampak",
    "start_workflow": "Memulai proses",
    "cancel_workflow": "Membatalkan proses",
    # === Session tools ===
    "get_session_events": "Membaca riwayat sesi",
    "search_chat_history": "Mencari riwayat chat",
    # === Tutorial tools ===
    "get_tutorial": "Memuat tutorial",
    "list_tutorials": "Menampilkan daftar tutorial",
    "start_tutorial": "Memulai tutorial",
    "advance_tutorial": "Melanjutkan tutorial",
    "dismiss_tutorial": "Melewatkan tutorial",
    # === Fallback ===
    "_composing": "Menyusun jawaban",
    "_default": "Sedang berpikir",
}


def get_stage_label(tool_name: str) -> str:
    """Get Indonesian stage label for a tool call."""
    return TOOL_STAGE_LABELS.get(tool_name, TOOL_STAGE_LABELS["_default"])
