"""Penerimaan Barang (GRN, 23 Okt 2026; RANCANGAN-penerimaan-barang-grn §6–§7): penentu & kontrak tagihan (fungsi murni + struktur rute).
Putusan pemilik: P1 ber-stok -> Dr Persediaan / Cr GRNI; P2 non-stok tanpa jurnal; P3 selisih -> Selisih Harga Pembelian; P6 flag."""
import asyncio
import inspect
from decimal import Decimal as D
from uuid import uuid4

import pytest

from app.services import grn_rencana as G
from app.services import grn_tagih as T
from app.services import role_resolver as RR


def test_peran_baru_terdaftar():
    assert RR.AccountRole.GOODS_RECEIVED_NOT_INVOICED == "GOODS_RECEIVED_NOT_INVOICED"
    assert RR.AccountRole.PURCHASE_PRICE_VARIANCE == "PURCHASE_PRICE_VARIANCE"
    katalog = getattr(RR, "_CATALOG", None)
    if katalog is not None:
        assert {"GOODS_RECEIVED_NOT_INVOICED", "PURCHASE_PRICE_VARIANCE"} <= set(katalog)


@pytest.mark.parametrize("tipe,track,harap", [("goods", True, True), ("goods", False, False), ("non_inventory", True, False),
                                              ("service", None, False), (None, True, False)])
def test_ber_stok_hanya_barang_terlacak(tipe, track, harap):
    assert G.ber_stok(tipe, track) is harap


def test_harga_satuan_terima_neto_diskon_tanpa_ppn():
    assert G.harga_satuan_terima({"quantity": D("4"), "subtotal": D("1000"), "discount_amount": D("100")}) == D("225")
    assert G.harga_satuan_terima({"quantity": D("0"), "subtotal": D("1"), "discount_amount": None}) == D("0")
    assert G.r2(D("3") * G.harga_satuan_terima({"quantity": D("3"), "subtotal": D("1000"), "discount_amount": D("0")})) == D("1000.00")


def _t(gid, qty, harga, sisa_nilai=None):
    t = {"gr_item_id": gid, "qty_terbuka": D(qty), "harga_satuan": D(harga)}
    if sisa_nilai is not None:
        t["sisa_nilai"] = D(sisa_nilai)
    return t


def test_fifo_menutup_beberapa_grn_berurutan():
    out = T.alokasi_fifo(D("8"), [_t("g1", "6", "1000"), _t("g2", "4", "1000")])
    assert [(x["gr_item_id"], x["qty"], x["nilai"]) for x in out] == [("g1", D("6"), D("6000.00")), ("g2", D("2"), D("2000.00"))]


def test_fifo_tautan_penghabis_memakai_sisa_nilai_persis():
    # 3 x 333.333.. = 1000.00 (baris GRN); tagihan 1 lalu 2: tautan kedua menghabiskan -> sisa_nilai 666.67, bukan 2 x 333.33 = 666.66
    t1 = T.alokasi_fifo(D("1"), [_t("g", "3", D("1000") / D("3"), "1000.00")])
    assert t1[0]["nilai"] == D("333.33")
    t2 = T.alokasi_fifo(D("2"), [_t("g", "2", D("1000") / D("3"), "666.67")])
    assert t2[0]["nilai"] == D("666.67") and t1[0]["nilai"] + t2[0]["nilai"] == D("1000.00")


def test_rencana_baris_grni_selisih_dan_inbound_sisa():
    bi = {"id": "b1", "quantity": D("10"), "subtotal": D("11000"), "discount_amount": D("0"), "purchase_order_item_id": "p1"}
    r = T.rencana_baris(bi, [_t("g1", "6", "1000", "6000"), _t("g2", "2", "1000", "2000")])
    assert r["qty_grn"] == D("8") and r["nilai_grni"] == D("8000") and r["nilai_tagih_grn"] == D("8800.00")
    assert r["selisih"] == D("800.00") and r["qty_inbound"] == D("2") and r["perlu_inbound"] is True
    r2 = T.rencana_baris({**bi, "quantity": D("5"), "subtotal": D("4500")}, [_t("g1", "6", "1000", "6000")])
    assert r2["selisih"] == D("-500.00") and r2["perlu_inbound"] is False  # harga tagihan < harga terima -> kredit selisih


def test_kontrak_flag_mati_kosong():
    class C:
        async def fetchval(self, q, *a):
            return None  # flag tak ada
        async def fetch(self, q, *a):
            raise AssertionError("flag mati tak boleh membaca GRN")
    assert asyncio.run(T.rencana_posting_baris(C(), "t", [{"id": 1, "purchase_order_item_id": uuid4(), "quantity": D("1")}])) == []


def test_kontrak_dua_baris_tagihan_satu_baris_po_fifo_berlanjut():
    po = uuid4()

    class C:
        async def fetchval(self, q, *a):
            return 1
        async def fetch(self, q, *a):
            assert "status = 'posted'" in q and "i.ber_stok" in q
            return [{"gr_item_id": "g1", "purchase_order_item_id": po, "unit_cost": D("100"), "qty_terbuka": D("5"), "sisa_nilai": D("500")},
                    {"gr_item_id": "g2", "purchase_order_item_id": po, "unit_cost": D("120"), "qty_terbuka": D("5"), "sisa_nilai": D("600")}]
    b1 = {"id": "b1", "purchase_order_item_id": po, "quantity": D("4"), "subtotal": D("400"), "discount_amount": D("0")}
    b2 = {"id": "b2", "purchase_order_item_id": po, "quantity": D("4"), "subtotal": D("480"), "discount_amount": D("0")}
    out = asyncio.run(T.rencana_posting_baris(C(), "t", [b1, b2]))
    assert [(x["gr_item_id"], x["qty"]) for x in out[0]["tautan"]] == [("g1", D("4"))]
    assert [(x["gr_item_id"], x["qty"], x["nilai"]) for x in out[1]["tautan"]] == [("g1", D("1"), D("100")), ("g2", D("3"), D("360.00"))]


def test_tulis_tautan_variance_sama_dengan_selisih_baris():
    tulis = []

    class C:
        async def execute(self, q, *a):
            tulis.append(a)
    butir = T.rencana_baris({"id": "b", "quantity": D("3"), "subtotal": D("1000"), "discount_amount": D("0"), "purchase_order_item_id": "p"},
                            [_t("g1", "1", "300", "300"), _t("g2", "2", "310", "620")])
    asyncio.run(T.tulis_tautan(C(), [butir]))
    assert sum(a[4] for a in tulis) == butir["selisih"] and sum(a[3] for a in tulis) == butir["nilai_grni"]


def test_rute_receive_flag_mati_perilaku_lama_dan_penentu_sama():
    from app.routers import purchase_orders as PO
    src = inspect.getsource(PO.receive_goods)
    assert "if await G.aktif(conn, ctx[\"tenant_id\"]):" in src and "G.rencana_grn(" in src and "G.tulis_grn(" in src
    assert "_tolak(_grn[\"blocks\"])" in src
    assert "G.rencana_grn(" in inspect.getsource(PO._pratinjau_grn)


def test_router_grn_terdaftar_dan_tahan_objek_query():
    from app.routers import goods_receipts as GR
    peta = {(r.path, tuple(sorted(r.methods))) for r in GR.router.routes}
    assert {("", ("GET",)), ("/{gr_id}", ("GET",)), ("/{gr_id}/void", ("POST",)), ("/{gr_id}/void/preview", ("POST",))} <= peta
    src = inspect.getsource(GR.list_goods_receipts)
    assert "isinstance(purchase_order_id, UUID)" in src and "isinstance(status, str)" in src


def test_izin_rute_grn_terpetakan():
    from pathlib import Path
    mw = (Path(G.__file__).parents[1] / "middleware" / "permission_middleware.py").read_text()
    for pola in (r'"^/api/goods-receipts/[^/]+/void$", ["POST"], "purchase_order", "V"',
                 r'"^/api/goods-receipts$", ["GET"], "purchase_order", "R"'):
        assert pola in mw, pola


def test_void_memakai_pembalik_bukan_ubah_status_jurnal():
    src = inspect.getsource(G.void_grn)
    assert "reversal_of=gr[\"journal_id\"]" in src and "reversed_by_id" in src and "record_inventory_reversal" in src
    assert "status = 'VOID'" not in src  # Law 2: jurnal asli tetap POSTED
