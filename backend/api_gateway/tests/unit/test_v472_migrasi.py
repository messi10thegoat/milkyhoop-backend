"""V472 GRN-SJ-VENDOR (2 Nov 2026): migrasi-duluan -- uji statis berkas migrasi (tanpa DB).
Aditif: kolom NULL + CHECK 1..100 + indeks parsial biasa; tanpa CONCURRENTLY (apply_mig menolak), tanpa BEGIN/COMMIT,
tanpa UNIQUE, tanpa ubah/hapus data. Rollback membalik (indeks -> constraint -> kolom)."""
import pathlib
import re

MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


def _sql(n):
    return re.sub(r"--[^\n]*", "", (MIG / n).read_text())


def test_v472_aditif():
    s = _sql("V472__grn_sj_vendor.sql")
    assert "ADD COLUMN IF NOT EXISTS vendor_delivery_note TEXT NULL" in s
    assert "CHECK (vendor_delivery_note IS NULL OR char_length(vendor_delivery_note) BETWEEN 1 AND 100)" in s
    assert re.search(r"CREATE INDEX IF NOT EXISTS idx_goods_receipts_sj_vendor ON goods_receipts \(tenant_id, vendor_id, vendor_delivery_note\)"
                     r"\s+WHERE vendor_delivery_note IS NOT NULL AND status <> 'void';", s)
    for larangan in ("CONCURRENTLY", "BEGIN", "COMMIT", "UNIQUE", "UPDATE ", "DELETE ", "DROP COLUMN"):
        assert larangan not in s.upper(), larangan


# Kata yang DITOLAK /root/apply_mig.sh (memindai teks MENTAH, termasuk komentar): kendali transaksi + CONCURRENTLY.
# 2 Nov 2026: komentar V472 menyebut "CONCURRENTLY" -> apply_mig RC=10, nol perubahan. Tes lama membuang komentar dulu -> lolos.
DITOLAK_ALAT = re.compile(r"CONCURRENTLY|\bBEGIN\s*;|\bCOMMIT\s*;|\bROLLBACK\s*;|START\s+TRANSACTION", re.I)


def _ditolak_alat(teks):
    return DITOLAK_ALAT.findall(teks)


def test_v472_mentah_lolos_pemindai_apply_mig():
    for n in ("V472__grn_sj_vendor.sql", "V472__grn_sj_vendor_ROLLBACK.sql"):
        assert _ditolak_alat((MIG / n).read_text()) == [], n


def test_pemindai_alat_bisa_merah():
    assert _ditolak_alat("-- Indeks biasa (apply_mig menolak CONCURRENTLY; tabel kecil).\nCREATE INDEX x ON t (a);")
    assert _ditolak_alat("BEGIN;\nALTER TABLE t ADD COLUMN c int;\nCOMMIT;")
    assert not _ditolak_alat("-- tanpa kata terlarang\nCREATE INDEX x ON t (a);")


def test_v472_rollback_membalik():
    r = _sql("V472__grn_sj_vendor_ROLLBACK.sql")
    i, c, k = (r.index("DROP INDEX IF EXISTS idx_goods_receipts_sj_vendor"),
               r.index("DROP CONSTRAINT IF EXISTS goods_receipts_sj_vendor_cek"), r.index("DROP COLUMN IF EXISTS vendor_delivery_note"))
    assert i < c < k
