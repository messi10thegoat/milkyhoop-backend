"""BUG-145 (8 Nov 2026; pemilik konfirmasi LANGSUNG di sesi BACKEND "Ya, dari faktur"): SO SELESAI (completed: Tutup pesanan manual /
selesai otomatis) -- "Sisa" dihitung dari nilai yang DIFAKTUR (ringkasan_pesanan.invoiced − tertutup); bagian yang ditutup TANPA faktur bukan
sisa (pola PO tutup / NetSuite closed line). SO belum selesai TIDAK berubah (total − tertutup). SATU pembentuk (PT.sisa_pesanan) untuk 6 titik:
so_posisi (pill aksi, fakta_daftar posisi + kolom Sisa, sisa_per_so = sort + Σ sisa), payment_summary.remaining_amount, Rekap, bundel dokumen.
Nyata nyawit SO-2611-0019: total 1.176.000, difaktur 888.000 lunas, 1 baris 288.000 ditutup -> Sisa 288.000 "Sebagian ditagih" -> 0 "Lunas"."""
import asyncio
import inspect
from decimal import Decimal as D

import pytest

from app.routers import dokumen as RD
from app.routers import sales_orders as RS
from app.services import proforma_terbayar as PT
from app.services import rekap_pesanan as RK
from app.services import so_posisi as SP


def _r(invoiced, settled, dp_unapplied=D("0"), outstanding=None, cn=D("0")):
    r = {k: D("0") for k in PT._KOSONG}
    r.update(invoiced=invoiced, invoice_settled=settled, dp_unapplied=dp_unapplied, credit_note=cn,
             invoice_outstanding=invoiced - settled if outstanding is None else outstanding)
    r["tertutup"] = r["invoice_settled"] + r["dp_unapplied"]
    return r


R19 = _r(D("888000"), D("888000"))  # SO-2611-0019


@pytest.mark.parametrize("status, harap", [("completed", D("0")), ("confirmed", D("288000")), ("partial", D("288000")),
                                           ("draft", D("288000")), ("cancelled", D("288000"))])
def test_sisa_pesanan_selesai_dari_faktur_lainnya_tetap(status, harap):
    assert PT.sisa_pesanan(status, D("1176000"), R19) == harap


def test_selesai_belum_lunas_tetap_bersisa_piutang():
    # selesai otomatis (non-stok tertagih penuh) belum dibayar: sisa = faktur − dibayar
    assert PT.sisa_pesanan("completed", D("500000"), _r(D("500000"), D("200000"))) == D("300000")
    # uang muka belum diterapkan menutup juga (tertutup), tak negatif dibatasi pemanggil
    assert PT.sisa_pesanan("completed", D("500000"), _r(D("0"), D("0"), dp_unapplied=D("100000"))) == D("-100000")


def test_posisi_selesai_lunas_bukan_sebagian_ditagih():
    s = PT.sisa_pesanan("completed", D("1176000"), R19)
    assert SP.posisi("completed", s, [], True, D("0"), False, D("0")) == ("Lunas", True)


def test_payment_summary_remaining_ikut_status():
    assert PT.ringkasan_pembayaran_so(D("1176000"), R19, "completed")["remaining_amount"] == 0.0
    assert PT.ringkasan_pembayaran_so(D("1176000"), R19, "confirmed")["remaining_amount"] == 288000.0
    assert PT.ringkasan_pembayaran_so(D("1176000"), R19)["remaining_amount"] == 288000.0  # tanpa status = perilaku lama


def test_sisa_per_so_daftar_sort_jumlah_satu_sumber(monkeypatch):
    async def ring(conn, tid, ids):
        return {i: dict(R19) for i in ids}
    monkeypatch.setattr(SP, "ringkasan_pesanan", ring)
    rows = [{"id": "a", "total_amount": D("1176000"), "status": "completed"}, {"id": "b", "total_amount": D("1176000"), "status": "confirmed"},
            {"id": "c", "total_amount": D("1176000"), "status": "cancelled"}]
    assert asyncio.run(SP.sisa_per_so(None, "t", rows)) == {"a": D("0"), "b": D("288000"), "c": None}


def test_enam_titik_memakai_pembentuk_tanpa_rumus_lama():
    for fn, n in ((SP.fakta_daftar, 1), (SP.sisa_per_so, 1), (RK, 1), (RD, 1)):
        src = inspect.getsource(fn)
        assert src.count("sisa_pesanan(") >= n, fn
        assert '- o["tertutup"]' not in src and '- rg[' not in src and 'total - r["tertutup"]' not in src, fn
    src_sp = inspect.getsource(SP)
    assert src_sp.count("sisa_pesanan(") == 3 and '"tertutup"]' not in src_sp
    assert 'ringkasan_pembayaran_so(order["total_amount"], ringkas[order["id"]], order["status"])' in inspect.getsource(RS)
    assert "remaining_amount\": float(max(NOL, sisa_pesanan(status, total, r)))" in inspect.getsource(PT.ringkasan_pembayaran_so)
