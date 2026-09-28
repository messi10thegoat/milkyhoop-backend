"""deposits[] detail SO + atribusi proforma per uang muka (MASTER 28 Sep 2026; kontrak WORKSPACE "UANG MUKA DITERIMA").
Atribusi = turunan hasil proforma_atribusi.atribusikan (SAMA dengan plafon tagihan / PDF / Sudah Dibayar).
Paritas: Σ uang muka ber-atribusi null (bukan draf) == tak_tertagih plafon; Σ per proforma == tertaut/dicocokkan.
Fixture = literal spek (repro journey + 3 SO grapgrap nyata, sama dengan test_proforma_plafon)."""
import inspect
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from app.routers import sales_orders as SO
from app.services import proforma_atribusi as PA


def _t(h, m=0):
    return datetime(2026, 9, h, m)


def _pf(i, amt, at, status="issued"):
    return {"id": i, "amount": D(str(amt)), "status": status, "issued_at": at, "proforma_number": f"PRO-{i}"}


def _dp(i, amt, at, pf_id=None):
    return {"id": i, "amount": D(str(amt)), "proforma_id": pf_id, "created_at": at}


FIXTURE = [
    # nama, proformas, deposits, harapan {dep: (proforma, cara)} , tak_tertagih
    ("grapgrap 019-09-26: PELUNASAN lalu DP 1 menit kemudian tanpa tautan",
     [_pf("a", 3_570_000, _t(18, 9))], [_dp("d", 3_570_000, _t(18, 10))], {"d": ("a", "cocok")}, 0),
    ("grapgrap SO-2609-0001: DP 14 Sep SEBELUM proforma 18 Sep, nominal sama",
     [_pf("a", 3_000_000, _t(18))], [_dp("d", 3_000_000, _t(14))], {"d": ("a", "cocok")}, 0),
    ("grapgrap 009-08-26: DP bayar proforma tanpa tautan",
     [_pf("a", 30_210_000, _t(16, 6))], [_dp("d", 30_210_000, _t(16, 7))], {"d": ("a", "cocok")}, 0),
    ("REPRO: DP 2 jt dulu, lalu PELUNASAN 3 jt neto -> DP di luar tagihan",
     [_pf("a", 3_000_000, _t(20))], [_dp("d", 2_000_000, _t(19))], {"d": (None, None)}, 2_000_000),
    ("tautan menang: DP1 tertaut b, DP2 cocok a, DP3 bebas tanpa pasangan",
     [_pf("a", 1_000_000, _t(20)), _pf("b", 500_000, _t(21))],
     [_dp("d1", 500_000, _t(22), "b"), _dp("d2", 1_000_000, _t(22, 5)), _dp("d3", 1_000_000, _t(22, 9))],
     {"d1": ("b", "tautan"), "d2": ("a", "cocok"), "d3": (None, None)}, 1_000_000),
]


@pytest.mark.parametrize("nama,pros,deps,harap,tak", FIXTURE)
def test_atribusi_per_uang_muka_literal_dan_paritas_dengan_plafon(nama, pros, deps, harap, tak):
    a = PA.atribusikan(pros, deps)
    per = {d["id"]: PA.atribusi_uang_muka(a, d["id"]) for d in deps}
    for dep, (pid, cara) in harap.items():
        assert (per[dep]["attributed_proforma_id"], per[dep]["attribution"]) == (pid, cara), nama
        assert per[dep]["attributed_proforma_number"] == (f"PRO-{pid}" if pid else None)
    # paritas: jumlah per uang muka membangun ulang angka plafon persis
    assert a["tak_tertagih"] == D(tak)
    assert sum((d["amount"] for d in deps if per[d["id"]]["attribution"] is None), D(0)) == a["tak_tertagih"]
    jumlah = defaultdict(lambda: {"tautan": D(0), "cocok": D(0)})
    for d in deps:
        if per[d["id"]]["attribution"]:
            jumlah[per[d["id"]]["attributed_proforma_id"]][per[d["id"]]["attribution"]] += d["amount"]
    for pid, v in a["per_proforma"].items():
        assert (jumlah[pid]["tautan"], jumlah[pid]["cocok"]) == (v["tertaut"], v["dicocokkan"]), (nama, pid)


def test_uang_muka_tak_dikenal_atau_draf_null():
    a = PA.atribusikan([_pf("a", 100, _t(20))], [])
    assert PA.atribusi_uang_muka(a, "draf") == {"attributed_proforma_id": None, "attributed_proforma_number": None,
                                                "attribution": None}


class _Conn:
    """Kueri daftar uang muka SO + dua kueri muat_atribusi (proformas, customer_deposits bukan void/draft)."""

    def __init__(self, daftar, pros, deps):
        self.daftar, self.pros, self.deps, self.sql = daftar, pros, deps, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "LEFT JOIN LATERAL" in sql:
            return self.daftar
        if "FROM proformas" in sql:
            return self.pros
        if "FROM customer_deposits cd" in sql:
            return self.deps
        raise AssertionError(sql)


def _baris(i, amt, status="posted", pf_id=None, bank=None, nomor=None, holder=None, akun=None, coa=None, metode="transfer"):
    return {"id": i, "deposit_number": f"UM-{i}", "deposit_date": date(2026, 9, 20), "amount": D(str(amt)),
            "status": status, "payment_method": metode, "proforma_id": pf_id, "account_name": akun, "bank_name": bank,
            "account_number": nomor, "account_holder_name": holder, "coa_name": coa}


@pytest.mark.asyncio
async def test_uang_muka_so_medan_rekening_dan_atribusi():
    so = "so-1"
    daftar = [
        _baris("d1", 500_000, pf_id="b", bank="BCA", nomor="8295032185", holder="Anthonius Iwan Adhipraja", akun="BCA Pemasukan"),
        _baris("d2", 1_000_000, bank="Bank BCA", nomor="1111222233", akun="Rekening Utama"),  # nama tanpa awalan bank: sabotase "nama akun = pemilik" harus terlihat
        _baris("d3", 1_000_000, akun="Kas Operasional", metode="cash"),
        _baris("d4", 70_000, status="draft", coa="Kas Kecil", metode="cash"),
    ]
    pros = [{**_pf("a", 1_000_000, _t(20)), "sales_order_id": so}, {**_pf("b", 500_000, _t(21)), "sales_order_id": so}]
    deps = [{**_dp("d1", 500_000, _t(22), "b"), "so_id": so}, {**_dp("d2", 1_000_000, _t(22, 5)), "so_id": so},
            {**_dp("d3", 1_000_000, _t(22, 9)), "so_id": so}]   # d4 draf: kueri atribusi tak memuatnya
    c = _Conn(daftar, pros, deps)
    out = {d["id"]: d for d in await PA.uang_muka_so(c, "kaos", so)}
    assert list(out) == ["d1", "d2", "d3", "d4"]
    assert out["d1"] | {} == {**out["d1"], "account_name": "BCA 8295032185 a.n. Anthonius Iwan Adhipraja",
                              "proforma_id": "b", "attributed_proforma_id": "b", "attributed_proforma_number": "PRO-b",
                              "attribution": "tautan", "deposit_date": "2026-09-20", "payment_method": "transfer"}
    assert out["d2"]["account_name"] == "Bank BCA 1111222233"      # tanpa pemilik -> tanpa a.n. (nama akun TAK dicetak)
    assert (out["d2"]["attributed_proforma_id"], out["d2"]["attribution"]) == ("a", "cocok")
    assert out["d3"]["account_name"] == "Kas Operasional" and out["d3"]["attribution"] is None
    assert out["d4"]["account_name"] == "Kas Kecil" and out["d4"]["attribution"] is None
    sql = c.sql[0][0]
    assert "cd.tenant_id = $1" in sql and "cd.status <> 'void'" in sql and "ba.tenant_id = cd.tenant_id" in sql
    assert c.sql[0][1] == ("kaos", so)
    assert all(a[0] == "kaos" for _, a in c.sql)


@pytest.mark.asyncio
async def test_so_tanpa_uang_muka_tak_memuat_atribusi():
    c = _Conn([], [], [])
    assert await PA.uang_muka_so(c, "kaos", "so") == [] and len(c.sql) == 1


def test_rute_detail_so_memakai_uang_muka_so_dan_skema_membawa_medan():
    src = inspect.getsource(SO.get_sales_order_detail)
    assert "uang_muka_so(conn, ctx[\"tenant_id\"]" in src and "FROM customer_deposits" not in src
    from app.schemas.sales_orders import SalesOrderDepositSummary as M
    for k in ("deposit_date", "payment_method", "account_name", "proforma_id", "attributed_proforma_id",
              "attributed_proforma_number", "attribution"):
        assert k in M.model_fields, k
