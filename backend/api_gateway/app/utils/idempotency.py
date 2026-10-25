"""
Idempotency helper for Law 14 compliance.

Usage:
    async with conn.transaction():
        result = await execute_idempotent(
            conn, tenant_id,
            idempotency_key=f"BILL_PAYMENT:{bill_id}:{amount}",
            source_type="BILL_PAYMENT",
            operation=lambda: create_payment_journal(...)
        )
        if result.was_cached:
            return result.data  # Return cached result, no new journal created

Key generation convention:
    f"{SOURCE_TYPE}:{entity_id}" — for operations on existing entities
    f"{SOURCE_TYPE}:{entity_id}:{amount}" — for payment-type operations
    Client can also pass custom key via X-Idempotency-Key header.
"""
import hashlib
import json
import logging
from decimal import Decimal
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# Medan sidik isi di dalam idempotency_keys.result (execute_idempotent); dibuang sebelum replay.
_KUNCI_SIDIK = "_idem_payload_hash"


@dataclass
class IdempotencyResult:
    """Result of an idempotent operation."""
    data: Any
    was_cached: bool
    idempotency_key: str


# ── Replay hanya bila DOKUMEN SUMBER masih ada (unit idempotensi-sumber, 21 Okt 2026) ──
# Kunci tersimpan = respons dokumen yang PERNAH dibuat. Dokumen dihapus (draf) / dibatalkan sesudahnya ->
# replay mengembalikan id HANTU (201 lalu GET 404). Peta di bawah = source_type "buat dokumen" -> (tabel, kolom
# status, status yang membuat replay kunci-DEFAULT-isi basi). Literal kode, bukan masukan pengguna.
#   dokumen TIDAK ADA            -> basi untuk SEMUA jalur (kunci default maupun header klien)
#   dokumen berstatus di daftar  -> basi HANYA untuk kunci default-isi (tak ada niat klien; orang membatalkan lalu
#                                   mencatat ulang yang sama). Kunci header klien tetap replay (semantik retry).
# Source_type aksi (void/apply/fulfill...) TIDAK ada di peta -> tak diperiksa (id-nya dokumen yang ada).
SUMBER_BUAT = {
    "RECEIVE_PAYMENT": ("receive_payments", "status", ("voided",)),
    "BILL_PAYMENT": ("bill_payments_v2", "status", ("voided",)),
    "SALES_ORDER_CREATE": ("sales_orders", None, ()),
    "QUOTE_CREATE": ("quotes", None, ()),
    "SI_CREATE": ("sales_invoices", None, ()),
    "PROFORMA_CREATE": ("proformas", None, ()),
    "CN_CREATE": ("credit_notes", None, ()),
    "PO_CREATE": ("purchase_orders", None, ()),  # P-U2: PO terhapus -> kunci basi, proses baru
    "VD_CREATE": ("vendor_deposits", None, ()),  # P-U3b: uang muka vendor draf terhapus -> kunci basi, proses baru
    "BILL_CREATE": ("bills", None, ()),
    "VC_CREATE": ("vendor_credits", None, ()),
    "VENDOR_CREATE": ("vendors", None, ()),  # VENDOR-IDEM 25 Okt: vendor terhapus keras -> kunci basi, proses baru  # 22 Okt 2026 (Law 14): nota debit draf terhapus -> kunci basi, proses baru  # 22 Okt 2026 (Law 14): faktur pembelian draf terhapus -> kunci basi, proses baru
}


def id_dokumen_tersimpan(result, result_id=None):
    """UUID dokumen dari result tersimpan (bentuk lama {data:{id}} / klien {response:{id|data:{id}}}) atau kolom result_id."""
    kandidat = [result_id]
    if isinstance(result, dict):
        resp = result.get("response") if isinstance(result.get("response"), dict) else result
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        kandidat += [data.get("id"), resp.get("id"), resp.get("created_payment_id")]
    for k in kandidat:
        if k:
            try:
                return UUID(str(k))
            except (ValueError, TypeError):
                continue
    return None


async def sumber_basi(conn, tenant_id: str, key: str, source_type: str, result, result_id=None,
                      kunci_default: bool = False) -> bool:
    """True bila entri idempotensi ini BASI (dokumen sumbernya tak ada, atau dibatalkan untuk kunci default-isi).
    Basi -> barisnya DIHAPUS di transaksi pemanggil (rollback memulihkannya) dan pemanggil memproses sebagai MISS.
    source_type di luar SUMBER_BUAT / id tak terbaca -> False (perilaku lama; tak pernah menebak)."""
    peta = SUMBER_BUAT.get(source_type)
    if not peta:
        return False
    doc_id = id_dokumen_tersimpan(result, result_id)
    if doc_id is None:
        return False
    tabel, kolom_status, status_basi = peta
    if kolom_status and status_basi and kunci_default:
        row = await conn.fetchrow(
            f"SELECT {kolom_status} AS st FROM {tabel} WHERE id = $1 AND tenant_id = $2", doc_id, tenant_id)  # nosec B608
        basi = row is None or row["st"] in status_basi
    else:
        basi = not await conn.fetchval(
            f"SELECT 1 FROM {tabel} WHERE id = $1 AND tenant_id = $2", doc_id, tenant_id)  # nosec B608
    if basi:
        logger.info(f"Idempotency basi dibuang: {source_type} key={key} dokumen={doc_id}")
        await conn.execute("DELETE FROM idempotency_keys WHERE tenant_id = $1 AND key = $2", tenant_id, key)
    return basi


async def execute_idempotent(
    conn,
    tenant_id: str,
    idempotency_key: str,
    source_type: str,
    operation: Callable[[], Awaitable[dict]],
    ttl_hours: int = 24,
    payload_hash: Optional[str] = None,
    kunci_default: bool = False,
) -> IdempotencyResult:
    """
    Execute an operation with idempotency guarantee.

    Same tenant_id + idempotency_key within TTL → return cached result.
    Different key or expired → execute operation and cache result.

    MUST be called inside a transaction (for advisory lock + atomic insert).

    Args:
        conn: Database connection (inside transaction)
        tenant_id: Tenant identifier
        idempotency_key: Unique key for this operation
        source_type: Journal source type (e.g., 'BILL_PAYMENT')
        operation: Async callable that returns a dict result
        ttl_hours: How long to cache the result (default 24h)

    Returns:
        IdempotencyResult with .data (the result) and .was_cached (bool)
    """
    # GATE: helper ini MENGANDALKAN pemanggil sudah di dalam transaksi (docstring
    # di atas menyatakannya, tapi pernyataan tanpa penegakan akan dilupakan).
    # Di luar transaksi, INSERT kunci commit terpisah dari operasinya -> crash di
    # antara keduanya = operasi tersimpan tanpa kunci = retry membuat DUPLIKAT.
    _iit = getattr(conn, "is_in_transaction", None)
    if callable(_iit) and not _iit():
        raise RuntimeError(
            "Law 14: execute_idempotent HARUS dipanggil di dalam transaksi "
            "(operasi + pencatatan kunci wajib atomik)."
        )

    # URUTAN PENTING: pemanggil WAJIB sudah mengambil advisory lock atas
    # idempotency_key SEBELUM memanggil helper ini. Kalau tidak, dua request
    # identik yang konkuren sama-sama MISS SELECT di bawah dan sama-sama jalan.
    # JANGAN membalik urutan ini saat refactor.
    existing = await conn.fetchrow(
        """
        SELECT result, result_id, result_status
        FROM idempotency_keys
        WHERE tenant_id = $1 AND key = $2 AND expires_at > NOW()
        """,
        tenant_id, idempotency_key
    )
    if existing and existing['result']:
        _res = existing['result']
        if isinstance(_res, str):
            _res = json.loads(_res)
        if await sumber_basi(conn, tenant_id, idempotency_key, source_type, _res,
                             existing['result_id'], kunci_default):
            existing = None

    if existing and existing['result']:
        logger.info(f"Idempotency hit: {source_type} key={idempotency_key}")
        data = json.loads(existing['result'])
        # C2 (26 Sep 2026): sidik isi disimpan di dalam result. Kunci sama + isi BEDA
        # = 409 (KunciIdempotensiDipakai), BUKAN replay diam respons lama — dulu
        # pengguna membetulkan nominal lalu kirim ulang: "sukses" dengan pembayaran
        # PERTAMA, nominal baru tak pernah tercatat. Baris tanpa sidik (lama / pemanggil
        # tanpa payload_hash) = perilaku lama (replay).
        tersimpan = data.pop(_KUNCI_SIDIK, None) if isinstance(data, dict) else None
        if payload_hash is not None and tersimpan is not None and tersimpan != payload_hash:
            raise KunciIdempotensiDipakai(data)
        return IdempotencyResult(
            data=data,
            was_cached=True,
            idempotency_key=idempotency_key,
        )

    # Execute the operation
    result = await operation()

    # Extract result_id if present
    result_id = None
    if isinstance(result, dict):
        for key in ('id', 'journal_id', 'payment_id'):
            if key in result and result[key]:
                try:
                    result_id = UUID(str(result[key]))
                except (ValueError, TypeError):
                    pass
                break

    # Store idempotency record
    await conn.execute(
        """
        INSERT INTO idempotency_keys (key, tenant_id, source_type, result, result_id, result_status, expires_at)
        VALUES ($1, $2, $3, $4, $5, 'SUCCESS', NOW() + make_interval(hours => $6))
        ON CONFLICT (tenant_id, key) DO NOTHING
        """,
        idempotency_key, tenant_id, source_type,
        json.dumps({**result, _KUNCI_SIDIK: payload_hash}
                   if payload_hash is not None and isinstance(result, dict) else result, default=str),
        result_id,
        ttl_hours,
    )

    return IdempotencyResult(
        data=result,
        was_cached=False,
        idempotency_key=idempotency_key,
    )


def get_idempotency_key(request, default_key: str) -> str:
    """
    Get idempotency key from X-Idempotency-Key header, falling back to default.

    Args:
        request: FastAPI request object
        default_key: Default key if header not provided

    Returns:
        The idempotency key to use
    """
    header_key = request.headers.get("X-Idempotency-Key")
    return header_key if header_key else default_key


def _norm_amount(x) -> str:
    """
    Normalisasi SATU komponen nominal untuk dipakai di kunci idempotency.

    KENAPA WAJIB: f"{x}" bergantung TIPE Python.
        Decimal("3500000.00") -> "3500000.00"
        float 3500000.0       -> "3500000.0"
        int 3500000           -> "3500000"
    Tiga string berbeda untuk SATU jumlah yang sama. Kalau FE atau Pydantic
    meng-coerce berbeda antar-jalur atau antar-versi, dedup GAGAL DIAM-DIAM —
    silent-fallback DI DALAM mekanisme anti-silent-fallback.

    None dipetakan eksplisit ke "0.00" supaya "None" dan "0.00" tak pernah
    menjadi dua kunci berbeda untuk keadaan yang sama.
    """
    if x is None:
        return "0.00"
    return str(Decimal(str(x)).quantize(Decimal("0.01")))


def build_idempotency_default(prefix: str, parts, allocations) -> str:
    """
    Bangun kunci deterministik sisi-server.

    `allocations` = iterable (doc_id, amount). DIURUTKAN supaya urutan baris tak
    mengubah kunci, dan PER-BARIS supaya total sama ke dokumen berbeda
    menghasilkan kunci berbeda.

    Nominal SELALU lewat _norm_amount(). Jangan pernah menyisipkan angka mentah.
    """
    alloc_s = "|".join(sorted(f"{d}:{_norm_amount(a)}" for d, a in allocations))
    alloc_h = hashlib.sha256(alloc_s.encode()).hexdigest()[:16]
    return ":".join([prefix, *[str(p) for p in parts], alloc_h])


# ── Kunci idempotency KIRIMAN KLIEN (W0 Conversational Workspace, 25 Sep 2026) ──
# Beda dengan build_idempotency_default: dokumen seperti SO boleh sah KEMBAR (dua
# pesanan identik untuk pelanggan yang sama), jadi TIDAK ada kunci default sisi server.
# Tanpa header = perilaku lama. Dengan header = satu kunci per pembukaan form di FE.
KUNCI_KLIEN_MAKS = 200


def kunci_idempotensi_klien(request) -> Optional[str]:
    """Kunci dari header X-Idempotency-Key (alias: Idempotency-Key), atau None.

    Kosong/spasi = None (bukan kunci ""); terlalu panjang = ValueError (pemanggil -> 400),
    bukan dipotong diam-diam — dua kunci panjang berbeda akan bertabrakan setelah dipotong.
    """
    mentah = request.headers.get("X-Idempotency-Key") or request.headers.get("Idempotency-Key")
    if mentah is None or not mentah.strip():
        return None
    kunci = mentah.strip()
    if len(kunci) > KUNCI_KLIEN_MAKS:
        raise ValueError(f"Idempotency-Key maksimal {KUNCI_KLIEN_MAKS} karakter")
    return kunci


def hash_payload(payload) -> str:
    """Sidik isi permintaan: kunci sama + isi beda = 409, bukan replay respons lain."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


class KunciIdempotensiDipakai(LookupError):
    """Kunci sama + isi beda. `respons` = respons ASLI tersimpan untuk kunci itu (ruang kunci
    tenant x pengguna x kunci -> selalu milik pemanggil yang sama; tak bisa bocor lintas tenant)."""

    def __init__(self, respons=None):
        super().__init__("Idempotency-Key sudah dipakai untuk permintaan dengan isi berbeda")
        self.respons = respons or {}


async def ambil_replay_klien(conn, tenant_id: str, kunci_penuh: str, sidik: str):
    """Cari respons SUKSES tersimpan untuk kunci ini. WAJIB di dalam transaksi, SESUDAH
    advisory lock per kunci (dua permintaan identik bersamaan tak boleh sama-sama MISS).

    Kembali: dict respons asli, atau None. Isi beda -> KunciIdempotensiDipakai (LookupError; pemanggil -> 409).
    """
    baris = await conn.fetchrow(
        "SELECT result, result_id, source_type FROM idempotency_keys WHERE tenant_id = $1 AND key = $2 AND expires_at > NOW()",
        tenant_id, kunci_penuh,
    )
    if not baris or baris["result"] is None:
        return None
    simpan = baris["result"]
    if isinstance(simpan, str):
        simpan = json.loads(simpan)
    # dokumen sumber sudah tak ada (draf dihapus) -> bukan replay: proses sebagai permintaan baru
    if await sumber_basi(conn, tenant_id, kunci_penuh, baris.get("source_type"), simpan, baris.get("result_id"), False):
        return None
    if simpan.get("payload_hash") != sidik:
        raise KunciIdempotensiDipakai(simpan.get("response"))
    return simpan.get("response")


async def simpan_replay_klien(conn, tenant_id: str, kunci_penuh: str, source_type: str,
                              sidik: str, respons: dict, result_id=None, ttl_hours: int = 24):
    """Catat respons SUKSES, di transaksi YANG SAMA dengan penulisan dokumennya: bila
    dokumen gagal (4xx/5xx, rollback) catatan ini ikut hilang -> kunci bebas dipakai ulang.

    Baris kedaluwarsa dengan kunci sama DITIMPA (PK tenant_id+key tetap ada sesudah
    expires_at; ON CONFLICT DO NOTHING akan diam-diam membuat kunci itu tak tercatat lagi).
    """
    await conn.execute(
        """
        INSERT INTO idempotency_keys (key, tenant_id, source_type, result, result_id, result_status, expires_at)
        VALUES ($1, $2, $3, $4, $5, 'SUCCESS', NOW() + make_interval(hours => $6))
        ON CONFLICT (tenant_id, key) DO UPDATE
           SET source_type = EXCLUDED.source_type, result = EXCLUDED.result,
               result_id = EXCLUDED.result_id, result_status = 'SUCCESS',
               created_at = NOW(), expires_at = EXCLUDED.expires_at
         WHERE idempotency_keys.expires_at <= NOW()
        """,
        kunci_penuh, tenant_id, source_type,
        json.dumps({"payload_hash": sidik, "response": respons}, default=str),
        result_id, ttl_hours,
    )
