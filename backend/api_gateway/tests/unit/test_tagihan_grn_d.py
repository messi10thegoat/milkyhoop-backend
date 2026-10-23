"""Tagihan <-> GRN ATURAN D (23 Okt 2026; putusan pemilik LANGSUNG, kontrak BACKEND<->BACKEND2): baris PO ber-stok SELALU Dr GRNI = nilai terima
+ nilai_tertunda (bagian belum diterima, B), PPV = selisih; GRN_MELEBIHI_TERIMA dicabut -> PO_QTY_TERLAMPAUI (toleransi 0, qty tertagih
diturunkan dari bill_items); tagihan bertaut penerimaan yang datang sesudahnya (tagih_dulu/legacy) -> void DIBLOK TAGIHAN_TERCOCOK_GRN
SEBELUM grn_tagih.balik_tautan (yang gagal-keras)."""
import asyncio
import inspect
from decimal import Decimal as D
from uuid import uuid4

from app.services import tagihan_aksi as TA


class _KonnD:
    def __init__(self, rows=None, po=None, links=None):
        self.rows, self.po, self.links, self.sql = rows or [], po or [], links or [], []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM purchase_order_items poi JOIN purchase_orders po" in sql:
            return self.po
        if "goods_receipt_bill_links" in sql:
            return self.links
        if "bill_payment_allocations" in sql or "vendor_credit_applications" in sql:
            return []
        return self.rows

    async def fetchval(self, sql, *a):
        return None


def test_grni_memuat_bagian_tertunda_dan_ppv_selisih(monkeypatch):
    grni, ppv, inv = uuid4(), uuid4(), uuid4()
    g1, g2 = uuid4(), uuid4()
    rows = [{"id": g1, "product_id": uuid4(), "net": D("1000"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None},
            {"id": g2, "product_id": uuid4(), "net": D("600"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None}]
    grn = {"rencana": {g1: {"nilai_grni": D("380"), "nilai_tertunda": D("600"), "selisih": D("20")},   # bill 10, terima 4 (R 380), 6 tertunda
                       g2: {"nilai_grni": D("0"), "nilai_tertunda": D("600"), "selisih": D("0")}},      # belum diterima sama sekali
           "akun": {"grni": grni, "ppv": ppv}}
    out = {a: amt for a, amt, _ in asyncio.run(TA.hitung_debit(_KonnD(rows), "t", uuid4(), D("1600"), inv, grn))}
    assert out[grni] == D("1580") and out[ppv] == D("20") and inv not in out
    assert sum(out.values()) == D("1600")  # Σ == nilai_neto (Law 4)


def test_tanpa_nilai_tertunda_perilaku_7c_tetap():
    grni, ppv, inv, g = uuid4(), uuid4(), uuid4(), uuid4()
    rows = [{"id": g, "product_id": uuid4(), "net": D("1000"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None}]
    grn = {"rencana": {g: {"nilai_grni": D("950"), "selisih": D("50")}}, "akun": {"grni": grni, "ppv": ppv}}
    out = {a: amt for a, amt, _ in asyncio.run(TA.hitung_debit(_KonnD(rows), "t", uuid4(), D("1000"), inv, grn))}
    assert out == {grni: D("950"), ppv: D("50")}


def test_po_qty_terlampaui_dari_bill_items_bukan_cache():
    po_item = uuid4()
    k = _KonnD(po=[{"id": po_item, "quantity": D("10"), "unit": "pcs", "po_number": "PO-1", "tertagih": D("6")}])
    items = [{"id": uuid4(), "purchase_order_item_id": po_item, "qty_dasar": D("5")}]
    b = asyncio.run(TA._blok_qty_po(k, "t", uuid4(), items))
    assert [x["code"] for x in b] == ["PO_QTY_TERLAMPAUI"] and "PO-1" in b[0]["message"]
    sql = k.sql[0][0]
    assert "FROM bill_items bi" in sql and "quantity_billed" not in sql and "b.id <> $2" in sql and "NOT IN ('draft', 'void')" in sql
    items[0]["qty_dasar"] = D("4")
    assert asyncio.run(TA._blok_qty_po(k, "t", uuid4(), items)) == []          # 6 + 4 = 10 pas -> lolos (toleransi 0)
    assert asyncio.run(TA._blok_qty_po(k, "t", uuid4(), [{"id": uuid4(), "purchase_order_item_id": po_item, "qty_dasar": None}])) == []


def test_rencana_grn_tanpa_grn_melebihi_terima_dan_kontrak_inbound_diblok():
    src = inspect.getsource(TA.rencana_grn)
    assert "GRN_MELEBIHI_TERIMA" not in src and '"GRN_KONTRAK_INBOUND"' in src and "_blok_qty_po(" in src


def test_void_diblok_penerimaan_sesudah_tagihan_lebih_dulu():
    bill = {"id": uuid4(), "status": "posted", "status_v2": "posted", "invoice_number": "BILL-9"}
    k = _KonnD(links=[{"receipt_number": "GRN-2610-0003"}])
    b = asyncio.run(TA.blok_void(k, "t", bill))
    assert b[0]["code"] == "TAGIHAN_TERCOCOK_GRN" and "GRN-2610-0003" in b[0]["message"]
    sql = next(s for s, _ in k.sql if "goods_receipt_bill_links" in s)
    assert "kind IN ('tagih_dulu', 'legacy')" in sql and "l.status = 'active'" in sql and "g.tenant_id = $2" in sql
    assert asyncio.run(TA.blok_void(_KonnD(), "t", bill)) == []


def test_void_bill_memanggil_blok_sebelum_balik_tautan():
    from pathlib import Path
    bs = (Path(TA.__file__).parent / "bills_service.py").read_text()
    vb = bs[bs.index("    async def void_bill("):bs.index("    async def get_summary(")]
    assert vb.index("TA.blok_void(conn") < vb.index("_gt.balik_tautan(")
