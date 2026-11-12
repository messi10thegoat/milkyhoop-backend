"""BF-1e (G1): jenis_tersedia + aksi_tersedia di daftar mutasi bank. FE TIDAK menyusun daftar sendiri. Penentu = fungsi MURNI pada kolom baris (status, cara, rekonsiliasi_id) + izin pengguna
(dihitung SEKALI per halaman); BUKAN penentu aksi dijalankan per baris (mahal). KESETARAAN dengan penentu tiap aksi (rencana_*) dikunci tes (test_bankfeed_tersedia): KODE_KEADAAN = kode blok
keadaan baris yang dihitung ulang di sini. Jenis = SATU sumber dengan penulis catat (bankfeed_aksi.JENIS_CATAT/JENIS_BELUM/LABEL_JENIS/IZIN_BUAT). Tenant eksplisit; tanpa jurnal."""
from typing import Optional

from ..middleware.permission_middleware import izin_rute_pengguna

URUTAN_JENIS = {"masuk": ("terima_pembayaran", "uang_muka_pelanggan", "uang_masuk", "transfer", "pribadi"),
                "keluar": ("bayar_tagihan", "uang_keluar", "transfer", "pribadi")}
DITINJAU = ("untuk_ditinjau", "mungkin_duplikat")
KODE_KEADAAN = ("SUDAH_DIPUTUS", "BELUM_DIPUTUS", "PAKAI_BATALKAN", "PAKAI_LEPASKAN", "SUDAH_DIREKONSILIASI")
ALASAN_TAK_IZIN = "Anda tak punya izin mengubah mutasi bank. Minta pemilik usaha membukanya."
ALASAN_TAK_IZIN_VOID = "Anda tak punya izin membatalkan dokumen ini. Minta pemilik usaha membukanya."
ALASAN_TAK_ADA_JENIS = "Tak ada jenis catatan yang boleh Anda pakai."
ALASAN_REKON_LEPAS = "Sudah direkonsiliasi, tidak bisa dilepas."
ALASAN_REKON_BATAL = "Sudah direkonsiliasi, tidak bisa dibatalkan."
ALASAN_PAKAI_BATALKAN = "Dicatat sebagai transaksi: pakai Batalkan catatan."


def _ba():
    from . import bankfeed_aksi as BA      # impor malas: bankfeed_aksi mengimpor bankfeed_daftar yang mengimpor modul ini
    return BA


def izin_semua() -> dict:
    return {"ubah": True, "void": True, "jenis": {j: True for j in _ba().IZIN_BUAT}}


async def izin_pengguna(user: Optional[dict], rekening_id) -> dict:
    """{ubah: kas_bank C (cocokkan/catat/kecualikan/lepaskan), void: kas_bank V (batalkan_catatan), jenis: {jenis: izin C modul tujuan}} -- SATU panggilan peran per halaman.
    Tanpa pengguna (unit orkestrasi) = semua boleh."""
    if not user:
        return izin_semua()
    BA = _ba()
    jalur_ubah = f"/api/bank-accounts/{rekening_id}/mutasi-bank/x/catat"
    jalur_void = f"/api/bank-accounts/{rekening_id}/mutasi-bank/x/batalkan-catatan"
    jalur = [jalur_ubah, jalur_void, *BA.IZIN_BUAT.values()]
    ok = await izin_rute_pengguna(user, jalur, "POST")
    return {"ubah": bool(ok.get(jalur_ubah)), "void": bool(ok.get(jalur_void)), "jenis": {j: bool(ok.get(p)) for j, p in BA.IZIN_BUAT.items()}}


def jenis_tersedia(arah: str, izin: dict) -> list:
    """[{kunci, label}] untuk baris ber-arah `arah`: urutan tetap, hanya jenis yang dikenal penulis catat, disaring izin C modul tujuan; tanpa izin ubah = kosong."""
    BA = _ba()
    if not izin["ubah"]:
        return []
    return [{"kunci": j, "label": BA.LABEL_JENIS.get(j, j)} for j in URUTAN_JENIS.get(arah, ())
            if j in BA.JENIS_CATAT and j not in BA.JENIS_BELUM and izin["jenis"].get(j, True)]


def _ya() -> dict:
    return {"ok": True, "alasan": None}


def _tidak(alasan: str) -> dict:
    return {"ok": False, "alasan": alasan}


def aksi_tersedia(row, jenis: list, izin: dict) -> dict:
    """{aksi: {ok, alasan}} menurut status/cara/rekonsiliasi baris; aksi yang tak berlaku untuk status itu tidak muncul."""
    status, cara, rekon = row["status"], row["cara"], row["rekonsiliasi_id"]
    ubah = izin["ubah"]
    if status in DITINJAU:
        tutup = None if ubah else ALASAN_TAK_IZIN
        return {"cocokkan": _tidak(tutup) if tutup else _ya(),
                "catat": _ya() if jenis else _tidak(tutup or ALASAN_TAK_ADA_JENIS),
                "kecualikan": _tidak(tutup) if tutup else _ya()}
    if status == "dicatat" and cara in ("catat", "pribadi"):
        batal = _ya() if izin["void"] and not rekon else _tidak(ALASAN_REKON_BATAL if rekon else ALASAN_TAK_IZIN_VOID)
        return {"batalkan_catatan": batal, "lepaskan": _tidak(ALASAN_PAKAI_BATALKAN)}
    if status in ("dicatat", "dikecualikan"):
        return {"lepaskan": _tidak(ALASAN_REKON_LEPAS) if rekon else (_ya() if ubah else _tidak(ALASAN_TAK_IZIN))}
    return {}
