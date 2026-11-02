"""GRN-SJ-VENDOR (V472, 2 Nov 2026, rancangan disetujui MASTER): No. SJ vendor pada Penerimaan Barang.

SPEK:
- badan /receive + pratinjau menerima vendor_delivery_note (strip, kosong -> None, maks 100); pratinjau juga menerima notes.
- payload pratinjau = badan /receive LENGKAP (notes + vendor_delivery_note bila diisi; tanpa nilai = kunci tak ditambahkan).
- tulis_grn menyimpan kolom; daftar/detail (_bentuk), PDF, ekspor (Referensi) membawanya.
- No. SJ sama + vendor sama + GRN non-batal -> CATATAN SJ_VENDOR_SUDAH_DIPAKAI (bukan blok) menyebut nomor GRN.
- migrasi aditif: kolom NULL + CHECK 1..100 + indeks parsial biasa (tanpa CONCURRENTLY/BEGIN/unique); rollback membalik.
"""
import asyncio
import inspect
import pathlib
import re
import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import purchase_orders as SP
from app.services import grn_rencana as G
from app.services import po_rencana as PR
from app.routers import goods_receipts as RG
from app.routers import purchase_orders as RPO
from app.services import bulk_export_beli as BE

APP = pathlib.Path(G.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations"
T = "tenant-uji"


def _j(c):
    return asyncio.run(c)


# ---- skema ----
@pytest.mark.parametrize("masuk,harap", [("  SJ-0012 ", "SJ-0012"), ("", None), ("   ", None), (None, None), ("A" * 100, "A" * 100)])
def test_normal_sj_vendor(masuk, harap):
    b = SP.ReceiveGoodsRequest(receive_date=date(2026, 11, 2), items=[{"po_item_id": "x", "quantity_received": 1}],
                               vendor_delivery_note=masuk)
    assert b.vendor_delivery_note == harap
    assert SP.ReceivePreviewRequest(vendor_delivery_note=masuk).vendor_delivery_note == harap


def test_sj_vendor_lebih_100_ditolak_dan_pratinjau_terima_notes():
    with pytest.raises(ValidationError):
        SP.ReceiveGoodsRequest(receive_date=date(2026, 11, 2), items=[{"po_item_id": "x", "quantity_received": 1}],
                               vendor_delivery_note="A" * 101)
    p = SP.ReceivePreviewRequest(notes="catatan", vendor_delivery_note="SJ-1")
    assert (p.notes, p.vendor_delivery_note) == ("catatan", "SJ-1")


# ---- payload pratinjau ----
class ConnPO:
    def __init__(self, baris):
        self.baris = baris

    async def fetch(self, sql, *a):
        return self.baris

    async def fetchval(self, sql, *a):
        return False  # flag GRN untuk aturan D (status bukan ditagih -> tak dibaca)


def _po():
    return {"id": uuid.uuid4(), "tenant_id": T, "status": "sent", "po_date": date(2026, 10, 1)}


def _baris():
    return [{"id": uuid.uuid4(), "description": "Kaos", "quantity": 5, "quantity_received": 0, "quantity_billed": 0, "unit": "pcs",
             "total": 50000}]


def test_payload_membawa_notes_dan_sj_vendor_bila_diisi():
    r = _j(PR.rencana_terima(ConnPO(_baris()), _po(), date(2026, 11, 2), None, date(2026, 11, 2), kunci=False,
                             notes="pagi", sj_vendor="SJ-77"))
    assert r["payload"]["notes"] == "pagi" and r["payload"]["vendor_delivery_note"] == "SJ-77"


def test_payload_tanpa_nilai_tak_menambah_kunci():
    r = _j(PR.rencana_terima(ConnPO(_baris()), _po(), date(2026, 11, 2), None, date(2026, 11, 2), kunci=False))
    assert set(r["payload"]) == {"receive_date", "items"}


# ---- catatan sudah dipakai ----
class ConnSJ:
    def __init__(self, nomor):
        self.nomor, self.q = nomor, []

    async def fetch(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return [{"receipt_number": n} for n in self.nomor]


def test_sj_vendor_dipakai_predikat_dan_catatan():
    vid = uuid.uuid4()
    c = ConnSJ(["GRN-2611-0001", "GRN-2611-0004"])
    nomor = _j(G.sj_vendor_dipakai(c, T, vid, "SJ-9"))
    sql, a = c.q[0]
    assert "tenant_id = $1 AND vendor_id = $2 AND vendor_delivery_note = $3" in sql and "status <> 'void'" in sql and a == (T, vid, "SJ-9")
    assert G.catatan_sj_dipakai("SJ-9", nomor) == {
        "code": "SJ_VENDOR_SUDAH_DIPAKAI", "message": "No. SJ vendor SJ-9 sudah dipakai di GRN-2611-0001, GRN-2611-0004."}
    c = ConnSJ(["X"])
    assert _j(G.sj_vendor_dipakai(c, T, vid, None)) == [] and _j(G.sj_vendor_dipakai(c, T, None, "SJ")) == [] and c.q == []


def test_pratinjau_grn_catatan_bukan_blok(monkeypatch):
    async def aktif(conn, tid): return True
    async def rencana(conn, tid, po, terima, wh): return {"blocks": []}
    async def dipakai(conn, tid, vid, sj): return ["GRN-2611-0002"] if sj else []
    monkeypatch.setattr(G, "aktif", aktif)
    monkeypatch.setattr(G, "rencana_grn", rencana)
    monkeypatch.setattr(G, "untuk_respons", lambda g: {"warehouse": None})
    monkeypatch.setattr(G, "sj_vendor_dipakai", dipakai)
    terima = {"blocks": []}
    out = _j(RPO._pratinjau_grn(None, T, {"vendor_id": uuid.uuid4()}, terima, None, "SJ-5"))
    assert out["notes"] == [{"code": "SJ_VENDOR_SUDAH_DIPAKAI", "message": "No. SJ vendor SJ-5 sudah dipakai di GRN-2611-0002."}]
    assert terima["blocks"] == []
    assert _j(RPO._pratinjau_grn(None, T, {"vendor_id": uuid.uuid4()}, {"blocks": []}, None, None))["notes"] == []


def test_rute_meneruskan_medan():
    pv = inspect.getsource(RPO.preview_receive_goods)
    assert "sj_vendor=body.vendor_delivery_note if body else None" in pv and "notes=body.notes if body else None" in pv
    assert "body.vendor_delivery_note if body else None))" in pv
    tulis = inspect.getsource(RPO)
    assert re.search(r"G\.tulis_grn\(conn, ctx, po, body\.receive_date, _grn, body\.notes,\s+body\.vendor_delivery_note\)", tulis)


def test_tulis_grn_menyimpan_kolom():
    s = inspect.getsource(G.tulis_grn)
    assert "notes, created_by, vendor_delivery_note) VALUES ($1, $2, $3, $4, $5, $6, $7, 'posted', $8, $9, $10)" in s
    assert "gudang_id, notes, uid, sj_vendor)" in s


# ---- pembaca ----
def test_bentuk_daftar_detail_membawa_sj_vendor():
    gr = {"id": uuid.uuid4(), "receipt_number": "GRN-1", "purchase_order_id": uuid.uuid4(), "receipt_date": date(2026, 11, 2),
          "status": "posted", "vendor_delivery_note": "SJ-3", "voided_at": None}
    assert RG._bentuk(gr)["vendor_delivery_note"] == "SJ-3"
    assert RG._bentuk({**gr, "vendor_delivery_note": None})["vendor_delivery_note"] is None


def test_pdf_dan_ekspor():
    from app.services import cetak_goods_receipt as CG
    s = inspect.getsource(CG)
    assert "g.vendor_delivery_note" in s and '"vendor_delivery_note": gr["vendor_delivery_note"]' in s
    t = (APP / "templates/pdf/goods_receipt.html").read_text()
    assert "{% if gr.vendor_delivery_note %}<tr><td>No. SJ Vendor</td><td>:</td><td>{{ gr.vendor_delivery_note }}</td></tr>{% endif %}" in t
    spec = BE.SPEC_BELI["goods-receipts"]
    assert "g.vendor_delivery_note" in spec.sql
    rows = [{"id": 1, "vendor_name": "V", "receipt_number": "GRN-1", "receipt_date": None, "po_number": "PO-1", "warehouse_name": None,
             "status": "posted", "vendor_delivery_note": "SJ-8"}]

    class D:
        @staticmethod
        def medan(r): return {"status_text": "Diterima"}
    import app.services.bulk_export_beli as M
    asli = M.GD
    M.GD = D
    try:
        baris = _j(M._grn(None, T, rows))[1]
    finally:
        M.GD = asli
    assert baris[-1] == "SJ-8" and len(baris) == len(spec.kolom) + 2


# ---- migrasi ----
def test_migrasi_v472_aditif_dan_rollback():
    def sql(n):
        return re.sub(r"--[^\n]*", "", (MIG / n).read_text())
    s, r = sql("V472__grn_sj_vendor.sql"), sql("V472__grn_sj_vendor_ROLLBACK.sql")
    assert "ADD COLUMN IF NOT EXISTS vendor_delivery_note TEXT NULL" in s
    assert "CHECK (vendor_delivery_note IS NULL OR char_length(vendor_delivery_note) BETWEEN 1 AND 100)" in s
    assert re.search(r"CREATE INDEX IF NOT EXISTS idx_goods_receipts_sj_vendor ON goods_receipts \(tenant_id, vendor_id, vendor_delivery_note\)"
                     r"\s+WHERE vendor_delivery_note IS NOT NULL AND status <> 'void';", s)
    for larangan in ("CONCURRENTLY", "BEGIN", "COMMIT", "UNIQUE", "UPDATE ", "DELETE "):
        assert larangan not in s.upper(), larangan
    assert r.index("DROP INDEX IF EXISTS idx_goods_receipts_sj_vendor") < r.index("DROP COLUMN IF EXISTS vendor_delivery_note")
