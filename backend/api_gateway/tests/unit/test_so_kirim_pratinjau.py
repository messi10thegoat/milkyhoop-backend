"""POST /sales-orders/{id}/fulfill(/preview) — Kirim barang CW (30 Sep 2026, MASTER/WORKSPACE).

Rencana (services/so_pengiriman.py): baris SO -> baris faktur TERBIT yang pengirimannya terbuka, TERTUA-DULU; SEMUA
blok (stok DIJUMLAH lintas faktur); lalu inti yang SAMA dengan /sales-invoices/{id}/fulfill (_execute_fulfillment)
per faktur. Pratinjau: transaksi SELALU di-ROLLBACK. Tulis: satu transaksi untuk semua faktur, kunci faktur SEBELUM
rencana membaca, blok -> 409 (periode -> 403, Law 5). Uji nyata di salinan DB: scratchpad nyata_salinan.py.
"""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException, Response  # noqa: E402

from app.services import so_pengiriman as SP  # noqa: E402
from app.services import so_kirim as SK  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.routers import sales_invoices as SI  # noqa: E402
from app.utils import tanggal_tenant as TT  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-0000000000f1")
IA, IB = UUID("60000000-0000-0000-0000-0000000000a1"), UUID("60000000-0000-0000-0000-0000000000b1")
L1, L2, L3 = (UUID(f"20000000-0000-0000-0000-00000000000{i}") for i in (1, 2, 3))
P1, P2 = UUID("30000000-0000-0000-0000-000000000001"), UUID("30000000-0000-0000-0000-000000000002")
WH = UUID("50000000-0000-0000-0000-000000000001")
HARI = date(2026, 9, 30)


def _req(key=None):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}),
                           headers=({"X-Idempotency-Key": key} if key else {}))


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

    async def __aenter__(self):
        self.c.tx.append("sp")
        return self.c

    async def __aexit__(self, et, *a):
        self.c.tx.append("sp-rollback" if et else "sp-release")
        return False


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


def _sii(i, inv, soi, pid, qty, terkirim=0, dilacak=True, perlu=None, no=1):
    return {"id": UUID(int=1000 + i), "invoice_id": inv, "sales_order_item_id": soi, "item_id": pid,
            "description": f"Barang {pid.int % 10}", "unit": "pcs", "quantity": Decimal(qty),
            "fulfilled_qty": Decimal(terkirim), "allocated_amount": Decimal("100"), "recognized_amount": Decimal("0"),
            "perlu_kirim": perlu, "line_number": no, "dilacak": dilacak, "nama_produk": f"Barang {pid.int % 10}"}


class _C:
    """Bawaan: SO dengan baris L1 (5 pcs P1) difakturkan di IA (2) dan IB (3); L2 (1 pcs P2) di IB."""

    def __init__(self, stok=None, wac=None, fak=None, baris=None, soi=None, draf=(), draf_qty=None, gudang=None, nk=()):
        self.fak = fak if fak is not None else [
            {"id": IA, "invoice_number": "INV-A", "invoice_date": date(2026, 9, 1), "created_at": 1, "status": "posted",
             "fulfillment_status": "pending", "customer_name": "Budi", "total_amount": Decimal("1")},
            {"id": IB, "invoice_number": "INV-B", "invoice_date": date(2026, 9, 5), "created_at": 2, "status": "partial",
             "fulfillment_status": "partial", "customer_name": "Budi", "total_amount": Decimal("1")}]
        self.baris = baris if baris is not None else [
            _sii(1, IA, L1, P1, "2"), _sii(2, IB, L1, P1, "3"), _sii(3, IB, L2, P2, "1", no=2)]
        self.soi = soi if soi is not None else [
            {"id": L1, "description": "Barang 1", "unit": "pcs", "quantity": Decimal("5"), "sort_order": 1, "butuh_kirim": True},
            {"id": L2, "description": "Barang 2", "unit": "pcs", "quantity": Decimal("1"), "sort_order": 2, "butuh_kirim": True}]
        self.stok = {P1: Decimal("100"), P2: Decimal("100")} if stok is None else stok
        self.wac = {P1: Decimal("10"), P2: Decimal("10")} if wac is None else wac
        self.draf, self.draf_qty = list(draf), draf_qty or {}
        self.gudang = gudang if gudang is not None else [
            {"id": WH, "name": "Gudang Utama", "is_active": True, "is_default": True}]
        self.nk = list(nk)
        self.tx, self.q, self.urutan = [], [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)
        if "INVOICE_FULFILL" in "".join(str(x) for x in a):
            self.urutan.append(("kunci", a[1]))

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if "FROM sales_orders WHERE id = $1 AND tenant_id = $2" in sql and "order_number" in sql:
            assert a[1] == T
            return {"id": SOID, "order_number": "SO-9", "status": "invoiced", "warehouse_id": None}
        if "SELECT status, shipped_qty FROM sales_orders" in sql:
            return {"status": "completed", "shipped_qty": Decimal("6")}
        if "FROM warehouses" in sql:
            return next((g for g in self.gudang if g["id"] == a[1]), None)
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "status <> 'void' ORDER BY id" in sql:
            return [{"id": f["id"]} for f in sorted(self.fak, key=lambda f: str(f["id"]))]
        if "SELECT invoice_number FROM sales_invoices" in sql:
            return [{"invoice_number": n} for n in self.draf]
        if "si.fulfillment_status = ANY" in sql:
            self.urutan.append(("baca-faktur", None))
            assert "ORDER BY si.invoice_date, si.created_at, si.invoice_number" in sql  # tertua-dulu = SQL
            assert list(a[2]) == list(SK.FAKTUR_TERBIT_KIRIM) and list(a[3]) == list(SK.KIRIM_TERBUKA)
            return list(self.fak)
        if "FROM sales_invoice_items sii" in sql and "LEFT JOIN products" in sql:
            return [b for b in self.baris if b["invoice_id"] in a[1]]
        if "credit_note_deferral_lines" in sql:
            return [{"invoice_item_id": i} for i in self.nk]
        if "FROM sales_order_items soi LEFT JOIN products" in sql:
            return list(self.soi)
        if "FROM warehouses" in sql:
            return [g for g in self.gudang if g["is_active"]]
        if "SUM(sii.quantity)" in sql:
            return [{"soi_id": k, "q": v} for k, v in self.draf_qty.items()]
        if "FROM sales_order_items WHERE sales_order_id" in sql:
            return [{"id": s["id"], "quantity": s["quantity"]} for s in self.soi]
        raise AssertionError(sql[:80])

    async def fetchval(self, sql, *a):
        self.q.append(sql)
        if "FROM warehouse_stock" in sql:
            return self.stok.get(a[0])
        if "get_weighted_average_cost" in sql:
            return self.wac.get(a[1])
        raise AssertionError(sql[:80])


@pytest.fixture
def pasang(monkeypatch):
    rekam = {"inti": [], "replay": []}

    def _p(c, periode_tutup=False, terkirim=None, gagal_ke=None):
        async def tgl(conn, tid):
            return HARI
        monkeypatch.setattr(TT, "tanggal_dokumen", tgl)

        async def periode(conn, tid, d):
            if periode_tutup:
                raise HTTPException(status_code=403, detail="Cannot post to closed period (2026-09)")
        monkeypatch.setattr(SI, "check_period_is_open", periode)

        async def kirim(conn, tid, ids):
            return (terkirim or {}), {}
        monkeypatch.setattr(SK, "terkirim_per_baris", kirim)

        async def inti(conn, tid, uid, invoice, items, wh, tgl_, recognize_revenue=True, idempotency_key=None,
                       notes=None, payload_hash=None):
            rekam["inti"].append({"invoice": invoice["invoice_number"], "items": [(i["invoice_item_id"], i["quantity"]) for i in items],
                                  "wh": wh, "tgl": tgl_, "key": idempotency_key, "tx": list(conn.tx)})
            conn.urutan.append(("inti", invoice["invoice_number"]))
            if gagal_ke and len(rekam["inti"]) == gagal_ke:
                raise HTTPException(409, "Stok X tidak cukup")
            n = len(rekam["inti"])
            return {"fulfillment_id": str(UUID(int=n)), "fulfillment_number": f"SJ-2609-000{n}",
                    "total_cogs": "10", "total_revenue": "100"}
        monkeypatch.setattr(SI, "_execute_fulfillment", inti)

        async def ambil(conn, tid, k, s):
            return None
        monkeypatch.setattr(SO, "ambil_replay_klien", ambil)

        async def simpan(conn, tid, k, st, s, resp, result_id=None):
            rekam["replay"].append(k)
        monkeypatch.setattr(SO, "simpan_replay_klien", simpan)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SO, "get_pool", pool)
        return c
    _p.rekam = rekam
    return _p


async def _pv(**badan):
    r = await SO.preview_fulfill_order(_req(), str(SOID), SP.SOFulfillRequest(**badan) if badan else None)
    return r["data"]


def _kode(d):
    return [b["code"] for b in d["blocks"]]


# ---------- rencana + inti ----------

@pytest.mark.asyncio
async def test_bawaan_kirim_semua_per_faktur_tertua_dulu_lewat_inti(pasang):
    c = pasang(_C())
    d = await _pv()
    assert d["ok"] is True and d["blocks"] == []
    assert [(x["invoice"], x["items"]) for x in pasang.rekam["inti"]] == [
        ("INV-A", [(UUID(int=1001), Decimal("2"))]),
        ("INV-B", [(UUID(int=1002), Decimal("3")), (UUID(int=1003), Decimal("1"))])]
    assert all(x["wh"] == WH and x["tgl"] == HARI for x in pasang.rekam["inti"])
    assert c.tx[0] == "start" and c.tx[-1] == "rollback"  # pratinjau SELALU rollback
    assert all("sp" in x["tx"] for x in pasang.rekam["inti"])  # inti di savepoint
    assert [f["fulfillment_number"] for f in d["fulfillments"]] == ["SJ-2609-0001", "SJ-2609-0002"]
    assert d["totals"] == {"quantity": 6.0, "invoices_touched": 2, "cogs_amount": 20.0, "revenue_recognized": 200.0}
    assert d["payload"] == {"items": [{"sales_order_item_id": str(L1), "quantity": "5"},
                                      {"sales_order_item_id": str(L2), "quantity": "1"}],
                            "warehouse_id": str(WH), "fulfillment_date": "2026-09-30", "notes": None}
    assert d["warehouse"] == {"id": str(WH), "name": "Gudang Utama", "source": "tenant_default"}


@pytest.mark.asyncio
async def test_qty_sebagian_mengisi_faktur_tertua_dulu(pasang):
    pasang(_C())
    d = await _pv(items=[{"sales_order_item_id": str(L1), "quantity": "3"}])
    assert d["ok"] is True
    assert [(x["invoice"], x["items"]) for x in pasang.rekam["inti"]] == [
        ("INV-A", [(UUID(int=1001), Decimal("2"))]), ("INV-B", [(UUID(int=1002), Decimal("1"))])]
    l1 = next(x for x in d["lines"] if x["sales_order_item_id"] == str(L1))
    l2 = next(x for x in d["lines"] if x["sales_order_item_id"] == str(L2))
    assert (l1["shippable"], l1["quantity"]) == (5.0, 3.0) and (l2["shippable"], l2["quantity"]) == (1.0, 0.0)


@pytest.mark.asyncio
async def test_stok_dijumlah_lintas_faktur(pasang):
    # stok 4: tiap faktur sendiri-sendiri (2 lalu 3) LOLOS cek per baris; jumlahnya 5 > 4 -> blok
    pasang(_C(stok={P1: Decimal("4"), P2: Decimal("100")}))
    d = await _pv()
    assert _kode(d) == ["SO_FULFILL_STOCK_SHORT"]
    b = d["blocks"][0]
    assert (b["available"], b["requested"], b["sales_order_item_id"]) == (4.0, 5.0, str(L1))
    assert pasang.rekam["inti"] == [] and d["payload"] is None and d["fulfillments"] == []


@pytest.mark.asyncio
async def test_semua_blok_dikumpulkan(pasang):
    pasang(_C(wac={P1: Decimal("10"), P2: Decimal("0")}), periode_tutup=True)  # L2 teralokasi, WAC-nya 0
    d = await _pv(items=[{"sales_order_item_id": str(L1), "quantity": "9"},
                         {"sales_order_item_id": str(UUID(int=77)), "quantity": "1"},
                         {"sales_order_item_id": str(L2), "quantity": "-1"},
                         {"sales_order_item_id": str(L2), "quantity": "1"}],
                  warehouse_id=str(WH))
    assert set(_kode(d)) >= {"SO_FULFILL_QTY_EXCEEDS", "SO_FULFILL_LINE_NOT_IN_SO", "SO_FULFILL_QTY_INVALID",
                             "SO_FULFILL_PERIOD_CLOSED", "SO_FULFILL_NO_COST"}
    assert pasang.rekam["inti"] == []


@pytest.mark.asyncio
async def test_items_kosong_bukan_kirim_semua(pasang):
    pasang(_C())
    d = await _pv(items=[])
    assert _kode(d) == ["SO_FULFILL_NOTHING_TO_SHIP"] and pasang.rekam["inti"] == []
    d = await _pv(items=[{"sales_order_item_id": str(L1), "quantity": "0"}])
    assert _kode(d) == ["SO_FULFILL_NOTHING_TO_SHIP"] and pasang.rekam["inti"] == []


@pytest.mark.asyncio
async def test_non_stok_tanpa_tanda_kirim(pasang):
    baris = [_sii(1, IA, L1, P1, "2"), _sii(3, IB, L2, P2, "1", dilacak=False, perlu=False, no=2)]
    soi = [{"id": L1, "description": "Barang 1", "unit": "pcs", "quantity": Decimal("2"), "sort_order": 1, "butuh_kirim": True},
           {"id": L2, "description": "Jasa", "unit": "jam", "quantity": Decimal("1"), "sort_order": 2, "butuh_kirim": False}]
    pasang(_C(baris=baris, soi=soi))
    d = await _pv()  # bawaan: baris jasa dilewati, bukan blok
    assert d["ok"] is True and [x["invoice"] for x in pasang.rekam["inti"]] == ["INV-A"]
    assert next(x for x in d["lines"] if x["sales_order_item_id"] == str(L2))["reason"] == "NON_STOCK"
    d = await _pv(items=[{"sales_order_item_id": str(L2), "quantity": "1"}])
    assert _kode(d) == ["SO_FULFILL_NOT_SHIPPABLE"]


@pytest.mark.asyncio
async def test_alasan_baris(pasang):
    soi = [{"id": L1, "description": "A", "unit": "pcs", "quantity": Decimal("2"), "sort_order": 1, "butuh_kirim": True},
           {"id": L2, "description": "B", "unit": "pcs", "quantity": Decimal("1"), "sort_order": 2, "butuh_kirim": True},
           {"id": L3, "description": "C", "unit": "pcs", "quantity": Decimal("4"), "sort_order": 3, "butuh_kirim": True}]
    baris = [_sii(1, IA, L1, P1, "2", terkirim="2")]
    pasang(_C(baris=baris, soi=soi, draf=["INV-D"], draf_qty={L2: Decimal("1")}), terkirim={L1: Decimal("2")})
    d = await _pv()
    assert {x["sales_order_item_id"]: x["reason"] for x in d["lines"]} == {
        str(L1): "FULLY_SHIPPED", str(L2): "INVOICE_DRAFT", str(L3): "NOT_INVOICED"}
    assert _kode(d) == ["SO_FULFILL_NOTHING_TO_SHIP"]
    assert "SO_FULFILL_DRAFT_INVOICES" in [n["code"] for n in d["notes"]]


@pytest.mark.asyncio
async def test_tanpa_faktur_terbit(pasang):
    pasang(_C(fak=[], baris=[], draf=["INV-D"], draf_qty={L1: Decimal("5")}))
    d = await _pv()
    assert "SO_FULFILL_NO_INVOICE" in _kode(d) and "INV-D" in d["blocks"][0]["message"]


@pytest.mark.asyncio
async def test_penolakan_inti_jadi_blok(pasang):
    pasang(_C(), gagal_ke=2)
    d = await _pv()
    assert _kode(d) == ["SO_FULFILL_REJECTED"] and d["blocks"][0]["message"] == "Stok X tidak cukup"
    assert d["payload"] is None and d["fulfillments"] == []


# ---------- tulis ----------

async def _tulis(key=None, **badan):
    return await SO.fulfill_order(_req(key), Response(), str(SOID), SP.SOFulfillRequest(**badan))


@pytest.mark.asyncio
async def test_tulis_atomik_kunci_dulu_lalu_rencana_lalu_inti(pasang):
    c = pasang(_C())
    r = await _tulis("K1")
    assert r["success"] is True and [f["fulfillment_number"] for f in r["data"]["fulfillments"]] == ["SJ-2609-0001", "SJ-2609-0002"]
    jenis = [u[0] for u in c.urutan]
    assert jenis == ["kunci", "kunci", "baca-faktur", "inti", "inti"]  # kunci faktur SEBELUM rencana membaca
    assert sorted(u[1] for u in c.urutan if u[0] == "kunci") == [f"INVOICE_FULFILL:{IA}", f"INVOICE_FULFILL:{IB}"]
    assert c.tx == ["sp", "sp-release"]  # SATU transaksi untuk semua faktur
    assert [x["key"] for x in pasang.rekam["inti"]] == [
        f"SO_FULFILL:00000000-0000-0000-0000-0000000000a1:K1:{IA}", f"SO_FULFILL:00000000-0000-0000-0000-0000000000a1:K1:{IB}"]
    assert len(pasang.rekam["replay"]) == 1 and pasang.rekam["replay"][0].startswith("SO_FULFILL:")


@pytest.mark.asyncio
async def test_tulis_blok_409_semua_blok_tanpa_inti(pasang):
    c = pasang(_C(stok={P1: Decimal("4"), P2: Decimal("0")}))
    with pytest.raises(HTTPException) as e:
        await _tulis()
    assert e.value.status_code == 409 and e.value.detail["code"] == "SO_FULFILL_BLOCKED"
    assert [b["code"] for b in e.value.detail["blocks"]] == ["SO_FULFILL_STOCK_SHORT", "SO_FULFILL_STOCK_SHORT"]
    assert pasang.rekam["inti"] == [] and c.tx == ["sp", "sp-rollback"]


@pytest.mark.asyncio
async def test_tulis_periode_tutup_403(pasang):
    pasang(_C(), periode_tutup=True)
    with pytest.raises(HTTPException) as e:
        await _tulis()
    assert e.value.status_code == 403 and e.value.detail["blocks"][0]["code"] == "SO_FULFILL_PERIOD_CLOSED"
    assert pasang.rekam["inti"] == []


@pytest.mark.asyncio
async def test_tulis_faktur_kedua_gagal_semua_batal(pasang):
    c = pasang(_C(), gagal_ke=2)
    with pytest.raises(HTTPException) as e:
        await _tulis("K2")
    assert e.value.detail == "Stok X tidak cukup"
    assert c.tx == ["sp", "sp-rollback"] and pasang.rekam["replay"] == []  # transaksi luar batal -> SJ pertama ikut batal


@pytest.mark.asyncio
async def test_tulis_items_kosong_409(pasang):
    pasang(_C())
    with pytest.raises(HTTPException) as e:
        await _tulis(items=[])
    assert e.value.status_code == 409 and pasang.rekam["inti"] == []
