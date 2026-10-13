"""13 Okt 2026 (W-20261013-SJSO-SUDAHFAKTUR; Law 1/16 ledger, 13 kunci, 24 tenant eksplisit, 25 Decimal): baris SO yang SUDAH difaktur
(terbit: bukan draf/void) tapi belum dikirim TIDAK boleh dikirim lewat SJ tanpa faktur -- SJ susulan tak pernah tertaut (alokasi hanya saat
faktur diposting) sehingga /fulfill faktur mengirim LAGI (stok + HPP ganda; terukur di scratch kaos).
sisa_SJ = qty - difaktur - (SJ-so aktif - teralokasi). Plus: `dilewati` memuat SEMUA alasan (tak diam) dan warehouse.source."""
import uuid
from decimal import Decimal

import pytest

from app.routers import sj_so as R
from app.services import sj_so as S
from tests.unit import test_sj_so as B   # fixture autouse + Konek + pembantu yang sama
from tests.unit.test_sj_so import _pasang, ctx, jalan, kodes, baris, SO, L1, L2, L3, P1, P2, GUD  # noqa: F401

INV1, INV2 = uuid.uuid4(), uuid.uuid4()
D = Decimal


class K(B.Konek):
    def __init__(self, *a, faktur=None, sj_baris=None, so_gudang=None, **k):
        super().__init__(*a, **k)
        self.faktur, self.sj_baris, self.so_gudang = faktur or [], sj_baris or [], so_gudang

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if "FROM sales_invoices si JOIN sales_invoice_items sii" in s and "NOT IN ('draft', 'void')" in s:
            return self.faktur
        if "FROM invoice_fulfillments f JOIN invoice_fulfillment_items ifi" in s and "f.asal = 'so'" in s:
            return self.sj_baris
        return await super().fetch(sql, *a)

    async def fetchrow(self, sql, *a):
        r = await super().fetchrow(sql, *a)
        if r and "order_number" in r:
            r = {**r, "warehouse_id": self.so_gudang}
        return r


def f(soi, inv, no, q):
    return {"soi_id": soi, "invoice_id": inv, "invoice_number": no, "q": D(str(q))}


def sjb(soi, s, alok):
    return {"soi_id": soi, "s": D(str(s)), "alok": D(str(alok))}


def body(*pairs, **k):
    return S.SJSOIn(items=[{"sales_order_item_id": i, "quantity": D(str(q))} for i, q in pairs], **k)


# ---- inti: baris sudah difaktur
def test_items_baris_sudah_difaktur_diblok_bermedan_dengan_faktur():
    c = K(rows=[baris(L1, P1, 10)], faktur=[f(L1, INV1, "INV-2610-0001", 10)])
    r = jalan(S.rencana(c, ctx(), SO, body((L1, 5))))
    assert kodes(r) == ["SJ_SO_BARIS_SUDAH_DIFAKTUR"]
    b = r["blocks"][0]
    assert b["sales_order_item_id"] == str(L1) and b["status"] == 409
    assert b["faktur"] == [{"invoice_id": str(INV1), "invoice_number": "INV-2610-0001"}]
    assert (b["invoice_id"], b["invoice_number"]) == (str(INV1), "INV-2610-0001")
    assert "INV-2610-0001" in b["message"]
    assert r["lines"] == []


def test_default_baris_sudah_difaktur_masuk_dilewati_bukan_lines():
    c = K(rows=[baris(L1, P1, 10), baris(L2, P2, 4)], faktur=[f(L1, INV1, "INV-1", 6), f(L1, INV2, "INV-2", 4)])
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    d = [x for x in r["dilewati"] if x["alasan"] == "SJ_SO_BARIS_SUDAH_DIFAKTUR"]
    assert len(d) == 1 and d[0]["sales_order_item_id"] == str(L1)
    assert [(v["invoice_id"], v["invoice_number"]) for v in d[0]["faktur"]] == [(str(INV1), "INV-1"), (str(INV2), "INV-2")]
    assert (d[0]["invoice_id"], d[0]["invoice_number"]) == (str(INV1), "INV-1")   # tingkat atas = faktur PERTAMA
    assert [l["sales_order_item_id"] for l in r["lines"]] == [str(L2)]   # baris belum difaktur tetap dikirim satu SJ
    assert r["blocks"] == []


def test_sebagian_difaktur_sisa_tanpa_faktur_boleh_selebihnya_diblok():
    c = K(rows=[baris(L1, P1, 10)], faktur=[f(L1, INV1, "INV-1", 4)])
    ok = jalan(S.rencana(c, ctx(), SO, body((L1, 6))))
    assert ok["blocks"] == [] and ok["lines"][0]["quantity"] == D("6") and ok["lines"][0]["invoiced_quantity"] == D("4")
    lebih = jalan(S.rencana(c, ctx(), SO, body((L1, 7))))
    assert kodes(lebih) == ["SJ_SO_BARIS_SUDAH_DIFAKTUR"]
    default = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert default["lines"][0]["quantity"] == D("6")   # default = sisa yang BOLEH, bukan sisa mentah


def test_qty_melebihi_sisa_tanpa_faktur_tetap_kode_lama():
    c = K(rows=[baris(L1, P1, 10)])   # tanpa faktur
    r = jalan(S.rencana(c, ctx(), SO, body((L1, 11))))
    assert kodes(r) == ["SJ_SO_QTY_MELEBIHI_SISA"]


# ---- urutan SJ dulu -> faktur (alur AMAN) tetap benar
def test_sj_dulu_lalu_faktur_teralokasi_tak_menghitung_ganda():
    # SJ-so 3 sudah teralokasi ke faktur 10 (alokasi 3); SO 10: seluruhnya difaktur -> sisa SJ 0 (sisa 7 dikirim lewat /fulfill)
    c = K(rows=[baris(L1, P1, 10)], terkirim={L1: D("3")}, faktur=[f(L1, INV1, "INV-1", 10)], sj_baris=[sjb(L1, 3, 3)])
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert r["lines"] == [] and [d["alasan"] for d in r["dilewati"]] == ["SJ_SO_BARIS_SUDAH_DIFAKTUR"]


def test_sj_belum_difaktur_sisa_sama_dengan_perilaku_lama():
    # SJ-so 3 belum teralokasi, tak ada faktur terbit (draf tak dihitung): sisa = 10 - 3 = 7 (sama dengan sebelum perubahan)
    c = K(rows=[baris(L1, P1, 10)], terkirim={L1: D("3")}, sj_baris=[sjb(L1, 3, 0)])
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert r["blocks"] == [] and r["lines"][0]["quantity"] == D("7") and r["lines"][0]["delivered_before"] == D("3")


def test_faktur_sebagian_setelah_sj_hitung_tunggal():
    # SO 10; SJ-so 3 teralokasi ke faktur 4 (alok 3) -> difaktur 4, SJ belum-teralokasi 0 -> sisa 6; terkirim 3 -> sisa lama 7 -> min = 6
    c = K(rows=[baris(L1, P1, 10)], terkirim={L1: D("3")}, faktur=[f(L1, INV1, "INV-1", 4)], sj_baris=[sjb(L1, 3, 3)])
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert r["lines"][0]["quantity"] == D("6")


# ---- dilewati: tak diam
def test_dilewati_memuat_tidak_perlu_dan_sudah_terkirim_dengan_alasan_dan_pesan():
    c = K(rows=[baris(L1, P1, 10), baris(L2, P2, 4, perlu=False, desc="Biaya"), baris(L3, uuid.uuid4(), 5, desc="Jahit")],
          terkirim={L3: D("5")})
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    peta = {d["sales_order_item_id"]: d for d in r["dilewati"]}
    assert peta[str(L2)]["alasan"] == "SJ_SO_BARIS_TIDAK_PERLU_KIRIM" and peta[str(L2)]["message"]
    assert peta[str(L3)]["alasan"] == "SJ_SO_BARIS_SUDAH_TERKIRIM" and peta[str(L3)]["message"]
    assert [l["sales_order_item_id"] for l in r["lines"]] == [str(L1)]


# ---- warehouse.source
@pytest.mark.parametrize("diminta,so_gudang,sumber", [(GUD, None, "diminta"), (None, GUD, "so"), (None, None, "default")])
def test_warehouse_source(diminta, so_gudang, sumber):
    c = K(rows=[baris(L2, P2, 5, stok=True, desc="Kaos")], flag_stok=True, so_gudang=so_gudang)
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn(warehouse_id=diminta)))
    assert r["blocks"] == [] and r["warehouse"]["source"] == sumber and r["warehouse"]["id"] == str(GUD) and r["warehouse"]["source_label"]


def test_warehouse_null_tanpa_baris_stok():
    c = K(rows=[baris(L1, P1, 10)])
    assert jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))["warehouse"] is None


# ---- bentuk JSON router (Decimal -> float)
def test_router_bentuk_kontrak_final_faktur_dan_source_label():
    c = K(rows=[baris(L1, P1, 10)], faktur=[f(L1, INV1, "INV-1", 10)])
    b = R._bentuk(jalan(S.rencana(c, ctx(), SO, body((L1, 1)))))
    assert b["ok"] is False
    blok = b["blocks"][0]
    assert blok["faktur"] == [{"invoice_id": str(INV1), "invoice_number": "INV-1"}] and blok["invoice_number"] == "INV-1"
    import json
    json.dumps(b)   # JSON murni (tanpa Decimal)
    c2 = K(rows=[baris(L2, P2, 5, stok=True, desc="Kaos")], flag_stok=True)
    w = R._bentuk(jalan(S.rencana(c2, ctx(), SO, S.SJSOIn())))["warehouse"]
    assert w == {"id": str(GUD), "name": "Gudang Utama", "source": "default", "source_label": "Gudang bawaan"}


# ---- penjaga statis SQL (Law 24: tenant eksplisit; draf/void tak dihitung)
def test_sql_faktur_terbit_tenant_eksplisit_dan_saring_draf_void():
    q = " ".join(S.SQL_FAKTUR_TERBIT_PER_BARIS.split())
    assert "si.tenant_id = $1" in q and "ORDER BY si.invoice_date" in q and "si.status NOT IN ('draft', 'void')" in q and "si.sales_order_id = $2" in q
    q2 = " ".join(S.SQL_SJ_SO_PER_BARIS.split())
    assert "f.tenant_id = $1" in q2 and "a.tenant_id = f.tenant_id" in q2 and "a.voided_at IS NULL" in q2 and "f.voided_at IS NULL" in q2
