"""Unit asimetri-draf PO (1 Nov 2026, putusan MASTER; cermin putusan pemilik 25 Sep untuk SO "faktur draf = belum difaktur").

Penjaga: (1) "ditagih" TAMPIL (amount_billed, sisa_ditagih, aksi perlu_ditagih, status partial_billed/billed, qty baris detail) = bill
POSTED; porsi DRAF terpisah (draft_bill_amount / quantity_billed_draft) di daftar, detail, summary, agregat -- SATU predikat;
(2) PAGAR = SEMUA bill non-void (draf ikut) untuk to-bill DAN posting (D2) -- dua angka, dua tujuan; (3) D1: status PO dari bill POSTED,
sinkron di 3 jalur posting; (4) D3: PO draf tanpa sisa ditagih (cermin SO draf tak berjalan), satu himpunan daftar = summary = agregat."""
import asyncio
import inspect
import re
import uuid
from decimal import Decimal as D

import pytest

from app.routers import purchase_orders as POR
from app.services import bills_service as BS
from app.services import po_agregat as PA
from app.services import po_daftar as PD
from app.services import po_rencana as PR
from app.services import tagihan_aksi as TA

T = "t1"


def _jalan(c): return asyncio.run(c)


# ---------------- (1) satu predikat per tujuan ----------------
def test_predikat_tagih_tiga_tujuan():
    assert PD.PRED_TAGIH == {
        "posted": "COALESCE(b.status_v2, b.status) NOT IN ('draft', 'void')",
        "draf": "COALESCE(b.status_v2, b.status) = 'draft'",
        "pagar": "COALESCE(b.status_v2, b.status) <> 'void'",
    }
    for j in PD.PRED_TAGIH:
        s = PD.sql_qty_tagih(j, "x", "$9")
        assert PD.PRED_TAGIH[j] in s and "bi.purchase_order_item_id = x.id" in s and "b.tenant_id = $9" in s
    assert PD.SQL_QTY_TAGIH == PD.sql_qty_tagih("pagar")  # cache quantity_billed = PAGAR


def test_agregat_tampil_dari_posted_draf_terpisah_bukan_cache():
    s = PD.SQL_AGREGAT
    assert PD.sql_qty_tagih("posted", "i", "po.tenant_id") in s and PD.sql_qty_tagih("draf", "i", "po.tenant_id") in s
    assert "q.posted * i.total" in s and "- q.posted, 0)" in s and "q.draf * i.total" in s and "AS draft_bill_amount" in s
    assert "quantity_billed" not in s  # cache PAGAR tak pernah jadi angka tampil


# ---------------- (4) D3: satu himpunan ----------------
def test_himpunan_tanpa_sisa_cermin_so_draf_tak_berjalan():
    from app.services import so_agregat as SA
    assert PD.STATUS_TANPA_SISA_TAGIH == ("draft", "cancelled", "closed")
    assert "draft" in SA.AKTIF_TIDAK  # SO draf tak "berjalan" -> belum difaktur 0 (pembanding putusan D3)
    assert PA.AKTIF_TIDAK is PD.STATUS_TANPA_SISA_TAGIH
    assert "'draft', 'cancelled', 'closed'" in PD.sql_sisa_ditagih() and PD.sql_berjalan() == "po.status NOT IN ('draft', 'cancelled', 'closed')"


class _ConnFakta:
    def __init__(self, rows): self.rows = rows

    async def fetch(self, sql, *a):
        assert "ag.draft_bill_amount" in sql
        return self.rows


@pytest.mark.parametrize("status,sisa", [("draft", D("0")), ("cancelled", D("0")), ("closed", D("0")), ("sent", D("600.00")),
                                         ("partial_billed", D("600.00"))])
def test_fakta_sisa_per_status_dan_draf_terpisah(status, sisa):
    pid = uuid.uuid4()
    r = {"id": pid, "status": status, "expected_date": None, "total_amount": D("1000"), "sisa_terima": D(0), "belum_ditagih": D(0),
         "amount_received": D(0), "amount_billed": D("400"), "draft_bill_amount": D("250.004")}
    f = _jalan(PD.fakta_po(_ConnFakta([r]), T, [pid], None))[pid]
    assert f["sisa_ditagih"] == sisa and f["draft_bill_amount"] == D("250.00") and f["amount_billed"] == D("400.00")


# ---------------- (3) D1: status dari POSTED ----------------
class _ConnSinkron:
    def __init__(self, status, baris): self.status, self.baris, self.exec = status, baris, []

    async def fetchrow(self, sql, *a): return {"id": a[0], "status": self.status}

    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))

    async def fetch(self, sql, *a):
        assert PD.sql_qty_tagih("posted") in sql and a[1] == T  # baris dibaca dengan qty POSTED
        return self.baris


@pytest.mark.parametrize("posted,harap", [(D(0), "received"), (D(4), "partial_billed"), (D(10), "billed")])
def test_sinkron_status_dari_posted_cache_qty_pagar(posted, harap):
    c = _ConnSinkron("received", [{"quantity": D(10), "quantity_received": D(10), "quantity_billed": posted, "total": D("1000")}])
    h = _jalan(PD.sinkron_tagih(c, T, uuid.uuid4()))
    assert h["status"] == harap and h["amount_billed"] == PD.r2(posted * D(100))
    upd_qty = c.exec[0]
    assert "SET quantity_billed = " + PD.SQL_QTY_TAGIH in upd_qty[0]  # cache tetap PAGAR (non-void)
    assert c.exec[-1][1][1:] == (PD.r2(posted * D(100)), harap)  # cache amount_billed + status dari POSTED


def test_tiga_jalur_posting_bill_menyinkron_po():
    for nama in ("create_bill", "create_bill_v2", "post_bill"):
        src = inspect.getsource(getattr(BS.BillsService, nama))
        i = src.index("status_v2 = 'posted'")
        assert src.count("await _pd.sinkron_po_bill(conn, tenant_id, bill_id)") == 1, nama
        assert src.index("await _pd.sinkron_po_bill(conn, tenant_id, bill_id)") > i, nama  # SESUDAH status posted


def test_sinkron_po_bill_kunci_po_lalu_sinkron(monkeypatch):
    dipanggil = []

    async def terkait(conn, tid, bid): return ["P2", "P1"]

    async def pulih(conn, tid, ids):
        dipanggil.append(sorted(ids))
        return []
    monkeypatch.setattr(PD, "po_terkait_bill", terkait)
    monkeypatch.setattr(PD, "pulihkan_tagih", pulih)
    _jalan(PD.sinkron_po_bill(None, T, "B1"))
    assert dipanggil == [["P1", "P2"]]


# ---------------- (2) PAGAR non-void: to-bill + posting ----------------
def test_pagar_posting_menghitung_draf_lain():
    src = inspect.getsource(TA._blok_qty_po)
    assert "COALESCE(b.status_v2, b.status) <> 'void'" in src and "AND b.id <> $2" in src
    assert "NOT IN ('draft', 'void')" not in src


class _ConnPagar:
    def __init__(self, tertagih): self.tertagih, self.sql = tertagih, None

    async def fetch(self, sql, *a):
        self.sql = sql
        return [{"id": "L1", "quantity": D(10), "unit": "pcs", "po_number": "PO-1", "tertagih": self.tertagih}]


@pytest.mark.parametrize("tertagih,ini,blok", [(D(6), D(4), False), (D(6), D(5), True)])
def test_pagar_posting_batas_qty(tertagih, ini, blok):
    c = _ConnPagar(tertagih)  # tertagih = bill lain non-void (draf ikut), dari SQL di atas
    out = _jalan(TA._blok_qty_po(c, T, "B-INI", [{"purchase_order_item_id": "L1", "qty_dasar": ini}]))
    assert bool(out) == blok
    if blok:
        assert out[0]["code"] == "PO_QTY_TERLAMPAUI" and "termasuk draf" in out[0]["message"]


def test_pagar_to_bill_tetap_cache_non_void():
    src = inspect.getsource(PR)
    assert 'unbilled = Decimal(po_item["quantity"]) - Decimal(po_item["quantity_billed"] or 0)' in src
    assert "quantity > COALESCE(quantity_billed, 0)" in src


# ---------------- (1) tampil: daftar, detail, summary, agregat ----------------
def test_daftar_detail_summary_membawa_draft_bill_amount_satu_sumber():
    src = inspect.getsource(POR)
    assert src.count('"draft_bill_amount": float(f["draft_bill_amount"])') == 2  # daftar + detail dari fakta_po
    assert '"quantity_billed": float(item["q_posted"] or 0)' in src and '"quantity_billed_draft": float(item["q_draf"] or 0)' in src
    assert 'PD.sql_qty_tagih("posted")} AS q_posted' in src and 'PD.sql_qty_tagih("draf")} AS q_draf' in src
    ring = inspect.getsource(POR.get_purchase_orders_summary)
    assert "COALESCE(SUM({PD.sql_sisa_ditagih()}), 0) as pending_amount" in ring
    assert "SUM(ag.draft_bill_amount) FILTER (WHERE {PD.sql_berjalan()})" in ring
    assert "'sent', 'partial_received', 'received', 'partial_billed'" not in ring  # himpunan lama pending_amount


def test_agregat_draft_dari_fakta_tanpa_sql_lokal():
    src = inspect.getsource(PA)
    assert "_SQL_DRAF" not in src and "status = 'draft'" not in src
    assert 'f[r["id"]]["draft_bill_amount"]' in inspect.getsource(PA.unbilled)
