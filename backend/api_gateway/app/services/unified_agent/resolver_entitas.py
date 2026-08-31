"""FASE B — SATU situs pencarian master untuk PIPELINE ENTITAS V2.

Kaidah 2 tiket ini: semua pencarian master di jalur V2 lewat SATU fungsi,
`resolve_entitas`. Nol pencarian master kedua di enricher. Alasannya
terukur di jalur lama: `_enrich_quote` mencari pelanggan lewat
`/api/customers?search=`, `_enrich_items` mencari barang lewat
`/api/items/{id}`, dan `entity_resolver._resolve_item` mencari barang lagi
lewat SQL dengan aturan yang berbeda. Tiga situs, tiga definisi "ketemu",
dan tak satu pun tahu apa yang dua lainnya putuskan.

KEHIDUPAN BARIS MASTER — DIUKUR di milkydb 2026-08-31, bukan disalin dari
kode lama:

  products           : `deleted_at IS NULL` yang menentukan. Kolom
                       `is_active` TIDAK ADA. `status` ADA tapi BUKAN
                       penanda hidup: pada tenant kaos-biru-konveksi ada
                       55 baris, `status='active'` 42, `deleted_at IS NULL`
                       hanya 3. Jalur lama (`entity_resolver._resolve_item`)
                       menyaring dengan `status = 'active'` saja, jadi ia
                       melihat 39 produk yang sudah dihapus. V2 TIDAK
                       menirunya.
  customers          : `deleted_at IS NULL`. Nama di kolom `nama`
                       (Bahasa Indonesia). Kolom `name` ADA tetapi KOSONG
                       pada seluruh 16 baris — membacanya = nol yang
                       meyakinkan.
  vendors            : TIDAK punya `deleted_at`; kehidupan = `is_active`.
  chart_of_accounts  : TIDAK punya `deleted_at`; `is_active` + bukan header.
  bank_accounts      : TIDAK punya `deleted_at`; `is_active`.

Jadi "semua master disaring deleted_at IS NULL" TIDAK benar untuk tiga dari
lima tipe — kolomnya memang tak ada di sana. Peta di bawah menyatakan
syarat hidup PER TIPE supaya perbedaan ini terlihat, bukan terkubur di satu
klausa yang menyesatkan.

Fungsi ini TIDAK PERNAH melempar exception untuk "tidak ketemu": tidak
ketemu adalah JAWABAN (`TIDAK_ADA`), bukan kegagalan. Exception hanya lolos
kalau DB-nya sendiri bermasalah, dan itu memang bukan urusan berkas ini.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from .hasil_resolve import HasilResolve, Kandidat, TIPE_SAH

logger = logging.getLogger(__name__)

# Batas kandidat. 8 = cukup untuk menyatakan "banyak" tanpa mengubah pil
# menjadi katalog. Angka ini muncul di pesan user lewat len(kandidat), jadi
# ia bagian dari kontrak, bukan detail bebas.
BATAS_KANDIDAT = 8


class _Master:
    """Deklarasi satu tipe master: tabel, kolom nama, kolom cari, syarat hidup."""

    __slots__ = ("tabel", "kolom_nama", "kolom_cari", "hidup")

    def __init__(
        self,
        tabel: str,
        kolom_nama: str,
        kolom_cari: Tuple[str, ...],
        hidup: str,
    ) -> None:
        self.tabel = tabel
        self.kolom_nama = kolom_nama
        self.kolom_cari = kolom_cari
        self.hidup = hidup


PETA_MASTER: Dict[str, _Master] = {
    # deleted_at, BUKAN status — lihat docstring modul.
    "item": _Master(
        "products", "nama_produk", ("nama_produk", "item_code", "sku"),
        "deleted_at IS NULL",
    ),
    # `nama`, bukan `name` (kolom `name` ada tapi kosong di seluruh baris).
    "customer": _Master(
        "customers", "nama", ("nama",), "deleted_at IS NULL",
    ),
    # vendors memakai penamaan Inggris dan TIDAK punya deleted_at.
    "vendor": _Master(
        "vendors", "name", ("name",), "is_active = true",
    ),
    "akun": _Master(
        "chart_of_accounts", "name", ("name", "account_code"),
        "is_active = true AND is_header = false",
    ),
    "bank": _Master(
        "bank_accounts", "account_name", ("account_name",), "is_active = true",
    ),
}


_SPASI = re.compile(r"\s+")


def normalisasi_cari(mentah: str) -> str:
    """Bentuk untuk MENCARI. `mentah` sendiri tidak pernah diubah.

    strip + rapatkan spasi ganda + casefold. Dipisah sebagai fungsi supaya
    sisi SQL dan sisi Python memakai definisi yang sama persis; kalau kedua
    sisi menormalkan sendiri-sendiri, "exact match" akan meleset pada nama
    yang mengandung spasi ganda dan tak seorang pun bisa melihat sebabnya.
    """
    return _SPASI.sub(" ", str(mentah or "").strip()).casefold()


def _sql_norm(kolom: str) -> str:
    """Padanan `normalisasi_cari` di sisi SQL, untuk kolom mana pun."""
    return "lower(btrim(regexp_replace(" + kolom + r", '\s+', ' ', 'g')))"


async def resolve_entitas(
    conn: Any,
    tenant_id: str,
    mentah: str,
    tipe: str,
    baris_index: int | None = None,
) -> HasilResolve:
    """Cari SATU entitas di master. Selalu mengembalikan HasilResolve.

    Urutan yang dijanjikan tiket, dan alasan tiap langkah:

    1. Normalisasi HANYA untuk mencari. `mentah` disimpan apa adanya di
       hasil — kaidah 3.
    2. Exact match (casefold) menang MUTLAK. Kalau nama master persis sama
       dengan ketikan user, jumlah kecocokan ILIKE tidak relevan: sebuah
       nama yang benar-benar ada di master tidak boleh berubah menjadi pil
       hanya karena ada nama-nama lain yang memuatnya sebagai substring.
    3. Baru ILIKE dengan pola melingkupi, maksimum BATAS_KANDIDAT.
       0 = TIDAK_ADA, 1 = DITEMUKAN, >= 2 = AMBIGU.
    4. Tidak ketemu BUKAN exception.
    """
    if tipe not in TIPE_SAH:
        raise ValueError("tipe tak dikenal: " + repr(tipe))
    master = PETA_MASTER[tipe]

    asli = str(mentah or "")
    kunci = normalisasi_cari(asli)
    if not kunci:
        # Nama sudah hilang di hulu. HasilResolve sengaja menolak `mentah`
        # kosong, jadi kita tidak boleh membuatnya di sini — dan kita juga
        # tidak boleh menelannya diam-diam. Melempar di sini menempatkan
        # kesalahan di tempat lahirnya, bukan di layar user.
        raise ValueError(
            "resolve_entitas menerima nama kosong — nama mentah sudah hilang "
            "sebelum pencarian"
        )

    hidup = "tenant_id = $1 AND " + master.hidup

    # 2. EXACT
    sql_exact = (
        "SELECT id, " + master.kolom_nama + " AS nm FROM " + master.tabel +
        " WHERE " + hidup + " AND " + _sql_norm(master.kolom_nama) + " = $2" +
        " ORDER BY " + master.kolom_nama + " LIMIT 2"
    )
    baris = await conn.fetch(sql_exact, tenant_id, kunci)
    if baris:
        # Dua baris dengan nama IDENTIK di master adalah data ganda, bukan
        # ambiguitas yang bisa dijawab user: dua pilihan yang tulisannya sama
        # persis tak bisa dibedakan di layar. Ambil yang pertama dan catat.
        if len(baris) > 1:
            logger.warning(
                "[PIPA_V2] master ganda tipe=%s n=%d — nama identik, ambil pertama",
                tipe,
                len(baris),
            )
        return HasilResolve(
            status="DITEMUKAN",
            mentah=asli,
            tipe=tipe,
            baris_index=baris_index,
            id=str(baris[0]["id"]),
            nama=str(baris[0]["nm"]),
        )

    # 3. ILIKE
    cocok = " OR ".join(k + " ILIKE $2" for k in master.kolom_cari)
    sql_like = (
        "SELECT id, " + master.kolom_nama + " AS nm FROM " + master.tabel +
        " WHERE " + hidup + " AND (" + cocok + ")" +
        " ORDER BY " + master.kolom_nama + " LIMIT " + str(BATAS_KANDIDAT)
    )
    baris = await conn.fetch(sql_like, tenant_id, "%" + kunci + "%")
    if not baris:
        return HasilResolve(
            status="TIDAK_ADA", mentah=asli, tipe=tipe, baris_index=baris_index
        )
    if len(baris) == 1:
        return HasilResolve(
            status="DITEMUKAN",
            mentah=asli,
            tipe=tipe,
            baris_index=baris_index,
            id=str(baris[0]["id"]),
            nama=str(baris[0]["nm"]),
        )
    return HasilResolve(
        status="AMBIGU",
        mentah=asli,
        tipe=tipe,
        baris_index=baris_index,
        kandidat=tuple(Kandidat(str(r["id"]), str(r["nm"])) for r in baris),
    )


def log_ringkas(tipe: str, hasil: List[HasilResolve]) -> None:
    """Satu baris log TANPA nama entitas.

    Nama entitas sengaja TIDAK dicetak: T181_PUING dicabut persis karena
    mencetak isi payload ke log. Yang dibutuhkan untuk mendiagnosis adalah
    BENTUK hasilnya, bukan isinya.
    """
    logger.warning(
        "[PIPA_V2] tahap=resolve tipe=%s n=%d ditemukan=%d ambigu=%d tidak_ada=%d",
        tipe,
        len(hasil),
        sum(1 for h in hasil if h.status == "DITEMUKAN"),
        sum(1 for h in hasil if h.status == "AMBIGU"),
        sum(1 for h in hasil if h.status == "TIDAK_ADA"),
    )
