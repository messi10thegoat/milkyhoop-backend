"""BUG-187: PATCH biaya draf tercatat di riwayat (pola BUG-058): event EXPENSE_UPDATED (riwayat=True) di audit_logs, ditulis di transaksi PATCH (bukan pratinjau), dibaca riwayat_biaya via ENTITAS_AUDIT."""
import inspect
from decimal import Decimal as D

import app.routers.expenses as EX
from app.services import audit_dokumen as AD
from app.services import biaya_riwayat as BR
from app.services import so_riwayat as SR


def test_event_biaya_diubah_terdaftar_dan_masuk_riwayat():
    ev = AD.EVENT[("expense", "updated")]
    assert (ev.nama, ev.entitas, ev.riwayat) == ("EXPENSE_UPDATED", "expenses", True) and AD.nama("expense", "updated") == "EXPENSE_UPDATED"
    assert "EXPENSE_UPDATED" not in AD.TANPA_RIWAYAT                       # tak dibuang dari riwayat
    assert SR.ENTITAS_AUDIT["expenses"] == "expense" and SR.ENTITAS_AUDIT["expense"] == "expense"
    assert SR.RINGKAS_AUDIT["EXPENSE_UPDATED"].startswith("Biaya")


def test_ringkas_ubah():
    assert BR.ringkas_ubah("EXP-1", ["amount", "expense_date", "tax_rate", "tax_id"], D("100000"), D("120000")) == "Biaya EXP-1 diubah: jumlah, tanggal, pajak · total Rp 100.000 → Rp 120.000"
    assert BR.ringkas_ubah("EXP-1", ["notes"], D("100000"), D("100000")) == "Biaya EXP-1 diubah: catatan"            # total tak berubah: tak ditampilkan
    assert BR.ringkas_ubah("EXP-1", ["field_baru"], None, None) == "Biaya EXP-1 diubah: field_baru"                   # nama tak dikenal tampil apa adanya
    assert BR.ringkas_ubah("EXP-1", [], D(1), D(1)) == "Biaya EXP-1 diubah"


def test_patch_mencatat_di_transaksi_sesudah_inti_sebelum_simpan_idempotensi():
    s = inspect.getsource(EX.update_expense)
    i_inti, i_catat, i_simpan = s.index("ubah_biaya_inti(conn, ctx, expense_id, body)"), s.index("catat_riwayat("), s.index("idem_buat.simpan(")
    assert i_inti < i_catat < i_simpan
    assert '_ad_biaya.nama("expense", "updated")' in s and '"medan": _medan' in s and '"total_sebelum"' in s and '"total_sesudah"' in s
    assert s.index("conn.transaction()") < i_catat                      # di dalam transaksi PATCH (gagal catat = ubah batal)


def test_pratinjau_ubah_tidak_mencatat():
    for nama in ("preview_update_expense", "_pratinjau_tulis"):
        assert "catat_riwayat" not in inspect.getsource(getattr(EX, nama)), nama
    assert "catat_riwayat" not in inspect.getsource(EX.ubah_biaya_inti)     # inti dipakai pratinjau juga: pencatatan HANYA di handler PATCH


def test_riwayat_biaya_meneruskan_audit_ke_penyusun():
    s = inspect.getsource(BR.riwayat_biaya)
    assert '_selesaikan(conn, tid, k, {"expense": [e["id"]]}, {"expense": True}, limit)' in s
