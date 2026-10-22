"""G3 (22 Okt 2026): recurring_bills.generate -- tagihan lahir DRAF sah; posting hanya lewat BillsService.post_bill; satu tagihan per periode."""
import asyncio
import inspect
import os
import uuid
from datetime import date
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import recurring_bills as RB  # noqa: E402
from app.schemas.recurring_bills import GenerateBillRequest  # noqa: E402

T = "kaos-biru-konveksi"
RID, VID = uuid.uuid4(), uuid.uuid4()
D = Decimal


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, items, status="active", ada=None, auto_post=False):
        self.items, self.status, self.ada, self.auto_post = items, status, ada, auto_post
        self.log, self.inserts, self.updates = [], [], []

    def transaction(self): return _Tx()

    async def execute(self, sql, *a):
        q = " ".join(sql.split()); self.log.append(("exec", q[:80]))
        if q.startswith("UPDATE recurring_bills"): self.updates.append(a)
        if q.startswith("INSERT INTO bill_items"): self.inserts.append(a)
        return "OK"

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split()); self.log.append(("fetchrow", q[:80]))
        if "FROM recurring_bills rb" in q:
            return {"id": RID, "tenant_id": T, "vendor_id": VID, "vendor_name": "Vendor X", "status": self.status, "next_bill_date": date(2026, 10, 25),
                    "due_days": 14, "auto_post": self.auto_post, "frequency": "monthly", "interval_count": 1, "end_date": None,
                    "subtotal": D("1000.00"), "tax_amount": D("110.00"), "total_amount": D("1110.00")}
        if "FROM bills" in q and "recurring_bill_id" in q: return self.ada
        if q.startswith("INSERT INTO bills"):
            self.log.append(("bill", a)); return {"id": uuid.UUID(int=42)}
        return None

    async def fetch(self, sql, *a):
        self.log.append(("fetch", " ".join(sql.split())[:80])); return self.items

    async def fetchval(self, sql, *a):
        self.log.append(("fetchval", " ".join(sql.split())[:80])); return "PB-2610-0001"


class Svc:
    def __init__(self, sukses=True):
        self.panggil, self.sukses = [], sukses
    async def post_bill(self, tenant_id, bill_id, user_id):
        self.panggil.append((tenant_id, bill_id)); return {"success": self.sukses, "message": "x", "data": {}}


def _jalan(monkeypatch, kon, body=None, svc=None):
    class CM:
        async def __aenter__(s): return kon
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    monkeypatch.setattr(RB, "get_pool", gp)
    if svc is not None:
        from app.routers import bills as B
        async def gs(): return svc
        monkeypatch.setattr(B, "get_bills_service", gs)
    return asyncio.run(RB.generate_bill(_Req(), RID, body))


PRODUK = [{"item_id": uuid.uuid4(), "description": "Kain", "quantity": D("10"), "unit_price": D("100"), "account_id": None, "line_total": D("1000.00"), "tax_amount": D("110.00"), "sort_order": 0}]
BIAYA = [{"item_id": None, "description": "Sewa", "quantity": D("1"), "unit_price": D("1000"), "account_id": uuid.uuid4(), "line_total": D("1000.00"), "tax_amount": D("110.00"), "sort_order": 0}]


def test_default_draf_sah_status_v2_dan_tanpa_post(monkeypatch):
    kon, svc = Kon(PRODUK), Svc()
    r = _jalan(monkeypatch, kon, None, svc)
    assert r.status == "draft" and r.bill_number == "PB-2610-0001" and r.total_amount == 1110.0
    assert svc.panggil == [] and len(kon.updates) == 1
    ins = next(q for k, q in kon.log if k == "exec" and False) if False else None
    sql_bill = next(l for l in kon.log if l[0] == "fetchrow" and l[1].startswith("INSERT INTO bills"))
    assert "status_v2" in inspect.getsource(RB.generate_bill) and "'draft', 'draft'" in inspect.getsource(RB.generate_bill)


def test_kunci_templat_sebelum_baca(monkeypatch):
    kon = Kon(PRODUK)
    _jalan(monkeypatch, kon, None, Svc())
    i_lock = next(i for i, (k, q) in enumerate(kon.log) if k == "exec" and "pg_advisory_xact_lock" in q)
    i_baca = next(i for i, (k, q) in enumerate(kon.log) if k == "fetchrow" and "FROM recurring_bills rb" in q)
    assert i_lock < i_baca and "FOR UPDATE OF rb" in inspect.getsource(RB.generate_bill)


def test_post_lewat_fungsi_kanonik_bukan_tulis_status(monkeypatch):
    kon, svc = Kon(PRODUK, auto_post=True), Svc(True)
    r = _jalan(monkeypatch, kon, None, svc)
    assert r.status == "posted" and len(svc.panggil) == 1
    src = inspect.getsource(RB.generate_bill)
    assert "UPDATE bills SET status = 'posted'" not in src and "TODO" not in src


def test_post_gagal_tetap_draf(monkeypatch):
    r = _jalan(monkeypatch, Kon(PRODUK), GenerateBillRequest(post_immediately=True), Svc(False))
    assert r.status == "draft"


def test_templat_akun_biaya_tidak_diposting_otomatis(monkeypatch):
    svc = Svc()
    r = _jalan(monkeypatch, Kon(BIAYA, auto_post=True), GenerateBillRequest(post_immediately=True), svc)
    assert r.status == "draft" and svc.panggil == []


def test_satu_tagihan_per_periode_penghitung_tak_naik(monkeypatch):
    ada = {"id": uuid.UUID(int=7), "invoice_number": "PB-2610-0099", "issue_date": date(2026, 10, 25), "due_date": date(2026, 11, 8),
           "grand_total": D("1110.00"), "st": "draft"}
    kon, svc = Kon(PRODUK, ada=ada), Svc()
    r = _jalan(monkeypatch, kon, None, svc)
    assert r.bill_number == "PB-2610-0099" and r.status == "draft" and kon.updates == [] and kon.inserts == [] and svc.panggil == []


def test_templat_tak_aktif_ditolak_indonesia(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, Kon(PRODUK, status="paused"), None, Svc())
    assert e.value.status_code == 400 and "tidak aktif" in e.value.detail


def test_baris_tagihan_membawa_nilai_templat(monkeypatch):
    kon = Kon(PRODUK)
    _jalan(monkeypatch, kon, None, Svc())
    a = kon.inserts[0]
    assert a[3] == D("10") and a[4] == D("100") and a[5] == D("1000.00") and a[6] == D("110.00") and a[7] == 1
