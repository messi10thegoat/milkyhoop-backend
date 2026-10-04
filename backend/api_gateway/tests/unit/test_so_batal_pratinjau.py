"""POST /sales-orders/{id}/cancel/preview + aturan batal baru (30 Sep 2026).

Putusan pemilik (LANGSUNG di sesi BACKEND, 29 Sep, pola SAP/NetSuite): SO boleh dibatalkan walau ada uang
muka POSTED -> tetap saldo uang muka pelanggan (nol jurnal); proforma TERBIT ikut dibatalkan. Uang muka DRAF
tetap menghalangi (belum berjurnal). Satu definisi (_rencana_batal_so) untuk /cancel dan /cancel/preview.
"""
import json
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_orders as SO  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402
from app.routers import proformas as PF  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-0000000000f1")
DP1 = UUID("20000000-0000-0000-0000-0000000000d1")
DPD = UUID("20000000-0000-0000-0000-0000000000d9")
P1, P2, P3 = (UUID(f"3000000{i}-0000-0000-0000-0000000000a1") for i in range(3))


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}))


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

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


PF_SEMUA = [
    {"id": P1, "proforma_number": "PF-1", "status": "issued", "amount": Decimal("500000")},
    {"id": P2, "proforma_number": "PF-2", "status": "draft", "amount": Decimal("100000")},
    {"id": P3, "proforma_number": "PF-3", "status": "cancelled", "amount": Decimal("50000")},
]


class _C:
    def __init__(self, status="confirmed", kirim=0, faktur=0, draf=(), proformas=(), ada=True):
        self.status, self.kirim, self.faktur, self.ada = status, kirim, faktur, ada
        self.draf, self.pf = bool(draf), list(proformas)
        self.tulis, self.tx = [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        if "FROM sales_orders" in sql:
            assert a[1] == T
            return ({"id": SOID, "status": self.status, "order_number": "SO-8", "shipped_qty": self.kirim,
                     "invoiced_qty": self.faktur, "total_amount": Decimal("1500000")} if self.ada else None)
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        if sql.lstrip().startswith("SELECT 1 FROM sales_orders") and "FOR UPDATE" in sql:
            return 1 if self.ada else None
        self.tulis.append((" ".join(sql.split()), a))
        return SOID

    async def fetch(self, sql, *a):
        if "FROM customer_deposits cd" in sql and "'draft'" in sql:
            assert "cd.tenant_id = $2" in sql
            return [{"id": DPD, "deposit_number": "DP-DRAF", "amount": Decimal("1")}] if self.draf else []
        if "FROM proformas" in sql and sql.lstrip().startswith("SELECT"):
            return list(self.pf)
        if "UPDATE proformas SET status = 'cancelled'" in sql:
            q = " ".join(sql.split())
            self.tulis.append((q, a))
            assert "status = 'issued'" in q and "tenant_id = $2" in q
            return [{"id": p["id"], "proforma_number": p["proforma_number"]} for p in self.pf if p["status"] == "issued"]
        raise AssertionError(sql[:60])


@pytest.fixture
def pasang(monkeypatch):
    def _p(c, dp=()):
        async def dps(conn, tid, sid):
            return [{"id": DP1, "deposit_number": "DP-1"}] if dp else []

        async def sisa(conn, tid, did):
            return dp[0]
        monkeypatch.setattr(CD, "linked_so_deposits", dps)
        monkeypatch.setattr(CD, "compute_deposit_remaining", sisa)

        async def dibayar(conn, tid, pid):
            return 250000.0 if pid == P1 else 0.0
        monkeypatch.setattr(PF, "compute_paid_amount", dibayar)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SO, "get_pool", pool)
        return c
    return _p


async def _pv(alasan=None):
    return (await SO.preview_cancel_sales_order(_req(), str(SOID), SO.CancelSalesOrderRequest(reason=alasan)))["data"]


async def _batal(alasan=None):
    return await SO.cancel_sales_order(_req(), str(SOID), SO.CancelSalesOrderRequest(reason=alasan))


def _audit(c):
    return [a for s, a in c.tulis if "INSERT INTO audit_logs" in s]


@pytest.mark.asyncio
async def test_pratinjau_nol_tulisan_semua_penghalang_urut(pasang):
    c = pasang(_C(status="invoiced", kirim=2, draf=True))
    d = await _pv()
    assert [b["code"] for b in d["blocks"]] == ["SO_STATUS_NOT_CANCELLABLE", "SO_HAS_SHIPMENTS_OR_INVOICES", "SO_DEPOSIT_DRAFT"]
    assert d["can_cancel"] is False and d["status_after"] == "cancelled"
    assert all("detail" not in b for b in d["blocks"]) and "DP-DRAF" in d["blocks"][2]["message"]
    assert c.tulis == [] and c.tx == ["start", "rollback"]


@pytest.mark.asyncio
async def test_uang_muka_posted_bukan_penghalang_tetap_saldo(pasang):
    pasang(_C(), dp=(Decimal("250000"),))
    d = await _pv("pelanggan batal")
    assert d["can_cancel"] is True and d["blocks"] == [] and d["reason"] == "pelanggan batal"
    assert d["deposits"] == [{"deposit_id": str(DP1), "deposit_number": "DP-1", "remaining": 250000.0, "after_cancel": "tetap_saldo"}]
    n = [x for x in d["notes"] if x["code"] == "SO_DEPOSIT_STAYS"][0]
    assert "DP-1 (sisa Rp 250.000)" in n["message"] and d["total_amount"] == 1500000.0


@pytest.mark.asyncio
async def test_proforma_terbit_saja_yang_dibatalkan(pasang):
    pasang(_C(proformas=PF_SEMUA))
    d = await _pv()
    assert [(p["proforma_number"], p["after_cancel"], p["paid"]) for p in d["proformas"]] == \
        [("PF-1", "dibatalkan", 250000.0), ("PF-2", None, 0.0), ("PF-3", None, 0.0)]
    assert [n["code"] for n in d["notes"]] == ["SO_PROFORMAS_CANCELLED"] and "PF-1" in d["notes"][0]["message"]
    assert "PF-2" not in d["notes"][0]["message"]


@pytest.mark.asyncio
async def test_batal_dengan_dp_dan_proforma_terbit_tulisan_hanya_status_proforma_audit(pasang):
    c = pasang(_C(proformas=PF_SEMUA), dp=(Decimal("250000"),))
    r = await _batal("pelanggan batal")
    d = r.data
    assert d["status"] == "cancelled" and d["reason"] == "pelanggan batal"
    assert d["proformas_cancelled"] == [{"proforma_id": str(P1), "proforma_number": "PF-1"}]
    assert d["deposits_remaining"] == [{"deposit_id": str(DP1), "deposit_number": "DP-1", "remaining": 250000.0}]
    jenis = [s.split(" ")[0] + " " + s.split(" ")[1] + " " + s.split(" ")[2] for s, _ in c.tulis]
    assert not any(k in s for s, _ in c.tulis for k in ("customer_deposit", "journal_", "bank_transactions"))
    assert any(s.startswith("UPDATE sales_orders SET status = 'cancelled'") for s, _ in c.tulis)
    pf_upd = [a for s, a in c.tulis if s.startswith("UPDATE proformas")]
    assert pf_upd and pf_upd[0][2] == "Pesanan SO-8 dibatalkan: pelanggan batal"
    au = _audit(c)
    assert [a[1] for a in au] == ["PROFORMA_CANCELLED", "SALES_ORDER_CANCELLED"], jenis
    meta_so = json.loads(au[1][-1])
    assert meta_so["reason"] == "pelanggan batal" and meta_so["proformas_cancelled"][0]["proforma_number"] == "PF-1"
    assert meta_so["deposits_remaining"][0]["deposit_number"] == "DP-1"
    assert json.loads(au[0][-1])["sebab"] == "SO_CANCELLED"
    assert c.tx == ["start", "commit"]


@pytest.mark.asyncio
async def test_batal_detail_galat_lama_identik(pasang):
    c = pasang(_C(status="completed"))
    with pytest.raises(HTTPException) as e:
        await _batal("x")
    assert e.value.status_code == 400 and e.value.detail.startswith("Pesanan ") and e.value.detail.endswith(" berstatus Selesai — tidak bisa dibatalkan.")
    c = pasang(_C(faktur=1))
    with pytest.raises(HTTPException) as e:
        await _batal()
    assert e.value.detail.endswith(" sudah punya pengiriman atau faktur — tidak bisa dibatalkan.") and _audit(c) == []


@pytest.mark.asyncio
async def test_batal_dp_draf_ditolak_tanpa_tulisan(pasang):
    c = pasang(_C(draf=True))
    with pytest.raises(HTTPException) as e:
        await _batal("x")
    assert e.value.status_code == 400 and "DP-DRAF" in e.value.detail
    assert not any(s.startswith(("UPDATE sales_orders", "UPDATE proformas")) for s, _ in c.tulis) and _audit(c) == []


SKENARIO = [
    dict(), dict(status="draft"), dict(status="completed"), dict(status="cancelled"), dict(kirim=1),
    dict(draf=True), dict(proformas=PF_SEMUA), dict(status="invoiced", draf=True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("sk", SKENARIO)
@pytest.mark.parametrize("dp", [(), (Decimal("10"),)])
async def test_pratinjau_sama_dengan_hasil_batal(pasang, sk, dp):
    pasang(_C(**sk), dp=dp)
    p = await _pv("r")
    c = pasang(_C(**sk), dp=dp)
    if p["can_cancel"]:
        r = await _batal("r")
        assert r.data["status"] == p["status_after"]
        assert [x["proforma_number"] for x in r.data["proformas_cancelled"]] == \
            [x["proforma_number"] for x in p["proformas"] if x["after_cancel"] == "dibatalkan"]
        assert [x["deposit_number"] for x in r.data["deposits_remaining"]] == \
            [x["deposit_number"] for x in p["deposits"] if x["after_cancel"] == "tetap_saldo"]
    else:
        with pytest.raises(HTTPException) as e:
            await _batal("r")
        assert e.value.status_code == 400
        assert e.value.detail == p["blocks"][0]["message"]
        assert _audit(c) == []


@pytest.mark.asyncio
async def test_404_dan_uuid_buruk(pasang, monkeypatch):
    c = pasang(_C(ada=False))
    with pytest.raises(HTTPException) as e:
        await _pv()
    assert e.value.status_code == 404 and c.tx == ["start", "rollback"]
    with pytest.raises(HTTPException) as e:
        await _batal()
    assert e.value.status_code == 404

    async def pool():
        raise AssertionError("DB tersentuh")
    monkeypatch.setattr(SO, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await SO.preview_cancel_sales_order(_req(), "bukan-uuid", None)
    assert e.value.status_code == 404


def test_hapus_so_tetap_menjaga_uang_muka():
    """Putusan pemilik hanya untuk BATAL. Hapus SO tetap ditolak bila ada uang muka aktif (dp_guard)."""
    import inspect
    assert "_tolak_bila_ada_uang_muka_aktif" in inspect.getsource(SO.delete_sales_order)
    assert "_tolak_bila_ada_uang_muka_aktif" not in inspect.getsource(SO.cancel_sales_order)
