"""POST /sales-orders/{id}/close/preview (28 Sep 2026, arahan MASTER untuk halaman "Tutup pesanan" CW).

Satu definisi: /close dan /close/preview memakai _rencana_tutup_so. Pratinjau = SEMUA penghalang,
nol tulisan (transaksi SELALU rollback). /close = penghalang PERTAMA dengan detail yang sama persis
seperti sebelum refaktor (urutan lama: status -> uang muka -> pendapatan tertahan -> belum penuh).
Nilai sisa dibatalkan = dasar total SO (neto sesudah diskon baris & bagian diskon dokumen + PPN dari
DPP berfaktor), TANPA ongkir; angka harapan di sini LITERAL hitungan tangan, bukan keluaran kalkulator.
"""
import inspect
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_orders as SO  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-0000000000c1")
IA = UUID("30000000-0000-0000-0000-00000000000a")
IB = UUID("30000000-0000-0000-0000-00000000000b")
DPID = UUID("20000000-0000-0000-0000-0000000000d1")


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}))


def _baris(on_a=0, on_b=0):
    """A: 10 x 10.000, PPN 12% berfaktor 11/12. B: 5 x 20.000 diskon 10%, tanpa PPN. Diskon dokumen 19.000."""
    return [
        {"id": IA, "description": "Kaos", "unit": "pcs", "quantity": Decimal("10"), "quantity_shipped": Decimal("0"),
         "quantity_invoiced": Decimal(on_a), "unit_price": Decimal("10000"), "discount_percent": Decimal("0"),
         "tax_rate": Decimal("12"), "tax_id": "PPN12", "on_invoices": Decimal(on_a), "sort_order": 1},
        {"id": IB, "description": "Sablon", "unit": "jasa", "quantity": Decimal("5"), "quantity_shipped": None,
         "quantity_invoiced": Decimal(on_b), "unit_price": Decimal("20000"), "discount_percent": Decimal("10"),
         "tax_rate": Decimal("0"), "tax_id": None, "on_invoices": Decimal(on_b), "sort_order": 2},
    ]


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

    async def commit(self):
        self.c.tx.append("commit")

    async def __aenter__(self):
        self.c.tx.append("start")
        return self.c

    async def __aexit__(self, et, *a):
        self.c.tx.append("rollback" if et else "commit")
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


class _C:
    def __init__(self, status="confirmed", baris=None, tertahan=(), dp=(), diskon=Decimal("19000"), ada=True):
        self.status, self.tertahan, self.dp, self.diskon, self.ada = status, list(tertahan), list(dp), diskon, ada
        self.rows = baris if baris is not None else _baris()
        self.tulis, self.tx, self.q = [], [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)
        if "pg_advisory" in sql:
            return
        self.tulis.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        assert a[-1] == T, "setiap kueri SO wajib bertenant"
        if not self.ada:
            return None
        if "SELECT discount_amount FROM sales_orders" in sql:
            return {"discount_amount": self.diskon}
        if "FROM sales_orders" in sql:
            return {"id": SOID, "status": self.status, "order_number": "SO-9"}
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "allocated_amount" in sql:
            assert "si.tenant_id = $2" in sql
            return [{"invoice_number": "INV-1", "description": "Kaos", "sisa": s} for s in self.tertahan]
        if "FROM sales_order_items" in sql:
            return [dict(r) for r in self.rows]
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))
        return 1


@pytest.fixture
def pasang(monkeypatch):
    def _p(c):
        async def dps(conn, tid, sid):
            return [{"id": DPID, "deposit_number": "DP-1"}] if conn.dp else []

        async def sisa(conn, tid, did):
            return conn.dp[0]
        monkeypatch.setattr(CD, "linked_so_deposits", dps)
        monkeypatch.setattr(CD, "compute_deposit_remaining", sisa)

        async def faktor(conn, tid, items, key, direction="output"):
            for it in items:
                it["dpp_factor_num"], it["dpp_factor_den"] = (11, 12) if it.get(key) == "PPN12" else (1, 1)
            return items
        monkeypatch.setattr(SO, "attach_dpp_factors", faktor)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SO, "get_pool", pool)
        return c
    return _p


async def _pratinjau(alasan=None):
    r = await SO.preview_close_sales_order(_req(), str(SOID), SO.CloseSalesOrderRequest(reason=alasan))
    return r["data"]


async def _tutup(alasan=None):
    return await SO.close_sales_order(_req(), str(SOID), SO.CloseSalesOrderRequest(reason=alasan))


# ---------- nol tulisan ----------

@pytest.mark.asyncio
async def test_pratinjau_nol_tulisan_selalu_rollback(pasang):
    for alasan in (None, "pelanggan batal"):
        c = pasang(_C())
        d = await _pratinjau(alasan)
        assert c.tulis == [] and c.tx == ["start", "rollback"], (c.tulis, c.tx)
        assert d["status"] == "confirmed" and d["status_after"] == "completed"


@pytest.mark.asyncio
async def test_pratinjau_galat_404_tetap_rollback(pasang):
    c = pasang(_C(ada=False))
    with pytest.raises(HTTPException) as e:
        await _pratinjau()
    assert e.value.status_code == 404 and c.tx == ["start", "rollback"] and c.tulis == []


@pytest.mark.asyncio
async def test_pratinjau_uuid_buruk_404_tanpa_db(monkeypatch):
    async def pool():
        raise AssertionError("DB tersentuh")
    monkeypatch.setattr(SO, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await SO.preview_close_sales_order(_req(), "bukan-uuid", None)
    assert e.value.status_code == 404


def test_rencana_tanpa_pernyataan_tulis():
    src = inspect.getsource(SO._rencana_tutup_so) + inspect.getsource(SO._nilai_batal)
    for kata in ("INSERT", "UPDATE", "DELETE", "catat_riwayat", "fetchval"):
        assert kata not in src, kata


def test_satu_definisi_dipakai_kedua_rute():
    for fn in (SO.close_sales_order, SO.preview_close_sales_order):
        s = inspect.getsource(fn)
        assert "_rencana_tutup_so(conn, ctx, order_id, reason)" in s
        assert "allocated_amount" not in s and "linked_so_deposits" not in s  # tak ada salinan aturan


# ---------- semua penghalang, bukan yang pertama ----------

@pytest.mark.asyncio
async def test_semua_penghalang_urutan_lama(pasang):
    pasang(_C(status="partial_invoiced", baris=_baris(4, 0), tertahan=[Decimal("50000")], dp=[Decimal("250000")]))
    d = await _pratinjau()
    # uang muka bersisa BUKAN penghalang (putusan pemilik 28 Sep) -> catatan + tetap_saldo
    assert [b["code"] for b in d["blocks"]] == ["SO_REVENUE_NOT_RECOGNIZED", "SO_NOT_FULLY_INVOICED"]
    assert d["can_close"] is False and d["requires_reason"] is True
    assert all("detail" not in b for b in d["blocks"])
    assert [n["code"] for n in d["notes"]] == ["SO_DEPOSIT_STAYS"] and "DP-1 (sisa Rp250.000)" in d["notes"][0]["message"]
    assert d["deposits"] == [{"deposit_id": str(DPID), "deposit_number": "DP-1", "remaining": 250000.0, "after_close": "tetap_saldo"}]


@pytest.mark.asyncio
async def test_status_tak_bisa_ditutup_ikut_dilaporkan_bersama_lainnya(pasang):
    pasang(_C(status="draft", dp=[Decimal("1")], tertahan=[Decimal("5")]))
    d = await _pratinjau("x")
    assert [b["code"] for b in d["blocks"]] == ["SO_STATUS_NOT_CLOSABLE", "SO_REVENUE_NOT_RECOGNIZED"]


@pytest.mark.asyncio
async def test_alasan_menghapus_hanya_penghalang_belum_penuh(pasang):
    pasang(_C(baris=_baris(4, 0)))
    tanpa, dengan = await _pratinjau(), await _pratinjau("pelanggan batal")
    assert [b["code"] for b in tanpa["blocks"]] == ["SO_NOT_FULLY_INVOICED"]
    assert dengan["blocks"] == [] and dengan["can_close"] is True and dengan["requires_reason"] is True


# ---------- /close tetap: penghalang PERTAMA, detail identik ----------

@pytest.mark.asyncio
async def test_close_penghalang_pertama_kini_pendapatan_tertahan(pasang):
    c = pasang(_C(status="partial_invoiced", baris=_baris(4, 0), tertahan=[Decimal("50000")], dp=[Decimal("250000")]))
    with pytest.raises(HTTPException) as e:
        await _tutup()
    assert e.value.status_code == 400 and e.value.detail["code"] == "SO_REVENUE_NOT_RECOGNIZED" and c.tulis == []


@pytest.mark.asyncio
async def test_close_dengan_sisa_uang_muka_berhasil_tanpa_jurnal(pasang):
    """Putusan pemilik 28 Sep (pola NetSuite/SAP): SO tertutup, uang muka TETAP saldo pelanggan.
    Tulisan HANYA status SO + satu baris audit: nol jurnal, nol sentuhan customer_deposits."""
    import json
    c = pasang(_C(status="shipped", baris=_baris(10, 5), dp=[Decimal("250000")]))
    r = await _tutup()
    assert r.data["status"] == "completed"
    assert r.data["deposits_remaining"] == [{"deposit_id": str(DPID), "deposit_number": "DP-1", "remaining": 250000.0}]
    tulis = [s for s, _ in c.tulis]
    assert len(tulis) == 2 and tulis[0].startswith("UPDATE sales_orders SET status = 'completed'")
    assert "INSERT INTO audit_logs" in tulis[1]
    assert not any(k in s for s in tulis for k in ("customer_deposit", "journal_", "bank_transactions"))
    [(_, a)] = [(s, a) for s, a in c.tulis if "INSERT INTO audit_logs" in s]
    assert json.loads(a[-1])["deposits_remaining"][0]["deposit_number"] == "DP-1"


@pytest.mark.asyncio
async def test_close_status_salah_detail_string_lama(pasang):
    pasang(_C(status="completed"))
    with pytest.raises(HTTPException) as e:
        await _tutup("x")
    assert e.value.detail == "Cannot close order with status 'completed'"


SKENARIO = [
    dict(status="confirmed"),
    dict(status="confirmed", alasan="pelanggan batal"),
    dict(status="invoiced", baris=_baris(10, 5)),
    dict(status="partial_invoiced", baris=_baris(4, 5), alasan="sisa batal"),
    dict(status="partial_invoiced", baris=_baris(4, 5)),
    dict(status="invoiced", baris=_baris(10, 5), tertahan=[Decimal("1000")]),
    dict(status="shipped", baris=_baris(10, 5), dp=[Decimal("5")]),
    dict(status="partial_invoiced", baris=_baris(4, 5), dp=[Decimal("5")]),
    dict(status="draft"),
    dict(status="cancelled", alasan="x"),
    dict(status="partial_shipped", baris=_baris(4, 0), tertahan=[Decimal("9")], dp=[Decimal("3")], alasan="y"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("sk", SKENARIO)
async def test_pratinjau_sama_dengan_hasil_close(pasang, sk):
    sk = dict(sk)
    alasan = sk.pop("alasan", None)
    pasang(_C(**sk))
    p = await _pratinjau(alasan)
    c = pasang(_C(**sk))
    if p["can_close"]:
        r = await _tutup(alasan)
        data = r.data
        assert data["status"] == p["status_after"]
        assert data["forced"] is p["requires_reason"]
        assert [(x["description"], x["quantity_cancelled"]) for x in data["cancelled_lines"]] == \
            [(x["description"], x["quantity_cancelled"]) for x in p["lines"] if x["quantity_cancelled"] > 0]
        assert any("UPDATE sales_orders SET status = 'completed'" in s for s, _ in c.tulis)
    else:
        with pytest.raises(HTTPException) as e:
            await _tutup(alasan)
        b0 = p["blocks"][0]
        assert e.value.status_code == 400
        if b0["code"] == "SO_STATUS_NOT_CLOSABLE":
            assert e.value.detail == f"Cannot close order with status '{p['status']}'"
        else:
            assert e.value.detail == b0
        assert c.tulis == []


# ---------- nilai sisa dibatalkan (Law 9, literal) ----------

@pytest.mark.asyncio
async def test_nilai_batal_penuh_confirmed_literal(pasang):
    # A: neto 100.000 - alokasi diskon 10.000 = 90.000; DPP 11/12 = 82.500; PPN 12% = 9.900 -> 99.900
    # B: neto 90.000 - alokasi 9.000 = 81.000, tanpa PPN -> 81.000.  Total 180.900 (= total SO tanpa ongkir)
    pasang(_C())
    d = await _pratinjau("batal semua")
    assert [(x["quantity_cancelled"], x["cancelled_amount"]) for x in d["lines"]] == [(10.0, 99900.0), (5.0, 81000.0)]
    assert d["cancelled_total"] == 180900.0


@pytest.mark.asyncio
async def test_nilai_batal_sebagian_literal(pasang):
    # A sisa 6: neto 60.000; diskon = 19.000 x 60.000/190.000 = 6.000; DPP hj 54.000 -> 11/12 = 49.500 -> PPN 5.940
    pasang(_C(status="partial_invoiced", baris=_baris(4, 5)))
    d = await _pratinjau("sisa batal")
    assert [(x["quantity_cancelled"], x["cancelled_amount"]) for x in d["lines"]] == [(6.0, 59940.0), (0.0, 0.0)]
    assert d["cancelled_total"] == 59940.0


@pytest.mark.asyncio
async def test_nilai_batal_pembulatan_half_up(pasang):
    # 3 x 10.000 PPN 11% 1/1, diskon dokumen 1.000; batal 1: diskon 333,33 -> DPP 9.666,67 -> PPN 1.063,3337 -> 1.063,33
    baris = [{"id": IA, "description": "X", "unit": "pcs", "quantity": Decimal("3"), "quantity_shipped": Decimal("0"),
              "quantity_invoiced": Decimal("2"), "unit_price": Decimal("10000"), "discount_percent": Decimal("0"),
              "tax_rate": Decimal("11"), "tax_id": "PPN11", "on_invoices": Decimal("2"), "sort_order": 1}]
    pasang(_C(status="partial_invoiced", baris=baris, diskon=Decimal("1000")))
    d = await _pratinjau("x")
    assert d["lines"][0]["cancelled_amount"] == 10730.0 and d["cancelled_total"] == 10730.0


@pytest.mark.asyncio
async def test_tanpa_sisa_nilai_nol_dan_bentuk_baris(pasang):
    pasang(_C(status="invoiced", baris=_baris(10, 5)))
    d = await _pratinjau()
    assert d["can_close"] is True and d["requires_reason"] is False and d["cancelled_total"] == 0.0
    assert d["lines"][1] == {"sales_order_item_id": str(IB), "description": "Sablon", "unit": "jasa",
                             "quantity_ordered": 5.0, "quantity_shipped": 0.0, "quantity_on_invoices": 5.0,
                             "quantity_cancelled": 0.0, "cancelled_amount": 0.0}


@pytest.mark.asyncio
async def test_sisa_dari_faktur_tertaut_bukan_pencacah(pasang):
    # kasus Rahayu Umar: pencacah quantity_invoiced berkata 10/10 tanpa satu pun faktur -> sisa = 10
    baris = _baris(0, 0)
    baris[0]["quantity_invoiced"] = Decimal("10")
    pasang(_C(status="invoiced", baris=baris))
    d = await _pratinjau()
    assert d["lines"][0]["quantity_cancelled"] == 10.0 and d["blocks"][0]["code"] == "SO_NOT_FULLY_INVOICED"


# ---------- alasan SELALU tersimpan -> tampil di riwayat ----------

class _HConn:
    """Tiruan riwayat_so: hanya SO + audit_logs yang ditangkap dari /close; tabel lain kosong."""

    def __init__(self, audit):
        self.audit = audit

    async def fetchrow(self, sql, *a):
        if "FROM sales_orders" in sql:
            return {"id": SOID, "order_number": "SO-9", "created_at": None, "created_by": None,
                    "confirmed_at": None, "confirmed_by": None}
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        if "FROM audit_logs" in sql:
            return self.audit
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("baris", [_baris(10, 5), _baris(4, 5)], ids=["penuh", "pendek"])
async def test_alasan_close_tampil_di_riwayat(pasang, monkeypatch, baris):
    import json
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.services import so_riwayat as SR

    c = pasang(_C(status="invoiced", baris=baris))
    r = await _tutup("pelanggan minta ditutup")
    assert r.data["reason"] == "pelanggan minta ditutup"
    [(sql, a)] = [(s, a) for s, a in c.tulis if "INSERT INTO audit_logs" in s]
    uid, ev, et, eid, nomor, tid, src, meta = a
    assert json.loads(meta)["reason"] == "pelanggan minta ditutup"

    async def z(conn, t):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(SR, "zona_tenant", z)

    async def semua(m):
        return True
    baris_audit = [{"id": "x", "createdAt": datetime(2026, 9, 28, 9, tzinfo=timezone.utc), "eventType": ev,
                    "userId": uid, "entity_type": et, "entity_id": UUID(eid), "entity_number": nomor,
                    "metadata": meta, "source": src}]
    h = await SR.riwayat_so(_HConn(baris_audit), T, SOID, semua)
    ringkas = [e["ringkas"] for e in h["events"] if e["jenis"] == ev]
    assert ringkas and "pelanggan minta ditutup" in ringkas[0], h



# ---------- sisa uang muka SO tertutup tetap bisa DIPAKAI (apply) / DIKEMBALIKAN (refund) ----------
# Jalur apply/refund TIDAK membaca SO: satu-satunya pagar pihak = pelanggan yang SAMA. Tiruan berhenti
# di compute_deposit_remaining (_Lolos) = semua pagar SEBELUM tulis sudah dilewati.

class _Lolos(BaseException):  # BaseException: rute refund menelan Exception jadi 500
    pass


C1 = UUID("40000000-0000-0000-0000-0000000000c1")
C2 = UUID("40000000-0000-0000-0000-0000000000c2")
INV_LAIN = UUID("50000000-0000-0000-0000-000000000001")


class _DConn:
    def __init__(self, pelanggan_faktur):
        self.pf, self.q, self.tx = pelanggan_faktur, [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if "FROM customer_deposits" in sql:
            # uang muka milik SO yang SUDAH DITUTUP (completed)
            return {"id": DPID, "status": "partial", "customer_id": str(C1), "sales_order_id": SOID,
                    "proforma_id": None, "deposit_number": "DP-1"}
        if "FROM sales_invoices" in sql:
            return {"invoice_number": "INV-SO-LAIN", "customer_id": self.pf}
        raise AssertionError(sql[:60])


@pytest.fixture
def lolos(monkeypatch):
    async def berhenti(*a, **k):
        raise _Lolos()
    monkeypatch.setattr(CD, "compute_deposit_remaining", berhenti)


def _apl():
    return SimpleNamespace(applications=[SimpleNamespace(invoice_id=str(INV_LAIN), amount=Decimal("100000"))],
                           application_date=None)


@pytest.mark.asyncio
async def test_apply_sisa_dp_so_tertutup_ke_faktur_so_lain_pelanggan_sama(lolos):
    c = _DConn(C1)
    with pytest.raises(_Lolos):
        await CD.apply_deposit_core(c, {"tenant_id": T, "user_id": None}, DPID, _apl())
    assert not any("sales_orders" in s for s in c.q)  # status SO tak pernah dibaca


@pytest.mark.asyncio
async def test_apply_ke_pelanggan_lain_ditolak(lolos):
    with pytest.raises(HTTPException) as e:
        await CD.apply_deposit_core(_DConn(C2), {"tenant_id": T, "user_id": None}, DPID, _apl())
    assert e.value.status_code == 400 and "pelanggan lain" in e.value.detail


@pytest.mark.asyncio
async def test_refund_sisa_dp_so_tertutup(lolos, monkeypatch):
    c = _DConn(C1)

    async def pool():
        return _Pool(c)
    monkeypatch.setattr(CD, "get_pool", pool)

    async def prasyarat(*a, **k):
        return None
    monkeypatch.setattr(CD, "_ensure_role_preconditions", prasyarat)
    body = SimpleNamespace(amount=Decimal("100000"), account_id=str(UUID(int=7)))
    with pytest.raises(_Lolos):
        await CD.refund_customer_deposit(_req(), DPID, body)
    assert not any("sales_orders" in s for s in c.q)
