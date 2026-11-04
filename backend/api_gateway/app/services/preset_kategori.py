"""Preset flag per KATEGORI USAHA (4 Nov 2026, desain disetujui MASTER; pola template industri saat onboarding Odoo/QBO).

SATU tempat daftar kunci per kategori. Menerapkan preset = MENAMBAH baris tenant_features (V304) saja: tak pernah mematikan
atau menghapus flag lain, idempoten. Kategori yang diterapkan dicatat sebagai flag `kategori_<kode>` (pembaca sama, nol
migrasi). Flag modul_<x>_mati = bentuk NEGATIF (baris aktif = modul disembunyikan di FE, utils/modulMati.ts).
Yang khas permintaan satu tenant (mis. Pajak/Batch/Gaji/Conversational mati) BUKAN isi preset: tetap flag per tenant.
"""

PRESET = {
    # distributor: tak memproduksi; izin per pengguna & varian/matriks disembunyikan (permintaan pemilik 4 Nov);
    # rekonsiliasi bank = putaran berikutnya (pemilik 5 Nov)
    "grosir_distribusi": ("modul_izinperan_mati", "modul_varian_mati", "modul_manufaktur_mati", "modul_rekonsiliasi_mati"),
}


def kunci_preset(kategori: str) -> tuple:
    """Flag yang dinyalakan preset (termasuk penanda kategori). Kategori tak dikenal = ValueError."""
    if kategori not in PRESET:
        raise ValueError(f"Kategori usaha tidak dikenal: {kategori}. Pilihan: {', '.join(sorted(PRESET))}.")
    return (f"kategori_{kategori}",) + PRESET[kategori]


async def rencana_preset(conn, tid: str, kategori: str) -> dict:
    """BACA SAJA. -> {tambah: [...] baris baru, nyalakan: [...] baris ada tapi mati, sudah: [...] sudah aktif}."""
    kunci = kunci_preset(kategori)
    ada = {r["feature"]: r["enabled"] for r in await conn.fetch(
        "SELECT feature, enabled FROM tenant_features WHERE tenant_id = $1 AND feature = ANY($2::text[])", tid, list(kunci))}
    return {"tambah": [k for k in kunci if k not in ada],
            "nyalakan": [k for k in kunci if ada.get(k) is False],
            "sudah": [k for k in kunci if ada.get(k) is True]}


async def terapkan_preset(conn, tid: str, kategori: str) -> dict:
    """Tulis rencana (pemanggil memegang transaksi). Hanya kunci preset yang disentuh."""
    r = await rencana_preset(conn, tid, kategori)
    ubah = r["tambah"] + r["nyalakan"]
    if ubah:
        await conn.execute(
            """INSERT INTO tenant_features (tenant_id, feature, enabled) SELECT $1, f, true FROM unnest($2::text[]) AS f
               ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now()""", tid, ubah)
    return r
