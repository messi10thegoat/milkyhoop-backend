"""Sumber terjemahan V429: pesan RAISE EXCEPTION fungsi/trigger DB yang bisa sampai ke pengguna Penjualan -> Indonesia.
Dipakai (1) pembangkit migrasi dari definisi berjalan, (2) uji: badan lama vs baru berbeda HANYA pada literal ini.
Aturan: jumlah & urutan placeholder % SAMA; prefiks 'Law N:' dipertahankan; ERRCODE/USING tak disentuh.
Keluarga periode (prevent_closed_period_journal) TIDAK di sini (FE friendlyApiError mencocokkan teks Inggrisnya).
salary component TIDAK di sini (routers/salary_components.py mencocokkan teksnya; bukan Penjualan)."""
import re
import sys

PETA = [
    ("No active e-Faktur sequence found. Please allocate NSFP from DJP.",
     "Belum ada rentang nomor e-Faktur yang aktif. Minta alokasi NSFP dari DJP dulu."),
    ("e-Faktur sequence exhausted. Please allocate new NSFP from DJP.",
     "Rentang nomor e-Faktur sudah habis. Minta alokasi NSFP baru dari DJP."),
    ("Law 29/30: Non-UUID source_id cannot touch PAYABLE. source_type=%, source_id=%",
     "Law 29/30: source_id bukan UUID tidak boleh menyentuh akun Utang. source_type=%, source_id=%"),
    ("Law 29/30: Cannot credit PAYABLE without bill. source_type=%, source_id=%",
     "Law 29/30: akun Utang tidak boleh dikredit tanpa tagihan pembelian. source_type=%, source_id=%"),
    ("Law 29/30: Non-UUID source_id cannot touch RECEIVABLE. source_type=%, source_id=%",
     "Law 29/30: source_id bukan UUID tidak boleh menyentuh akun Piutang. source_type=%, source_id=%"),
    ("Law 29/30: Cannot debit RECEIVABLE without invoice. source_type=%, source_id=%",
     "Law 29/30: akun Piutang tidak boleh didebit tanpa faktur. source_type=%, source_id=%"),
    ("Law 4 line/header imbalance on journal %: header debit=% credit=% but lines sum to debit=% credit=% "
     "(posting rejected to prevent silent ledger corruption)",
     "Law 4: jurnal % tidak seimbang antara header dan baris: header debit=% kredit=% tetapi jumlah baris debit=% "
     "kredit=% (posting ditolak agar buku besar tidak rusak diam-diam)"),
    ("Cannot post opening balance: operational transactions already exist for tenant %. "
     "Reverse existing opening balance first, then re-post.",
     "Saldo awal tidak bisa diposting: usaha % sudah punya transaksi operasional. "
     "Balik saldo awal yang ada dulu, lalu posting ulang."),
    ("Cannot delete % journal entry (%). Financial history must be preserved.",
     "Jurnal berstatus % (%) tidak boleh dihapus; riwayat keuangan wajib utuh."),
    ("Cannot modify lines of % journal entry (%). Use reversal instead.",
     "Baris jurnal berstatus % (%) tidak boleh diubah. Batalkan lewat jurnal pembalik."),
    ("Cannot modify POSTED journal entry (%). Use reversal instead.",
     "Jurnal POSTED (%) tidak boleh diubah. Batalkan lewat jurnal pembalik."),
    ("Cannot modify completed sales receipt. Use void instead.",
     "Kwitansi penjualan yang sudah selesai tidak bisa diubah. Batalkan (void) lalu buat yang baru."),
    ("Cannot modify voided sales receipt",
     "Kwitansi penjualan yang sudah dibatalkan tidak bisa diubah."),
    ("Invalid status transition from % to %",
     "Perubahan status tagihan pembelian dari % ke % tidak diizinkan."),
    ("Invalid expense status transition from % to %",
     "Perubahan status biaya dari % ke % tidak diizinkan."),
]


def periksa_peta():
    for lama, baru in PETA:
        assert lama.count("%") == baru.count("%"), (lama, baru)
        assert "'" not in baru, baru
        m = re.match(r"Law [\d/]+", lama)
        assert m is None or baru.startswith(m.group()), (lama, baru)   # nomor Law dipertahankan


def terjemahkan(sql: str) -> str:
    periksa_peta()
    for lama, baru in PETA:
        n = sql.count(f"'{lama}'")
        assert n == 1, (n, lama)
        sql = sql.replace(f"'{lama}'", f"'{baru}'")
    return sql


def normal_tanpa_pesan(sql: str, pihak: int) -> str:
    """Badan fungsi dengan SETIAP literal di PETA (sisi lama=0/baru=1) diganti penanda -> harus identik."""
    for i, pasangan in enumerate(PETA):
        sql = sql.replace(f"'{pasangan[pihak]}'", f"'<<PESAN {i}>>'")
    return sql


if __name__ == "__main__":
    lama = open(sys.argv[1]).read()
    baru = terjemahkan(lama)
    open(sys.argv[2], "w").write(baru)
    assert normal_tanpa_pesan(lama, 0) == normal_tanpa_pesan(baru, 1)
    print("ok", len(PETA))
