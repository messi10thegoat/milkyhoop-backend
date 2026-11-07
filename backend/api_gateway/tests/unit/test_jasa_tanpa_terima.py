"""JASA-TANPA-TERIMA (putusan pemilik LANGSUNG 5 Nov 2026, pola Accurate/QBO/Odoo): baris JASA di PO ditagih menurut qty PESANAN tanpa
langkah Terima barang. Penentu tunggal grn_rencana.perlu_terima / sql_jasa (item_type 'service'; non_inventory & baris tanpa barang =
barang). (a) dasar tagih per baris (batas_tagih -> to-bill + pagar posting; agregat daftar), (b) Terima/GRN mengecualikan jasa,
(c) kontrak pill terima_tersedia / tagih_tersedia, status PO dari baris barang. Tiruan DB mencocokkan POLA, bukan teks SQL utuh."""
import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import grn_rencana as G
from app.services import po_daftar as PD
from app.services import po_rencana as PR

T = "kaos-biru-konveksi"
PO_ID, BRG, JSA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
HARI = date(2026, 11, 5)
APP = Path(PR.__file__).resolve().parents[1]
D = Decimal


def _po(status="sent"):
    return {"id": PO_ID, "tenant_id": T, "status": status, "po_date": date(2026, 11, 1), "tax_rate": D("0"), "po_number": "PO-1"}


def _kode(r):
    return [b["code"] for b in r["blocks"]]


def _fungsi(rel, nama):
    pohon = ast.parse((APP / rel).read_text())
    return next(n for n in ast.walk(pohon) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama)


# ---------------------------------------------------------------- penentu tunggal
def test_penentu_jasa_hanya_service_dan_padanan_sql():
    assert [G.perlu_terima(t) for t in ("service", "goods", "non_inventory", None)] == [False, True, True, True]
    s = G.sql_jasa("poi", "$1")
    assert "p_j.id = poi.item_id" in s and "p_j.tenant_id = $1" in s and f"p_j.item_type = '{G.JENIS_JASA}'" in s
    assert G.JENIS_JASA == "service"


def test_semua_penentu_memakai_sql_jasa_satu_sumber():
    assert "sql_jasa(" in ast.unparse(_fungsi("services/grn_rencana.py", "batas_tagih"))
    assert "sql_jasa(" in ast.unparse(_fungsi("services/po_rencana.py", "rencana_terima"))
    assert "sql_jasa(" in ast.unparse(_fungsi("services/po_daftar.py", "sinkron_tagih"))
    assert "AS jasa" in PD.SQL_AGREGAT and G.sql_jasa("i", "po.tenant_id") in PD.SQL_AGREGAT
    assert "CASE WHEN q.jasa THEN 0 ELSE" in PD.SQL_AGREGAT  # sisa_terima tanpa jasa
    assert "CASE WHEN q0.jasa THEN i.quantity ELSE COALESCE(i.quantity_received, 0) END AS dasar" in PD.SQL_AGREGAT
    assert "GREATEST(q.dasar - q.posted, 0)" in PD.SQL_AGREGAT and "GREATEST(q.dasar - q.pagar, 0)" in PD.SQL_AGREGAT


# ---------------------------------------------------------------- (a) dasar tagih
class KonBatas:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items poi" in q and "goods_receipt_items" in q and "AS jasa" in q:
            return self.rows
        raise AssertionError(q[:100])


def _rb(i, jasa, diterima="0", ada=True, ditagih="0", qty="10"):
    return {"id": i, "quantity": D(qty), "ditagih": D(ditagih), "diterima": D(diterima), "ada_grn": ada, "jasa": jasa}


@pytest.mark.asyncio
@pytest.mark.parametrize("jasa,diterima,ada,ditagih,batas,dasar,qty_dasar", [
    (True, "0", True, "0", "10", "dipesan", "10"),      # PO campuran ber-GRN: jasa tetap qty pesanan (dulu 0 = harus "diterima")
    (True, "12", True, "3", "10", "dipesan", "7"),      # jasa lama yang dulu "diterima" lebih: diterima diabaikan, batas = pesanan
    (True, "0", True, "11", "10", "dipesan", "0"),      # sudah ditagih > pesanan (data lama): dijepit 0, tak negatif
    (True, "0", False, "4", "10", "dipesan", "6"),      # PO tanpa GRN: sama dgn perilaku lama
    (False, "6", True, "0", "10", "diterima", "6"),     # barang: 3-arah ketat tetap
    (False, "0", False, "0", "10", "dipesan", "10")])   # barang tanpa GRN: perilaku lama
async def test_batas_tagih_jasa_selalu_dipesan(jasa, diterima, ada, ditagih, batas, dasar, qty_dasar):
    b = (await G.batas_tagih(KonBatas([_rb(BRG, jasa, diterima, ada, ditagih)]), T, [BRG]))[str(BRG)]
    assert (b["batas"], b["dasar"], b["qty_dasar"], b["jasa"]) == (D(batas), dasar, D(qty_dasar), jasa)


class KonTagih(KonBatas):
    """to-bill nyata: baris PO + kueri batas_tagih (bukan monkeypatch) -> dasar tagih dari penentu yang sama dgn posting."""

    def __init__(self, poi, rows):
        super().__init__(rows)
        self.poi = poi

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE id = $1 AND po_id = $2" in q:
            return next((b for b in self.poi if b["id"] == a[0]), None)
        raise AssertionError(q[:100])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE po_id = $1" in q:
            return self.poi
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        raise AssertionError(" ".join(sql.split())[:100])


def _poi(i, qty, nama):
    return {"id": i, "quantity": D(qty), "quantity_billed": D("0"), "unit_price": D("10000"), "discount_percent": D("0"),
            "discount_amount": D("0"), "tax_rate": D("0"), "item_id": uuid.uuid4(), "item_code": "X", "description": nama, "unit": "pcs"}


@pytest.mark.asyncio
async def test_to_bill_po_campuran_ber_grn_jasa_penuh_pesanan_barang_hanya_diterima():
    kon = KonTagih([_poi(BRG, "10", "Kain"), _poi(JSA, "2", "Jasa bordir")],
                   [_rb(BRG, False, diterima="4"), _rb(JSA, True, qty="2")])
    r = await PR.rencana_tagih(kon, _po("partial_received"), None, kunci=False)
    assert r["blocks"] == []
    assert [(ln["po_item"]["description"], ln["qty"], ln["batas"]["dasar"]) for ln in r["lines"]] == [
        ("Kain", D("4"), "diterima"), ("Jasa bordir", D("2"), "dipesan")]


@pytest.mark.asyncio
async def test_to_bill_po_jasa_saja_status_sent_tanpa_terima():
    kon = KonTagih([_poi(JSA, "3", "Jasa bordir")], [_rb(JSA, True, qty="3", ada=False)])
    r = await PR.rencana_tagih(kon, _po("sent"), None, kunci=False)
    assert r["blocks"] == [] and [ln["qty"] for ln in r["lines"]] == [D("3")]


# ---------------------------------------------------------------- (b) Terima mengecualikan jasa
class KonTerima:
    def __init__(self, baris):
        self.baris = baris

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_settings" in q:
            return None
        if "FROM tenant_features" in q:
            return 1
        raise AssertionError(q[:100])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items i" in q and "i.po_id = $1" in q and "AS jasa" in q:
            assert a[1] == T  # predikat jasa ber-tenant eksplisit
            return self.baris
        raise AssertionError(q[:100])


def _bt(i, nama, jasa, qty="10", diterima="0"):
    return {"id": i, "description": nama, "quantity": D(qty), "quantity_received": D(diterima), "quantity_billed": D("0"), "unit": "pcs",
            "total": D("100000"), "jasa": jasa}


async def _terima(kon, items, status="sent"):
    return await PR.rencana_terima(kon, _po(status), HARI, items, HARI, kunci=False)


@pytest.mark.asyncio
async def test_terima_bawaan_melewati_jasa_dan_halaman_hanya_baris_barang():
    r = await _terima(KonTerima([_bt(BRG, "Kain", False), _bt(JSA, "Jasa bordir", True, qty="2")]), None)
    assert r["blocks"] == []
    assert [i["po_item_id"] for i in r["payload"]["items"]] == [str(BRG)]
    assert [b["po_item_id"] for b in r["baris"]] == [str(BRG)]          # halaman Terima = baris barang saja
    assert r["status_sesudah"] == "received"                            # barang lengkap -> diterima (jasa tak menahan)


@pytest.mark.asyncio
async def test_terima_baris_jasa_dikirim_diblok_berkalimat():
    r = await _terima(KonTerima([_bt(BRG, "Kain", False), _bt(JSA, "Jasa bordir", True)]),
                      [{"po_item_id": str(JSA), "quantity_received": D("1")}])
    assert [(b["code"], b["message"]) for b in r["blocks"]] == [
        ("BARIS_JASA_TANPA_TERIMA", "Jasa bordir adalah jasa; jasa tidak perlu diterima. Langsung buat faktur pembelian.")]
    assert r["payload"]["items"] == []


@pytest.mark.asyncio
async def test_terima_po_jasa_saja_diblok():
    r = await _terima(KonTerima([_bt(JSA, "Jasa bordir", True)]), None)
    assert [(b["code"], b["message"]) for b in r["blocks"]] == [
        ("PO_TANPA_BARANG", "Pesanan ini hanya berisi jasa; jasa tidak perlu diterima. Langsung buat faktur pembelian.")]
    assert r["baris"] == [] and r["payload"]["items"] == []


def test_penulis_grn_memakai_penentu_terima_yang_sama():
    # GRN (tulis) dibentuk dari hasil rencana_terima (akan = baris barang) -> jasa tak pernah masuk goods_receipt_items
    assert "terima['akan']" in ast.unparse(_fungsi("services/grn_rencana.py", "rencana_grn"))
    s = ast.unparse(_fungsi("routers/purchase_orders.py", "receive_goods"))
    assert "PR.rencana_terima(" in s and "G.rencana_grn(" in s


# ---------------------------------------------------------------- status PO + pill
class KonSink:
    def __init__(self, status, baris):
        self.status, self.baris, self.writes = status, baris, []

    async def fetchrow(self, sql, *a):
        return {"id": PO_ID, "status": self.status}

    async def fetch(self, sql, *a):
        assert "AS jasa" in " ".join(sql.split())
        return self.baris

    async def execute(self, sql, *a):
        if " ".join(sql.split()).startswith("UPDATE purchase_orders"):
            self.writes.append(a)
        return "OK"


def _bs(q, r, t, jasa):
    return {"quantity": D(q), "quantity_received": D(r), "quantity_billed": D(t), "total": D("1000"), "jasa": jasa}


@pytest.mark.asyncio
@pytest.mark.parametrize("awal,baris,harap", [
    ("partial_received", [_bs("10", "10", "0", False), _bs("2", "0", "0", True)], "received"),   # barang lengkap, jasa belum ditagih
    ("sent", [_bs("2", "0", "0", True)], "sent"),                                                # PO jasa-saja belum ditagih
    ("sent", [_bs("2", "0", "1", True)], "partial_billed"),
    ("sent", [_bs("2", "0", "2", True)], "billed"),                                              # jasa-saja ditagih penuh tanpa terima
    ("received", [_bs("10", "10", "0", False), _bs("2", "0", "2", True)], "partial_billed")])
async def test_sinkron_status_terima_dari_baris_barang_saja(awal, baris, harap):
    kon = KonSink(awal, baris)
    assert (await PD.sinkron_tagih(kon, T, PO_ID))["status"] == harap and kon.writes[-1][2] == harap


def test_pill_daftar_po_sent_berisi_jasa_perlu_ditagih_barang_sent_tidak():
    assert "sent" in PD.STATUS_BISA_TAGIH
    assert PD.aksi_po("sent", None, D("0"), D("2"), HARI, D("2")) == "perlu_ditagih"     # belum_ditagih jasa = qty pesanan
    assert PD.aksi_po("sent", None, D("10"), D("0"), HARI, D("0")) is None               # barang belum diterima: tetap tanpa pill
    assert "'sent', 'partial_received', 'received', 'partial_billed'" in PD.sql_aksi("perlu_ditagih", "$2")


class KonFakta:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        assert f"tf.feature = '{G.FLAG}'" in q and "AS aturan_d" in q
        return self.rows


def _rf(status, sisa_terima, belum, aturan_d=False):
    return {"id": uuid.uuid4(), "status": status, "expected_date": None, "total_amount": D("100"), "diterima_barang": 0, "sisa_terima": D(sisa_terima),
            "belum_ditagih": D(belum), "amount_received": D("0"), "amount_billed": D("0"), "draft_bill_amount": D("0"),
            "belum_ditagih_sesudah_draf": D(belum), "aturan_d": aturan_d}


@pytest.mark.asyncio
async def test_kontrak_pill_terima_tersedia_dan_tagih_tersedia():
    rows = [_rf("sent", "0", "2"),                     # PO jasa-saja: tanpa Terima, langsung faktur
            _rf("sent", "10", "0"),                    # barang belum datang: Terima, belum bisa faktur
            _rf("partial_received", "5", "7"),         # campuran: dua-duanya
            _rf("partial_billed", "5", "0", True),     # aturan D (flag nyala): ditagih dulu, barang masih bisa diterima
            _rf("partial_billed", "5", "0", False),    # flag mati: tidak
            _rf("billed", "0", "0", True)]
    f = await PD.fakta_po(KonFakta(rows), T, [r["id"] for r in rows], HARI)
    assert [(f[r["id"]]["terima_tersedia"], f[r["id"]]["tagih_tersedia"]) for r in rows] == [
        (False, True), (True, False), (True, True), (True, False), (False, False), (False, False)]


def test_router_daftar_dan_detail_membawa_kontrak_pill():
    src = (APP / "routers/purchase_orders.py").read_text()
    assert src.count('"terima_tersedia": f["terima_tersedia"], "tagih_tersedia": f["tagih_tersedia"]') == 2
