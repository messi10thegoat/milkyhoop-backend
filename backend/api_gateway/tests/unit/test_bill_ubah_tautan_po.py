"""BUG-036 (5 Nov 2026): ubah draf tagihan dari PO (GANTI SEMUA baris) dulu menulis baris TANPA purchase_order_item_id ->
posting memilih jalur "tagihan tanpa penerimaan" (stok masuk KEDUA + Dr Persediaan bukan BDBD; nyawit BILL-2611-0001)."""
import os
from contextlib import asynccontextmanager
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import bills_service as BS  # noqa: E402
from app.services import tagihan_rencana as TR  # noqa: E402

PO = "50000000-0000-0000-0000-000000000001"
L1, L2, L3 = (f"60000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3))
P1, P2, P9 = (f"70000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 9))
BILL = UUID("80000000-0000-0000-0000-000000000001")


class _K:
    """po_baris = [(po_item_id, product_id)], lama = [(po_item_id, product_id)] baris tagihan tertaut sekarang."""
    def __init__(self, po=PO, po_baris=((L1, P1), (L2, P2)), lama=((L1, P1), (L2, P2))):
        self.po, self.po_baris, self.lama, self.sql, self.exec = po, po_baris, lama, [], []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "purchase_order_id FROM bills" in sql:
            return self.po
        return 0

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM purchase_order_items" in sql:
            return [{"id": i, "item_id": p} for i, p in self.po_baris]
        if "FROM bill_items" in sql:
            return [{"purchase_order_item_id": i, "product_id": p} for i, p in self.lama]
        return []

    async def fetchrow(self, sql, *a):
        return {"id": BILL, "status_v2": "draft", "vendor_id": None, "tax_rate": 0, "invoice_number": "BILL-T", "amount": 0}

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


def _i(pid, **k):
    return {"product_id": pid, "qty": 40, "price": 233100, "product_name": "x", **k}


@pytest.mark.asyncio
async def test_ubah_harga_tautan_tetap_urut_baris_lama():
    t, b = await TR.tautan_po(_K(), "t", BILL, [_i(P1), _i(P2)])
    assert (t, b) == ([L1, L2], [])


@pytest.mark.asyncio
async def test_urutan_baris_ditukar_tetap_ikut_barang():
    t, b = await TR.tautan_po(_K(), "t", BILL, [_i(P2), _i(P1)])
    assert (t, b) == ([L2, L1], [])


@pytest.mark.asyncio
async def test_barang_sama_dua_baris_urutan_lama():
    k = _K(po_baris=((L1, P1), (L2, P1)), lama=((L1, P1), (L2, P1)))
    assert await TR.tautan_po(k, "t", BILL, [_i(P1), _i(P1)]) == ([L1, L2], [])


@pytest.mark.asyncio
async def test_tagihan_bukan_dari_po_tanpa_tautan_tanpa_blok():
    assert await TR.tautan_po(_K(po=None), "t", BILL, [_i(P1)]) == ([None], [])


@pytest.mark.asyncio
async def test_barang_di_luar_po_dan_baris_teks_tanpa_tautan():
    k = _K(po_baris=((L1, P1), (L3, None)), lama=((L1, P1),))
    assert await TR.tautan_po(k, "t", BILL, [_i(P1), _i(P9), _i(None)]) == ([L1, None, None], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("lama,items", [
    (((L1, P1), (L2, P2)), [_i(P1), _i(P1), _i(P2)]),  # baris barang P1 dipecah dua -> tak jelas
    ((), [_i(P1)]),                                   # tautan sudah hilang (draf rusak sebelum perbaikan)
])
async def test_tak_bisa_dicocokkan_diblok_bukan_diam_tanpa_penerimaan(lama, items):
    t, b = await TR.tautan_po(_K(lama=lama), "t", BILL, items)
    assert b and b[0]["code"] == "TAUTAN_PO_HILANG"


@pytest.mark.asyncio
async def test_tautan_eksplisit_wajib_milik_po_dan_tak_ganda():
    _, b = await TR.tautan_po(_K(), "t", BILL, [_i(P1, purchase_order_item_id=L3)])
    assert b[0]["code"] == "TAUTAN_PO_TIDAK_SAH"
    _, b = await TR.tautan_po(_K(), "t", BILL, [_i(P1, purchase_order_item_id=L1), _i(P1, purchase_order_item_id=L1)])
    assert b[0]["code"] == "TAUTAN_PO_GANDA"
    _, b = await TR.tautan_po(_K(), "t", BILL, [_i(P2, purchase_order_item_id=L1)])  # baris PO milik barang lain
    assert b[0]["code"] == "TAUTAN_PO_TIDAK_SAH"


@pytest.mark.asyncio
async def test_semua_kueri_bertenant():
    k = _K()
    await TR.tautan_po(k, "t", BILL, [_i(P1)])
    assert len(k.sql) == 3 and all("tenant_id = $2" in s for s in k.sql)  # Law 24


@pytest.mark.asyncio
async def test_blok_ubah_memuat_blok_tautan():
    b = await TR.blok_ubah(_K(lama=()), "t", {"id": BILL, "status_v2": "draft", "vendor_id": None}, {"items": [_i(P1)]})
    assert "TAUTAN_PO_HILANG" in [x["code"] for x in b]


@pytest.mark.asyncio
async def test_update_bill_v2_menulis_ulang_baris_DENGAN_tautan_po(monkeypatch):
    """Perilaku tulis (bukan inspeksi teks): INSERT bill_items membawa purchase_order_item_id baris lama."""
    async def nol(*a, **k):
        return []

    async def hitung(conn, tid, req):
        n = len(req["items"])
        return {"header": {f: 0 for f in ("subtotal", "item_discount_total", "invoice_discount_total", "cash_discount_total",
                                          "dpp", "tax_amount", "grand_total")},
                "baris": [{"item_calc": {"discount_amount": 0, "total": 1, "subtotal": 1}, "dpp": 1, "ppn": 0}] * n}

    async def timpa(*a):
        return None

    monkeypatch.setattr(TR, "blok_ubah", nol)
    monkeypatch.setattr(TR, "hitung", hitung)
    monkeypatch.setattr(BS, "_timpa_nama_dari_master", timpa)
    k = _K()

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield k

    r = await BS.BillsService(_Pool()).update_bill_v2("t", BILL, {"items": [_i(P2), _i(P1)]}, None)
    assert r["success"], r
    ins = [a for s, a in k.exec if "INSERT INTO bill_items" in s]
    assert [a[-1] for a in ins] == [UUID(L2), UUID(L1)]
    hapus = [i for i, (s, _) in enumerate(k.exec) if "DELETE FROM bill_items" in s][0]
    assert hapus < [i for i, (s, _) in enumerate(k.exec) if "INSERT INTO bill_items" in s][0]
