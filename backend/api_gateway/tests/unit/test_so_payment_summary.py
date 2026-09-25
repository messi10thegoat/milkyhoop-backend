"""Q-011 (25 Sep 2026): payment_summary di GET /api/sales-orders/{id} + pagar DP batal SO lewat proforma.

Kontrak disetujui MASTER: semua angka turunan jurnal (bukan Σ deposits[]/invoices[]); paid_amount =
"tertutup SO" (dasar terbayar proforma); paid_cash_amount = invoice_settled − credit_note + dp_unapplied
(uang yang benar-benar masuk); credit_note_amount dipisah. Pagar batal SO dulu hanya membaca
customer_deposits.sales_order_id -> DP yang menunjuk PROFORMA SO saja lolos.
"""
import ast
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import sales_orders as SOR
from app.schemas.sales_orders import SalesOrderDetail
from app.services import dp_guard as G
from app.services import proforma_terbayar as PT

TENANT = "kaos-biru-konveksi"
SO = uuid.uuid4()


def _r(**k):
    r = {x: D(0) for x in ("invoiced", "invoice_outstanding", "invoice_settled", "credit_note",
                           "dp_received", "dp_applied", "dp_refunded", "dp_unapplied")}
    r.update({a: D(str(b)) for a, b in k.items()})
    r["tertutup"] = r["invoice_settled"] + r["dp_unapplied"]
    return r


# ---------- rumus (murni) ----------

def test_rahayu_sesudah_tautan():
    # SO 6,3 jt; faktur 6,3 jt lunas lewat DP 3,42 (diterapkan) + RCV 2,88
    s = PT.ringkasan_pembayaran_so(D("6300000"), _r(invoiced=6300000, invoice_settled=6300000,
                                                     dp_received=3420000, dp_applied=3420000))
    assert (s["paid_amount"], s["paid_cash_amount"], s["remaining_amount"]) == (6300000.0, 6300000.0, 0.0)
    assert (s["uninvoiced_amount"], s["invoice_outstanding_amount"], s["dp_unapplied_amount"]) == (0.0, 0.0, 0.0)


def test_nota_kredit_menutup_tanpa_uang_masuk():
    # faktur 1 jt: 600 rb dibayar tunai + 400 rb nota kredit -> tertutup 1 jt, uang masuk 600 rb
    s = PT.ringkasan_pembayaran_so(D("1000000"), _r(invoiced=1000000, invoice_settled=1000000, credit_note=400000))
    assert (s["paid_amount"], s["paid_cash_amount"], s["credit_note_amount"]) == (1000000.0, 600000.0, 400000.0)


def test_dp_belum_diterapkan_dan_belum_difakturkan():
    s = PT.ringkasan_pembayaran_so(D("5000000"), _r(dp_received=1500000, dp_unapplied=1500000))
    assert (s["paid_amount"], s["paid_cash_amount"], s["remaining_amount"], s["uninvoiced_amount"]) == \
           (1500000.0, 1500000.0, 3500000.0, 5000000.0)


def test_dp_direfund_bukan_uang_masuk():
    s = PT.ringkasan_pembayaran_so(D("2000000"), _r(dp_received=500000, dp_refunded=500000))
    assert (s["paid_amount"], s["paid_cash_amount"], s["dp_refunded_amount"]) == (0.0, 0.0, 500000.0)


def test_medan_kontrak_lengkap():
    s = PT.ringkasan_pembayaran_so(D("1"), _r())
    assert set(s) == {"order_total", "invoiced_amount", "uninvoiced_amount", "invoice_settled_amount",
                      "invoice_outstanding_amount", "credit_note_amount", "dp_received_amount",
                      "dp_applied_amount", "dp_refunded_amount", "dp_unapplied_amount", "paid_amount",
                      "paid_cash_amount", "remaining_amount"}


# ---------- ringkasan_pesanan: jurnal, nota kredit, DP ----------

class DB:
    def __init__(self, fakturs, outstanding, deps, cn=None):
        self.fakturs, self.outstanding, self.deps, self.cn = fakturs, outstanding, deps, cn or {}
        self.sql, self.args = [], []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        if "FROM sales_invoices si" in sql:
            return self.fakturs
        if "compute_ar_outstanding" in sql:
            return [{"invoice_id": k, "outstanding": v} for k, v in self.outstanding.items()]
        if "FROM credit_notes cn" in sql:
            return [{"inv": k, "kredit": v} for k, v in self.cn.items()]
        if "FROM customer_deposits cd" in sql:
            return self.deps
        raise AssertionError(sql)


@pytest.fixture
def jurnal(monkeypatch):
    from app.routers import customer_deposits as CD
    from app.services import role_resolver as RR
    sisa = {}

    async def palsu(conn, tenant_id, ids):
        return {str(i): sisa[str(i)] for i in ids}

    async def akun(conn, tenant_id, peran):
        assert peran == RR.AccountRole.CUSTOMER_DEPOSIT_LIABILITY
        return "akun-dp"
    monkeypatch.setattr(CD, "compute_deposit_remaining_many", palsu)
    monkeypatch.setattr(RR, "resolve_account_id_by_role", akun)
    return sisa


@pytest.mark.asyncio
async def test_ringkasan_campuran(jurnal):
    i1, i2, d1, d2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    jurnal[str(d1)], jurnal[str(d2)] = D("0"), D("250000")
    db = DB([{"id": i1, "sales_order_id": SO, "tagih": D("1000000")},
             {"id": i2, "sales_order_id": SO, "tagih": D("500000")}],
            {i2: D("200000")},
            [{"id": d1, "so_id": SO, "terima": D("400000"), "terap": D("400000"), "lain": D("0")},
             {"id": d2, "so_id": SO, "terima": D("300000"), "terap": D("0"), "lain": D("50000")}],
            cn={i1: D("100000")})
    r = (await PT.ringkasan_pesanan(db, TENANT, [SO]))[SO]
    assert r["invoiced"] == D("1500000") and r["invoice_outstanding"] == D("200000")
    assert r["invoice_settled"] == D("1300000") and r["credit_note"] == D("100000")
    assert (r["dp_received"], r["dp_applied"], r["dp_refunded"], r["dp_unapplied"]) == \
           (D("700000"), D("400000"), D("50000"), D("250000"))
    assert r["dp_received"] - r["dp_applied"] - r["dp_refunded"] == r["dp_unapplied"]   # invarian
    assert r["tertutup"] == D("1550000")
    s = PT.ringkasan_pembayaran_so(D("2000000"), r)
    assert s["paid_cash_amount"] == 1450000.0            # 1,3 jt − 0,1 jt CN + 0,25 jt DP


@pytest.mark.asyncio
async def test_sisa_dp_dari_sumber_terpisah_bukan_selisih(jurnal):
    # Invarian received − applied − refunded = unapplied hanya bermakna bila unapplied dibaca dari
    # compute_deposit_remaining_many, BUKAN dipaksakan dari selisih (tautologi). Data sengaja tak cocok.
    d = uuid.uuid4()
    jurnal[str(d)] = D("240000")
    db = DB([], {}, [{"id": d, "so_id": SO, "terima": D("300000"), "terap": D("0"), "lain": D("50000")}])
    r = (await PT.ringkasan_pesanan(db, TENANT, [SO]))[SO]
    assert r["dp_unapplied"] == D("240000")
    assert r["dp_received"] - r["dp_applied"] - r["dp_refunded"] != r["dp_unapplied"]   # selisih terlihat


@pytest.mark.asyncio
async def test_sql_journal_derived(jurnal):
    db = DB([{"id": uuid.uuid4(), "sales_order_id": SO, "tagih": D("1")}], {}, [])
    await PT.ringkasan_pesanan(db, TENANT, [SO])
    semua = "\n".join(db.sql)
    assert "amount_applied" not in semua and "total_amount" not in semua   # bukan cache/wrapper
    cn = next(s for s in db.sql if "FROM credit_notes cn" in s)
    assert "je.source_type = 'CREDIT_NOTE'" in cn and "cn.original_invoice_id = ANY($2::uuid[])" in cn
    assert "coa.account_type = 'RECEIVABLE' AND jl.credit > 0" in cn
    dep = next(s for s in db.sql if "FROM customer_deposits cd" in s)
    assert "is_effective_journal(je.id)" in dep and "jl.account_id = $3" in dep
    assert "je.source_type = 'CUSTOMER_DEPOSIT'" in dep and "je.source_type = 'DEPOSIT_APPLICATION'" in dep
    assert db.args[-1][2] == "akun-dp"


# ---------- penyambungan detail SO ----------

def test_skema_mendeklarasikan_payment_summary():
    assert "payment_summary" in SalesOrderDetail.model_fields


def test_detail_so_mengisi_payment_summary():
    pohon = ast.parse(Path(SOR.__file__).read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_sales_order_detail")
    dipanggil = {getattr(n.func, "id", None) for n in ast.walk(f) if isinstance(n, ast.Call)}
    assert {"ringkasan_pesanan", "ringkasan_pembayaran_so"} <= dipanggil
    kw = {k.arg for n in ast.walk(f) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "SalesOrderDetail"
          for k in n.keywords}
    assert "payment_summary" in kw


# ---------- pagar DP batal/hapus SO lewat proforma (dua sisi) ----------

class GDB:
    def __init__(self, baris):
        self.baris, self.sql = baris, None

    async def fetch(self, sql, *a):
        self.sql, self.args = sql, a
        return self.baris


@pytest.mark.asyncio
async def test_pagar_menolak_dp_aktif_lewat_proforma():
    db = GDB([{"deposit_number": "DEP-2609-0020"}])
    with pytest.raises(HTTPException) as e:
        await G.tolak_bila_ada_uang_muka_aktif_pesanan(db, SO, TENANT, "dibatalkan")
    assert e.value.status_code == 400
    assert e.value.detail.startswith("Pesanan penjualan memiliki uang muka aktif (DEP-2609-0020) dan tidak bisa dibatalkan.")
    assert "cd.proforma_id IN (SELECT p.id FROM proformas p" in db.sql and "p.sales_order_id = $1" in db.sql
    assert "cd.sales_order_id = $1" in db.sql
    assert "cd.status <> 'void'" in db.sql and "(cd.amount - COALESCE(cd.amount_refunded, 0)) > 0" in db.sql
    assert db.args == (SO, TENANT)


@pytest.mark.asyncio
async def test_pagar_lolos_tanpa_dp_aktif():
    await G.tolak_bila_ada_uang_muka_aktif_pesanan(GDB([]), SO, TENANT, "dibatalkan")


def test_batal_dan_hapus_so_memakai_pagar_pesanan():
    src = Path(SOR.__file__).read_text()
    pohon = ast.parse(src)
    pemanggil = set()
    for f in pohon.body:
        if isinstance(f, ast.AsyncFunctionDef):
            if any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_tolak_bila_ada_uang_muka_aktif"
                   for n in ast.walk(f)):
                pemanggil.add(f.name)
    assert {"cancel_sales_order", "delete_sales_order"} <= pemanggil
    pembungkus = next(f for f in pohon.body if isinstance(f, ast.AsyncFunctionDef)
                      and f.name == "_tolak_bila_ada_uang_muka_aktif")
    dipanggil = {getattr(n.func, "id", None) for n in ast.walk(pembungkus) if isinstance(n, ast.Call)}
    assert dipanggil == {"tolak_bila_ada_uang_muka_aktif_pesanan"}
