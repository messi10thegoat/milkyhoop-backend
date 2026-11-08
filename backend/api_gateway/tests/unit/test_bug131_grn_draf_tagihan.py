"""BUG-131 / ROW (r) 8 Nov 2026: GRN dengan faktur pembelian DRAF. Tautan GRN<->tagihan baru ditulis saat tagihan DIPOSTING, jadi dulu GRN
tetap aksi "belum_ditagih" (FE "Buat faktur") selama tagihannya draf. Kini cermin PO (DRAF-TAGIHAN 4 Nov): billing_state/status_text tetap
dari POSTED, partisi aksi belum_ditagih | draf_tagihan dari sisa SESUDAH draf (qty_draf = cakupan draf baris PO, FIFO urutan posting)."""
import inspect
from decimal import Decimal as D

import pytest

from app.routers import goods_receipts as GR
from app.services import grn_daftar as GD


def _m(dasar, taut, draf):
    return GD.medan({"status": "posted", "qty_dasar": D(dasar), "qty_tertaut": D(taut), "qty_draf": D(draf), "grni_terbuka": D("1000")})


@pytest.mark.parametrize("dasar,taut,draf,aksi,state,sisa", [
    ("10", "0", "10", "draf_tagihan", "belum_ditagih", "0"),         # GRN-2611-0011: draf penuh -> Buka faktur draf, teks tetap
    ("10", "0", "4", "belum_ditagih", "belum_ditagih", "6"),         # draf sebagian -> masih bisa dibuatkan faktur
    ("10", "6", "4", "draf_tagihan", "sebagian_ditagih", "0"),       # posted sebagian + draf sisanya
    ("10", "6", "0", "belum_ditagih", "sebagian_ditagih", "4"),
    ("10", "10", "0", None, "sudah_ditagih", "0"),
])
def test_partisi_aksi_dari_sisa_sesudah_draf_status_tetap_posted(dasar, taut, draf, aksi, state, sisa):
    m = _m(dasar, taut, draf)
    assert (m["aksi"], m["billing_state"], m["belum_ditagih_sesudah_draf"]) == (aksi, state, sisa)
    assert m["status_text"] == GD.TEKS_TAGIH[state] and m["aksi_semua"] == ([aksi] if aksi else [])


def test_dua_kunci_aksi_satu_kosakata_dengan_po():
    from app.services import po_daftar as PD
    assert GD.AKSI == ("belum_ditagih", "draf_tagihan") and "draf_tagihan" in PD.AKSI


def test_predikat_sql_partisi_eksklusif_dan_memakai_qty_draf():
    b, d = GD.sql_aksi("belum_ditagih"), GD.sql_aksi("draf_tagihan")
    for p in (b, d):
        assert GD.sql_qty_draf() in p and GD.sql_qty_tertaut() in p and "status = 'posted'" in p
    assert "> 0" in b and "<= 0" in d   # sisa sesudah draf > 0 vs <= 0: tak ada GRN di dua kunci


def test_sql_qty_draf_fifo_urutan_posting_hanya_draf_ber_stok():
    s = GD.sql_qty_draf()
    assert "COALESCE(b.status_v2, b.status) = 'draft'" in s and "i.ber_stok" in s
    assert "(gj.receipt_date, gj.created_at, j.line_number) < (g.receipt_date, g.created_at, i.line_number)" in s  # == grn_tagih FIFO
    assert "gj.status = 'posted'" in s and "l.status = 'active'" in s


def test_summary_dan_daftar_satu_predikat_per_kunci():
    src = inspect.getsource(GR.goods_receipts_summary)
    assert "for k in GD.AKSI" in src and "GD.sql_aksi(k)" in src
    assert "GD.sql_aksi(ak)" in inspect.getsource(GR.list_goods_receipts)
    pola = next(p for p in inspect.signature(GR.list_goods_receipts).parameters.values() if p.name == "aksi").default
    assert "draf_tagihan" in str(pola.metadata) or "draf_tagihan" in str(pola)


def test_detail_dan_daftar_memuat_draft_bills():
    assert "GD.draf_tagihan_banyak(" in inspect.getsource(GR.get_goods_receipt)
    assert "GD.draf_tagihan_banyak(" in inspect.getsource(GR.list_goods_receipts)
    assert "COALESCE(b.status_v2, b.status) = 'draft'" in GD.SQL_DRAF_TAGIHAN and "b.tenant_id = $1" in GD.SQL_DRAF_TAGIHAN


def test_summary_sql_memuat_filter_tiap_kunci_dan_kunci_keluaran(monkeypatch):
    """Perilaku (bukan sumber): SQL /summary memuat FILTER dengan predikat SAMA sql_aksi(k) untuk SETIAP kunci GD.AKSI."""
    import asyncio
    from starlette.requests import Request
    tangkap = []

    class K:
        async def fetchrow(self, sql, *a):
            tangkap.append(sql)
            return {"total": 3, "posted": 3, "void": 0, "today_count": 0, "n_belum_ditagih": 2, "v_belum_ditagih": D("10"),
                    "n_draf_tagihan": 1, "v_draf_tagihan": D("5")}

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(s):
                    return K()

                async def __aexit__(s, *a):
                    return False
            return Cm()

    async def pool():
        return P()
    monkeypatch.setattr(GR, "get_pool", pool)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "u1"}
    d = asyncio.run(GR.goods_receipts_summary(r, purchase_order_id=None, search=None))["data"]
    for k in GD.AKSI:
        assert f"FILTER (WHERE {GD.sql_aksi(k)}) AS n_{k}" in tangkap[0], k
    assert (d["belum_ditagih_count"], d["draf_tagihan_count"], d["draf_tagihan_value"]) == (2, 1, "5.00")
