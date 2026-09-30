"""Modul CW kasbon (V334, 30 Sep 2026): pratinjau grant/void (satu definisi + ROLLBACK + SEMUA penghalang),
idempotensi grant (Law 14), periode tertutup 403 (Law 5), detail/riwayat/ringkasan/daftar ber-pagar pay-group."""
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import employee_advances as EA  # noqa: E402
import app.services.pay_group_access as PGA  # noqa: E402

T = "kaos-biru-konveksi"
EMP = UUID("11111111-0000-0000-0000-000000000001")
KASBON = UUID("22222222-0000-0000-0000-000000000002")
EKUITAS = UUID("33333333-0000-0000-0000-000000000003")
BANK_COA = UUID("44444444-0000-0000-0000-000000000004")
BA = UUID("66666666-0000-0000-0000-000000000006")
ADV = UUID("77777777-0000-0000-0000-000000000007")
GJ = UUID("88888888-0000-0000-0000-000000000008")
PR = UUID("99999999-0000-0000-0000-000000000009")
U1 = "00000000-0000-0000-0000-0000000000a1"


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


class _C:
    """Tiruan satu koneksi: kasbon aktif 500.000 dari rekening bank; 1 potongan gaji 200.000 (bila dipotong)."""

    def __init__(self, dipotong=False, status="active", emp_ada=True, idem=None, sumber=BANK_COA):
        self.dipotong, self.status, self.emp_ada, self.idem, self.sumber = dipotong, status, emp_ada, idem, sumber
        self.tx, self.tulis, self.q = [], [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, q, *a):
        s = " ".join(q.split())
        self.q.append(s)
        if "pg_advisory" not in s:
            self.tulis.append(s[:40])

    async def fetchval(self, q, *a):
        s = " ".join(q.split())
        self.q.append(s)
        if "generate_employee_advance_number" in s:
            self.tulis.append("NOMOR")
            return "KSB-2609-0007"
        if "SUM(amount)" in s and "employee_advance_movements" in s:
            return Decimal("100000")
        if "employee_advance_balance" in s:
            return Decimal("300000") if self.dipotong else Decimal("500000")
        if "SELECT id FROM employee_advance_movements" in s:
            return UUID(int=5)
        if "account_roles" in s:
            return EKUITAS
        if "SELECT journal_number FROM journal_entries" in s:
            return "KASBON-ABCD1234"
        if "SELECT name FROM chart_of_accounts" in s:
            return "BCA Pengeluaran"
        if "COUNT(*)" in s:
            return 1
        raise AssertionError(s[:80])

    async def fetchrow(self, q, *a):
        s = " ".join(q.split())
        self.q.append(s)
        if "FROM idempotency_keys" in s:
            return {"result": json.dumps(self.idem)} if self.idem else None
        if "FROM chart_of_accounts" in s:
            return {"id": a[0], "name": "BCA Pengeluaran" if a[0] == BANK_COA else "Modal Saldo Awal", "account_code": "1-10202"}
        if "FROM employees" in s:
            return {"id": EMP, "name": "Suryani"} if self.emp_ada else None
        if "FROM bank_accounts WHERE coa_id" in s:
            return {"id": BA, "account_name": "BCA Pengeluaran"} if a[0] == BANK_COA else None
        if "FROM employee_advances a" in s:  # _muat_kasbon
            return {"id": ADV, "advance_number": "KSB-2609-0001", "employee_id": EMP, "employee_name": "Suryani",
                    "principal": Decimal("500000"), "granted_date": date(2026, 9, 1), "status": self.status,
                    "notes": None, "grant_journal_id": GJ, "source_account_id": self.sumber, "created_by": UUID(U1),
                    "created_at": datetime(2026, 9, 21, 11, 7, tzinfo=timezone.utc),
                    "voided_at": None if self.status != "void" else datetime(2026, 9, 29, tzinfo=timezone.utc),
                    "voided_by": None if self.status != "void" else UUID(U1), "void_reason": "salah" if self.status == "void" else None}
        if "FROM employee_advances WHERE id" in s:  # rencana batal
            return {"id": ADV, "employee_id": EMP, "principal": Decimal("500000"), "status": self.status,
                    "grant_journal_id": GJ, "source_account_id": self.sumber, "granted_date": date(2026, 9, 1),
                    "advance_number": "KSB-2609-0001"}
        if "FROM bank_transactions bt" in s and "LEFT JOIN bank_accounts" in s:
            return {"id": UUID(int=9), "bank_account_id": BA, "amount": Decimal("-500000"), "account_name": "BCA Pengeluaran"}
        if "FROM bank_transactions bt" in s:  # cermin baru (pratinjau)
            return {"bank_account_id": BA, "account_name": "BCA Pengeluaran", "amount": Decimal("-500000"), "transaction_type": "withdrawal"}
        raise AssertionError(s[:80])

    async def fetch(self, q, *a):
        s = " ".join(q.split())
        self.q.append(s)
        if "FROM journal_lines jl JOIN chart_of_accounts" in s:
            return [{"account_code": "1-10450", "account_name": "Piutang Karyawan", "debit": Decimal("500000"), "credit": Decimal("0")},
                    {"account_code": "1-10202", "account_name": "BCA Pengeluaran", "debit": Decimal("0"), "credit": Decimal("500000")}]
        if "FROM employee_advance_movements m" in s and "payroll_runs" in s and "advance_id = $1" in s:
            g = [{"id": UUID(int=1), "movement_type": "grant", "amount": Decimal("500000"), "payroll_id": None, "journal_id": GJ,
                  "created_at": datetime(2026, 9, 21, 11, 7, tzinfo=timezone.utc), "created_by": UUID(U1),
                  "payroll_number": None, "period_start": None, "journal_number": "KASBON-ABCD1234", "journal_date": date(2026, 9, 1)}]
            if self.dipotong:
                g.append({"id": UUID(int=2), "movement_type": "deduction", "amount": Decimal("-200000"), "payroll_id": PR,
                          "journal_id": UUID(int=3), "created_at": datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc),
                          "created_by": UUID(U1), "payroll_number": "PAY-2609-0001", "period_start": date(2026, 9, 1),
                          "journal_number": "PAY-J-1", "journal_date": date(2026, 9, 28)})
            return g
        if 'FROM "User"' in s:
            return [{"id": U1, "nama": "Anton"}]
        if "GROUP BY m.employee_id" in s:
            return [{"employee_id": EMP, "sisa": Decimal("300000")}, {"employee_id": UUID(int=77), "sisa": Decimal("0")}]
        if "FROM employee_advances a" in s and "LIMIT" in s:
            return [{"id": ADV, "advance_number": "KSB-2609-0001"}]
        raise AssertionError(s[:80])


@pytest.fixture
def pasang(monkeypatch):
    def _p(c, periode_tutup=False, cakupan=True, semua_pg=True):
        async def pool():
            return _Pool(c)
        monkeypatch.setattr(EA, "get_pool", pool)

        async def peran(conn, tid, role):
            return {"EMPLOYEE_ADVANCE": KASBON, "EQUITY_OPENING_BALANCE": EKUITAS}[role]
        monkeypatch.setattr(EA, "resolve_account_id_by_role", peran)

        async def dalam(*a, **k):
            return cakupan
        monkeypatch.setattr(PGA, "employee_in_scope", dalam)

        async def filt(*a, **k):
            return (True, []) if semua_pg else (False, [])
        monkeypatch.setattr(PGA, "accessible_pay_group_filter", filt)

        async def periode(conn, tid, d):
            if periode_tutup:
                raise HTTPException(status_code=403, detail="Cannot post to closed period (Sep 2026)")
        monkeypatch.setattr(EA, "check_period_is_open", periode)

        async def hari(*a, **k):
            return date(2026, 9, 30)
        monkeypatch.setattr(EA, "tanggal_dokumen", hari)

        async def zona(*a, **k):
            return ZoneInfo("Asia/Jakarta")
        monkeypatch.setattr(EA, "zona_tenant", zona)

        async def cermin(conn, **kw):
            conn.tulis.append("CERMIN")
            return UUID(int=8)
        monkeypatch.setattr(EA, "create_bank_transaction_for_journal", cermin)

        async def balik(conn, **kw):
            conn.tulis.append("BALIK")
            return UUID(int=9)
        monkeypatch.setattr(EA, "create_reversal_bank_transaction", balik)
        return c
    return _p


def _req(kunci=None):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": U1, "tenant_id": T}),
                           headers={"X-Idempotency-Key": kunci} if kunci else {})


def _badan(**kw):
    d = dict(employee_id=EMP, principal=Decimal("500000"), granted_date=date(2026, 9, 1), source_account_id=BANK_COA)
    d.update(kw)
    return EA.GrantAdvanceRequest(**d)


# ---------- pratinjau grant ----------

@pytest.mark.asyncio
async def test_pratinjau_grant_bersih_nol_tulisan_bawa_payload(pasang):
    c = pasang(_C())
    d = (await EA.preview_grant_advance(_req(), _badan()))["data"]
    assert d["can_grant"] is True and d["blocks"] == [] and d["number_preview"] == "KSB-2609-0007"
    assert d["employee"] == {"id": str(EMP), "name": "Suryani", "remaining_before": 100000.0, "remaining_after": 600000.0}
    assert d["source"]["bank_account_id"] == str(BA) and d["source"]["is_opening_balance"] is False
    assert [(x["account_code"], x["debit"], x["credit"]) for x in d["journal_lines"]] == [("1-10450", 500000.0, 0.0), ("1-10202", 0.0, 500000.0)]
    assert d["bank_mirror"]["amount"] == -500000.0 and d["bank_mirror"]["transaction_type"] == "withdrawal"
    assert EA.GrantAdvanceRequest(**d["payload"]) == _badan()
    # penulis yang SAMA jalan (tulisan terjadi) tapi transaksi luar SELALU rollback
    assert "CERMIN" in c.tulis and c.tx == ["start", "sp", "sp-release", "rollback"]


@pytest.mark.asyncio
async def test_pratinjau_grant_semua_penghalang_tanpa_penulis(pasang):
    c = pasang(_C(emp_ada=False), periode_tutup=True)
    d = (await EA.preview_grant_advance(_req(), _badan(opening_balance=True)))["data"]
    assert [b["code"] for b in d["blocks"]] == ["KASBON_OPENING_WITH_SOURCE", "KASBON_EMPLOYEE_NOT_FOUND", "PERIOD_CLOSED"]
    assert d["can_grant"] is False and d["payload"] is None and d["number_preview"] is None and d["employee"] is None
    assert c.tulis == [] and c.tx == ["start", "rollback"]


# ---------- grant: galat pertama, periode 403, idempotensi ----------

@pytest.mark.asyncio
async def test_grant_periode_tertutup_403_bukan_500(pasang):
    c = pasang(_C(), periode_tutup=True)
    with pytest.raises(HTTPException) as e:
        await EA.grant_advance(_req(), _badan())
    assert e.value.status_code == 403 and "closed period" in e.value.detail and c.tulis == []


@pytest.mark.asyncio
async def test_grant_galat_pertama_urutan_lama(pasang):
    pasang(_C(emp_ada=False))
    with pytest.raises(HTTPException) as e:
        await EA.grant_advance(_req(), _badan())
    assert e.value.status_code == 404 and e.value.detail == "Karyawan tidak ditemukan"


@pytest.mark.asyncio
async def test_grant_idempotensi_simpan_lalu_replay_dan_409(pasang):
    c = pasang(_C())
    r = await EA.grant_advance(_req("k-1"), _badan())
    assert r["advance_number"] == "KSB-2609-0007"
    simpan = [s for s in c.q if s.startswith("INSERT INTO idempotency_keys")]
    assert len(simpan) == 1
    # replay: isi sama -> respons tersimpan, NOL tulisan baru
    sidik = EA.hash_payload(_badan().model_dump(mode="json"))
    c2 = pasang(_C(idem={"payload_hash": sidik, "response": r}))
    r2 = await EA.grant_advance(_req("k-1"), _badan())
    assert r2["advance_id"] == r["advance_id"] and r2["was_cached"] is True and c2.tulis == []
    # isi beda -> 409
    pasang(_C(idem={"payload_hash": sidik, "response": r}))
    with pytest.raises(HTTPException) as e:
        await EA.grant_advance(_req("k-1"), _badan(principal=Decimal("400000")))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert e.value.detail["advance_number"] == "KSB-2609-0007"


# ---------- pratinjau void ----------

@pytest.mark.asyncio
async def test_pratinjau_void_bersih_bawa_pembalik_bank(pasang):
    c = pasang(_C())
    d = (await EA.preview_void_advance(_req(), ADV, EA.VoidAdvancePreviewRequest(reason="salah input")))["data"]
    assert d["can_void"] is True and d["remaining_before"] == 500000.0 and d["remaining_after"] == 0.0
    assert d["bank_reversal"] == {"bank_account_id": str(BA), "bank_account_name": "BCA Pengeluaran", "amount": 500000.0}
    assert d["advance_number"] == "KSB-2609-0001" and len(d["journal_lines"]) == 2
    assert "BALIK" in c.tulis and c.tx[-1] == "rollback"


@pytest.mark.asyncio
async def test_pratinjau_void_semua_penghalang(pasang):
    pasang(_C(dipotong=True), periode_tutup=True)
    d = (await EA.preview_void_advance(_req(), ADV, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["KASBON_REASON_REQUIRED", "KASBON_ALREADY_DEDUCTED", "PERIOD_CLOSED"]
    assert d["can_void"] is False and d["remaining_after"] == 300000.0


@pytest.mark.asyncio
async def test_void_galat_lama_identik(pasang):
    pasang(_C(dipotong=True))
    with pytest.raises(HTTPException) as e:
        await EA.void_advance(_req(), ADV, EA.VoidAdvanceRequest(reason="x"))
    assert e.value.status_code == 400 and e.value.detail.startswith("Kasbon sudah dipotong sebagian")


# ---------- detail / riwayat (pagar pay-group) ----------

@pytest.mark.asyncio
async def test_detail_turunan_dan_sumber(pasang):
    pasang(_C(dipotong=True))
    d = (await EA.get_advance(_req(), ADV))["data"]
    assert d["advance_number"] == "KSB-2609-0001" and d["remaining"] == 300000.0 and d["deducted_total"] == 200000.0
    assert d["source"]["bank_account_id"] == str(BA) and d["source"]["is_opening_balance"] is False
    assert [(m["type"], m["amount"], m["payroll_number"]) for m in d["movements"]] == [("grant", 500000.0, None), ("deduction", -200000.0, "PAY-2609-0001")]
    assert d["employee"] == {"id": str(EMP), "name": "Suryani"} and d["void"] is None


@pytest.mark.asyncio
async def test_detail_saldo_awal_ditandai(pasang):
    pasang(_C(sumber=EKUITAS))
    d = (await EA.get_advance(_req(), ADV))["data"]
    assert d["source"]["is_opening_balance"] is True and d["source"]["bank_account_id"] is None


@pytest.mark.asyncio
async def test_detail_dan_riwayat_luar_pay_group_404(pasang):
    pasang(_C(), cakupan=False)
    for f, extra in ((EA.get_advance, ()), (EA.get_advance_history, (200,))):
        with pytest.raises(HTTPException) as e:
            await f(_req(), ADV, *extra)
        assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_riwayat_terbaru_dulu_dengan_dokumen_gaji(pasang):
    pasang(_C(dipotong=True, status="void"))
    d = (await EA.get_advance_history(_req(), ADV, 200))["data"]
    assert [e["jenis"] for e in d["events"]] == ["KASBON_DIBATALKAN", "KASBON_DIPOTONG_GAJI", "KASBON_DIBERIKAN"]
    potong = d["events"][1]
    assert potong["dokumen"] == {"tipe": "payroll", "id": str(PR), "nomor": "PAY-2609-0001"} and "Rp200.000" in potong["ringkas"]
    assert d["events"][2]["aktor"] == {"id": U1, "nama": "Anton"} and d["events"][2]["at"].endswith("+07:00")
    assert "BCA Pengeluaran" in d["events"][2]["ringkas"] and d["total"] == 3


# ---------- ringkasan / daftar ----------

@pytest.mark.asyncio
async def test_ringkasan_turunan(pasang, monkeypatch):
    c = pasang(_C())

    async def diberi(*a, **k):
        return {"jml": Decimal("800000"), "n": 2}
    orig = c.fetchrow

    async def fr(q, *a):
        if "SUM(a.principal)" in q:
            assert a[1] == date(2026, 9, 1) and a[2] == date(2026, 10, 1)
            return await diberi()
        return await orig(q, *a)
    c.fetchrow = fr
    of = c.fetchval

    async def fv(q, *a):
        if "-SUM(m.amount)" in q:
            return Decimal("200000")
        return await of(q, *a)
    c.fetchval = fv
    d = (await EA.advances_summary(_req(), None))["data"]
    assert d == {"period": "2026-09", "total_active_remaining": 300000.0, "employees_with_kasbon": 1,
                 "granted_this_period": 800000.0, "granted_count": 2, "deducted_this_period": 200000.0}


@pytest.mark.asyncio
async def test_ringkasan_desember_ke_januari(pasang):
    c = pasang(_C())
    ditangkap = {}

    async def fr(q, *a):
        ditangkap["rentang"] = (a[1], a[2])
        return {"jml": Decimal("0"), "n": 0}
    c.fetchrow = fr

    async def fv(q, *a):
        return Decimal("0")
    c.fetchval = fv
    await EA.advances_summary(_req(), "2026-12")
    assert ditangkap["rentang"] == (date(2026, 12, 1), date(2027, 1, 1))


@pytest.mark.asyncio
async def test_daftar_cari_nomor_atau_nama_dan_total(pasang):
    c = pasang(_C())
    r = await EA.list_advances(_req(), None, None, "ksb-2609", 20, 0)
    assert r["total"] == 1 and r["limit"] == 20 and r["data"][0]["advance_number"] == "KSB-2609-0001"
    q = [s for s in c.q if "FROM employee_advances a" in s and "LIMIT" in s][0]
    assert "a.advance_number ILIKE $2 OR e.name ILIKE $2" in q and "a.tenant_id = $1" in q


@pytest.mark.asyncio
async def test_daftar_tanpa_akses_pay_group_kosong(pasang):
    c = pasang(_C(), semua_pg=False)
    r = await EA.list_advances(_req(), None, None, None, 50, 0)
    assert r == {"success": True, "data": [], "total": 0, "limit": 50, "offset": 0} and c.q == []
